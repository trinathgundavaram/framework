"""Batch creation (scheduled and ad-hoc) and batch rows."""
from __future__ import annotations

import importlib.util
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from . import config as cfg
from . import db
from .audit import EventLogger
from .common import (ADHOC, REQUEST_CREATED, SCHEDULED, Clock, ConfigError, annual_window, build_btch_id, code,
                     period_lookback)
from .config import RunType, XwalkRow
from .db import Connection
from .settings import Settings

log = logging.getLogger(__name__)

DATA_EVENTS = ("FILE_PROMOTED", "LATE_ARRIVAL_PROMOTED", "CORRECTION_PROMOTED", "CARRY_FORWARD_APPLIED")
REOPENED_AWAITING_DATA = f"""EXISTS (SELECT 1 FROM ComplianceRequestFileDetail o WHERE o.Req_ID = c.Req_ID
      AND o.Event_Ty = 'BATCH_REOPENED' AND NOT EXISTS (SELECT 1 FROM ComplianceRequestFileDetail n
            WHERE n.Req_ID = o.Req_ID AND n.Detail_ID > o.Detail_ID
              AND n.Event_Ty IN ({", ".join(f"'{e}'" for e in DATA_EVENTS)})))"""


@dataclass
class CreateResult:
    req_id: int
    created: bool


def find_batch(conn, project_cd, table_nm, src_id, run_ty, rpt_start: date, rpt_end: date, req_dt: date) -> Optional[dict]:
    return conn.execute(
        """SELECT * FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s AND Run_Ty=%s
              AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s AND Req_Dt_Key=%s""",
        (project_cd, table_nm, src_id, run_ty, rpt_start, rpt_end, req_dt)).fetchone()


def scheduled(rt: Optional[RunType], x: XwalkRow) -> bool:
    """Whether the schedule creates this crosswalk row's batches: every row of a SCHEDULED run type, and the rows of
    an ADHOC run type that has a schedule when the row has its own Rpt_Period_Cd / Rpt_Dt_Sql_Txt (its other rows
    get batches from ComplianceRequestInTake only)."""
    if rt is None or not rt.active:
        return False
    return rt.run_category_cd == SCHEDULED or bool(rt.run_category_cd == ADHOC and rt.has_schedule
                                                    and (x.rpt_dt_sql or x.rpt_period_cd))


def open_batch_of_other(conn, x: XwalkRow, rpt_start: date, rpt_end: date, intake_id: int) -> Optional[dict]:
    """An open batch for these report dates that this intake request did not create itself."""
    return conn.execute(
        """SELECT c.* FROM ComplianceRequestControl c WHERE c.Project_Cd=%s AND c.Table_Nm=%s AND c.Src_ID=%s
              AND c.Run_Ty=%s AND c.Rpt_Start_Dt_Key=%s AND c.Rpt_End_Dt_Key=%s AND c.Batch_Close_Ind = 0
              AND NOT EXISTS (SELECT 1 FROM ComplianceRequestFileDetail d WHERE d.Req_ID = c.Req_ID
                                AND d.Event_Ty = 'BATCH_CREATED' AND d.Intake_ID = %s)
            ORDER BY c.Req_ID DESC""",
        (x.project_cd, x.table_nm, x.src_id, x.run_ty, rpt_start, rpt_end, intake_id)).fetchone()


def get_batch(conn: Connection, req_id: int) -> dict:
    """The batch row; callers that change it hold the batch lock."""
    return conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s", (req_id,)).fetchone()


def promoted_load(conn: Connection, req_id: int) -> Optional[dict]:
    """The batch's PROMOTED load, if any."""
    return conn.execute("SELECT * FROM ComplianceFileLoad WHERE Req_ID=%s AND Load_Stat='PROMOTED'",
                        (req_id,)).fetchone()


def create_batch(conn: Connection, clock: Clock, logger: EventLogger, *, xwalk: XwalkRow,
                 rpt_start: date, rpt_end: date, req_dt: date, intake_id: Optional[int] = None,
                 lock_timeout: Optional[float] = 300, locked: bool = False) -> CreateResult:
    """Create the batch for (period, run date) unless it exists; `locked` = the caller holds the sequence lock."""
    x = xwalk
    if locked:
        return _create_batch(conn, clock, logger, x, rpt_start, rpt_end, req_dt, intake_id)
    with db.held(conn, db.seq_key(x.project_cd, x.table_nm, x.run_ty), lock_timeout):
        with conn.transaction():
            return _create_batch(conn, clock, logger, x, rpt_start, rpt_end, req_dt, intake_id)


