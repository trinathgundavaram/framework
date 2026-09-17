"""Configuration validation (design §5.1, P1). Returns a list of issues; empty means valid."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from itertools import combinations
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from typing import Optional

import psycopg

from ..batches import cron
from ..batches.period_strategies import strategy_file_exists
from ..common.status import StatusModel
from ..connections import ConnectionManager
from ..errors import ConfigError
from ..extract.trigger import EXTRACT_ATTRS
from ..settings import Settings, coerce
from ..load.tables import columns, CORE_FRAMEWORK_COLS, STAGING_FRAMEWORK_COLS
from . import repository as repo
from .templates import TemplateError, TemplateMatcher, compile_template, parse_template, render

REQUIRED_EVENTS = (
    "BATCH_CREATED", "BATCH_CLOSED", "FILE_RECEIVED", "FILE_PROMOTED", "FILE_REJECTED_UNPARSEABLE",
    "FILE_REJECTED_NO_BATCH", "EXTRACT_TRIGGERED", "EXTRACT_TRIGGER_FAILED", "CONFIG_VALIDATION_FAILED",
    "REOPEN_CANDIDATE_CREATED", "REOPEN_PROMOTED", "SOURCE_MISSING_AT_CLOSE",
)


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = "ERROR"      # ERROR blocks activation; WARNING is informational


def _valid_tz(tz: str) -> bool:
    try:
        ZoneInfo(tz)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def _check_target_tables(add, dconn: psycopg.Connection, c, label: str) -> None:
    where = f" in connection {c.target_connection_nm}" if c.target_connection_nm else ""
    stg = {col.name for col in columns(dconn, c.stg_schema_nm, c.stg_tblnm)}
    core = {col.name for col in columns(dconn, c.core_schema_nm, c.core_tblnm)}
    if not stg:
        add("TARGET_TABLE", f"{label}: staging table {c.stg_schema_nm}.{c.stg_tblnm} not found{where}")
    elif missing := [x for x in STAGING_FRAMEWORK_COLS if x not in stg]:
        add("TARGET_TABLE", f"{label}: staging table lacks {missing}")
    if not core:
        add("TARGET_TABLE", f"{label}: core table {c.core_schema_nm}.{c.core_tblnm} not found{where}")
    elif missing := [x for x in CORE_FRAMEWORK_COLS if x not in core]:
        add("TARGET_TABLE", f"{label}: core table lacks {missing}")


def validate_all(conn: psycopg.Connection, case_sensitive: bool = True,
                 conns: Optional[ConnectionManager] = None) -> list[Issue]:
    """`conn` is the metadata database; `conns` resolves data connections (None = everything in `conn`)."""
    issues: list[Issue] = []
    def add(code: str, msg: str, severity: str = "ERROR") -> None:
        issues.append(Issue(code, msg, severity))

    # --- framework settings stored in metadata
    known = set(Settings.names())
    for r in conn.execute("SELECT Setting_Nm, Setting_Val FROM ComplianceFrameworkSetting WHERE Active_Ind=1").fetchall():
        name = r["setting_nm"].lower()
        if name not in known:
            add("SETTING_UNKNOWN", f"ComplianceFrameworkSetting {r['setting_nm']} is not a known setting", "WARNING")
        elif name in ("config_file", "metadata_schema", "aws_region"):
            add("SETTING_BOOTSTRAP", f"{r['setting_nm']} cannot be set in metadata (use environment or config file)",
                "WARNING")
        elif r["setting_val"] is not None:
            try:
                coerce(name, r["setting_val"])
            except (ValueError, TypeError) as e:
                add("SETTING_VALUE", f"ComplianceFrameworkSetting {r['setting_nm']}={r['setting_val']!r}: {e}")

    # --- data connections
    data_conns: dict[Optional[str], Optional[psycopg.Connection]] = {None: conn}
    def data_conn(name: Optional[str], label: str) -> Optional[psycopg.Connection]:
        key = None if conns is None or conns.is_metadata(name) else name
        if key is None and name and conns is None:
            add("CONNECTION", f"{label}: Target_Connection_Nm {name} cannot be checked without a connection manager",
                "WARNING")
            return None
        if key not in data_conns:
            try:
                data_conns[key] = conns.get(key)
            except ConfigError as e:
                add("CONNECTION", f"{label}: {e}")
                data_conns[key] = None
        return data_conns[key]
    if conns is not None:
        for r in conn.execute("SELECT Connection_Nm FROM ComplianceDbConnection WHERE Active_Ind=1").fetchall():
            try:
                conns.spec(r["connection_nm"])
            except ConfigError as e:
                add("CONNECTION", str(e))

    # --- status model (Q-01) and event vocabulary
    try:
        StatusModel.load(conn)
    except ConfigError as e:
        add("STATUS_MODEL", str(e))
    have = {r["event_ty"] for r in conn.execute("SELECT Event_Ty FROM ComplianceEventType").fetchall()}
    for ev in REQUIRED_EVENTS:
        if ev not in have:
            add("EVENT_TYPE_MISSING", f"ComplianceEventType lacks {ev} (run init-db)")

    run_types = repo.run_types(conn)
    for rt in run_types.values():
        if not rt.run_ty.isalnum():
            add("RUN_TYPE_CODE", f"Run_Ty {rt.run_ty!r} must be alphanumeric to be usable as {{RUNTY}}")

    xw = repo.xwalk_rows(conn)
    cfgs = repo.active_file_configs(conn)

    # --- crosswalk
    for x in xw:
        label = f"xwalk {x.project_cd}/{x.table_nm}/{x.src_cd}/{x.run_ty}@{x.effective_start_dt}"
        rt = run_types.get(x.run_ty)
        if rt is None:
            add("XWALK_RUN_TYPE", f"{label}: unknown run type")
            continue
        if not _valid_tz(x.business_tz):
            add("XWALK_TZ", f"{label}: invalid Business_Tz {x.business_tz!r}")
        if rt.run_category_cd == "ROUTINE":
            if not x.schedule_cron_expr:
                add("XWALK_CRON", f"{label}: ROUTINE run type requires Schedule_Cron_Expr")
            elif not cron.is_valid(x.schedule_cron_expr):
                add("XWALK_CRON", f"{label}: invalid cron {x.schedule_cron_expr!r}")
            if not x.period_strategy_cd:
                add("XWALK_STRATEGY", f"{label}: ROUTINE run type requires Period_Strategy_Cd")
        elif x.schedule_cron_expr:
            add("XWALK_CRON", f"{label}: ADHOC run type must not have a cron")
        if x.period_strategy_cd:
            st = repo.period_strategy(conn, x.period_strategy_cd)
            if st is None:
                add("XWALK_STRATEGY", f"{label}: unknown strategy {x.period_strategy_cd}")
            else:
                if not strategy_file_exists(st.sql_file_nm):
                    add("STRATEGY_FILE", f"strategy {st.code}: file {st.sql_file_nm} not shipped")
                if st.requires_lookback_days and x.lookback_days is None:
                    add("XWALK_STRATEGY", f"{label}: {st.code} requires Lookback_Days")
                if st.requires_lookback_weeks and x.lookback_weeks is None:
                    add("XWALK_STRATEGY", f"{label}: {st.code} requires Lookback_Weeks")
        if not any(c.project_cd == x.project_cd and c.table_nm == x.table_nm and c.src_cd == x.src_cd for c in cfgs):
            add("XWALK_NO_FILE_CONFIG", f"{label}: no active ComplianceSourceFileConfig")
        if repo.extract_policy(conn, x.project_cd, x.table_nm, x.run_ty) is None:
            add("XWALK_NO_POLICY", f"{label}: no ComplianceExtractPolicy for ({x.project_cd},{x.table_nm},{x.run_ty})")

    # all sources of an extract must share the period definition and timezone
    groups: dict[tuple, set] = {}
    for x in xw:
        groups.setdefault((x.project_cd, x.table_nm, x.run_ty), set()).add(
            (x.period_strategy_cd, x.lookback_days, x.lookback_weeks, x.business_tz))
    for k, defs in groups.items():
        if len(defs) > 1:
            add("XWALK_PERIOD_MISMATCH", f"{k}: sources use different period definitions/timezones {sorted(map(str, defs))}")

    # --- file configs
    for c in cfgs:
        label = f"file config {c.cfg_id} ({c.project_cd}/{c.table_nm}/{c.src_cd})"
        try:
            parse_template(c.src_file_nm_tmplt)
            compile_template(c.src_file_nm_tmplt, c.project_alias, c.table_alias, c.src_alias, case_sensitive)
        except TemplateError as e:
            add("TEMPLATE", f"{label}: {e}")
        if not c.src_file_nm_tmplt.lower().endswith(c.src_file_ty.lower()):
            add("TEMPLATE_EXTENSION", f"{label}: template should end with {c.src_file_ty}")
        if not any(x.project_cd == c.project_cd and x.table_nm == c.table_nm and x.src_cd == c.src_cd for x in xw):
            add("FILE_CONFIG_NO_XWALK", f"{label}: no active crosswalk row")
        if c.engine_cd == "SPARK" and c.has_trailer:
            add("ENGINE", f"{label}: Spark engine does not support trailer records")
        dconn = data_conn(c.target_connection_nm, label)
        if dconn is not None:
            _check_target_tables(add, dconn, c, label)
        for p in ("s3_src_file_path", "src_file_archive_path", "s3_quarantine_path"):
            v = getattr(c, p)
            if not (v.startswith("s3://") or v.startswith("local://")):
                add("PATH", f"{label}: {p} must be an s3:// URI")
        if c.rules_required and not repo.rule_bindings(conn, c.project_cd, c.table_nm, c.src_cd, "FILE_LEVEL"):
            add("RULES_BINDING", f"{label}: Is_Rules_Engine_Required=1 but no FILE_LEVEL rule binding", "WARNING")

    # --- one data connection per (project, table): combine and period rules read one database
    by_table: dict[tuple, set] = {}
    for c in cfgs:
        by_table.setdefault((c.project_cd, c.table_nm), set()).add(c.target_connection_nm or None)
    for k, names in by_table.items():
        if len(names) > 1:
            add("TARGET_CONNECTION_MISMATCH", f"{k}: sources use different Target_Connection_Nm "
                                              f"{sorted(n or 'METADATA' for n in names)}")

    # --- template overlap (§9.3): render a sample for each config/run type and test all other configs
    matcher = TemplateMatcher(cfgs, case_sensitive)
    for c in matcher.configs:
        rts = sorted({x.run_ty for x in xw if (x.project_cd, x.table_nm, x.src_cd) == (c.project_cd, c.table_nm, c.src_cd)}) or ["X1"]
        for rt in rts:
            try:
                name = render(c.src_file_nm_tmplt, project=c.project_alias, table=c.table_alias, src=c.src_alias,
                              runty=rt, rpt_start=date(2026, 1, 1), rpt_end=date(2026, 1, 31),
                              ts=datetime(2026, 2, 1, 9, 30, 0))
            except TemplateError:
                continue
            others = [o.cfg_id for o, _ in matcher.candidates(name) if o.cfg_id != c.cfg_id]
            if others:
                add("TEMPLATE_OVERLAP", f"file config {c.cfg_id}: sample name {name} also matches {others}")
    for a, b in combinations(matcher.configs, 2):
        if (a.project_alias, a.table_alias, a.src_alias) == (b.project_alias, b.table_alias, b.src_alias):
            add("ALIAS_COLLISION", f"file configs {a.cfg_id} and {b.cfg_id} share aliases")

    # --- extract policies / job params
    for pol in conn.execute("SELECT * FROM ComplianceExtractPolicy WHERE Active_Ind=1").fetchall():
        label = f"policy {pol['project_cd']}/{pol['table_nm']}/{pol['run_ty']}"
        for p in repo.job_params(conn, pol["project_cd"], pol["table_nm"], pol["run_ty"]):
            if p.param_src_cd == "EXTRACT_ATTR" and (p.param_val or "").lower() not in EXTRACT_ATTRS:
                add("JOB_PARAM", f"{label}: parameter {p.param_nm} refers to unknown attribute {p.param_val}")
    return issues
