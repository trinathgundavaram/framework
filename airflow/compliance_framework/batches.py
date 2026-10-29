"""Batch life cycle: creation (schedule, calendar, intake), release / hold, overrides, close."""
from __future__ import annotations

import importlib.util
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from compliance_framework import config as cfg, config as cfgmod, db
from compliance_framework.audit import EventLogger
from compliance_framework.common import (ADHOC, Clock, CloseBlocked, CloseDeferred, ConfigError,
    DATA_NOT_PROVIDED, OPEN_STATUSES, REOPEN_STATUS, REQUEST_COMPLETE, REQUEST_CREATED, SCHEDULED,
    SOURCE_DATA_CARRY_FORWARD, annual_window, build_btch_id, check_transition, code, earliest_close_date,
    period_lookback)
from compliance_framework.config import RunType, Scope, XwalkRow
from compliance_framework.db import Connection, Settings


# batches: Batch creation (scheduled and ad-hoc) and batch rows.

log = logging.getLogger(__name__)

DATA_EVENTS = ("FILE_LOADED", "LATE_ARRIVAL_LOADED", "CORRECTION_LOADED", "CARRY_FORWARD_APPLIED")
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


HAS_DATA = ("NEW_FILE", "CARRY_FORWARD")


def has_data(conn: Connection, batch: dict) -> bool:
    """Whether a batch row holds data: its own file / pull with rows, or carried-forward data. An empty file or pull
    (NO_DATA) is no data, so a late file takes LATE_ARRIVAL and a REUSE applies. Without Resolution_Ty: its load."""
    if batch.get("resolution_ty"):
        return batch["resolution_ty"] in HAS_DATA
    return current_load(conn, batch["req_id"]) is not None


def current_load(conn: Connection, req_id: int) -> Optional[dict]:
    """The batch's current load (FILE_LOADED), if any."""
    return conn.execute("SELECT * FROM ComplianceFileLoad WHERE Req_ID=%s AND Load_Stat='FILE_LOADED'",
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
             Rpt_End_Dt_Key, Req_Dt_Key, Btch_ID, Cmplnc_Vrsn, Req_Stat, Batch_Close_Ind, Load_Dtts, LastUpdt_Dtts)
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


_RANGE = re.compile(r"^RANGE\(\s*(DAY|WEEK|MONTH|QUARTER|YEAR)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", re.IGNORECASE)
RANGE_MONTHS = {"MONTH": 1, "QUARTER": 3, "YEAR": 12}


def range_period(spec: str, d: date) -> Optional[tuple[date, date]]:
    """RANGE(unit, back, length): `length` units starting `back` units before D's unit (DAY, WEEK Mon-Sun, MONTH,
    QUARTER, YEAR). RANGE(DAY,1,1) = the day before; RANGE(WEEK,5,1) = the week 5 weeks back; RANGE(MONTH,3,3) =
    the 3 months ending last month. None when `spec` is not a RANGE."""
    if not (spec or "").strip().upper().startswith("RANGE"):
        return None
    m = _RANGE.match(spec.strip())
    if not m or int(m.group(3)) < 1:
        raise ConfigError(f"period {spec!r} must be RANGE(DAY|WEEK|MONTH|QUARTER|YEAR, back, length) with length >= 1")
    unit, back, length = m.group(1).upper(), int(m.group(2)), int(m.group(3))
    if unit in ("DAY", "WEEK"):
        days = 1 if unit == "DAY" else 7
        start = (d if unit == "DAY" else d - timedelta(days=d.weekday())) - timedelta(days=days * back)
        return start, start + timedelta(days=days * length - 1)
    months = RANGE_MONTHS[unit]
    base = _month_start(d) if unit == "MONTH" else _quarter_start(d) if unit == "QUARTER" else date(d.year, 1, 1)
    start = _month_start(base, -months * back)
    return start, _month_start(start, months * length) - timedelta(days=1)


EDGE_MAX_DAYS = {"WEEK": 7, "MONTH": 28, "QUARTER": 90, "YEAR": 365}
_EDGE_DAYS = re.compile(r"^(FIRST|LAST)_DAYS\(\s*(WEEK|MONTH|QUARTER|YEAR)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", re.IGNORECASE)


