from __future__ import annotations

from collections.abc import Callable

import boto3

from cft.config.paths import AppPaths, get_app_paths
from cft.config.settings import AppSettings, load_app_settings, settings_profile_name

SessionFactory = Callable[..., boto3.Session]


class S3BucketDiscoveryService:
    """Read-only S3 bucket discovery for guided setup flows."""

    def __init__(
        self,
        profile_name: str | None = None,
        *,
        paths: AppPaths | None = None,
        settings: AppSettings | None = None,
        session_factory: SessionFactory = boto3.Session,
    ) -> None:
        self.profile_name = profile_name
        self.paths = paths or get_app_paths()
        self.settings = settings
        self.session_factory = session_factory

    def list_bucket_names(self) -> tuple[str, ...]:
        settings = self.settings or load_app_settings(
            self.paths,
            profile_name=settings_profile_name(self.profile_name),
        )
        session = self.session_factory(
            profile_name=self.profile_name,
            region_name=settings.aws.cloudfront_region,
        )
        response = session.client("s3").list_buckets()
        buckets = response.get("Buckets", []) or []
        names = sorted(
            str(bucket.get("Name", "")).strip()
            for bucket in buckets
            if str(bucket.get("Name", "")).strip()
        )
        return tuple(names)
