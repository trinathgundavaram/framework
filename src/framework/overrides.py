"""Manual overrides on ComplianceBatchOverride (design §7 P7, D-12, D-70, D-74).

People create and approve rows with the templates in sql/approvals.sql. One shape covers three cases:

  REUSE         an open batch without data reuses an earlier batch's data (run type Carry_Fwd_Ind = 1).
                `process-decisions` applies it to the batch and removes it again when it expires.
  LATE_ARRIVAL  a file may still be promoted into a closed batch that has no data.
  CORRECTION    a corrected file may replace the data of a closed batch.
                Those two are read by the ingest pipeline; this job only validates and audits them.

An approval is valid through Valid_Thru_Dt_Key. There is no revoke: setting that date in the past
stops the override, and the next run of this job removes an applied REUSE.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import psycopg

from . import config as cfgmod
from .audit import EventLogger
from .batches import get_batch, promoted_load
from .common import CARRIED_FORWARD, OPEN_STATUSES, PENDING, Clock, check_transition
from .settings import Settings

log = logging.getLogger(__name__)

REUSE, LATE_ARRIVAL, CORRECTION = "REUSE", "LATE_ARRIVAL", "CORRECTION"


@dataclass
class DecisionSummary:
    applied: list[int] = field(default_factory=list)       # REUSE overrides applied to their batch
    expired: list[int] = field(default_factory=list)       # applied REUSE overrides that ran out
    invalid: list[int] = field(default_factory=list)
    extracts_to_refresh: set[int] = field(default_factory=set)


class DecisionProcessor:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings,
                 refresh_extract: Optional[Callable[[int], None]] = None):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.refresh_extract = refresh_extract
        self.logger = EventLogger(conn, clock)

    def run(self) -> DecisionSummary:
        s = DecisionSummary()
        today = self.clock.today(self.settings.business_tz)
        self._expire(s, today)
        self._apply(s, today)
        for ext in sorted(s.extracts_to_refresh):
            if self.refresh_extract:
                self.refresh_extract(ext)
        return s

    # ------------------------------------------------------------------ REUSE: apply
    def _apply(self, s: DecisionSummary, today: date) -> None:
        """Approved, still-valid REUSE overrides whose batch is open and has no data yet."""
        rows = self.conn.execute(
            """SELECT o.Ovrd_ID FROM ComplianceBatchOverride o
                 JOIN ComplianceRequestControl c ON c.Req_ID = o.Req_ID
                WHERE o.Override_Ty = 'REUSE' AND o.Apprvl_Stat = 'APPROVED' AND o.Valid_Thru_Dt_Key >= %s
                  AND c.Batch_Close_Ind = 0 AND c.Resolution_Ty IS DISTINCT FROM 'CARRY_FORWARD'
                ORDER BY o.Ovrd_ID""", (today,)).fetchall()
        for r in rows:
            with self.conn.transaction():
                o = self.conn.execute("SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s FOR UPDATE SKIP LOCKED",
                                      (r["ovrd_id"],)).fetchone()
                if o is None:
                    continue
                b = get_batch(self.conn, o["req_id"], for_update=True)
                ctx = dict(ovrd_id=o["ovrd_id"], req_id=b["req_id"], extract_id=b["extract_id"], btch_id=b["btch_id"],
                           project_cd=b["project_cd"], table_nm=b["table_nm"], src_cd=b["src_cd"], run_ty=b["run_ty"])
                problem = self._reuse_problem(o, b)
                if problem:
                    self.logger.audit("OVERRIDE_INVALID_DETECTED", actor=o["apprvd_by"] or o["requested_by"],
                                      description=f"REUSE: {problem}", **ctx)
                    s.invalid.append(o["ovrd_id"])
                    continue
                src = self._reuse_source(o, b)
                check_transition(b["req_stat"], CARRIED_FORWARD)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Resolution_Ty='CARRY_FORWARD', Reuse_Btch_ID=%s,
                              Req_Stat=%s, Updated_Dtts=%s WHERE Req_ID=%s""",
                    (src["btch_id"], CARRIED_FORWARD, self.clock.now(), b["req_id"]))
                if o["reuse_btch_id"] != src["btch_id"]:
                    self.conn.execute("UPDATE ComplianceBatchOverride SET Reuse_Btch_ID=%s, Updated_Dtts=%s "
                                      "WHERE Ovrd_ID=%s", (src["btch_id"], self.clock.now(), o["ovrd_id"]))
                self.logger.audit("OVERRIDE_APPROVED", actor=o["apprvd_by"],
                                  description=f"REUSE of {src['btch_id']} valid through {o['valid_thru_dt_key']}", **ctx)
                self.logger.batch_event("CARRY_FORWARD_APPLIED", req_id=b["req_id"], btch_id=b["btch_id"],
                                        ovrd_id=o["ovrd_id"], actor=o["apprvd_by"] or "SYSTEM", entry_ty="MANUAL",
                                        detail=f"reuses {src['btch_id']} through {o['valid_thru_dt_key']}")
                s.applied.append(o["ovrd_id"])
                s.extracts_to_refresh.add(b["extract_id"])

    # ------------------------------------------------------------------ REUSE: expire
    def _expire(self, s: DecisionSummary, today: date) -> None:
        """A batch still carrying data whose override has run out (or was rejected) goes back to PENDING."""
        rows = self.conn.execute(
            """SELECT c.Req_ID, o.Ovrd_ID FROM ComplianceRequestControl c
                 LEFT JOIN ComplianceBatchOverride o
                        ON o.Req_ID = c.Req_ID AND o.Override_Ty = 'REUSE' AND o.Apprvl_Stat = 'APPROVED'
                WHERE c.Resolution_Ty = 'CARRY_FORWARD' AND c.Batch_Close_Ind = 0
                  AND (o.Ovrd_ID IS NULL OR o.Valid_Thru_Dt_Key < %s)
                ORDER BY c.Req_ID""", (today,)).fetchall()
        for r in rows:
            with self.conn.transaction():
                b = get_batch(self.conn, r["req_id"], for_update=True)
                if (b["resolution_ty"] != "CARRY_FORWARD" or b["batch_close_ind"] == 1
                        or b["req_stat"] not in OPEN_STATUSES):
                    continue
                to_stat = PENDING if b["req_stat"] == CARRIED_FORWARD else b["req_stat"]
                check_transition(b["req_stat"], to_stat)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Resolution_Ty=NULL, Reuse_Btch_ID=NULL, Req_Stat=%s,
                              Updated_Dtts=%s WHERE Req_ID=%s""", (to_stat, self.clock.now(), b["req_id"]))
                self.logger.audit("OVERRIDE_EXPIRED", ovrd_id=r["ovrd_id"], req_id=b["req_id"], btch_id=b["btch_id"],
                                  extract_id=b["extract_id"], project_cd=b["project_cd"], table_nm=b["table_nm"],
                                  src_cd=b["src_cd"], run_ty=b["run_ty"],
                                  description=f"REUSE of {b['reuse_btch_id']} is no longer valid")
                self.logger.batch_event("CARRY_FORWARD_REMOVED", req_id=b["req_id"], btch_id=b["btch_id"],
                                        ovrd_id=r["ovrd_id"], detail=f"reuse of {b['reuse_btch_id']} expired")
                s.expired.append(r["ovrd_id"] or b["req_id"])
                s.extracts_to_refresh.add(b["extract_id"])

    # ------------------------------------------------------------------ validation
    def _reuse_problem(self, o: dict, b: dict) -> Optional[str]:
        rt = cfgmod.run_type(self.conn, b["run_ty"])
        if rt is None or not rt.carry_fwd:
            return f"run type {b['run_ty']} does not allow carry-forward (Carry_Fwd_Ind = 0)"
        if b["batch_close_ind"] == 1:
            return "batch is closed"
        if promoted_load(self.conn, b["btch_id"]) is not None:
            return "batch already has promoted data"
        if self._reuse_source(o, b) is None:
            return ("no earlier closed batch with data"
                    + (f" matches Reuse_Btch_ID {o['reuse_btch_id']}" if o["reuse_btch_id"] else ""))
        return None

    def _reuse_source(self, o: dict, b: dict) -> Optional[dict]:
        """The batch whose data is reused: the requested one, else the latest earlier closed batch of the
        same (project, table, source, run type) that has data. A carried batch resolves to its source."""
        row = self.conn.execute(
            """SELECT * FROM ComplianceRequestControl
                WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s AND Run_Ty=%s AND Req_ID <> %s
                  AND Batch_Close_Ind = 1 AND Resolution_Ty IN ('NEW_FILE','CARRY_FORWARD')
                  AND (Rpt_Start_Dt_Key, Req_Dt_Key) <= (%s, %s)
                  AND (%s::text IS NULL OR Btch_ID = %s)
                ORDER BY Rpt_Start_Dt_Key DESC, Req_Dt_Key DESC, Req_ID DESC LIMIT 1""",
            (b["project_cd"], b["table_nm"], b["src_cd"], b["run_ty"], b["req_id"], b["rpt_start_dt_key"],
             b["req_dt_key"], o["reuse_btch_id"], o["reuse_btch_id"])).fetchone()
        if row and row["resolution_ty"] == "CARRY_FORWARD":
            row = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Btch_ID=%s",
                                    (row["reuse_btch_id"],)).fetchone()
        return row if row and promoted_load(self.conn, row["btch_id"]) else None
