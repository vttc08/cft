from __future__ import annotations

from typing import Protocol

from cft.application.models import DashboardLoadResult, DashboardSnapshot
from cft.models.configuration import (
    CloudWatchLogGroupSummary,
    ConfigurationSnapshot,
    SaveCloudWatchLogs,
    SaveDataExport,
)


class CftApplication(Protocol):
    @property
    def profile_name(self) -> str: ...

    def load_dashboard(self, *, refresh: bool = False) -> DashboardLoadResult: ...

    def write_startup_trace(self) -> None: ...

    def get_configuration(self) -> ConfigurationSnapshot: ...

    def discover_s3_buckets(self) -> tuple[str, ...]: ...

    def discover_cwl_log_groups(self) -> tuple[CloudWatchLogGroupSummary, ...]: ...

    def save_data_export(self, command: SaveDataExport) -> ConfigurationSnapshot: ...

    def save_cwl_log_group(self, command: SaveCloudWatchLogs) -> ConfigurationSnapshot: ...

    def set_distribution_type(
        self, distribution_id: str, distribution_type: str
    ) -> DashboardSnapshot | None: ...

    def has_seen_onboarding(self) -> bool: ...

    def mark_onboarding_seen(self) -> None: ...
