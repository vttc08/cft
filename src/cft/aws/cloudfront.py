from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
from cft.models.cache import ProfileCacheState
from cft.models.distribution import DistributionSummary, normalize_distribution

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
            cached_inventory = self._inventory_from_cache(state)
            if cached_inventory is not None:
                return cached_inventory

        try:
            inventory = self._load_from_aws(session, profile_name, loaded_at=now)
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
        )
        cache_store.write(refreshed_state.to_payload())
        return inventory

    def _load_from_aws(
        self,
        session: boto3.Session,
        profile_name: str,
        *,
        loaded_at: datetime,
    ) -> CloudFrontInventory:
        identity = self._get_identity(session)
        distributions = tuple(self._list_distributions(session))
        return CloudFrontInventory(
            profile_name=profile_name,
            identity=identity,
            distributions=distributions,
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
            cache_last_updated=state.last_updated,
            from_cache=True,
        )


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
