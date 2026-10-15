"""Configuration rows, filename templates and the validator."""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import combinations
from typing import Iterable, Mapping, Optional, Sequence

from . import db
from .adapters import parse_uri
from .db import Connection
from .common import ADHOC, SCHEDULED, ConfigError, code
from .layout import VALUE_CODES



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
    release_mode: str = "ON_LOAD"

    @classmethod
    def from_row(cls, r: dict) -> "RunType":
        return cls(code(r["run_ty"]), code(r["run_category_cd"]), r["sla_days"], r["carry_fwd_ind"] == 1, r["active_ind"] == 1,
                   _text(r.get("batch_schedule_sql_txt")), code(r.get("release_mode_cd") or "ON_LOAD"))


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

    @classmethod
    def from_row(cls, r: dict) -> "XwalkRow":
        return cls(code(r["project_cd"]), code(r["table_nm"]), code(r["src_id"]), code(r["run_ty"]), db.as_date(r["effective_start_dt_key"]),
                   db.as_date(r["effective_end_dt_key"]), code(r["cmplnc_vrsn"]), r["active_ind"] == 1,
                   _text(r.get("rpt_dt_sql_txt")))

    def effective_on(self, d: date) -> bool:
        return self.active and self.effective_start_dt <= d and (self.effective_end_dt is None or d <= self.effective_end_dt)


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

    @classmethod
    def from_row(cls, r: dict) -> "FileCheck":
        return cls(code(r["project_cd"]), code(r["table_nm"]), code(r["src_id"]), code(r["check_cd"]),
                   _text(r["check_val_txt"]), r["active_ind"] == 1, r["include_ind"] == 1)

    @property
    def label(self) -> str:
        return f"ComplianceFileCheckConfig {self.project_cd}/{self.table_nm}/{self.src_id}/{self.check_cd}"


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


def xwalk_rows(conn: Connection, *, project_cd: Optional[str] = None, table_nm: Optional[str] = None,
               src_id: Optional[str] = None, run_ty: Optional[str] = None) -> list[XwalkRow]:
    """Active crosswalk rows, optionally filtered."""
    where, params = ["Active_Ind = 1"], []
    for col, val in (("Project_Cd", project_cd), ("Table_Nm", table_nm), ("Src_ID", src_id), ("Run_Ty", run_ty)):
        if val is not None:
            where.append(f"UPPER({col}) = %s")
            params.append(code(val))
    rows = conn.execute(f"SELECT * FROM ComplianceDataSetSourceXwalk WHERE {' AND '.join(where)} "
                        "ORDER BY Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key", params).fetchall()
    return [XwalkRow.from_row(r) for r in rows]


def effective_sources(conn, project_cd, table_nm, run_ty, on_date: date) -> list[XwalkRow]:
    return [x for x in xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(on_date)]


SOURCE_DB_TYPES = ("TERADATA", "SQLSERVER")


@dataclass(frozen=True)
class QueryConfig:
    """One ComplianceSourceQueryConfig row: an internal source pulled with its own SELECT (SOURCE_LOAD).

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
        return f"ComplianceSourceQueryConfig {self.project_cd}/{self.table_nm}/{self.src_id}"

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
    """The active internal query sources; none when ComplianceSourceQueryConfig does not exist."""
    if not db.table_exists(conn, conn.schema, "ComplianceSourceQueryConfig"):
        return []
    rows = conn.execute("SELECT * FROM ComplianceSourceQueryConfig WHERE Active_Ind = 1 "
                        "ORDER BY Project_Cd, Table_Nm, Src_ID").fetchall()
    return [QueryConfig.from_row(r, settings) for r in rows]


@dataclass(frozen=True)
class ExportConfig:
    """One ComplianceExportConfig row: the export table a core table's batches are copied to (EXPORT_LOAD)."""
    project_cd: str
    table_nm: str
    exp_schema_nm: str
    exp_table_nm: str
    active: bool = True
    exp_btch_col: str = "exp_btch_id"

    @property
    def label(self) -> str:
        return f"ComplianceExportConfig {self.project_cd}/{self.table_nm}"

    @classmethod
    def from_row(cls, r: dict, settings=None) -> "ExportConfig":
        name = settings.resolve_env if settings else (lambda text, identifier=True: text)
        return cls(code(r["project_cd"]), code(r["table_nm"]), name(r["exp_schema_nm"]), r["exp_table_nm"],
                   r["active_ind"] == 1, (_text(r.get("exp_btch_col_nm")) or "Exp_Btch_ID").lower())


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