def _create_batch(conn, clock, logger, x: XwalkRow, rpt_start, rpt_end, req_dt, intake_id) -> CreateResult:
    existing = find_batch(conn, x.project_cd, x.table_nm, x.src_id, x.run_ty, rpt_start, rpt_end, req_dt)
    if existing:
        return CreateResult(existing["req_id"], False)
    btch = table_batch_id(conn, x, rpt_start, rpt_end, req_dt)
    now = clock.now()
    conn.execute(
        """INSERT INTO ComplianceRequestControl (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key,
             Rpt_End_Dt_Key, Req_Dt_Key, Btch_ID, Cmplnc_Vrsn, Req_Stat, Batch_Close_Ind, Created_Dtts, Updated_Dtts)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s)""",
        (x.project_cd, x.table_nm, x.src_id, x.run_ty, rpt_start, rpt_end, req_dt, btch, x.cmplnc_vrsn,
         REQUEST_CREATED, now, now))
    req_id = conn.execute("SELECT Req_ID FROM ComplianceRequestControl WHERE Btch_ID=%s AND Src_ID=%s",
                          (btch, x.src_id)).fetchone()["req_id"]
    logger.batch_event("BATCH_CREATED", req_id=req_id, btch_id=btch, intake_id=intake_id,
                       detail=f"run_date={req_dt} period={rpt_start}..{rpt_end}")
    return CreateResult(req_id, True)


def table_batch_id(conn, x: XwalkRow, rpt_start: date, rpt_end: date, req_dt: date) -> str:
    """The table-level batch of these report dates on this request date: the one other sources already have, else
    the next free one (no suffix for the first report period of the day, then _2, _3, ...). The caller holds the
    table's sequence lock."""
    same = conn.execute(
        """SELECT DISTINCT Btch_ID, Rpt_Start_Dt_Key, Rpt_End_Dt_Key FROM ComplianceRequestControl
            WHERE Project_Cd=%s AND Table_Nm=%s AND Run_Ty=%s AND Req_Dt_Key=%s""",
        (x.project_cd, x.table_nm, x.run_ty, req_dt)).fetchall()
    for r in same:
        if (db.as_date(r["rpt_start_dt_key"]), db.as_date(r["rpt_end_dt_key"])) == (rpt_start, rpt_end):
            return r["btch_id"]
    taken = {r["btch_id"] for r in same}
    seq = 1
    while build_btch_id(req_dt, x.project_cd, x.table_nm, x.run_ty, seq) in taken:
        seq += 1
    return build_btch_id(req_dt, x.project_cd, x.table_nm, x.run_ty, seq)


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
    """Report period for a run date: a built-in name, an ANNUAL_WINDOW(...), or a statement from a period file."""
    if window := annual_window(name, sched_dt):
        return window
    name, days, weeks = period_lookback(name)
    lookback_days, lookback_weeks = days or lookback_days, weeks or lookback_weeks
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
    order = [n.lower() for n in _PLACEHOLDER.findall(body)]
    try:
        rows = conn.execute_qmark(_PLACEHOLDER.sub("?", body), [params[n] for n in order]).fetchall()
    except Exception as e:  # noqa: BLE001
        raise ConfigError(f"{what} failed for {params.get('run_date')}: {str(e).strip().splitlines()[0]}") from None
    if cache is not None:
        cache[key] = rows
    return rows


WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
SCHED_CODES = ("DAILY", "WEEKLY", "MONTHLY")


def _scheduled_on(rt: RunType, d: date) -> bool:
    """Whether the run type's schedule codes name this date."""
    if rt.sched_cd == "DAILY":
        return True
    if rt.sched_cd == "WEEKLY":
        return WEEKDAYS[d.weekday()] == rt.sched_day
    if rt.sched_months and d.month not in rt.sched_months:
        return False
    last = (_month_start(d, 1) - timedelta(days=1)).day
    if rt.sched_day in WEEKDAYS:
        if WEEKDAYS[d.weekday()] != rt.sched_day:
            return False
        return d.day > last - 7 if rt.sched_week == "LAST" else (d.day - 1) // 7 + 1 == int(rt.sched_week)
    return d.day == (last if rt.sched_day == "LAST" else min(int(rt.sched_day), last))


