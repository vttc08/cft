from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from collections.abc import Callable

from cft.application.errors import DashboardLoadError
from cft.application.models import (
    DashboardLoadResult,
    DashboardSnapshot,
    DashboardWarning,
)
from cft.aws.cloudfront import CloudFrontInventoryService
from cft.aws.cloudfront_s3_logs import CloudFrontS3LogsUploadService
from cft.aws.cloudwatch import CloudFrontUsageService
from cft.aws.cloudwatch_logs import (
    CloudFrontLogsUploadService,
    CloudWatchLogGroupDiscoveryService,
)
from cft.aws.s3 import S3BucketDiscoveryService
from cft.cache.repository import ProfileStateRepository
from cft.config.paths import AppPaths, get_app_paths
from cft.config.settings import (
    AppSettings,
    load_app_settings,
    save_cwl_log_group_settings,
    save_data_export_settings,
    settings_profile_name,
)
from cft.data_exports import CurDataExportService
from cft.models.billing import BillingSnapshot
from cft.models.cache import SourceMetrics, normalize_distribution_type
from cft.models.configuration import (
    CloudWatchLogGroupSummary,
    CloudWatchLogsConfiguration,
    ConfigurationSnapshot,
    DataExportConfiguration,
    SaveCloudWatchLogs,
    SaveDataExport,
)
from cft.startup_trace import StartupTrace


