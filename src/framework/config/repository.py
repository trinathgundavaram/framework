"""Read access to configuration tables. All functions take an open connection."""
from __future__ import annotations

from datetime import date
from typing import Optional

import psycopg

from .models import (ExtractPolicy, FileConfig, JobParam, PeriodStrategy, RuleBinding, RunType,
                     XwalkRow)


def run_type(conn: psycopg.Connection, run_ty: str) -> Optional[RunType]:
    r = conn.execute("SELECT * FROM ComplianceRunType WHERE Run_Ty = %s", (run_ty,)).fetchone()
    return RunType.from_row(r) if r else None


def run_types(conn: psycopg.Connection) -> dict[str, RunType]:
    return {r["run_ty"]: RunType.from_row(r) for r in conn.execute("SELECT * FROM ComplianceRunType").fetchall()}


def xwalk_rows(conn: psycopg.Connection, *, project_cd: Optional[str] = None, table_nm: Optional[str] = None,
               src_cd: Optional[str] = None, run_ty: Optional[str] = None,
               active_only: bool = True) -> list[XwalkRow]:
    sql = "SELECT * FROM ComplianceDataSetSourceXwalk WHERE TRUE"
    params: list = []
    for col, val in (("Project_Cd", project_cd), ("Table_Nm", table_nm), ("Src_Cd", src_cd), ("Run_Ty", run_ty)):
        if val is not None:
            sql += f" AND {col} = %s"
            params.append(val)
    if active_only:
        sql += " AND Active_Ind = 1"
    sql += " ORDER BY Project_Cd, Table_Nm, Src_Cd, Run_Ty, Effective_Start_Dt"
    return [XwalkRow.from_row(r) for r in conn.execute(sql, params).fetchall()]


def effective_xwalk(conn: psycopg.Connection, project_cd: str, table_nm: str, src_cd: str, run_ty: str,
                    on_date: date) -> Optional[XwalkRow]:
    for x in xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, src_cd=src_cd, run_ty=run_ty):
        if x.effective_on(on_date):
            return x
    return None


def effective_sources(conn: psycopg.Connection, project_cd: str, table_nm: str, run_ty: str,
                      on_date: date) -> list[XwalkRow]:
    return [x for x in xwalk_rows(conn, project_cd=project_cd, table_nm=table_nm, run_ty=run_ty)
            if x.effective_on(on_date)]


def routine_xwalk_rows(conn: psycopg.Connection) -> list[XwalkRow]:
    rows = conn.execute(
        """SELECT x.* FROM ComplianceDataSetSourceXwalk x
             JOIN ComplianceRunType r ON r.Run_Ty = x.Run_Ty
            WHERE x.Active_Ind = 1 AND r.Active_Ind = 1 AND r.Run_Category_Cd = 'ROUTINE'
            ORDER BY x.Project_Cd, x.Table_Nm, x.Src_Cd, x.Run_Ty, x.Effective_Start_Dt""").fetchall()
    return [XwalkRow.from_row(r) for r in rows]


def active_file_configs(conn: psycopg.Connection) -> list[FileConfig]:
    rows = conn.execute("SELECT * FROM ComplianceSourceFileConfig WHERE Active_Ind = 1 ORDER BY Cfg_ID").fetchall()
    return [FileConfig.from_row(r) for r in rows]


def file_config(conn: psycopg.Connection, project_cd: str, table_nm: str, src_cd: str) -> Optional[FileConfig]:
    r = conn.execute(
        """SELECT * FROM ComplianceSourceFileConfig
            WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s AND Active_Ind=1""",
        (project_cd, table_nm, src_cd)).fetchone()
    return FileConfig.from_row(r) if r else None


def file_config_by_id(conn: psycopg.Connection, cfg_id: int) -> Optional[FileConfig]:
    r = conn.execute("SELECT * FROM ComplianceSourceFileConfig WHERE Cfg_ID=%s", (cfg_id,)).fetchone()
    return FileConfig.from_row(r) if r else None


def extract_policy(conn: psycopg.Connection, project_cd: str, table_nm: str, run_ty: str) -> Optional[ExtractPolicy]:
    r = conn.execute(
        "SELECT * FROM ComplianceExtractPolicy WHERE Project_Cd=%s AND Table_Nm=%s AND Run_Ty=%s",
        (project_cd, table_nm, run_ty)).fetchone()
    return ExtractPolicy.from_row(r) if r else None


def job_params(conn: psycopg.Connection, project_cd: str, table_nm: str, run_ty: str) -> list[JobParam]:
    rows = conn.execute(
        """SELECT Param_Nm, Param_Src_Cd, Param_Val, Param_Seq FROM ComplianceExtractJobParam
            WHERE Project_Cd=%s AND Table_Nm=%s AND Run_Ty=%s ORDER BY Param_Seq, Param_Nm""",
        (project_cd, table_nm, run_ty)).fetchall()
    return [JobParam(r["param_nm"], r["param_src_cd"], r["param_val"], r["param_seq"]) for r in rows]


def rule_bindings(conn: psycopg.Connection, project_cd: str, table_nm: str, src_cd: str,
                  scope: str) -> list[RuleBinding]:
    rows = conn.execute(
        """SELECT * FROM ComplianceRuleBinding
            WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s AND Rule_Scope_Cd=%s AND Active_Ind=1
            ORDER BY Gre_Rule_Group, Gre_Rule_Variant""",
        (project_cd, table_nm, src_cd, scope)).fetchall()
    return [RuleBinding(r["project_cd"], r["table_nm"], r["src_cd"], r["rule_scope_cd"],
                        r["gre_rule_group"], r["gre_rule_variant"]) for r in rows]


def period_strategy(conn: psycopg.Connection, code: str) -> Optional[PeriodStrategy]:
    r = conn.execute("SELECT * FROM CompliancePeriodStrategy WHERE Period_Strategy_Cd=%s", (code,)).fetchone()
    if not r:
        return None
    return PeriodStrategy(r["period_strategy_cd"], r["sql_file_nm"],
                          r["requires_lookback_days_ind"] == 1, r["requires_lookback_weeks_ind"] == 1)


def business_tz_for_extract(conn: psycopg.Connection, project_cd: str, table_nm: str, run_ty: str) -> str:
    r = conn.execute(
        """SELECT Business_Tz FROM ComplianceDataSetSourceXwalk
            WHERE Project_Cd=%s AND Table_Nm=%s AND Run_Ty=%s
            ORDER BY Active_Ind DESC, Effective_Start_Dt DESC LIMIT 1""",
        (project_cd, table_nm, run_ty)).fetchone()
    return r["business_tz"] if r else "UTC"


def table_connection_names(conn: psycopg.Connection, project_cd: str, table_nm: str) -> set[Optional[str]]:
    rows = conn.execute(
        """SELECT DISTINCT Target_Connection_Nm FROM ComplianceSourceFileConfig
            WHERE Project_Cd=%s AND Table_Nm=%s AND Active_Ind=1""", (project_cd, table_nm)).fetchall()
    return {r["target_connection_nm"] for r in rows}


def table_file_config(conn: psycopg.Connection, project_cd: str, table_nm: str) -> Optional[FileConfig]:
    """Any active file config of the table (all share the target connection and core table - validator)."""
    r = conn.execute(
        """SELECT * FROM ComplianceSourceFileConfig WHERE Project_Cd=%s AND Table_Nm=%s AND Active_Ind=1
            ORDER BY Cfg_ID LIMIT 1""", (project_cd, table_nm)).fetchone()
    return FileConfig.from_row(r) if r else None
