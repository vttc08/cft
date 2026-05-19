from __future__ import annotations

import json
from datetime import datetime, timezone

import boto3
from botocore.stub import Stubber

from cft.aws.cloudfront import CloudFrontInventory
from cft.aws.cloudwatch import CloudFrontUsageService
from cft.config.paths import AppPaths
from cft.config.settings import AppSettings, CacheSettings
from cft.models.cache import ProfileCacheState
from cft.models.distribution import DistributionSummary


def fake_inventory() -> CloudFrontInventory:
    return CloudFrontInventory(
        profile_name="dev",
        identity=None,
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
    )


def cloudwatch_client() -> tuple[object, Stubber]:
    client = boto3.Session(
        aws_access_key_id="test",
        aws_secret_access_key="test",
        aws_session_token="test",
        region_name="us-east-1",
    ).client("cloudwatch")
    return client, Stubber(client)


class StubSession:
    profile_name = "dev"

    def __init__(self, cloudwatch: object) -> None:
        self.cloudwatch = cloudwatch

    def client(self, service_name: str, **_: object) -> object:
        assert service_name == "cloudwatch"
        return self.cloudwatch


def test_cloudfront_usage_service_reads_and_writes_current_month_cache(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(usage_ttl_seconds=3600))
    client, stubber = cloudwatch_client()
    expected_start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)

    stubber.add_response(
        "get_metric_statistics",
        {"Label": "BytesDownloaded", "Datapoints": [{"Timestamp": now, "Sum": 1234.0}]},
        {
            "Namespace": "AWS/CloudFront",
            "MetricName": "BytesDownloaded",
            "Dimensions": [
                {"Name": "DistributionId", "Value": "E123"},
                {"Name": "Region", "Value": "Global"},
            ],
            "StartTime": expected_start,
            "EndTime": now,
            "Period": 3600,
            "Statistics": ["Sum"],
        },
    )
    stubber.add_response(
        "get_metric_statistics",
        {"Label": "Requests", "Datapoints": [{"Timestamp": now, "Sum": 4321.0}]},
        {
            "Namespace": "AWS/CloudFront",
            "MetricName": "Requests",
            "Dimensions": [
                {"Name": "DistributionId", "Value": "E123"},
                {"Name": "Region", "Value": "Global"},
            ],
            "StartTime": expected_start,
            "EndTime": now,
            "Period": 3600,
            "Statistics": ["Sum"],
        },
    )

    with stubber:
        service = CloudFrontUsageService(
            profile_name="dev",
            paths=paths,
            settings=settings,
            session_factory=lambda **_: StubSession(client),  # type: ignore[arg-type]
            now=lambda: now,
        )

        snapshot = service.load(fake_inventory())

    assert snapshot.from_cache is False
    assert snapshot.usage_by_distribution["E123"].download == 1234
    assert snapshot.usage_by_distribution["E123"].upload is None
    assert snapshot.usage_by_distribution["E123"].requests == 4321
    assert snapshot.usage_by_distribution["E123"].month_key == "2026-05"

    payload = json.loads(paths.profile_state_file("dev").read_text(encoding="utf-8"))
    assert payload["distributions"]["E123"]["cw"] == {
        "download": 1234,
        "requests": 4321,
        "last_updated": "2026-05-13T12:00:00Z",
        "month_key": "2026-05",
    }

    cached_client, cached_stubber = cloudwatch_client()
    with cached_stubber:
        cached = CloudFrontUsageService(
            profile_name="dev",
            paths=paths,
            settings=settings,
            session_factory=lambda **_: StubSession(cached_client),  # type: ignore[arg-type]
            now=lambda: now,
        ).load(fake_inventory())

    assert cached.from_cache is True
    assert cached.usage_by_distribution["E123"].download == 1234
    assert cached.usage_by_distribution["E123"].requests == 4321


def test_cloudfront_usage_service_refreshes_stale_same_month_cache_incrementally(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    stale = datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(usage_ttl_seconds=3600))
    state = ProfileCacheState.from_payload(
        {
            "profile_name": "dev",
            "distributions": {
                "E123": {
                    "cw": {
                        "download": 100,
                        "requests": 10,
                        "last_updated": "2026-05-13T10:00:00Z",
                        "month_key": "2026-05",
                    }
                }
            },
        },
        profile_name="dev",
    )
    paths.profile_state_file("dev").parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_file("dev").write_text(
        json.dumps(state.to_payload(), indent=2),
        encoding="utf-8",
    )

    client, stubber = cloudwatch_client()
    for metric_name, total in (("BytesDownloaded", 25.0), ("Requests", 5.0)):
        stubber.add_response(
            "get_metric_statistics",
            {"Label": metric_name, "Datapoints": [{"Timestamp": now, "Sum": total}]},
            {
                "Namespace": "AWS/CloudFront",
                "MetricName": metric_name,
                "Dimensions": [
                    {"Name": "DistributionId", "Value": "E123"},
                    {"Name": "Region", "Value": "Global"},
                ],
                "StartTime": stale,
                "EndTime": now,
                "Period": 3600,
                "Statistics": ["Sum"],
            },
        )

    with stubber:
        snapshot = CloudFrontUsageService(
            profile_name="dev",
            paths=paths,
            settings=settings,
            session_factory=lambda **_: StubSession(client),  # type: ignore[arg-type]
            now=lambda: now,
        ).load(fake_inventory())

    assert snapshot.usage_by_distribution["E123"].download == 125
    assert snapshot.usage_by_distribution["E123"].requests == 15


