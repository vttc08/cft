from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import re
from typing import Any, Callable

import boto3

from cft.cache.policies import (
    CachePolicy,
    format_utc_datetime,
    parse_utc_datetime,
    utc_now,
)
from cft.cache.store import JsonFileStore
from cft.config.paths import AppPaths, get_app_paths
from cft.config.settings import AppSettings, load_app_settings, settings_profile_name
from cft.models.cache import (
    DistributionCacheRecord,
    ProfileCacheState,
    StandardLogDeliveryRecord,
    normalize_distribution_type,
)
from cft.models.distribution import DistributionSummary, normalize_distribution

STANDARD_LOG_SOURCE_RE = re.compile(r"^CreatedByCloudFront-(?P<distribution_id>[A-Za-z0-9]+)-")

SessionFactory = Callable[..., boto3.Session]


@dataclass(frozen=True)
class AccountIdentity:
    account_id: str
    arn: str
    user_id: str


@dataclass(frozen=True)
class CloudFrontInventory:
    profile_name: str
    identity: AccountIdentity | None
    distributions: tuple[DistributionSummary, ...]
    distribution_types: dict[str, str] = field(default_factory=dict)
    standard_log_deliveries: dict[str, tuple[StandardLogDeliveryRecord, ...]] = field(
        default_factory=dict
    )
    cache_last_updated: datetime | None = None
    from_cache: bool = False


