from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

import boto3

from cft.cache.policies import CachePolicy, utc_now
from cft.cache.store import JsonFileStore
from cft.config.paths import AppPaths, get_app_paths
from cft.config.settings import AppSettings, load_app_settings, settings_profile_name
from cft.models.cache import (
    DistributionCacheRecord,
    ProfileCacheState,
    SourceMetrics,
)

from .cloudfront import CloudFrontInventory

SessionFactory = Callable[..., boto3.Session]

LOGS_INSIGHTS_QUERY = "stats sum(`cs-bytes`) as uploads by DistributionId"
MAX_LOG_GROUP_IDENTIFIERS_PER_QUERY = 50
DEFAULT_QUERY_POLL_INTERVAL_SECONDS = 0.5
DEFAULT_QUERY_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class CloudWatchLogGroupSummary:
    log_group_name: str
    log_group_arn: str | None = None
    log_group_class: str | None = None


@dataclass(frozen=True)
class CloudFrontLogsUploadSnapshot:
    profile_name: str
    upload_by_distribution: dict[str, SourceMetrics]
    from_cache: bool


class CloudWatchLogGroupDiscoveryService:
    """Read-only CloudWatch Logs adapter for selecting a shared log group override."""

    def __init__(
        self,
        profile_name: str | None = None,
        region_name: str | None = None,
        *,
        paths: AppPaths | None = None,
        settings: AppSettings | None = None,
        session_factory: SessionFactory = boto3.Session,
    ) -> None:
        self.profile_name = profile_name
        self.region_name = region_name
        self.paths = paths or get_app_paths()
        self.settings = settings
        self.session_factory = session_factory

    def list_log_groups(self) -> tuple[CloudWatchLogGroupSummary, ...]:
        settings = self.settings or load_app_settings(
            self.paths, profile_name=settings_profile_name(self.profile_name)
        )
        session = self.session_factory(
            profile_name=self.profile_name,
            region_name=self.region_name or settings.aws.cloudfront_region,
        )
        logs_client = session.client("logs")
        summaries: list[CloudWatchLogGroupSummary] = []
        next_token: str | None = None
        while True:
            request: dict[str, object] = {}
            if next_token:
                request["nextToken"] = next_token
            page = logs_client.list_log_groups(**request)
            log_groups: list[dict[str, object]] = page.get("logGroups", []) or []
            for log_group in log_groups:
                if not isinstance(log_group, dict):
                    continue
                name = str(log_group.get("logGroupName", "")).strip()
                if not name:
                    continue
                summaries.append(
                    CloudWatchLogGroupSummary(
                        log_group_name=name,
                        log_group_arn=str(log_group.get("logGroupArn", "")).strip() or None,
                        log_group_class=str(log_group.get("logGroupClass", "")).strip() or None,
                    )
                )
            next_token = str(page.get("nextToken", "")).strip() or None
            if not next_token:
                break
        return tuple(
            sorted(
                summaries,
                key=lambda summary: (summary.log_group_name, summary.log_group_arn or ""),
            )
        )


