from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import tomlkit
from tomlkit.items import Table

from cft.config.paths import AppPaths

DEFAULT_DISTRIBUTION_TTL_SECONDS = 60 * 60
DEFAULT_USAGE_TTL_SECONDS = 60 * 60
DEFAULT_LOGS_UPLOAD_TTL_SECONDS = 60 * 60
DEFAULT_DATA_EXPORT_MANIFEST_CHECK_SECONDS = 4 * 60 * 60


@dataclass(frozen=True)
class CacheSettings:
    distribution_ttl_seconds: int = DEFAULT_DISTRIBUTION_TTL_SECONDS
    usage_ttl_seconds: int = DEFAULT_USAGE_TTL_SECONDS
    logs_upload_ttl_seconds: int = DEFAULT_LOGS_UPLOAD_TTL_SECONDS
    data_export_manifest_check_seconds: int = DEFAULT_DATA_EXPORT_MANIFEST_CHECK_SECONDS


@dataclass(frozen=True)
class AwsSettings:
    default_profile: str | None = None
    cloudfront_region: str = "us-east-1"


@dataclass(frozen=True)
class DataExportSettings:
    bucket: str | None = None
    prefix: str | None = None
    export_name: str | None = None


@dataclass(frozen=True)
class AppSettings:
    aws: AwsSettings = field(default_factory=AwsSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    data_export: DataExportSettings = field(default_factory=DataExportSettings)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> AppSettings:
        aws = mapping.get("aws", {}) or {}
        cache = mapping.get("cache", {}) or {}
        data_export = mapping.get("data_export", {}) or {}

        return cls(
            aws=AwsSettings(
                default_profile=_none_if_blank(aws.get("default_profile")),
                cloudfront_region=str(aws.get("cloudfront_region") or "us-east-1"),
            ),
            cache=CacheSettings(
                distribution_ttl_seconds=_positive_int(
                    cache.get("distribution_ttl_seconds"),
                    DEFAULT_DISTRIBUTION_TTL_SECONDS,
                ),
                usage_ttl_seconds=_positive_int(
                    cache.get("usage_ttl_seconds"),
                    DEFAULT_USAGE_TTL_SECONDS,
                ),
                logs_upload_ttl_seconds=_positive_int(
                    cache.get("logs_upload_ttl_seconds"),
                    DEFAULT_LOGS_UPLOAD_TTL_SECONDS,
                ),
                data_export_manifest_check_seconds=_positive_int(
                    cache.get("data_export_manifest_check_seconds"),
                    DEFAULT_DATA_EXPORT_MANIFEST_CHECK_SECONDS,
                ),
            ),
            data_export=DataExportSettings(
                bucket=_none_if_blank(data_export.get("bucket")),
                prefix=_none_if_blank(data_export.get("prefix")),
                export_name=_none_if_blank(data_export.get("export_name")),
            ),
        )


def load_app_settings(
    paths: AppPaths | None = None,
    *,
    profile_name: str | None = None,
    create: bool = True,
) -> AppSettings:
    if paths is None:
        from cft.config.paths import get_app_paths

        paths = get_app_paths()

    paths.ensure_base_dirs()
    if create and not paths.config_file.exists():
        paths.config_file.write_text(default_config_text(), encoding="utf-8")

    profile_config_file = paths.profile_config_file(profile_name) if profile_name else None
    if create and profile_config_file is not None and not profile_config_file.exists():
        profile_config_file.write_text(default_profile_config_text(), encoding="utf-8")

    mapping: dict[str, Any] = {}
    if paths.config_file.exists():
        mapping = tomlkit.parse(paths.config_file.read_text(encoding="utf-8")).unwrap()
    if profile_config_file is not None and profile_config_file.exists():
        mapping = _merge_mappings(mapping, tomlkit.parse(profile_config_file.read_text(encoding="utf-8")).unwrap())
    return AppSettings.from_mapping(mapping)


def settings_profile_name(profile_name: str | None) -> str:
    return (profile_name or "default").strip() or "default"


def normalize_data_export_prefix(prefix: str | None) -> str | None:
    if prefix is None:
        return None
    text = str(prefix).strip()
    if not text or text == "/":
        return None
    return text.strip("/")


def display_data_export_prefix(prefix: str | None) -> str:
    normalized = normalize_data_export_prefix(prefix)
    return f"/{normalized}" if normalized else "/"


def save_data_export_settings(
    *,
    bucket: str,
    export_name: str,
    prefix: str | None = None,
    paths: AppPaths | None = None,
    profile_name: str | None = None,
) -> None:
    if paths is None:
        from cft.config.paths import get_app_paths

        paths = get_app_paths()

    profile_name = settings_profile_name(profile_name)
    paths.ensure_base_dirs()
    target = paths.profile_config_file(profile_name)
    if not target.exists():
        target.write_text(default_profile_config_text(), encoding="utf-8")

    document = tomlkit.parse(target.read_text(encoding="utf-8"))
    data_export = _ensure_table(document, "data_export")
    data_export["bucket"] = bucket.strip()
    data_export["prefix"] = normalize_data_export_prefix(prefix) or ""
    data_export["export_name"] = export_name.strip()
    target.write_text(tomlkit.dumps(document), encoding="utf-8")


def default_config_text() -> str:
    document = tomlkit.document()
    document.add(tomlkit.comment("cft local application settings. AWS credentials stay in ~/.aws."))
    document.add(tomlkit.nl())

    aws = tomlkit.table()
    aws.add(tomlkit.comment("Leave blank to use boto3's default profile resolution."))
    aws["default_profile"] = ""
    aws["cloudfront_region"] = "us-east-1"
    document["aws"] = aws

    cache = tomlkit.table()
    cache.add(tomlkit.comment("Distribution discovery is an AWS read; keep this non-zero."))
    cache["distribution_ttl_seconds"] = DEFAULT_DISTRIBUTION_TTL_SECONDS
    cache["usage_ttl_seconds"] = DEFAULT_USAGE_TTL_SECONDS
    cache["logs_upload_ttl_seconds"] = DEFAULT_LOGS_UPLOAD_TTL_SECONDS
    cache["data_export_manifest_check_seconds"] = DEFAULT_DATA_EXPORT_MANIFEST_CHECK_SECONDS
    document["cache"] = cache

    data_export = tomlkit.table()
    data_export.add(tomlkit.comment("Optional existing AWS Data Export/CUR 2.0 delivery location."))
    data_export["bucket"] = ""
    data_export["prefix"] = ""
    data_export["export_name"] = ""
    document["data_export"] = data_export

    return tomlkit.dumps(document)


def default_profile_config_text() -> str:
    document = tomlkit.document()
    document.add(tomlkit.comment("Profile-specific linkage and overrides."))
    document.add(tomlkit.nl())

    aws = tomlkit.table()
    aws.add(tomlkit.comment("Optional profile name override. Leave blank to use the CLI/session profile."))
    aws["default_profile"] = ""
    document["aws"] = aws

    data_export = tomlkit.table()
    data_export.add(tomlkit.comment("Link this profile to an existing CUR/Data Export destination."))
    data_export["bucket"] = ""
    data_export["prefix"] = ""
    data_export["export_name"] = ""
    document["data_export"] = data_export

    return tomlkit.dumps(document)


def _none_if_blank(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _merge_mappings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_mappings(merged[key], value)
        else:
            merged[key] = value
    return merged


def _ensure_table(document: Any, key: str) -> Table:
    table = document.get(key)
    if isinstance(table, Table):
        return table

    new_table = tomlkit.table()
    document[key] = new_table
    return new_table
