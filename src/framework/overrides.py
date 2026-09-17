"""Manual decisions on ComplianceBatchOverride (design §7 P7, D-12, D-48, D-53, D-70).

Analysts approve / reject / revoke with the SQL templates in sql/approvals.sql; `process-decisions`
detects the change (Apprvl_Stat <> Last_Processed_Apprvl_Stat) and acts on it:
  * reopens    -> promote the reviewed candidate file into core (re-staging from the archive if needed)
  * waivers    -> refresh the extract
  * carry-forward -> the open batch reuses the latest earlier batch's data (run type Carry_Fwd_Ind = 1)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import psycopg

from . import config as cfgmod
from . import db
from .adapters import ObjectStore
from .audit import EventLogger
from .batches import get_batch
from .common import (CARRIED_FORWARD, COMPLETED, OPEN_STATUSES, PENDING, Clock, TechnicalFailure,
                     check_transition)
from .load import restage, sanitize_db_error, staged_row_count, swap
from .settings import Settings

log = logging.getLogger(__name__)

REOPENS = ("LATE_ARRIVAL_REOPEN", "CORRECTION_REOPEN")
WAIVERS = ("SOURCE_WAIVER", "RULE_WAIVER")
REVOCABLE = WAIVERS + ("CARRY_FORWARD",)
LEGAL = {
    (None, "PENDING_REVIEW"), (None, "APPROVED"), (None, "REJECTED"),
    ("PENDING_REVIEW", "APPROVED"), ("PENDING_REVIEW", "REJECTED"),
    ("APPROVED", "REVOKED"),
}


@dataclass
class DecisionSummary:
    processed: int = 0
    invalid: list[int] = field(default_factory=list)
    promoted: list[int] = field(default_factory=list)
    promotion_failed: list[int] = field(default_factory=list)
    extracts_to_refresh: set[int] = field(default_factory=set)
    reopened_extracts: set[int] = field(default_factory=set)


class DecisionProcessor:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings, store: ObjectStore,
                 after_reopen: Optional[Callable[[int], None]] = None,
                 refresh_extract: Optional[Callable[[int], None]] = None):
        self.conn, self.clock, self.settings, self.store = conn, clock, settings, store
        self.after_reopen = after_reopen          # D-41 re-trigger hook
        self.refresh_extract = refresh_extract    # waiver / carry-forward decisions
        self.logger = EventLogger(self.conn, clock)

    def run(self) -> DecisionSummary:
        s = DecisionSummary()
        ids = [r["ovrd_id"] for r in self.conn.execute(
            """SELECT Ovrd_ID FROM ComplianceBatchOverride
                WHERE Apprvl_Stat IS DISTINCT FROM Last_Processed_Apprvl_Stat ORDER BY Ovrd_ID""").fetchall()]
        for oid in ids:
            self._decision(oid, s)
        pending = [r["ovrd_id"] for r in self.conn.execute(
            """SELECT Ovrd_ID FROM ComplianceBatchOverride WHERE Override_Ty IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN')
                  AND Apprvl_Stat='APPROVED' AND Promotion_Stat IN ('PENDING','FAILED')
                  AND Last_Processed_Apprvl_Stat='APPROVED' ORDER BY Ovrd_ID""").fetchall()]
        for oid in pending:
            self.promote_reopen(oid, s)
        for ext in sorted(s.extracts_to_refresh - s.reopened_extracts):
            if self.refresh_extract:
                self.refresh_extract(ext)
        for ext in sorted(s.reopened_extracts):
            if self.after_reopen:
                self.after_reopen(ext)
        return s

    # ------------------------------------------------------------------ detection
    def _decision(self, ovrd_id: int, s: DecisionSummary) -> None:
        with self.conn.transaction():
            o = self.conn.execute(
                """SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s
                      AND Apprvl_Stat IS DISTINCT FROM Last_Processed_Apprvl_Stat FOR UPDATE SKIP LOCKED""",
                (ovrd_id,)).fetchone()
            if o is None:
                return
            prev, new = o["last_processed_apprvl_stat"], o["apprvl_stat"]
            ctx = dict(ovrd_id=ovrd_id, req_id=o["req_id"], extract_id=o["extract_id"], btch_id=o["btch_id"],
                       project_cd=o["project_cd"], table_nm=o["table_nm"], src_cd=o["src_cd"], run_ty=o["run_ty"])
            problem = self._validate(o, prev, new)
            actor = o["apprvd_by"] or o["rejected_by"] or o["revoked_by"] or o["updated_by"] or "UNKNOWN"
            if problem:
                self.logger.audit("APPROVAL_INVALID_DETECTED", actor=actor,
                                  description=f"{prev} -> {new}: {problem}", **ctx)
                s.invalid.append(ovrd_id)
            elif new == "APPROVED" and o["override_ty"] in REOPENS:
                ev = "CORRECTION_REOPEN_APPROVED" if o["override_ty"] == "CORRECTION_REOPEN" else "LATE_ARRIVAL_REOPEN_APPROVED"
                self.logger.audit(ev, actor=o["apprvd_by"], load_id=o["candidate_load_id"], **ctx)
                self.conn.execute("UPDATE ComplianceBatchOverride SET Promotion_Stat='PENDING' WHERE Ovrd_ID=%s",
                                  (ovrd_id,))
            elif new == "APPROVED" and o["override_ty"] == "CARRY_FORWARD":
                self._apply_carry_forward(o, ctx)
                s.extracts_to_refresh.add(o["extract_id"])
            elif new == "APPROVED":
                ev = "SOURCE_WAIVER_APPROVED" if o["override_ty"] == "SOURCE_WAIVER" else "RULE_WAIVER_APPROVED"
                self.logger.audit(ev, actor=o["apprvd_by"], description=o["rule_ref"], **ctx)
                s.extracts_to_refresh.add(o["extract_id"])
            elif new == "REJECTED":
                self.logger.audit("OVERRIDE_REJECTED", actor=o["rejected_by"], description=o["rejection_rsn"], **ctx)
                if o["override_ty"] in REOPENS and o["candidate_load_id"]:
                    self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='SUPERSEDED', Updated_Dtts=%s "
                                      "WHERE Load_ID=%s AND Load_Stat='PENDING_APPROVAL'",
                                      (self.clock.now(), o["candidate_load_id"]))
                if o["override_ty"] in REVOCABLE:
                    s.extracts_to_refresh.add(o["extract_id"])
            elif new == "REVOKED":
                self.logger.audit("WAIVER_REVOKED", actor=o["revoked_by"],
                                  description=f"{o['override_ty']}: {o['revocation_rsn']}", **ctx)
                if o["override_ty"] == "CARRY_FORWARD":
                    self._remove_carry_forward(o)
                s.extracts_to_refresh.add(o["extract_id"])
            self.conn.execute("UPDATE ComplianceBatchOverride SET Last_Processed_Apprvl_Stat=%s WHERE Ovrd_ID=%s",
                              (new, ovrd_id))
            s.processed += 1

    def _validate(self, o: dict, prev: Optional[str], new: str) -> Optional[str]:
        if (prev, new) not in LEGAL:
            return "transition not allowed"
        if new == "REVOKED" and o["override_ty"] not in REVOCABLE:
            return "only waivers and carry-forwards can be revoked"
        if o["override_ty"] in REVOCABLE:
            e = self.conn.execute("SELECT Trigger_Stat FROM ComplianceExtractControl WHERE Extract_ID=%s",
                                  (o["extract_id"],)).fetchone()
            if new in ("APPROVED", "REVOKED") and e and e["trigger_stat"] in ("TRIGGERED", "REQUESTED"):
                return "waiver / carry-forward decisions are not allowed after the extract was triggered (D-48)"
            if o["override_ty"] in ("SOURCE_WAIVER", "CARRY_FORWARD") and new == "APPROVED":
                b = get_batch(self.conn, o["req_id"])
                if b["batch_close_ind"] == 1:
                    return f"{o['override_ty'].lower()} on a closed batch"
                if o["override_ty"] == "CARRY_FORWARD":
                    return self._carry_forward_problem(o, b)
        if new == "APPROVED" and o["override_ty"] in REOPENS:
            if o["reviewed_load_id"] != o["candidate_load_id"]:
                return "Reviewed_Load_ID does not match Candidate_Load_ID"
            if prev is None:
                return "reopen overrides are created by the framework only"
        return None

    # ------------------------------------------------------------------ promotion (D-04, D-53)
    def promote_reopen(self, ovrd_id: int, s: DecisionSummary) -> None:
        o = self.conn.execute("SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", (ovrd_id,)).fetchone()
        try:
            with db.held(self.conn, db.batch_key(o["btch_id"]), self.settings.lock_timeout_seconds):
                done = self._promote_locked(ovrd_id)
            if done:
                s.promoted.append(ovrd_id)
                s.reopened_extracts.add(o["extract_id"])
        except Exception as e:  # noqa: BLE001
            log.exception("reopen promotion %s failed", ovrd_id)
            with self.conn.transaction():
                self.conn.execute("UPDATE ComplianceBatchOverride SET Promotion_Stat='FAILED', Updated_Dtts=%s "
                                  "WHERE Ovrd_ID=%s AND Promotion_Stat IN ('PENDING','FAILED')",
                                  (self.clock.now(), ovrd_id))
                self.logger.audit("REOPEN_PROMOTION_FAILED", ovrd_id=ovrd_id, req_id=o["req_id"], btch_id=o["btch_id"],
                                  extract_id=o["extract_id"], load_id=o["candidate_load_id"],
                                  description=sanitize_db_error(f"{type(e).__name__}: {e}"))
            s.promotion_failed.append(ovrd_id)

    def _promote_locked(self, ovrd_id: int) -> bool:
        now = self.clock.now()
        o = self.conn.execute("SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", (ovrd_id,)).fetchone()
        if not (o["apprvl_stat"] == "APPROVED" and o["promotion_stat"] in ("PENDING", "FAILED")
                and o["reviewed_load_id"] == o["candidate_load_id"]):
            return False  # changed since selection (e.g. a newer file reset it)
        b = get_batch(self.conn, o["req_id"])
        cfg = cfgmod.file_config(self.conn, b["project_cd"], b["table_nm"], b["src_cd"])
        if cfg is None:
            raise TechnicalFailure("no active file config for the batch")
        load = self.conn.execute("SELECT * FROM ComplianceFileLoad WHERE Load_ID=%s", (o["candidate_load_id"],)).fetchone()
        if staged_row_count(self.conn, cfg, b["btch_id"], load["load_id"]) != load["stg_rcd_cnt"]:
            restage(self.conn, self.store, self.settings, cfg, load, now)
            with self.conn.transaction():
                self.logger.audit("REOPEN_RESTAGED_FROM_ARCHIVE", ovrd_id=ovrd_id, req_id=b["req_id"],
                                  btch_id=b["btch_id"], load_id=load["load_id"], extract_id=b["extract_id"])
        with self.conn.transaction():
            b = get_batch(self.conn, o["req_id"], for_update=True)
            o = self.conn.execute("SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s FOR UPDATE",
                                  (ovrd_id,)).fetchone()
            if not (o["apprvl_stat"] == "APPROVED" and o["reviewed_load_id"] == load["load_id"]):
                return False
            check_transition(b["req_stat"], COMPLETED)
            res = swap(self.conn, cfg, b["btch_id"], load["load_id"], load["stg_rcd_cnt"], now)
            if b["current_load_id"] and b["current_load_id"] != load["load_id"]:
                self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='SUPERSEDED', Updated_Dtts=%s "
                                  "WHERE Load_ID=%s AND Load_Stat='PROMOTED'", (now, b["current_load_id"]))
            self.conn.execute(
                """UPDATE ComplianceRequestControl SET Resolution_Ty='NEW_FILE', Current_Load_ID=%s, Req_Stat=%s,
                          Reuse_Btch_ID=NULL, Updated_Dtts=%s WHERE Req_ID=%s""",
                (load["load_id"], COMPLETED, now, b["req_id"]))
            self.conn.execute(
                """UPDATE ComplianceFileLoad SET Load_Stat='PROMOTED', Core_Appended_Cnt=%s, Core_Disabled_Cnt=%s,
                          Promoted_Dtts=%s, Updated_Dtts=%s WHERE Load_ID=%s""",
                (res.appended_cnt, res.disabled_cnt, now, now, load["load_id"]))
            self.conn.execute(
                """UPDATE ComplianceBatchOverride SET Promotion_Stat='PROMOTED', Promoted_Dtts=%s,
                          History = History || E'\\n' || %s, Updated_Dtts=%s WHERE Ovrd_ID=%s""",
                (now, f"{now.isoformat()} promoted load {load['load_id']} (appended {res.appended_cnt})", now, ovrd_id))
            self.logger.batch_event("REOPEN_PROMOTED", req_id=b["req_id"], btch_id=b["btch_id"], load_id=load["load_id"],
                                    ovrd_id=ovrd_id, actor=o["apprvd_by"],
                                    detail=f"{o['override_ty']} appended={res.appended_cnt} disabled={res.disabled_cnt}")
            flagged = self.conn.execute("SELECT Currently_Flagged_For_Correction AS f FROM vw_crc_current_flags "
                                        "WHERE Req_ID=%s", (b["req_id"],)).fetchone()
            if o["override_ty"] == "CORRECTION_REOPEN" and flagged and flagged["f"]:
                self.logger.batch_event("CORRECTION_FLAG_CLEARED", req_id=b["req_id"], btch_id=b["btch_id"],
                                        load_id=load["load_id"], ovrd_id=ovrd_id, actor=o["apprvd_by"],
                                        entry_ty="MANUAL")
        return True

    # ------------------------------------------------------------------ carry-forward (D-70)
    def _reuse_source(self, o: dict, b: dict) -> Optional[dict]:
        """The batch whose data is reused: the requested one, else the latest earlier batch of the same
        (project, table, source, run type) that is closed with data. A carried batch resolves to its source."""
        row = self.conn.execute(
            """SELECT * FROM ComplianceRequestControl
                WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s AND Run_Ty=%s AND Req_ID <> %s
                  AND Batch_Close_Ind = 1 AND Resolution_Ty IN ('NEW_FILE','CARRY_FORWARD')
                  AND Rpt_Start_Dt_Key < %s AND (%s::text IS NULL OR Btch_ID = %s)
                ORDER BY Rpt_Start_Dt_Key DESC, Req_ID DESC LIMIT 1""",
            (b["project_cd"], b["table_nm"], b["src_cd"], b["run_ty"], b["req_id"], b["rpt_start_dt_key"],
             o["reuse_btch_id"], o["reuse_btch_id"])).fetchone()
        if row and row["resolution_ty"] == "CARRY_FORWARD":
            row = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Btch_ID=%s",
                                    (row["reuse_btch_id"],)).fetchone()
        return row if row and row["current_load_id"] else None

    def _carry_forward_problem(self, o: dict, b: dict) -> Optional[str]:
        rt = cfgmod.run_type(self.conn, b["run_ty"])
        if not rt.carry_fwd:
            return f"run type {b['run_ty']} does not allow carry-forward (Carry_Fwd_Ind = 0)"
        if b["current_load_id"] is not None:
            return "batch already has promoted data"
        if self._reuse_source(o, b) is None:
            return ("no earlier closed batch with data" +
                    (f" matches Reuse_Btch_ID {o['reuse_btch_id']}" if o["reuse_btch_id"] else ""))
        return None

    def _apply_carry_forward(self, o: dict, ctx: dict) -> None:
        b = get_batch(self.conn, o["req_id"], for_update=True)
        src = self._reuse_source(o, b)
        check_transition(b["req_stat"], CARRIED_FORWARD)
        self.conn.execute(
            """UPDATE ComplianceRequestControl SET Resolution_Ty='CARRY_FORWARD', Reuse_Btch_ID=%s, Req_Stat=%s,
                      Updated_Dtts=%s WHERE Req_ID=%s""", (src["btch_id"], CARRIED_FORWARD, self.clock.now(), b["req_id"]))
        self.conn.execute("UPDATE ComplianceBatchOverride SET Reuse_Btch_ID=%s WHERE Ovrd_ID=%s",
                          (src["btch_id"], o["ovrd_id"]))
        self.logger.audit("CARRY_FORWARD_APPROVED", actor=o["apprvd_by"], description=f"reuses {src['btch_id']}", **ctx)
        self.logger.batch_event("CARRY_FORWARD_APPLIED", req_id=b["req_id"], btch_id=b["btch_id"],
                                ovrd_id=o["ovrd_id"], actor=o["apprvd_by"], entry_ty="MANUAL",
                                detail=f"reuses {src['btch_id']} (load {src['current_load_id']})")

    def _remove_carry_forward(self, o: dict) -> None:
        b = get_batch(self.conn, o["req_id"], for_update=True)
        if b["resolution_ty"] != "CARRY_FORWARD" or b["req_stat"] not in OPEN_STATUSES:
            return
        to_stat = b["req_stat"] if b["req_stat"] != CARRIED_FORWARD else PENDING
        check_transition(b["req_stat"], to_stat)
        self.conn.execute(
            """UPDATE ComplianceRequestControl SET Resolution_Ty=NULL, Reuse_Btch_ID=NULL, Req_Stat=%s,
                      Updated_Dtts=%s WHERE Req_ID=%s""", (to_stat, self.clock.now(), b["req_id"]))
        self.logger.batch_event("CARRY_FORWARD_REMOVED", req_id=b["req_id"], btch_id=b["btch_id"],
                                ovrd_id=o["ovrd_id"], actor=o["revoked_by"] or "SYSTEM", entry_ty="MANUAL",
                                detail=f"revoked: {o['revocation_rsn']}")