def edge_days_period(spec: str, d: date) -> Optional[tuple[date, date]]:
    """FIRST_DAYS(unit, back, n) / LAST_DAYS(unit, back, n): the first / last n days of the unit `back` units before
    D's unit. LAST_DAYS(MONTH,1,14) on 2026-07-05 = 06-17 to 06-30. None when `spec` is neither."""
    head = (spec or "").strip().upper()
    if not (head.startswith("FIRST_DAYS") or head.startswith("LAST_DAYS")):
        return None
    m = _EDGE_DAYS.match(spec.strip())
    if not m or int(m.group(4)) < 1:
        raise ConfigError(f"period {spec!r} must be FIRST_DAYS / LAST_DAYS(WEEK|MONTH|QUARTER|YEAR, back, days) with days >= 1")
    edge, unit, back, n = m.group(1).upper(), m.group(2).upper(), m.group(3), int(m.group(4))
    if n > EDGE_MAX_DAYS[unit]:
        raise ConfigError(f"period {spec!r}: at most {EDGE_MAX_DAYS[unit]} days (the shortest {unit.lower()})")
    start, end = range_period(f"RANGE({unit},{back},1)", d)
    return (start, start + timedelta(days=n - 1)) if edge == "FIRST" else (end - timedelta(days=n - 1), end)


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
    if window := annual_window(name, sched_dt) or range_period(name, sched_dt) or edge_days_period(name, sched_dt):
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
    db.log_sql(what, body, params)
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
            return "Sched_Months_Txt must list months 1 to 12, comma separated (e.g. 1,4,7,10)"
    if rt.sched_window_days < 1:
        return "Sched_Window_Days must be 1 or more"
    if abs(rt.sched_offset_days) > 31:
        return "Sched_Offset_Days must be between -31 and 31"
    return None


def batch_due(conn, rt: RunType, run_date: date, project_cd: str, cache: Optional[dict] = None) -> Optional[dict]:
    """Whether a batch of the run type is due on a run date: None, or a row whose period_dt is the scheduled date.

    With schedule codes the batch is due on its scheduled date (the date the codes name, moved by Sched_Offset_Days:
    -2 = two days before) and the Sched_Window_Days - 1 days after it (a missed day still gets it; same report
    dates, so one batch). Otherwise the row of Batch_Schedule_Sql_Txt; its columns
    are available by name to the report date statements of the crosswalk.
    """
    if rt.sched_cd:
        problem = check_schedule(rt)
        if problem:
            raise ConfigError(f"run type {rt.run_ty}: {problem}")
        for back in range(rt.sched_window_days):
            day = run_date - timedelta(days=back)
            if _scheduled_on(rt, day - timedelta(days=rt.sched_offset_days)):
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
                   tables: Optional[tuple] = None, sources: Optional[tuple] = None) -> ScheduleSummary:
    """The scheduled batches due on the run date for the project's effective crosswalk rows (of `sources` when given).

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
            and (tables is None or x.table_nm in tables) and (sources is None or x.src_id in sources)]
    if not rows and (run_ty or table_nm or tables is not None or sources is not None):
        shown = table_nm or (",".join(tables) if tables else "*")
        s.errors.append(f"no effective crosswalk rows for {project_cd}/{shown}/{run_ty or '*'} on {s.run_date}")
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
                if (_made_since(conn, x, dates, batch_due(conn, rt, s.run_date, x.project_cd, cache)["period_dt"], s.run_date)
                        if rt.sched_cd else _already_created(conn, x, dates, s.run_date, lambda day: dates_on(x, day))):
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
        _from_calendar(conn, clock, settings, logger, s, project_cd, run_ty, table_nm, tables, run_types, sources)
    return s


def _from_calendar(conn, clock, settings, logger, s, project_cd, run_ty, table_nm, tables, run_types,
                   sources=None) -> None:
    """The batches ComplianceBatchCalendar lists for the run date: every effective crosswalk source of the run type
    (and table, when the row names one) gets a batch for the row's report dates."""
    for c in cfg.calendar_rows(conn, project_cd, s.run_date):
        rt = run_types.get(c.run_ty)
        if (run_ty and c.run_ty != code(run_ty)) or rt is None or not rt.active:
            continue
        for x in cfg.effective_sources(conn, c.project_cd, None if c.table_nm == "*" else c.table_nm, c.run_ty, s.run_date):
            if ((table_nm and x.table_nm != code(table_nm)) or (tables is not None and x.table_nm not in tables)
                    or (sources is not None and x.src_id not in sources)):
                continue
            label = f"{x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty}"
            res = create_batch(conn, clock, logger, xwalk=x, rpt_start=c.rpt_start, rpt_end=c.rpt_end,
                               req_dt=s.run_date, lock_timeout=settings.lock_timeout_seconds)
            if res.created:
                s.created += 1
            else:
                s.existing += 1
            s.batches.append(f"{label} {c.rpt_start}..{c.rpt_end}: {'created' if res.created else 'exists'} (calendar)")