class CloudFrontLogsUploadService:
    """Read-through CloudWatch Logs cache for CloudFront standard-log upload totals."""

    def __init__(
        self,
        profile_name: str | None = None,
        region_name: str | None = None,
        *,
        paths: AppPaths | None = None,
        settings: AppSettings | None = None,
        session_factory: SessionFactory = boto3.Session,
        now: Callable[[], datetime] = utc_now,
        query_poll_interval_seconds: float = DEFAULT_QUERY_POLL_INTERVAL_SECONDS,
        query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    ) -> None:
        self.profile_name = profile_name
        self.region_name = region_name
        self.paths = paths or get_app_paths()
        self.settings = settings
        self.session_factory = session_factory
        self.now = now
        self.query_poll_interval_seconds = max(0.0, query_poll_interval_seconds)
        self.query_timeout_seconds = max(1.0, query_timeout_seconds)

    def load(
        self,
        inventory: CloudFrontInventory,
        *,
        refresh: bool = False,
    ) -> CloudFrontLogsUploadSnapshot:
        settings = self.settings or load_app_settings(
            self.paths, profile_name=settings_profile_name(inventory.profile_name)
        )
        session = self.session_factory(
            profile_name=inventory.profile_name,
            region_name=self.region_name or settings.aws.cloudfront_region,
        )
        self.paths.ensure_profile_dirs(inventory.profile_name)
        state_file = self.paths.profile_state_file(inventory.profile_name)
        cache_store = JsonFileStore(state_file)
        state = ProfileCacheState.from_payload(
            cache_store.read(),
            profile_name=inventory.profile_name,
        )
        now = self._coerce_utc(self.now())
        month_key = self._month_key(now)
        cache_policy = CachePolicy.from_seconds(settings.cache.logs_upload_ttl_seconds)

        logs_client = session.client(
            "logs",
            region_name=self.region_name or settings.aws.cloudfront_region,
        )
        manual_log_group = self._normalize_log_group_identifier(settings.aws.cwl_log_group or "")
        distribution_log_groups = self._distribution_log_groups(
            inventory,
            manual_log_group=manual_log_group,
            logs_client=logs_client,
        )
        distribution_source_keys = self._distribution_source_keys(
            distribution_log_groups,
            manual_log_group=manual_log_group,
        )

        upload_by_distribution: dict[str, SourceMetrics] = {}
        updated_distributions = dict(state.distributions)
        from_cache = True
        query_needed = refresh
        target_distribution_ids = set(distribution_log_groups)
        if not query_needed:
            for distribution in inventory.distributions:
                target_distribution = bool(manual_log_group) or (
                    distribution.distribution_id in target_distribution_ids
                )
                if not target_distribution:
                    continue
                existing = updated_distributions.get(distribution.distribution_id) or DistributionCacheRecord(
                    distribution_id=distribution.distribution_id
                )
                cached = existing.cwl
                source_key = distribution_source_keys.get(
                    distribution.distribution_id,
                    self._empty_source_key(manual_log_group=manual_log_group),
                )
                cache_is_current_month = cached.month_key == month_key
                cache_has_upload = cached.upload is not None
                cache_is_fresh = (
                    cache_is_current_month
                    and cache_has_upload
                    and cached.source_key == source_key
                    and cache_policy.is_fresh(cached.last_updated, now=now)
                )
                if not cache_is_fresh:
                    query_needed = True
                    break

        query_groups = tuple(
            sorted({group for groups in distribution_log_groups.values() for group in groups})
        )
        query_batches = tuple(self._chunked(query_groups, MAX_LOG_GROUP_IDENTIFIERS_PER_QUERY))
        batch_results: list[dict[str, int] | None] = []
        group_to_batch_index: dict[str, int] = {}

        if query_needed and query_batches:
            for batch in query_batches:
                try:
                    batch_results.append(
                        self._query_upload_totals(
                            logs_client,
                            log_group_identifiers=batch,
                            start_time=self._month_start(now),
                            end_time=now,
                        )
                    )
                except Exception:
                    batch_results.append(None)

            group_to_batch_index = {
                group: batch_index
                for batch_index, batch in enumerate(query_batches)
                for group in batch
            }

        for distribution in inventory.distributions:
            existing = updated_distributions.get(distribution.distribution_id) or DistributionCacheRecord(
                distribution_id=distribution.distribution_id
            )
            cached = existing.cwl
            source_key = distribution_source_keys.get(
                distribution.distribution_id,
                self._empty_source_key(manual_log_group=manual_log_group),
            )
            cache_is_current_month = cached.month_key == month_key
            cache_has_upload = cached.upload is not None
            cache_is_fresh = (
                not refresh
                and cache_is_current_month
                and cache_has_upload
                and cached.source_key == source_key
                and cache_policy.is_fresh(cached.last_updated, now=now)
            )
            if cache_is_fresh:
                upload_by_distribution[distribution.distribution_id] = cached
                continue

            if not query_needed:
                if cached.upload is not None and cached.source_key == source_key:
                    upload_by_distribution[distribution.distribution_id] = cached
                else:
                    cleared = self._empty_metrics(
                        month_key=month_key,
                        source_key=source_key,
                        last_updated=now,
                    )
                    upload_by_distribution[distribution.distribution_id] = cleared
                    updated_distributions[distribution.distribution_id] = replace(
                        existing,
                        cwl=cleared,
                    )
                continue

            batch_indexes = tuple(
                sorted({group_to_batch_index[group] for group in distribution_log_groups.get(distribution.distribution_id, ())})
            )
            if not batch_indexes:
                if cached.upload is not None and cached.source_key == source_key:
                    upload_by_distribution[distribution.distribution_id] = cached
                else:
                    cleared = self._empty_metrics(
                        month_key=month_key,
                        source_key=source_key,
                        last_updated=now,
                    )
                    upload_by_distribution[distribution.distribution_id] = cleared
                    updated_distributions[distribution.distribution_id] = replace(
                        existing,
                        cwl=cleared,
                    )
                continue

            if any(batch_results[index] is None for index in batch_indexes):
                if cached.upload is not None and cached.source_key == source_key:
                    upload_by_distribution[distribution.distribution_id] = cached
                else:
                    cleared = self._empty_metrics(
                        month_key=month_key,
                        source_key=source_key,
                        last_updated=now,
                    )
                    upload_by_distribution[distribution.distribution_id] = cleared
                    updated_distributions[distribution.distribution_id] = replace(
                        existing,
                        cwl=cleared,
                    )
                continue

            total_upload = sum(
                batch_results[index].get(distribution.distribution_id, 0)
                for index in batch_indexes
                if batch_results[index] is not None
            )
            refreshed = SourceMetrics(
                download=cached.download,
                upload=total_upload,
                requests=cached.requests,
                last_updated=now,
                month_key=month_key,
                source_key=source_key,
            )
            from_cache = False
            upload_by_distribution[distribution.distribution_id] = refreshed
            updated_distributions[distribution.distribution_id] = replace(existing, cwl=refreshed)

        updated_state = replace(
            state,
            profile_name=inventory.profile_name,
            distributions=updated_distributions,
        )
        cache_store.write(updated_state.to_payload())
        return CloudFrontLogsUploadSnapshot(
            profile_name=inventory.profile_name,
            upload_by_distribution=upload_by_distribution,
            from_cache=from_cache,
        )

    def _query_upload_totals(
        self,
        logs_client: object,
        *,
        log_group_identifiers: tuple[str, ...],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, int]:
        response = logs_client.start_query(
            logGroupIdentifiers=list(log_group_identifiers),
            queryString=LOGS_INSIGHTS_QUERY,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            limit=1000,
        )
        query_id = str(response.get("queryId", "")).strip()
        if not query_id:
            raise RuntimeError("CloudWatch Logs query did not return a queryId")

        deadline = time.monotonic() + self.query_timeout_seconds
        while True:
            result = logs_client.get_query_results(queryId=query_id)
            status = str(result.get("status", "")).strip()
            if status == "Complete":
                return self._sum_uploads_by_distribution(result)
            if status in {"Failed", "Cancelled", "Timeout", "Unknown"}:
                raise RuntimeError(f"CloudWatch Logs query ended with status {status}")
            if time.monotonic() >= deadline:
                raise TimeoutError("CloudWatch Logs query timed out")
            if self.query_poll_interval_seconds > 0:
                time.sleep(self.query_poll_interval_seconds)

    @staticmethod
    def _sum_uploads_by_distribution(result: object) -> dict[str, int]:
        if not isinstance(result, dict):
            return {}
        rows = result.get("results", []) or []
        totals: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, list):
                continue
            distribution_id = ""
            upload_value = 0
            has_upload = False
            for field in row:
                if not isinstance(field, dict):
                    continue
                field_name = str(field.get("field", "")).strip()
                if field_name == "DistributionId":
                    distribution_id = str(field.get("value", "")).strip()
                elif field_name == "uploads":
                    try:
                        upload_value = int(float(field.get("value", 0)))
                        has_upload = True
                    except (TypeError, ValueError):
                        has_upload = False
            if distribution_id and has_upload:
                totals[distribution_id] = totals.get(distribution_id, 0) + upload_value
        return totals

    @staticmethod
    def _list_delivery_destination_resource_arns(logs_client: object) -> dict[str, str]:
        paginator = logs_client.get_paginator("describe_delivery_destinations")
        destination_resource_arns: dict[str, str] = {}
        for page in paginator.paginate():
            destinations: list[dict[str, object]] = page.get("deliveryDestinations", []) or []
            for destination in destinations:
                if not isinstance(destination, dict):
                    continue
                delivery_destination_arn = str(destination.get("arn", "")).strip()
                delivery_destination_configuration = destination.get(
                    "deliveryDestinationConfiguration"
                )
                if not isinstance(delivery_destination_configuration, dict):
                    continue
                resource_arn = str(
                    delivery_destination_configuration.get("destinationResourceArn", "")
                ).strip()
                resource_arn = CloudFrontLogsUploadService._normalize_log_group_identifier(
                    resource_arn
                )
                if delivery_destination_arn and resource_arn:
                    destination_resource_arns[delivery_destination_arn] = resource_arn
        return destination_resource_arns

    @classmethod
    def _distribution_log_groups(
        cls,
        inventory: CloudFrontInventory,
        *,
        manual_log_group: str | None,
        logs_client: object | None,
    ) -> dict[str, tuple[str, ...]]:
        if manual_log_group:
            return {
                distribution.distribution_id: (manual_log_group,)
                for distribution in inventory.distributions
                if distribution.distribution_id
            }

        deliveries = inventory.standard_log_deliveries
        if not deliveries:
            return {}

        needs_resolution = any(
            (delivery.delivery_destination_type or "").upper() == "CWL"
            and not delivery.delivery_destination_resource_arn
            for records in deliveries.values()
            for delivery in records
        )
        destination_resource_arns = (
            cls._list_delivery_destination_resource_arns(logs_client)
            if needs_resolution and logs_client is not None
            else {}
        )

        distribution_log_groups: dict[str, tuple[str, ...]] = {}
        for distribution_id, records in deliveries.items():
            groups = {
                cls._normalize_log_group_identifier(
                    delivery.delivery_destination_resource_arn
                    or destination_resource_arns.get(delivery.delivery_destination_arn or "")
                    or ""
                )
                for delivery in records
                if (delivery.delivery_destination_type or "").upper() == "CWL"
            }
            groups.discard("")
            if groups:
                distribution_log_groups[distribution_id] = tuple(sorted(groups))
        return distribution_log_groups

    @staticmethod
    def _distribution_source_keys(
        distribution_log_groups: dict[str, tuple[str, ...]],
        *,
        manual_log_group: str | None,
    ) -> dict[str, str]:
        if manual_log_group:
            source_key = f"manual:{manual_log_group}"
            return {
                distribution_id: source_key
                for distribution_id in distribution_log_groups
            }
        return {
            distribution_id: f"auto:{'|'.join(groups)}"
            for distribution_id, groups in distribution_log_groups.items()
            if groups
        }

    @staticmethod
    def _chunked(values: tuple[str, ...], size: int) -> tuple[tuple[str, ...], ...]:
        if size <= 0:
            return (values,)
        return tuple(values[index : index + size] for index in range(0, len(values), size))

    @staticmethod
    def _normalize_log_group_identifier(value: str) -> str:
        text = value.strip()
        if text.endswith(":*"):
            return text[:-2]
        if text.endswith("*"):
            return text[:-1]
        return text

    @staticmethod
    def _month_key(value: datetime) -> str:
        return value.strftime("%Y-%m")

    @staticmethod
    def _month_start(value: datetime) -> datetime:
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _coerce_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _empty_source_key(*, manual_log_group: str | None) -> str:
        if manual_log_group:
            return f"manual:{manual_log_group}"
        return "auto:none"

    @staticmethod
    def _empty_metrics(
        *,
        month_key: str,
        source_key: str,
        last_updated: datetime | None = None,
    ) -> SourceMetrics:
        return SourceMetrics(
            month_key=month_key,
            source_key=source_key,
            last_updated=last_updated,
        )
