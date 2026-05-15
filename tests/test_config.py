from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cft.cache.policies import CachePolicy, format_utc_datetime, parse_utc_datetime
from cft.cache.store import JsonFileStore
from cft.config.paths import AppPaths, profile_key
from cft.config.settings import (
    display_data_export_prefix,
    load_app_settings,
    normalize_data_export_prefix,
    save_data_export_settings,
)
from cft.models.cache import (
    DistributionCacheRecord,
    ProfileCacheState,
    ProfileSummaryCache,
    SourceMetrics,
)


def test_app_paths_layout_uses_profile_scoped_config_cache_and_data(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")

    assert paths.config_file == tmp_path / "cft" / "config" / "config.toml"
    assert paths.profile_config_file("dev/account") == tmp_path / "cft" / "config" / "dev_account.toml"
    assert paths.profile_state_file("dev") == (
        tmp_path / "cft" / "cache" / "dev" / "state.json"
    )
    assert paths.distributions_cache_file("dev") == paths.profile_state_file("dev")
    assert paths.usage_cache_file("dev") == paths.profile_state_file("dev")
    assert paths.billing_cache_file("dev") == paths.profile_state_file("dev")
    assert paths.cloudfront_logs_cache_file("dev") == paths.profile_state_file("dev")
    assert paths.parquet_dir("dev") == (
        tmp_path / "cft" / "data" / "data_exports" / "dev" / "parquet"
    )


def test_profile_key_falls_back_to_default_and_sanitizes() -> None:
    assert profile_key(None) == "default"
    assert profile_key("prod") == "prod"
    assert profile_key("../prod/account") == "prod_account"


def test_load_app_settings_creates_human_editable_default_config(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")

    settings = load_app_settings(paths)

    assert paths.config_file.exists()
    assert settings.aws.cloudfront_region == "us-east-1"
    assert settings.cache.distribution_ttl_seconds == 3600
    assert settings.cache.data_export_manifest_check_seconds == 14400
    config_text = paths.config_file.read_text(encoding="utf-8")
    assert "[aws]" in config_text
    assert "[cache]" in config_text
    assert "[data_export]" in config_text


def test_load_app_settings_merges_profile_specific_config(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    load_app_settings(paths)
    profile_file = paths.profile_config_file("dev")
    profile_file.write_text(
        """
[data_export]
bucket = "profile-bucket"
prefix = "profile-prefix"
export_name = "profile-export"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    settings = load_app_settings(paths, profile_name="dev", create=False)

    assert settings.data_export.bucket == "profile-bucket"
    assert settings.data_export.prefix == "profile-prefix"
    assert settings.data_export.export_name == "profile-export"


def test_save_data_export_settings_writes_profile_file_and_round_trips(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")

    save_data_export_settings(
        paths=paths,
        profile_name="dev",
        bucket="example-bucket",
        prefix="/",
        export_name="cur-export",
    )

    settings = load_app_settings(paths, profile_name="dev", create=False)
    profile_text = paths.profile_config_file("dev").read_text(encoding="utf-8")

    assert 'bucket = "example-bucket"' in profile_text
    assert 'prefix = ""' in profile_text
    assert 'export_name = "cur-export"' in profile_text
    assert settings.data_export.bucket == "example-bucket"
    assert settings.data_export.prefix is None
    assert settings.data_export.export_name == "cur-export"


def test_data_export_prefix_helpers_normalize_and_display_root() -> None:
    assert normalize_data_export_prefix(None) is None
    assert normalize_data_export_prefix("") is None
    assert normalize_data_export_prefix("/") is None
    assert normalize_data_export_prefix("/exports/monthly/") == "exports/monthly"
    assert display_data_export_prefix(None) == "/"
    assert display_data_export_prefix("/") == "/"
    assert display_data_export_prefix("exports/monthly") == "/exports/monthly"


def test_json_file_store_round_trips_cache_payload(tmp_path) -> None:
    store = JsonFileStore(tmp_path / "cache" / "distributions.json")

    store.write({"schema_version": 1, "profile_name": "dev", "distributions": {"E123": {"comment": "site"}}})

    assert store.read() == {
        "schema_version": 1,
        "profile_name": "dev",
        "distributions": {"E123": {"comment": "site"}},
    }


def test_profile_cache_state_round_trips_nested_sources() -> None:
    state = ProfileCacheState(
        profile_name="dev",
        last_updated=datetime(2026, 5, 13, 12, tzinfo=timezone.utc),
        profile=ProfileSummaryCache(
            last_updated=datetime(2026, 5, 13, 12, tzinfo=timezone.utc),
            manifest_last_checked=datetime(2026, 5, 13, 11, tzinfo=timezone.utc),
            month_key="2026-05",
            s3_cur_bucket="billing-bucket",
            s3_cur_prefix="exports",
            s3_cur_export_name="cloudfront-cur",
            manifest_key="exports/cloudfront-cur/BILLING_PERIOD=2026-05/metadata/cloudfront-cur-Manifest.json",
            manifest_etag="etag-1",
            parquet_files={
                "exports/file-1.parquet": {
                    "etag": "etag-a",
                    "local_path": "/tmp/file-1.parquet",
                }
            },
            data_start=datetime(2026, 5, 1, 0, tzinfo=timezone.utc),
            data_end=datetime(2026, 5, 13, 8, tzinfo=timezone.utc),
            download=1,
            upload=2,
            requests=3,
            cost=4.5,
        ),
        distributions={
            "E123": DistributionCacheRecord(
                distribution_id="E123",
                type="PAYG",
                inventory={"comment": "site"},
                cw=SourceMetrics(
                    download=7,
                    requests=8,
                    last_updated=datetime(2026, 5, 13, 12, tzinfo=timezone.utc),
                    month_key="2026-05",
                ),
                s3=SourceMetrics(download=10, upload=11, requests=12),
                cwl=SourceMetrics(download=13, upload=14, requests=15),
            )
        },
    )

    payload = state.to_payload()
    round_tripped = ProfileCacheState.from_payload(payload)

    assert round_tripped.profile_name == "dev"
    assert round_tripped.profile.cost == 4.5
    assert round_tripped.profile.month_key == "2026-05"
    assert round_tripped.profile.s3_cur_bucket == "billing-bucket"
    assert round_tripped.profile.manifest_etag == "etag-1"
    assert round_tripped.profile.parquet_files["exports/file-1.parquet"]["etag"] == "etag-a"
    assert round_tripped.profile.data_end == datetime(2026, 5, 13, 8, tzinfo=timezone.utc)
    assert round_tripped.distributions["E123"].type == "PAYG"
    assert round_tripped.distributions["E123"].cw.download == 7
    assert round_tripped.distributions["E123"].cw.month_key == "2026-05"
    assert round_tripped.distributions["E123"].s3.download == 10
    assert round_tripped.distributions["E123"].cwl.requests == 15


def test_distribution_cache_record_defaults_plan_type_to_payg() -> None:
    record = DistributionCacheRecord.from_payload(
        "E123",
        {
            "inventory": {"comment": "site"},
        },
    )
    legacy = DistributionCacheRecord.from_payload(
        "E123",
        {
            "type": "unknown",
            "inventory": {"comment": "site"},
        },
    )

    assert record.type == "PAYG"
    assert legacy.type == "PAYG"


def test_cache_policy_uses_utc_timestamps() -> None:
    updated = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    policy = CachePolicy(ttl=timedelta(hours=1))

    assert policy.is_fresh(updated, now=datetime(2026, 5, 13, 12, 59, tzinfo=timezone.utc))
    assert policy.is_stale(updated, now=datetime(2026, 5, 13, 13, 1, tzinfo=timezone.utc))
    assert format_utc_datetime(updated) == "2026-05-13T12:00:00Z"
    assert parse_utc_datetime("2026-05-13T12:00:00Z") == updated
