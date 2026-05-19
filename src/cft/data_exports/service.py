from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable

import boto3
import duckdb
from botocore.exceptions import ClientError

from cft.cache.policies import CachePolicy, utc_now
from cft.cache.store import JsonFileStore
from cft.config.paths import AppPaths, get_app_paths
from cft.config.settings import (
    AppSettings,
    load_app_settings,
    normalize_data_export_prefix,
    settings_profile_name,
)
from cft.models.cache import ProfileCacheState, ProfileSummaryCache
from cft.startup_trace import StartupTrace

SessionFactory = Callable[..., boto3.Session]

BYTES_PER_GB = Decimal("1000000000")

SUMMARY_SQL = """
SELECT
  COALESCE(SUM(
    CASE
      WHEN line_item_product_code = 'AmazonCloudFront'
        AND line_item_usage_type LIKE '%-DataTransfer-Out-Bytes'
      THEN line_item_usage_amount
      ELSE 0
    END
  ), 0) AS download_gb,
  COALESCE(SUM(
    CASE
      WHEN line_item_product_code = 'AmazonCloudFront'
        AND line_item_usage_type LIKE '%-DataTransfer-Out-OBytes'
      THEN line_item_usage_amount
      ELSE 0
    END
  ), 0) AS upload_gb,
  COALESCE(SUM(
    CASE
      WHEN line_item_product_code = 'AmazonCloudFront'
        AND line_item_usage_type LIKE '%-Requests-HTTP-Proxy'
      THEN line_item_usage_amount
      ELSE 0
    END
  ), 0) AS requests,
  COALESCE(SUM(
    CASE
      WHEN line_item_product_code IN ('AmazonCloudFront', 'CloudFrontPlans')
      THEN line_item_net_unblended_cost
      ELSE 0
    END
  ), 0) AS cost,
  MIN(line_item_usage_start_date) AS data_start,
  MAX(line_item_usage_end_date) AS data_end
FROM data
WHERE line_item_line_item_type = 'Usage'
"""


@dataclass(frozen=True)
class BillingSnapshot:
    profile_name: str
    configured: bool
    download_bytes: int | None = None
    upload_bytes: int | None = None
    requests: int | None = None
    cost: float | None = None
    last_updated: datetime | None = None
    data_start: datetime | None = None
    data_end: datetime | None = None
    from_cache: bool = False
    message: str | None = None


