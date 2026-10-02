"""Shared primitives."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


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
    """A file failed a structural pre-check; carries the quarantine event code."""

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


class Clock:
    """Injected time source."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

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
        self._at = at.astimezone(timezone.utc)


def parse_as_of(value: str | None, business_tz: str = "UTC") -> Clock:
    """`--as-of` accepts ISO-8601 (date or timestamp)."""
    if not value:
        return Clock()
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(business_tz))
    return FixedClock(dt)


BTCH_ID_MAX_LEN = 250


def build_btch_id(req_dt: date, project_cd: str, table_nm: str, src_id: str, run_ty: str,
                  cmplnc_vrsn: str, seq: int) -> str:
    """{Req_Dt_Key:YYYYMMDD}_{Project}_{Table}_{Src}_{Run_Ty}_{Vrsn}_{Seq}."""
    if seq < 1:
        raise ValueError("seq must be >= 1")
    value = f"{req_dt:%Y%m%d}_{project_cd}_{table_nm}_{src_id}_{run_ty}_{cmplnc_vrsn}_{seq}"
    if len(value) > BTCH_ID_MAX_LEN:
        raise ValueError(f"Btch_ID exceeds {BTCH_ID_MAX_LEN} characters: {value}")
    return value


def earliest_close_date(req_dt: date, sla_days: int) -> date:
    """Earliest close date: Req_Dt_Key + SLA_Days - 1."""
    if sla_days < 1:
        raise ValueError("SLA_Days must be >= 1")
    return req_dt + timedelta(days=sla_days - 1)


PENDING = "PENDING"
PROMOTED = "PROMOTED"
CARRIED_FORWARD = "CARRIED_FORWARD"
EXCEPTION_PENDING = "EXCEPTION_PENDING"
COMPLETED = "COMPLETED"
COMPLETED_WITH_EXCEPTION = "COMPLETED_WITH_EXCEPTION"
DATA_NOT_PROVIDED = "DATA_NOT_PROVIDED"

OPEN_STATUSES = (PENDING, PROMOTED, CARRIED_FORWARD, EXCEPTION_PENDING)

TRANSITIONS: dict[str, set[str]] = {
    PENDING: {PROMOTED, EXCEPTION_PENDING, CARRIED_FORWARD, DATA_NOT_PROVIDED},
    PROMOTED: {EXCEPTION_PENDING, COMPLETED},
    CARRIED_FORWARD: {PROMOTED, EXCEPTION_PENDING, PENDING, COMPLETED},
    EXCEPTION_PENDING: {PROMOTED, CARRIED_FORWARD, PENDING, COMPLETED_WITH_EXCEPTION},
    COMPLETED: {COMPLETED},
    COMPLETED_WITH_EXCEPTION: {COMPLETED},
    DATA_NOT_PROVIDED: {COMPLETED},
}


def check_transition(from_stat: str, to_stat: str) -> None:
    if from_stat != to_stat and to_stat not in TRANSITIONS.get(from_stat, ()):
        raise InvalidStatusTransition(f"Req_Stat {from_stat} -> {to_stat} is not an allowed transition")
