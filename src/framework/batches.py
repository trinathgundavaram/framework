"""Batches: CRC / extract-row creation (§5.2), scheduled batch creation (P2) and intake processing (P4).

Scheduling lives outside the framework (EventBridge / Step Functions / cron). Each scheduled run calls
`create-batches` for one project and run type, naming the report-period SQL to use (period_sql.py).
A missed run is re-created by running the same command with `--as-of <missed date>`.
"""
from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import psycopg

from . import config as cfg
from . import db
from .audit import EventLogger
from .common import PENDING, Clock, ConfigError, build_btch_id, earliest_close_date
from .config import RunType, XwalkRow
from .period_sql import PERIOD_SQL
from .settings import Settings

log = logging.getLogger(__name__)


# ============================================================================ CRC / extract rows
@dataclass
class CreateResult:
    req_id: Optional[int]
    created: bool
    skipped_reason: Optional[str] = None
    extract_id: Optional[int] = None


def find_batch(conn, project_cd, table_nm, src_cd, run_ty, rpt_start: date, rpt_end: date,
               for_update: bool = False) -> Optional[dict]:
    return conn.execute(
        """SELECT * FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s AND Run_Ty=%s
              AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s""" + (" FOR UPDATE" if for_update else ""),
        (project_cd, table_nm, src_cd, run_ty, rpt_start, rpt_end)).fetchone()


def get_batch(conn: psycopg.Connection, req_id: int, for_update: bool = False) -> dict:
    return conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s" + (" FOR UPDATE" if for_update else ""),
                        (req_id,)).fetchone()


def batches_of_extract(conn: psycopg.Connection, extract_id: int, open_only: bool = False) -> list[dict]:
    return conn.execute("SELECT * FROM ComplianceRequestControl WHERE Extract_ID=%s"
                        + (" AND Batch_Close_Ind=0 FOR UPDATE" if open_only else " ORDER BY Src_Cd"),
                        (extract_id,)).fetchall()


def find_extract(conn, project_cd, table_nm, run_ty, rpt_start: date, rpt_end: date) -> Optional[dict]:
    return conn.execute(
        """SELECT * FROM ComplianceExtractControl WHERE Project_Cd=%s AND Table_Nm=%s AND Run_Ty=%s
              AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s""",
        (project_cd, table_nm, run_ty, rpt_start, rpt_end)).fetchone()


def create_batch(conn: psycopg.Connection, clock: Clock, logger: EventLogger, *, xwalk: XwalkRow,
                 run_type: RunType, rpt_start: date, rpt_end: date, req_dt: date, created_by: str,
                 required_cnt: int, intake_id: Optional[str] = None) -> CreateResult:
    """Idempotent: returns created=False when the batch for the period already exists (D-30)."""
    x = xwalk
    with conn.transaction():
        db.xact_lock(conn, db.seq_key(x.project_cd, x.table_nm, x.src_cd, x.run_ty))
        existing = find_batch(conn, x.project_cd, x.table_nm, x.src_cd, x.run_ty, rpt_start, rpt_end)
        if existing:
            return CreateResult(existing["req_id"], False, "EXISTS", existing["extract_id"])
        earliest = earliest_close_date(req_dt, run_type.sla_days)
        conn.execute(
            """INSERT INTO ComplianceExtractControl (Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key,
                 Required_Src_Cnt, Earliest_Trigger_Dt) VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key) DO NOTHING""",
            (x.project_cd, x.table_nm, x.run_ty, rpt_start, rpt_end, required_cnt, earliest))
        ext = find_extract(conn, x.project_cd, x.table_nm, x.run_ty, rpt_start, rpt_end)
        if ext["trigger_stat"] == "TRIGGERED":
            logger.audit("BATCH_CREATE_SKIPPED_EXTRACT_TRIGGERED", project_cd=x.project_cd, table_nm=x.table_nm,
                         src_cd=x.src_cd, run_ty=x.run_ty, extract_id=ext["extract_id"], intake_id=intake_id,
                         description=f"period {rpt_start}..{rpt_end} already triggered; batch not created")
            return CreateResult(None, False, "EXTRACT_TRIGGERED", ext["extract_id"])
        seq = 1 + conn.execute(
            """SELECT count(*) AS n FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s
                  AND Run_Ty=%s AND Req_Dt_Key=%s""", (x.project_cd, x.table_nm, x.src_cd, x.run_ty, req_dt)).fetchone()["n"]
        btch = build_btch_id(req_dt, x.project_cd, x.table_nm, x.src_cd, x.run_ty, x.cmplnc_vrsn, seq)
        now = clock.now()
        row = conn.execute(
            """INSERT INTO ComplianceRequestControl (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key,
                 Rpt_End_Dt_Key, Req_Dt_Key, Earliest_Close_Dt, Btch_ID, Cmplnc_Vrsn, Extract_ID, Intake_ID, Req_Stat,
                 Created_By, Created_Dtts, Updated_Dtts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING Req_ID""",
            (x.project_cd, x.table_nm, x.src_cd, x.run_ty, rpt_start, rpt_end, req_dt, earliest, btch,
             x.cmplnc_vrsn, ext["extract_id"], intake_id, PENDING, created_by, now, now)).fetchone()
        conn.execute("""UPDATE ComplianceExtractControl SET Earliest_Trigger_Dt = GREATEST(Earliest_Trigger_Dt, %s),
                          Updated_Dtts = %s WHERE Extract_ID = %s""", (earliest, now, ext["extract_id"]))
        logger.batch_event("BATCH_CREATED", req_id=row["req_id"], btch_id=btch, intake_id=intake_id,
                           detail=f"created_by={created_by} period={rpt_start}..{rpt_end} earliest_close={earliest}")
        return CreateResult(row["req_id"], True, None, ext["extract_id"])


