from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

import boto3

from cft.cache.policies import CachePolicy, utc_now
from cft.cache.store import JsonFileStore
from cft.config.paths import AppPaths, get_app_paths
from cft.config.settings import AppSettings, load_app_settings
from cft.models.cache import DistributionCacheRecord, ProfileCacheState, SourceMetrics

from .cloudfront import CloudFrontInventory

SessionFactory = Callable[..., boto3.Session]

CLOUDFRONT_NAMESPACE = "AWS/CloudFront"
CLOUDFRONT_DIMENSIONS = ("DistributionId", "Region")
CLOUDFRONT_REGION = "Global"
CLOUDFRONT_METRICS = ("BytesDownloaded", "Requests")
CLOUDFRONT_PERIOD_SECONDS = 3600


@dataclass(frozen=True)
class CloudFrontUsageSnapshot:
    profile_name: str
    usage_by_distribution: dict[str, SourceMetrics]
    from_cache: bool


class CloudFrontUsageService:
    """Read-through CloudWatch usage cache for current-month distribution totals."""

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

    def load(
        self,
        inventory: CloudFrontInventory,
        *,
        refresh: bool = False,
    ) -> CloudFrontUsageSnapshot:
        settings = self.settings or load_app_settings(
            self.paths, profile_name=inventory.profile_name
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
        cache_policy = CachePolicy.from_seconds(settings.cache.usage_ttl_seconds)

        usage_by_distribution: dict[str, SourceMetrics] = {}
        updated_distributions = dict(state.distributions)
        from_cache = True
        cloudwatch = None

        for distribution in inventory.distributions:
            existing = updated_distributions.get(distribution.distribution_id) or DistributionCacheRecord(
                distribution_id=distribution.distribution_id
            )
            cached = existing.cw
            cache_is_current_month = cached.month_key == month_key
            cache_is_fresh = (
                not refresh
                and cache_is_current_month
                and cache_policy.is_fresh(cached.last_updated, now=now)
            )
            if cache_is_fresh:
                usage_by_distribution[distribution.distribution_id] = cached
                continue

            if cloudwatch is None:
                try:
                    cloudwatch = session.client(
                        "cloudwatch",
                        region_name=self.region_name or settings.aws.cloudfront_region,
                    )
                except Exception:
                    if cached.download is not None or cached.requests is not None:
                        usage_by_distribution[distribution.distribution_id] = cached
                        continue
                    usage_by_distribution[distribution.distribution_id] = SourceMetrics(
                        month_key=month_key
                    )
                    continue

            query_start = self._month_start(now)
            base_download = 0
            base_requests = 0
            if cache_is_current_month and cached.last_updated is not None:
                query_start = cached.last_updated
                base_download = cached.download or 0
                base_requests = cached.requests or 0

            try:
                refreshed = self._refresh_distribution_usage(
                    cloudwatch,
                    distribution_id=distribution.distribution_id,
                    start_time=query_start,
                    end_time=now,
                    base_download=base_download,
                    base_requests=base_requests,
                    month_key=month_key,
                )
            except Exception:
                if cached.download is not None or cached.requests is not None:
                    usage_by_distribution[distribution.distribution_id] = cached
                    continue
                usage_by_distribution[distribution.distribution_id] = SourceMetrics(
                    month_key=month_key
                )
                continue

            from_cache = False
            usage_by_distribution[distribution.distribution_id] = refreshed
            updated_distributions[distribution.distribution_id] = replace(existing, cw=refreshed)

        updated_state = replace(
            state,
            profile_name=inventory.profile_name,
            distributions=updated_distributions,
        )
        cache_store.write(updated_state.to_payload())
        return CloudFrontUsageSnapshot(
            profile_name=inventory.profile_name,
            usage_by_distribution=usage_by_distribution,
            from_cache=from_cache,
        )

    def _refresh_distribution_usage(
        self,
        cloudwatch_client: object,
        *,
        distribution_id: str,
        start_time: datetime,
        end_time: datetime,
        base_download: int,
        base_requests: int,
        month_key: str,
    ) -> SourceMetrics:
        download = base_download + self._metric_sum(
            cloudwatch_client,
            distribution_id=distribution_id,
            metric_name="BytesDownloaded",
            start_time=start_time,
            end_time=end_time,
        )
        requests = base_requests + self._metric_sum(
            cloudwatch_client,
            distribution_id=distribution_id,
            metric_name="Requests",
            start_time=start_time,
            end_time=end_time,
        )
        return SourceMetrics(
            download=download,
            upload=None,
            requests=requests,
            last_updated=end_time,
            month_key=month_key,
        )

    @staticmethod
    def _metric_sum(
        cloudwatch_client: object,
        *,
        distribution_id: str,
        metric_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        response = cloudwatch_client.get_metric_statistics(
            Namespace=CLOUDFRONT_NAMESPACE,
            MetricName=metric_name,
            Dimensions=[
                {"Name": CLOUDFRONT_DIMENSIONS[0], "Value": distribution_id},
                {"Name": CLOUDFRONT_DIMENSIONS[1], "Value": CLOUDFRONT_REGION},
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=CLOUDFRONT_PERIOD_SECONDS,
            Statistics=["Sum"],
        )
        datapoints = response.get("Datapoints", []) or []
        total = 0.0
        for datapoint in datapoints:
            if not isinstance(datapoint, dict):
                continue
            value = datapoint.get("Sum")
            if isinstance(value, (int, float)):
                total += float(value)
        return int(round(total))

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