def check_schedule(rt: RunType) -> Optional[str]:
    """What is wrong with a run type's schedule codes, or None."""
    if not rt.sched_cd:
        return None
    if rt.sched_cd not in SCHED_CODES:
        return f"Sched_Cd must be one of {', '.join(SCHED_CODES)}"
    if rt.sched_cd == "WEEKLY" and rt.sched_day not in WEEKDAYS:
        return f"Sched_Day_Cd of a WEEKLY schedule must be one of {', '.join(WEEKDAYS)}"
    if rt.sched_cd == "MONTHLY":
        if rt.sched_day in WEEKDAYS:
            if rt.sched_week not in ("1", "2", "3", "4", "LAST"):
                return "a MONTHLY schedule on a weekday needs Sched_Week_Cd 1, 2, 3, 4 or LAST"
        elif not (rt.sched_day == "LAST" or (rt.sched_day or "").isdigit() and 1 <= int(rt.sched_day) <= 31):
            return "Sched_Day_Cd of a MONTHLY schedule must be 1 to 31, LAST or a weekday (MON ... SUN)"
        if any(not 1 <= m <= 12 for m in rt.sched_months):
            return "Sched_Months_Txt must list months 1 to 12"
    if rt.sched_window_days < 1:
        return "Sched_Window_Days must be 1 or more"
    return None


def batch_due(conn, rt: RunType, run_date: date, project_cd: str, cache: Optional[dict] = None) -> Optional[dict]:
    """Whether a batch of the run type is due on a run date: None, or a row whose period_dt is the scheduled date.

    With schedule codes the batch is due on its scheduled date and the Sched_Window_Days - 1 days after it (a missed
    day still gets it; same report dates, so one batch). Otherwise the row of Batch_Schedule_Sql_Txt; its columns
    are available by name to the report date statements of the crosswalk.
    """
    if rt.sched_cd:
        problem = check_schedule(rt)
        if problem:
            raise ConfigError(f"run type {rt.run_ty}: {problem}")
        for back in range(rt.sched_window_days):
            day = run_date - timedelta(days=back)
            if _scheduled_on(rt, day):
                return {"period_dt": day}
        return None
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
    if x.rpt_period_cd:
        return compute_period(conn, x.rpt_period_cd, db.as_date(due.get("period_dt")) or run_date)
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
        raise ConfigError(f"{x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty} has no Rpt_Period_Cd or Rpt_Dt_Sql_Txt")
    start, end = db.as_date(row["rpt_start"]), db.as_date(row["rpt_end"])
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


def create_batches(conn: Connection, clock: Clock, settings: Settings, *, project_cd: str, run_ty: Optional[str] = None,
                   period: Optional[str] = None, table_nm: Optional[str] = None, period_file: Optional[str] = None,
                   lookback_days: Optional[int] = None, lookback_weeks: Optional[int] = None,
                   tables: Optional[tuple] = None) -> ScheduleSummary:
    """The scheduled batches due on the run date for the project's effective crosswalk rows.

    The run type's schedule (Sched_Cd ..., else Batch_Schedule_Sql_Txt) says whether batches are due on the run date;
    the crosswalk row's Rpt_Period_Cd (else Rpt_Dt_Sql_Txt) gives its report dates (rows of ADHOC run types only when
    they have both, see `scheduled`). Then the ComplianceBatchCalendar rows of the run date. A stretch of consecutive due days with the same dates gets one
    batch. `period` replaces both for this call: one batch for that period on this run date.
    """
    if period and not period_file:
        check_period(period)
    s = ScheduleSummary(run_date=clock.today(settings.business_tz))
    run_types = cfg.run_types(conn)
    if run_ty is not None:
        rt = run_types.get(code(run_ty))
        if rt is None or not rt.active or not (rt.run_category_cd == SCHEDULED or rt.has_schedule):
            raise ConfigError(f"run type {run_ty} is unknown, inactive, or {ADHOC} without a schedule")
    logger = EventLogger(conn, clock)
    rows = [x for x in cfg.xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(s.run_date) and scheduled(run_types.get(x.run_ty), x)
            and (tables is None or x.table_nm in tables)]
    if not rows and (run_ty or table_nm or tables is not None):
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
            elif rt.has_schedule:
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
                s.batches.append(f"{label}: run type {rt.run_ty} has no schedule")
                continue
            else:
                raise ConfigError(f"run type {rt.run_ty} has no schedule (Sched_Cd or Batch_Schedule_Sql_Txt); set one "
                                  "or pass --period")
        except ConfigError as e:
            s.errors.append(f"{label}: {e}")
            continue
        res = create_batch(conn, clock, logger, xwalk=x, rpt_start=dates[0], rpt_end=dates[1], req_dt=s.run_date,
                           lock_timeout=settings.lock_timeout_seconds)
        if res.created:
            s.created += 1
        else:
            s.existing += 1
        s.batches.append(f"{label} {dates[0]}..{dates[1]}: {'created' if res.created else 'exists'}")
    if not period:
        _from_calendar(conn, clock, settings, logger, s, project_cd, run_ty, table_nm, tables, run_types)
    return s


