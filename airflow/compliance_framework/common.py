"""Shared primitives."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


def code(value):
    """Codes (project, table, source, run type, category, override type, status) are compared case-insensitively:
    the framework keeps them upper case."""
    return value.strip().upper() if isinstance(value, str) else value


class FrameworkError(Exception):
    """Base class."""


class ConfigError(FrameworkError):
    """Configuration is missing or invalid."""


class LockTimeout(FrameworkError):
    """An advisory lock could not be acquired in time (retry later)."""


class InvalidStatusTransition(FrameworkError):
    """A Req_Stat move not listed in TRANSITIONS."""


class TechnicalFailure(FrameworkError):
    """Retryable infrastructure failure (DB, rules engine, file store...)."""


class RuleEngineNotConfigured(TechnicalFailure):
    """The GRE entry point is not configured (open question Q-12)."""


class FileRejected(FrameworkError):
    """A file failed a structural pre-check; carries the reject event code."""

    def __init__(self, event_ty: str, message: str):
        super().__init__(message)
        self.event_ty = event_ty


class RowCountMismatch(FrameworkError):
    """Core swap appended a different number of rows than were staged."""


class CloseBlocked(FrameworkError):
    """The batch may not be closed (already closed, or its SLA hold has not passed)."""


class CloseDeferred(FrameworkError):
    """The batch is locked by another process; try again later."""


ENV_TOKEN_VALUES = {"DEV": "dev", "QA": "qa", "INT": "int", "UAT": "", "PROD": ""}
_ENV_TOKEN = re.compile(r"\$env", re.IGNORECASE)


def resolve_env(text: Optional[str], environment: str, value: Optional[str] = None, identifier: bool = True):
    """Replace $env / $ENV / $Env with the environment's value in the token's own casing (GRE convention)."""
    if not text or not _ENV_TOKEN.search(text):
        return text
    low = (value if value is not None else ENV_TOKEN_VALUES.get(environment.upper(), environment.lower())).lower()

    def sub(m):
        token = m.group(0)[1:]
        return low.upper() if token.isupper() else low if token.islower() else low.capitalize()

    out = _ENV_TOKEN.sub(sub, text)
    return re.sub(r"_{2,}", "_", out) if identifier else out




_ANNUAL_WINDOW = re.compile(r"^ANNUAL_WINDOW\(\s*(\d\d)-(\d\d)\s*,\s*(\d\d)-(\d\d)\s*(?:,\s*(\d\d)-(\d\d)\s*)?\)$",
                            re.IGNORECASE)


def annual_window(period: str, run_date: date) -> Optional[tuple[date, date]]:
    """ANNUAL_WINDOW(start MM-DD, end MM-DD[, from MM-DD]): the same dates every year, e.g. 12-01 to 01-31.

    The window used is the latest one whose `from` date has passed; `from` defaults to the day after the
    window ends, so (12-01,01-31) moves to the new December-January window every February 1.
    None when `period` is not an ANNUAL_WINDOW.
    """
    if not (period or "").strip().upper().startswith("ANNUAL_WINDOW"):
        return None
    m = _ANNUAL_WINDOW.match(period.strip())
    if not m:
        raise ConfigError(f"period {period!r} must be ANNUAL_WINDOW(MM-DD,MM-DD) or ANNUAL_WINDOW(MM-DD,MM-DD,MM-DD)")
    try:
        (sm, sd), (em, ed) = (int(m.group(1)), int(m.group(2))), (int(m.group(3)), int(m.group(4)))
        rollover = (int(m.group(5)), int(m.group(6))) if m.group(5) else None
        for month, day in [(sm, sd), (em, ed), *([rollover] if rollover else [])]:
            if (month, day) == (2, 29):
                raise ValueError("02-29 is not in every year")
            date(2001, month, day)
    except ValueError as e:
        raise ConfigError(f"period {period!r}: {e}") from None
    for year in range(run_date.year + 1, run_date.year - 3, -1):
        end = date(year, em, ed)
        start = date(year if (sm, sd) <= (em, ed) else year - 1, sm, sd)
        if rollover is None:
            becomes_current = end + timedelta(days=1)
        else:
            becomes_current = date(year, *rollover)
            if becomes_current <= end:
                becomes_current = date(year + 1, *rollover)
        if becomes_current <= run_date:
            return start, end
    raise ConfigError(f"period {period!r} has no window for {run_date}")


_LOOKBACK = re.compile(r"^(PREV_N_DAYS|PREV_WEEK_SAME_DAY)\s*\(\s*(\d+)\s*\)$", re.IGNORECASE)


def period_lookback(period: str) -> tuple:
    """PREV_N_DAYS(7) -> ('PREV_N_DAYS', 7, None); PREV_WEEK_SAME_DAY(2) -> (name, None, 2); else (period, None, None)."""
    m = _LOOKBACK.match((period or "").strip())
    if not m:
        return period, None, None
    name, n = m.group(1).upper(), int(m.group(2))
    return (name, n, None) if name == "PREV_N_DAYS" else (name, None, n)


BUSINESS_TZ = "America/Chicago"


class Clock:
    """Injected time source: the current time in the business time zone (Central), as written to the tables."""

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(BUSINESS_TZ))

    def today(self, tz: str) -> date:
        return self.now().astimezone(ZoneInfo(tz)).date()


class FixedClock(Clock):
    """Clock pinned to a moment (the CLI --as-of argument, tests)."""

    def __init__(self, at: datetime):
        self.set(at)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("timezone-aware datetime required")
        self._at = at.astimezone(ZoneInfo(BUSINESS_TZ))


def parse_as_of(value: str | None, business_tz: str = "UTC") -> Clock:
    """`--as-of` accepts ISO-8601 (date or timestamp)."""
    if not value:
        return Clock()
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(business_tz))
    return FixedClock(dt)


BTCH_ID_MAX_LEN = 250


def build_btch_id(req_dt: date, project_cd: str, table_nm: str, run_ty: str, seq: int = 1) -> str:
    """{Req_Dt_Key:YYYYMMDD}_{Project}_{Table}_{Run_Ty}, shared by every source of the table and run type; a second,
    third, ... report period on the same request date gets _2, _3, ..."""
    if seq < 1:
        raise ValueError("seq must be >= 1")
    value = "_".join([f"{req_dt:%Y%m%d}", *map(code, (project_cd, table_nm, run_ty))]) + (f"_{seq}" if seq > 1 else "")
    if len(value) > BTCH_ID_MAX_LEN:
        raise ValueError(f"Btch_ID exceeds {BTCH_ID_MAX_LEN} characters: {value}")
    return value


def earliest_close_date(req_dt: date, sla_days: int) -> date:
    """Earliest close date: Req_Dt_Key + SLA_Days - 1."""
    if sla_days < 1:
        raise ValueError("SLA_Days must be >= 1")
    return req_dt + timedelta(days=sla_days - 1)


SCHEDULED = "SCHEDULED"
ADHOC = "ADHOC"

REQUEST_CREATED = "REQUEST_CREATED"
SOURCE_DATA_READY = "SOURCE_DATA_READY"
SOURCE_DATA_READY_NO_DATA = "SOURCE_DATA_READY_NO_DATA"
SOURCE_DATA_CARRY_FORWARD = "SOURCE_DATA_CARRY_FORWARD"
REQUEST_COMPLETE = "REQUEST_COMPLETE"
DATA_NOT_PROVIDED = "DATA_NOT_PROVIDED"

OPEN_STATUSES = (REQUEST_CREATED, SOURCE_DATA_READY, SOURCE_DATA_READY_NO_DATA, SOURCE_DATA_CARRY_FORWARD)

TRANSITIONS: dict[str, set[str]] = {
    REQUEST_CREATED: {SOURCE_DATA_READY, SOURCE_DATA_READY_NO_DATA, SOURCE_DATA_CARRY_FORWARD, DATA_NOT_PROVIDED},
    SOURCE_DATA_READY: {SOURCE_DATA_READY_NO_DATA, REQUEST_COMPLETE},
    SOURCE_DATA_READY_NO_DATA: {SOURCE_DATA_READY, DATA_NOT_PROVIDED, SOURCE_DATA_CARRY_FORWARD},
    SOURCE_DATA_CARRY_FORWARD: {SOURCE_DATA_READY, SOURCE_DATA_READY_NO_DATA, REQUEST_CREATED, REQUEST_COMPLETE},
    REQUEST_COMPLETE: {REQUEST_COMPLETE, DATA_NOT_PROVIDED, SOURCE_DATA_READY, SOURCE_DATA_CARRY_FORWARD},
    DATA_NOT_PROVIDED: {REQUEST_COMPLETE, DATA_NOT_PROVIDED, REQUEST_CREATED, SOURCE_DATA_READY_NO_DATA},
}
REOPEN_STATUS = {"NEW_FILE": SOURCE_DATA_READY, "CARRY_FORWARD": SOURCE_DATA_CARRY_FORWARD, "NO_DATA": SOURCE_DATA_READY_NO_DATA}


def check_transition(from_stat: str, to_stat: str) -> None:
    if from_stat != to_stat and to_stat not in TRANSITIONS.get(from_stat, ()):
        raise InvalidStatusTransition(f"Req_Stat {from_stat} -> {to_stat} is not an allowed transition")