class CloudFrontInventoryService:
    """Read-only AWS adapter for the first-stage CloudFront browser."""

    def __init__(
        self,
        profile_name: str | None = None,
        region_name: str | None = None,
        *,
        paths: AppPaths | None = None,
        settings: AppSettings | None = None,
        session_factory: SessionFactory = boto3.Session,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.profile_name = profile_name
        self.region_name = region_name
        self.paths = paths or get_app_paths()
        self.settings = settings
        self.session_factory = session_factory
        self.now = now

    def load(self, *, refresh: bool = False) -> CloudFrontInventory:
        settings = self.settings or load_app_settings(
            self.paths, profile_name=settings_profile_name(self.profile_name)
        )
        session = self.session_factory(
            profile_name=self.profile_name,
            region_name=self.region_name or settings.aws.cloudfront_region,
        )
        profile_name = session.profile_name or self.profile_name or "default"
        self.paths.ensure_profile_dirs(profile_name)

        cache_store = JsonFileStore(self.paths.profile_state_file(profile_name))
        cache_payload = cache_store.read()
        state = ProfileCacheState.from_payload(cache_payload, profile_name=profile_name)
        cache_last_updated = state.last_updated
        cache_policy = CachePolicy.from_seconds(settings.cache.distribution_ttl_seconds)
        now = self.now()

        if (
            cache_payload is not None
            and not refresh
            and cache_policy.is_fresh(cache_last_updated, now=now)
        ):
            try:
                standard_log_deliveries = self._list_standard_log_deliveries(
                    session,
                    loaded_at=now,
                )
            except Exception:
                cached_inventory = self._inventory_from_cache(state)
                if cached_inventory is not None:
                    return cached_inventory
            else:
                if standard_log_deliveries:
                    refreshed_state = state.with_inventory(
                        profile_name=profile_name,
                        identity=state.identity,
                        inventory={
                            distribution_id: record.inventory
                            for distribution_id, record in state.distributions.items()
                        },
                        last_updated=now,
                        standard_log_deliveries=standard_log_deliveries,
                    )
                    cache_store.write(refreshed_state.to_payload())
                    cached_inventory = self._inventory_from_cache(refreshed_state)
                    if cached_inventory is not None:
                        return cached_inventory
            cached_inventory = self._inventory_from_cache(state)
            if cached_inventory is not None:
                return cached_inventory

        try:
            inventory = self._load_from_aws(session, profile_name, state, loaded_at=now)
        except Exception:
            cached_inventory = self._inventory_from_cache(state)
            if cached_inventory is not None:
                return cached_inventory
            raise

        refreshed_state = state.with_inventory(
            profile_name=profile_name,
            identity=_identity_to_cache(inventory.identity),
            inventory={
                distribution.distribution_id: _distribution_to_cache(distribution)
                for distribution in inventory.distributions
                if distribution.distribution_id
            },
            last_updated=now,
            standard_log_deliveries=inventory.standard_log_deliveries,
        )
        refreshed_inventory = CloudFrontInventory(
            profile_name=profile_name,
            identity=inventory.identity,
            distributions=inventory.distributions,
            distribution_types=_distribution_types_from_state(refreshed_state),
            standard_log_deliveries=_standard_log_deliveries_from_state(refreshed_state),
            cache_last_updated=now,
            from_cache=False,
        )
        cache_store.write(refreshed_state.to_payload())
        return refreshed_inventory

    def _load_from_aws(
        self,
        session: boto3.Session,
        profile_name: str,
        state: ProfileCacheState,
        *,
        loaded_at: datetime,
    ) -> CloudFrontInventory:
        identity = self._get_identity(session)
        distributions = tuple(self._list_distributions(session))
        try:
            standard_log_deliveries = self._list_standard_log_deliveries(
                session,
                loaded_at=loaded_at,
            )
        except Exception:
            standard_log_deliveries = _standard_log_deliveries_from_state(state)
        return CloudFrontInventory(
            profile_name=profile_name,
            identity=identity,
            distributions=distributions,
            distribution_types={},
            standard_log_deliveries=standard_log_deliveries,
            cache_last_updated=loaded_at,
            from_cache=False,
        )

    @staticmethod
    def _get_identity(session: boto3.Session) -> AccountIdentity:
        response = session.client("sts").get_caller_identity()
        return AccountIdentity(
            account_id=str(response.get("Account", "")),
            arn=str(response.get("Arn", "")),
            user_id=str(response.get("UserId", "")),
        )

    @staticmethod
    def _list_distributions(session: boto3.Session) -> list[DistributionSummary]:
        client = session.client("cloudfront")
        paginator = client.get_paginator("list_distributions")
        distributions: list[DistributionSummary] = []
        for page in paginator.paginate():
            items: list[dict[str, Any]] = (
                page.get("DistributionList", {}).get("Items", []) or []
            )
            distributions.extend(normalize_distribution(item) for item in items)
        return distributions

    @staticmethod
    def _list_standard_log_deliveries(
        session: boto3.Session,
        *,
        loaded_at: datetime,
    ) -> dict[str, tuple[StandardLogDeliveryRecord, ...]]:
        client = session.client("logs")
        destination_resource_arns = CloudFrontInventoryService._list_delivery_destination_resource_arns(
            client
        )
        paginator = client.get_paginator("describe_deliveries")
        grouped: dict[str, list[StandardLogDeliveryRecord]] = {}
        for page in paginator.paginate():
            deliveries: list[dict[str, Any]] = page.get("deliveries", []) or []
            for delivery in deliveries:
                if not isinstance(delivery, dict):
                    continue
                source_name = str(delivery.get("deliverySourceName", "")).strip()
                if "CreatedByCloudFront" not in source_name:
                    continue
                distribution_id = _distribution_id_from_source_name(source_name)
                if not distribution_id:
                    continue
                record = StandardLogDeliveryRecord.from_payload(
                    {
                        "id": delivery.get("id"),
                        "arn": delivery.get("arn"),
                        "deliveryDestinationArn": delivery.get("deliveryDestinationArn"),
                        "deliveryDestinationResourceArn": destination_resource_arns.get(
                            str(delivery.get("deliveryDestinationArn", "")).strip()
                        ),
                        "deliveryDestinationType": delivery.get("deliveryDestinationType"),
                        "deliverySourceName": source_name,
                    }
                )
                if not record.delivery_id:
                    continue
                record = replace(record, last_updated=loaded_at)
                grouped.setdefault(distribution_id, []).append(record)
        return {
            distribution_id: tuple(sorted(records, key=_standard_log_delivery_sort_key))
            for distribution_id, records in grouped.items()
        }

    @staticmethod
    def _list_delivery_destination_resource_arns(
        logs_client: object,
    ) -> dict[str, str]:
        paginator = logs_client.get_paginator("describe_delivery_destinations")
        destination_resource_arns: dict[str, str] = {}
        for page in paginator.paginate():
            destinations: list[dict[str, Any]] = page.get("deliveryDestinations", []) or []
            for destination in destinations:
                if not isinstance(destination, dict):
                    continue
                delivery_destination_arn = str(destination.get("arn", "")).strip()
                resource_arn = str(
                    (
                        destination.get("deliveryDestinationConfiguration") or {}
                    ).get("destinationResourceArn", "")
                ).strip()
                resource_arn = _normalize_log_group_identifier(resource_arn)
                if delivery_destination_arn and resource_arn:
                    destination_resource_arns[delivery_destination_arn] = resource_arn
        return destination_resource_arns

    @staticmethod
    def _inventory_from_cache(state: ProfileCacheState | None) -> CloudFrontInventory | None:
        if state is None:
            return None
        distributions = tuple(
            _distribution_from_cache(value.to_payload())
            for value in state.distributions.values()
        )
        return CloudFrontInventory(
            profile_name=state.profile_name,
            identity=_identity_from_cache(state.identity),
            distributions=distributions,
            distribution_types=_distribution_types_from_state(state),
            standard_log_deliveries=_standard_log_deliveries_from_state(state),
            cache_last_updated=state.last_updated,
            from_cache=True,
        )

    def save_distribution_type(
        self,
        *,
        profile_name: str,
        distribution_id: str,
        distribution_type: str,
    ) -> None:
        normalized_type = normalize_distribution_type(distribution_type)
        self.paths.ensure_profile_dirs(profile_name)
        cache_store = JsonFileStore(self.paths.profile_state_file(profile_name))
        state = ProfileCacheState.from_payload(cache_store.read(), profile_name=profile_name)
        existing = state.distributions.get(distribution_id)
        if existing is None:
            updated_distributions = {
                **state.distributions,
                distribution_id: DistributionCacheRecord(
                    distribution_id=distribution_id,
                    type=normalized_type,
                ),
            }
        else:
            updated_distributions = {
                **state.distributions,
                distribution_id: replace(existing, type=normalized_type),
            }
        cache_store.write(replace(state, distributions=updated_distributions).to_payload())


def _identity_to_cache(identity: AccountIdentity | None) -> dict[str, str] | None:
    if identity is None:
        return None
    return {
        "account_id": identity.account_id,
        "arn": identity.arn,
        "user_id": identity.user_id,
    }


def _identity_from_cache(payload: object) -> AccountIdentity | None:
    if not isinstance(payload, dict):
        return None
    return AccountIdentity(
        account_id=str(payload.get("account_id", "")),
        arn=str(payload.get("arn", "")),
        user_id=str(payload.get("user_id", "")),
    )


def _distribution_to_cache(distribution: DistributionSummary) -> dict[str, Any]:
    return {
        "distribution_id": distribution.distribution_id,
        "arn": distribution.arn,
        "comment": distribution.comment,
        "domain_name": distribution.domain_name,
        "enabled": distribution.enabled,
        "status": distribution.status,
        "aliases": list(distribution.aliases),
        "origins": list(distribution.origins),
        "last_modified_time": (
            format_utc_datetime(distribution.last_modified_time)
            if distribution.last_modified_time
            else None
        ),
    }


def _distribution_from_cache(payload: dict[str, Any]) -> DistributionSummary:
    inventory = payload.get("inventory")
    if isinstance(inventory, dict):
        payload = inventory

    aliases = payload.get("aliases") or []
    origins = payload.get("origins") or []
    return DistributionSummary(
        distribution_id=str(payload.get("distribution_id", "")),
        arn=str(payload.get("arn", "")),
        comment=str(payload.get("comment", "")),
        domain_name=str(payload.get("domain_name", "")),
        enabled=bool(payload.get("enabled", False)),
        status=str(payload.get("status", "")),
        aliases=tuple(str(alias) for alias in aliases),
        origins=tuple(str(origin) for origin in origins),
        last_modified_time=parse_utc_datetime(payload.get("last_modified_time")),
    )


def _distribution_types_from_state(state: ProfileCacheState) -> dict[str, str]:
    return {
        distribution_id: normalize_distribution_type(record.type)
        for distribution_id, record in state.distributions.items()
        if distribution_id
    }


def _standard_log_deliveries_from_state(
    state: ProfileCacheState,
) -> dict[str, tuple[StandardLogDeliveryRecord, ...]]:
    return {
        distribution_id: record.standard_logs
        for distribution_id, record in state.distributions.items()
        if distribution_id and record.standard_logs
    }


def _distribution_id_from_source_name(source_name: str) -> str | None:
    match = STANDARD_LOG_SOURCE_RE.match(source_name)
    if match:
        return match.group("distribution_id")
    parts = source_name.split("-")
    if len(parts) >= 3 and parts[0] == "CreatedByCloudFront":
        candidate = parts[1].strip()
        return candidate or None
    return None


def _standard_log_delivery_sort_key(record: StandardLogDeliveryRecord) -> tuple[str, str, str]:
    return (
        record.delivery_destination_type or "",
        record.delivery_destination_arn or "",
        record.delivery_id or "",
    )


def _normalize_log_group_identifier(value: str) -> str:
    text = value.strip()
    if text.endswith(":*"):
        return text[:-2]
    if text.endswith("*"):
        return text[:-1]
    return text
