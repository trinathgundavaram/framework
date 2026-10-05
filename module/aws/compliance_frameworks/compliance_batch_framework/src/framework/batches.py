"""Batch creation (scheduled and ad-hoc) and batch rows."""
from __future__ import annotations

import importlib.util
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import psycopg

from . import config as cfg
from . import db
from .audit import EventLogger
from .common import (ADHOC, PENDING, SCHEDULED, Clock, ConfigError, annual_window, build_btch_id, code,
                     period_lookback)
from .config import RunType, XwalkRow
from .period_sql import PERIOD_SQL
from .settings import Settings

log = logging.getLogger(__name__)


@dataclass
class CreateResult:
    req_id: int
    created: bool


def find_batch(conn, project_cd, table_nm, src_id, run_ty, rpt_start: date, rpt_end: date, req_dt: date,
               for_update: bool = False) -> Optional[dict]:
    return conn.execute(
        """SELECT * FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s AND Run_Ty=%s
              AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s AND Req_Dt_Key=%s""" + (" FOR UPDATE" if for_update else ""),
        (project_cd, table_nm, src_id, run_ty, rpt_start, rpt_end, req_dt)).fetchone()


def get_batch(conn: psycopg.Connection, req_id: int, for_update: bool = False) -> dict:
    return conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s" + (" FOR UPDATE" if for_update else ""),
                        (req_id,)).fetchone()


def promoted_load(conn: psycopg.Connection, btch_id: str) -> Optional[dict]:
    """The batch's PROMOTED load, if any."""
    return conn.execute("SELECT * FROM ComplianceFileLoad WHERE Btch_ID=%s AND Load_Stat='PROMOTED'",
                        (btch_id,)).fetchone()


def create_batch(conn: psycopg.Connection, clock: Clock, logger: EventLogger, *, xwalk: XwalkRow,
                 rpt_start: date, rpt_end: date, req_dt: date, intake_id: Optional[int] = None) -> CreateResult:
    """Create the batch for (period, run date) unless it exists."""
    x = xwalk
    with conn.transaction():
        db.xact_lock(conn, db.seq_key(x.project_cd, x.table_nm, x.src_id, x.run_ty))
        existing = find_batch(conn, x.project_cd, x.table_nm, x.src_id, x.run_ty, rpt_start, rpt_end, req_dt)
        if existing:
            return CreateResult(existing["req_id"], False)
        seq = 1 + conn.execute(
            """SELECT count(*) AS n FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s
                  AND Run_Ty=%s AND Req_Dt_Key=%s""",
            (x.project_cd, x.table_nm, x.src_id, x.run_ty, req_dt)).fetchone()["n"]
        btch = build_btch_id(req_dt, x.project_cd, x.table_nm, x.src_id, x.run_ty, x.cmplnc_vrsn, seq)
        now = clock.now()
        row = conn.execute(
            """INSERT INTO ComplianceRequestControl (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key,
                 Rpt_End_Dt_Key, Req_Dt_Key, Btch_ID, Req_Stat, Created_Dtts, Updated_Dtts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING Req_ID""",
            (x.project_cd, x.table_nm, x.src_id, x.run_ty, rpt_start, rpt_end, req_dt, btch, PENDING, now,
             now)).fetchone()
        logger.batch_event("BATCH_CREATED", req_id=row["req_id"], btch_id=btch, intake_id=intake_id,
                           detail=f"run_date={req_dt} period={rpt_start}..{rpt_end}")
        return CreateResult(row["req_id"], True)


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
    if window := annual_window(name, sched_dt):
        return window
    name, days, weeks = period_lookback(name)
    lookback_days, lookback_weeks = days or lookback_days, weeks or lookback_weeks
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


MAX_STRETCH_DAYS = 366
RESERVED_PARAMS = ("run_date", "project_cd", "run_ty", "table_nm", "src_id")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _config_sql(conn, what: str, sql: str, params: dict, cache: Optional[dict] = None) -> list[dict]:
    """Rows of a configured SELECT; its {name} placeholders are bound as parameters."""
    body = sql.strip().rstrip(";").strip()
    if ";" in body or not re.match(r"^(SELECT|WITH)\b", body, re.IGNORECASE):
        raise ConfigError(f"{what} must be a single SELECT statement")
    names = sorted({n.lower() for n in _PLACEHOLDER.findall(body)})
    unknown = [n for n in names if n not in params]
    if unknown:
        raise ConfigError(f"{what} uses {{{unknown[0]}}}; available: {', '.join('{' + p + '}' for p in params)}")
    values = {n: params[n] for n in names}
    key = (body, tuple(values.items()))
    if cache is not None and key in cache:
        return cache[key]
    query = _PLACEHOLDER.sub(lambda m: f"%({m.group(1).lower()})s", body.replace("%", "%%"))
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            rows = conn.execute(query, values).fetchall()
    except psycopg.Error as e:
        raise ConfigError(f"{what} failed for {params.get('run_date')}: {str(e).strip().splitlines()[0]}") from None
    if cache is not None:
        cache[key] = rows
    return rows


