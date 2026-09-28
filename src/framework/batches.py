"""Batches: CRC / extract-row creation (§5.2), scheduled batch creation (P2) and ad-hoc intake (P4).

Scheduling lives outside the framework (EventBridge / Step Functions / cron). Each scheduled run calls
`run --module BATCH_CREATION` for one project and run type, naming the report-period SQL to use (period_sql.py).
A missed run is re-created by running the same command with `--as-of <missed date>`.

A batch is one (project, table, source, run type, report period, **run date**): a run type whose
period spans several days (a CMS universe, say) gets one batch - and one extract - per run date.
"""
from __future__ import annotations

import importlib.util
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import psycopg

from . import config as cfg
from . import db
from .audit import EventLogger
from .common import PENDING, Clock, ConfigError, build_btch_id
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


def find_batch(conn, project_cd, table_nm, src_id, run_ty, rpt_start: date, rpt_end: date, req_dt: date,
               for_update: bool = False) -> Optional[dict]:
    return conn.execute(
        """SELECT * FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s AND Run_Ty=%s
              AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s AND Req_Dt_Key=%s""" + (" FOR UPDATE" if for_update else ""),
        (project_cd, table_nm, src_id, run_ty, rpt_start, rpt_end, req_dt)).fetchone()


def get_batch(conn: psycopg.Connection, req_id: int, for_update: bool = False) -> dict:
    return conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s" + (" FOR UPDATE" if for_update else ""),
                        (req_id,)).fetchone()


def batches_of_extract(conn: psycopg.Connection, extract_id: int, open_only: bool = False) -> list[dict]:
    return conn.execute("SELECT * FROM ComplianceRequestControl WHERE Extract_ID=%s"
                        + (" AND Batch_Close_Ind=0 ORDER BY Src_ID FOR UPDATE" if open_only else " ORDER BY Src_ID"),
                        (extract_id,)).fetchall()


def find_extract(conn, project_cd, table_nm, run_ty, rpt_start: date, rpt_end: date, req_dt: date) -> Optional[dict]:
    return conn.execute(
        """SELECT * FROM ComplianceExtractControl WHERE Project_Cd=%s AND Table_Nm=%s AND Run_Ty=%s
              AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s AND Req_Dt_Key=%s""",
        (project_cd, table_nm, run_ty, rpt_start, rpt_end, req_dt)).fetchone()


def promoted_load(conn: psycopg.Connection, btch_id: str) -> Optional[dict]:
    """The batch's current data: its single PROMOTED load (D-75, replaces CRC.Current_Load_ID)."""
    return conn.execute("SELECT * FROM ComplianceFileLoad WHERE Btch_ID=%s AND Load_Stat='PROMOTED'",
                        (btch_id,)).fetchone()


