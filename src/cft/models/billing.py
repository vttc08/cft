from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BillingSnapshot:
    profile_name: str
    configured: bool
    download_bytes: int | None = None
    upload_bytes: int | None = None
    requests: int | None = None
    cost: float | None = None
    last_updated: datetime | None = None
    data_start: datetime | None = None
    data_end: datetime | None = None
    from_cache: bool = False
    message: str | None = None