def batch_due(conn, rt: RunType, run_date: date, project_cd: str, cache: Optional[dict] = None) -> Optional[dict]:
    """The row of the run type's Batch_Schedule_Sql_Txt for a run date, or None when no batch is due that day.

    Its columns are available by name to the report date statements of the crosswalk.
    """
    what = f"run type {rt.run_ty}: Batch_Schedule_Sql_Txt"
    rows = _config_sql(conn, what, rt.schedule_sql,
                       {"run_date": run_date, "project_cd": code(project_cd), "run_ty": rt.run_ty}, cache)
    if not rows:
        return None
    if len(rows) > 1:
        raise ConfigError(f"{what} returned {len(rows)} rows for {run_date}; it must return one row on a day "
                          "batches are due and no row on any other day")
    taken = [c for c in rows[0] if c in RESERVED_PARAMS]
    if taken:
        raise ConfigError(f"{what} must not return a column named {taken[0]}")
    return dict(rows[0])


def report_dates(conn, x: XwalkRow, run_date: date, due: dict, cache: Optional[dict] = None) -> Optional[tuple[date, date]]:
    """The report dates of a crosswalk row's batch: its Rpt_Dt_Sql_Txt, else rpt_start / rpt_end of the schedule row.

    None = the statement returned no row: this table has no batch for the run date.
    """
    what = f"Rpt_Dt_Sql_Txt of {x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty}"
    if x.rpt_dt_sql:
        rows = _config_sql(conn, what, x.rpt_dt_sql, {**due, "run_date": run_date, "project_cd": x.project_cd,
                                                      "run_ty": x.run_ty, "table_nm": x.table_nm, "src_id": x.src_id}, cache)
        if not rows:
            return None
        if len(rows) > 1:
            raise ConfigError(f"{what} returned {len(rows)} rows for {run_date}; it must return one row")
        row = rows[0]
        missing = [c for c in ("rpt_start", "rpt_end") if c not in row]
        if missing:
            raise ConfigError(f"{what} must return {', '.join(missing)}")
    elif "rpt_start" in due and "rpt_end" in due:
        row, what = due, f"run type {x.run_ty}: Batch_Schedule_Sql_Txt"
    else:
        raise ConfigError(f"{x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty} has no Rpt_Dt_Sql_Txt")
    start, end = row["rpt_start"], row["rpt_end"]
    if not isinstance(start, date) or not isinstance(end, date) or end < start:
        raise ConfigError(f"{what} returned invalid report dates for {run_date}: rpt_start={start}, rpt_end={end}")
    return start, end


@dataclass
class ScheduleSummary:
    run_date: Optional[date] = None
    created: int = 0
    existing: int = 0
    not_due: int = 0
    errors: list[str] = field(default_factory=list)
    batches: list[str] = field(default_factory=list)


def create_batches(conn: psycopg.Connection, clock: Clock, settings: Settings, *, project_cd: str, run_ty: Optional[str] = None,
                   period: Optional[str] = None, table_nm: Optional[str] = None, period_file: Optional[str] = None,
                   lookback_days: Optional[int] = None, lookback_weeks: Optional[int] = None) -> ScheduleSummary:
    """The scheduled batches due on the run date for the project's effective crosswalk rows.

    The run type's Batch_Schedule_Sql_Txt says whether batches are due on the run date; the crosswalk row's
    Rpt_Dt_Sql_Txt gives its report dates. A stretch of consecutive due days with the same dates gets one
    batch. `period` replaces both for this call: one batch for that period on this run date.
    """
    if period and not period_file:
        check_period(period)
    s = ScheduleSummary(run_date=clock.today(settings.business_tz))
    run_types = cfg.run_types(conn)
    if run_ty is not None:
        rt = run_types.get(code(run_ty))
        if rt is None or not rt.active or rt.run_category_cd != SCHEDULED:
            raise ConfigError(f"run type {run_ty} is unknown, inactive or not {SCHEDULED}")
    logger = EventLogger(conn, clock)
    rows = [x for x in cfg.xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(s.run_date) and x.run_ty in run_types and run_types[x.run_ty].active
            and run_types[x.run_ty].run_category_cd == SCHEDULED]
    if not rows and (run_ty or table_nm):
        s.errors.append(f"no effective crosswalk rows for {project_cd}/{table_nm or '*'}/{run_ty or '*'} on {s.run_date}")
    cache: dict = {}

    def dates_on(x: XwalkRow, day: date) -> Optional[tuple[date, date]]:
        due = batch_due(conn, run_types[x.run_ty], day, x.project_cd, cache)
        return None if due is None else report_dates(conn, x, day, due, cache)

    for x in rows:
        rt = run_types[x.run_ty]
        label = f"{x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty}"
        try:
            if period:
                dates = compute_period(conn, period, s.run_date, lookback_days, lookback_weeks, period_file)
            elif rt.schedule_sql:
                dates = dates_on(x, s.run_date)
                if dates is None:
                    s.not_due += 1
                    s.batches.append(f"{label}: not due")
                    continue
                if _already_created(conn, x, dates, s.run_date, lambda day: dates_on(x, day)):
                    s.existing += 1
                    s.batches.append(f"{label} {dates[0]}..{dates[1]}: exists")
                    continue
            elif run_ty is None:
                s.batches.append(f"{label}: run type {rt.run_ty} has no Batch_Schedule_Sql_Txt")
                continue
            else:
                raise ConfigError(f"run type {rt.run_ty} has no Batch_Schedule_Sql_Txt; set it or pass --period")
        except ConfigError as e:
            s.errors.append(f"{label}: {e}")
            continue
        res = create_batch(conn, clock, logger, xwalk=x, rpt_start=dates[0], rpt_end=dates[1], req_dt=s.run_date)
        if res.created:
            s.created += 1
        else:
            s.existing += 1
        s.batches.append(f"{label} {dates[0]}..{dates[1]}: {'created' if res.created else 'exists'}")
    return s


