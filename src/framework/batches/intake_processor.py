"""ComplianceRequestInTake processing (design §7 P4): CYCLE_INIT, ADHOC_REQUEST, CORRECTION_REQUEST."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

import psycopg

from ..audit.event_logger import EventLogger
from ..clock import Clock
from ..common.status import StatusModel
from ..config import repository as repo
from ..settings import Settings
from .crc_repository import create_batch, find_batch, find_extract

log = logging.getLogger(__name__)


@dataclass
class IntakeSummary:
    processed: int = 0
    failed: int = 0
    partial: int = 0
    details: list[str] = field(default_factory=list)


class IntakeProcessor:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.logger = EventLogger(conn, clock)

    def run(self) -> IntakeSummary:
        summary = IntakeSummary()
        failed_ids: set[str] = set()
        while True:
            current = None
            try:
                with self.conn.transaction():
                    row = self.conn.execute(
                        """SELECT * FROM ComplianceRequestInTake WHERE Intake_Stat='NEW'
                            AND NOT (Intake_ID = ANY(%s))
                            ORDER BY Requested_Dtts, Intake_ID LIMIT 1 FOR UPDATE SKIP LOCKED""",
                        (list(failed_ids),)).fetchone()
                    if row is None:
                        return summary
                    current = row["intake_id"]
                    self._finish(row, summary)
            except Exception as e:  # unexpected error: record it and continue with the next intake
                if current is None:
                    raise
                log.exception("intake %s failed", current)
                failed_ids.add(current)
                with self.conn.transaction():
                    self.conn.execute(
                        """UPDATE ComplianceRequestInTake SET Intake_Stat='FAILED', Processed_Dtts=%s, Error_Txt=%s
                            WHERE Intake_ID=%s AND Intake_Stat='NEW'""",
                        (self.clock.now(), f"technical error: {e}", current))
                    self.logger.audit("INTAKE_FAILED", intake_id=current, description=f"technical error: {e}")
                summary.failed += 1

    def _finish(self, row: dict, summary: "IntakeSummary") -> None:
        stat, err = self._process(row)
        self.conn.execute(
            """UPDATE ComplianceRequestInTake SET Intake_Stat=%s, Processed_Dtts=%s, Error_Txt=%s
                WHERE Intake_ID=%s""", (stat, self.clock.now(), err, row["intake_id"]))
        if stat == "FAILED":
            summary.failed += 1
            self.logger.audit("INTAKE_FAILED", intake_id=row["intake_id"], project_cd=row["project_cd"],
                              table_nm=row["table_nm"], src_cd=row["src_cd"], run_ty=row["run_ty"],
                              description=err)
        elif stat == "PARTIALLY_PROCESSED":
            summary.partial += 1
        else:
            summary.processed += 1
        summary.details.append(f"{row['intake_id']}: {stat} {err or ''}".strip())

    # ------------------------------------------------------------------
    def _process(self, it: dict) -> tuple[str, str | None]:
        rt = repo.run_type(self.conn, it["run_ty"])
        if rt is None or not rt.active:
            return "FAILED", f"unknown or inactive run type {it['run_ty']}"
        if it["req_ty"] == "CORRECTION_REQUEST":
            return self._correction(it)
        expected = "ROUTINE" if it["req_ty"] == "CYCLE_INIT" else "ADHOC"
        if rt.run_category_cd != expected:
            return "FAILED", f"{it['req_ty']} requires a {expected} run type; {rt.run_ty} is {rt.run_category_cd}"
        ref_date = it["rpt_start_dt_key"]                                            # Q-04 proposal
        all_sources = repo.effective_sources(self.conn, it["project_cd"], it["table_nm"], it["run_ty"], ref_date)
        targets = [x for x in all_sources if it["src_cd"] is None or x.src_cd == it["src_cd"]]
        if not targets:
            return "FAILED", "no active, effective crosswalk source for this request"
        if it["req_ty"] == "CYCLE_INIT":
            return self._cycle_init(it, rt, targets, len(all_sources))
        return self._adhoc(it, rt, targets)

    def _req_dt(self, x) -> date:
        return self.clock.now().astimezone(ZoneInfo(x.business_tz)).date()

    def _cycle_init(self, it, rt, targets, required):
        status = StatusModel.load(self.conn)
        ok, bad, msgs = 0, 0, []
        for x in targets:
            res = create_batch(self.conn, self.clock, status, self.logger, xwalk=x, run_type=rt,
                               rpt_start=it["rpt_start_dt_key"], rpt_end=it["rpt_end_dt_key"],
                               req_dt=self._req_dt(x), created_by="CYCLE_INIT", required_cnt=required,
                               intake_id=it["intake_id"])
            if res.created:
                ok += 1
            elif res.skipped_reason == "EXISTS" and self.settings.cycle_init_existing_batch == "SKIP":  # Q-18
                ok += 1
                msgs.append(f"{x.src_cd}: batch already exists (skipped)")
            else:
                bad += 1
                msgs.append(f"{x.src_cd}: {res.skipped_reason}")
        return self._outcome(ok, bad, msgs)

    def _adhoc(self, it, rt, targets):
        status = StatusModel.load(self.conn)
        start, end = it["rpt_start_dt_key"], it["rpt_end_dt_key"]
        ext = find_extract(self.conn, it["project_cd"], it["table_nm"], it["run_ty"], start, end)
        if ext is not None and (ext["trigger_stat"] == "TRIGGERED" or not self.settings.adhoc_allow_add_source_before_trigger):
            return "FAILED", "an extract for this period already exists" + (
                " and was triggered" if ext["trigger_stat"] == "TRIGGERED" else "")      # Q-05
        ok, bad, msgs = 0, 0, []
        to_create = []
        for x in targets:
            if find_batch(self.conn, x.project_cd, x.table_nm, x.src_cd, x.run_ty, start, end):
                bad += 1
                msgs.append(f"{x.src_cd}: batch already exists for the period (D-35)")
                self.logger.audit("INTAKE_DUPLICATE_PERIOD", intake_id=it["intake_id"], project_cd=x.project_cd,
                                  table_nm=x.table_nm, src_cd=x.src_cd, run_ty=x.run_ty,
                                  description=f"period {start}..{end}")
            else:
                to_create.append(x)
        existing_required = ext["required_src_cnt"] if ext else 0
        for x in to_create:
            res = create_batch(self.conn, self.clock, status, self.logger, xwalk=x, run_type=rt,
                               rpt_start=start, rpt_end=end, req_dt=self._req_dt(x), created_by="ADHOC_INTAKE",
                               required_cnt=len(to_create), intake_id=it["intake_id"])
            if res.created:
                ok += 1
            else:
                bad += 1
                msgs.append(f"{x.src_cd}: {res.skipped_reason}")
        if ext is not None and ok:
            self.conn.execute(
                "UPDATE ComplianceExtractControl SET Required_Src_Cnt=%s, Updated_Dtts=%s WHERE Extract_ID=%s",
                (existing_required + ok, self.clock.now(), ext["extract_id"]))
        return self._outcome(ok, bad, msgs)

    def _correction(self, it):
        b = find_batch(self.conn, it["project_cd"], it["table_nm"], it["src_cd"], it["run_ty"],
                       it["rpt_start_dt_key"], it["rpt_end_dt_key"])
        if b is None:
            return "FAILED", "no batch matches the correction request"
        self.logger.batch_event("CORRECTION_FLAGGED", req_id=b["req_id"], btch_id=b["btch_id"],
                                actor=it["requested_by"], entry_ty="MANUAL", intake_id=it["intake_id"],
                                detail="correction requested")
        self.logger.audit("DATA_QUALITY_ISSUE_FLAGGED", actor=it["requested_by"], req_id=b["req_id"],
                          btch_id=b["btch_id"], intake_id=it["intake_id"], project_cd=b["project_cd"],
                          table_nm=b["table_nm"], src_cd=b["src_cd"], run_ty=b["run_ty"],
                          extract_id=b["extract_id"])
        return "PROCESSED", None

    @staticmethod
    def _outcome(ok: int, bad: int, msgs: list[str]):
        text = "; ".join(msgs) or None
        if bad == 0:
            return "PROCESSED", text
        if ok == 0:
            return "FAILED", text
        return "PARTIALLY_PROCESSED", text
