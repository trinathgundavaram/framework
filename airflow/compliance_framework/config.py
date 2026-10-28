"""Metadata tables: the configuration rows and their validation, the scope of a run, the file check options."""
from __future__ import annotations

import codecs
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Iterable, Mapping, NamedTuple, Optional, Sequence

from compliance_framework import db
from compliance_framework.adapters import parse_uri
from compliance_framework.common import ADHOC, ConfigError, SCHEDULED, code
from compliance_framework.db import Connection


# config: Configuration rows, filename templates and the validator.

def _text(value) -> Optional[str]:
    return value.strip() or None if isinstance(value, str) else None


@dataclass(frozen=True)
class RunType:
    run_ty: str
    run_category_cd: str
    sla_days: int
    carry_fwd: bool
    active: bool
    schedule_sql: Optional[str] = None
    sched_cd: Optional[str] = None
    sched_day: Optional[str] = None
    sched_week: Optional[str] = None
    sched_months: tuple = ()
    sched_window_days: int = 1
    sched_offset_days: int = 0

    @property
    def has_schedule(self) -> bool:
        return bool(self.sched_cd or self.schedule_sql)

    @classmethod
    def from_row(cls, r: dict) -> "RunType":
        months = _text(r.get("sched_months_txt"))
        return cls(code(r["run_ty"]), code(r["run_category_cd"]), r["sla_days"], r["carry_fwd_ind"] == 1, r["active_ind"] == 1,
                   _text(r.get("batch_schedule_sql_txt")), code(_text(r.get("sched_cd"))), code(_text(r.get("sched_day_cd"))),
                   code(_text(r.get("sched_week_cd"))),
                   tuple(int(m) if m.isdigit() else 0 for m in months.replace(" ", "").split(",") if m) if months else (),
                   int(r.get("sched_window_days") or 1), int(r.get("sched_offset_days") or 0))


@dataclass(frozen=True)
class XwalkRow:
    project_cd: str
    table_nm: str
    src_id: str
    run_ty: str
    effective_start_dt: date
    effective_end_dt: Optional[date]
    cmplnc_vrsn: str
    active: bool
    rpt_dt_sql: Optional[str] = None
    rpt_period_cd: Optional[str] = None
    include: bool = True
    listed: bool = False
    overridden: tuple = ()

    @classmethod
    def from_row(cls, r: dict, run_ty: Optional[str] = None, listed: bool = False) -> "XwalkRow":
        return cls(code(r["project_cd"]), code(r["table_nm"]), code(r["src_id"]), code(run_ty or r["run_ty"]),
                   db.as_date(r["effective_start_dt_key"]), db.as_date(r["effective_end_dt_key"]), code(r["cmplnc_vrsn"]),
                   r["active_ind"] == 1, _text(r.get("rpt_dt_sql_txt")), code(_text(r.get("rpt_period_cd"))),
                   r.get("include_ind", 1) != 0, listed)

    @property
    def window(self) -> tuple:
        return self.effective_start_dt, self.effective_end_dt

    def effective_on(self, d: date) -> bool:
        return (self.active and _within(d, self.window)
                and not any(_within(d, w) for w in self.overridden))


def _within(d: date, window: tuple) -> bool:
    return window[0] <= d and (window[1] is None or d <= window[1])


@dataclass(frozen=True)
class FileConfig:
    cfg_id: int
    project_cd: str
    table_nm: str
    src_id: str
    src_file_nm_tmplt: str
    delmtr_cd: Optional[str]
    has_header: bool
    has_trailer: bool
    allow_zero_records: bool
    src_file_path: str
    stg_schema_nm: str
    stg_table_nm: str
    core_schema_nm: str
    core_table_nm: str
    active: bool = True
    file_vrsn_cd: Optional[str] = None

    @property
    def name_parts(self) -> dict:
        """The values of the row tokens of a file name template."""
        return {"PROJECT": self.project_cd, "TABLE": self.table_nm, "SRC": self.src_id, "VERSION": self.file_vrsn_cd}

    @property
    def src_file_ty(self) -> str:
        """The template's file extension."""
        return os.path.splitext(self.src_file_nm_tmplt)[1].lower()

    @classmethod
    def from_row(cls, r: dict, settings=None) -> "FileConfig":
        """`settings` resolves $env tokens in the staging / core database names and the inbound folder."""
        name = settings.resolve_env if settings else (lambda text, identifier=True: text)
        return cls(r["cfg_id"], code(r["project_cd"]), code(r["table_nm"]), code(r["src_id"]), r["src_file_nm_tmplt"],
                   r["delmtr_cd"],
                   r["src_file_has_hdr_ind"] == 1, r["src_file_has_trlr_ind"] == 1, r["allow_zero_rcd_ind"] == 1,
                   name(r["src_file_path"], False),
                   name(r["stg_schema_nm"]), r["stg_table_nm"], name(r["core_schema_nm"]), r["core_table_nm"],
                   r["active_ind"] == 1,
                   _text(r.get("file_vrsn_cd")))


@dataclass(frozen=True)
class TargetColumn:
    """One ComplianceTargetColumnConfig row: a staging / core column the framework fills ('*' = every table)."""
    project_cd: str
    table_nm: str
    column_nm: str
    value_cd: str
    active: bool
    value_txt: Optional[str] = None

    @classmethod
    def from_row(cls, r: dict) -> "TargetColumn":
        return cls(code(r["project_cd"]), code(r["table_nm"]), r["column_nm"].strip().lower(), code(r["value_cd"]),
                   r["active_ind"] == 1, _text(r.get("value_txt")))

    @property
    def label(self) -> str:
        return f"ComplianceTargetColumnConfig {self.project_cd}/{self.table_nm}/{self.column_nm}"


@dataclass(frozen=True)
class FileCheck:
    """One ComplianceFileCheckConfig row: a check included for, or excluded from, a project's files ('*' = all)."""
    project_cd: str
    table_nm: str
    src_id: str
    check_cd: str
    check_val_txt: Optional[str]
    active: bool
    include: bool = True
    field_nm: str = "*"

    @classmethod
    def from_row(cls, r: dict) -> "FileCheck":
        return cls(code(r["project_cd"]), code(r["table_nm"]), code(r["src_id"]), code(r["check_cd"]),
                   _text(r["check_val_txt"]), r["active_ind"] == 1, r["include_ind"] == 1,
                   (_text(r.get("check_field_nm")) or "*").lower())

    @property
    def label(self) -> str:
        field = "" if self.field_nm == "*" else f"/{self.field_nm}"
        return f"ComplianceFileCheckConfig {self.project_cd}/{self.table_nm}/{self.src_id}/{self.check_cd}{field}"


@dataclass(frozen=True)
class RuleBinding:
    project_cd: str
    table_nm: str
    src_id: str
    run_ty: str
    gre_rule_group: str
    gre_rule_variant: str


def run_type(conn: Connection, run_ty: str) -> Optional[RunType]:
    r = conn.execute("SELECT * FROM ComplianceRunType WHERE UPPER(Run_Ty) = %s", (code(run_ty),)).fetchone()
    return RunType.from_row(r) if r else None


def run_types(conn: Connection) -> dict[str, RunType]:
    return {code(r["run_ty"]): RunType.from_row(r) for r in conn.execute("SELECT * FROM ComplianceRunType").fetchall()}


def xwalk_run_types(text) -> list[str]:
    return [code(t) for t in str(text or "").split(",") if t.strip()]


def xwalk_rows(conn: Connection, *, project_cd: Optional[str] = None, table_nm: Optional[str] = None,
               src_id: Optional[str] = None, run_ty: Optional[str] = None, switched_off: bool = False) -> list[XwalkRow]:
    """Active crosswalk rows, one per run type, optionally filtered.

    Run_Ty may list run types (DAILY,WEEKLY,MNT). A row for one run type replaces a list's entry for that run type
    while it is effective (Include_Ind = 0 switches it off). A row without Rpt_Period_Cd / Rpt_Dt_Sql_Txt takes the
    project's ComplianceRunTypePeriod. `switched_off` also returns the Include_Ind = 0 rows (validation).
    """
    where, params = ["Active_Ind = 1"], []
    for col, val in (("Project_Cd", project_cd), ("Table_Nm", table_nm), ("Src_ID", src_id)):
        if val is not None:
            where.append(f"UPPER({col}) = %s")
            params.append(code(val))
    rows = []
    for r in conn.execute(f"SELECT * FROM ComplianceDataSetSourceXwalk WHERE {' AND '.join(where)}", params).fetchall():
        types = xwalk_run_types(r["run_ty"])
        rows += [XwalkRow.from_row(r, t, len(types) > 1) for t in types if run_ty is None or t == code(run_ty)]
    own: dict[tuple, list] = defaultdict(list)
    for x in rows:
        if not x.listed:
            own[(x.project_cd, x.table_nm, x.src_id, x.run_ty)].append(x.window)
    periods = run_type_periods(conn) if any(not (x.rpt_period_cd or x.rpt_dt_sql) for x in rows) else {}
    out = []
    for x in rows:
        if x.listed and own.get(key := (x.project_cd, x.table_nm, x.src_id, x.run_ty)):
            if any(_within(x.effective_start_dt, w) and (w[1] is None or x.effective_end_dt and x.effective_end_dt <= w[1])
                   for w in own[key]):
                continue
            x = replace(x, overridden=tuple(own[key]))
        if not (x.rpt_period_cd or x.rpt_dt_sql):
            period, sql = periods.get((x.project_cd, x.table_nm, x.run_ty)) or periods.get((x.project_cd, "*", x.run_ty)) \
                or (None, None)
            x = replace(x, rpt_period_cd=period, rpt_dt_sql=sql)
        if x.include or switched_off:
            out.append(x)
    return sorted(out, key=lambda x: (x.project_cd, x.table_nm, x.src_id, x.run_ty, x.effective_start_dt))


