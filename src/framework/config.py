"""Configuration rows, filename templates and the validator."""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from itertools import combinations
from typing import Iterable, Optional, Sequence

import psycopg

from .load import CORE_FRAMEWORK_COLS, STAGING_FRAMEWORK_COLS, columns


@dataclass(frozen=True)
class RunType:
    run_ty: str
    run_category_cd: str
    sla_days: int
    carry_fwd: bool
    active: bool

    @classmethod
    def from_row(cls, r: dict) -> "RunType":
        return cls(r["run_ty"], r["run_category_cd"], r["sla_days"], r["carry_fwd_ind"] == 1, r["active_ind"] == 1)


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

    @classmethod
    def from_row(cls, r: dict) -> "XwalkRow":
        return cls(r["project_cd"], r["table_nm"], r["src_id"], r["run_ty"], r["effective_start_dt_key"],
                   r["effective_end_dt_key"], r["cmplnc_vrsn"], r["active_ind"] == 1)

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
    s3_src_file_path: str
    src_file_archive_path: str
    stg_schema_nm: str
    stg_table_nm: str
    core_schema_nm: str
    sucs_email_notfn_id: Optional[str] = None
    failr_email_notfn_id: Optional[str] = None
    email_subjct_txt: Optional[str] = None
    active: bool = True

    @property
    def src_file_ty(self) -> str:
        """The template's file extension."""
        return os.path.splitext(self.src_file_nm_tmplt)[1].lower()

    @classmethod
    def from_row(cls, r: dict) -> "FileConfig":
        return cls(r["cfg_id"], r["project_cd"], r["table_nm"], r["src_id"], r["src_file_nm_tmplt"], r["delmtr_cd"],
                   r["src_file_has_hdr_ind"] == 1, r["src_file_has_trlr_ind"] == 1, r["allow_zero_rcd_ind"] == 1,
                   r["s3_src_file_path"], r["src_file_archive_path"], r["stg_schema_nm"], r["stg_table_nm"],
                   r["core_schema_nm"], r["sucs_email_notfn_id"], r["failr_email_notfn_id"], r["email_subjct_txt"],
                   r["active_ind"] == 1)


@dataclass(frozen=True)
class RuleBinding:
    project_cd: str
    table_nm: str
    src_id: str
    run_ty: str
    gre_rule_group: str
    gre_rule_variant: str


def run_type(conn: psycopg.Connection, run_ty: str) -> Optional[RunType]:
    r = conn.execute("SELECT * FROM ComplianceRunType WHERE Run_Ty = %s", (run_ty,)).fetchone()
    return RunType.from_row(r) if r else None


def run_types(conn: psycopg.Connection) -> dict[str, RunType]:
    return {r["run_ty"]: RunType.from_row(r) for r in conn.execute("SELECT * FROM ComplianceRunType").fetchall()}


def xwalk_rows(conn: psycopg.Connection, *, project_cd: Optional[str] = None, table_nm: Optional[str] = None,
               src_id: Optional[str] = None, run_ty: Optional[str] = None) -> list[XwalkRow]:
    """Active crosswalk rows, optionally filtered."""
    where, params = ["Active_Ind = 1"], []
    for col, val in (("Project_Cd", project_cd), ("Table_Nm", table_nm), ("Src_ID", src_id), ("Run_Ty", run_ty)):
        if val is not None:
            where.append(f"{col} = %s")
            params.append(val)
    rows = conn.execute(f"SELECT * FROM ComplianceDataSetSourceXwalk WHERE {' AND '.join(where)} "
                        "ORDER BY Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key", params).fetchall()
    return [XwalkRow.from_row(r) for r in rows]


def effective_xwalk(conn, project_cd, table_nm, src_id, run_ty, on_date: date) -> Optional[XwalkRow]:
    return next((x for x in xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, src_id=src_id, run_ty=run_ty)
                 if x.effective_on(on_date)), None)


def effective_sources(conn, project_cd, table_nm, run_ty, on_date: date) -> list[XwalkRow]:
    return [x for x in xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(on_date)]


def active_file_configs(conn: psycopg.Connection) -> list[FileConfig]:
    rows = conn.execute("SELECT * FROM ComplianceSourceFileConfig WHERE Active_Ind = 1 ORDER BY Cfg_ID").fetchall()
    return [FileConfig.from_row(r) for r in rows]


