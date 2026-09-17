"""Batch (CRC) and extract-row creation and lookup (design §5.2, §7 P2-P4)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import psycopg

from ..audit.event_logger import EventLogger
from ..clock import Clock
from ..common import btch_id as bid
from ..common.status import S_AWAITING, StatusModel
from ..config.models import RunType, XwalkRow
from .. import locks


@dataclass
class CreateResult:
    req_id: Optional[int]
    created: bool
    skipped_reason: Optional[str] = None
    extract_id: Optional[int] = None


def find_batch(conn: psycopg.Connection, project_cd: str, table_nm: str, src_cd: str, run_ty: str,
               rpt_start: date, rpt_end: date, for_update: bool = False) -> Optional[dict]:
    sql = """SELECT * FROM ComplianceRequestControl
              WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s AND Run_Ty=%s
                AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s"""
    if for_update:
        sql += " FOR UPDATE"
    return conn.execute(sql, (project_cd, table_nm, src_cd, run_ty, rpt_start, rpt_end)).fetchone()


def get_batch(conn: psycopg.Connection, req_id: int, for_update: bool = False) -> dict:
    sql = "SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s" + (" FOR UPDATE" if for_update else "")
    return conn.execute(sql, (req_id,)).fetchone()


def find_extract(conn: psycopg.Connection, project_cd: str, table_nm: str, run_ty: str,
                 rpt_start: date, rpt_end: date) -> Optional[dict]:
    return conn.execute(
        """SELECT * FROM ComplianceExtractControl WHERE Project_Cd=%s AND Table_Nm=%s AND Run_Ty=%s
              AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s""",
        (project_cd, table_nm, run_ty, rpt_start, rpt_end)).fetchone()


def ensure_extract(conn: psycopg.Connection, project_cd: str, table_nm: str, run_ty: str,
                   rpt_start: date, rpt_end: date, required_cnt: int, earliest_trigger_dt: date) -> dict:
    conn.execute(
        """INSERT INTO ComplianceExtractControl
             (Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Required_Src_Cnt, Earliest_Trigger_Dt)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key) DO NOTHING""",
        (project_cd, table_nm, run_ty, rpt_start, rpt_end, required_cnt, earliest_trigger_dt))
    return find_extract(conn, project_cd, table_nm, run_ty, rpt_start, rpt_end)


def create_batch(conn: psycopg.Connection, clock: Clock, status: StatusModel, logger: EventLogger, *,
                 xwalk: XwalkRow, run_type: RunType, rpt_start: date, rpt_end: date, req_dt: date,
                 created_by: str, required_cnt: int, intake_id: Optional[str] = None,
                 allow_extract_triggered: bool = False) -> CreateResult:
    """Idempotent: returns created=False when the batch for the period already exists (D-30)."""
    with conn.transaction():
        locks.xact_lock(conn, locks.seq_key(xwalk.project_cd, xwalk.table_nm, xwalk.src_cd, xwalk.run_ty))
        existing = find_batch(conn, xwalk.project_cd, xwalk.table_nm, xwalk.src_cd, xwalk.run_ty, rpt_start, rpt_end)
        if existing:
            return CreateResult(existing["req_id"], False, "EXISTS", existing["extract_id"])
        earliest = bid.earliest_close_date(req_dt, run_type.sla_days)
        ext = ensure_extract(conn, xwalk.project_cd, xwalk.table_nm, xwalk.run_ty, rpt_start, rpt_end,
                             required_cnt, earliest)
        if ext["trigger_stat"] == "TRIGGERED" and not allow_extract_triggered:
            logger.audit("BATCH_CREATE_SKIPPED_EXTRACT_TRIGGERED", project_cd=xwalk.project_cd,
                         table_nm=xwalk.table_nm, src_cd=xwalk.src_cd, run_ty=xwalk.run_ty,
                         extract_id=ext["extract_id"], intake_id=intake_id,
                         description=f"period {rpt_start}..{rpt_end} already triggered; batch not created")
            return CreateResult(None, False, "EXTRACT_TRIGGERED", ext["extract_id"])
        seq = bid.next_seq(conn, xwalk.project_cd, xwalk.table_nm, xwalk.src_cd, xwalk.run_ty, req_dt)
        btch = bid.build(req_dt, xwalk.project_cd, xwalk.table_nm, xwalk.src_cd, xwalk.run_ty, xwalk.cmplnc_vrsn, seq)
        now = clock.now()
        row = conn.execute(
            """INSERT INTO ComplianceRequestControl
                 (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key,
                  Earliest_Close_Dt, Btch_ID, Cmplnc_Vrsn, Extract_ID, Intake_ID, Req_Stat, Created_By,
                  Created_Dtts, Updated_Dtts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key) DO NOTHING
               RETURNING Req_ID""",
            (xwalk.project_cd, xwalk.table_nm, xwalk.src_cd, xwalk.run_ty, rpt_start, rpt_end, req_dt,
             earliest, btch, xwalk.cmplnc_vrsn, ext["extract_id"], intake_id, status.code(S_AWAITING),
             created_by, now, now)).fetchone()
        if row is None:  # concurrent creator won
            existing = find_batch(conn, xwalk.project_cd, xwalk.table_nm, xwalk.src_cd, xwalk.run_ty, rpt_start, rpt_end)
            return CreateResult(existing["req_id"], False, "EXISTS", existing["extract_id"])
        conn.execute(
            """UPDATE ComplianceExtractControl
                  SET Earliest_Trigger_Dt = GREATEST(Earliest_Trigger_Dt, %s), Updated_Dtts = %s
                WHERE Extract_ID = %s""", (earliest, now, ext["extract_id"]))
        logger.batch_event("BATCH_CREATED", req_id=row["req_id"], btch_id=btch, intake_id=intake_id,
                           detail=f"created_by={created_by} period={rpt_start}..{rpt_end} earliest_close={earliest}")
        return CreateResult(row["req_id"], True, None, ext["extract_id"])


def batches_of_extract(conn: psycopg.Connection, extract_id: int) -> list[dict]:
    return conn.execute("SELECT * FROM ComplianceRequestControl WHERE Extract_ID=%s ORDER BY Src_Cd",
                        (extract_id,)).fetchall()
