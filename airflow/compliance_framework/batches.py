"""Batch creation (scheduled and ad-hoc) and batch rows."""
from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from . import config as cfg
from . import db
from .audit import EventLogger
from .common import PENDING, Clock, ConfigError, build_btch_id
from .config import RunType, XwalkRow
from .db import Connection
from .settings import Settings

log = logging.getLogger(__name__)


@dataclass
class CreateResult:
    req_id: int
    created: bool


def find_batch(conn, project_cd, table_nm, src_id, run_ty, rpt_start: date, rpt_end: date, req_dt: date) -> Optional[dict]:
    return conn.execute(
        """SELECT * FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s AND Run_Ty=%s
              AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s AND Req_Dt_Key=%s""",
        (project_cd, table_nm, src_id, run_ty, rpt_start, rpt_end, req_dt)).fetchone()


def get_batch(conn: Connection, req_id: int) -> dict:
    """The batch row; callers that change it hold the batch lock."""
    return conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s", (req_id,)).fetchone()


def promoted_load(conn: Connection, btch_id: str) -> Optional[dict]:
    """The batch's PROMOTED load, if any."""
    return conn.execute("SELECT * FROM ComplianceFileLoad WHERE Btch_ID=%s AND Load_Stat='PROMOTED'",
                        (btch_id,)).fetchone()


def create_batch(conn: Connection, clock: Clock, logger: EventLogger, *, xwalk: XwalkRow,
                 rpt_start: date, rpt_end: date, req_dt: date, intake_id: Optional[int] = None,
                 lock_timeout: Optional[float] = 300, locked: bool = False) -> CreateResult:
    """Create the batch for (period, run date) unless it exists; `locked` = the caller holds the sequence lock."""
    x = xwalk
    if locked:
        return _create_batch(conn, clock, logger, x, rpt_start, rpt_end, req_dt, intake_id)
    with db.held(conn, db.seq_key(x.project_cd, x.table_nm, x.src_id, x.run_ty), lock_timeout):
        with conn.transaction():
            return _create_batch(conn, clock, logger, x, rpt_start, rpt_end, req_dt, intake_id)


def _create_batch(conn, clock, logger, x: XwalkRow, rpt_start, rpt_end, req_dt, intake_id) -> CreateResult:
    existing = find_batch(conn, x.project_cd, x.table_nm, x.src_id, x.run_ty, rpt_start, rpt_end, req_dt)
    if existing:
        return CreateResult(existing["req_id"], False)
    seq = 1 + conn.execute(
        """SELECT count(*) AS n FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s
              AND Run_Ty=%s AND Req_Dt_Key=%s""",
        (x.project_cd, x.table_nm, x.src_id, x.run_ty, req_dt)).fetchone()["n"]
    btch = build_btch_id(req_dt, x.project_cd, x.table_nm, x.src_id, x.run_ty, x.cmplnc_vrsn, seq)
    now = clock.now()
    conn.execute(
        """INSERT INTO ComplianceRequestControl (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key,
             Rpt_End_Dt_Key, Req_Dt_Key, Btch_ID, Req_Stat, Batch_Close_Ind, Created_Dtts, Updated_Dtts)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s)""",
        (x.project_cd, x.table_nm, x.src_id, x.run_ty, rpt_start, rpt_end, req_dt, btch, PENDING, now, now))
    req_id = conn.execute("SELECT Req_ID FROM ComplianceRequestControl WHERE Btch_ID=%s", (btch,)).fetchone()["req_id"]
    logger.batch_event("BATCH_CREATED", req_id=req_id, btch_id=btch, intake_id=intake_id,
                       detail=f"run_date={req_dt} period={rpt_start}..{rpt_end}")
    return CreateResult(req_id, True)