def run_type_periods(conn: Connection) -> dict[tuple, tuple]:
    """The active ComplianceRunTypePeriod rows: (project, table or '*', run type) -> (Rpt_Period_Cd, Rpt_Dt_Sql_Txt),
    one of them set (the code wins when a row has both; validate-config reports that)."""
    if not db.table_exists(conn, conn.schema, "ComplianceRunTypePeriod"):
        return {}
    out = {}
    for r in conn.execute("SELECT * FROM ComplianceRunTypePeriod WHERE Active_Ind = 1").fetchall():
        period, sql = code(_text(r.get("rpt_period_cd"))), _text(r.get("rpt_dt_sql_txt"))
        if period or sql:
            out[(code(r["project_cd"]), code(r["table_nm"] or "*"), code(r["run_ty"]))] = (period, None if period else sql)
    return out


@dataclass(frozen=True)
class CalendarRow:
    """One ComplianceBatchCalendar row: batches of a run type created on Req_Dt_Key for these report dates."""
    project_cd: str
    table_nm: str
    run_ty: str
    req_dt: date
    rpt_start: date
    rpt_end: date

    @classmethod
    def from_row(cls, r: dict) -> "CalendarRow":
        return cls(code(r["project_cd"]), code(r["table_nm"] or "*"), code(r["run_ty"]), db.as_date(r["req_dt_key"]),
                   db.as_date(r["rpt_start_dt_key"]), db.as_date(r["rpt_end_dt_key"]))


def calendar_rows(conn: Connection, project_cd: str, req_dt: date) -> list[CalendarRow]:
    """The active calendar rows of a project for a request date; none when ComplianceBatchCalendar does not exist."""
    if not db.table_exists(conn, conn.schema, "ComplianceBatchCalendar"):
        return []
    rows = conn.execute("SELECT * FROM ComplianceBatchCalendar WHERE UPPER(Project_Cd) = %s AND Req_Dt_Key = %s "
                        "AND Active_Ind = 1 ORDER BY Run_Ty, Table_Nm", (code(project_cd), req_dt)).fetchall()
    return [CalendarRow.from_row(r) for r in rows]


def effective_sources(conn, project_cd, table_nm, run_ty, on_date: date) -> list[XwalkRow]:
    return [x for x in xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(on_date)]


SOURCE_DB_TYPES = ("TERADATA", "SQLSERVER")


@dataclass(frozen=True)
class QueryConfig:
    """One ComplianceInternalSourceConfig row: an internal source pulled with its own SELECT (SOURCE_LOAD).

    No Src_Conn_Cd = the source is on the framework's own Teradata system: the rows move with INSERT ... SELECT.
    """
    project_cd: str
    table_nm: str
    src_id: str
    db_ty: str
    conn_cd: Optional[str]
    sql: str
    stg_schema_nm: str
    stg_table_nm: str
    core_schema_nm: str
    core_table_nm: str
    active: bool = True
    delmtr_cd = None
    cfg_id = None

    @property
    def label(self) -> str:
        return f"ComplianceInternalSourceConfig {self.project_cd}/{self.table_nm}/{self.src_id}"

    @property
    def same_system(self) -> bool:
        return self.db_ty == "TERADATA" and not self.conn_cd

    @classmethod
    def from_row(cls, r: dict, settings=None) -> "QueryConfig":
        name = settings.resolve_env if settings else (lambda text, identifier=True: text)
        return cls(code(r["project_cd"]), code(r["table_nm"]), code(r["src_id"]), code(r["src_db_ty"]),
                   name(_text(r.get("src_conn_cd")), False), r["src_sql_txt"], name(r["stg_schema_nm"]),
                   r["stg_table_nm"], name(r["core_schema_nm"]), r["core_table_nm"], r["active_ind"] == 1)


def active_query_configs(conn: Connection, settings=None) -> list[QueryConfig]:
    """The active internal query sources; none when ComplianceInternalSourceConfig does not exist."""
    if not db.table_exists(conn, conn.schema, "ComplianceInternalSourceConfig"):
        return []
    rows = conn.execute("SELECT * FROM ComplianceInternalSourceConfig WHERE Active_Ind = 1 "
                        "ORDER BY Project_Cd, Table_Nm, Src_ID").fetchall()
    return [QueryConfig.from_row(r, settings) for r in rows]


EXPORT_PROC_PARAMS = ("btch_id", "data_btch_id", "reused_btch_ids", "project_cd", "table_nm", "src_id",
                      "run_ty", "version", "req_id", "req_dt", "rpt_start", "rpt_end")


class ProcArg(NamedTuple):
    """One argument of the export procedure: PARAM (a placeholder), TEXT (a fixed value) or OUT (a parameter name)."""
    kind: str
    value: str


def proc_args(text: str) -> tuple:
    """Exp_Proc_Parm_Txt -> its arguments in order: {placeholder}, 'fixed text' (quotes inside doubled) or the name of
    an OUT parameter. A name without braces that is a placeholder stays one."""
    args, rest = [], text.strip()
    while rest:
        m = re.match(r"'((?:[^']|'')*)'\s*(?:,|$)", rest)
        if m:
            args.append(ProcArg("TEXT", m.group(1).replace("''", "'")))
        else:
            m = re.match(r"([^,]*)(?:,|$)", rest)
            token = m.group(1).strip()
            name = token.strip("{}").strip()
            if name:
                param = token.startswith("{") or name.lower() in EXPORT_PROC_PARAMS
                args.append(ProcArg("PARAM", name.lower()) if param else ProcArg("OUT", name))
        rest = rest[m.end():].strip()
    return tuple(args)


DEFAULT_EXPORT_PROC_PARAMS = tuple(ProcArg("PARAM", n) for n in ("btch_id", "data_btch_id"))
EXPORT_BATCH_PARAMS = ("btch_id", "project_cd", "table_nm", "run_ty", "version", "req_dt", "rpt_start",
                       "rpt_end", "exp_schema_nm", "exp_table_nm")
EXPORT_SQL_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def export_statement(label: str, text: str, allowed: tuple, select: bool = False) -> str:
    """A configured export statement, checked: one statement (a SELECT for Exp_Sql_Txt), only known {names}."""
    body = text.strip().rstrip(";").strip()
    if ";" in body:
        raise ConfigError(f"{label} must be one statement (put several steps in a procedure)")
    if select and not re.match(r"^(SELECT|SEL|WITH)\b", body, re.IGNORECASE):
        raise ConfigError(f"{label} must be a SELECT statement")
    unknown = sorted({n.lower() for n in EXPORT_SQL_PLACEHOLDER.findall(body)} - set(allowed))
    if unknown:
        raise ConfigError(f"{label} uses {', '.join('{' + n + '}' for n in unknown)}; available: "
                          f"{', '.join('{' + n + '}' for n in allowed)}")
    return body


@dataclass(frozen=True)
class ExportConfig:
    """One ComplianceExportConfig row: the export table a core table's batches are copied to (EXPORT_LOAD)."""
    project_cd: str
    table_nm: str
    exp_schema_nm: str
    exp_table_nm: str
    active: bool = True
    proc_nm: Optional[str] = None
    proc_params: tuple = DEFAULT_EXPORT_PROC_PARAMS
    sql_txt: Optional[str] = None
    pre_sql: Optional[str] = None
    post_sql: Optional[str] = None

    @property
    def label(self) -> str:
        return f"ComplianceExportConfig {self.project_cd}/{self.table_nm}"

    @classmethod
    def from_row(cls, r: dict, settings=None) -> "ExportConfig":
        name = settings.resolve_env if settings else (lambda text, identifier=True: text)
        return cls(code(r["project_cd"]), code(r["table_nm"]), name(r["exp_schema_nm"]), r["exp_table_nm"],
                   r["active_ind"] == 1,
                   name(_text(r.get("exp_proc_nm"))) if _text(r.get("exp_proc_nm")) else None,
                   proc_args(r["exp_proc_parm_txt"]) if _text(r.get("exp_proc_parm_txt")) else DEFAULT_EXPORT_PROC_PARAMS,
                   _text(r.get("exp_sql_txt")), _text(r.get("exp_pre_sql_txt")), _text(r.get("exp_post_sql_txt")))


