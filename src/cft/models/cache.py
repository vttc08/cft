from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from cft.cache.policies import format_utc_datetime, parse_utc_datetime


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _compact_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _mapping_of_strings(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        key_text = str(key).strip()
        value_text = str(value).strip()
        if key_text and value_text:
            result[key_text] = value_text
    return result


def _mapping_of_mapping_strings(payload: object) -> dict[str, dict[str, str]]:
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key, value in payload.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        normalized = _mapping_of_strings(value)
        if normalized:
            result[key_text] = normalized
    return result


@dataclass(frozen=True)
class SourceMetrics:
    download: int | None = None
    upload: int | None = None
    requests: int | None = None
    last_updated: datetime | None = None
    month_key: str | None = None

    @classmethod
    def from_payload(cls, payload: object) -> SourceMetrics:
        if not isinstance(payload, dict):
            return cls()

        return cls(
            download=_int_or_none(payload.get("download") or payload.get("bytes_downloaded")),
            upload=_int_or_none(payload.get("upload") or payload.get("bytes_uploaded")),
            requests=_int_or_none(payload.get("requests")),
            last_updated=parse_utc_datetime(payload.get("last_updated")),
            month_key=_string_or_none(payload.get("month_key")),
        )

    def to_payload(self) -> dict[str, Any]:
        return _compact_mapping(
            {
                "download": self.download,
                "upload": self.upload,
                "requests": self.requests,
                "last_updated": (
                    format_utc_datetime(self.last_updated) if self.last_updated else None
                ),
                "month_key": self.month_key,
            }
        )

    def with_timestamp(self, last_updated: datetime | None) -> SourceMetrics:
        return replace(self, last_updated=last_updated)


@dataclass(frozen=True)
class ProfileSummaryCache:
    last_updated: datetime | None = None
    manifest_last_checked: datetime | None = None
    month_key: str | None = None
    s3_cur_bucket: str | None = None
    s3_cur_prefix: str | None = None
    s3_cur_export_name: str | None = None
    manifest_key: str | None = None
    manifest_etag: str | None = None
    parquet_files: dict[str, dict[str, str]] = field(default_factory=dict)
    data_start: datetime | None = None
    data_end: datetime | None = None
    download: int | None = None
    upload: int | None = None
    requests: int | None = None
    cost: float | None = None

    @classmethod
    def from_payload(cls, payload: object) -> ProfileSummaryCache:
        if not isinstance(payload, dict):
            return cls()

        return cls(
            last_updated=parse_utc_datetime(payload.get("last_updated")),
            manifest_last_checked=parse_utc_datetime(payload.get("manifest_last_checked")),
            month_key=_string_or_none(payload.get("month_key")),
            s3_cur_bucket=_string_or_none(payload.get("s3_cur_bucket") or payload.get("bucket")),
            s3_cur_prefix=_string_or_none(payload.get("s3_cur_prefix") or payload.get("prefix")),
            s3_cur_export_name=_string_or_none(
                payload.get("s3_cur_export_name") or payload.get("export_name")
            ),
            manifest_key=_string_or_none(payload.get("manifest_key")),
            manifest_etag=_string_or_none(payload.get("manifest_etag")),
            parquet_files=_mapping_of_mapping_strings(payload.get("parquet_files")),
            data_start=parse_utc_datetime(payload.get("data_start")),
            data_end=parse_utc_datetime(payload.get("data_end")),
            download=_int_or_none(payload.get("download") or payload.get("bytes_downloaded")),
            upload=_int_or_none(payload.get("upload") or payload.get("bytes_uploaded")),
            requests=_int_or_none(payload.get("requests")),
            cost=_float_or_none(payload.get("cost")),
        )

    def to_payload(self) -> dict[str, Any]:
        return _compact_mapping(
            {
                "last_updated": (
                    format_utc_datetime(self.last_updated) if self.last_updated else None
                ),
                "manifest_last_checked": (
                    format_utc_datetime(self.manifest_last_checked)
                    if self.manifest_last_checked
                    else None
                ),
                "month_key": self.month_key,
                "bucket": self.s3_cur_bucket,
                "prefix": self.s3_cur_prefix,
                "export_name": self.s3_cur_export_name,
                "manifest_key": self.manifest_key,
                "manifest_etag": self.manifest_etag,
                "parquet_files": self.parquet_files or None,
                "data_start": (
                    format_utc_datetime(self.data_start) if self.data_start else None
                ),
                "data_end": format_utc_datetime(self.data_end) if self.data_end else None,
                "download": self.download,
                "upload": self.upload,
                "requests": self.requests,
                "cost": self.cost,
            }
        )


@dataclass(frozen=True)
class DistributionCacheRecord:
    distribution_id: str
    type: str = "unknown"
    inventory: dict[str, Any] = field(default_factory=dict)
    cw: SourceMetrics = field(default_factory=SourceMetrics)
    s3: SourceMetrics = field(default_factory=SourceMetrics)
    cwl: SourceMetrics = field(default_factory=SourceMetrics)
    last_updated: datetime | None = None

    @classmethod
    def from_payload(cls, distribution_id: str, payload: object) -> DistributionCacheRecord:
        if not isinstance(payload, dict):
            return cls(distribution_id=distribution_id)

        inventory = payload.get("inventory")
        if not isinstance(inventory, dict):
            inventory = {
                key: value
                for key, value in payload.items()
                if key
                not in {"distribution_id", "type", "inventory", "cw", "s3", "cwl", "last_updated"}
            }

        return cls(
            distribution_id=str(payload.get("distribution_id") or distribution_id),
            type=_string_or_none(payload.get("type")) or "unknown",
            inventory=inventory,
            cw=SourceMetrics.from_payload(payload.get("cw")),
            s3=SourceMetrics.from_payload(payload.get("s3")),
            cwl=SourceMetrics.from_payload(payload.get("cwl")),
            last_updated=parse_utc_datetime(payload.get("last_updated")),
        )

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "distribution_id": self.distribution_id,
            "type": self.type,
            "inventory": self.inventory,
            "cw": self.cw.to_payload(),
            "s3": self.s3.to_payload(),
            "cwl": self.cwl.to_payload(),
        }
        if self.last_updated is not None:
            payload["last_updated"] = format_utc_datetime(self.last_updated)
        return payload

    def merged_inventory(
        self,
        inventory: dict[str, Any],
        *,
        last_updated: datetime | None,
        distribution_type: str | None = None,
    ) -> DistributionCacheRecord:
        return replace(
            self,
            inventory=inventory,
            last_updated=last_updated if last_updated is not None else self.last_updated,
            type=distribution_type if distribution_type is not None else self.type,
        )


