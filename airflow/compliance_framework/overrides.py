"""Manual overrides on ComplianceBatchOverride (design §7 P7, D-12, D-70, D-74)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial
from typing import Optional

from . import config as cfgmod
from . import db
from .audit import EventLogger
from .batches import get_batch, promoted_load
from .common import CARRIED_FORWARD, OPEN_STATUSES, PENDING, Clock, check_transition, code
from .db import Connection
from .settings import Settings


@dataclass
class DecisionSummary:
    applied: list[int] = field(default_factory=list)
    expired: list[int] = field(default_factory=list)
    invalid: list[int] = field(default_factory=list)


class DecisionProcessor:
    def __init__(self, conn: Connection, clock: Clock, settings: Settings):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.logger = EventLogger(conn, clock)

    def run(self, project_cd: Optional[str] = None) -> DecisionSummary:
        """Expire, then apply, REUSE overrides - of one project, or of every project when project_cd is None."""
        s = DecisionSummary()
        project_cd = code(project_cd)
        today = self.clock.today(self.settings.business_tz)
        self._expire(s, today, project_cd)
        self._apply(s, today, project_cd)
        return s

    def _apply(self, s: DecisionSummary, today: date, project_cd: Optional[str]) -> None:
        """Approved, still-valid REUSE overrides whose batch is open and has no data yet."""
        where, params = "", [today]
        if project_cd is not None:
            where, params = " AND c.Project_Cd = %s", [today, project_cd]
        rows = self.conn.execute(
            f"""SELECT o.Ovrd_ID, c.Btch_ID FROM ComplianceBatchOverride o
                 JOIN ComplianceRequestControl c ON c.Req_ID = o.Req_ID
                WHERE UPPER(o.Override_Ty) = 'REUSE' AND UPPER(o.Apprvl_Stat) = 'APPROVED' AND o.Valid_Thru_Dt_Key >= %s
                  AND c.Batch_Close_Ind = 0 AND (c.Resolution_Ty IS NULL OR c.Resolution_Ty <> 'CARRY_FORWARD'){where}
                ORDER BY o.Ovrd_ID""", params).fetchall()
        run_types = cfgmod.run_types(self.conn) if rows else {}
        for r in rows:
            key = db.batch_key(r["btch_id"])
            if not db.try_lock(self.conn, key):
                continue
            try:
                with self.conn.transaction():
                    self._apply_one(s, r["ovrd_id"], today, run_types)
            finally:
                db.unlock(self.conn, key)

    def _apply_one(self, s: DecisionSummary, ovrd_id: int, today: date, run_types: dict) -> None:
        o = self.conn.execute("SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", (ovrd_id,)).fetchone()
        valid_thru = db.as_date(o["valid_thru_dt_key"]) if o else None
        if (o is None or code(o["override_ty"]) != "REUSE" or code(o["apprvl_stat"]) != "APPROVED"
                or valid_thru is None or valid_thru < today):
            return
        b = get_batch(self.conn, o["req_id"])
        if b["resolution_ty"] == "CARRY_FORWARD":
            return
        ctx = dict(ovrd_id=o["ovrd_id"], req_id=b["req_id"], btch_id=b["btch_id"],
                   project_cd=b["project_cd"], table_nm=b["table_nm"], src_id=b["src_id"], run_ty=b["run_ty"])
        src = self._reuse_source(o, b)
        problem = self._reuse_problem(o, b, src, run_types.get(b["run_ty"]))
        if problem:
            if not self.conn.execute(
                    "SELECT 1 AS ok FROM CMS_ComplianceExceptionsAudit WHERE Ovrd_ID=%s "
                    "AND Event_Ty='OVERRIDE_INVALID_DETECTED'", (o["ovrd_id"],)).fetchone():
                self.logger.audit("OVERRIDE_INVALID_DETECTED", actor=o["reviewed_by"] or o["created_by"],
                                  description=f"REUSE: {problem}", **ctx)
            s.invalid.append(o["ovrd_id"])
            return
        check_transition(b["req_stat"], CARRIED_FORWARD)
        self.conn.execute(
            """UPDATE ComplianceRequestControl SET Resolution_Ty='CARRY_FORWARD', Reuse_Btch_ID=%s,
                      Req_Stat=%s, Updated_Dtts=%s WHERE Req_ID=%s""",
            (src["btch_id"], CARRIED_FORWARD, self.clock.now(), b["req_id"]))
        if code(o["reuse_btch_id"]) != src["btch_id"]:
            self.conn.execute("UPDATE ComplianceBatchOverride SET Reuse_Btch_ID=%s, Updated_Dtts=%s "
                              "WHERE Ovrd_ID=%s", (src["btch_id"], self.clock.now(), o["ovrd_id"]))
        self.logger.audit("OVERRIDE_APPROVED", actor=o["reviewed_by"],
                          description=f"REUSE of {src['btch_id']} valid through {valid_thru}", **ctx)
        self.logger.batch_event("CARRY_FORWARD_APPLIED", req_id=b["req_id"], btch_id=b["btch_id"],
                                ovrd_id=o["ovrd_id"], actor=o["reviewed_by"] or "SYSTEM",
                                detail=f"reuses {src['btch_id']} through {valid_thru}")
        s.applied.append(o["ovrd_id"])

    def _expire(self, s: DecisionSummary, today: date, project_cd: Optional[str]) -> None:
        """A batch still carrying data whose override has run out (or was rejected) goes back to PENDING."""
        where, params = "", [today]
        if project_cd is not None:
            where, params = " AND c.Project_Cd = %s", [today, project_cd]
        rows = self.conn.execute(
            f"""SELECT c.Req_ID, c.Btch_ID, o.Ovrd_ID FROM ComplianceRequestControl c
                 LEFT JOIN ComplianceBatchOverride o
                        ON o.Req_ID = c.Req_ID AND UPPER(o.Override_Ty) = 'REUSE' AND UPPER(o.Apprvl_Stat) = 'APPROVED'
                WHERE c.Resolution_Ty = 'CARRY_FORWARD' AND c.Batch_Close_Ind = 0
                  AND (o.Ovrd_ID IS NULL OR o.Valid_Thru_Dt_Key < %s){where}
                ORDER BY c.Req_ID""", params).fetchall()
        for r in rows:
            key = db.batch_key(r["btch_id"])
            if not db.try_lock(self.conn, key):
                continue
            try:
                with self.conn.transaction():
                    self._expire_one(s, r, today)
            finally:
                db.unlock(self.conn, key)

    def _expire_one(self, s: DecisionSummary, r: dict, today: date) -> None:
        b = get_batch(self.conn, r["req_id"])
        if (b["resolution_ty"] != "CARRY_FORWARD" or b["batch_close_ind"] == 1
                or b["req_stat"] not in OPEN_STATUSES):
            return
        still_valid = self.conn.execute(
            """SELECT 1 AS ok FROM ComplianceBatchOverride WHERE Req_ID=%s AND UPPER(Override_Ty)='REUSE'
                  AND UPPER(Apprvl_Stat)='APPROVED' AND Valid_Thru_Dt_Key >= %s""", (b["req_id"], today)).fetchone()
        if still_valid:
            return
        to_stat = PENDING if b["req_stat"] == CARRIED_FORWARD else b["req_stat"]
        check_transition(b["req_stat"], to_stat)
        self.conn.execute(
            """UPDATE ComplianceRequestControl SET Resolution_Ty=NULL, Reuse_Btch_ID=NULL, Req_Stat=%s,
                      Updated_Dtts=%s WHERE Req_ID=%s""", (to_stat, self.clock.now(), b["req_id"]))
        self.logger.audit("OVERRIDE_EXPIRED", ovrd_id=r["ovrd_id"], req_id=b["req_id"], btch_id=b["btch_id"],
                          project_cd=b["project_cd"], table_nm=b["table_nm"],
                          src_id=b["src_id"], run_ty=b["run_ty"],
                          description=f"REUSE of {b['reuse_btch_id']} is no longer valid")
        self.logger.batch_event("CARRY_FORWARD_REMOVED", req_id=b["req_id"], btch_id=b["btch_id"],
                                ovrd_id=r["ovrd_id"], detail=f"reuse of {b['reuse_btch_id']} expired")
        s.expired.append(r["ovrd_id"] or b["req_id"])

    def health(self) -> dict[str, list[dict]]:
        """Pending reviews and approved overrides expiring within a week."""
        q = partial(db.fetch_all, self.conn)
        today = self.clock.today(self.settings.business_tz)
        return {
            "pending_reviews": q("""SELECT Ovrd_ID, Override_Ty, Req_ID, Btch_ID, Created_Dtts
                                     FROM ComplianceBatchOverride WHERE UPPER(Apprvl_Stat)='PENDING_REVIEW'
                                     ORDER BY Ovrd_ID"""),
            "overrides_expiring_soon": q(
                """SELECT Ovrd_ID, Override_Ty, Btch_ID, Valid_Thru_Dt_Key FROM ComplianceBatchOverride
                    WHERE UPPER(Apprvl_Stat)='APPROVED' AND Valid_Thru_Dt_Key BETWEEN %s AND %s
                    ORDER BY Valid_Thru_Dt_Key""", today, today + timedelta(days=7)),
        }

    def _reuse_problem(self, o: dict, b: dict, src: Optional[dict], rt: Optional[cfgmod.RunType]) -> Optional[str]:
        if rt is None or not rt.carry_fwd:
            return f"run type {b['run_ty']} does not allow carry-forward (Carry_Fwd_Ind = 0)"
        if b["batch_close_ind"] == 1:
            return "batch is closed"
        if promoted_load(self.conn, b["btch_id"]) is not None:
            return "batch already has promoted data"
        if src is None:
            return ("no earlier closed batch with data"
                    + (f" matches Reuse_Btch_ID {o['reuse_btch_id']}" if o["reuse_btch_id"] else ""))
        return None

    def _reuse_source(self, o: dict, b: dict) -> Optional[dict]:
        """The batch whose data is reused: the latest earlier closed batch with data (or the one named)."""
        where = ""
        params = [b["project_cd"], b["table_nm"], b["src_id"], b["run_ty"], b["req_id"],
                  b["rpt_start_dt_key"], b["rpt_start_dt_key"], b["req_dt_key"]]
        if o["reuse_btch_id"]:
            where, params = " AND Btch_ID = %s", params + [code(o["reuse_btch_id"])]
        rows = self.conn.execute(
            f"""SELECT * FROM ComplianceRequestControl
                WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s AND Run_Ty=%s AND Req_ID <> %s
                  AND Batch_Close_Ind = 1 AND Resolution_Ty IN ('NEW_FILE','CARRY_FORWARD')
                  AND (Rpt_Start_Dt_Key < %s OR (Rpt_Start_Dt_Key = %s AND Req_Dt_Key <= %s)){where}
                ORDER BY Rpt_Start_Dt_Key DESC, Req_Dt_Key DESC, Req_ID DESC""", params).fetchall()
        row = rows[0] if rows else None
        if row and row["resolution_ty"] == "CARRY_FORWARD":
            row = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Btch_ID=%s",
                                    (row["reuse_btch_id"],)).fetchone()
        return row if row and promoted_load(self.conn, row["btch_id"]) else None
