"""Batch close (design §11)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import psycopg

from . import db
from .audit import EventLogger
from .common import (COMPLETED, COMPLETED_WITH_EXCEPTION, DATA_NOT_PROVIDED, EXCEPTION_PENDING, Clock,
                     CloseBlocked, CloseDeferred, check_transition, earliest_close_date, code)
from .settings import Settings

HAS_DATA = ("NEW_FILE", "CARRY_FORWARD")


def close_resolution(batch: dict) -> tuple[str, str]:
    """(Req_Stat, Resolution_Ty) a batch closes with."""
    if batch["req_stat"] == EXCEPTION_PENDING:
        return COMPLETED_WITH_EXCEPTION, batch["resolution_ty"] or "MISSING"
    if batch["resolution_ty"] in HAS_DATA:
        return COMPLETED, batch["resolution_ty"]
    return DATA_NOT_PROVIDED, "MISSING"


def closes_automatically(batch: dict) -> bool:
    return batch["resolution_ty"] in HAS_DATA or batch["req_stat"] == EXCEPTION_PENDING


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


class BatchCloser:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.logger = EventLogger(conn, clock)

    def close(self, btch_id: str, closed_by: str) -> CloseOutcome:
        """Close one open batch whose SLA hold has passed, whatever its data (CloseBlocked otherwise)."""
        b = self._batch(btch_id)
        if b is None:
            raise LookupError(f"batch {btch_id} not found")
        reason = ("batch is already closed" if b["batch_close_ind"] == 1
                  else f"SLA hold until {b['hold_dt']}" if self._today() < b["hold_dt"] else None)
        if reason:
            self._blocked(b, closed_by, reason)
        out = self._close_locked(b, closed_by, automatic=False)
        if out is None:
            self._blocked(b, closed_by, "batch is already closed")
        return out

    def run(self, project_cd: Optional[str] = None, table_nm: Optional[str] = None,
            run_ty: Optional[str] = None) -> CloseSummary:
        """Sweep the open batches in the job's scope whose hold has passed."""
        s = CloseSummary()
        for b in self._open_past_hold(project_cd, table_nm, run_ty):
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
            for b in self._open_past_hold(None, None, None, last_hold=self._today() - timedelta(days=1))]}

    def _today(self) -> date:
        return self.clock.today(self.settings.business_tz)

    def _batch(self, btch_id: str) -> Optional[dict]:
        b = self.conn.execute(
            """SELECT c.*, r.SLA_Days FROM ComplianceRequestControl c
                 LEFT JOIN ComplianceRunType r ON UPPER(r.Run_Ty) = c.Run_Ty WHERE c.Btch_ID = %s""", (code(btch_id),)).fetchone()
        if b:
            b["hold_dt"] = earliest_close_date(b["req_dt_key"], 1 if b["sla_days"] is None else b["sla_days"])
        return b

    def _open_past_hold(self, project_cd, table_nm, run_ty, last_hold: Optional[date] = None) -> list[dict]:
        """Open batches in scope whose hold ends on or before `last_hold` (default today)."""
        sql, params = _scope_sql(
            """SELECT c.* FROM ComplianceRequestControl c JOIN ComplianceRunType r ON UPPER(r.Run_Ty) = c.Run_Ty
                WHERE c.Batch_Close_Ind = 0 AND c.Req_Dt_Key + (r.SLA_Days - 1) <= %(last_hold)s""",
            "ORDER BY c.Req_ID", project_cd, table_nm, run_ty, last_hold=last_hold or self._today())
        return self.conn.execute(sql, params).fetchall()

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
                cur = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s FOR UPDATE",
                                        (b["req_id"],)).fetchone()
                if cur["batch_close_ind"] == 1 or (automatic and not closes_automatically(cur)):
                    return None
                to_stat, resolution = close_resolution(cur)
                check_transition(cur["req_stat"], to_stat)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Batch_Close_Ind=1, Req_Stat=%s, Resolution_Ty=%s,
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


def _scope_sql(sql: str, order_by: str, project_cd, table_nm, run_ty, **params) -> tuple[str, dict]:
    """Append the optional project / table / run type scope of a scheduled job."""
    for col, name, value in (("c.Project_Cd", "p", project_cd), ("c.Table_Nm", "t", table_nm), ("c.Run_Ty", "r", run_ty)):
        if value is not None:
            sql += f" AND {col} = %({name})s"
            params[name] = code(value)
    return f"{sql} {order_by}", params