def _made_since(conn, x: XwalkRow, dates: tuple, scheduled: date, run_date: date) -> bool:
    """Whether the batch of this scheduled date exists (made on it or a later day of its window): with schedule codes
    every scheduled date gets its own batch, also when its report dates are the previous date's (e.g. DAILY with
    fixed report dates)."""
    return conn.execute(
        """SELECT 1 AS ok FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s AND Run_Ty=%s
              AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s AND Req_Dt_Key BETWEEN %s AND %s""",
        (x.project_cd, x.table_nm, x.src_id, x.run_ty, dates[0], dates[1], db.as_date(scheduled), run_date)).fetchone() is not None


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
    SCHEDULED one outside its schedule). A request stands on its own: its source and compliance version are on the
    row, no crosswalk row is needed. It reuses an open batch of the same source, run type and report dates that
    something else created; its own batches on the days of its window are new each day."""

    def __init__(self, conn: Connection, clock: Clock, settings: Settings):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.logger = EventLogger(conn, clock)

    def run(self, project_cd: Optional[str] = None, run_ty: Optional[str] = None,
            tables: Optional[tuple] = None, sources: Optional[tuple] = None) -> IntakeSummary:
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
        if sources is not None:
            marks, names = db.in_list(sources)
            where.append(f"UPPER(Src_ID) IN ({marks})")
            params.extend(names)
        due = self.conn.execute(f"SELECT Intake_ID FROM ComplianceRequestInTake WHERE {' AND '.join(where)} "
                                "ORDER BY Load_Dtts, Intake_ID", params).fetchall()
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
        rpt_start, rpt_end = db.as_date(it["rpt_start_dt_key"]), db.as_date(it["rpt_end_dt_key"])
        reason = intake_problem(self.conn, it, run_types, self.settings)
        targets: list[XwalkRow] = [] if reason else [XwalkRow(
            it["project_cd"], it["table_nm"], it["src_id"], it["run_ty"], rpt_start, None,
            code(_text(it.get("cmplnc_vrsn"))), True)]
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
        self.conn.execute("UPDATE ComplianceRequestInTake SET Last_Run_Dt_Key=%s, LastUpdt_Dtts=%s, LastUpdt_ID=USER WHERE Intake_ID=%s",
                          (self.clock.today(self.settings.business_tz), self.clock.now(), intake_id))


def _text(value) -> Optional[str]:
    return value.strip() or None if isinstance(value, str) else None


def intake_problem(conn, it: dict, run_types: dict, settings=None) -> Optional[str]:
    """Why an intake request cannot make its batch, or None. A request stands on its own (no crosswalk row): it names
    its source (Src_ID) and its compliance version (Cmplnc_Vrsn); the source needs a file or internal source config."""
    rt = run_types.get(code(it["run_ty"]))
    if rt is None or not rt.active:
        return f"run type {it['run_ty']} is unknown or inactive"
    if not _text(it.get("src_id")):
        return "Src_ID is required (one request per source)"
    if not _text(it.get("cmplnc_vrsn")):
        return "Cmplnc_Vrsn is required (the compliance version of the batch)"
    if db.as_date(it["rpt_end_dt_key"]) < db.as_date(it["rpt_start_dt_key"]):
        return "Rpt_End_Dt_Key is before Rpt_Start_Dt_Key"
    project = conn.execute("SELECT Active_Ind FROM ComplianceProject WHERE UPPER(Project_Cd) = %s",
                           (code(it["project_cd"]),)).fetchone()
    if project is None or project["active_ind"] != 1:
        return f"project {it['project_cd']} is unknown or inactive"
    source = conn.execute("SELECT Active_Ind FROM ComplianceSourceSystem WHERE UPPER(Src_ID) = %s",
                          (code(it["src_id"]),)).fetchone()
    if source is None or source["active_ind"] != 1:
        return f"source {it['src_id']} is unknown or inactive"
    if cfg.target_tables(conn, it["project_cd"], it["table_nm"], it["src_id"], settings) is None:
        return (f"source {it['src_id']} has no active file config or internal source config for table "
                f"{it['table_nm']}, so no data could arrive")
    return None


def _coded(intake: dict) -> dict:
    """An intake request with its codes in the framework's case."""
    return {**intake, **{k: code(intake[k]) for k in ("project_cd", "table_nm", "src_id", "run_ty")}}


KNOWN_PERIODS = frozenset(PERIODS)


def check_period(spec: str) -> None:
    """Raise ConfigError when a period passed to a run is not a built-in one."""
    if annual_window(spec, date.today()) or range_period(spec, date.today()) or edge_days_period(spec, date.today()):
        return
    name = period_lookback(spec)[0]
    if code(name) not in KNOWN_PERIODS:
        raise ConfigError(f"unknown period {spec!r}; built-in: {', '.join(sorted(KNOWN_PERIODS))}, ANNUAL_WINDOW(...), "
                          "RANGE(unit,back,length), FIRST_DAYS / LAST_DAYS(unit,back,days)")


# release: When a batch's data may be pulled from an internal source, exported and closed.

RELEASED = "BATCH_RELEASED"
TRUE = ("TRUE", "YES", "Y", "1")


def wanted(value) -> bool:
    return str(value or "").strip().upper() in TRUE