def file_config(conn, project_cd: str, table_nm: str, src_id: Optional[str] = None) -> Optional[FileConfig]:
    """Active file config of a source; with src_id=None any source of the table (all share the core table)."""
    r = conn.execute(
        """SELECT * FROM ComplianceSourceFileConfig WHERE Project_Cd=%s AND Table_Nm=%s
              AND (%s::text IS NULL OR Src_ID=%s) AND Active_Ind=1 ORDER BY Cfg_ID LIMIT 1""",
        (project_cd, table_nm, src_id, src_id)).fetchone()
    return FileConfig.from_row(r) if r else None


def file_config_by_id(conn: psycopg.Connection, cfg_id: int) -> Optional[FileConfig]:
    r = conn.execute("SELECT * FROM ComplianceSourceFileConfig WHERE Cfg_ID=%s", (cfg_id,)).fetchone()
    return FileConfig.from_row(r) if r else None


def rule_bindings(conn, project_cd: str, table_nm: str, src_id: str, run_ty: str) -> list[RuleBinding]:
    """Every active binding that applies to a file (additive)."""
    rows = conn.execute(
        """SELECT * FROM ComplianceRuleBinding
            WHERE Project_Cd = %(p)s AND Table_Nm IN (%(t)s, '*') AND Src_ID IN (%(s)s, '*')
              AND Run_Ty IN (%(r)s, '*') AND Active_Ind = 1
            ORDER BY Gre_Rule_Group, Gre_Rule_Variant,
                     (Table_Nm = '*')::int + (Src_ID = '*')::int + (Run_Ty = '*')::int""",
        {"p": project_cd, "t": table_nm, "s": src_id, "r": run_ty}).fetchall()
    out: dict[tuple[str, str], RuleBinding] = {}
    for r in rows:
        out.setdefault((r["gre_rule_group"], r["gre_rule_variant"]), RuleBinding(
            r["project_cd"], r["table_nm"], r["src_id"], r["run_ty"], r["gre_rule_group"], r["gre_rule_variant"]))
    return list(out.values())


PLACEHOLDERS = ("RUNTY", "RPTSTART", "RPTEND", "TS")
_TOKEN = re.compile(r"\{([^{}]*)\}")
_PATTERNS = {"RUNTY": r"(?P<runty>[A-Za-z0-9]+)", "RPTSTART": r"(?P<rptstart>\d{8})",
             "RPTEND": r"(?P<rptend>\d{8})", "TS": r"(?P<ts>\d{14})"}


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class MatchResult:
    cfg: FileConfig
    run_ty: str
    rpt_start: date
    rpt_end: date
    file_ts: datetime


class MatchError(Exception):
    def __init__(self, event_ty: str, message: str, cfg: Optional[FileConfig] = None):
        super().__init__(message)
        self.event_ty = event_ty
        self.cfg = cfg


def parse_template(template: str) -> list[tuple[str, str]]:
    """Split into [('lit', text) | ('ph', NAME)] and validate the grammar."""
    parts: list[tuple[str, str]] = []
    pos = 0
    for m in _TOKEN.finditer(template):
        if m.start() > pos:
            parts.append(("lit", template[pos:m.start()]))
        parts.append(("ph", m.group(1)))
        pos = m.end()
    if pos < len(template):
        parts.append(("lit", template[pos:]))
    literal = "".join(t for k, t in parts if k == "lit")
    if "{" in literal or "}" in literal:
        raise TemplateError(f"unbalanced braces in template {template!r}")
    names = [t for k, t in parts if k == "ph"]
    unknown = sorted(set(names) - set(PLACEHOLDERS))
    if unknown:
        raise TemplateError(f"unknown placeholder(s) {unknown} in {template!r}")
    for p in PLACEHOLDERS:
        n = names.count(p)
        if n != 1:
            raise TemplateError(f"placeholder {{{p}}} must appear exactly once in {template!r} (found {n})")
    for a, b in zip(parts, parts[1:]):
        if a[0] == "ph" and b[0] == "ph":
            raise TemplateError(f"placeholders {{{a[1]}}}{{{b[1]}}} must be separated by literal text in {template!r}")
    if "/" in template:
        raise TemplateError(f"template must describe a file name only (no '/'): {template!r}")
    return parts


