"""Date/time ranges used by the dashboard, the DevOps page and both syncs.

A range is half-open, ``[start, end)``, in naive UTC (the database stores naive
UTC datetimes). The API accepts ISO dates (``2026-10-01``) or date-times
(``2026-10-01T09:30``, ``2026-10-01T09:30:00Z``, ``...+05:30``): a date-only
``date_from`` starts at midnight and a date-only ``date_to`` covers that whole
day. Date-times without an offset are taken as UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

MAX_SPAN = timedelta(days=3650)


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def parse_bound(text: str, *, is_end: bool = False) -> datetime:
    """Parse an API date or date-time into naive UTC; raises ``ValueError`` if invalid."""
    text = text.strip()
    if len(text) == 10:
        day = date.fromisoformat(text)
        return datetime.combine(day + timedelta(days=1) if is_end else day, time.min)
    return to_naive_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))


@dataclass(frozen=True)
class TimeWindow:
    start: datetime  # inclusive, naive UTC
    end: datetime  # exclusive, naive UTC

    @classmethod
    def last_days(cls, days: float, now: datetime | None = None) -> "TimeWindow":
        end = now or utc_now_naive()
        return cls(end - timedelta(days=days), end)

    @classmethod
    def parse(
        cls,
        date_from: str | None,
        date_to: str | None,
        default_days: float = 30,
    ) -> "TimeWindow":
        """Build a window from API parameters (``ValueError`` on bad input)."""
        end = parse_bound(date_to, is_end=True) if date_to else utc_now_naive()
        start = parse_bound(date_from) if date_from else end - timedelta(days=default_days)
        if start >= end:
            raise ValueError("date_from must be before date_to")
        if end - start > MAX_SPAN:
            raise ValueError("the date range may span at most 10 years")
        return cls(start, end)

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400

    @property
    def weeks(self) -> float:
        return max(self.days / 7, 1)

    def contains(self, value: datetime | None) -> bool:
        return value is not None and self.start <= value < self.end

    def describe(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat() + "Z",
            "end": self.end.isoformat() + "Z",
            "days": round(self.days, 2),
        }