def active_export_configs(conn: Connection, settings=None) -> list[ExportConfig]:
    """The active export tables; none when ComplianceExportConfig does not exist."""
    if not db.table_exists(conn, conn.schema, "ComplianceExportConfig"):
        return []
    rows = conn.execute("SELECT * FROM ComplianceExportConfig WHERE Active_Ind = 1 ORDER BY Project_Cd, Table_Nm").fetchall()
    return [ExportConfig.from_row(r, settings) for r in rows]


def target_tables(conn: Connection, project_cd: str, table_nm: str, src_id: str, settings=None):
    """The staging / core tables of a source: its file config, else its query config."""
    cfg = file_config(conn, project_cd, table_nm, src_id, settings=settings)
    if cfg is not None:
        return cfg
    return next((q for q in active_query_configs(conn, settings)
                 if (q.project_cd, q.table_nm, q.src_id) == (code(project_cd), code(table_nm), code(src_id))), None)


def active_file_configs(conn: Connection, settings=None) -> list[FileConfig]:
    rows = conn.execute("SELECT * FROM ComplianceSourceFileConfig WHERE Active_Ind = 1 ORDER BY Cfg_ID").fetchall()
    return [FileConfig.from_row(r, settings) for r in rows]


def file_config(conn, project_cd: str, table_nm: str, src_id: Optional[str] = None,
                settings=None) -> Optional[FileConfig]:
    """Active file config of a source; with src_id=None any source of the table (all share the core table)."""
    where, params = "UPPER(Project_Cd)=%s AND UPPER(Table_Nm)=%s AND Active_Ind = 1", [code(project_cd), code(table_nm)]
    if src_id is not None:
        where, params = where + " AND UPPER(Src_ID)=%s", params + [code(src_id)]
    rows = conn.execute(f"SELECT * FROM ComplianceSourceFileConfig WHERE {where} ORDER BY Cfg_ID", params).fetchall()
    return FileConfig.from_row(rows[0], settings) if rows else None


def target_columns(conn) -> list[TargetColumn]:
    """Every ComplianceTargetColumnConfig row, active or not."""
    rows = conn.execute("SELECT * FROM ComplianceTargetColumnConfig ORDER BY Project_Cd, Table_Nm, Column_Nm").fetchall()
    return [TargetColumn.from_row(r) for r in rows]


def file_checks(conn) -> list[FileCheck]:
    """Every ComplianceFileCheckConfig row; an inactive row is ignored by the checks."""
    rows = conn.execute("SELECT * FROM ComplianceFileCheckConfig ORDER BY Project_Cd, Table_Nm, Src_ID, Check_Cd").fetchall()
    return [FileCheck.from_row(r) for r in rows]


def rule_bindings(conn, project_cd: str, table_nm: str, src_id: str, run_ty: str) -> list[RuleBinding]:
    """Every active binding that applies to a file (additive)."""
    rows = conn.execute(
        """SELECT * FROM ComplianceRuleBinding
            WHERE UPPER(Project_Cd) = %(p)s AND UPPER(Table_Nm) IN (%(t)s, '*') AND UPPER(Src_ID) IN (%(s)s, '*')
              AND UPPER(Run_Ty) IN (%(r)s, '*') AND Active_Ind = 1""",
        {"p": code(project_cd), "t": code(table_nm), "s": code(src_id), "r": code(run_ty)}).fetchall()
    rows.sort(key=lambda r: (r["gre_rule_group"], r["gre_rule_variant"],
                             (r["table_nm"] == "*") + (r["src_id"] == "*") + (r["run_ty"] == "*")))
    out: dict[tuple[str, str], RuleBinding] = {}
    for r in rows:
        out.setdefault((r["gre_rule_group"], r["gre_rule_variant"]), RuleBinding(
            code(r["project_cd"]), code(r["table_nm"]), code(r["src_id"]), code(r["run_ty"]), r["gre_rule_group"],
            r["gre_rule_variant"]))
    return list(out.values())


NAME_TOKENS = ("RUNTY", "RPTSTART", "RPTEND", "RPTMONTH", "TS", "ANY")
ROW_TOKENS = ("PROJECT", "TABLE", "SRC", "VERSION")
_TOKEN = re.compile(r"\{([^{}]*)\}")
_DEFAULT_FORMAT = {"RPTSTART": "YYYYMMDD", "RPTEND": "YYYYMMDD", "RPTMONTH": "YYYYMM"}
_FORMAT_CODES = (("YYYY", "%Y", r"\d{4}"), ("YY", "%y", r"\d{2}"), ("DD", "%d", r"\d{2}"), ("HH", "%H", r"\d{2}"),
                 ("MI", "%M", r"\d{2}"), ("SS", "%S", r"\d{2}"))
_TS_DEFAULT = r"\d{12}(?:\d{2})?"
_VERSION_ANY = r"[A-Za-z0-9.]+"


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class MatchResult:
    cfg: FileConfig
    run_ty: str
    rpt_start: date
    rpt_end: date
    file_ts: Optional[datetime] = None


class MatchError(Exception):
    def __init__(self, event_ty: str, message: str, cfg: Optional[FileConfig] = None):
        super().__init__(message)
        self.event_ty = event_ty
        self.cfg = cfg


def date_format(fmt: str) -> tuple[str, str]:
    """A token format such as YYYYMMDD or MM-DD-YYYY -> (strptime format, regex); MM after HH is minutes."""
    out, rx, i, hours = "", "", 0, False
    text = fmt.upper()
    while i < len(text):
        if text.startswith("MM", i):
            out, rx, i = out + ("%M" if hours else "%m"), rx + r"\d{2}", i + 2
            continue
        for name, directive, pattern in _FORMAT_CODES:
            if text.startswith(name, i):
                out, rx, i, hours = out + directive, rx + pattern, i + len(name), hours or name == "HH"
                break
        else:
            if text[i].isalnum() or text[i] in "%/":
                raise TemplateError(f"date format {fmt!r} is not understood; use YYYY, YY, MM, DD, HH, MI, SS "
                                    "and separators such as - or .")
            out, rx, i = out + text[i], rx + re.escape(text[i]), i + 1
    return out, rx


def _parse(template: str, parts: Optional[Mapping[str, Optional[str]]] = None, wild: bool = False) -> list[tuple]:
    """[('lit', text, None) | ('ph', NAME, format)]: row tokens become literals (or a wildcard version)."""
    out: list[tuple] = []
    pos = 0
    unknown = []
    for m in _TOKEN.finditer(template):
        if m.start() > pos:
            out.append(("lit", template[pos:m.start()], None))
        name, _, fmt = m.group(1).partition(":")
        name, fmt = name.strip().upper(), fmt.strip() or None
        if name in NAME_TOKENS:
            if fmt and name in ("RUNTY", "ANY"):
                raise TemplateError(f"{{{name}}} takes no format in {template!r}")
            out.append(("ph", name, fmt or _DEFAULT_FORMAT.get(name)))
        elif name in ROW_TOKENS:
            if fmt:
                raise TemplateError(f"{{{name}}} takes no format in {template!r}")
            value = (parts or {}).get(name)
            if wild and name == "VERSION":
                out.append(("ph", "VERSION", None))
            elif value in (None, ""):
                hint = "set File_Vrsn_Cd on the file config" if name == "VERSION" else "it is filled from the file config"
                raise TemplateError(f"{{{name}}} in {template!r} has no value: {hint}")
            else:
                out.append(("lit", str(value), None))
        else:
            unknown.append(name)
        pos = m.end()
    if pos < len(template):
        out.append(("lit", template[pos:], None))
    if "{" in _TOKEN.sub("", template) or "}" in _TOKEN.sub("", template):
        raise TemplateError(f"unbalanced braces in template {template!r}")
    if unknown:
        raise TemplateError(f"unknown placeholder(s) {sorted(set(unknown))} in {template!r}")
    names = [t for k, t, _ in out if k == "ph"]
    month = names.count("RPTMONTH")
    if month and (month > 1 or "RPTSTART" in names or "RPTEND" in names):
        raise TemplateError(f"{{RPTMONTH}} stands for both report dates: use it once, without {{RPTSTART}} / {{RPTEND}}, "
                            f"in {template!r}")
    for p in ("RUNTY",) if month else ("RUNTY", "RPTSTART", "RPTEND"):
        n = names.count(p)
        if n != 1:
            raise TemplateError(f"placeholder {{{p}}} must appear exactly once in {template!r} (found {n})")
    if names.count("TS") > 1:
        raise TemplateError(f"placeholder {{TS}} must appear at most once in {template!r}")
    for kind, name, fmt in out:
        if kind != "ph" or not fmt:
            continue
        directives = date_format(fmt)[0]
        year = "%Y" in directives or "%y" in directives
        if name == "RPTMONTH":
            ok, need = year and "%m" in directives and "%d" not in directives, "a year and a month, no day"
        elif name == "TS":
            ok, need = year and "%m" in directives and "%d" in directives, "at least a year, a month and a day"
        else:
            ok, need = year and "%m" in directives and "%d" in directives, "a year, a month and a day"
        if not ok:
            raise TemplateError(f"format {fmt!r} of {{{name}}} in {template!r} must have {need}")
    for a, b in zip(out, out[1:]):
        if a[0] == "ph" and b[0] == "ph":
            raise TemplateError(f"placeholders {{{a[1]}}}{{{b[1]}}} must be separated by literal text in {template!r}")
    if "/" in "".join(t for k, t, _ in out if k == "lit"):
        raise TemplateError(f"template must describe a file name only (no '/'): {template!r}")
    return out


