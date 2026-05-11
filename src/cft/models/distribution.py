from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DistributionSummary:
    """Normalized CloudFront distribution fields used by the first-stage UI."""

    distribution_id: str
    arn: str
    comment: str
    domain_name: str
    enabled: bool
    status: str
    aliases: tuple[str, ...]
    origins: tuple[str, ...]
    last_modified_time: datetime | None


def normalize_distribution(item: dict[str, Any]) -> DistributionSummary:
    aliases = item.get("Aliases", {}).get("Items", []) or []
    origins = item.get("Origins", {}).get("Items", []) or []
    origin_domains = tuple(
        str(origin.get("DomainName", "")) for origin in origins if origin.get("DomainName")
    )

    return DistributionSummary(
        distribution_id=str(item.get("Id", "")),
        arn=str(item.get("ARN", "")),
        comment=str(item.get("Comment", "")),
        domain_name=str(item.get("DomainName", "")),
        enabled=bool(item.get("Enabled", False)),
        status=str(item.get("Status", "")),
        aliases=tuple(str(alias) for alias in aliases),
        origins=origin_domains,
        last_modified_time=item.get("LastModifiedTime"),
    )
