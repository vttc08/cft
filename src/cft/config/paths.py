from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "cft"
DEFAULT_APP_HOME = Path.home() / ".cft"


def profile_key(profile_name: str | None) -> str:
    """Return a filesystem-safe profile key while preserving normal AWS names."""

    profile = profile_name or "default"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile).strip("._")
    return safe or "default"


@dataclass(frozen=True)
class AppPaths:
    """Platform-aware local paths for config, cache, and downloaded data."""

    config_dir: Path
    cache_dir: Path
    data_dir: Path
    root_dir: Path | None = None

    @classmethod
    def from_base(cls, base_dir: Path) -> AppPaths:
        base_dir = base_dir.expanduser()
        return cls(
            config_dir=base_dir / "config",
            cache_dir=base_dir / "cache",
            data_dir=base_dir / "data",
            root_dir=base_dir,
        )

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def profile_cache_root(self) -> Path:
        return self.cache_dir

    @property
    def data_exports_root(self) -> Path:
        return self.data_dir / "data_exports"

    def profile_config_file(self, profile_name: str | None) -> Path:
        return self.config_dir / f"{profile_key(profile_name)}.toml"

    def profile_cache_dir(self, profile_name: str | None) -> Path:
        return self.profile_cache_root / profile_key(profile_name)

    def profile_state_file(self, profile_name: str | None) -> Path:
        return self.profile_cache_dir(profile_name) / "state.json"

    def distributions_cache_file(self, profile_name: str | None) -> Path:
        return self.profile_state_file(profile_name)

    def usage_cache_file(self, profile_name: str | None) -> Path:
        return self.profile_state_file(profile_name)

    def billing_cache_file(self, profile_name: str | None) -> Path:
        return self.profile_state_file(profile_name)

    def cloudfront_logs_cache_file(self, profile_name: str | None) -> Path:
        return self.profile_state_file(profile_name)

    def data_exports_dir(self, profile_name: str | None) -> Path:
        return self.data_exports_root / profile_key(profile_name)

    def parquet_dir(self, profile_name: str | None) -> Path:
        return self.data_exports_dir(profile_name) / "parquet"

    def ensure_base_dirs(self) -> None:
        for directory in (
            self.config_dir,
            self.cache_dir,
            self.data_dir,
            self.data_exports_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def ensure_profile_dirs(self, profile_name: str | None) -> None:
        self.ensure_base_dirs()
        for directory in (
            self.profile_cache_dir(profile_name),
            self.data_exports_dir(profile_name),
            self.parquet_dir(profile_name),
        ):
            directory.mkdir(parents=True, exist_ok=True)


def get_app_paths() -> AppPaths:
    """Resolve cft paths using a single home tree by default."""

    home_override = os.environ.get("CFT_HOME")
    if home_override:
        return AppPaths.from_base(Path(home_override))

    config_dir = os.environ.get("CFT_CONFIG_DIR")
    cache_dir = os.environ.get("CFT_CACHE_DIR")
    data_dir = os.environ.get("CFT_DATA_DIR")
    if config_dir or cache_dir or data_dir:
        return AppPaths(
            config_dir=Path(config_dir).expanduser() if config_dir else DEFAULT_APP_HOME / "config",
            cache_dir=Path(cache_dir).expanduser() if cache_dir else DEFAULT_APP_HOME / "cache",
            data_dir=Path(data_dir).expanduser() if data_dir else DEFAULT_APP_HOME / "data",
            root_dir=None,
        )

    return AppPaths.from_base(DEFAULT_APP_HOME)