def compile_template(template: str, case_sensitive: bool = True,
                     parts: Optional[Mapping[str, Optional[str]]] = None) -> re.Pattern:
    return re.compile(_pattern(_parse(template, parts)), 0 if case_sensitive else re.IGNORECASE)


def render(template: str, *, runty: str, rpt_start: date, rpt_end: date, ts: datetime,
           parts: Optional[Mapping[str, Optional[str]]] = None) -> str:
    """Build a file name from a template (used by validator overlap tests and by tests)."""
    values = {"RUNTY": lambda f: runty, "RPTSTART": lambda f: rpt_start.strftime(date_format(f)[0]),
              "RPTEND": lambda f: rpt_end.strftime(date_format(f)[0]),
              "RPTMONTH": lambda f: rpt_start.strftime(date_format(f)[0]),
              "TS": lambda f: ts.strftime(date_format(f)[0] if f else "%Y%m%d%H%M%S"), "ANY": lambda f: "X"}
    return "".join(values[t](f) if k == "ph" else t for k, t, f in _parse(template, parts))


class TemplateMatcher:
    def __init__(self, configs: Iterable[FileConfig], case_sensitive: bool = True):
        self._compiled: list[tuple[FileConfig, re.Pattern, dict]] = []
        self._any_version: list[tuple[FileConfig, re.Pattern]] = []
        self.invalid: list[tuple[FileConfig, str]] = []
        flags = 0 if case_sensitive else re.IGNORECASE
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


