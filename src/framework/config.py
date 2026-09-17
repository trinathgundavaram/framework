"""Configuration: typed rows, read access, filename templates (§9) and the configuration validator (P1).

Configuration tables: ComplianceRunType, ComplianceSourceSystem, ComplianceDataSetSourceXwalk,
ComplianceSourceFileConfig, ComplianceRuleBinding. Everything else is job-level (settings.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from itertools import combinations
from typing import Iterable, Optional, Sequence

import psycopg

from .load import CORE_FRAMEWORK_COLS, STAGING_FRAMEWORK_COLS, columns


# ============================================================================ typed rows
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
    src_cd: str
    run_ty: str
    effective_start_dt: date
    effective_end_dt: Optional[date]
    cmplnc_vrsn: str
    active: bool

    @classmethod
    def from_row(cls, r: dict) -> "XwalkRow":
        return cls(r["project_cd"], r["table_nm"], r["src_cd"], r["run_ty"], r["effective_start_dt"],
                   r["effective_end_dt"], r["cmplnc_vrsn"], r["active_ind"] == 1)

    def effective_on(self, d: date) -> bool:
        return self.active and self.effective_start_dt <= d and (self.effective_end_dt is None or d <= self.effective_end_dt)


@dataclass(frozen=True)
class FileConfig:
    cfg_id: int
    project_cd: str
    table_nm: str
    src_cd: str
    src_file_nm_tmplt: str
    project_alias: str
    table_alias: str
    src_alias: str
    src_file_ty: str
    delmtr_cd: Optional[str]
    line_term_cd: Optional[str]
    has_header: bool
    has_trailer: bool
    allow_zero_records: bool
    s3_src_file_path: str
    src_file_archive_path: str
    s3_quarantine_path: str
    stg_schema_nm: str
    stg_tblnm: str
    core_schema_nm: str
    core_tblnm: str
    sucs_email_notfn_id: Optional[str] = None
    failr_email_notfn_id: Optional[str] = None
    notify_channel_cd: Optional[str] = None
    email_subjct_txt: Optional[str] = None
    active: bool = True

    @classmethod
    def from_row(cls, r: dict) -> "FileConfig":
        return cls(r["cfg_id"], r["project_cd"], r["table_nm"], r["src_cd"], r["src_file_nm_tmplt"],
                   r["project_alias"], r["table_alias"], r["src_alias"], r["src_file_ty"].lower(),
                   r["delmtr_cd"], r["line_term_cd"], r["src_file_has_hdr_ind"] == 1,
                   r["src_file_has_trlr_ind"] == 1, r["allow_zero_rcd_ind"] == 1, r["s3_src_file_path"],
                   r["src_file_archive_path"], r["s3_quarantine_path"], r["stg_schema_nm"], r["stg_tblnm"],
                   r["core_schema_nm"], r["core_tblnm"], r["sucs_email_notfn_id"], r["failr_email_notfn_id"],
                   r["notify_channel_cd"], r["email_subjct_txt"], r["active_ind"] == 1)


@dataclass(frozen=True)
class RuleBinding:
    project_cd: str
    table_nm: str
    src_cd: str
    rule_scope_cd: str
    gre_rule_group: str
    gre_rule_variant: str


# ============================================================================ read access
def run_type(conn: psycopg.Connection, run_ty: str) -> Optional[RunType]:
    r = conn.execute("SELECT * FROM ComplianceRunType WHERE Run_Ty = %s", (run_ty,)).fetchone()
    return RunType.from_row(r) if r else None


def run_types(conn: psycopg.Connection) -> dict[str, RunType]:
    return {r["run_ty"]: RunType.from_row(r) for r in conn.execute("SELECT * FROM ComplianceRunType").fetchall()}


def xwalk_rows(conn: psycopg.Connection, *, project_cd: Optional[str] = None, table_nm: Optional[str] = None,
               src_cd: Optional[str] = None, run_ty: Optional[str] = None) -> list[XwalkRow]:
    """Active crosswalk rows, optionally filtered."""
    where, params = ["Active_Ind = 1"], []
    for col, val in (("Project_Cd", project_cd), ("Table_Nm", table_nm), ("Src_Cd", src_cd), ("Run_Ty", run_ty)):
        if val is not None:
            where.append(f"{col} = %s")
            params.append(val)
    rows = conn.execute(f"SELECT * FROM ComplianceDataSetSourceXwalk WHERE {' AND '.join(where)} "
                        "ORDER BY Project_Cd, Table_Nm, Src_Cd, Run_Ty, Effective_Start_Dt", params).fetchall()
    return [XwalkRow.from_row(r) for r in rows]


def effective_xwalk(conn, project_cd, table_nm, src_cd, run_ty, on_date: date) -> Optional[XwalkRow]:
    return next((x for x in xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, src_cd=src_cd, run_ty=run_ty)
                 if x.effective_on(on_date)), None)


def effective_sources(conn, project_cd, table_nm, run_ty, on_date: date) -> list[XwalkRow]:
    return [x for x in xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(on_date)]


def active_file_configs(conn: psycopg.Connection) -> list[FileConfig]:
    rows = conn.execute("SELECT * FROM ComplianceSourceFileConfig WHERE Active_Ind = 1 ORDER BY Cfg_ID").fetchall()
    return [FileConfig.from_row(r) for r in rows]


def file_config(conn, project_cd: str, table_nm: str, src_cd: Optional[str] = None) -> Optional[FileConfig]:
    """Active file config of a source; with src_cd=None any source of the table (all share the core table)."""
    r = conn.execute(
        """SELECT * FROM ComplianceSourceFileConfig WHERE Project_Cd=%s AND Table_Nm=%s
              AND (%s::text IS NULL OR Src_Cd=%s) AND Active_Ind=1 ORDER BY Cfg_ID LIMIT 1""",
        (project_cd, table_nm, src_cd, src_cd)).fetchone()
    return FileConfig.from_row(r) if r else None


def file_config_by_id(conn: psycopg.Connection, cfg_id: int) -> Optional[FileConfig]:
    r = conn.execute("SELECT * FROM ComplianceSourceFileConfig WHERE Cfg_ID=%s", (cfg_id,)).fetchone()
    return FileConfig.from_row(r) if r else None


def rule_bindings(conn, project_cd: str, table_nm: str, src_cd: str, scope: str) -> list[RuleBinding]:
    rows = conn.execute(
        """SELECT * FROM ComplianceRuleBinding
            WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s AND Rule_Scope_Cd=%s AND Active_Ind=1
            ORDER BY Gre_Rule_Group, Gre_Rule_Variant""", (project_cd, table_nm, src_cd, scope)).fetchall()
    return [RuleBinding(r["project_cd"], r["table_nm"], r["src_cd"], r["rule_scope_cd"],
                        r["gre_rule_group"], r["gre_rule_variant"]) for r in rows]


# ============================================================================ filename templates (§9)
# A template is literal text plus exactly one of each placeholder:
#   {PROJECT} {TABLE} {SRC}  -> the config row's aliases, literally (D-31)
#   {RUNTY}                  -> [A-Za-z0-9]+, must equal a Run_Ty code exactly (D-31)
#   {RPTSTART} {RPTEND}      -> YYYYMMDD (D-32)
#   {TS}                     -> YYYYMMDDHHMMSS, uniqueness only (D-36, D-37)
# Adjacent placeholders must be separated by literal text.

PLACEHOLDERS = ("PROJECT", "TABLE", "SRC", "RUNTY", "RPTSTART", "RPTEND", "TS")
_TOKEN = re.compile(r"\{([^{}]*)\}")


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
    if "{" in "".join(t for k, t in parts if k == "lit") or "}" in "".join(t for k, t in parts if k == "lit"):
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


def compile_template(template: str, project_alias: str, table_alias: str, src_alias: str,
                     case_sensitive: bool = True) -> re.Pattern:
    aliases = {"PROJECT": project_alias, "TABLE": table_alias, "SRC": src_alias}
    out = []
    for kind, text in parse_template(template):
        if kind == "lit":
            out.append(re.escape(text))
        elif text in aliases:
            out.append(f"(?P<{text.lower()}>{re.escape(aliases[text])})")
        elif text == "RUNTY":
            out.append(r"(?P<runty>[A-Za-z0-9]+)")
        elif text in ("RPTSTART", "RPTEND"):
            out.append(rf"(?P<{text.lower()}>\d{{8}})")
        elif text == "TS":
            out.append(r"(?P<ts>\d{14})")
    return re.compile("".join(out), 0 if case_sensitive else re.IGNORECASE)


def render(template: str, *, project: str, table: str, src: str, runty: str,
           rpt_start: date, rpt_end: date, ts: datetime) -> str:
    """Build a file name from a template (used by validator overlap tests and by tests)."""
    values = {"PROJECT": project, "TABLE": table, "SRC": src, "RUNTY": runty,
              "RPTSTART": f"{rpt_start:%Y%m%d}", "RPTEND": f"{rpt_end:%Y%m%d}", "TS": f"{ts:%Y%m%d%H%M%S}"}
    return "".join(values[t] if k == "ph" else t for k, t in parse_template(template))


class TemplateMatcher:
    def __init__(self, configs: Iterable[FileConfig], case_sensitive: bool = True):
        self.case_sensitive = case_sensitive
        self._compiled: list[tuple[FileConfig, re.Pattern]] = []
        self.invalid: list[tuple[FileConfig, str]] = []
        for c in configs:
            try:
                self._compiled.append((c, compile_template(c.src_file_nm_tmplt, c.project_alias,
                                                           c.table_alias, c.src_alias, case_sensitive)))
            except TemplateError as e:
                self.invalid.append((c, str(e)))

    @property
    def configs(self) -> Sequence[FileConfig]:
        return [c for c, _ in self._compiled]

    def candidates(self, name: str) -> list[tuple[FileConfig, re.Match]]:
        out = []
        for c, rx in self._compiled:
            m = rx.fullmatch(name)
            if m:
                out.append((c, m))
        return out

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


# ============================================================================ validator (P1)
REQUIRED_EVENTS = (
    "BATCH_CREATED", "BATCH_CLOSED", "FILE_RECEIVED", "FILE_PROMOTED", "FILE_REJECTED_UNPARSEABLE",
    "FILE_REJECTED_NO_BATCH", "FILE_REJECTED_BATCH_CLOSED", "EXTRACT_CLOSED", "EXTRACT_CLOSE_BLOCKED",
    "CONFIG_VALIDATION_FAILED", "LATE_ARRIVAL_PROMOTED", "CORRECTION_PROMOTED", "SOURCE_MISSING_AT_CLOSE",
    "CARRY_FORWARD_APPLIED", "CARRY_FORWARD_REMOVED", "OVERRIDE_APPROVED", "OVERRIDE_EXPIRED",
)


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = "ERROR"      # ERROR blocks activation; WARNING is informational


def validate_all(conn: psycopg.Connection, case_sensitive: bool = True) -> list[Issue]:
    """Validate the configuration tables and the target tables they point to. Empty list = valid."""
    issues: list[Issue] = []

    def add(code: str, msg: str, severity: str = "ERROR") -> None:
        issues.append(Issue(code, msg, severity))

    have = {r["event_ty"] for r in conn.execute("SELECT Event_Ty FROM ComplianceEventType").fetchall()}
    for ev in REQUIRED_EVENTS:
        if ev not in have:
            add("EVENT_TYPE_MISSING", f"ComplianceEventType lacks {ev} (run init-db)")

    rts = run_types(conn)
    xw = xwalk_rows(conn)
    cfgs = active_file_configs(conn)
    for x in xw:
        label = f"xwalk {x.project_cd}/{x.table_nm}/{x.src_cd}/{x.run_ty}@{x.effective_start_dt}"
        if not rts[x.run_ty].active:
            add("XWALK_RUN_TYPE", f"{label}: run type {x.run_ty} is inactive", "WARNING")
        if not any((c.project_cd, c.table_nm, c.src_cd) == (x.project_cd, x.table_nm, x.src_cd) for c in cfgs):
            add("XWALK_NO_FILE_CONFIG", f"{label}: no active ComplianceSourceFileConfig")

    for c in cfgs:
        label = f"file config {c.cfg_id} ({c.project_cd}/{c.table_nm}/{c.src_cd})"
        try:
            compile_template(c.src_file_nm_tmplt, c.project_alias, c.table_alias, c.src_alias, case_sensitive)
        except TemplateError as e:
            add("TEMPLATE", f"{label}: {e}")
        if not c.src_file_nm_tmplt.lower().endswith(c.src_file_ty.lower()):
            add("TEMPLATE_EXTENSION", f"{label}: template should end with {c.src_file_ty}")
        if not any((x.project_cd, x.table_nm, x.src_cd) == (c.project_cd, c.table_nm, c.src_cd) for x in xw):
            add("FILE_CONFIG_NO_XWALK", f"{label}: no active crosswalk row")
        for p in ("s3_src_file_path", "src_file_archive_path", "s3_quarantine_path"):
            if not getattr(c, p).startswith(("s3://", "local://")):
                add("PATH", f"{label}: {p} must be an s3:// URI")
        for kind, schema, table, required in (("staging", c.stg_schema_nm, c.stg_tblnm, STAGING_FRAMEWORK_COLS),
                                              ("core", c.core_schema_nm, c.core_tblnm, CORE_FRAMEWORK_COLS)):
            found = {col.name for col in columns(conn, schema, table)}
            if not found:
                add("TARGET_TABLE", f"{label}: {kind} table {schema}.{table} not found")
            elif missing := [x for x in required if x not in found]:
                add("TARGET_TABLE", f"{label}: {kind} table {schema}.{table} lacks {missing}")

    # template overlap (§9.3): render a sample for each config/run type and test all other configs
    matcher = TemplateMatcher(cfgs, case_sensitive)
    for c in matcher.configs:
        for rt in sorted({x.run_ty for x in xw if (x.project_cd, x.table_nm, x.src_cd) ==
                          (c.project_cd, c.table_nm, c.src_cd)}) or ["X1"]:
            name = render(c.src_file_nm_tmplt, project=c.project_alias, table=c.table_alias, src=c.src_alias,
                          runty=rt, rpt_start=date(2026, 1, 1), rpt_end=date(2026, 1, 31),
                          ts=datetime(2026, 2, 1, 9, 30, 0))
            others = [o.cfg_id for o, _ in matcher.candidates(name) if o.cfg_id != c.cfg_id]
            if others:
                add("TEMPLATE_OVERLAP", f"file config {c.cfg_id}: sample name {name} also matches {others}")
    for a, b in combinations(matcher.configs, 2):
        if (a.project_alias, a.table_alias, a.src_alias) == (b.project_alias, b.table_alias, b.src_alias):
            add("ALIAS_COLLISION", f"file configs {a.cfg_id} and {b.cfg_id} share aliases")
    return issues