def _already_created(conn, x: XwalkRow, dates: tuple, run_date: date, period_on) -> bool:
    """Whether this stretch of consecutive due days (same report dates) already has its batch."""
    made = [r["req_dt_key"] for r in conn.execute(
        """SELECT Req_Dt_Key FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s
              AND Run_Ty=%s AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s AND Req_Dt_Key <= %s""",
        (x.project_cd, x.table_nm, x.src_id, x.run_ty, dates[0], dates[1], run_date)).fetchall()]
    if not made:
        return False
    last = max(made)
    if (run_date - last).days > MAX_STRETCH_DAYS:
        return False
    return all(period_on(last + timedelta(days=n)) == dates for n in range(1, (run_date - last).days))


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

    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.logger = EventLogger(conn, clock)

    def run(self, project_cd: Optional[str] = None, run_ty: Optional[str] = None) -> IntakeSummary:
        """Sweep the requests due today."""
        summary = IntakeSummary(run_date=self.clock.today(self.settings.business_tz))
        run_types: Optional[dict[str, RunType]] = None
        while True:
            current = None
            try:
                with self.conn.transaction():
                    row = self.conn.execute(
                        """SELECT * FROM ComplianceRequestInTake
                            WHERE %(d)s BETWEEN Req_Start_Dt_Key AND Req_End_Dt_Key
                              AND Last_Run_Dt_Key IS DISTINCT FROM %(d)s
                              AND (%(p)s::text IS NULL OR UPPER(Project_Cd) = %(p)s) AND (%(r)s::text IS NULL OR UPPER(Run_Ty) = %(r)s)
                            ORDER BY Created_Dtts, Intake_ID LIMIT 1 FOR UPDATE SKIP LOCKED""",
                        {"d": summary.run_date, "p": code(project_cd), "r": code(run_ty)}).fetchone()
                    if row is None:
                        return summary
                    current = row["intake_id"]
                    summary.handled += 1
                    if run_types is None:
                        run_types = cfg.run_types(self.conn)
                    self._process(_coded(row), summary, run_types)
            except Exception as e:
                if current is None:
                    raise
                log.exception("intake %s failed", current)
                with self.conn.transaction():
                    self._mark_run(current)
                    self.logger.audit("INTAKE_FAILED", intake_id=current, description=f"technical error: {e}")
                summary.failed += 1

    def _process(self, it: dict, summary: IntakeSummary, run_types: dict[str, RunType]) -> None:
        self._mark_run(it["intake_id"])
        rt = run_types.get(it["run_ty"])
        if rt is None or not rt.active or rt.run_category_cd != ADHOC:
            self._fail(it, summary, f"run type {it['run_ty']} is unknown, inactive or not ADHOC")
            return
        sources = cfg.effective_sources(self.conn, it["project_cd"], it["table_nm"], it["run_ty"],
                                        it["rpt_start_dt_key"])
        targets = [x for x in sources if it["src_id"] is None or x.src_id == it["src_id"]]
        if not targets:
            self._fail(it, summary, "no active, effective crosswalk source for this request")
            return
        created = sum(create_batch(self.conn, self.clock, self.logger, xwalk=x, rpt_start=it["rpt_start_dt_key"],
                                   rpt_end=it["rpt_end_dt_key"], req_dt=summary.run_date,
                                   intake_id=it["intake_id"]).created for x in targets)
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


def _coded(intake: dict) -> dict:
    """An intake request with its codes in the framework's case."""
    return {**intake, **{k: code(intake[k]) for k in ("project_cd", "table_nm", "src_id", "run_ty")}}


KNOWN_PERIODS = frozenset(PERIOD_SQL)


def check_period(spec: str) -> None:
    """Raise ConfigError when a period passed to a run is not a built-in one."""
    if annual_window(spec, date.today()):
        return
    name = period_lookback(spec)[0]
    if code(name) not in KNOWN_PERIODS:
        raise ConfigError(f"unknown period {spec!r}; built-in: {', '.join(sorted(KNOWN_PERIODS))}, ANNUAL_WINDOW(...)")
