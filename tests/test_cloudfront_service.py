from datetime import datetime, timezone
import json

from cft.aws.cloudfront import CloudFrontInventoryService
from cft.config.paths import AppPaths
from cft.config.settings import AppSettings, AwsSettings, CacheSettings


class FakePaginator:
    def paginate(self) -> list[dict[str, object]]:
        return [
            {
                "DistributionList": {
                    "Items": [
                        {
                            "Id": "E123",
                            "Comment": "site",
                            "DomainName": "d111.cloudfront.net",
                            "Enabled": True,
                            "Status": "Deployed",
                        }
                    ]
                }
            }
        ]


class FakeCloudFrontClient:
    def get_paginator(self, name: str) -> FakePaginator:
        assert name == "list_distributions"
        return FakePaginator()


class FakeStsClient:
    def get_caller_identity(self) -> dict[str, str]:
        return {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/test",
            "UserId": "AIDA",
        }


class FakeSession:
    profile_name = "dev"

    def client(self, service_name: str) -> object:
        if service_name == "sts":
            return FakeStsClient()
        if service_name == "cloudfront":
            return FakeCloudFrontClient()
        raise AssertionError(service_name)


def test_cloudfront_inventory_service_reads_identity_and_distributions() -> None:
    service = CloudFrontInventoryService()

    identity = service._get_identity(FakeSession())  # type: ignore[arg-type]
    distributions = service._list_distributions(FakeSession())  # type: ignore[arg-type]

    assert identity.account_id == "123456789012"
    assert len(distributions) == 1
    assert distributions[0].distribution_id == "E123"


def test_cloudfront_inventory_service_loads_cached_distribution_types(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(distribution_ttl_seconds=3600))
    state = {
        "schema_version": 1,
        "profile_name": "dev",
        "last_updated": "2026-05-13T12:00:00Z",
        "distributions": {
            "E123": {
                "distribution_id": "E123",
                "type": "Free",
                "inventory": {
                    "comment": "site",
                    "domain_name": "d111.cloudfront.net",
                },
            }
        },
    }
    paths.profile_state_file("dev").parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_file("dev").write_text(json.dumps(state), encoding="utf-8")

    class CachedOnlySession(FakeSession):
        def client(self, service_name: str) -> object:
            raise AssertionError(f"cache hit should not call {service_name}")

    inventory = CloudFrontInventoryService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: CachedOnlySession(),  # type: ignore[arg-type]
        now=lambda: now,
    ).load()

    assert inventory.from_cache is True
    assert inventory.distribution_types["E123"] == "Free"


def test_cloudfront_inventory_service_saves_distribution_type_to_cache(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    service = CloudFrontInventoryService(profile_name="dev", paths=paths)

    service.save_distribution_type(
        profile_name="dev",
        distribution_id="E123",
        distribution_type="Free",
    )

    payload = json.loads(paths.profile_state_file("dev").read_text(encoding="utf-8"))
    assert payload["distributions"]["E123"]["type"] == "Free"


def test_cloudfront_inventory_service_refresh_preserves_manual_distribution_type(
    tmp_path,
) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(distribution_ttl_seconds=3600))
    state = {
        "schema_version": 1,
        "profile_name": "dev",
        "last_updated": "2026-05-13T10:00:00Z",
        "distributions": {
            "E123": {
                "distribution_id": "E123",
                "type": "Free",
                "inventory": {
                    "comment": "site",
                    "domain_name": "d111.cloudfront.net",
                },
            }
        },
    }
    paths.profile_state_file("dev").parent.mkdir(parents=True, exist_ok=True)
    paths.profile_state_file("dev").write_text(json.dumps(state), encoding="utf-8")

    class UpdatedPaginator:
        def paginate(self) -> list[dict[str, object]]:
            return [
                {
                    "DistributionList": {
                        "Items": [
                            {
                                "Id": "E123",
                                "Comment": "site",
                                "DomainName": "d111.cloudfront.net",
                                "Enabled": True,
                                "Status": "Deployed",
                            }
                        ]
                    }
                }
            ]

    class UpdatedCloudFrontClient:
        def get_paginator(self, name: str) -> UpdatedPaginator:
            assert name == "list_distributions"
            return UpdatedPaginator()

    class UpdatedSession(FakeSession):
        def client(self, service_name: str) -> object:
            if service_name == "cloudfront":
                return UpdatedCloudFrontClient()
            return super().client(service_name)

    inventory = CloudFrontInventoryService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: UpdatedSession(),  # type: ignore[arg-type]
        now=lambda: now,
    ).load(refresh=True)

    payload = json.loads(paths.profile_state_file("dev").read_text(encoding="utf-8"))
    assert inventory.distribution_types["E123"] == "Free"
    assert payload["distributions"]["E123"]["type"] == "Free"