def _from_calendar(conn, clock, settings, logger, s, project_cd, run_ty, table_nm, tables, run_types) -> None:
    """The batches ComplianceBatchCalendar lists for the run date: every effective crosswalk source of the run type
    (and table, when the row names one) gets a batch for the row's report dates."""
    for c in cfg.calendar_rows(conn, project_cd, s.run_date):
        rt = run_types.get(c.run_ty)
        if (run_ty and c.run_ty != code(run_ty)) or rt is None or not rt.active:
            continue
        for x in cfg.effective_sources(conn, c.project_cd, None if c.table_nm == "*" else c.table_nm, c.run_ty, s.run_date):
            if (table_nm and x.table_nm != code(table_nm)) or (tables is not None and x.table_nm not in tables):
                continue
            label = f"{x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty}"
            res = create_batch(conn, clock, logger, xwalk=x, rpt_start=c.rpt_start, rpt_end=c.rpt_end,
                               req_dt=s.run_date, lock_timeout=settings.lock_timeout_seconds)
            if res.created:
                s.created += 1
            else:
                s.existing += 1
            s.batches.append(f"{label} {c.rpt_start}..{c.rpt_end}: {'created' if res.created else 'exists'} (calendar)")


def _already_created(conn, x: XwalkRow, dates: tuple, run_date: date, period_on) -> bool:
    """Whether this stretch of consecutive due days (same report dates) already has its batch."""
    made = [db.as_date(r["req_dt_key"]) for r in conn.execute(
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
    """ComplianceRequestInTake: requests for batches of given report dates, for any active run type (ADHOC, or a
    SCHEDULED one outside its schedule). A request reuses an open batch of the same source, run type and report dates
    that something else created; its own batches on the days of its window are new each day."""

    def __init__(self, conn: Connection, clock: Clock, settings: Settings):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.logger = EventLogger(conn, clock)

    def run(self, project_cd: Optional[str] = None, run_ty: Optional[str] = None,
            tables: Optional[tuple] = None) -> IntakeSummary:
        """Sweep the requests due today; each request is claimed with its own lock."""
        summary = IntakeSummary(run_date=self.clock.today(self.settings.business_tz))
        where = ["%s BETWEEN Req_Start_Dt_Key AND Req_End_Dt_Key", "(Last_Run_Dt_Key IS NULL OR Last_Run_Dt_Key <> %s)"]
        params: list = [summary.run_date, summary.run_date]
        for col, val in (("Project_Cd", project_cd), ("Run_Ty", run_ty)):
            if val is not None:
                where.append(f"UPPER({col}) = %s")
                params.append(code(val))
        if tables is not None:
            marks, names = db.in_list(tables or ("",))
            where.append(f"UPPER(Table_Nm) IN ({marks})")
            params.extend(names)
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
                    self._process(_coded(it), summary, run_types)
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
        if rt is None or not rt.active:
            reason = f"run type {it['run_ty']} is unknown or inactive"
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
        keys = sorted({db.seq_key(x.project_cd, x.table_nm, x.run_ty) for x in targets})
        with db.held_all(self.conn, keys, self.settings.lock_timeout_seconds):
            with self.conn.transaction():
                self._mark_run(it["intake_id"])
                created, reused = 0, []
                for x in targets:
                    other = open_batch_of_other(self.conn, x, rpt_start, rpt_end, it["intake_id"])
                    if other is not None:
                        reused.append(other["btch_id"])
                        continue
                    created += create_batch(self.conn, self.clock, self.logger, xwalk=x, rpt_start=rpt_start,
                                            rpt_end=rpt_end, req_dt=summary.run_date, intake_id=it["intake_id"],
                                            locked=True).created
        summary.created += created
        summary.existing += len(targets) - created
        summary.details.append(f"{it['intake_id']}: created={created} existing={len(targets) - created}"
                               + (f" (open batch used: {', '.join(reused)})" if reused else ""))

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


KNOWN_PERIODS = frozenset(PERIODS)


def check_period(spec: str) -> None:
    """Raise ConfigError when a period passed to a run is not a built-in one."""
    if annual_window(spec, date.today()):
        return
    name = period_lookback(spec)[0]
    if code(name) not in KNOWN_PERIODS:
        raise ConfigError(f"unknown period {spec!r}; built-in: {', '.join(sorted(KNOWN_PERIODS))}, ANNUAL_WINDOW(...)")
