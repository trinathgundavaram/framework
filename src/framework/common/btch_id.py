"""Btch_ID construction (design §4): {Req_Dt_Key:YYYYMMDD}_{Project}_{Table}_{Src}_{Run_Ty}_{Vrsn}_{Seq}."""
from __future__ import annotations

from datetime import date

import psycopg

MAX_LEN = 250


def build(req_dt: date, project_cd: str, table_nm: str, src_cd: str, run_ty: str, cmplnc_vrsn: str, seq: int) -> str:
    if seq < 1:
        raise ValueError("seq must be >= 1")
    value = f"{req_dt:%Y%m%d}_{project_cd}_{table_nm}_{src_cd}_{run_ty}_{cmplnc_vrsn}_{seq}"
    if len(value) > MAX_LEN:
        raise ValueError(f"Btch_ID exceeds {MAX_LEN} characters: {value}")
    return value


def next_seq(conn: psycopg.Connection, project_cd: str, table_nm: str, src_cd: str, run_ty: str, req_dt: date) -> int:
    """Caller must hold the SEQ lock for (project, table, source, run type)."""
    row = conn.execute(
        """SELECT count(*) AS n FROM ComplianceRequestControl
            WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s AND Run_Ty=%s AND Req_Dt_Key=%s""",
        (project_cd, table_nm, src_cd, run_ty, req_dt)).fetchone()
    return int(row["n"]) + 1


def earliest_close_date(req_dt: date, sla_days: int) -> date:
    """D-38: SLA 1 = creation day, SLA 2 = next day, ... (calendar days)."""
    from datetime import timedelta

    if sla_days < 1:
        raise ValueError("SLA_Days must be >= 1")
    return req_dt + timedelta(days=sla_days - 1)