class CftApplicationService:
    """Shared application use cases for Textual and future CLI frontends."""

    def __init__(
        self,
        profile_name: str | None = None,
        *,
        paths: AppPaths | None = None,
        now: Callable[[], datetime] = datetime.now,
        startup_trace: StartupTrace | None = None,
    ) -> None:
        self.requested_profile_name = profile_name
        self.paths = paths or get_app_paths()
        self._profile_name = settings_profile_name(profile_name)
        self.now = now
        self.startup_trace = startup_trace or StartupTrace.from_env()
        self.state_repository = ProfileStateRepository(self.paths)
        self.settings = self._load_settings()
        self._latest_snapshot: DashboardSnapshot | None = None
        self._build_services()

    @property
    def profile_name(self) -> str:
        return self._profile_name

    def _load_settings(self) -> AppSettings:
        return load_app_settings(self.paths, profile_name=self._profile_name)

    def _build_services(self) -> None:
        common = {
            "profile_name": self.requested_profile_name,
            "paths": self.paths,
            "settings": self.settings,
        }
        traced = {
            **common,
            "trace": self.startup_trace,
            "state_repository": self.state_repository,
        }
        self.inventory_service = CloudFrontInventoryService(**traced)
        self.usage_service = CloudFrontUsageService(**traced)
        self.s3_logs_upload_service = CloudFrontS3LogsUploadService(**traced)
        self.logs_upload_service = CloudFrontLogsUploadService(**traced)
        self.billing_service = CurDataExportService(**traced)
        self.log_group_service = CloudWatchLogGroupDiscoveryService(**common)
        self.bucket_service = S3BucketDiscoveryService(**common)

    def _reload_settings(self) -> None:
        self.settings = self._load_settings()
        self._build_services()

    def load_dashboard(self, *, refresh: bool = False) -> DashboardLoadResult:
        self._reload_settings()
        warnings: list[DashboardWarning] = []

        try:
            with self.startup_trace.step("startup.inventory", refresh=refresh) as step:
                inventory = self.inventory_service.load(refresh=refresh)
                step["from_cache"] = inventory.from_cache
        except Exception as error:
            raise DashboardLoadError("inventory", error) from error

        try:
            with self.startup_trace.step("startup.usage", refresh=refresh) as step:
                usage_snapshot = self.usage_service.load(inventory, refresh=refresh)
                usage = usage_snapshot.usage_by_distribution
                usage_from_cache = usage_snapshot.from_cache
                if not self.settings.aws.cloudfront_bytes_uploaded_metric:
                    s3_snapshot = self.s3_logs_upload_service.load(inventory, refresh=refresh)
                    logs_snapshot = self.logs_upload_service.load(inventory, refresh=refresh)
                    usage = self._merge_usage(
                        self._merge_usage(usage, s3_snapshot.upload_by_distribution),
                        logs_snapshot.upload_by_distribution,
                    )
                    usage_from_cache = (
                        usage_from_cache
                        and s3_snapshot.from_cache
                        and logs_snapshot.from_cache
                    )
                step["from_cache"] = usage_from_cache
        except Exception as error:
            raise DashboardLoadError("usage", error) from error

        try:
            with self.startup_trace.step("startup.billing", refresh=refresh) as step:
                billing = self.billing_service.load(refresh=refresh)
                step["from_cache"] = billing.from_cache
        except Exception as error:
            billing = BillingSnapshot(
                profile_name=self._profile_name,
                configured=bool(
                    self.settings.data_export.bucket
                    and self.settings.data_export.export_name
                ),
                message=f"Billing unavailable: {error}",
            )
            warnings.append(DashboardWarning(stage="billing", message=str(error)))

        state = self.state_repository.load(self._profile_name)
        snapshot = DashboardSnapshot(
            inventory=inventory,
            usage_by_distribution=usage,
            billing=billing,
            configuration=self.get_configuration(),
            onboarding_seen=state.onboarding_seen,
        )
        self._latest_snapshot = snapshot
        return DashboardLoadResult(
            snapshot=snapshot,
            inventory_from_cache=inventory.from_cache,
            usage_from_cache=usage_from_cache,
            billing_from_cache=billing.from_cache,
            warnings=tuple(warnings),
        )

    def get_configuration(self) -> ConfigurationSnapshot:
        settings = load_app_settings(
            self.paths,
            profile_name=self._profile_name,
            create=False,
        )
        return ConfigurationSnapshot(
            profile_name=self._profile_name,
            data_export=DataExportConfiguration(
                bucket=settings.data_export.bucket,
                prefix=settings.data_export.prefix,
                export_name=settings.data_export.export_name,
            ),
            cloudwatch_logs=CloudWatchLogsConfiguration(
                log_group=settings.aws.cwl_log_group,
            ),
        )

    def write_startup_trace(self) -> None:
        self.startup_trace.write()

    def discover_s3_buckets(self) -> tuple[str, ...]:
        self._reload_settings()
        return self.bucket_service.list_bucket_names()

    def discover_cwl_log_groups(self) -> tuple[CloudWatchLogGroupSummary, ...]:
        self._reload_settings()
        return self.log_group_service.list_log_groups()

    def save_data_export(self, command: SaveDataExport) -> ConfigurationSnapshot:
        save_data_export_settings(
            paths=self.paths,
            profile_name=self._profile_name,
            bucket=command.bucket,
            prefix=command.prefix,
            export_name=command.export_name,
        )
        self._reload_settings()
        return self.get_configuration()

    def save_cwl_log_group(self, command: SaveCloudWatchLogs) -> ConfigurationSnapshot:
        save_cwl_log_group_settings(
            paths=self.paths,
            profile_name=self._profile_name,
            log_group=command.log_group,
        )
        self._reload_settings()
        return self.get_configuration()

    def set_distribution_type(
        self,
        distribution_id: str,
        distribution_type: str,
    ) -> DashboardSnapshot | None:
        normalized_type = normalize_distribution_type(distribution_type)
        state_profile_name = (
            self._latest_snapshot.inventory.profile_name
            if self._latest_snapshot is not None
            else self._profile_name
        )
        self.state_repository.set_distribution_type(
            profile_name=state_profile_name,
            distribution_id=distribution_id,
            distribution_type=normalized_type,
        )
        if self._latest_snapshot is None:
            return None
        inventory = replace(
            self._latest_snapshot.inventory,
            distribution_types={
                **self._latest_snapshot.inventory.distribution_types,
                distribution_id: normalized_type,
            },
        )
        self._latest_snapshot = replace(self._latest_snapshot, inventory=inventory)
        return self._latest_snapshot

    def mark_onboarding_seen(self) -> None:
        self.state_repository.mark_onboarding_seen(self._profile_name)
        if self._latest_snapshot is not None:
            self._latest_snapshot = replace(self._latest_snapshot, onboarding_seen=True)

    def has_seen_onboarding(self) -> bool:
        return self.state_repository.load(self._profile_name).onboarding_seen

    @staticmethod
    def _merge_usage(
        base_usage: dict[str, SourceMetrics],
        overlay_usage: dict[str, SourceMetrics],
    ) -> dict[str, SourceMetrics]:
        merged = dict(base_usage)
        for distribution_id, overlay in overlay_usage.items():
            existing = merged.get(distribution_id, SourceMetrics())
            merged[distribution_id] = replace(
                existing,
                upload=overlay.upload if overlay.upload is not None else existing.upload,
                last_updated=overlay.last_updated or existing.last_updated,
                month_key=overlay.month_key or existing.month_key,
                source_key=overlay.source_key or existing.source_key,
            )
        return merged
