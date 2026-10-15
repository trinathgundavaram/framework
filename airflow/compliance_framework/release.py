"""When a batch's data may be pulled from an internal source, exported and closed.

Scheduled run types go from the batch's due date (its close date, Req_Dt_Key + SLA_Days - 1; on that day from
RELEASE_TIME on, if set); ad-hoc run types only once released. The `hold` input of a step (run
types, comma separated, in its Variable or trigger) holds those run types' batches for as long as it is given,
whatever their mode. A run with `release` releases the held batches it is given (BATCH_RELEASED): from then on
they go, hold or not, BATCH_CLOSE included.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from . import config as cfgmod
from . import db
from .audit import EventLogger
from .common import SCHEDULED, ConfigError, code, earliest_close_date

RELEASED = "BATCH_RELEASED"
TRUE = ("TRUE", "YES", "Y", "1")


def wanted(value) -> bool:
    return str(value or "").strip().upper() in TRUE


def run_type_list(value) -> tuple:
    """The run types of a `hold` input ('WEEKLY, MONTHLY' or a list)."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").strip("[] ").split(",")
    return tuple(code(str(v).strip(" '\"")) for v in items if str(v).strip(" '\""))


def release_time(value: Optional[str]) -> Optional[str]:
    """RELEASE_TIME as HH:MM (None = from the start of the due date)."""
    if not value:
        return None
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(value).strip())
    if not m:
        raise ConfigError(f"RELEASE_TIME must be HH:MM (24 hours), got {value!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def released(conn, req_ids: Iterable[int]) -> set:
    """The batches with a BATCH_RELEASED event."""
    ids, out = sorted(set(req_ids)), set()
    for i in range(0, len(ids), 500):
        marks, chunk = db.in_list(ids[i:i + 500])
        out.update(r["req_id"] for r in conn.execute(
            f"SELECT DISTINCT Req_ID FROM ComplianceRequestFileDetail WHERE Event_Ty = %s AND Req_ID IN ({marks})",
            [RELEASED, *chunk]).fetchall())
    return out


class Releases:
    def __init__(self, conn, clock, settings, hold: Iterable[str] = ()):
        self.conn, self.clock = conn, clock
        self.today = clock.today(settings.business_tz)
        self.now_hhmm = clock.now().astimezone(ZoneInfo(settings.business_tz)).strftime("%H:%M")
        self.release_time = release_time(settings.release_time)
        self.run_types = cfgmod.run_types(conn)
        self.on_hold = set(hold)
        self.logger = EventLogger(conn, clock)

    def hold(self, b: dict, done: set) -> Optional[str]:
        """None when the batch may go now, else until when it waits."""
        if b["req_id"] in done:
            return None
        if b["run_ty"] in self.on_hold:
            return f"on hold: run type {b['run_ty']} is held by this run's hold input"
        rt = self.run_types.get(b["run_ty"])
        if rt is None:
            return None
        if rt.run_category_cd != SCHEDULED:
            return f"held until released (run type {b['run_ty']} is ad hoc)"
        due = earliest_close_date(db.as_date(b["req_dt_key"]), rt.sla_days)
        if due < self.today or (due == self.today and (self.release_time is None or self.now_hhmm >= self.release_time)):
            return None
        return f"held until {due}{f' {self.release_time}' if self.release_time else ''} (its due date)"

    def split(self, batches: list[dict]) -> tuple[list[dict], list[dict]]:
        """(batches that may go now, held batches with a `hold` note)."""
        done = released(self.conn, (b["req_id"] for b in batches))
        ready, held = [], []
        for b in batches:
            why = self.hold(b, done)
            if why is None:
                ready.append(b)
            else:
                held.append({**b, "hold": why})
        return ready, held

    def release(self, batches: list[dict], step: str) -> None:
        """Record the release of held batches."""
        with self.conn.transaction():
            for b in batches:
                self.logger.batch_event(RELEASED, req_id=b["req_id"], btch_id=b["btch_id"],
                                        detail=f"released by {step} ({b['hold']})")
