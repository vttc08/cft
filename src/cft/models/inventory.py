from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from cft.models.cache import StandardLogDeliveryRecord
from cft.models.distribution import DistributionSummary


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
    distribution_types: dict[str, str] = field(default_factory=dict)
    standard_log_deliveries: dict[str, tuple[StandardLogDeliveryRecord, ...]] = field(
        default_factory=dict
    )
    cache_last_updated: datetime | None = None
    from_cache: bool = False
