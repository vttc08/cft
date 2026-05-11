from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3

from cft.models.distribution import DistributionSummary, normalize_distribution


@dataclass(frozen=True)
class AccountIdentity:
    account_id: str
    arn: str
    user_id: str


@dataclass(frozen=True)
class CloudFrontInventory:
    profile_name: str
    identity: AccountIdentity | None
    distributions: tuple[DistributionSummary, ...]


class CloudFrontInventoryService:
    """Read-only AWS adapter for the first-stage CloudFront browser."""

    def __init__(self, profile_name: str | None = None, region_name: str | None = None) -> None:
        self.profile_name = profile_name
        self.region_name = region_name

    def load(self) -> CloudFrontInventory:
        session = boto3.Session(
            profile_name=self.profile_name,
            region_name=self.region_name,
        )
        profile_name = session.profile_name or self.profile_name or "default"
        identity = self._get_identity(session)
        distributions = self._list_distributions(session)
        return CloudFrontInventory(
            profile_name=profile_name,
            identity=identity,
            distributions=tuple(distributions),
        )

    @staticmethod
    def _get_identity(session: boto3.Session) -> AccountIdentity:
        response = session.client("sts").get_caller_identity()
        return AccountIdentity(
            account_id=str(response.get("Account", "")),
            arn=str(response.get("Arn", "")),
            user_id=str(response.get("UserId", "")),
        )

    @staticmethod
    def _list_distributions(session: boto3.Session) -> list[DistributionSummary]:
        client = session.client("cloudfront")
        paginator = client.get_paginator("list_distributions")
        distributions: list[DistributionSummary] = []
        for page in paginator.paginate():
            items: list[dict[str, Any]] = (
                page.get("DistributionList", {}).get("Items", []) or []
            )
            distributions.extend(normalize_distribution(item) for item in items)
        return distributions
