from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import boto3
import duckdb
from botocore.exceptions import ClientError

from cft.cache.policies import CachePolicy, utc_now
from cft.cache.store import JsonFileStore
from cft.config.paths import AppPaths, get_app_paths
from cft.config.settings import AppSettings, load_app_settings, settings_profile_name
from cft.models.cache import DistributionCacheRecord, ProfileCacheState, SourceMetrics

from .cloudfront import CloudFrontInventory

SessionFactory = Callable[..., boto3.Session]

S3_LOG_PREFIX = "AWSLogs"
S3_LOG_QUERY_SUBDIR = "cloudfront_s3_logs"
S3_LOG_FILE_PATTERN = re.compile(
    r"^(?P<distribution_id>[^./]+)\.(?P<date>\d{4}-\d{2}-\d{2})\.(?P<suffix>[^/]+)\.parquet$"
)


@dataclass(frozen=True)
class CloudFrontS3LogsUploadSnapshot:
    profile_name: str
    upload_by_distribution: dict[str, SourceMetrics]
    from_cache: bool


@dataclass(frozen=True)
class _S3LogSource:
    bucket: str
    prefix: str


class CloudFrontS3LogsUploadService:
    """Read-through CloudFront standard-log upload totals stored in S3 parquet files."""

    def __init__(
        self,
        profile_name: str | None = None,
        region_name: str | None = None,
        *,
        paths: AppPaths | None = None,
        settings: AppSettings | None = None,
        session_factory: SessionFactory = boto3.Session,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.profile_name = profile_name
        self.region_name = region_name
        self.paths = paths or get_app_paths()
        self.settings = settings
        self.session_factory = session_factory
        self.now = now

    def load(
        self,
        inventory: CloudFrontInventory,
        *,
        refresh: bool = False,
    ) -> CloudFrontS3LogsUploadSnapshot:
        settings = self.settings or load_app_settings(
            self.paths,
            profile_name=settings_profile_name(inventory.profile_name),
        )
        session = self.session_factory(
            profile_name=inventory.profile_name,
            region_name=self.region_name or settings.aws.cloudfront_region,
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
        month_start = self._month_start(now)
        cache_policy = CachePolicy.from_seconds(settings.cache.logs_upload_ttl_seconds)
        s3_client = session.client(
            "s3",
            region_name=self.region_name or settings.aws.cloudfront_region,
        )

        sources_by_distribution = self._distribution_s3_sources(inventory)
        source_key_by_distribution = self._distribution_source_keys(sources_by_distribution)
        target_distribution_ids = self._target_distribution_ids(
            inventory=inventory,
            state=state,
            source_key_by_distribution=source_key_by_distribution,
        )

        upload_by_distribution: dict[str, SourceMetrics] = {}
        updated_distributions = dict(state.distributions)
        from_cache = True
        query_needed = refresh

        for distribution in inventory.distributions:
            if distribution.distribution_id not in target_distribution_ids:
                continue
            existing = updated_distributions.get(distribution.distribution_id) or DistributionCacheRecord(
                distribution_id=distribution.distribution_id
            )
            cached = existing.s3
            source_key = source_key_by_distribution.get(
                distribution.distribution_id,
                "s3:none",
            )
            cache_is_current_month = cached.month_key == month_key
            cache_has_upload = cached.upload is not None
            cache_is_fresh = (
                not refresh
                and cache_is_current_month
                and cache_has_upload
                and cached.source_key == source_key
                and cache_policy.is_fresh(cached.last_updated, now=now)
            )
            if not cache_is_fresh:
                query_needed = True
                break

        downloaded_files: list[Path] = []
        if query_needed:
            source_thresholds = self._source_thresholds(
                inventory=inventory,
                state=state,
                source_key_by_distribution=source_key_by_distribution,
                month_key=month_key,
            )
            unique_sources = sorted({source for sources in sources_by_distribution.values() for source in sources}, key=lambda source: (source.bucket, source.prefix))
            for source in unique_sources:
                try:
                    downloaded_files.extend(
                        self._download_source_files(
                            s3_client,
                            source=source,
                            profile_name=inventory.profile_name,
                            month_key=month_key,
                            month_start=month_start,
                            threshold=source_thresholds.get(source.bucket, month_start),
                        )
                    )
                except ClientError:
                    continue
                except Exception:
                    continue

        local_parquet_files = self._local_parquet_files(
            profile_name=inventory.profile_name,
            month_key=month_key,
        )
        summary_by_distribution = self._query_summary(local_parquet_files)

        for distribution in inventory.distributions:
            if distribution.distribution_id not in target_distribution_ids:
                continue
            existing = updated_distributions.get(distribution.distribution_id) or DistributionCacheRecord(
                distribution_id=distribution.distribution_id
            )
            cached = existing.s3
            source_key = source_key_by_distribution.get(
                distribution.distribution_id,
                "s3:none",
            )
            cache_is_current_month = cached.month_key == month_key
            cache_has_upload = cached.upload is not None
            cache_is_fresh = (
                not refresh
                and cache_is_current_month
                and cache_has_upload
                and cached.source_key == source_key
                and cache_policy.is_fresh(cached.last_updated, now=now)
            )
            if cache_is_fresh:
                upload_by_distribution[distribution.distribution_id] = cached
                continue

            if not query_needed:
                if cached.upload is not None and cached.source_key == source_key:
                    upload_by_distribution[distribution.distribution_id] = cached
                else:
                    cleared = self._empty_metrics(
                        month_key=month_key,
                        source_key=source_key,
                        last_updated=now,
                    )
                    upload_by_distribution[distribution.distribution_id] = cleared
                    updated_distributions[distribution.distribution_id] = replace(
                        existing,
                        s3=cleared,
                    )
                continue

            if source_key == "s3:none":
                cleared = self._empty_metrics(
                    month_key=month_key,
                    source_key=source_key,
                    last_updated=now,
                )
                upload_by_distribution[distribution.distribution_id] = cleared
                updated_distributions[distribution.distribution_id] = replace(
                    existing,
                    s3=cleared,
                )
                continue

            total_upload = summary_by_distribution.get(distribution.distribution_id, 0)
            refreshed = SourceMetrics(
                download=cached.download,
                upload=total_upload,
                requests=cached.requests,
                last_updated=now,
                month_key=month_key,
                source_key=source_key,
            )
            from_cache = False
            upload_by_distribution[distribution.distribution_id] = refreshed
            updated_distributions[distribution.distribution_id] = replace(existing, s3=refreshed)

        updated_state = replace(
            state,
            profile_name=inventory.profile_name,
            distributions=updated_distributions,
        )
        cache_store.write(updated_state.to_payload())
        return CloudFrontS3LogsUploadSnapshot(
            profile_name=inventory.profile_name,
            upload_by_distribution=upload_by_distribution,
            from_cache=from_cache,
        )

    def _download_source_files(
        self,
        s3_client: object,
        *,
        source: _S3LogSource,
        profile_name: str,
        month_key: str,
        month_start: datetime,
        threshold: datetime,
    ) -> list[Path]:
        paginator = s3_client.get_paginator("list_objects_v2")
        local_files: list[Path] = []
        for page in paginator.paginate(Bucket=source.bucket, Prefix=source.prefix):
            for item in page.get("Contents", []) or []:
                if not isinstance(item, dict):
                    continue
                remote_key = str(item.get("Key", "")).strip()
                if not remote_key or not remote_key.endswith(".parquet"):
                    continue
                object_last_modified = self._coerce_utc(item.get("LastModified"))
                if object_last_modified <= threshold:
                    continue
                parsed = self._parse_log_key(remote_key)
                if parsed is None or parsed[1] != month_key:
                    continue
                local_path = self._local_parquet_path(
                    profile_name=profile_name,
                    month_key=month_key,
                    bucket=source.bucket,
                    remote_key=remote_key,
                )
                if not local_path.exists() or object_last_modified >= month_start:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    s3_client.download_file(source.bucket, remote_key, str(local_path))
                local_files.append(local_path)
        return local_files

    @staticmethod
    def _distribution_s3_sources(
        inventory: CloudFrontInventory,
    ) -> dict[str, tuple[_S3LogSource, ...]]:
        account_id = inventory.identity.account_id if inventory.identity else ""
        sources: dict[str, tuple[_S3LogSource, ...]] = {}
        for distribution_id, deliveries in inventory.standard_log_deliveries.items():
            buckets = {
                CloudFrontS3LogsUploadService._normalize_s3_bucket_identifier(
                    delivery.delivery_destination_resource_arn
                    or delivery.delivery_destination_arn
                    or ""
                )
                for delivery in deliveries
                if (delivery.delivery_destination_type or "").upper() == "S3"
            }
            buckets.discard("")
            if not buckets:
                continue
            sources[distribution_id] = tuple(
                _S3LogSource(
                    bucket=bucket,
                    prefix="/".join(
                        part
                        for part in (
                            S3_LOG_PREFIX,
                            account_id,
                            "CloudFront",
                        )
                        if part
                    )
                    + "/",
                )
                for bucket in sorted(buckets)
            )
        return sources

    @staticmethod
    def _distribution_source_keys(
        sources_by_distribution: dict[str, tuple[_S3LogSource, ...]],
    ) -> dict[str, str]:
        return {
            distribution_id: "s3:" + "|".join(
                f"{source.bucket}/{source.prefix}" for source in sources
            )
            for distribution_id, sources in sources_by_distribution.items()
            if sources
        }

    @staticmethod
    def _source_thresholds(
        *,
        inventory: CloudFrontInventory,
        state: ProfileCacheState,
        source_key_by_distribution: dict[str, str],
        month_key: str,
    ) -> dict[str, datetime]:
        thresholds: dict[str, datetime] = {}
        for distribution in inventory.distributions:
            existing = state.distributions.get(distribution.distribution_id)
            if existing is None:
                continue
            cached = existing.s3
            if cached.month_key != month_key:
                continue
            source_key = source_key_by_distribution.get(distribution.distribution_id, "s3:none")
            if cached.source_key != source_key or cached.last_updated is None:
                continue
            bucket_names = CloudFrontS3LogsUploadService._bucket_names_from_source_key(source_key)
            for bucket_name in bucket_names:
                current = thresholds.get(bucket_name)
                if current is None or cached.last_updated < current:
                    thresholds[bucket_name] = cached.last_updated
        return thresholds

    @staticmethod
    def _target_distribution_ids(
        *,
        inventory: CloudFrontInventory,
        state: ProfileCacheState,
        source_key_by_distribution: dict[str, str],
    ) -> set[str]:
        target_distribution_ids: set[str] = set()
        for distribution in inventory.distributions:
            distribution_id = distribution.distribution_id
            source_key = source_key_by_distribution.get(distribution_id, "s3:none")
            cached = state.distributions.get(distribution_id)
            if source_key != "s3:none":
                target_distribution_ids.add(distribution_id)
                continue
            if cached is not None and (
                cached.s3.upload is not None or cached.s3.source_key not in {None, "s3:none"}
            ):
                target_distribution_ids.add(distribution_id)
        return target_distribution_ids

    @staticmethod
    def _bucket_names_from_source_key(source_key: str) -> tuple[str, ...]:
        if not source_key.startswith("s3:"):
            return ()
        payload = source_key[len("s3:") :]
        if not payload or payload == "none":
            return ()
        buckets = []
        for part in payload.split("|"):
            bucket, _, _ = part.partition("/")
            if bucket:
                buckets.append(bucket)
        return tuple(sorted(set(buckets)))

    def _local_parquet_files(self, *, profile_name: str, month_key: str) -> list[Path]:
        root = self.paths.parquet_month_dir(profile_name, month_key) / S3_LOG_QUERY_SUBDIR
        if not root.exists():
            return []
        return sorted(path for path in root.rglob("*.parquet") if path.is_file())

    def _local_parquet_path(
        self,
        *,
        profile_name: str,
        month_key: str,
        bucket: str,
        remote_key: str,
    ) -> Path:
        digest = hashlib.sha256(f"{bucket}:{remote_key}".encode("utf-8")).hexdigest()[:16]
        filename = Path(remote_key).name
        return (
            self.paths.parquet_month_dir(profile_name, month_key)
            / S3_LOG_QUERY_SUBDIR
            / digest
            / filename
        )

    @staticmethod
    def _query_summary(local_parquet_files: list[Path]) -> dict[str, int]:
        if not local_parquet_files:
            return {}

        connection = duckdb.connect(database=":memory:")
        try:
            connection.read_parquet([str(path) for path in local_parquet_files]).create_view("data")
            rows = connection.execute(
                """
                SELECT
                  "DistributionId" AS distribution_id,
                  COALESCE(SUM("cs-bytes"), 0) AS upload_bytes
                FROM data
                WHERE "DistributionId" IS NOT NULL
                GROUP BY "DistributionId"
                """
            ).fetchall()
        finally:
            connection.close()

        totals: dict[str, int] = {}
        for row in rows or []:
            if not row:
                continue
            distribution_id = str(row[0]).strip()
            if not distribution_id:
                continue
            try:
                totals[distribution_id] = int(float(row[1]))
            except (TypeError, ValueError):
                continue
        return totals

    @staticmethod
    def _parse_log_key(remote_key: str) -> tuple[str, str] | None:
        filename = Path(remote_key).name
        match = S3_LOG_FILE_PATTERN.match(filename)
        if not match:
            return None
        return match.group("distribution_id"), match.group("date")[:7]

    @staticmethod
    def _normalize_s3_bucket_identifier(value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        if text.startswith("arn:aws:s3:::"):
            return text[len("arn:aws:s3:::") :].strip("/")
        return text.removeprefix("s3://").strip("/")

    @staticmethod
    def _coerce_utc(value: object) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return datetime.min.replace(tzinfo=timezone.utc)

    @staticmethod
    def _month_key(value: datetime) -> str:
        return value.strftime("%Y-%m")

    @staticmethod
    def _month_start(value: datetime) -> datetime:
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _empty_metrics(
        *,
        month_key: str,
        source_key: str,
        last_updated: datetime | None = None,
    ) -> SourceMetrics:
        return SourceMetrics(
            month_key=month_key,
            source_key=source_key,
            last_updated=last_updated,
        )