def _pattern(parsed: list[tuple]) -> str:
    out = ""
    for kind, text, fmt in parsed:
        if kind == "lit":
            out += re.escape(text)
        elif text == "RUNTY":
            out += r"(?P<runty>[A-Za-z0-9]+)"
        elif text == "ANY":
            out += r".+?"
        elif text == "VERSION":
            out += f"(?P<version>{_VERSION_ANY})"
        else:
            out += f"(?P<{text.lower()}>{date_format(fmt)[1] if fmt else _TS_DEFAULT})"
    return out


def compile_template(template: str, parts: Optional[Mapping[str, Optional[str]]] = None) -> re.Pattern:
    return re.compile(_pattern(_parse(template, parts)), re.IGNORECASE)


def render(template: str, *, runty: str, rpt_start: date, rpt_end: date, ts: datetime,
           parts: Optional[Mapping[str, Optional[str]]] = None) -> str:
    """Build a file name from a template (used by validator overlap tests and by tests)."""
    values = {"RUNTY": lambda f: runty, "RPTSTART": lambda f: rpt_start.strftime(date_format(f)[0]),
              "RPTEND": lambda f: rpt_end.strftime(date_format(f)[0]),
              "RPTMONTH": lambda f: rpt_start.strftime(date_format(f)[0]),
              "TS": lambda f: ts.strftime(date_format(f)[0] if f else "%Y%m%d%H%M%S"), "ANY": lambda f: "X"}
    return "".join(values[t](f) if k == "ph" else t for k, t, f in _parse(template, parts))


class TemplateMatcher:
    def __init__(self, configs: Iterable[FileConfig]):
        self._compiled: list[tuple[FileConfig, re.Pattern, dict]] = []
        self._any_version: list[tuple[FileConfig, re.Pattern]] = []
        self.invalid: list[tuple[FileConfig, str]] = []
        flags = re.IGNORECASE
        for c in configs:
            try:
                parsed = _parse(c.src_file_nm_tmplt, c.name_parts)
                self._compiled.append((c, re.compile(_pattern(parsed), flags),
                                       {name: fmt for kind, name, fmt in parsed if kind == "ph"}))
            except TemplateError as e:
                self.invalid.append((c, str(e)))
                continue
            if "{VERSION" in c.src_file_nm_tmplt.upper():
                try:
                    self._any_version.append((c, re.compile(_pattern(_parse(c.src_file_nm_tmplt, c.name_parts, True)), flags)))
                except TemplateError:
                    pass

    @property
    def configs(self) -> Sequence[FileConfig]:
        return [c for c, _, _ in self._compiled]

    def candidates(self, name: str) -> list[tuple[FileConfig, re.Match]]:
        return [(c, m) for c, rx, _ in self._compiled if (m := rx.fullmatch(name))]

    def _wrong_version(self, name: str) -> Optional[MatchError]:
        near = [(c, m.group("version")) for c, rx in self._any_version if (m := rx.fullmatch(name))]
        if not near:
            return None
        expected = sorted({c.file_vrsn_cd for c, _ in near})
        return MatchError("FILE_REJECTED_INVALID_TOKEN",
                          f"{name}: version {near[0][1]!r} in the file name is not the configured version "
                          f"({', '.join(expected)})", near[0][0] if len(near) == 1 else None)

    def match(self, name: str) -> MatchResult:
        found = self.candidates(name)
        if not found:
            raise self._wrong_version(name) or MatchError("FILE_REJECTED_UNPARSEABLE",
                                                          f"{name} matches no active filename template")
        if len(found) > 1:
            ids = [c.cfg_id for c, _ in found]
            raise MatchError("FILE_REJECTED_AMBIGUOUS_TEMPLATE", f"{name} matches several templates (Cfg_ID {ids})")
        cfg, m = found[0]
        formats = next(f for c, _, f in self._compiled if c is cfg)
        got = m.groupdict()
        try:
            if "rptmonth" in got:
                start = datetime.strptime(got["rptmonth"], date_format(formats["RPTMONTH"])[0]).date().replace(day=1)
                end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            else:
                start = datetime.strptime(got["rptstart"], date_format(formats["RPTSTART"])[0]).date()
                end = datetime.strptime(got["rptend"], date_format(formats["RPTEND"])[0]).date()
        except ValueError:
            used = sorted({formats[n] for n in ("RPTMONTH", "RPTSTART", "RPTEND") if n in formats})
            raise MatchError("FILE_REJECTED_INVALID_TOKEN", f"{name}: report dates are not valid {' / '.join(used)}", cfg)
        if end < start:
            raise MatchError("FILE_REJECTED_INVALID_TOKEN", f"{name}: report end date is before start date", cfg)
        ts = None
        if "ts" in got:
            fmt = formats["TS"]
            try:
                ts = datetime.strptime(got["ts"], date_format(fmt)[0] if fmt else
                                       "%Y%m%d%H%M%S" if len(got["ts"]) == 14 else "%Y%m%d%H%M")
            except ValueError:
                raise MatchError("FILE_REJECTED_INVALID_TOKEN",
                                 f"{name}: {{TS}} is not a valid {fmt or 'YYYYMMDDHHMM or YYYYMMDDHHMMSS'}", cfg)
        return MatchResult(cfg, got["runty"], start, end, ts)


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = "ERROR"