def _month_start(d: date, months: int = 0) -> date:
    index = d.year * 12 + (d.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _shift_months(d: date, months: int) -> date:
    first = _month_start(d, months)
    last_day = (_month_start(first, 1) - timedelta(days=1)).day
    return first.replace(day=min(d.day, last_day))


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _need(name: str, param: str, value: Optional[int]) -> int:
    if value is None:
        raise ConfigError(f"period {name} requires --{param.replace('_', '-')}")
    return value


PERIODS: dict[str, Callable[[date, Optional[int], Optional[int]], tuple[date, date]]] = {
    "SAME_DAY": lambda d, days, weeks: (d, d),
    "PREV_DAY": lambda d, days, weeks: (d - timedelta(days=1), d - timedelta(days=1)),
    "PREV_N_DAYS": lambda d, days, weeks: (d - timedelta(days=_need("PREV_N_DAYS", "lookback_days", days)),
                                           d - timedelta(days=1)),
    "PREV_WEEK_SAME_DAY": lambda d, days, weeks: (
        d - timedelta(days=7 * _need("PREV_WEEK_SAME_DAY", "lookback_weeks", weeks)), d - timedelta(days=1)),
    "PREV_CALENDAR_WEEK": lambda d, days, weeks: (d - timedelta(days=d.weekday() + 7),
                                                  d - timedelta(days=d.weekday() + 1)),
    "CURRENT_CALENDAR_MONTH": lambda d, days, weeks: (_month_start(d), _month_start(d, 1) - timedelta(days=1)),
    "PREV_CALENDAR_MONTH": lambda d, days, weeks: (_month_start(d, -1), _month_start(d) - timedelta(days=1)),
    "ROLLING_1_MONTH": lambda d, days, weeks: (_shift_months(d, -1), d - timedelta(days=1)),
    "PREV_CALENDAR_QUARTER": lambda d, days, weeks: (_month_start(_quarter_start(d), -3),
                                                     _quarter_start(d) - timedelta(days=1)),
    "PREV_CALENDAR_YEAR": lambda d, days, weeks: (date(d.year - 1, 1, 1), date(d.year - 1, 12, 31)),
}


def period_sql(name: str, period_file: str) -> str:
    """SQL text for `name` from a project file defining PERIOD_SQL (Teradata SQL, one statement)."""
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


def compute_period(conn: Connection, name: str, sched_dt: date, lookback_days: Optional[int] = None,
                   lookback_weeks: Optional[int] = None, period_file: Optional[str] = None) -> tuple[date, date]:
    """Report period for a run date: a built-in name, or a statement from a project period file."""
    if period_file:
        text = period_sql(name, period_file)
        for param, value in (("lookback_days", lookback_days), ("lookback_weeks", lookback_weeks)):
            if f"%({param})s" in text and value is None:
                raise ConfigError(f"period {name} requires --{param.replace('_', '-')}")
        row = conn.execute(text, {"sched_dt": sched_dt, "lookback_days": lookback_days,
                                  "lookback_weeks": lookback_weeks}).fetchone()
        start, end = db.as_date(row["rpt_start"]), db.as_date(row["rpt_end"])
    else:
        fn = PERIODS.get(name) or PERIODS.get(name.upper())
        if fn is None:
            raise ConfigError(f"unknown period {name!r}; available: {', '.join(sorted(PERIODS))}")
        start, end = fn(sched_dt, lookback_days, lookback_weeks)
    if start is None or end is None or end < start:
        raise ConfigError(f"period {name} returned an invalid period {start}..{end} for {sched_dt}")
    return start, end


@dataclass
class ScheduleSummary:
    run_date: Optional[date] = None
    rpt_start: Optional[date] = None
    rpt_end: Optional[date] = None
    created: int = 0
    existing: int = 0
    errors: list[str] = field(default_factory=list)


def create_batches(conn: Connection, clock: Clock, settings: Settings, *, project_cd: str, run_ty: str,
                   period: str, table_nm: Optional[str] = None, period_file: Optional[str] = None,
                   lookback_days: Optional[int] = None, lookback_weeks: Optional[int] = None) -> ScheduleSummary:
    """One batch per effective (table, source) of the project for the period computed from the run date."""
    s = ScheduleSummary()
    rt = cfg.run_type(conn, run_ty)
    if rt is None or not rt.active or rt.run_category_cd != "ROUTINE":
        raise ConfigError(f"run type {run_ty} is unknown, inactive or not ROUTINE")
    s.run_date = clock.today(settings.business_tz)
    s.rpt_start, s.rpt_end = compute_period(conn, period, s.run_date, lookback_days, lookback_weeks, period_file)
    logger = EventLogger(conn, clock)
    rows = [x for x in cfg.xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(s.run_date)]
    if not rows:
        s.errors.append(f"no effective crosswalk rows for {project_cd}/{table_nm or '*'}/{run_ty} on {s.run_date}")
    for x in rows:
        res = create_batch(conn, clock, logger, xwalk=x, rpt_start=s.rpt_start, rpt_end=s.rpt_end, req_dt=s.run_date,
                           lock_timeout=settings.lock_timeout_seconds)
        if res.created:
            s.created += 1
        else:
            s.existing += 1
    return s


@dataclass
class IntakeSummary:
    run_date: Optional[date] = None
    handled: int = 0
    created: int = 0
    existing: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)