class CurDataExportService:
    """Read-through CUR/Data Export sync backed by S3 and DuckDB."""

    def __init__(
        self,
        profile_name: str | None = None,
        *,
        paths: AppPaths | None = None,
        settings: AppSettings | None = None,
        session_factory: SessionFactory = boto3.Session,
        now: Callable[[], datetime] = utc_now,
        trace: StartupTrace | None = None,
    ) -> None:
        self.profile_name = profile_name
        self.paths = paths or get_app_paths()
        self.settings = settings
        self.session_factory = session_factory
        self.now = now
        self.trace = trace

    def load(self, *, refresh: bool = False) -> BillingSnapshot:
        settings = self.settings or load_app_settings(
            self.paths,
            profile_name=settings_profile_name(self.profile_name),
        )
        export_bucket = settings.data_export.bucket
        export_name = settings.data_export.export_name
        export_prefix = normalize_data_export_prefix(settings.data_export.prefix)
        if not export_bucket or not export_name:
            return BillingSnapshot(
                profile_name=self.profile_name or "default",
                configured=False,
                message="Setup required",
            )

        profile_name = self.profile_name or "default"
        self.paths.ensure_profile_dirs(profile_name)

        state_store = JsonFileStore(self.paths.billing_cache_file(profile_name))
        state = ProfileCacheState.from_payload(
            state_store.read(),
            profile_name=profile_name,
        )
        now = self._coerce_utc(self.now())
        month_key = now.strftime("%Y-%m")
        manifest_key = self._manifest_key(
            prefix=export_prefix,
            export_name=export_name,
            month_key=month_key,
        )
        cached_profile = state.profile
        manifest_check_policy = CachePolicy.from_seconds(
            settings.cache.data_export_manifest_check_seconds
        )
        cache_matches_export = self._cache_matches_export(
            cached_profile,
            month_key=month_key,
            export_bucket=export_bucket,
            export_prefix=export_prefix,
            export_name=export_name,
            manifest_key=manifest_key,
        )

        if (
            not refresh
            and cache_matches_export
            and manifest_check_policy.is_fresh(
                cached_profile.manifest_last_checked,
                now=now,
            )
            and cached_profile.last_updated is not None
        ):
            if self.trace is not None:
                self.trace.emit(
                    "billing.cache",
                    profile_name=profile_name,
                    decision="cache_hit",
                )
            return self._snapshot_from_cache(
                profile_name=profile_name,
                summary=cached_profile,
                configured=True,
                from_cache=True,
            )

        if self.trace is not None:
            self.trace.emit(
                "billing.cache",
                profile_name=profile_name,
                decision="refresh" if refresh else "stale_or_missing",
            )

        session = self.session_factory(
            profile_name=self.profile_name,
            region_name=settings.aws.cloudfront_region,
        )
        profile_name = session.profile_name or self.profile_name or "default"
        if profile_name != (self.profile_name or "default"):
            self.paths.ensure_profile_dirs(profile_name)
            state_store = JsonFileStore(self.paths.billing_cache_file(profile_name))
            state = ProfileCacheState.from_payload(
                state_store.read(),
                profile_name=profile_name,
            )
            cached_profile = state.profile

        s3_client = session.client("s3")
        local_manifest_file = self.paths.data_export_manifest_file(
            profile_name,
            month_key,
            export_name,
        )

        try:
            manifest_head = s3_client.head_object(Bucket=export_bucket, Key=manifest_key)
        except ClientError:
            if not refresh and cache_matches_export and cached_profile.last_updated is not None:
                return self._snapshot_from_cache(
                    profile_name=profile_name,
                    summary=cached_profile,
                    configured=True,
                    from_cache=True,
                )
            raise

        remote_manifest_etag = self._clean_etag(manifest_head.get("ETag"))
        manifest_is_unchanged = (
            cache_matches_export
            and cached_profile.manifest_etag == remote_manifest_etag
            and local_manifest_file.exists()
        )

        if not manifest_is_unchanged:
            local_manifest_file.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(export_bucket, manifest_key, str(local_manifest_file))

        manifest_payload = json.loads(local_manifest_file.read_text(encoding="utf-8"))
        data_file_keys = self._extract_data_files(
            manifest_payload,
            bucket=export_bucket,
        )
        local_parquet_files: list[Path] = []
        parquet_files_cache: dict[str, dict[str, str]] = {}

        for data_file_key in data_file_keys:
            head = s3_client.head_object(Bucket=export_bucket, Key=data_file_key)
            remote_file_etag = self._clean_etag(head.get("ETag"))
            cache_entry = (
                cached_profile.parquet_files.get(data_file_key, {})
                if cache_matches_export
                else {}
            )
            local_path = self._local_parquet_path(
                profile_name=profile_name,
                month_key=month_key,
                remote_key=data_file_key,
            )

            if (
                refresh
                or not local_path.exists()
                or cache_entry.get("etag") != remote_file_etag
            ):
                local_path.parent.mkdir(parents=True, exist_ok=True)
                s3_client.download_file(export_bucket, data_file_key, str(local_path))

            local_parquet_files.append(local_path)
            parquet_files_cache[data_file_key] = {
                "etag": remote_file_etag or "",
                "local_path": str(local_path),
            }

        summary = self._query_summary(local_parquet_files)
        updated_profile = replace(
            cached_profile,
            last_updated=now,
            manifest_last_checked=now,
            month_key=month_key,
            s3_cur_bucket=export_bucket,
            s3_cur_prefix=export_prefix,
            s3_cur_export_name=export_name,
            manifest_key=manifest_key,
            manifest_etag=remote_manifest_etag,
            parquet_files=parquet_files_cache,
            data_start=summary.data_start,
            data_end=summary.data_end,
            download=summary.download_bytes,
            upload=summary.upload_bytes,
            requests=summary.requests,
            cost=summary.cost,
        )
        state_store.write_if_changed(replace(state, profile=updated_profile).to_payload())
        return BillingSnapshot(
            profile_name=profile_name,
            configured=True,
            download_bytes=summary.download_bytes,
            upload_bytes=summary.upload_bytes,
            requests=summary.requests,
            cost=summary.cost,
            last_updated=now,
            data_start=summary.data_start,
            data_end=summary.data_end,
            from_cache=False,
        )

    @staticmethod
    def _cache_matches_export(
        cached_profile: ProfileSummaryCache,
        *,
        month_key: str,
        export_bucket: str,
        export_prefix: str | None,
        export_name: str,
        manifest_key: str,
    ) -> bool:
        return (
            cached_profile.month_key == month_key
            and cached_profile.s3_cur_bucket == export_bucket
            and cached_profile.s3_cur_prefix == export_prefix
            and cached_profile.s3_cur_export_name == export_name
            and cached_profile.manifest_key == manifest_key
        )

    @staticmethod
    def _manifest_key(*, prefix: str | None, export_name: str, month_key: str) -> str:
        parts = [
            prefix,
            export_name,
            "metadata",
            f"BILLING_PERIOD={month_key}",
            f"{export_name}-Manifest.json",
        ]
        return "/".join(part.strip("/") for part in parts if part)

    @staticmethod
    def _extract_data_files(payload: object, *, bucket: str | None = None) -> list[str]:
        if not isinstance(payload, dict):
            return []
        data_files = payload.get("dataFiles")
        if not isinstance(data_files, list):
            return []

        resolved: list[str] = []
        for item in data_files:
            if isinstance(item, str) and item.strip():
                normalized = CurDataExportService._normalize_data_file_reference(
                    item.strip(),
                    bucket=bucket,
                )
                if normalized:
                    resolved.append(normalized)
                continue
            if isinstance(item, dict):
                for key_name in ("key", "path", "s3Key"):
                    value = item.get(key_name)
                    if isinstance(value, str) and value.strip():
                        normalized = CurDataExportService._normalize_data_file_reference(
                            value.strip(),
                            bucket=bucket,
                        )
                        if normalized:
                            resolved.append(normalized)
                        break
        return resolved

    @staticmethod
    def _normalize_data_file_reference(reference: str, *, bucket: str | None = None) -> str | None:
        text = reference.strip()
        if not text:
            return None
        if not text.startswith("s3://"):
            return text

        without_scheme = text[len("s3://") :]
        bucket_name, separator, key = without_scheme.partition("/")
        if not separator or not key.strip():
            return None
        if bucket is not None and bucket_name != bucket:
            return None
        return key.strip()

    def _local_parquet_path(
        self,
        *,
        profile_name: str,
        month_key: str,
        remote_key: str,
    ) -> Path:
        digest = hashlib.sha256(remote_key.encode("utf-8")).hexdigest()[:16]
        filename = Path(remote_key).name
        return self.paths.parquet_month_dir(profile_name, month_key) / f"{digest}-{filename}"

    @staticmethod
    def _query_summary(local_parquet_files: list[Path]) -> BillingSnapshot:
        if not local_parquet_files:
            return BillingSnapshot(profile_name="default", configured=True)

        connection = duckdb.connect(database=":memory:")
        try:
            connection.read_parquet([str(path) for path in local_parquet_files]).create_view("data")
            row = connection.execute(SUMMARY_SQL).fetchone()
        finally:
            connection.close()

        if row is None:
            return BillingSnapshot(profile_name="default", configured=True)

        download_gb, upload_gb, requests, cost, data_start, data_end = row
        return BillingSnapshot(
            profile_name="default",
            configured=True,
            download_bytes=CurDataExportService._usage_gb_to_bytes(download_gb),
            upload_bytes=CurDataExportService._usage_gb_to_bytes(upload_gb),
            requests=CurDataExportService._count_to_int(requests),
            cost=float(cost) if cost is not None else None,
            data_start=CurDataExportService._coerce_optional_utc(data_start),
            data_end=CurDataExportService._coerce_optional_utc(data_end),
        )

    @staticmethod
    def _usage_gb_to_bytes(value: object) -> int | None:
        if value is None:
            return None
        decimal_value = Decimal(str(value)) * BYTES_PER_GB
        return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _count_to_int(value: object) -> int | None:
        if value is None:
            return None
        decimal_value = Decimal(str(value))
        return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _clean_etag(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip().strip('"')
        return text or None

    @staticmethod
    def _snapshot_from_cache(
        *,
        profile_name: str,
        summary: ProfileSummaryCache,
        configured: bool,
        from_cache: bool,
    ) -> BillingSnapshot:
        return BillingSnapshot(
            profile_name=profile_name,
            configured=configured,
            download_bytes=summary.download,
            upload_bytes=summary.upload,
            requests=summary.requests,
            cost=summary.cost,
            last_updated=summary.last_updated,
            data_start=summary.data_start,
            data_end=summary.data_end,
            from_cache=from_cache,
        )

    @staticmethod
    def _coerce_optional_utc(value: object) -> datetime | None:
        if not isinstance(value, datetime):
            return None
        return CurDataExportService._coerce_utc(value)

    @staticmethod
    def _coerce_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