# ============================================================================ report period (§5.1)
def period_sql(name: str, period_file: Optional[str] = None) -> str:
    """SQL text for `name` from period_sql.py, or from a project file defining PERIOD_SQL."""
    source = PERIOD_SQL
    if period_file:
        spec = importlib.util.spec_from_file_location("project_period_sql", period_file)
        if spec is None or spec.loader is None:
            raise ConfigError(f"cannot load period file {period_file}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except FileNotFoundError as e:
            raise ConfigError(f"period file {period_file} does not exist") from e
        source = getattr(module, "PERIOD_SQL", None)
        if not isinstance(source, dict):
            raise ConfigError(f"{period_file} must define a PERIOD_SQL dict")
    text = source.get(name) or source.get(name.upper())
    if not text:
        raise ConfigError(f"unknown period {name!r}; available: {', '.join(sorted(source))}")
    text = text.strip().rstrip(";")
    if ";" in text:
        raise ConfigError(f"period {name} must be a single statement")
    return text


def compute_period(conn: psycopg.Connection, name: str, sched_dt: date, lookback_days: Optional[int] = None,
                   lookback_weeks: Optional[int] = None, period_file: Optional[str] = None) -> tuple[date, date]:
    text = period_sql(name, period_file)
    for param, value in (("lookback_days", lookback_days), ("lookback_weeks", lookback_weeks)):
        if f"%({param})s" in text and value is None:
            raise ConfigError(f"period {name} requires --{param.replace('_', '-')}")
    row = conn.execute(text, {"sched_dt": sched_dt, "lookback_days": lookback_days,
                              "lookback_weeks": lookback_weeks}).fetchone()
    start, end = row["rpt_start"], row["rpt_end"]
    if start is None or end is None or end < start:
        raise ConfigError(f"period {name} returned an invalid period {start}..{end} for {sched_dt}")
    return start, end


# ============================================================================ scheduled creation (P2)
@dataclass
class ScheduleSummary:
    rpt_start: Optional[date] = None
    rpt_end: Optional[date] = None
    created: int = 0
    existing: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def create_batches(conn: psycopg.Connection, clock: Clock, settings: Settings, *, project_cd: str, run_ty: str,
                   period: str, table_nm: Optional[str] = None, period_file: Optional[str] = None,
                   lookback_days: Optional[int] = None, lookback_weeks: Optional[int] = None) -> ScheduleSummary:
    """One batch per effective (table, source) of the project for the period computed from the run date."""
    s = ScheduleSummary()
    rt = cfg.run_type(conn, run_ty)
    if rt is None or not rt.active or rt.run_category_cd != "ROUTINE":
        raise ConfigError(f"run type {run_ty} is unknown, inactive or not ROUTINE")
    sched_dt = clock.today(settings.business_tz)             # D-29/D-29b: run date = creation date
    s.rpt_start, s.rpt_end = compute_period(conn, period, sched_dt, lookback_days, lookback_weeks, period_file)
    logger = EventLogger(conn, clock)
    rows = [x for x in cfg.xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(sched_dt)]
    if not rows:
        s.errors.append(f"no effective crosswalk rows for {project_cd}/{table_nm or '*'}/{run_ty} on {sched_dt}")
    required = {}
    for x in rows:
        required[x.table_nm] = required.get(x.table_nm, 0) + 1
    for x in rows:
        res = create_batch(conn, clock, logger, xwalk=x, run_type=rt, rpt_start=s.rpt_start, rpt_end=s.rpt_end,
                           req_dt=sched_dt, created_by="SCHEDULER", required_cnt=required[x.table_nm])
        if res.created:
            s.created += 1
        elif res.skipped_reason == "EXISTS":
            s.existing += 1
        else:
            s.skipped += 1
    return s


# ============================================================================ intake (P4)
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
        rt = cfg.run_type(self.conn, it["run_ty"])
        if rt is None or not rt.active:
            return "FAILED", f"unknown or inactive run type {it['run_ty']}"
        if it["req_ty"] == "CORRECTION_REQUEST":
            return self._correction(it)
        expected = "ROUTINE" if it["req_ty"] == "CYCLE_INIT" else "ADHOC"
        if rt.run_category_cd != expected:
            return "FAILED", f"{it['req_ty']} requires a {expected} run type; {rt.run_ty} is {rt.run_category_cd}"
        ref_date = it["rpt_start_dt_key"]                                            # Q-04 proposal
        all_sources = cfg.effective_sources(self.conn, it["project_cd"], it["table_nm"], it["run_ty"], ref_date)
        targets = [x for x in all_sources if it["src_cd"] is None or x.src_cd == it["src_cd"]]
        if not targets:
            return "FAILED", "no active, effective crosswalk source for this request"
        if it["req_ty"] == "CYCLE_INIT":
            return self._cycle_init(it, rt, targets, len(all_sources))
        return self._adhoc(it, rt, targets)

    def _cycle_init(self, it, rt, targets, required):
        ok, bad, msgs = 0, 0, []
        for x in targets:
            res = create_batch(self.conn, self.clock, self.logger, xwalk=x, run_type=rt,
                               rpt_start=it["rpt_start_dt_key"], rpt_end=it["rpt_end_dt_key"],
                               req_dt=self.clock.today(self.settings.business_tz), created_by="CYCLE_INIT", required_cnt=required,
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
            res = create_batch(self.conn, self.clock, self.logger, xwalk=x, run_type=rt,
                               rpt_start=start, rpt_end=end, req_dt=self.clock.today(self.settings.business_tz), created_by="ADHOC_INTAKE",
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
