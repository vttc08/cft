from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DataExportConfiguration:
    bucket: str | None = None
    prefix: str | None = None
    export_name: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.bucket and self.export_name)


@dataclass(frozen=True)
class CloudWatchLogsConfiguration:
    log_group: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.log_group and self.log_group.strip())


@dataclass(frozen=True)
class ConfigurationSnapshot:
    profile_name: str
    data_export: DataExportConfiguration = field(default_factory=DataExportConfiguration)
    cloudwatch_logs: CloudWatchLogsConfiguration = field(
        default_factory=CloudWatchLogsConfiguration
    )


@dataclass(frozen=True)
class CloudWatchLogGroupSummary:
    log_group_name: str
    log_group_arn: str | None = None
    log_group_class: str | None = None


@dataclass(frozen=True)
class SaveDataExport:
    bucket: str
    prefix: str | None
    export_name: str


@dataclass(frozen=True)
class SaveCloudWatchLogs:
    log_group: str