def create_batch(conn: psycopg.Connection, clock: Clock, logger: EventLogger, *, xwalk: XwalkRow,
                 rpt_start: date, rpt_end: date, req_dt: date, required_cnt: int,
                 intake_id: Optional[int] = None) -> CreateResult:
    """Idempotent: created=False when the batch for (period, run date) already exists (D-30)."""
    x = xwalk
    with conn.transaction():
        db.xact_lock(conn, db.seq_key(x.project_cd, x.table_nm, x.src_id, x.run_ty))
        existing = find_batch(conn, x.project_cd, x.table_nm, x.src_id, x.run_ty, rpt_start, rpt_end, req_dt)
        if existing:
            return CreateResult(existing["req_id"], False, "EXISTS", existing["extract_id"])
        conn.execute(
            """INSERT INTO ComplianceExtractControl (Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key,
                 Req_Dt_Key, Required_Src_Cnt) VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key) DO NOTHING""",
            (x.project_cd, x.table_nm, x.run_ty, rpt_start, rpt_end, req_dt, required_cnt))
        ext = find_extract(conn, x.project_cd, x.table_nm, x.run_ty, rpt_start, rpt_end, req_dt)
        if ext["extract_close_ind"] == 1:
            logger.audit("BATCH_CREATE_SKIPPED_EXTRACT_CLOSED", project_cd=x.project_cd, table_nm=x.table_nm,
                         src_id=x.src_id, run_ty=x.run_ty, extract_id=ext["extract_id"], intake_id=intake_id,
                         description=f"run date {req_dt}, period {rpt_start}..{rpt_end} is closed; batch not created")
            return CreateResult(None, False, "EXTRACT_CLOSED", ext["extract_id"])
        seq = 1 + conn.execute(
            """SELECT count(*) AS n FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s
                  AND Run_Ty=%s AND Req_Dt_Key=%s""",
            (x.project_cd, x.table_nm, x.src_id, x.run_ty, req_dt)).fetchone()["n"]
        btch = build_btch_id(req_dt, x.project_cd, x.table_nm, x.src_id, x.run_ty, x.cmplnc_vrsn, seq)
        now = clock.now()
        row = conn.execute(
            """INSERT INTO ComplianceRequestControl (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key,
                 Rpt_End_Dt_Key, Req_Dt_Key, Btch_ID, Extract_ID, Req_Stat, Created_Dtts, Updated_Dtts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING Req_ID""",
            (x.project_cd, x.table_nm, x.src_id, x.run_ty, rpt_start, rpt_end, req_dt, btch, ext["extract_id"],
             PENDING, now, now)).fetchone()
        if required_cnt > ext["required_src_cnt"]:          # a later source joined the same run (ad-hoc intake)
            conn.execute("UPDATE ComplianceExtractControl SET Required_Src_Cnt=%s, Updated_Dtts=%s WHERE Extract_ID=%s",
                         (required_cnt, now, ext["extract_id"]))
        logger.batch_event("BATCH_CREATED", req_id=row["req_id"], btch_id=btch, intake_id=intake_id,
                           detail=f"run_date={req_dt} period={rpt_start}..{rpt_end}")
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
    run_date: Optional[date] = None
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
    s.run_date = clock.today(settings.business_tz)            # D-29: run date = creation date
    s.rpt_start, s.rpt_end = compute_period(conn, period, s.run_date, lookback_days, lookback_weeks, period_file)
    logger = EventLogger(conn, clock)
    rows = [x for x in cfg.xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(s.run_date)]
    if not rows:
        s.errors.append(f"no effective crosswalk rows for {project_cd}/{table_nm or '*'}/{run_ty} on {s.run_date}")
    required = Counter(x.table_nm for x in rows)
    for x in rows:
        res = create_batch(conn, clock, logger, xwalk=x, rpt_start=s.rpt_start, rpt_end=s.rpt_end,
                           req_dt=s.run_date, required_cnt=required[x.table_nm])
        if res.created:
            s.created += 1
        elif res.skipped_reason == "EXISTS":
            s.existing += 1
        else:
            s.skipped += 1
    return s


# ============================================================================ ad-hoc intake (P4)
@dataclass
class IntakeSummary:
    run_date: Optional[date] = None
    handled: int = 0
    created: int = 0
    existing: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)