def compile_template(template: str, case_sensitive: bool = True) -> re.Pattern:
    out = "".join(re.escape(text) if kind == "lit" else _PATTERNS[text] for kind, text in parse_template(template))
    return re.compile(out, 0 if case_sensitive else re.IGNORECASE)


def render(template: str, *, runty: str, rpt_start: date, rpt_end: date, ts: datetime) -> str:
    """Build a file name from a template (used by validator overlap tests and by tests)."""
    values = {"RUNTY": runty, "RPTSTART": f"{rpt_start:%Y%m%d}", "RPTEND": f"{rpt_end:%Y%m%d}",
              "TS": f"{ts:%Y%m%d%H%M%S}"}
    return "".join(values[t] if k == "ph" else t for k, t in parse_template(template))


class TemplateMatcher:
    def __init__(self, configs: Iterable[FileConfig], case_sensitive: bool = True):
        self._compiled: list[tuple[FileConfig, re.Pattern]] = []
        self.invalid: list[tuple[FileConfig, str]] = []
        for c in configs:
            try:
                self._compiled.append((c, compile_template(c.src_file_nm_tmplt, case_sensitive)))
            except TemplateError as e:
                self.invalid.append((c, str(e)))

    @property
    def configs(self) -> Sequence[FileConfig]:
        return [c for c, _ in self._compiled]

    def candidates(self, name: str) -> list[tuple[FileConfig, re.Match]]:
        return [(c, m) for c, rx in self._compiled if (m := rx.fullmatch(name))]

    def match(self, name: str) -> MatchResult:
        found = self.candidates(name)
        if not found:
            raise MatchError("FILE_REJECTED_UNPARSEABLE", f"{name} matches no active filename template")
        if len(found) > 1:
            ids = [c.cfg_id for c, _ in found]
            raise MatchError("FILE_REJECTED_AMBIGUOUS_TEMPLATE", f"{name} matches several templates (Cfg_ID {ids})")
        cfg, m = found[0]
        try:
            start = datetime.strptime(m.group("rptstart"), "%Y%m%d").date()
            end = datetime.strptime(m.group("rptend"), "%Y%m%d").date()
        except ValueError:
            raise MatchError("FILE_REJECTED_INVALID_TOKEN", f"{name}: report dates are not valid YYYYMMDD", cfg)
        if end < start:
            raise MatchError("FILE_REJECTED_INVALID_TOKEN", f"{name}: report end date is before start date", cfg)
        try:
            ts = datetime.strptime(m.group("ts"), "%Y%m%d%H%M%S")
        except ValueError:
            raise MatchError("FILE_REJECTED_INVALID_TOKEN", f"{name}: {{TS}} is not a valid YYYYMMDDHHMMSS", cfg)
        return MatchResult(cfg, m.group("runty"), start, end, ts)


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = "ERROR"