class IntakeProcessor:
    """ComplianceRequestInTake holds ad-hoc requests only (the run type must be in the ADHOC category)."""

    def __init__(self, conn: Connection, clock: Clock, settings: Settings):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.logger = EventLogger(conn, clock)

    def run(self, project_cd: Optional[str] = None, run_ty: Optional[str] = None) -> IntakeSummary:
        """Sweep the requests due today; each request is claimed with its own lock."""
        summary = IntakeSummary(run_date=self.clock.today(self.settings.business_tz))
        where = ["%s BETWEEN Req_Start_Dt_Key AND Req_End_Dt_Key", "(Last_Run_Dt_Key IS NULL OR Last_Run_Dt_Key <> %s)"]
        params: list = [summary.run_date, summary.run_date]
        for col, val in (("Project_Cd", project_cd), ("Run_Ty", run_ty)):
            if val is not None:
                where.append(f"{col} = %s")
                params.append(val)
        due = self.conn.execute(f"SELECT Intake_ID FROM ComplianceRequestInTake WHERE {' AND '.join(where)} "
                                "ORDER BY Created_Dtts, Intake_ID", params).fetchall()
        run_types = cfg.run_types(self.conn) if due else {}
        for d in due:
            key = db.row_key("INTAKE", d["intake_id"])
            if not db.try_lock(self.conn, key):
                continue
            try:
                it = self.conn.execute("SELECT * FROM ComplianceRequestInTake WHERE Intake_ID=%s",
                                       (d["intake_id"],)).fetchone()
                if it is None or db.as_date(it["last_run_dt_key"]) == summary.run_date:
                    continue
                summary.handled += 1
                try:
                    self._process(it, summary, run_types)
                except Exception as e:
                    log.exception("intake %s failed", it["intake_id"])
                    with self.conn.transaction():
                        self._mark_run(it["intake_id"])
                        self.logger.audit("INTAKE_FAILED", intake_id=it["intake_id"],
                                          description=f"technical error: {e}")
                    summary.failed += 1
            finally:
                db.unlock(self.conn, key)
        return summary

    def _process(self, it: dict, summary: IntakeSummary, run_types: dict[str, RunType]) -> None:
        rt = run_types.get(it["run_ty"])
        reason = None
        targets: list[XwalkRow] = []
        if rt is None or not rt.active or rt.run_category_cd != "ADHOC":
            reason = f"run type {it['run_ty']} is unknown, inactive or not ADHOC"
        else:
            rpt_start, rpt_end = db.as_date(it["rpt_start_dt_key"]), db.as_date(it["rpt_end_dt_key"])
            sources = cfg.effective_sources(self.conn, it["project_cd"], it["table_nm"], it["run_ty"], rpt_start)
            targets = [x for x in sources if it["src_id"] is None or x.src_id == it["src_id"]]
            if not targets:
                reason = "no active, effective crosswalk source for this request"
        if reason:
            with self.conn.transaction():
                self._mark_run(it["intake_id"])
                self._fail(it, summary, reason)
            return
        keys = [db.seq_key(x.project_cd, x.table_nm, x.src_id, x.run_ty) for x in targets]
        with db.held_all(self.conn, keys, self.settings.lock_timeout_seconds):
            with self.conn.transaction():
                self._mark_run(it["intake_id"])
                created = sum(create_batch(self.conn, self.clock, self.logger, xwalk=x, rpt_start=rpt_start,
                                           rpt_end=rpt_end, req_dt=summary.run_date, intake_id=it["intake_id"],
                                           locked=True).created for x in targets)
        summary.created += created
        summary.existing += len(targets) - created
        summary.details.append(f"{it['intake_id']}: created={created} existing={len(targets) - created}")

    def _fail(self, it: dict, summary: IntakeSummary, reason: str) -> None:
        self.logger.audit("INTAKE_FAILED", intake_id=it["intake_id"], project_cd=it["project_cd"],
                          table_nm=it["table_nm"], src_id=it["src_id"], run_ty=it["run_ty"], description=reason)
        summary.failed += 1
        summary.details.append(f"{it['intake_id']}: FAILED {reason}")

    def _mark_run(self, intake_id: int) -> None:
        self.conn.execute("UPDATE ComplianceRequestInTake SET Last_Run_Dt_Key=%s, Updated_Dtts=%s WHERE Intake_ID=%s",
                          (self.clock.today(self.settings.business_tz), self.clock.now(), intake_id))