def validate_all(conn: Connection, settings=None, run_date: Optional[date] = None) -> list[Issue]:
    """Validate the configuration tables and the target tables they point to."""
    issues: list[Issue] = []

    def add(code: str, msg: str, severity: str = "ERROR") -> None:
        issues.append(Issue(code, msg, severity))

    for table, column in (("ComplianceProject", "Project_Cd"), ("ComplianceSourceSystem", "Src_ID"),
                          ("ComplianceRunType", "Run_Ty")):
        for r in conn.execute(f"SELECT UPPER({column}) AS cd, count(*) AS n FROM {table} GROUP BY UPPER({column}) "
                              "HAVING count(*) > 1").fetchall():
            add("CODE_CASE_DUPLICATE", f"{table}: {r['n']} rows have {column} {r['cd']} in different upper / lower case")
    rts = run_types(conn)
    from compliance_framework.batches import (batch_due, check_period, check_schedule, intake_problem, report_dates,
                                              scheduled)
    from compliance_framework.load import VALUE_CODES
    for rt in rts.values():
        if not rt.run_ty.isalnum():
            add("RUN_TYPE", f"run type {rt.run_ty!r} must be letters and digits only (it is the {{RUNTY}} token)")
        if rt.run_category_cd not in (SCHEDULED, ADHOC) or rt.sla_days < 1:
            add("RUN_TYPE", f"run type {rt.run_ty}: category must be SCHEDULED/ADHOC and SLA_Days >= 1")
        problem = check_schedule(rt)
        if problem:
            add("RUN_TYPE", f"run type {rt.run_ty}: {problem}")
    projects = {code(r["project_cd"]): r["active_ind"] == 1
                for r in conn.execute("SELECT Project_Cd, Active_Ind FROM ComplianceProject").fetchall()}
    sources = {code(r["src_id"]) for r in conn.execute("SELECT Src_ID FROM ComplianceSourceSystem").fetchall()}
    xw_all = xwalk_rows(conn, switched_off=True)
    xw = [x for x in xw_all if x.include]

    if db.table_exists(conn, conn.schema, "ComplianceRunTypePeriod"):
        for r in conn.execute("SELECT * FROM ComplianceRunTypePeriod WHERE Active_Ind = 1").fetchall():
            label = f"ComplianceRunTypePeriod {code(r['project_cd'])}/{code(r['table_nm'] or '*')}/{code(r['run_ty'])}"
            if code(r["project_cd"]) not in projects:
                add("RUN_TYPE_PERIOD", f"{label}: project {code(r['project_cd'])} is not in ComplianceProject")
            if code(r["run_ty"]) not in rts:
                add("RUN_TYPE_PERIOD", f"{label}: run type {code(r['run_ty'])} is not in ComplianceRunType")
            period, sql = _text(r.get("rpt_period_cd")), _text(r.get("rpt_dt_sql_txt"))
            if period and sql:
                add("RUN_TYPE_PERIOD", f"{label}: set Rpt_Period_Cd or Rpt_Dt_Sql_Txt, not both (the code is used)")
            elif not period and not sql:
                add("RUN_TYPE_PERIOD", f"{label}: needs Rpt_Period_Cd or Rpt_Dt_Sql_Txt")
            elif sql and not re.match(r"^\s*(SELECT|WITH)\b[^;]*;?\s*$", sql, re.IGNORECASE):
                add("RUN_TYPE_PERIOD", f"{label}: Rpt_Dt_Sql_Txt must be a single SELECT statement")
            if period:
                try:
                    check_period(period)
                except ConfigError as e:
                    add("RUN_TYPE_PERIOD", f"{label}: {e}")

    run_date, cache, due = run_date or date.today(), {}, {}
    for x in xw:
        rt = rts.get(x.run_ty)
        if rt and rt.active and rt.run_category_cd == SCHEDULED and rt.sched_cd and not (x.rpt_period_cd or x.rpt_dt_sql):
            add("RPT_PERIOD", f"{x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty}: no report period (Rpt_Period_Cd on "
                              "the crosswalk row or a ComplianceRunTypePeriod row)")
        if x.rpt_period_cd and x.rpt_dt_sql:
            add("RPT_PERIOD", f"xwalk {x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty}: set Rpt_Period_Cd or "
                              "Rpt_Dt_Sql_Txt, not both (the code is used)")
        if x.rpt_period_cd:
            try:
                check_period(x.rpt_period_cd)
            except ConfigError as e:
                add("RPT_PERIOD", f"Rpt_Period_Cd of {x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty}: {e}")
        if not scheduled(rt, x) or not rt.has_schedule or not x.effective_on(run_date) or check_schedule(rt):
            continue
        key = (x.project_cd, x.run_ty)
        if key not in due:
            try:
                due[key] = batch_due(conn, rt, run_date, x.project_cd, cache)
            except ConfigError as e:
                due[key] = e
                add("BATCH_SCHEDULE_SQL", f"project {x.project_cd}: {e}")
        if isinstance(due[key], dict):
            try:
                report_dates(conn, x, run_date, due[key], cache)
            except ConfigError as e:
                add("RPT_DT_SQL", str(e))
        elif x.rpt_dt_sql and not re.match(r"^\s*(SELECT|WITH)\b[^;]*;?\s*$", x.rpt_dt_sql, re.IGNORECASE):
            add("RPT_DT_SQL", f"Rpt_Dt_Sql_Txt of {x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty} must be a single "
                              "SELECT statement")
    for r in conn.execute("SELECT * FROM ComplianceRequestInTake WHERE Req_End_Dt_Key >= %s ORDER BY Intake_ID",
                          (run_date,)).fetchall():
        problem = intake_problem(conn, r, rts, settings)
        if problem:
            add("INTAKE_REQUEST", f"ComplianceRequestInTake {r['intake_id']} ({code(r['project_cd'])}/{code(r['table_nm'])}/"
                                  f"{code(r['run_ty'])}): {problem}")
    if db.table_exists(conn, conn.schema, "ComplianceBatchCalendar"):
        for r in conn.execute("SELECT * FROM ComplianceBatchCalendar WHERE Active_Ind = 1").fetchall():
            c = CalendarRow.from_row(r)
            label = f"ComplianceBatchCalendar {c.project_cd}/{c.table_nm}/{c.run_ty} {c.req_dt}"
            if c.project_cd not in projects:
                add("BATCH_CALENDAR", f"{label}: project {c.project_cd} is not in ComplianceProject")
            if c.run_ty not in rts:
                add("BATCH_CALENDAR", f"{label}: run type {c.run_ty} is not in ComplianceRunType")
            if c.rpt_end < c.rpt_start:
                add("BATCH_CALENDAR", f"{label}: Rpt_End_Dt_Key is before Rpt_Start_Dt_Key")
    cfgs = active_file_configs(conn, settings)

    checks = file_checks(conn)
    from compliance_framework.load import header_columns

    for c in cfgs:
        try:
            opts = check_options(c, checks)
        except ConfigError as e:
            add("FILE_CHECK_CONFIG", str(e))
            continue
        extra = 1 if opts.record_types else 0
        counts = {k: v for k, v in (("COLUMN_COUNT", opts.column_count),
                                    ("TARGET_COLUMNS", len(opts.target_columns) + extra if opts.target_columns else None),
                                    ("HEADER_NAMES", len(header_columns(c, opts)) + extra if opts.header else None)) if v}
        if len(set(counts.values())) > 1:
            add("FILE_CHECK_CONFIG", f"file config {c.cfg_id} ({c.project_cd}/{c.table_nm}/{c.src_id}): the field counts "
                                     "do not agree: " + ", ".join(f"{k} {v}" for k, v in counts.items())
                                     + (" (record type included)" if extra else ""))
    for k in checks:
        if k.check_cd not in CHECKS:
            add("FILE_CHECK_CONFIG", f"{k.label}: unknown Check_Cd; accepted: {', '.join(CHECKS)}")
        if k.project_cd not in projects:
            add("FILE_CHECK_CONFIG", f"{k.label}: project {k.project_cd} is not in ComplianceProject")
        elif not any(c.project_cd == k.project_cd and k.table_nm in ("*", c.table_nm) and k.src_id in ("*", c.src_id)
                     for c in cfgs):
            add("FILE_CHECK_NO_FILE", f"{k.label}: matches no active file config", "WARNING")
    seen_cfg: dict[tuple, int] = {}
    for c in cfgs:
        key = (c.project_cd, c.table_nm, c.src_id)
        if key in seen_cfg:
            add("FILE_CONFIG_DUPLICATE", f"file configs {seen_cfg[key]} and {c.cfg_id} are both active for "
                                         f"{c.project_cd}/{c.table_nm}/{c.src_id}")
        seen_cfg.setdefault(key, c.cfg_id)
    for r in conn.execute("""SELECT Req_ID, UPPER(Override_Ty) AS override_ty, count(*) AS n FROM ComplianceBatchOverride
                              WHERE UPPER(Apprvl_Stat) IN ('PENDING_REVIEW','APPROVED')
                                AND NOT (UPPER(Override_Ty) = 'REOPEN' AND EXISTS (SELECT 1 FROM ComplianceRequestFileDetail d
                                         WHERE d.Ovrd_ID = ComplianceBatchOverride.Ovrd_ID AND d.Event_Ty = 'BATCH_REOPENED'))
                              GROUP BY Req_ID, UPPER(Override_Ty) HAVING count(*) > 1""").fetchall():
        add("OVERRIDE_DUPLICATE", f"batch {r['req_id']} has {r['n']} open {r['override_ty']} overrides")
    cfg_sources = {(c.project_cd, c.table_nm, c.src_id) for c in cfgs}
    queries = active_query_configs(conn, settings)
    query_sources = {(q.project_cd, q.table_nm, q.src_id) for q in queries}
    xw_by_source: dict[tuple, list[XwalkRow]] = defaultdict(list)
    for x in xw_all:
        if x.include:
            xw_by_source[(x.project_cd, x.table_nm, x.src_id)].append(x)
        label = f"xwalk {x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty}@{x.effective_start_dt}"
        if x.run_ty not in rts:
            add("XWALK_RUN_TYPE", f"{label}: run type {x.run_ty} is not in ComplianceRunType")
        elif not rts[x.run_ty].active:
            add("XWALK_RUN_TYPE", f"{label}: run type {x.run_ty} is inactive", "WARNING")
        if x.project_cd not in projects:
            add("XWALK_PROJECT", f"{label}: project {x.project_cd} is not in ComplianceProject")
        elif not projects[x.project_cd]:
            add("XWALK_PROJECT", f"{label}: project {x.project_cd} is inactive", "WARNING")
        if x.src_id not in sources:
            add("XWALK_SOURCE", f"{label}: source {x.src_id} is not in ComplianceSourceSystem")
        if x.include and (x.project_cd, x.table_nm, x.src_id) not in cfg_sources | query_sources:
            add("XWALK_NO_FILE_CONFIG", f"{label}: no active ComplianceSourceFileConfig or ComplianceInternalSourceConfig")
    by_source_all: dict[tuple, list[XwalkRow]] = defaultdict(list)
    for x in xw_all:
        by_source_all[(x.project_cd, x.table_nm, x.src_id)].append(x)
    for rows in by_source_all.values():
        for a, b in combinations(rows, 2):
            if (a.run_ty == b.run_ty and a.listed == b.listed
                    and (b.effective_end_dt is None or a.effective_start_dt <= b.effective_end_dt)
                    and (a.effective_end_dt is None or b.effective_start_dt <= a.effective_end_dt)):
                add("XWALK_OVERLAP", f"xwalk {a.project_cd}/{a.table_nm}/{a.src_id}/{a.run_ty}: effective windows "
                                     f"starting {a.effective_start_dt} and {b.effective_start_dt} overlap")

    from compliance_framework.load import delimiter_for, file_layout

    targets = target_columns(conn)
    for t in targets:
        if t.value_cd not in VALUE_CODES:
            add("TARGET_COLUMN", f"{t.label}: unknown Value_Cd {t.value_cd!r}; accepted: {', '.join(VALUE_CODES)}")
        if t.project_cd not in projects:
            add("TARGET_COLUMN", f"{t.label}: project {t.project_cd} is not in ComplianceProject")
        names = {n.lower() for n in re.findall(r"\{(\w+)\}", t.value_txt or "")} if t.value_cd == "SQL" else set()
        if "btch_id" in names and not names & {"src_id", "req_id"}:
            add("TARGET_COLUMN", f"{t.label}: Value_Txt uses {{btch_id}} without {{src_id}}; a batch ID covers every "
                                 "source of the table, so add {src_id} (or use {req_id} for the one batch row)", "WARNING")

    for c in cfgs:
        label = f"file config {c.cfg_id} ({c.project_cd}/{c.table_nm}/{c.src_id})"
        try:
            compile_template(c.src_file_nm_tmplt, c.name_parts)
        except TemplateError as e:
            add("TEMPLATE", f"{label}: {e}")
        try:
            delimiter_for(c)
        except ConfigError as e:
            add("DELIMITER", str(e))
        if not c.src_file_ty:
            add("TEMPLATE_EXTENSION", f"{label}: template must end with a file extension such as .txt")
        if (c.project_cd, c.table_nm, c.src_id) not in xw_by_source:
            add("FILE_CONFIG_NO_XWALK", f"{label}: no active crosswalk row")
        try:
            parse_uri(c.src_file_path)
        except ValueError as e:
            add("PATH", f"{label}: {e}")
        try:
            opts = check_options(c, checks)
        except ConfigError:
            opts = None
        try:
            layout = file_layout(conn, c, targets, opts)
            if layout.names and not layout.targets:
                free = {n.casefold() for n in layout.free_cols}
                unmatched = [n for n in layout.names if n.casefold() not in free]
                if unmatched and len(unmatched) < len(layout.names):
                    add("HEADER_NAMES_PARTIAL", f"{label}: header names that are not staging columns: "
                        f"{', '.join(unmatched[:10])}{' ...' if len(unmatched) > 10 else ''}; the fields load by "
                        "position into the staging table's data columns (check their order, or add TARGET_COLUMNS)",
                        "WARNING")
        except (LookupError, ConfigError) as e:
            add("TARGET_TABLE", f"{label}: {e}")

    from compliance_framework.load import sql_body
    for q in queries:
        label = q.label
        if q.db_ty not in SOURCE_DB_TYPES:
            add("SOURCE_QUERY", f"{label}: Src_Db_Ty {q.db_ty!r}; accepted: {', '.join(SOURCE_DB_TYPES)}")
        if q.db_ty == "SQLSERVER" and not q.conn_cd:
            add("SOURCE_QUERY", f"{label}: a SQLSERVER source needs Src_Conn_Cd (the Airflow Connection of its server)")
        if q.project_cd not in projects:
            add("SOURCE_QUERY", f"{label}: project {q.project_cd} is not in ComplianceProject")
        if q.src_id not in sources:
            add("SOURCE_QUERY", f"{label}: source {q.src_id} is not in ComplianceSourceSystem")
        if (q.project_cd, q.table_nm, q.src_id) not in xw_by_source:
            add("SOURCE_QUERY", f"{label}: no active crosswalk row")
        if (q.project_cd, q.table_nm, q.src_id) in cfg_sources:
            add("SOURCE_QUERY", f"{label}: the source also has an active file config")
        try:
            sql_body(label, q.sql)
        except ConfigError as e:
            add("SOURCE_QUERY", str(e))
        try:
            file_layout(conn, q, targets, check_options(q, checks))
        except (LookupError, ConfigError) as e:
            add("TARGET_TABLE", f"{label}: {e}")
    from compliance_framework.load import columns
    table_cols: dict[tuple, set] = defaultdict(set)
    for c in [*cfgs, *queries]:
        for schema, table in ((c.stg_schema_nm, c.stg_table_nm), (c.core_schema_nm, c.core_table_nm)):
            table_cols[(c.project_cd, c.table_nm)] |= {col.name for col in columns(conn, schema, table)}
    for t in targets:
        found = [cols for (p, tb), cols in table_cols.items() if p == t.project_cd and t.table_nm in ("*", tb)]
        if found and not any(t.column_nm in cols for cols in found):
            add("TARGET_COLUMN", f"{t.label}: column {t.column_nm} is in no staging or core table of "
                                 f"{t.project_cd}/{t.table_nm}")

    from compliance_framework.load import BATCH_COLUMN, batch_column, column_mapping, export_plan
    cores = {(c.project_cd, c.table_nm): c for c in [*cfgs, *queries]}
    for e in active_export_configs(conn, settings):
        label = e.label
        if e.project_cd not in projects:
            add("EXPORT_CONFIG", f"{label}: project {e.project_cd} is not in ComplianceProject")
        src = cores.get((e.project_cd, e.table_nm))
        if src is None:
            add("EXPORT_CONFIG", f"{label}: the table has no active file or query config", "WARNING")
            continue
        unknown = [a.value for a in e.proc_params if a.kind == "PARAM" and a.value not in EXPORT_PROC_PARAMS]
        if e.proc_nm and unknown:
            add("EXPORT_CONFIG", f"{label}: Exp_Proc_Parm_Txt uses {', '.join(unknown)}; available: "
                                 f"{', '.join('{' + n + '}' for n in EXPORT_PROC_PARAMS)}")
        outs = [a.value for a in e.proc_params if a.kind == "OUT"]
        if e.proc_nm and any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", o) for o in outs):
            add("EXPORT_CONFIG", f"{label}: Exp_Proc_Parm_Txt has {', '.join(outs)} as OUT parameter name(s); "
                                 "a fixed value needs quotes ('...'), a placeholder braces ({...})")
        if e.proc_nm and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}(\.[A-Za-z_][A-Za-z0-9_]{0,127})?", e.proc_nm):
            add("EXPORT_CONFIG", f"{label}: Exp_Proc_Nm {e.proc_nm!r} must be [database.]procedure")
        for what, text, allowed, select in (("Exp_Sql_Txt", e.sql_txt, EXPORT_PROC_PARAMS, True),
                                            ("Exp_Pre_Sql_Txt", e.pre_sql, EXPORT_BATCH_PARAMS, False),
                                            ("Exp_Post_Sql_Txt", e.post_sql, EXPORT_BATCH_PARAMS, False)):
            if text:
                try:
                    export_statement(f"{label}: {what}", text, allowed, select)
                except ConfigError as err:
                    add("EXPORT_CONFIG", str(err))
        if e.proc_nm and e.sql_txt:
            add("EXPORT_CONFIG", f"{label}: Exp_Proc_Nm and Exp_Sql_Txt are both set; the procedure is used", "WARNING")
        exp = {c.name for c in columns(conn, e.exp_schema_nm, e.exp_table_nm) if not c.auto}
        if not exp:
            add("EXPORT_CONFIG", f"{label}: export table {e.exp_schema_nm}.{e.exp_table_nm} not found")
        elif not batch_column(exp, column_mapping(src, targets)):
            add("EXPORT_CONFIG", f"{label}: export table {e.exp_schema_nm}.{e.exp_table_nm} lacks the batch column "
                                 f"{BATCH_COLUMN} (or a column mapped to BATCH_ID)")
        elif not (e.proc_nm or e.sql_txt):
            exp_list = [c for c in columns(conn, e.exp_schema_nm, e.exp_table_nm) if not c.auto]
            seen = set()
            for x in [*cfgs, *queries]:
                if (x.project_cd, x.table_nm) != (e.project_cd, e.table_nm) or (x.core_schema_nm, x.core_table_nm) in seen:
                    continue
                seen.add((x.core_schema_nm, x.core_table_nm))
                mapping = column_mapping(x, targets)
                core_list = [c for c in columns(conn, x.core_schema_nm, x.core_table_nm) if not c.auto]
                core_batch = batch_column((c.name for c in core_list), mapping)
                if not core_list or core_batch is None:
                    continue
                plan = export_plan(exp_list, core_list, mapping, batch_column(exp, mapping), core_batch)
                where = f"{label}: from core {x.core_schema_nm}.{x.core_table_nm}"
                renamed = [f"{s} -> {c.name}" for c, s in plan if s and s.casefold() != c.name.casefold()
                           and mapping.get(c.name) is None and c.name.casefold() != batch_column(exp, mapping).casefold()]
                if renamed:
                    log.info("%s by column position: %s", where, ", ".join(renamed))
                empty = [c.name for c, s in plan if s is None
                         and mapping.get(c.name) not in ("CURRENT_FLAG", "END_TIMESTAMP")]
                if empty:
                    add("EXPORT_CONFIG", f"{where}: export columns with no core column (loaded as NULL): "
                                         f"{', '.join(empty)}", "WARNING")

    for r in conn.execute("SELECT * FROM ComplianceRuleBinding WHERE Active_Ind = 1 "
                          "ORDER BY Project_Cd, Table_Nm, Src_ID, Run_Ty").fetchall():
        if code(r["project_cd"]) not in projects:
            add("RULE_BINDING_PROJECT", f"rule binding project {r['project_cd']} is not in ComplianceProject")
        label = (f"rule binding {r['project_cd']}/{r['table_nm']}/{r['src_id']}/{r['run_ty']} "
                 f"{r['gre_rule_group']}:{r['gre_rule_variant']}")
        if not any(x.project_cd == code(r["project_cd"]) and code(r["table_nm"]) in ("*", x.table_nm)
                   and code(r["src_id"]) in ("*", x.src_id) and code(r["run_ty"]) in ("*", x.run_ty) for x in xw):
            add("RULE_BINDING_NO_XWALK", f"{label}: matches no active crosswalk row")

    matcher = TemplateMatcher(cfgs)
    for c in matcher.configs:
        for rt in sorted({x.run_ty for x in xw_by_source.get((c.project_cd, c.table_nm, c.src_id), ())}) or ["X1"]:
            name = render(c.src_file_nm_tmplt, runty=rt, rpt_start=date(2026, 1, 1), rpt_end=date(2026, 1, 31),
                          ts=datetime(2026, 2, 1, 9, 30, 0), parts=c.name_parts)
            others = [o.cfg_id for o, _ in matcher.candidates(name) if o.cfg_id != c.cfg_id]
            if others:
                add("TEMPLATE_OVERLAP", f"file config {c.cfg_id}: sample name {name} also matches {others}")
    return issues


