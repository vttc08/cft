from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class CachePolicy:
    ttl: timedelta

    @classmethod
    def from_seconds(cls, seconds: int) -> CachePolicy:
        return cls(ttl=timedelta(seconds=max(1, seconds)))

    def is_fresh(self, last_updated: datetime | None, *, now: datetime | None = None) -> bool:
        if last_updated is None:
            return False
        current = now or utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return last_updated + self.ttl > current.astimezone(timezone.utc)

    def is_stale(self, last_updated: datetime | None, *, now: datetime | None = None) -> bool:
        return not self.is_fresh(last_updated, now=now)
