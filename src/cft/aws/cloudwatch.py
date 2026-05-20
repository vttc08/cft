from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

import boto3

from cft.cache.policies import CachePolicy, utc_now
from cft.cache.store import JsonFileStore
from cft.config.paths import AppPaths, get_app_paths
from cft.config.settings import AppSettings, load_app_settings, settings_profile_name
from cft.models.cache import DistributionCacheRecord, ProfileCacheState, SourceMetrics
from cft.startup_trace import StartupTrace

from .cloudfront import CloudFrontInventory

SessionFactory = Callable[..., boto3.Session]

CLOUDFRONT_NAMESPACE = "AWS/CloudFront"
CLOUDFRONT_DIMENSIONS = ("DistributionId", "Region")
CLOUDFRONT_REGION = "Global"
CLOUDFRONT_METRICS = ("BytesDownloaded", "BytesUploaded", "Requests")
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
        trace: StartupTrace | None = None,
    ) -> None:
        self.profile_name = profile_name
        self.region_name = region_name
        self.paths = paths or get_app_paths()
        self.settings = settings
        self.session_factory = session_factory
        self.now = now
        self.trace = trace

    def load(
        self,
        inventory: CloudFrontInventory,
        *,
        refresh: bool = False,
    ) -> CloudFrontUsageSnapshot:
        settings = self.settings or load_app_settings(
            self.paths, profile_name=settings_profile_name(inventory.profile_name)
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
        use_bytes_uploaded = settings.aws.cloudfront_bytes_uploaded_metric

        usage_by_distribution: dict[str, SourceMetrics] = {}
        updated_distributions = dict(state.distributions)
        from_cache = True
        cloudwatch = None
        session = None

        for distribution in inventory.distributions:
            existing = updated_distributions.get(distribution.distribution_id) or DistributionCacheRecord(
                distribution_id=distribution.distribution_id
            )
            cached = existing.cw
            cache_is_current_month = cached.month_key == month_key
            cache_has_complete_usage = (
                cached.download is not None
                and cached.requests is not None
                and (not use_bytes_uploaded or cached.upload is not None)
            )
            cache_is_fresh = (
                not refresh
                and cache_is_current_month
                and cache_has_complete_usage
                and cache_policy.is_fresh(cached.last_updated, now=now)
            )
            if cache_is_fresh:
                if self.trace is not None:
                    self.trace.emit(
                        "usage.cache",
                        distribution_id=distribution.distribution_id,
                        decision="cache_hit",
                    )
                usage_by_distribution[distribution.distribution_id] = cached
                continue

            if cloudwatch is None:
                try:
                    if session is None:
                        session = self.session_factory(
                            profile_name=inventory.profile_name,
                            region_name=self.region_name or settings.aws.cloudfront_region,
                        )
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
            if self.trace is not None:
                self.trace.emit(
                    "usage.cache",
                    distribution_id=distribution.distribution_id,
                    decision="refresh",
                    cached_download=cached.download,
                    cached_requests=cached.requests,
                )

            try:
                refreshed = self._refresh_distribution_usage(
                    cloudwatch,
                    distribution_id=distribution.distribution_id,
                    cached=cached,
                    cache_is_current_month=cache_is_current_month,
                    end_time=now,
                    month_key=month_key,
                    use_bytes_uploaded=use_bytes_uploaded,
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
        cache_store.write_if_changed(updated_state.to_payload())
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
        cached: SourceMetrics,
        cache_is_current_month: bool,
        end_time: datetime,
        month_key: str,
        use_bytes_uploaded: bool,
    ) -> SourceMetrics:
        download = self._refresh_metric(
            cloudwatch_client,
            distribution_id=distribution_id,
            metric_name="BytesDownloaded",
            cached_value=cached.download,
            cached_last_updated=cached.last_updated,
            cache_is_current_month=cache_is_current_month,
            end_time=end_time,
        )
        upload = cached.upload
        if use_bytes_uploaded:
            upload = self._refresh_metric(
                cloudwatch_client,
                distribution_id=distribution_id,
                metric_name="BytesUploaded",
                cached_value=cached.upload,
                cached_last_updated=cached.last_updated,
                cache_is_current_month=cache_is_current_month,
                end_time=end_time,
            )
        requests = self._refresh_metric(
            cloudwatch_client,
            distribution_id=distribution_id,
            metric_name="Requests",
            cached_value=cached.requests,
            cached_last_updated=cached.last_updated,
            cache_is_current_month=cache_is_current_month,
            end_time=end_time,
        )
        return SourceMetrics(
            download=download,
            upload=upload,
            requests=requests,
            last_updated=end_time,
            month_key=month_key,
        )

    @staticmethod
    def _refresh_metric(
        cloudwatch_client: object,
        *,
        distribution_id: str,
        metric_name: str,
        cached_value: int | None,
        cached_last_updated: datetime | None,
        cache_is_current_month: bool,
        end_time: datetime,
    ) -> int | None:
        start_time = end_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        base_value = 0
        if cache_is_current_month and cached_value is not None and cached_last_updated is not None:
            start_time = cached_last_updated
            base_value = cached_value

        try:
            metric_value = CloudFrontUsageService._metric_sum(
                cloudwatch_client,
                distribution_id=distribution_id,
                metric_name=metric_name,
                start_time=start_time,
                end_time=end_time,
            )
        except Exception:
            return cached_value
        return base_value + metric_value

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
