from __future__ import annotations

import json
from datetime import datetime, timezone

from cft.aws.cloudfront import AccountIdentity, CloudFrontInventory
from cft.aws.cloudwatch_logs import CloudFrontLogsUploadService
from cft.aws.cloudwatch_logs import CloudWatchLogGroupDiscoveryService
from cft.aws.cloudwatch_logs import CloudWatchLogGroupSummary
from cft.config.paths import AppPaths
from cft.config.settings import AppSettings, AwsSettings, CacheSettings
from cft.models.cache import SourceMetrics, StandardLogDeliveryRecord
from cft.models.distribution import DistributionSummary


def fake_inventory() -> CloudFrontInventory:
    return CloudFrontInventory(
        profile_name="dev",
        identity=AccountIdentity(
            account_id="123456789012",
            arn="arn:aws:iam::123456789012:user/test",
            user_id="AIDA",
        ),
        distributions=(
            DistributionSummary(
                distribution_id="E123",
                arn="arn:aws:cloudfront::123456789012:distribution/E123",
                comment="site",
                domain_name="d111.cloudfront.net",
                enabled=True,
                status="Deployed",
                aliases=(),
                origins=(),
                last_modified_time=None,
            ),
        ),
        standard_log_deliveries={
            "E123": (
                StandardLogDeliveryRecord(
                    delivery_id="delivery-1",
                    delivery_arn="arn:aws:logs:us-east-1:123456789012:delivery:delivery-1",
                    delivery_destination_arn="arn:aws:logs:us-east-1:123456789012:delivery-destination:dest-1",
                    delivery_destination_resource_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs",
                    delivery_destination_type="CWL",
                    delivery_source_name="CreatedByCloudFront-E123-ACCESS_LOGS",
                ),
            )
        },
    )


class FakeLogsPaginator:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages

    def paginate(self) -> list[dict[str, object]]:
        return self.pages


class FakeLogsClient:
    def __init__(self) -> None:
        self.start_query_calls: list[dict[str, object]] = []
        self.query_results_calls: list[str] = []

    def get_paginator(self, name: str) -> FakeLogsPaginator:
        assert name == "describe_delivery_destinations"
        return FakeLogsPaginator(
            [
                {
                    "deliveryDestinations": [
                        {
                            "arn": "arn:aws:logs:us-east-1:123456789012:delivery-destination:dest-1",
                            "deliveryDestinationConfiguration": {
                                "destinationResourceArn": "arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs"
                            },
                        }
                    ]
                }
            ]
        )

    def start_query(self, **kwargs: object) -> dict[str, str]:
        self.start_query_calls.append(kwargs)
        return {"queryId": "query-1"}

    def get_query_results(self, *, queryId: str) -> dict[str, object]:
        self.query_results_calls.append(queryId)
        return {
            "status": "Complete",
            "results": [
                [
                    {"field": "DistributionId", "value": "E123"},
                    {"field": "uploads", "value": "1234"},
                ]
            ],
            "statistics": {"bytesScanned": 42.0},
        }


class FakeDiscoveryLogsPaginator:
    def __init__(self, groups: list[dict[str, object]]) -> None:
        self.groups = groups

    def paginate(self) -> list[dict[str, object]]:
        return [{"logGroups": self.groups}]


class FakeDiscoveryLogsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.log_groups = [
            {
                "logGroupName": "cloudfrontlogs",
                "logGroupArn": "arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs",
                "logGroupClass": "INFREQUENT_ACCESS",
            },
            {
                "logGroupName": "cloudfrontlogs2",
                "logGroupArn": "arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs2",
                "logGroupClass": "STANDARD",
            },
        ]

    def list_log_groups(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"logGroups": self.log_groups}


class FakeSession:
    profile_name = "dev"

    def __init__(self, logs_client: FakeLogsClient) -> None:
        self.logs_client = logs_client

    def client(self, service_name: str, **_: object) -> object:
        assert service_name == "logs"
        return self.logs_client


class FakeDiscoverySession:
    profile_name = "dev"

    def __init__(self, logs_client: FakeDiscoveryLogsClient) -> None:
        self.logs_client = logs_client

    def client(self, service_name: str, **_: object) -> object:
        assert service_name == "logs"
        return self.logs_client