# scope: What a step works on: a project, one table group or one table of it, and optionally one run type and some
# sources.

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scope:
    """`tables` = the tables of the table group (or the one table); None = every table of the project(s).
    `src_ids` = the Src_IDs of the sources given; None = every source."""
    project_cd: Optional[str] = None
    table_group: Optional[str] = None
    table_nm: Optional[str] = None
    run_ty: Optional[str] = None
    tables: Optional[tuple] = None
    src_ids: Optional[tuple] = None

    @property
    def narrowed(self) -> bool:
        """Only part of a project's files belong to this run."""
        return self.tables is not None or self.run_ty is not None or self.src_ids is not None

    def covers(self, project_cd: Optional[str], table_nm: Optional[str] = None, run_ty: Optional[str] = None,
               src_id: Optional[str] = None) -> bool:
        if self.project_cd and code(project_cd) != self.project_cd:
            return False
        if self.tables is not None and table_nm is not None and code(table_nm) not in self.tables:
            return False
        if self.src_ids is not None and src_id is not None and code(src_id) not in self.src_ids:
            return False
        return not (self.run_ty and run_ty is not None and code(run_ty) != self.run_ty)

    def sql(self, alias: str = "", run_type: bool = True) -> tuple[str, list]:
        """(' AND ...' conditions, parameters) on Project_Cd / Table_Nm / Run_Ty / Src_ID of a table or alias."""
        a = f"{alias}." if alias else ""
        parts, params = [], []
        if self.project_cd:
            parts.append(f"{a}Project_Cd = %s")
            params.append(self.project_cd)
        if self.tables is not None:
            if self.tables:
                parts.append(f"{a}Table_Nm IN ({', '.join(['%s'] * len(self.tables))})")
                params.extend(self.tables)
            else:
                parts.append("1 = 0")
        if run_type and self.run_ty:
            parts.append(f"{a}Run_Ty = %s")
            params.append(self.run_ty)
        if self.src_ids is not None:
            parts.append(f"{a}Src_ID IN ({', '.join(['%s'] * len(self.src_ids))})")
            params.extend(self.src_ids)
        return "".join(f" AND {p}" for p in parts), params

    def label(self) -> str:
        parts = [f"project {self.project_cd}" if self.project_cd else "every project"]
        if self.table_group:
            parts.append(f"table group {self.table_group} ({', '.join(self.tables) or 'no active table'})")
        if self.table_nm:
            parts.append(f"table {self.table_nm}")
        if self.run_ty:
            parts.append(f"run type {self.run_ty}")
        if self.src_ids is not None:
            parts.append(f"source {', '.join(self.src_ids)}")
        return ", ".join(parts)


