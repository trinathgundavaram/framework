"""Detects manual SQL decisions on ComplianceBatchOverride and acts on them (design §7 P7, D-12, D-48, D-53)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import psycopg

from .. import locks
from ..audit.event_logger import EventLogger
from ..batches.crc_repository import get_batch
from ..clock import Clock
from ..connections import ConnectionManager
from ..common.status import S_COMPLETE, StatusModel
from ..config import repository as repo
from ..errors import TechnicalFailure
from ..ingest.file_reader import sanitize_db_error
from ..load import promoter
from ..load.archive_restager import restage
from ..settings import Settings
from ..storage.object_store import ObjectStore

log = logging.getLogger(__name__)

REOPENS = ("LATE_ARRIVAL_REOPEN", "CORRECTION_REOPEN")
WAIVERS = ("SOURCE_WAIVER", "RULE_WAIVER")
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
    def __init__(self, conns: ConnectionManager, clock: Clock, settings: Settings, store: ObjectStore,
                 after_reopen: Optional[Callable[[int], None]] = None,
                 refresh_extract: Optional[Callable[[int], None]] = None):
        self.conns = conns
        self.conn, self.clock, self.settings, self.store = conns.meta, clock, settings, store
        self.after_reopen = after_reopen          # D-41 re-trigger hook
        self.refresh_extract = refresh_extract    # waiver decisions
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
                if o["override_ty"] in WAIVERS:
                    s.extracts_to_refresh.add(o["extract_id"])
            elif new == "REVOKED":
                self.logger.audit("WAIVER_REVOKED", actor=o["revoked_by"], description=o["revocation_rsn"], **ctx)
                s.extracts_to_refresh.add(o["extract_id"])
            self.conn.execute("UPDATE ComplianceBatchOverride SET Last_Processed_Apprvl_Stat=%s WHERE Ovrd_ID=%s",
                              (new, ovrd_id))
            s.processed += 1

    def _validate(self, o: dict, prev: Optional[str], new: str) -> Optional[str]:
        if (prev, new) not in LEGAL:
            return "transition not allowed"
        if new == "REVOKED" and o["override_ty"] not in WAIVERS:
            return "only waivers can be revoked"
        if o["override_ty"] in WAIVERS:
            e = self.conn.execute("SELECT Trigger_Stat FROM ComplianceExtractControl WHERE Extract_ID=%s",
                                  (o["extract_id"],)).fetchone()
            if new in ("APPROVED", "REVOKED") and e and e["trigger_stat"] in ("TRIGGERED", "REQUESTED"):
                return "waiver decisions are not allowed after the extract was triggered (D-48)"
            if o["override_ty"] == "SOURCE_WAIVER" and new == "APPROVED":
                b = get_batch(self.conn, o["req_id"])
                if b["batch_close_ind"] == 1:
                    return "source waiver on a closed batch"
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
            with locks.held(self.conn, locks.batch_key(o["btch_id"]), self.settings.lock_timeout_seconds):
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
        cfg = repo.file_config(self.conn, b["project_cd"], b["table_nm"], b["src_cd"])
        if cfg is None:
            raise TechnicalFailure("no active file config for the batch")
        load = self.conn.execute("SELECT * FROM ComplianceFileLoad WHERE Load_ID=%s", (o["candidate_load_id"],)).fetchone()
        data = self.conns.for_config(cfg)
        if promoter.staged_row_count(data, cfg, b["btch_id"], load["load_id"]) != load["stg_rcd_cnt"]:
            restage(data, self.store, cfg, load, self.settings, now, self.conns.spec(cfg.target_connection_nm))
            with self.conn.transaction():
                self.logger.audit("REOPEN_RESTAGED_FROM_ARCHIVE", ovrd_id=ovrd_id, req_id=b["req_id"],
                                  btch_id=b["btch_id"], load_id=load["load_id"], extract_id=b["extract_id"])
        status = StatusModel.load(self.conn)
        with self.conn.transaction():
            b = get_batch(self.conn, o["req_id"], for_update=True)
            o = self.conn.execute("SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s FOR UPDATE",
                                  (ovrd_id,)).fetchone()
            if not (o["apprvl_stat"] == "APPROVED" and o["reviewed_load_id"] == load["load_id"]):
                return False
            to_code = status.code(S_COMPLETE)
            status.check(b["req_stat"], to_code)
            with data.transaction():      # commits before the metadata transaction; re-run is idempotent
                res = promoter.swap(data, cfg, b["btch_id"], load["load_id"], load["stg_rcd_cnt"], now)
            if b["current_load_id"] and b["current_load_id"] != load["load_id"]:
                self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='SUPERSEDED', Updated_Dtts=%s "
                                  "WHERE Load_ID=%s AND Load_Stat='PROMOTED'", (now, b["current_load_id"]))
            self.conn.execute(
                """UPDATE ComplianceRequestControl SET Resolution_Ty='NEW_FILE', Current_Load_ID=%s, Req_Stat=%s,
                          Updated_Dtts=%s WHERE Req_ID=%s""", (load["load_id"], to_code, now, b["req_id"]))
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