def run_type_list(value) -> tuple:
    """The run types of a `hold` input ('WEEKLY, MONTHLY' or a list)."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").strip("[] ").split(",")
    return tuple(code(str(v).strip(" '\"")) for v in items if str(v).strip(" '\""))


def release_time(value: Optional[str]) -> Optional[str]:
    """RELEASE_TIME as HH:MM (None = from the start of the due date)."""
    if not value:
        return None
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(value).strip())
    if not m:
        raise ConfigError(f"RELEASE_TIME must be HH:MM (24 hours), got {value!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def released(conn, req_ids: Iterable[int]) -> set:
    """The batches with a BATCH_RELEASED event."""
    ids, out = sorted(set(req_ids)), set()
    for i in range(0, len(ids), 500):
        marks, chunk = db.in_list(ids[i:i + 500])
        out.update(r["req_id"] for r in conn.execute(
            f"SELECT DISTINCT Req_ID FROM ComplianceRequestFileDetail WHERE Event_Ty = %s AND Req_ID IN ({marks})",
            [RELEASED, *chunk]).fetchall())
    return out


class Releases:
    def __init__(self, conn, clock, settings, hold: Iterable[str] = ()):
        self.conn, self.clock = conn, clock
        self.today = clock.today(settings.business_tz)
        self.now_hhmm = clock.now().astimezone(ZoneInfo(settings.business_tz)).strftime("%H:%M")
        self.release_time = release_time(settings.release_time)
        self.run_types = cfgmod.run_types(conn)
        self.on_hold = set(hold)
        self.logger = EventLogger(conn, clock)

    def hold(self, b: dict, done: set, due: bool = True) -> Optional[str]:
        """None when the batch may go now, else until when it waits; due=False: the batch does not wait for its due
        date (SOURCE_LOAD pulls from the request date on). Ad hoc run types go like scheduled ones; a release only
        lets BATCH_CLOSE close them."""
        if b["req_id"] in done:
            return None
        if b["run_ty"] in self.on_hold:
            return f"on hold: run type {b['run_ty']} is held by this run's hold input"
        rt = self.run_types.get(b["run_ty"])
        if rt is None:
            return None
        if not due:
            return None
        due = earliest_close_date(db.as_date(b["req_dt_key"]), rt.sla_days)
        if due < self.today or (due == self.today and (self.release_time is None or self.now_hhmm >= self.release_time)):
            return None
        return f"held until {due}{f' {self.release_time}' if self.release_time else ''} (its due date)"

    def split(self, batches: list[dict], due: bool = True) -> tuple[list[dict], list[dict]]:
        """(batches that may go now, held batches with a `hold` note)."""
        done = released(self.conn, (b["req_id"] for b in batches))
        ready, held = [], []
        for b in batches:
            why = self.hold(b, done, due)
            if why is None:
                ready.append(b)
            else:
                held.append({**b, "hold": why})
        return ready, held

    def unreleased_adhoc(self, batches: list[dict]) -> list[dict]:
        """The batches of ad hoc run types not released yet (a release lets BATCH_CLOSE close them)."""
        adhoc = [b for b in batches if (rt := self.run_types.get(b["run_ty"])) is not None
                 and rt.run_category_cd != SCHEDULED]
        done = released(self.conn, (b["req_id"] for b in adhoc)) if adhoc else set()
        return [{**b, "hold": f"run type {b['run_ty']} is ad hoc"} for b in adhoc if b["req_id"] not in done]

    def release(self, batches: list[dict], step: str) -> None:
        """Record the release of held batches."""
        with self.conn.transaction():
            for b in batches:
                self.logger.batch_event(RELEASED, req_id=b["req_id"], btch_id=b["btch_id"],
                                        detail=f"released by {step} ({b['hold']})")


# overrides: Manual overrides on ComplianceBatchOverride (design §7 P7, D-12, D-70, D-74).

@dataclass
class DecisionSummary:
    applied: list[int] = field(default_factory=list)
    expired: list[int] = field(default_factory=list)
    invalid: list[int] = field(default_factory=list)
    reopened: list[int] = field(default_factory=list)


class DecisionProcessor:
    def __init__(self, conn: Connection, clock: Clock, settings: Settings):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.logger = EventLogger(conn, clock)

    def run(self, scope: Optional[Scope] = None) -> DecisionSummary:
        """Reopen batches (REOPEN), then expire and apply REUSE overrides of a scope (a project, a table group or
        table, a run type), or of every project."""
        s = DecisionSummary()
        scope = scope or Scope()
        today = self.clock.today(self.settings.business_tz)
        self._reopen(s, today, scope)
        self._expire(s, today, scope)
        self._apply(s, today, scope)
        return s

    def _apply(self, s: DecisionSummary, today: date, scope: Scope) -> None:
        """Approved, still-valid REUSE overrides whose batch is open and has no data yet."""
        where, params = scope.sql("c")
        params = [today, *params]
        rows = self.conn.execute(
            f"""SELECT o.Ovrd_ID, c.Req_ID, c.Btch_ID FROM ComplianceBatchOverride o
                 JOIN ComplianceRequestControl c ON c.Req_ID = o.Req_ID
                WHERE UPPER(o.Override_Ty) = 'REUSE' AND UPPER(o.Apprvl_Stat) = 'APPROVED' AND o.Valid_Thru_Dt_Key >= %s
                  AND c.Batch_Close_Ind = 0 AND (c.Resolution_Ty IS NULL OR c.Resolution_Ty <> 'CARRY_FORWARD'){where}
                ORDER BY o.Ovrd_ID""", params).fetchall()
        run_types = cfgmod.run_types(self.conn) if rows else {}
        for r in rows:
            key = db.req_key(r)
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
                self.logger.audit("OVERRIDE_INVALID_DETECTED", actor=o["reviewed_by"] or o["loaduser_id"],
                                  description=f"REUSE: {problem}", **ctx)
            s.invalid.append(o["ovrd_id"])
            return
        check_transition(b["req_stat"], SOURCE_DATA_CARRY_FORWARD)
        empty = current_load(self.conn, b["req_id"])
        if empty is not None:
            self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='RELOADED', LastUpdt_Dtts=%s, LastUpdt_ID=USER WHERE Load_ID=%s",
                              (self.clock.now(), empty["load_id"]))
        self.conn.execute(
            """UPDATE ComplianceRequestControl SET Resolution_Ty='CARRY_FORWARD', Reuse_Btch_ID=%s,
                      Req_Stat=%s, LastUpdt_Dtts=%s, LastUpdt_ID=USER WHERE Req_ID=%s""",
            (src["btch_id"], SOURCE_DATA_CARRY_FORWARD, self.clock.now(), b["req_id"]))
        if code(o["reuse_btch_id"]) != src["btch_id"]:
            self.conn.execute("UPDATE ComplianceBatchOverride SET Reuse_Btch_ID=%s, LastUpdt_Dtts=%s, LastUpdt_ID=USER "
                              "WHERE Ovrd_ID=%s", (src["btch_id"], self.clock.now(), o["ovrd_id"]))
        self.logger.audit("OVERRIDE_APPROVED", actor=o["reviewed_by"] or o.get("loaduser_id") or "SYSTEM",
                          description=f"REUSE of {src['btch_id']} valid through {valid_thru}", **ctx)
        self.logger.batch_event("CARRY_FORWARD_APPLIED", req_id=b["req_id"], btch_id=b["btch_id"],
                                ovrd_id=o["ovrd_id"], actor=o["reviewed_by"] or "SYSTEM",
                                detail=f"reuses {src['btch_id']} through {valid_thru}")
        s.applied.append(o["ovrd_id"])

    def _reopen(self, s: DecisionSummary, today: date, scope: Scope) -> None:
        """Approved, still-valid REOPEN overrides not applied yet whose batch is closed: the batch is open again
        (with the open status of its data) until new data arrives; BATCH_CLOSE then closes it."""
        where, params = scope.sql("c")
        rows = self.conn.execute(
            f"""SELECT o.Ovrd_ID, o.Reviewed_By, o.LoadUser_ID, c.Req_ID, c.Btch_ID FROM ComplianceBatchOverride o
                 JOIN ComplianceRequestControl c ON c.Req_ID = o.Req_ID
                WHERE UPPER(o.Override_Ty) = 'REOPEN' AND UPPER(o.Apprvl_Stat) = 'APPROVED' AND o.Valid_Thru_Dt_Key >= %s
                  AND c.Batch_Close_Ind = 1 AND NOT EXISTS (SELECT 1 FROM ComplianceRequestFileDetail d
                        WHERE d.Ovrd_ID = o.Ovrd_ID AND d.Event_Ty = 'BATCH_REOPENED'){where}
                ORDER BY o.Ovrd_ID""", [today, *params]).fetchall()
        for r in rows:
            key = db.req_key(r)
            if not db.try_lock(self.conn, key):
                continue
            try:
                with self.conn.transaction():
                    b = get_batch(self.conn, r["req_id"])
                    if b["batch_close_ind"] != 1:
                        continue
                    to_stat = REOPEN_STATUS.get(b["resolution_ty"], REQUEST_CREATED)
                    resolution = None if b["resolution_ty"] == "MISSING" else b["resolution_ty"]
                    check_transition(b["req_stat"], to_stat)
                    self.conn.execute(
                        """UPDATE ComplianceRequestControl SET Batch_Close_Ind = 0, Req_Stat=%s, Resolution_Ty=%s,
                                  LastUpdt_Dtts=%s, LastUpdt_ID=USER WHERE Req_ID=%s""", (to_stat, resolution, self.clock.now(), b["req_id"]))
                    actor = r["reviewed_by"] or r["loaduser_id"] or "SYSTEM"
                    detail = f"reopened: {b['req_stat']} / {b['resolution_ty']} -> {to_stat}; open until new data arrives"
                    self.logger.batch_event("BATCH_REOPENED", req_id=b["req_id"], btch_id=b["btch_id"],
                                            ovrd_id=r["ovrd_id"], actor=actor, detail=detail)
                    self.logger.audit("BATCH_REOPENED", actor=actor, description=detail, ovrd_id=r["ovrd_id"],
                                      req_id=b["req_id"], btch_id=b["btch_id"], project_cd=b["project_cd"],
                                      table_nm=b["table_nm"], src_id=b["src_id"], run_ty=b["run_ty"])
                    s.reopened.append(r["ovrd_id"])
            finally:
                db.unlock(self.conn, key)

    def _expire(self, s: DecisionSummary, today: date, scope: Scope) -> None:
        """A batch still carrying data whose override has run out (or was rejected) goes back to REQUEST_CREATED."""
        where, params = scope.sql("c")
        params = [today, *params]
        rows = self.conn.execute(
            f"""SELECT c.Req_ID, c.Btch_ID, o.Ovrd_ID FROM ComplianceRequestControl c
                 LEFT JOIN ComplianceBatchOverride o
                        ON o.Req_ID = c.Req_ID AND UPPER(o.Override_Ty) = 'REUSE' AND UPPER(o.Apprvl_Stat) = 'APPROVED'
                WHERE c.Resolution_Ty = 'CARRY_FORWARD' AND c.Batch_Close_Ind = 0
                  AND (o.Ovrd_ID IS NULL OR o.Valid_Thru_Dt_Key < %s) AND NOT {REOPENED_AWAITING_DATA}{where}
                ORDER BY c.Req_ID""", params).fetchall()
        for r in rows:
            key = db.req_key(r)
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
        to_stat = REQUEST_CREATED if b["req_stat"] == SOURCE_DATA_CARRY_FORWARD else b["req_stat"]
        check_transition(b["req_stat"], to_stat)
        self.conn.execute(
            """UPDATE ComplianceRequestControl SET Resolution_Ty=NULL, Reuse_Btch_ID=NULL, Req_Stat=%s,
                      LastUpdt_Dtts=%s, LastUpdt_ID=USER WHERE Req_ID=%s""", (to_stat, self.clock.now(), b["req_id"]))
        self.logger.audit("OVERRIDE_EXPIRED", ovrd_id=r["ovrd_id"], req_id=b["req_id"], btch_id=b["btch_id"],
                          project_cd=b["project_cd"], table_nm=b["table_nm"],
                          src_id=b["src_id"], run_ty=b["run_ty"],
                          description=f"REUSE of {b['reuse_btch_id']} is no longer valid")
        self.logger.batch_event("CARRY_FORWARD_REMOVED", req_id=b["req_id"], btch_id=b["btch_id"],
                                ovrd_id=r["ovrd_id"], detail=f"reuse of {b['reuse_btch_id']} expired")
        s.expired.append(r["ovrd_id"] or b["req_id"])

    def health(self, project_cd: Optional[str] = None) -> dict[str, list[dict]]:
        """Pending reviews and approved overrides expiring within a week; of one project's batches when given."""
        q = partial(db.fetch_all, self.conn)
        today = self.clock.today(self.settings.business_tz)
        own, mine = ("", ()) if not project_cd else (
            " AND Req_ID IN (SELECT Req_ID FROM ComplianceRequestControl WHERE Project_Cd = %s)", (code(project_cd),))
        return {
            "pending_reviews": q(f"""SELECT Ovrd_ID, Override_Ty, Req_ID, Btch_ID, Load_Dtts
                                     FROM ComplianceBatchOverride WHERE UPPER(Apprvl_Stat)='PENDING_REVIEW'{own}
                                     ORDER BY Ovrd_ID""", *mine),
            "overrides_expiring_soon": q(
                f"""SELECT Ovrd_ID, Override_Ty, Btch_ID, Valid_Thru_Dt_Key FROM ComplianceBatchOverride
                    WHERE UPPER(Apprvl_Stat)='APPROVED' AND Valid_Thru_Dt_Key BETWEEN %s AND %s{own}
                    ORDER BY Valid_Thru_Dt_Key""", today, today + timedelta(days=7), *mine),
        }

    def _reuse_problem(self, o: dict, b: dict, src: Optional[dict], rt: Optional[cfgmod.RunType]) -> Optional[str]:
        if rt is None or not rt.carry_fwd:
            return f"run type {b['run_ty']} does not allow carry-forward (Carry_Fwd_Ind = 0)"
        if b["batch_close_ind"] == 1:
            return "batch is closed"
        if has_data(self.conn, b):
            return "batch already has data"
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
            row = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Btch_ID=%s AND Src_ID=%s",
                                    (row["reuse_btch_id"], row["src_id"])).fetchone()
        return row if row and current_load(self.conn, row["req_id"]) else None