def test_cloudwatch_log_group_discovery_service_lists_log_groups(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    groups = CloudWatchLogGroupDiscoveryService(
        profile_name="dev",
        paths=paths,
        session_factory=lambda **_: FakeDiscoverySession(FakeDiscoveryLogsClient()),  # type: ignore[arg-type]
    ).list_log_groups()

    assert groups == (
        CloudWatchLogGroupSummary(
            log_group_name="cloudfrontlogs",
            log_group_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs",
            log_group_class="INFREQUENT_ACCESS",
        ),
        CloudWatchLogGroupSummary(
            log_group_name="cloudfrontlogs2",
            log_group_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs2",
            log_group_class="STANDARD",
        ),
    )


def test_cloudfront_logs_upload_service_queries_cwl_and_writes_cache(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(logs_upload_ttl_seconds=3600))
    logs_client = FakeLogsClient()

    snapshot = CloudFrontLogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(logs_client),  # type: ignore[arg-type]
        now=lambda: now,
        query_poll_interval_seconds=0,
    ).load(fake_inventory())

    payload = json.loads(paths.profile_state_file("dev").read_text(encoding="utf-8"))

    assert snapshot.from_cache is False
    assert snapshot.upload_by_distribution["E123"].upload == 1234
    assert logs_client.start_query_calls[0]["logGroupIdentifiers"] == [
        "arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs"
    ]
    assert logs_client.start_query_calls[0]["queryString"] == "stats sum(`cs-bytes`) as uploads by DistributionId"
    assert payload["distributions"]["E123"]["cwl"]["upload"] == 1234
    assert payload["distributions"]["E123"]["cwl"]["month_key"] == "2026-05"


def test_cloudfront_logs_upload_service_batches_shared_log_group_once(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(logs_upload_ttl_seconds=3600))
    logs_client = FakeLogsClient()

    inventory = CloudFrontInventory(
        profile_name="dev",
        identity=fake_inventory().identity,
        distributions=(
            fake_inventory().distributions[0],
            DistributionSummary(
                distribution_id="E456",
                arn="arn:aws:cloudfront::123456789012:distribution/E456",
                comment="site-2",
                domain_name="d222.cloudfront.net",
                enabled=True,
                status="Deployed",
                aliases=(),
                origins=(),
                last_modified_time=None,
            ),
        ),
        standard_log_deliveries={
            "E123": (
                StandardLogDeliveryRecord(
                    delivery_id="delivery-1",
                    delivery_arn="arn:aws:logs:us-east-1:123456789012:delivery:delivery-1",
                    delivery_destination_arn="arn:aws:logs:us-east-1:123456789012:delivery-destination:dest-1",
                    delivery_destination_resource_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs",
                    delivery_destination_type="CWL",
                    delivery_source_name="CreatedByCloudFront-E123-ACCESS_LOGS",
                ),
            ),
            "E456": (
                StandardLogDeliveryRecord(
                    delivery_id="delivery-2",
                    delivery_arn="arn:aws:logs:us-east-1:123456789012:delivery:delivery-2",
                    delivery_destination_arn="arn:aws:logs:us-east-1:123456789012:delivery-destination:dest-1",
                    delivery_destination_resource_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs",
                    delivery_destination_type="CWL",
                    delivery_source_name="CreatedByCloudFront-E456-ACCESS_LOGS",
                ),
            ),
        },
    )

    snapshot = CloudFrontLogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(logs_client),  # type: ignore[arg-type]
        now=lambda: now,
        query_poll_interval_seconds=0,
    ).load(inventory)

    assert len(logs_client.start_query_calls) == 1
    assert logs_client.start_query_calls[0]["logGroupIdentifiers"] == [
        "arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs"
    ]
    assert snapshot.upload_by_distribution["E123"].upload == 1234
    assert snapshot.upload_by_distribution["E456"].upload == 0


