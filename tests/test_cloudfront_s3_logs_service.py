from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb

from cft.aws.cloudfront import AccountIdentity, CloudFrontInventory
from cft.aws.cloudfront_s3_logs import CloudFrontS3LogsUploadService
from cft.config.paths import AppPaths
from cft.config.settings import AppSettings, AwsSettings, CacheSettings
from cft.models.cache import StandardLogDeliveryRecord
from cft.models.distribution import DistributionSummary


def write_s3_log_parquet(
    path: Path,
    *,
    distribution_id: str,
    upload_bytes: int,
) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE logs AS
            SELECT * FROM (
                VALUES
                    (
                        ?,
                        DATE '2026-05-10',
                        TIME '12:00:00',
                        'YVR52-P238',
                        0,
                        '1.2.3.4',
                        ?,
                        12345,
                        'example'
                    )
            ) AS t(
                "DistributionId",
                "date",
                "time",
                "x-edge-location",
                "sc-bytes",
                "c-ip",
                "cs-bytes",
                "c-port",
                "x-edge-detailed-result-type"
            )
            """,
            [distribution_id, upload_bytes],
        )
        connection.execute(f"COPY logs TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()
    return path.read_bytes()


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
                    delivery_destination_arn="arn:aws:s3:::cloudfront-logs",
                    delivery_destination_resource_arn="arn:aws:s3:::cloudfront-logs",
                    delivery_destination_type="S3",
                    delivery_source_name="CreatedByCloudFront-E123-ACCESS_LOGS",
                ),
            ),
            "E456": (
                StandardLogDeliveryRecord(
                    delivery_id="delivery-2",
                    delivery_arn="arn:aws:logs:us-east-1:123456789012:delivery:delivery-2",
                    delivery_destination_arn="arn:aws:s3:::cloudfront-logs",
                    delivery_destination_resource_arn="arn:aws:s3:::cloudfront-logs",
                    delivery_destination_type="S3",
                    delivery_source_name="CreatedByCloudFront-E456-ACCESS_LOGS",
                ),
            ),
        },
    )


class FakeS3Paginator:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages

    def paginate(self, **kwargs: object) -> list[dict[str, object]]:
        return self.pages


class FakeS3Client:
    def __init__(self, pages: list[dict[str, object]], downloads: dict[str, bytes]) -> None:
        self.pages = pages
        self.downloads = downloads
        self.download_calls: list[tuple[str, str, str]] = []
        self.paginator_calls: list[dict[str, object]] = []

    def get_paginator(self, name: str) -> FakeS3Paginator:
        assert name == "list_objects_v2"
        return FakeS3Paginator(self.pages)

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.download_calls.append((bucket, key, filename))
        target = Path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.downloads[key])


class FakeSession:
    profile_name = "dev"

    def __init__(self, s3_client: FakeS3Client) -> None:
        self.s3_client = s3_client

    def client(self, service_name: str, **_: object) -> object:
        assert service_name == "s3"
        return self.s3_client


def test_cloudfront_s3_logs_upload_service_downloads_current_month_files_and_caches_them(
    tmp_path,
) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    settings = AppSettings(
        aws=AwsSettings(),
        cache=CacheSettings(logs_upload_ttl_seconds=3600),
    )
    bucket = "cloudfront-logs"
    current = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    pages = [
        {
            "Contents": [
                {
                    "Key": "AWSLogs/123456789012/CloudFront/E123.2026-05-10.abc.parquet",
                    "LastModified": datetime(2026, 5, 13, 10, tzinfo=timezone.utc),
                },
                {
                    "Key": "AWSLogs/123456789012/CloudFront/E456.2026-05-11.def.parquet",
                    "LastModified": datetime(2026, 5, 13, 10, tzinfo=timezone.utc),
                },
                {
                    "Key": "AWSLogs/123456789012/CloudFront/E123.2026-04-30.old.parquet",
                    "LastModified": datetime(2026, 4, 30, 10, tzinfo=timezone.utc),
                },
            ]
        }
    ]
    downloads = {
        "AWSLogs/123456789012/CloudFront/E123.2026-05-10.abc.parquet": write_s3_log_parquet(
            tmp_path / "e123.parquet",
            distribution_id="E123",
            upload_bytes=100,
        ),
        "AWSLogs/123456789012/CloudFront/E456.2026-05-11.def.parquet": write_s3_log_parquet(
            tmp_path / "e456.parquet",
            distribution_id="E456",
            upload_bytes=300,
        ),
        "AWSLogs/123456789012/CloudFront/E123.2026-04-30.old.parquet": write_s3_log_parquet(
            tmp_path / "old.parquet",
            distribution_id="E123",
            upload_bytes=50,
        ),
    }
    s3_client = FakeS3Client(pages, downloads)

    service = CloudFrontS3LogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(s3_client),  # type: ignore[arg-type]
        now=lambda: current,
    )

    first = service.load(fake_inventory())
    payload = paths.profile_state_file("dev").read_text(encoding="utf-8")

    assert first.from_cache is False
    assert first.upload_by_distribution["E123"].upload == 100
    assert first.upload_by_distribution["E456"].upload == 300
    assert [call[1] for call in s3_client.download_calls] == [
        "AWSLogs/123456789012/CloudFront/E123.2026-05-10.abc.parquet",
        "AWSLogs/123456789012/CloudFront/E456.2026-05-11.def.parquet",
    ]
    assert '"upload": 100' in payload
    assert '"upload": 300' in payload

    current = datetime(2026, 5, 13, 12, 30, tzinfo=timezone.utc)
    second = service.load(fake_inventory())

    assert second.from_cache is True
    assert second.upload_by_distribution["E123"].upload == 100
    assert second.upload_by_distribution["E456"].upload == 300
    assert [call[1] for call in s3_client.download_calls] == [
        "AWSLogs/123456789012/CloudFront/E123.2026-05-10.abc.parquet",
        "AWSLogs/123456789012/CloudFront/E456.2026-05-11.def.parquet",
    ]


def test_cloudfront_s3_logs_upload_service_downloads_only_new_files_after_cache_expiry(
    tmp_path,
) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    settings = AppSettings(
        aws=AwsSettings(),
        cache=CacheSettings(logs_upload_ttl_seconds=3600),
    )
    current = {"value": datetime(2026, 5, 13, 12, tzinfo=timezone.utc)}
    pages = [
        {
            "Contents": [
                {
                    "Key": "AWSLogs/123456789012/CloudFront/E123.2026-05-10.abc.parquet",
                    "LastModified": datetime(2026, 5, 13, 10, tzinfo=timezone.utc),
                },
            ]
        }
    ]
    downloads = {
        "AWSLogs/123456789012/CloudFront/E123.2026-05-10.abc.parquet": write_s3_log_parquet(
            tmp_path / "first.parquet",
            distribution_id="E123",
            upload_bytes=100,
        ),
        "AWSLogs/123456789012/CloudFront/E123.2026-05-12.xyz.parquet": write_s3_log_parquet(
            tmp_path / "second.parquet",
            distribution_id="E123",
            upload_bytes=50,
        ),
    }
    s3_client = FakeS3Client(pages, downloads)

    service = CloudFrontS3LogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(s3_client),  # type: ignore[arg-type]
        now=lambda: current["value"],
    )

    first = service.load(fake_inventory())
    assert first.upload_by_distribution["E123"].upload == 100

    pages[0]["Contents"].append(
        {
            "Key": "AWSLogs/123456789012/CloudFront/E123.2026-05-12.xyz.parquet",
            "LastModified": datetime(2026, 5, 13, 12, 30, tzinfo=timezone.utc),
        }
    )
    current["value"] = datetime(2026, 5, 13, 14, tzinfo=timezone.utc)

    second = service.load(fake_inventory())

    assert second.from_cache is False
    assert second.upload_by_distribution["E123"].upload == 150
    assert [call[1] for call in s3_client.download_calls] == [
        "AWSLogs/123456789012/CloudFront/E123.2026-05-10.abc.parquet",
        "AWSLogs/123456789012/CloudFront/E123.2026-05-12.xyz.parquet",
    ]


def test_cloudfront_s3_logs_upload_service_uses_fresh_cache_without_s3_calls(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    settings = AppSettings(
        aws=AwsSettings(),
        cache=CacheSettings(logs_upload_ttl_seconds=3600),
    )
    current = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    pages = [
        {
            "Contents": [
                {
                    "Key": "AWSLogs/123456789012/CloudFront/E123.2026-05-10.abc.parquet",
                    "LastModified": datetime(2026, 5, 13, 10, tzinfo=timezone.utc),
                }
            ]
        }
    ]
    downloads = {
        "AWSLogs/123456789012/CloudFront/E123.2026-05-10.abc.parquet": write_s3_log_parquet(
            tmp_path / "e123.parquet",
            distribution_id="E123",
            upload_bytes=100,
        ),
    }
    first = CloudFrontS3LogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(FakeS3Client(pages, downloads)),  # type: ignore[arg-type]
        now=lambda: current,
    ).load(fake_inventory())

    assert first.upload_by_distribution["E123"].upload == 100
    assert first.upload_by_distribution["E456"].upload == 0

    class NoSession:
        profile_name = "dev"

        def client(self, service_name: str, **_: object) -> object:
            raise AssertionError(f"cache hit should not call {service_name}")

    second = CloudFrontS3LogsUploadService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: NoSession(),  # type: ignore[arg-type]
        now=lambda: datetime(2026, 5, 13, 12, 30, tzinfo=timezone.utc),
    ).load(fake_inventory())

    assert second.from_cache is True
    assert second.upload_by_distribution["E123"].upload == 100
    assert second.upload_by_distribution["E456"].upload == 0
