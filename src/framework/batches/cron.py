"""Cron expansion in the business timezone (DST-aware)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter


def is_valid(expr: str) -> bool:
    return croniter.is_valid(expr)


def fire_times(expr: str, tz: str, start_exclusive: datetime, end_inclusive: datetime) -> list[datetime]:
    """All fire times t with start < t <= end, as aware datetimes in `tz`."""
    zone = ZoneInfo(tz)
    start = start_exclusive.astimezone(zone)
    end = end_inclusive.astimezone(zone)
    it = croniter(expr, start)
    out: list[datetime] = []
    while True:
        t = it.get_next(datetime)
        if t > end:
            break
        if t > start:
            out.append(t)
        if len(out) > 100_000:
            raise ValueError("cron expansion window too large")
    return out