def code_list(value) -> tuple:
    """The codes of a scope input that takes several: 'ODAG, FA', a list, or a list as text ("['ODAG', 'FA']")."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").strip("[] ").split(",")
    return tuple(code(str(v).strip(" '\"")) for v in items if str(v).strip(" '\""))


def source_ids(conn, values: tuple) -> tuple:
    """The Src_IDs of sources given by Src_ID or by Src_Nm (a name may stand for several IDs)."""
    try:
        rows = conn.execute("SELECT Src_ID, Src_Nm FROM ComplianceSourceSystem").fetchall()
    except Exception as e:  # noqa: BLE001
        raise ConfigError(f"source {', '.join(values)}: ComplianceSourceSystem could not be read: {db.error_line(e)}") from None
    ids = []
    for v in values:
        found = ([code(r["src_id"]) for r in rows if code(r["src_id"]) == v]
                 or [code(r["src_id"]) for r in rows if code(r["src_nm"]) == v])
        if not found:
            raise ConfigError(f"source {v} is neither a Src_ID nor a Src_Nm of ComplianceSourceSystem")
        ids.extend(found)
    return tuple(dict.fromkeys(ids))


def resolve_scope(conn, project: Optional[str] = None, table_group: Optional[str] = None,
                  table: Optional[str] = None, run_type: Optional[str] = None, source=None) -> Scope:
    """The scope of a run. Table groups, tables and sources are one or a list each. A table group is looked up in
    ComplianceTable only when one is given; a source (by Src_ID or Src_Nm) in ComplianceSourceSystem."""
    project, run_type = (code(v) or None for v in (project, run_type))
    groups, names = code_list(table_group), code_list(table)
    if (groups or names) and not project:
        raise ConfigError("a table group or a table needs its project")
    if groups and names:
        raise ConfigError("give table groups or tables, not both")
    tables = None
    if names:
        tables = tuple(dict.fromkeys(names))
    elif groups:
        marks, values = db.in_list(groups)
        try:
            rows = conn.execute(f"SELECT Table_Nm, Table_Grp_Cd FROM ComplianceTable WHERE UPPER(Project_Cd) = %s "
                                f"AND UPPER(Table_Grp_Cd) IN ({marks}) AND Active_Ind = 1", (project, *values)).fetchall()
        except Exception as e:  # noqa: BLE001
            raise ConfigError(f"table group {', '.join(groups)}: ComplianceTable could not be read: "
                              f"{db.error_line(e)}") from None
        tables = tuple(sorted({code(r["table_nm"]) for r in rows}))
        found = {code(r["table_grp_cd"]) for r in rows}
        for g in groups:
            if g not in found:
                log.warning("table group %s of project %s has no active table in ComplianceTable: nothing to do for it",
                            g, project)
    sources = code_list(source)
    return Scope(project, ", ".join(dict.fromkeys(groups)) or None, ", ".join(dict.fromkeys(names)) or None, run_type,
                 tables, source_ids(conn, sources) if sources else None)


# filecheck_options: Which content checks a file gets (ComplianceFileCheckConfig); read by FILE_CHECK and by the load.

TRAILER_FIELDS = ("RECORD_TYPE", "FILE_NAME", "DATA_ROW_COUNT", "TOTAL_ROW_COUNT", "TIMESTAMP", "SKIP")
FIELD_CHECKS = ("MIN_LENGTH", "MAX_LENGTH", "MIN_VALUE", "MAX_VALUE", "VALUE_IN", "PATTERN", "DATE_FORMAT")
LIMIT_CHECKS = ("MIN_RECORD_LENGTH", "MAX_RECORD_LENGTH", "MIN_ROWS", "MAX_ROWS")
CHECKS = ("RECORD_TYPE", "HEADER_NAMES", "COLUMN_COUNT", "TARGET_COLUMNS", "TRAILER", "CHARACTER_SET", "COLUMN_VALUES",
          *LIMIT_CHECKS, *FIELD_CHECKS)
_DATE_TOKENS = (("YYYY", "%Y"), ("MM", "%m"), ("DD", "%d"), ("HH", "%H"), ("MI", "%M"), ("SS", "%S"))
VALUE_RULES = ("LENGTH", "NUMBER", "REQUIRED")
COUNT_FIELDS = ("DATA_ROW_COUNT", "TOTAL_ROW_COUNT")
_TIMESTAMP_FORMATS = {8: "%Y%m%d", 12: "%Y%m%d%H%M", 14: "%Y%m%d%H%M%S"}


@dataclass(frozen=True)
class CheckOptions:
    record_types: Optional[tuple[str, str, str]] = None
    trailer_fields: tuple[str, ...] = ()
    header: Optional[str] = None
    charset: Optional[str] = None
    value_rules: tuple = ()
    column_count: Optional[int] = None
    target_columns: tuple = ()
    limits: tuple = ()
    field_rules: tuple = ()

    def limit(self, check_cd: str) -> Optional[int]:
        return next((v for c, v in self.limits if c == check_cd), None)


@dataclass(frozen=True)
class FieldRule:
    """A check on one field of every data row: check code, field name, the parsed value and its text."""
    check_cd: str
    field: str
    value: object
    text: str
    label: str


def _field_rule(k: "FileCheck") -> FieldRule:
    text = k.check_val_txt.strip()
    try:
        if k.check_cd in ("MIN_LENGTH", "MAX_LENGTH"):
            value = int(text)
            if value < 0:
                raise ValueError
        elif k.check_cd in ("MIN_VALUE", "MAX_VALUE"):
            value = Decimal(text)
        elif k.check_cd == "VALUE_IN":
            value = frozenset(v.strip().casefold() for v in text.split("|" if "|" in text else ","))
        elif k.check_cd == "PATTERN":
            value = re.compile(text, re.IGNORECASE)
        else:
            value = text.upper()
            for token, directive in _DATE_TOKENS:
                value = value.replace(token, directive)
            datetime.strptime(datetime(2026, 1, 31, 23, 59, 58).strftime(value), value)
    except (ValueError, InvalidOperation, re.error):
        raise ConfigError(f"{k.label}: Check_Val_Txt {text!r} is not valid for {k.check_cd}") from None
    return FieldRule(k.check_cd, k.field_nm, value, text, k.label)

def _names(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(i).strip() for i in items if str(i).strip()]


def check_options(cfg, checks) -> CheckOptions:
    """The content checks of a file config, from the ComplianceFileCheckConfig rows.

    Inactive rows are ignored. The rows add up: every check (Check_Cd and Check_Field_Nm; '*' = the whole file or
    row) set for the project, the table or the source applies. For the same check the most specific active row
    wins: its table and source, then its table, then its source, then '*'. Include_Ind of that row says whether the
    check applies (1) or is switched off (0) for the file.
    """
    chosen: dict = {}
    for k in checks:
        if (not k.active or k.project_cd != cfg.project_cd or k.table_nm not in ("*", cfg.table_nm)
                or k.src_id not in ("*", cfg.src_id)):
            continue
        rank = 2 * (k.table_nm != "*") + (k.src_id != "*")
        key = (k.check_cd, k.field_nm)
        if key not in chosen or rank > chosen[key][0]:
            chosen[key] = (rank, k)
    values, field_rules, limits = {}, [], []
    for (check_cd, field), (_, k) in sorted(chosen.items()):
        if not k.include or check_cd not in CHECKS:
            continue
        if not k.check_val_txt:
            raise ConfigError(f"{k.label}: Check_Val_Txt is empty")
        if (check_cd in FIELD_CHECKS) != (field != "*"):
            raise ConfigError(f"{k.label}: {check_cd} " + ("needs Check_Field_Nm (the field it checks)"
                                                          if check_cd in FIELD_CHECKS else "takes no Check_Field_Nm"))
        if check_cd in FIELD_CHECKS:
            field_rules.append(_field_rule(k))
        elif check_cd in LIMIT_CHECKS:
            text = k.check_val_txt.strip()
            if not text.isdigit():
                raise ConfigError(f"{k.label}: Check_Val_Txt must be a whole number")
            limits.append((check_cd, int(text)))
        else:
            values[check_cd] = k
    types = [t.upper() for t in _names(values["RECORD_TYPE"].check_val_txt)] if "RECORD_TYPE" in values else []
    if types and len(types) != 3:
        raise ConfigError(f"{values['RECORD_TYPE'].label}: Check_Val_Txt must be three values: header, detail, trailer "
                          "(for example H,D,T)")
    fields = tuple(f.upper() for f in _names(values["TRAILER"].check_val_txt)) if "TRAILER" in values else ()
    bad = [f for f in fields if f not in TRAILER_FIELDS]
    if bad:
        raise ConfigError(f"{values['TRAILER'].label}: Check_Val_Txt has unknown value {bad[0]!r}; "
                          f"accepted: {', '.join(TRAILER_FIELDS)}")
    header = values["HEADER_NAMES"].check_val_txt.strip() if "HEADER_NAMES" in values else None
    charset = values["CHARACTER_SET"].check_val_txt.strip() if "CHARACTER_SET" in values else None
    if charset:
        try:
            codecs.lookup(charset)
        except LookupError:
            raise ConfigError(f"{values['CHARACTER_SET'].label}: Check_Val_Txt {charset!r} is not a character set "
                              "(for example latin-1, ascii, cp1252)") from None
    rules = tuple(r.upper() for r in _names(values["COLUMN_VALUES"].check_val_txt)) if "COLUMN_VALUES" in values else ()
    if rules == ("ALL",):
        rules = VALUE_RULES
    bad = [r for r in rules if r not in VALUE_RULES]
    if bad:
        raise ConfigError(f"{values['COLUMN_VALUES'].label}: Check_Val_Txt has unknown value {bad[0]!r}; "
                          f"accepted: ALL, {', '.join(VALUE_RULES)}")
    count = None
    if "COLUMN_COUNT" in values:
        text = values["COLUMN_COUNT"].check_val_txt.strip()
        if not text.isdigit() or int(text) < 1:
            raise ConfigError(f"{values['COLUMN_COUNT'].label}: Check_Val_Txt must be the number of fields in a row "
                              "(for example 23)")
        count = int(text)
    targets = ()
    if "TARGET_COLUMNS" in values:
        text = values["TARGET_COLUMNS"].check_val_txt
        targets = tuple(n.strip().lower() for n in text.split("|" if "|" in text else ",") if n.strip())
        if len(set(targets)) != len(targets):
            raise ConfigError(f"{values['TARGET_COLUMNS'].label}: Check_Val_Txt names a column twice")
    return CheckOptions(tuple(types) if types else None, fields, header, charset, rules, count, targets, tuple(limits),
                        tuple(field_rules))


def data_fields(opts: CheckOptions, names: tuple) -> Optional[int]:
    """Fields per row the file must have (record type included): COLUMN_COUNT, else TARGET_COLUMNS, else
    HEADER_NAMES; None = not set."""
    if opts.column_count:
        return opts.column_count
    listed = opts.target_columns or names
    return len(listed) + (1 if opts.record_types else 0) if listed else None


def _is_timestamp(value: str) -> bool:
    fmt = _TIMESTAMP_FORMATS.get(len(value))
    if not fmt or not value.isdigit():
        return False
    try:
        datetime.strptime(value, fmt)
    except ValueError:
        return False
    return True


def trailer_problems(rec: list, line_no: int, name: str, opts: CheckOptions, data_rows: int,
                     lines: int) -> list[tuple[str, str]]:
    """[(check, message)] of a trailer record against the file's TRAILER and RECORD_TYPE rows.

    `data_rows` = detail records, `lines` = every non-blank line including the header and the trailer.
    """
    out = []
    trailer_code = opts.record_types[2] if opts.record_types else None
    if trailer_code and rec[0].strip().upper() != trailer_code:
        out.append(("RECORD_TYPE", f"line {line_no}: trailer record type {rec[0]!r}, expected {trailer_code!r}"))
    if not opts.trailer_fields:
        return out
    if len(rec) != len(opts.trailer_fields):
        out.append(("TRAILER", f"line {line_no}: trailer has {len(rec)} fields, expected "
                               f"{len(opts.trailer_fields)} ({', '.join(opts.trailer_fields)})"))
    for value, kind in zip(rec, opts.trailer_fields):
        value = value.strip()
        if kind == "FILE_NAME":
            if value.casefold() != name.casefold():
                out.append(("TRAILER", f"line {line_no}: trailer file name {value!r} is not the name of the file"))
        elif kind in COUNT_FIELDS:
            actual, unit = (data_rows, "data rows") if kind == "DATA_ROW_COUNT" else (lines, "lines")
            if not value.isdigit() or int(value) != actual:
                out.append(("TRAILER", f"line {line_no}: trailer count {value!r}, the file has {actual} {unit}"))
        elif kind == "TIMESTAMP" and not _is_timestamp(value):
            out.append(("TRAILER", f"line {line_no}: trailer timestamp {value!r} is not a valid "
                                   "YYYYMMDD, YYYYMMDDHHMM or YYYYMMDDHHMMSS"))
    return out