def validate_all(conn: Connection, case_sensitive: bool = True, settings=None,
                 run_date: Optional[date] = None) -> list[Issue]:
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
    for rt in rts.values():
        if not rt.run_ty.isalnum():
            add("RUN_TYPE", f"run type {rt.run_ty!r} must be letters and digits only (it is the {{RUNTY}} token)")
        if rt.run_category_cd not in (SCHEDULED, ADHOC) or rt.sla_days < 1:
            add("RUN_TYPE", f"run type {rt.run_ty}: category must be SCHEDULED/ADHOC and SLA_Days >= 1")
        if rt.release_mode not in ("ON_LOAD", "ON_DUE", "MANUAL"):
            add("RUN_TYPE", f"run type {rt.run_ty}: Release_Mode_Cd must be ON_LOAD, ON_DUE or MANUAL")
    projects = {code(r["project_cd"]): r["active_ind"] == 1
                for r in conn.execute("SELECT Project_Cd, Active_Ind FROM ComplianceProject").fetchall()}
    sources = {code(r["src_id"]) for r in conn.execute("SELECT Src_ID FROM ComplianceSourceSystem").fetchall()}
    xw = xwalk_rows(conn)
    from .batches import batch_due, report_dates

    run_date, cache, due = run_date or date.today(), {}, {}
    for x in xw:
        rt = rts.get(x.run_ty)
        if rt is None or not rt.active or rt.run_category_cd != SCHEDULED or not rt.schedule_sql or not x.effective_on(run_date):
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
    cfgs = active_file_configs(conn, settings)
    from .filecheck_options import CHECKS, check_options

    checks = file_checks(conn)
    from .load import header_columns

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
    for x in xw:
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
        if (x.project_cd, x.table_nm, x.src_id) not in cfg_sources | query_sources:
            add("XWALK_NO_FILE_CONFIG", f"{label}: no active ComplianceSourceFileConfig or ComplianceSourceQueryConfig")
    for rows in xw_by_source.values():
        for a, b in combinations(rows, 2):
            if (a.run_ty == b.run_ty
                    and (b.effective_end_dt is None or a.effective_start_dt <= b.effective_end_dt)
                    and (a.effective_end_dt is None or b.effective_start_dt <= a.effective_end_dt)):
                add("XWALK_OVERLAP", f"xwalk {a.project_cd}/{a.table_nm}/{a.src_id}/{a.run_ty}: effective windows "
                                     f"starting {a.effective_start_dt} and {b.effective_start_dt} overlap")

    from .load import delimiter_for, file_layout

    targets = target_columns(conn)
    for t in targets:
        if t.value_cd not in VALUE_CODES:
            add("TARGET_COLUMN", f"{t.label}: unknown Value_Cd {t.value_cd!r}; accepted: {', '.join(VALUE_CODES)}")
        if t.project_cd not in projects:
            add("TARGET_COLUMN", f"{t.label}: project {t.project_cd} is not in ComplianceProject")

    for c in cfgs:
        label = f"file config {c.cfg_id} ({c.project_cd}/{c.table_nm}/{c.src_id})"
        try:
            compile_template(c.src_file_nm_tmplt, case_sensitive, c.name_parts)
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
            file_layout(conn, c, targets, opts)
        except (LookupError, ConfigError) as e:
            add("TARGET_TABLE", f"{label}: {e}")

    from .layout import sql_body
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

    from .layout import BATCH_COLUMN
    from .load import columns
    cores = {(c.project_cd, c.table_nm): c for c in [*cfgs, *queries]}
    for e in active_export_configs(conn, settings):
        label = e.label
        if e.project_cd not in projects:
            add("EXPORT_CONFIG", f"{label}: project {e.project_cd} is not in ComplianceProject")
        src = cores.get((e.project_cd, e.table_nm))
        if src is None:
            add("EXPORT_CONFIG", f"{label}: the table has no active file or query config", "WARNING")
            continue
        exp = {c.name for c in columns(conn, e.exp_schema_nm, e.exp_table_nm) if not c.auto}
        if not exp:
            add("EXPORT_CONFIG", f"{label}: export table {e.exp_schema_nm}.{e.exp_table_nm} not found")
        elif BATCH_COLUMN not in exp:
            add("EXPORT_CONFIG", f"{label}: export table {e.exp_schema_nm}.{e.exp_table_nm} lacks the batch column {BATCH_COLUMN}")
        else:
            missing = sorted(exp - {c.name for c in columns(conn, src.core_schema_nm, src.core_table_nm)} - {e.exp_btch_col})
            if missing:
                add("EXPORT_CONFIG", f"{label}: export columns not in core {src.core_schema_nm}.{src.core_table_nm} "
                                     f"(loaded as NULL): {', '.join(missing)}", "WARNING")

    for r in conn.execute("SELECT * FROM ComplianceRuleBinding WHERE Active_Ind = 1 "
                          "ORDER BY Project_Cd, Table_Nm, Src_ID, Run_Ty").fetchall():
        if code(r["project_cd"]) not in projects:
            add("RULE_BINDING_PROJECT", f"rule binding project {r['project_cd']} is not in ComplianceProject")
        label = (f"rule binding {r['project_cd']}/{r['table_nm']}/{r['src_id']}/{r['run_ty']} "
                 f"{r['gre_rule_group']}:{r['gre_rule_variant']}")
        if not any(x.project_cd == code(r["project_cd"]) and code(r["table_nm"]) in ("*", x.table_nm)
                   and code(r["src_id"]) in ("*", x.src_id) and code(r["run_ty"]) in ("*", x.run_ty) for x in xw):
            add("RULE_BINDING_NO_XWALK", f"{label}: matches no active crosswalk row")

    matcher = TemplateMatcher(cfgs, case_sensitive)
    for c in matcher.configs:
        for rt in sorted({x.run_ty for x in xw_by_source.get((c.project_cd, c.table_nm, c.src_id), ())}) or ["X1"]:
            name = render(c.src_file_nm_tmplt, runty=rt, rpt_start=date(2026, 1, 1), rpt_end=date(2026, 1, 31),
                          ts=datetime(2026, 2, 1, 9, 30, 0), parts=c.name_parts)
            others = [o.cfg_id for o, _ in matcher.candidates(name) if o.cfg_id != c.cfg_id]
            if others:
                add("TEMPLATE_OVERLAP", f"file config {c.cfg_id}: sample name {name} also matches {others}")
    return issues