class IntakeProcessor:
    """ComplianceRequestInTake holds ad-hoc requests only (the run type must be in the ADHOC category).

    One row asks for batches for the SAME report period on every run date from Req_Start_Dt_Key to
    Req_End_Dt_Key. This job runs daily: each request whose window contains the run date is handled once
    for that date (Last_Run_Dt_Key), its batches are created, and every outcome is written to the audit
    tables under its Intake_ID. A request that cannot be served is audited as INTAKE_FAILED on each run
    date of its window until its configuration is fixed.
    """

    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.logger = EventLogger(conn, clock)

    def run(self, project_cd: Optional[str] = None, run_ty: Optional[str] = None) -> IntakeSummary:
        """Sweep the requests due today. `BATCH_CREATION` (modules.py) always calls this with
        `project_cd` set, so one project's trigger only ever touches that project's requests; pass
        neither argument to sweep every project (a one-off, unscoped run)."""
        summary = IntakeSummary(run_date=self.clock.today(self.settings.business_tz))
        run_types: Optional[dict[str, RunType]] = None                 # read once, when the first request is due
        while True:
            current = None
            try:
                with self.conn.transaction():
                    row = self.conn.execute(
                        """SELECT * FROM ComplianceRequestInTake
                            WHERE %(d)s BETWEEN Req_Start_Dt_Key AND Req_End_Dt_Key
                              AND Last_Run_Dt_Key IS DISTINCT FROM %(d)s
                              AND (%(p)s::text IS NULL OR Project_Cd = %(p)s) AND (%(r)s::text IS NULL OR Run_Ty = %(r)s)
                            ORDER BY Created_Dtts, Intake_ID LIMIT 1 FOR UPDATE SKIP LOCKED""",
                        {"d": summary.run_date, "p": project_cd, "r": run_ty}).fetchone()
                    if row is None:
                        return summary
                    current = row["intake_id"]
                    summary.handled += 1
                    if run_types is None:
                        run_types = cfg.run_types(self.conn)
                    self._process(row, summary, run_types)
            except Exception as e:  # unexpected error: record it and continue with the next intake
                if current is None:
                    raise
                log.exception("intake %s failed", current)
                with self.conn.transaction():
                    self._mark_run(current)
                    self.logger.audit("INTAKE_FAILED", intake_id=current, description=f"technical error: {e}")
                summary.failed += 1

    # ------------------------------------------------------------------
    def _process(self, it: dict, summary: IntakeSummary, run_types: dict[str, RunType]) -> None:
        self._mark_run(it["intake_id"])
        rt = run_types.get(it["run_ty"])
        if rt is None or not rt.active or rt.run_category_cd != "ADHOC":
            self._fail(it, summary, f"run type {it['run_ty']} is unknown, inactive or not ADHOC")
            return
        sources = cfg.effective_sources(self.conn, it["project_cd"], it["table_nm"], it["run_ty"],
                                        it["rpt_start_dt_key"])
        targets = [x for x in sources if it["src_id"] is None or x.src_id == it["src_id"]]
        if not targets:
            self._fail(it, summary, "no active, effective crosswalk source for this request")
            return
        created = existing = 0
        msgs = []
        for x in targets:
            res = create_batch(self.conn, self.clock, self.logger, xwalk=x, rpt_start=it["rpt_start_dt_key"],
                               rpt_end=it["rpt_end_dt_key"], req_dt=summary.run_date, required_cnt=len(targets),
                               intake_id=it["intake_id"])
            if res.created:
                created += 1
            elif res.skipped_reason == "EXISTS":
                existing += 1
            else:
                msgs.append(f"{x.src_id}: {res.skipped_reason}")
        summary.created += created
        summary.existing += existing
        summary.details.append(f"{it['intake_id']}: created={created} existing={existing}"
                               + (f" {'; '.join(msgs)}" if msgs else ""))

    def _fail(self, it: dict, summary: IntakeSummary, reason: str) -> None:
        self.logger.audit("INTAKE_FAILED", intake_id=it["intake_id"], project_cd=it["project_cd"],
                          table_nm=it["table_nm"], src_id=it["src_id"], run_ty=it["run_ty"], description=reason)
        summary.failed += 1
        summary.details.append(f"{it['intake_id']}: FAILED {reason}")

    def _mark_run(self, intake_id: int) -> None:
        self.conn.execute("UPDATE ComplianceRequestInTake SET Last_Run_Dt_Key=%s, Updated_Dtts=%s WHERE Intake_ID=%s",
                          (self.clock.today(self.settings.business_tz), self.clock.now(), intake_id))
