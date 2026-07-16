from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from cft.application.service import CftApplicationService
from cft.aws.cloudfront_s3_logs import CloudFrontS3LogsUploadSnapshot
from cft.aws.cloudwatch import CloudFrontUsageSnapshot
from cft.aws.cloudwatch_logs import CloudFrontLogsUploadSnapshot
from cft.cache.repository import (
    ProfileStateRepository,
    UnsupportedStateVersionError,
)
from cft.config.paths import AppPaths
from cft.models.billing import BillingSnapshot
from cft.models.cache import DistributionCacheRecord, ProfileCacheState, SourceMetrics
from cft.models.configuration import SaveCloudWatchLogs
from cft.models.distribution import DistributionSummary
from cft.models.inventory import AccountIdentity, CloudFrontInventory


def inventory() -> CloudFrontInventory:
    return CloudFrontInventory(
        profile_name="dev",
        identity=AccountIdentity(account_id="123", arn="arn", user_id="user"),
        distributions=(
            DistributionSummary(
                distribution_id="E123",
                arn="arn:aws:cloudfront::123:distribution/E123",
                comment="site",
                domain_name="example.cloudfront.net",
                enabled=True,
                status="Deployed",
                aliases=(),
                origins=(),
                last_modified_time=None,
            ),
        ),
    )


class Loader:
    def __init__(self, value: object) -> None:
        self.value = value
        self.refreshes: list[bool] = []

    def load(self, *args: object, refresh: bool = False) -> object:
        self.refreshes.append(refresh)
        return self.value


def test_application_orchestrates_and_merges_source_usage(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    application = CftApplicationService(profile_name="dev", paths=paths)
    application._reload_settings = lambda: None  # type: ignore[method-assign]
    application.inventory_service = Loader(inventory())
    application.usage_service = Loader(
        CloudFrontUsageSnapshot(
            profile_name="dev",
            usage_by_distribution={
                "E123": SourceMetrics(download=123, requests=456, upload=1)
            },
            from_cache=False,
        )
    )
    application.s3_logs_upload_service = Loader(
        CloudFrontS3LogsUploadSnapshot(
            profile_name="dev",
            upload_by_distribution={"E123": SourceMetrics(upload=789, source_key="s3")},
            from_cache=False,
        )
    )
    application.logs_upload_service = Loader(
        CloudFrontLogsUploadSnapshot(
            profile_name="dev",
            upload_by_distribution={"E123": SourceMetrics(upload=999, source_key="cwl")},
            from_cache=False,
        )
    )
    application.billing_service = Loader(
        BillingSnapshot(profile_name="dev", configured=False, from_cache=True)
    )

    result = application.load_dashboard(refresh=True)

    usage = result.snapshot.usage_by_distribution["E123"]
    assert (usage.download, usage.requests, usage.upload, usage.source_key) == (
        123,
        456,
        999,
        "cwl",
    )
    assert application.inventory_service.refreshes == [True]
    assert application.usage_service.refreshes == [True]
    assert result.from_cache is False


def test_application_uses_cloudwatch_upload_without_log_services(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    application = CftApplicationService(profile_name="dev", paths=paths)
    application.settings = replace(
        application.settings,
        aws=replace(application.settings.aws, cloudfront_bytes_uploaded_metric=True),
    )
    application._reload_settings = lambda: None  # type: ignore[method-assign]
    application.inventory_service = Loader(inventory())
    application.usage_service = Loader(
        CloudFrontUsageSnapshot(
            profile_name="dev",
            usage_by_distribution={"E123": SourceMetrics(download=1, upload=2, requests=3)},
            from_cache=False,
        )
    )
    application.s3_logs_upload_service = Loader(AssertionError("must not load S3 logs"))
    application.logs_upload_service = Loader(AssertionError("must not load CWL logs"))
    application.billing_service = Loader(
        BillingSnapshot(profile_name="dev", configured=False, from_cache=True)
    )

    result = application.load_dashboard()

    assert result.snapshot.usage_by_distribution["E123"].upload == 2
    assert application.s3_logs_upload_service.refreshes == []
    assert application.logs_upload_service.refreshes == []


def test_configuration_save_rebuilds_services_with_current_settings(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    application = CftApplicationService(profile_name="dev", paths=paths)

    configuration = application.save_cwl_log_group(
        SaveCloudWatchLogs(log_group="arn:aws:logs:us-east-1:123:log-group:cloudfront")
    )

    assert configuration.cloudwatch_logs.log_group.endswith("log-group:cloudfront")
    assert application.settings.aws.cwl_log_group == configuration.cloudwatch_logs.log_group
    assert application.logs_upload_service.settings.aws.cwl_log_group == (
        configuration.cloudwatch_logs.log_group
    )


def test_profile_state_repository_preserves_unrelated_fields_and_rejects_future_schema(
    tmp_path,
) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    repository = ProfileStateRepository(paths)
    repository.save(
        ProfileCacheState(
            profile_name="dev",
            distributions={
                "E123": DistributionCacheRecord(
                    distribution_id="E123",
                    inventory={"comment": "site"},
                    cw=SourceMetrics(download=123),
                )
            },
        )
    )

    updated = repository.set_distribution_type(
        profile_name="dev",
        distribution_id="E123",
        distribution_type="Free",
    )

    assert updated.distributions["E123"].inventory == {"comment": "site"}
    assert updated.distributions["E123"].cw.download == 123
    assert updated.distributions["E123"].type == "Free"

    future = replace(updated, schema_version=2)
    repository.save(future)
    with pytest.raises(UnsupportedStateVersionError, match="schema version 2"):
        repository.load("dev")


def test_tui_modules_do_not_import_infrastructure_services() -> None:
    tui_root = Path(__file__).parents[1] / "src" / "cft" / "tui"
    forbidden = (
        "cft.aws",
        "cft.cache.repository",
        "cft.cache.store",
        "cft.config.settings",
        "cft.data_exports",
    )
    violations: list[str] = []
    for path in tui_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(forbidden):
                    violations.append(f"{path.name}: {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden):
                        violations.append(f"{path.name}: {alias.name}")
    assert violations == []
