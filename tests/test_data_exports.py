from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import boto3
import duckdb
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from cft.config.paths import AppPaths
from cft.config.settings import AppSettings, AwsSettings, CacheSettings, DataExportSettings
from cft.data_exports import CurDataExportService


class FakeSession:
    def __init__(self, s3_client: object, *, profile_name: str = "dev") -> None:
        self._s3_client = s3_client
        self.profile_name = profile_name

    def client(self, service_name: str, **kwargs) -> object:
        assert service_name == "s3"
        return self._s3_client


class DownloadTrackingS3Client:
    def __init__(self, client: object, downloads: dict[str, bytes]) -> None:
        self._client = client
        self._downloads = downloads
        self.download_calls: list[tuple[str, str, str]] = []

    def head_object(self, **kwargs):
        return self._client.head_object(**kwargs)

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.download_calls.append((bucket, key, filename))
        target = Path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._downloads[key])


def write_cur_parquet(
    path: Path,
    *,
    download_gb: float,
    upload_gb: float,
    requests: int,
    cost: float,
) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE billing AS
            SELECT * FROM (
                VALUES
                    ('Usage', ?, ?, TIMESTAMP '2026-05-01 00:00:00', TIMESTAMP '2026-05-11 08:00:00', 'CA-DataTransfer-Out-Bytes', 'AmazonCloudFront'),
                    ('Usage', ?, ?, TIMESTAMP '2026-05-01 00:00:00', TIMESTAMP '2026-05-11 08:00:00', 'CA-DataTransfer-Out-OBytes', 'AmazonCloudFront'),
                    ('Usage', ?, ?, TIMESTAMP '2026-05-01 00:00:00', TIMESTAMP '2026-05-11 08:00:00', 'CA-Requests-HTTP-Proxy', 'AmazonCloudFront')
            ) AS t(
                line_item_line_item_type,
                line_item_usage_amount,
                line_item_net_unblended_cost,
                line_item_usage_start_date,
                line_item_usage_end_date,
                line_item_usage_type,
                line_item_product_code
            )
            """,
            [
                download_gb,
                cost,
                upload_gb,
                0.0,
                requests,
                0.0,
            ],
        )
        connection.execute(f"COPY billing TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()
    return path.read_bytes()


def data_export_settings(bucket: str) -> AppSettings:
    return AppSettings(
        aws=AwsSettings(),
        cache=CacheSettings(data_export_manifest_check_seconds=4 * 60 * 60),
        data_export=DataExportSettings(
            bucket=bucket,
            prefix="exports",
            export_name="cloudfront-cur",
        ),
    )


def s3_client() -> object:
    return boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def test_cur_service_returns_setup_required_without_session(tmp_path) -> None:
    service = CurDataExportService(
        profile_name="dev",
        paths=AppPaths.from_base(tmp_path / "cft"),
        settings=AppSettings(
            aws=AwsSettings(),
            cache=CacheSettings(),
            data_export=DataExportSettings(),
        ),
        session_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("session not expected")),
        now=lambda: datetime(2026, 5, 11, 9, 30, tzinfo=timezone.utc),
    )

    snapshot = service.load()

    assert snapshot.configured is False
    assert snapshot.message == "Setup required"
    assert snapshot.download_bytes is None


def test_cur_service_downloads_manifest_and_parquet_then_uses_cache(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    settings = data_export_settings("billing-bucket")
    manifest_key = (
        "exports/cloudfront-cur/metadata/BILLING_PERIOD=2026-05/cloudfront-cur-Manifest.json"
    )
    parquet_key = "exports/cloudfront-cur/data/BILLING_PERIOD=2026-05/part-0001.parquet"
    manifest_payload = json.dumps(
        {"dataFiles": [f"s3://billing-bucket/{parquet_key}"]}
    ).encode("utf-8")
    parquet_bytes = write_cur_parquet(
        tmp_path / "sample.parquet",
        download_gb=128.4,
        upload_gb=6.8,
        requests=1_240_000,
        cost=8.42,
    )

    boto_client = s3_client()
    stubber = Stubber(boto_client)
    stubber.add_response(
        "head_object",
        {"ETag": '"manifest-etag"'},
        {"Bucket": "billing-bucket", "Key": manifest_key},
    )
    stubber.add_response(
        "head_object",
        {"ETag": '"parquet-etag"'},
        {"Bucket": "billing-bucket", "Key": parquet_key},
    )
    stubber.activate()

    tracking_client = DownloadTrackingS3Client(
        boto_client,
        {
            manifest_key: manifest_payload,
            parquet_key: parquet_bytes,
        },
    )

    current_now = {"value": datetime(2026, 5, 11, 9, 30, tzinfo=timezone.utc)}
    service = CurDataExportService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **kwargs: FakeSession(tracking_client),
        now=lambda: current_now["value"],
    )

    first = service.load()

    assert first.configured is True
    assert first.from_cache is False
    assert first.download_bytes == 128_400_000_000
    assert first.upload_bytes == 6_800_000_000
    assert first.requests == 1_240_000
    assert first.cost == 8.42
    assert first.data_end == datetime(2026, 5, 11, 8, 0, tzinfo=timezone.utc)
    assert [call[1] for call in tracking_client.download_calls] == [manifest_key, parquet_key]

    current_now["value"] = datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc)
    second = service.load()

    assert second.configured is True
    assert second.from_cache is True
    assert second.download_bytes == first.download_bytes
    assert second.cost == first.cost
    assert [call[1] for call in tracking_client.download_calls] == [manifest_key, parquet_key]

    state_payload = json.loads(paths.profile_state_file("dev").read_text(encoding="utf-8"))
    profile_payload = state_payload["profile"]
    assert profile_payload["bucket"] == "billing-bucket"
    assert profile_payload["manifest_etag"] == "manifest-etag"
    assert profile_payload["parquet_files"][parquet_key]["etag"] == "parquet-etag"

    stubber.deactivate()


def test_cur_service_rechecks_s3_when_bucket_changes_inside_manifest_ttl(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    manifest_key = (
        "exports/cloudfront-cur/metadata/BILLING_PERIOD=2026-05/cloudfront-cur-Manifest.json"
    )
    parquet_key = "exports/cloudfront-cur/data/BILLING_PERIOD=2026-05/part-0001.parquet"

    old_boto_client = s3_client()
    old_stubber = Stubber(old_boto_client)
    old_stubber.add_response(
        "head_object",
        {"ETag": '"old-manifest-etag"'},
        {"Bucket": "old-bucket", "Key": manifest_key},
    )
    old_stubber.add_response(
        "head_object",
        {"ETag": '"old-parquet-etag"'},
        {"Bucket": "old-bucket", "Key": parquet_key},
    )
    old_stubber.activate()
    old_tracking_client = DownloadTrackingS3Client(
        old_boto_client,
        {
            manifest_key: json.dumps(
                {"dataFiles": [f"s3://old-bucket/{parquet_key}"]}
            ).encode("utf-8"),
            parquet_key: write_cur_parquet(
                tmp_path / "old.parquet",
                download_gb=10,
                upload_gb=1,
                requests=100,
                cost=1.0,
            ),
        },
    )
    first_service = CurDataExportService(
        profile_name="dev",
        paths=paths,
        settings=data_export_settings("old-bucket"),
        session_factory=lambda **kwargs: FakeSession(old_tracking_client),
        now=lambda: datetime(2026, 5, 11, 9, 30, tzinfo=timezone.utc),
    )

    first = first_service.load()

    assert first.download_bytes == 10_000_000_000
    old_stubber.deactivate()

    new_boto_client = s3_client()
    new_stubber = Stubber(new_boto_client)
    new_stubber.add_response(
        "head_object",
        {"ETag": '"new-manifest-etag"'},
        {"Bucket": "new-bucket", "Key": manifest_key},
    )
    new_stubber.add_response(
        "head_object",
        {"ETag": '"new-parquet-etag"'},
        {"Bucket": "new-bucket", "Key": parquet_key},
    )
    new_stubber.activate()
    new_tracking_client = DownloadTrackingS3Client(
        new_boto_client,
        {
            manifest_key: json.dumps(
                {"dataFiles": [f"s3://new-bucket/{parquet_key}"]}
            ).encode("utf-8"),
            parquet_key: write_cur_parquet(
                tmp_path / "new.parquet",
                download_gb=25,
                upload_gb=2,
                requests=250,
                cost=2.5,
            ),
        },
    )
    second_service = CurDataExportService(
        profile_name="dev",
        paths=paths,
        settings=data_export_settings("new-bucket"),
        session_factory=lambda **kwargs: FakeSession(new_tracking_client),
        now=lambda: datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc),
    )

    second = second_service.load()

    assert second.from_cache is False
    assert second.download_bytes == 25_000_000_000
    assert [call[:2] for call in new_tracking_client.download_calls] == [
        ("new-bucket", manifest_key),
        ("new-bucket", parquet_key),
    ]
    profile_payload = json.loads(paths.profile_state_file("dev").read_text(encoding="utf-8"))[
        "profile"
    ]
    assert profile_payload["bucket"] == "new-bucket"
    assert profile_payload["manifest_etag"] == "new-manifest-etag"

    new_stubber.deactivate()


def test_cur_service_force_refresh_does_not_hide_wrong_bucket_with_stale_cache(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    manifest_key = (
        "exports/cloudfront-cur/metadata/BILLING_PERIOD=2026-05/cloudfront-cur-Manifest.json"
    )
    parquet_key = "exports/cloudfront-cur/data/BILLING_PERIOD=2026-05/part-0001.parquet"

    good_boto_client = s3_client()
    good_stubber = Stubber(good_boto_client)
    good_stubber.add_response(
        "head_object",
        {"ETag": '"manifest-etag"'},
        {"Bucket": "good-bucket", "Key": manifest_key},
    )
    good_stubber.add_response(
        "head_object",
        {"ETag": '"parquet-etag"'},
        {"Bucket": "good-bucket", "Key": parquet_key},
    )
    good_stubber.activate()
    good_tracking_client = DownloadTrackingS3Client(
        good_boto_client,
        {
            manifest_key: json.dumps(
                {"dataFiles": [f"s3://good-bucket/{parquet_key}"]}
            ).encode("utf-8"),
            parquet_key: write_cur_parquet(
                tmp_path / "good.parquet",
                download_gb=128.4,
                upload_gb=6.8,
                requests=1_240_000,
                cost=8.42,
            ),
        },
    )
    good_service = CurDataExportService(
        profile_name="dev",
        paths=paths,
        settings=data_export_settings("good-bucket"),
        session_factory=lambda **kwargs: FakeSession(good_tracking_client),
        now=lambda: datetime(2026, 5, 11, 9, 30, tzinfo=timezone.utc),
    )

    cached = good_service.load()

    assert cached.download_bytes == 128_400_000_000
    good_stubber.deactivate()

    wrong_boto_client = s3_client()
    wrong_stubber = Stubber(wrong_boto_client)
    wrong_stubber.add_client_error(
        "head_object",
        service_error_code="404",
        service_message="Not Found",
        http_status_code=404,
        expected_params={"Bucket": "wrong-bucket", "Key": manifest_key},
    )
    wrong_stubber.activate()
    wrong_tracking_client = DownloadTrackingS3Client(wrong_boto_client, {})
    wrong_service = CurDataExportService(
        profile_name="dev",
        paths=paths,
        settings=data_export_settings("wrong-bucket"),
        session_factory=lambda **kwargs: FakeSession(wrong_tracking_client),
        now=lambda: datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc),
    )

    with pytest.raises(ClientError):
        wrong_service.load(refresh=True)

    wrong_stubber.deactivate()