@dataclass(frozen=True)
class ProfileCacheState:
    schema_version: int = 1
    profile_name: str = "default"
    last_updated: datetime | None = None
    identity: dict[str, str] | None = None
    profile: ProfileSummaryCache = field(default_factory=ProfileSummaryCache)
    distributions: dict[str, DistributionCacheRecord] = field(default_factory=dict)

    @classmethod
    def from_payload(
        cls,
        payload: object,
        *,
        profile_name: str | None = None,
    ) -> ProfileCacheState:
        if not isinstance(payload, dict):
            return cls(profile_name=profile_name or "default")

        profile_payload = payload.get("profile")
        if not isinstance(profile_payload, dict):
            profile_payload = payload

        distributions_payload = payload.get("distributions")
        if not isinstance(distributions_payload, dict):
            distributions_payload = {}

        return cls(
            schema_version=_int_or_none(payload.get("schema_version")) or 1,
            profile_name=str(payload.get("profile_name") or profile_name or "default"),
            last_updated=parse_utc_datetime(payload.get("last_updated")),
            identity=_identity_from_payload(payload.get("identity")),
            profile=ProfileSummaryCache.from_payload(profile_payload),
            distributions={
                str(distribution_id): DistributionCacheRecord.from_payload(
                    str(distribution_id),
                    distribution_payload,
                )
                for distribution_id, distribution_payload in distributions_payload.items()
                if isinstance(distribution_payload, dict)
            },
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "profile_name": self.profile_name,
            "profile": self.profile.to_payload(),
            "distributions": {
                distribution_id: distribution.to_payload()
                for distribution_id, distribution in sorted(self.distributions.items())
            },
        }
        if self.last_updated is not None:
            payload["last_updated"] = format_utc_datetime(self.last_updated)
        if self.identity is not None:
            payload["identity"] = self.identity
        return payload

    def with_inventory(
        self,
        *,
        profile_name: str,
        identity: dict[str, str] | None,
        inventory: dict[str, dict[str, Any]],
        last_updated: datetime,
    ) -> ProfileCacheState:
        merged_distributions: dict[str, DistributionCacheRecord] = {}
        for distribution_id, record in sorted(inventory.items()):
            existing = self.distributions.get(distribution_id) or DistributionCacheRecord(
                distribution_id=distribution_id
            )
            merged_distributions[distribution_id] = existing.merged_inventory(
                record,
                last_updated=last_updated,
            )

        return replace(
            self,
            profile_name=profile_name,
            last_updated=last_updated,
            identity=identity if identity is not None else self.identity,
            profile=replace(self.profile, last_updated=last_updated),
            distributions=merged_distributions,
        )


def _identity_from_payload(payload: object) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    identity = {
        "account_id": _string_or_none(payload.get("account_id")),
        "arn": _string_or_none(payload.get("arn")),
        "user_id": _string_or_none(payload.get("user_id")),
    }
    compact = _compact_mapping(identity)
    return compact or None