def test_cloudfront_inventory_service_writes_and_reads_fresh_cache(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    now = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(distribution_ttl_seconds=3600))

    service = CloudFrontInventoryService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(),  # type: ignore[arg-type]
        now=lambda: now,
    )

    live_inventory = service.load()

    assert live_inventory.from_cache is False
    assert paths.profile_state_file("dev").exists()
    payload = json.loads(paths.profile_state_file("dev").read_text(encoding="utf-8"))
    assert payload["profile_name"] == "dev"
    assert "profile" in payload
    assert "s3_cur_bucket" not in payload["profile"]
    assert payload["distributions"]["E123"]["inventory"]["comment"] == "site"

    class CachedOnlySession:
        profile_name = "dev"

        def client(self, service_name: str) -> object:
            raise AssertionError(f"cache hit should not call {service_name}")

    cached_service = CloudFrontInventoryService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: CachedOnlySession(),  # type: ignore[arg-type]
        now=lambda: now,
    )

    cached_inventory = cached_service.load()

    assert cached_inventory.from_cache is True
    assert cached_inventory.identity is not None
    assert cached_inventory.identity.account_id == "123456789012"
    assert cached_inventory.distributions[0].distribution_id == "E123"


def test_cloudfront_inventory_service_prefers_aws_default_profile_when_profile_is_unspecified(
    tmp_path,
) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    settings = AppSettings(aws=AwsSettings(default_profile="custom"), cache=CacheSettings())

    class DefaultSession(FakeSession):
        profile_name = None

    service = CloudFrontInventoryService(
        paths=paths,
        settings=settings,
        session_factory=lambda **_: DefaultSession(),  # type: ignore[arg-type]
        now=lambda: datetime(2026, 5, 13, 12, tzinfo=timezone.utc),
    )

    inventory = service.load()

    assert inventory.profile_name == "default"
    assert paths.profile_state_file("default").exists()


def test_cloudfront_inventory_service_refreshes_stale_cache(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    first = datetime(2026, 5, 13, 10, tzinfo=timezone.utc)
    later = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(distribution_ttl_seconds=3600))

    CloudFrontInventoryService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(),  # type: ignore[arg-type]
        now=lambda: first,
    ).load()

    class UpdatedPaginator:
        def paginate(self) -> list[dict[str, object]]:
            return [{"DistributionList": {"Items": [{"Id": "E456", "Comment": "new"}]}}]

    class UpdatedCloudFrontClient:
        def get_paginator(self, name: str) -> UpdatedPaginator:
            assert name == "list_distributions"
            return UpdatedPaginator()

    class UpdatedSession(FakeSession):
        def client(self, service_name: str) -> object:
            if service_name == "cloudfront":
                return UpdatedCloudFrontClient()
            return super().client(service_name)

    refreshed = CloudFrontInventoryService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: UpdatedSession(),  # type: ignore[arg-type]
        now=lambda: later,
    ).load()

    assert refreshed.from_cache is False
    assert refreshed.distributions[0].distribution_id == "E456"


def test_cloudfront_inventory_service_falls_back_to_stale_cache_on_aws_error(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    first = datetime(2026, 5, 13, 10, tzinfo=timezone.utc)
    later = datetime(2026, 5, 13, 12, tzinfo=timezone.utc)
    settings = AppSettings(cache=CacheSettings(distribution_ttl_seconds=3600))

    CloudFrontInventoryService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FakeSession(),  # type: ignore[arg-type]
        now=lambda: first,
    ).load()

    class FailingSession:
        profile_name = "dev"

        def client(self, service_name: str) -> object:
            raise RuntimeError(f"{service_name} unavailable")

    cached = CloudFrontInventoryService(
        profile_name="dev",
        paths=paths,
        settings=settings,
        session_factory=lambda **_: FailingSession(),  # type: ignore[arg-type]
        now=lambda: later,
    ).load()

    assert cached.from_cache is True
    assert cached.distributions[0].distribution_id == "E123"