def validate_all(conn: psycopg.Connection, case_sensitive: bool = True) -> list[Issue]:
    """Validate the configuration tables and the target tables they point to."""
    issues: list[Issue] = []

    def add(code: str, msg: str, severity: str = "ERROR") -> None:
        issues.append(Issue(code, msg, severity))

    rts = run_types(conn)
    for rt in rts.values():
        if not rt.run_ty.isalnum():
            add("RUN_TYPE", f"run type {rt.run_ty!r} must be letters and digits only (it is the {{RUNTY}} token)")
        if rt.run_category_cd not in ("ROUTINE", "ADHOC") or rt.sla_days < 1:
            add("RUN_TYPE", f"run type {rt.run_ty}: category must be ROUTINE/ADHOC and SLA_Days >= 1")
    projects = {r["project_cd"]: r["active_ind"] == 1
                for r in conn.execute("SELECT Project_Cd, Active_Ind FROM ComplianceProject").fetchall()}
    xw = xwalk_rows(conn)
    cfgs = active_file_configs(conn)
    cfg_sources = {(c.project_cd, c.table_nm, c.src_id) for c in cfgs}
    xw_by_source: dict[tuple, list[XwalkRow]] = defaultdict(list)
    for x in xw:
        xw_by_source[(x.project_cd, x.table_nm, x.src_id)].append(x)
        label = f"xwalk {x.project_cd}/{x.table_nm}/{x.src_id}/{x.run_ty}@{x.effective_start_dt}"
        if not rts[x.run_ty].active:
            add("XWALK_RUN_TYPE", f"{label}: run type {x.run_ty} is inactive", "WARNING")
        if not projects[x.project_cd]:
            add("XWALK_PROJECT", f"{label}: project {x.project_cd} is inactive", "WARNING")
        if (x.project_cd, x.table_nm, x.src_id) not in cfg_sources:
            add("XWALK_NO_FILE_CONFIG", f"{label}: no active ComplianceSourceFileConfig")
    for rows in xw_by_source.values():
        for a, b in combinations(rows, 2):
            if (a.run_ty == b.run_ty
                    and (b.effective_end_dt is None or a.effective_start_dt <= b.effective_end_dt)
                    and (a.effective_end_dt is None or b.effective_start_dt <= a.effective_end_dt)):
                add("XWALK_OVERLAP", f"xwalk {a.project_cd}/{a.table_nm}/{a.src_id}/{a.run_ty}: effective windows "
                                     f"starting {a.effective_start_dt} and {b.effective_start_dt} overlap")

    for c in cfgs:
        label = f"file config {c.cfg_id} ({c.project_cd}/{c.table_nm}/{c.src_id})"
        try:
            compile_template(c.src_file_nm_tmplt, case_sensitive)
        except TemplateError as e:
            add("TEMPLATE", f"{label}: {e}")
        if not c.src_file_ty:
            add("TEMPLATE_EXTENSION", f"{label}: template must end with a file extension such as .txt")
        if (c.project_cd, c.table_nm, c.src_id) not in xw_by_source:
            add("FILE_CONFIG_NO_XWALK", f"{label}: no active crosswalk row")
        for p in ("s3_src_file_path", "src_file_archive_path"):
            if not getattr(c, p).startswith(("s3://", "local://")):
                add("PATH", f"{label}: {p} must be an s3:// URI")
        for kind, schema, table, required in (("staging", c.stg_schema_nm, c.stg_table_nm, STAGING_FRAMEWORK_COLS),
                                              ("core", c.core_schema_nm, c.table_nm, CORE_FRAMEWORK_COLS)):
            found = {col.name for col in columns(conn, schema, table)}
            if not found:
                add("TARGET_TABLE", f"{label}: {kind} table {schema}.{table} not found")
            elif missing := [x for x in required if x not in found]:
                add("TARGET_TABLE", f"{label}: {kind} table {schema}.{table} lacks {missing}")

    for r in conn.execute("SELECT * FROM ComplianceRuleBinding WHERE Active_Ind = 1 "
                          "ORDER BY Project_Cd, Table_Nm, Src_ID, Run_Ty").fetchall():
        label = (f"rule binding {r['project_cd']}/{r['table_nm']}/{r['src_id']}/{r['run_ty']} "
                 f"{r['gre_rule_group']}:{r['gre_rule_variant']}")
        if not any(x.project_cd == r["project_cd"] and r["table_nm"] in ("*", x.table_nm)
                   and r["src_id"] in ("*", x.src_id) and r["run_ty"] in ("*", x.run_ty) for x in xw):
            add("RULE_BINDING_NO_XWALK", f"{label}: matches no active crosswalk row")

    matcher = TemplateMatcher(cfgs, case_sensitive)
    for c in matcher.configs:
        for rt in sorted({x.run_ty for x in xw_by_source.get((c.project_cd, c.table_nm, c.src_id), ())}) or ["X1"]:
            name = render(c.src_file_nm_tmplt, runty=rt, rpt_start=date(2026, 1, 1), rpt_end=date(2026, 1, 31),
                          ts=datetime(2026, 2, 1, 9, 30, 0))
            others = [o.cfg_id for o, _ in matcher.candidates(name) if o.cfg_id != c.cfg_id]
            if others:
                add("TEMPLATE_OVERLAP", f"file config {c.cfg_id}: sample name {name} also matches {others}")
    return issues