# closing: Batch close (design §11).

def close_resolution(batch: dict) -> tuple[str, str]:
    """(Req_Stat, Resolution_Ty) a batch closes with."""
    if batch["resolution_ty"] in HAS_DATA:
        return REQUEST_COMPLETE, batch["resolution_ty"]
    if batch["resolution_ty"] == "NO_DATA":
        return DATA_NOT_PROVIDED, "NO_DATA"
    return DATA_NOT_PROVIDED, "MISSING"


@dataclass
class CloseOutcome:
    btch_id: str
    req_stat: str
    resolution_ty: str


@dataclass
class CloseSummary:
    evaluated: int = 0
    closed: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    on_hold: list[str] = field(default_factory=list)
    released: list[str] = field(default_factory=list)


class BatchCloser:
    def __init__(self, conn: Connection, clock: Clock, settings: Settings):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.logger = EventLogger(conn, clock)

    def close(self, btch_id: str, closed_by: str, src_id: Optional[str] = None) -> list[CloseOutcome]:
        """Close the open sources of a batch (or the one named) whose SLA hold has passed, whatever their data
        (CloseBlocked otherwise, before any is closed)."""
        rows = self._batches(btch_id, src_id)
        if not rows:
            raise LookupError(f"batch {btch_id}{f' source {src_id}' if src_id else ''} not found")
        todo = [b for b in rows if b["batch_close_ind"] == 0]
        if not todo:
            self._blocked(rows[0], closed_by, "batch is already closed")
        for b in todo:
            if self._today() < b["hold_dt"] and not self._released(b):
                self._blocked(b, closed_by, f"SLA hold until {b['hold_dt']}")
        outs = [o for o in (self._close_locked(b, closed_by) for b in todo) if o is not None]
        if not outs:
            self._blocked(rows[0], closed_by, "batch is already closed")
        return outs

    def run(self, scope: Optional[Scope] = None, hold: tuple = (), release: bool = False) -> CloseSummary:
        """Close the open batches of a scope (a project, a table group or table, a run type) whose hold has passed,
        with or without data (no data: DATA_NOT_PROVIDED / MISSING and a SOURCE_MISSING_AT_CLOSE alert), and the
        batches with data released early (BATCH_RELEASED). A batch of an ad hoc run type with data closes only once
        released. Batches of the run types in `hold` stay open unless released; reopened batches stay open until new
        data arrives. `release` first releases the scope's open batches with data, so they close now."""
        s = CloseSummary()
        scope = scope or Scope()
        if release:
            s.released = self._release(scope)
        rows = self._open_past_hold(scope, sweep=True)
        done = released(self.conn, [b["req_id"] for b in rows if b["run_ty"] in hold]) if hold else set()
        for b in rows:
            if b["run_ty"] in hold and b["req_id"] not in done:
                s.on_hold.append(b["btch_id"])
                continue
            s.evaluated += 1
            try:
                out = self._close_locked(b, "SYSTEM")
            except CloseDeferred:
                s.deferred.append(b["btch_id"])
                continue
            (s.closed if out else s.waiting).append(b["btch_id"])
        return s

    def health(self, project_cd: Optional[str] = None) -> dict[str, list[dict]]:
        """Open batches past their SLA hold (design §15.3); of one project when given."""
        return {"batches_past_hold_not_closed": [
            {k: b[k] for k in ("req_id", "btch_id", "project_cd", "table_nm", "src_id", "run_ty", "req_dt_key",
                               "req_stat", "resolution_ty")}
            for b in self._open_past_hold(Scope(project_cd=code(project_cd) or None),
                                          last_hold=self._today() - timedelta(days=1))]}

    def _today(self) -> date:
        return self.clock.today(self.settings.business_tz)

    def _batches(self, btch_id: str, src_id: Optional[str] = None) -> list[dict]:
        where, params = ("c.Btch_ID = %s", [code(btch_id)]) if src_id is None else \
            ("c.Btch_ID = %s AND c.Src_ID = %s", [code(btch_id), code(src_id)])
        rows = self.conn.execute(
            f"""SELECT c.*, r.SLA_Days FROM ComplianceRequestControl c
                 LEFT JOIN ComplianceRunType r ON UPPER(r.Run_Ty) = c.Run_Ty WHERE {where} ORDER BY c.Req_ID""",
            params).fetchall()
        for b in rows:
            b["hold_dt"] = earliest_close_date(db.as_date(b["req_dt_key"]), 1 if b["sla_days"] is None else b["sla_days"])
        return rows

    def _release(self, scope: Scope) -> list[str]:
        """Release the scope's open batches with data that are not released yet."""
        where, params = scope.sql("c")
        rows = self.conn.execute(
            f"""SELECT c.* FROM ComplianceRequestControl c
                 WHERE c.Batch_Close_Ind = 0 AND COALESCE(c.Resolution_Ty, 'NO_DATA') <> 'NO_DATA' AND NOT {REOPENED_AWAITING_DATA}
                   AND NOT EXISTS (SELECT 1 FROM ComplianceRequestFileDetail d
                                    WHERE d.Req_ID = c.Req_ID AND d.Event_Ty = '{RELEASED}'){where}
                 ORDER BY c.Req_ID""", params).fetchall()
        if rows:
            Releases(self.conn, self.clock, self.settings).release(
                [{**b, "hold": "close requested"} for b in rows], "BATCH_CLOSE")
        return [b["btch_id"] for b in rows]

    def _released(self, b: dict) -> bool:
        return b["resolution_ty"] is not None and self.conn.execute(
            "SELECT 1 AS ok FROM ComplianceRequestFileDetail WHERE Req_ID = %s AND Event_Ty = 'BATCH_RELEASED'",
            (b["req_id"],)).fetchone() is not None

    def _open_past_hold(self, scope: Scope, last_hold: Optional[date] = None, sweep: bool = False) -> list[dict]:
        """Open batches in scope whose hold ends on or before `last_hold` (default today); with `sweep`, also the
        batches with data released early, and not the reopened batches still waiting for new data."""
        where, params = scope.sql("c")
        if sweep:
            where = f" AND NOT {REOPENED_AWAITING_DATA}{where}"
        is_released = f"""EXISTS (SELECT 1 FROM ComplianceRequestFileDetail d
                          WHERE d.Req_ID = c.Req_ID AND d.Event_Ty = '{RELEASED}')"""
        early = f"OR (c.Resolution_Ty IS NOT NULL AND {is_released})" if sweep else ""
        return self.conn.execute(
            f"""SELECT c.* FROM ComplianceRequestControl c JOIN ComplianceRunType r ON UPPER(r.Run_Ty) = c.Run_Ty
                 WHERE c.Batch_Close_Ind = 0 AND (c.Req_Dt_Key + (r.SLA_Days - 1) <= %s {early})
                   AND NOT (UPPER(r.Run_Category_Cd) <> '{SCHEDULED}' AND COALESCE(c.Resolution_Ty, 'NO_DATA') <> 'NO_DATA'
                            AND NOT {is_released}){where} ORDER BY c.Req_ID""",
            [last_hold or self._today(), *params]).fetchall()

    def _blocked(self, b: dict, closed_by: str, reason: str) -> None:
        with self.conn.transaction():
            self.logger.audit("BATCH_CLOSE_BLOCKED", actor=closed_by, description=reason, **_ctx(b))
        raise CloseBlocked(reason)

    def _close_locked(self, b: dict, closed_by: str) -> Optional[CloseOutcome]:
        """Close under the batch lock; None if there is nothing to close any more."""
        key = db.req_key(b)
        if not db.try_lock(self.conn, key):
            with self.conn.transaction():
                self.logger.audit("BATCH_CLOSE_DEFERRED_LOCKED", description="batch busy; close deferred", **_ctx(b))
            raise CloseDeferred(f"batch {b['btch_id']} is locked")
        try:
            with self.conn.transaction():
                cur = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s",
                                        (b["req_id"],)).fetchone()
                if cur["batch_close_ind"] == 1 or cur["req_stat"] not in OPEN_STATUSES:
                    return None
                to_stat, resolution = close_resolution(cur)
                check_transition(cur["req_stat"], to_stat)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Batch_Close_Ind = 1, Req_Stat=%s, Resolution_Ty=%s,
                              LastUpdt_Dtts=%s, LastUpdt_ID=USER WHERE Req_ID=%s""", (to_stat, resolution, self.clock.now(), cur["req_id"]))
                self.logger.batch_event("BATCH_CLOSED", req_id=cur["req_id"], btch_id=cur["btch_id"], actor=closed_by,
                                        detail=f"{to_stat} / {resolution}")
                if resolution == "MISSING":
                    self.logger.audit("SOURCE_MISSING_AT_CLOSE", actor=closed_by, **_ctx(cur),
                                      description=f"no data from source {cur['src_id']} for {db.as_date(cur['rpt_start_dt_key'])}"
                                                  f" to {db.as_date(cur['rpt_end_dt_key'])} by the close date; closed as "
                                                  f"{DATA_NOT_PROVIDED}")
                return CloseOutcome(cur["btch_id"], to_stat, resolution)
        finally:
            db.unlock(self.conn, key)


def _ctx(b: dict) -> dict:
    return {k: b[k] for k in ("req_id", "btch_id", "project_cd", "table_nm", "src_id", "run_ty")}
