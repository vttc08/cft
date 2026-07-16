from __future__ import annotations

from dataclasses import dataclass

from cft.models.billing import BillingSnapshot
from cft.models.cache import SourceMetrics
from cft.models.configuration import ConfigurationSnapshot
from cft.models.inventory import CloudFrontInventory


@dataclass(frozen=True)
class DashboardSnapshot:
    inventory: CloudFrontInventory
    usage_by_distribution: dict[str, SourceMetrics]
    billing: BillingSnapshot
    configuration: ConfigurationSnapshot
    onboarding_seen: bool


@dataclass(frozen=True)
class DashboardWarning:
    stage: str
    message: str


@dataclass(frozen=True)
class DashboardLoadResult:
    snapshot: DashboardSnapshot
    inventory_from_cache: bool
    usage_from_cache: bool
    billing_from_cache: bool
    warnings: tuple[DashboardWarning, ...] = ()

    @property
    def from_cache(self) -> bool:
        return (
            self.inventory_from_cache
            and self.usage_from_cache
            and self.billing_from_cache
        )