def test_cloudfront_logs_upload_service_uses_manual_log_group_override(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(
        aws=AwsSettings(
            cwl_log_group="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs",
        ),
        cache=CacheSettings(logs_upload_ttl_seconds=3600),
    )
    logs_client = FakeLogsClient()

    inventory = CloudFrontInventory(
        profile_name="dev",
        identity=fake_inventory().identity,
        distributions=fake_inventory().distributions,
        standard_log_deliveries={},
    )

    snapshot = CloudFrontLogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(logs_client),  # type: ignore[arg-type]
        now=lambda: now,
        query_poll_interval_seconds=0,
    ).load(inventory)

    assert len(logs_client.start_query_calls) == 1
    assert logs_client.start_query_calls[0]["logGroupIdentifiers"] == [
        "arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs"
    ]
    assert snapshot.upload_by_distribution["E123"].upload == 1234


def test_cloudfront_logs_upload_service_invalidates_cached_source_key_on_override_change(
    tmp_path,
) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(
        aws=AwsSettings(
            cwl_log_group="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs",
        ),
        cache=CacheSettings(logs_upload_ttl_seconds=3600),
    )
    paths.profile_state_file("dev").parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_file("dev").write_text(
        json.dumps(
            {
                "profile_name": "dev",
                "distributions": {
                    "E123": {
                        "cwl": {
                            "upload": 999,
                            "last_updated": "2026-05-13T10:00:00Z",
                            "month_key": "2026-05",
                            "source_key": "manual:arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs-old",
                        }
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logs_client = FakeLogsClient()

    snapshot = CloudFrontLogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(logs_client),  # type: ignore[arg-type]
        now=lambda: now,
        query_poll_interval_seconds=0,
    ).load(fake_inventory())

    payload = json.loads(paths.profile_state_file("dev").read_text(encoding="utf-8"))

    assert snapshot.from_cache is False
    assert snapshot.upload_by_distribution["E123"].upload == 1234
    assert len(logs_client.start_query_calls) == 1
    assert payload["distributions"]["E123"]["cwl"]["source_key"] == (
        "manual:arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs"
    )


def test_cloudfront_logs_upload_service_strips_wildcard_from_log_group_arn(
    tmp_path,
) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(logs_upload_ttl_seconds=3600))

    inventory = fake_inventory()
    inventory = CloudFrontInventory(
        profile_name=inventory.profile_name,
        identity=inventory.identity,
        distributions=inventory.distributions,
        distribution_types=inventory.distribution_types,
        standard_log_deliveries={
            "E123": (
                StandardLogDeliveryRecord(
                    delivery_id="delivery-1",
                    delivery_arn="arn:aws:logs:us-east-1:123456789012:delivery:delivery-1",
                    delivery_destination_arn="arn:aws:logs:us-east-1:123456789012:delivery-destination:dest-1",
                    delivery_destination_resource_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs:*",
                    delivery_destination_type="CWL",
                    delivery_source_name="CreatedByCloudFront-E123-ACCESS_LOGS",
                ),
            )
        },
        cache_last_updated=inventory.cache_last_updated,
        from_cache=inventory.from_cache,
    )

    logs_client = FakeLogsClient()

    snapshot = CloudFrontLogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(logs_client),  # type: ignore[arg-type]
        now=lambda: now,
        query_poll_interval_seconds=0,
    ).load(inventory)

    assert snapshot.upload_by_distribution["E123"].upload == 1234
    assert logs_client.start_query_calls[0]["logGroupIdentifiers"] == [
        "arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs"
    ]


def test_cloudfront_logs_upload_service_uses_incremental_same_month_cache(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(logs_upload_ttl_seconds=3600))
    paths.profile_state_file("dev").parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_file("dev").write_text(
        json.dumps(
            {
                "profile_name": "dev",
                "distributions": {
                    "E123": {
                        "cwl": {
                            "upload": 100,
                            "last_updated": "2026-05-13T10:00:00Z",
                            "month_key": "2026-05",
                        }
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    logs_client = FakeLogsClient()

    snapshot = CloudFrontLogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(logs_client),  # type: ignore[arg-type]
        now=lambda: now,
        query_poll_interval_seconds=0,
    ).load(fake_inventory())

    assert snapshot.upload_by_distribution["E123"].upload == 1234
    assert logs_client.start_query_calls[0]["startTime"] == int(datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc).timestamp())
    assert logs_client.start_query_calls[0]["endTime"] == int(now.timestamp())
