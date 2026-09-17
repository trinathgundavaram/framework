"""Injected time source. Services never call datetime.now() / date.today() directly."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today(self, tz: str) -> date:
        return self.now().astimezone(ZoneInfo(tz)).date()


class FixedClock(Clock):
    """Clock pinned to a moment (the CLI --as-of argument, tests)."""

    def __init__(self, at: datetime):
        if at.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._at = at.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("timezone-aware datetime required")
        self._at = at.astimezone(timezone.utc)


def parse_as_of(value: str | None) -> Clock:
    """`--as-of` accepts ISO-8601; naive values are treated as UTC."""
    if not value:
        return Clock()
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))  # py3.10 does not accept "Z"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return FixedClock(dt)
