from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cft.cache.policies import CachePolicy, format_utc_datetime, parse_utc_datetime
from cft.cache.store import JsonFileStore
from cft.config.paths import AppPaths, profile_key
from cft.config.settings import load_app_settings
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
                s3=SourceMetrics(download=10, upload=11, requests=12),
                cwl=SourceMetrics(download=13, upload=14, requests=15),
            )
        },
    )

    payload = state.to_payload()
    round_tripped = ProfileCacheState.from_payload(payload)

    assert round_tripped.profile_name == "dev"
    assert round_tripped.profile.cost == 4.5
    assert round_tripped.distributions["E123"].type == "PAYG"
    assert round_tripped.distributions["E123"].s3.download == 10
    assert round_tripped.distributions["E123"].cwl.requests == 15


def test_cache_policy_uses_utc_timestamps() -> None:
    updated = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    policy = CachePolicy(ttl=timedelta(hours=1))

    assert policy.is_fresh(updated, now=datetime(2026, 5, 13, 12, 59, tzinfo=timezone.utc))
    assert policy.is_stale(updated, now=datetime(2026, 5, 13, 13, 1, tzinfo=timezone.utc))
    assert format_utc_datetime(updated) == "2026-05-13T12:00:00Z"
    assert parse_utc_datetime("2026-05-13T12:00:00Z") == updated