def test_cloudfront_usage_service_restarts_from_month_start_after_rollover(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(usage_ttl_seconds=3600))
    paths.profile_state_file("dev").parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_file("dev").write_text(
        json.dumps(
            {
                "profile_name": "dev",
                "distributions": {
                    "E123": {
                        "cw": {
                            "download": 999,
                            "requests": 999,
                            "last_updated": "2026-04-30T23:59:59Z",
                            "month_key": "2026-04",
                        }
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    client, stubber = cloudwatch_client()
    expected_start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    for metric_name, total in (("BytesDownloaded", 20.0), ("Requests", 3.0)):
        stubber.add_response(
            "get_metric_statistics",
            {"Label": metric_name, "Datapoints": [{"Timestamp": now, "Sum": total}]},
            {
                "Namespace": "AWS/CloudFront",
                "MetricName": metric_name,
                "Dimensions": [
                    {"Name": "DistributionId", "Value": "E123"},
                    {"Name": "Region", "Value": "Global"},
                ],
                "StartTime": expected_start,
                "EndTime": now,
                "Period": 3600,
                "Statistics": ["Sum"],
            },
        )

    with stubber:
        snapshot = CloudFrontUsageService(
            profile_name="dev",
            paths=paths,
            settings=settings,
            session_factory=lambda **_: StubSession(client),  # type: ignore[arg-type]
            now=lambda: now,
        ).load(fake_inventory())

    assert snapshot.usage_by_distribution["E123"].download == 20
    assert snapshot.usage_by_distribution["E123"].requests == 3


def test_cloudfront_usage_service_falls_back_to_cached_values_on_cloudwatch_error(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(usage_ttl_seconds=3600))
    paths.profile_state_file("dev").parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_file("dev").write_text(
        json.dumps(
            {
                "profile_name": "dev",
                "distributions": {
                    "E123": {
                        "cw": {
                            "download": 100,
                            "requests": 10,
                            "last_updated": "2026-05-13T09:00:00Z",
                            "month_key": "2026-05",
                        }
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    class FailingSession:
        profile_name = "dev"

        def client(self, service_name: str, **_: object) -> object:
            assert service_name == "cloudwatch"
            raise RuntimeError("cloudwatch unavailable")

    snapshot = CloudFrontUsageService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FailingSession(),  # type: ignore[arg-type]
        now=lambda: now,
    ).load(fake_inventory())

    assert snapshot.usage_by_distribution["E123"].download == 100
    assert snapshot.usage_by_distribution["E123"].requests == 10


def test_cloudfront_usage_service_refreshes_incomplete_current_month_cache(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(usage_ttl_seconds=3600))
    paths.profile_state_file("dev").parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_file("dev").write_text(
        json.dumps(
            {
                "profile_name": "dev",
                "distributions": {
                    "E123": {
                        "cw": {
                            "requests": 10,
                            "last_updated": "2026-05-13T11:00:00Z",
                            "month_key": "2026-05",
                        }
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    client, stubber = cloudwatch_client()
    stubber.add_response(
        "get_metric_statistics",
        {"Label": "BytesDownloaded", "Datapoints": [{"Timestamp": now, "Sum": 50.0}]},
        {
            "Namespace": "AWS/CloudFront",
            "MetricName": "BytesDownloaded",
            "Dimensions": [
                {"Name": "DistributionId", "Value": "E123"},
                {"Name": "Region", "Value": "Global"},
            ],
            "StartTime": datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
            "EndTime": now,
            "Period": 3600,
            "Statistics": ["Sum"],
        },
    )
    stubber.add_response(
        "get_metric_statistics",
        {"Label": "Requests", "Datapoints": [{"Timestamp": now, "Sum": 5.0}]},
        {
            "Namespace": "AWS/CloudFront",
            "MetricName": "Requests",
            "Dimensions": [
                {"Name": "DistributionId", "Value": "E123"},
                {"Name": "Region", "Value": "Global"},
            ],
            "StartTime": datetime(2026, 5, 13, 11, 0, tzinfo=timezone.utc),
            "EndTime": now,
            "Period": 3600,
            "Statistics": ["Sum"],
        },
    )

    with stubber:
        snapshot = CloudFrontUsageService(
            profile_name="dev",
            paths=paths,
            settings=settings,
            session_factory=lambda **_: StubSession(client),  # type: ignore[arg-type]
            now=lambda: now,
        ).load(fake_inventory())

    assert snapshot.usage_by_distribution["E123"].download == 50
    assert snapshot.usage_by_distribution["E123"].requests == 15


def test_cloudfront_usage_service_treats_zero_values_as_complete_cached_usage(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(usage_ttl_seconds=3600))
    paths.profile_state_file("dev").parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_file("dev").write_text(
        json.dumps(
            {
                "profile_name": "dev",
                "distributions": {
                    "E123": {
                        "cw": {
                            "download": 0,
                            "requests": 0,
                            "last_updated": "2026-05-13T11:30:00Z",
                            "month_key": "2026-05",
                        }
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    class NoSession:
        profile_name = "dev"

        def client(self, service_name: str, **_: object) -> object:
            raise AssertionError(f"cache hit should not call {service_name}")

    snapshot = CloudFrontUsageService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: NoSession(),  # type: ignore[arg-type]
        now=lambda: now,
    ).load(fake_inventory())

    assert snapshot.from_cache is True
    assert snapshot.usage_by_distribution["E123"].download == 0
    assert snapshot.usage_by_distribution["E123"].requests == 0
