"""Batch close (design §11)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from . import db
from .audit import EventLogger
from .batches import REOPENED_AWAITING_DATA
from .common import (COMPLETED_WITH_EXCEPTION, DATA_NOT_PROVIDED, EXCEPTION_PENDING, REQUEST_COMPLETE, Clock,
                     CloseBlocked, CloseDeferred, check_transition, earliest_close_date, code)
from .db import Connection
from .release import released
from .settings import Settings
from .scope import Scope

HAS_DATA = ("NEW_FILE", "CARRY_FORWARD")
DELIVERED = (*HAS_DATA, "NO_DATA")


def close_resolution(batch: dict) -> tuple[str, str]:
    """(Req_Stat, Resolution_Ty) a batch closes with."""
    if batch["req_stat"] == EXCEPTION_PENDING:
        return COMPLETED_WITH_EXCEPTION, batch["resolution_ty"] or "MISSING"
    if batch["resolution_ty"] in HAS_DATA:
        return REQUEST_COMPLETE, batch["resolution_ty"]
    if batch["resolution_ty"] == "NO_DATA":
        return DATA_NOT_PROVIDED, "NO_DATA"
    return DATA_NOT_PROVIDED, "MISSING"


def closes_automatically(batch: dict) -> bool:
    return batch["resolution_ty"] in DELIVERED or batch["req_stat"] == EXCEPTION_PENDING


@dataclass
class CloseOutcome:
    btch_id: str
    req_stat: str
    resolution_ty: str


@dataclass
class CloseSummary:
    evaluated: int = 0
    closed: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    on_hold: list[str] = field(default_factory=list)


class BatchCloser:
    def __init__(self, conn: Connection, clock: Clock, settings: Settings):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.logger = EventLogger(conn, clock)

    def close(self, btch_id: str, closed_by: str) -> CloseOutcome:
        """Close one open batch whose SLA hold has passed, whatever its data (CloseBlocked otherwise)."""
        b = self._batch(btch_id)
        if b is None:
            raise LookupError(f"batch {btch_id} not found")
        reason = ("batch is already closed" if b["batch_close_ind"] == 1
                  else f"SLA hold until {b['hold_dt']}" if self._today() < b["hold_dt"] and not self._released(b)
                  else None)
        if reason:
            self._blocked(b, closed_by, reason)
        out = self._close_locked(b, closed_by, automatic=False)
        if out is None:
            self._blocked(b, closed_by, "batch is already closed")
        return out

    def run(self, scope: Optional[Scope] = None, hold: tuple = ()) -> CloseSummary:
        """Sweep the open batches of a scope (a project, a table group or table, a run type) whose hold has passed, and
        the batches with data released early (BATCH_RELEASED). Batches of the run types in `hold` stay open unless
        released."""
        s = CloseSummary()
        rows = self._open_past_hold(scope or Scope(), sweep=True)
        done = released(self.conn, [b["req_id"] for b in rows if b["run_ty"] in hold]) if hold else set()
        for b in rows:
            if b["run_ty"] in hold and b["req_id"] not in done:
                s.on_hold.append(b["btch_id"])
                continue
            s.evaluated += 1
            if not closes_automatically(b):
                s.waiting.append(b["btch_id"])
                continue
            try:
                out = self._close_locked(b, "SYSTEM", automatic=True)
            except CloseDeferred:
                s.deferred.append(b["btch_id"])
                continue
            (s.closed if out else s.waiting).append(b["btch_id"])
        return s

    def health(self) -> dict[str, list[dict]]:
        """Open batches past their SLA hold (design §15.3)."""
        return {"batches_past_hold_not_closed": [
            {k: b[k] for k in ("req_id", "btch_id", "project_cd", "table_nm", "src_id", "run_ty", "req_dt_key",
                               "req_stat", "resolution_ty")}
            for b in self._open_past_hold(Scope(), last_hold=self._today() - timedelta(days=1))]}

    def _today(self) -> date:
        return self.clock.today(self.settings.business_tz)

    def _batch(self, btch_id: str) -> Optional[dict]:
        b = self.conn.execute(
            """SELECT c.*, r.SLA_Days FROM ComplianceRequestControl c
                 LEFT JOIN ComplianceRunType r ON UPPER(r.Run_Ty) = c.Run_Ty WHERE c.Btch_ID = %s""", (code(btch_id),)).fetchone()
        if b:
            b["hold_dt"] = earliest_close_date(db.as_date(b["req_dt_key"]), 1 if b["sla_days"] is None else b["sla_days"])
        return b

    def _released(self, b: dict) -> bool:
        return b["resolution_ty"] is not None and self.conn.execute(
            "SELECT 1 AS ok FROM ComplianceRequestFileDetail WHERE Req_ID = %s AND Event_Ty = 'BATCH_RELEASED'",
            (b["req_id"],)).fetchone() is not None

    def _open_past_hold(self, scope: Scope, last_hold: Optional[date] = None, sweep: bool = False) -> list[dict]:
        """Open batches in scope whose hold ends on or before `last_hold` (default today); with `sweep`, also the
        batches with data released early, and not the reopened batches still waiting for new data."""
        where, params = scope.sql("c")
        if sweep:
            where = f" AND NOT {REOPENED_AWAITING_DATA}{where}"
        early = ("""OR (c.Resolution_Ty IS NOT NULL AND EXISTS (SELECT 1 FROM ComplianceRequestFileDetail d
                          WHERE d.Req_ID = c.Req_ID AND d.Event_Ty = 'BATCH_RELEASED'))""" if sweep else "")
        return self.conn.execute(
            f"""SELECT c.* FROM ComplianceRequestControl c JOIN ComplianceRunType r ON UPPER(r.Run_Ty) = c.Run_Ty
                 WHERE c.Batch_Close_Ind = 0 AND (c.Req_Dt_Key + (r.SLA_Days - 1) <= %s {early}){where} ORDER BY c.Req_ID""",
            [last_hold or self._today(), *params]).fetchall()

    def _blocked(self, b: dict, closed_by: str, reason: str) -> None:
        with self.conn.transaction():
            self.logger.audit("BATCH_CLOSE_BLOCKED", actor=closed_by, description=reason, **_ctx(b))
        raise CloseBlocked(reason)

    def _close_locked(self, b: dict, closed_by: str, automatic: bool) -> Optional[CloseOutcome]:
        """Close under the batch lock; None if there is nothing to close any more."""
        key = db.batch_key(b["btch_id"])
        if not db.try_lock(self.conn, key):
            with self.conn.transaction():
                self.logger.audit("BATCH_CLOSE_DEFERRED_LOCKED", description="batch busy; close deferred", **_ctx(b))
            raise CloseDeferred(f"batch {b['btch_id']} is locked")
        try:
            with self.conn.transaction():
                cur = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s",
                                        (b["req_id"],)).fetchone()
                if cur["batch_close_ind"] == 1 or (automatic and not closes_automatically(cur)):
                    return None
                to_stat, resolution = close_resolution(cur)
                check_transition(cur["req_stat"], to_stat)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Batch_Close_Ind = 1, Req_Stat=%s, Resolution_Ty=%s,
                              Updated_Dtts=%s WHERE Req_ID=%s""", (to_stat, resolution, self.clock.now(), cur["req_id"]))
                self.logger.batch_event("BATCH_CLOSED", req_id=cur["req_id"], btch_id=cur["btch_id"], actor=closed_by,
                                        detail=f"{to_stat} / {resolution}")
                if resolution == "MISSING":
                    self.logger.audit("SOURCE_MISSING_AT_CLOSE", actor=closed_by, **_ctx(cur))
                return CloseOutcome(cur["btch_id"], to_stat, resolution)
        finally:
            db.unlock(self.conn, key)


def _ctx(b: dict) -> dict:
    return {k: b[k] for k in ("req_id", "btch_id", "project_cd", "table_nm", "src_id", "run_ty")}
