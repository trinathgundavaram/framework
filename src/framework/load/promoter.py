"""Core swap (D-01, design §10.2): disable current rows of the batch, append the load's staged rows.

Must run inside the caller's open transaction; the caller holds the batch advisory lock."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg import sql

from ..config.models import FileConfig
from ..errors import RowCountMismatch
from .tables import core_insert_columns


@dataclass
class PromotionResult:
    disabled_cnt: int
    appended_cnt: int


def staged_row_count(conn: psycopg.Connection, cfg: FileConfig, btch_id: str, load_id: int) -> int:
    table = sql.Identifier(cfg.stg_schema_nm.lower(), cfg.stg_tblnm.lower())
    row = conn.execute(sql.SQL("SELECT count(*) AS n FROM {} WHERE btch_id=%s AND load_id=%s").format(table),
                       (btch_id, load_id)).fetchone()
    return int(row["n"])


def swap(conn: psycopg.Connection, cfg: FileConfig, btch_id: str, load_id: int, expected_rows: int,
         now: datetime) -> PromotionResult:
    cols = core_insert_columns(conn, cfg.stg_schema_nm, cfg.stg_tblnm, cfg.core_schema_nm, cfg.core_tblnm,
                               cfg.load_exclude_cols)
    core = sql.Identifier(cfg.core_schema_nm.lower(), cfg.core_tblnm.lower())
    stg = sql.Identifier(cfg.stg_schema_nm.lower(), cfg.stg_tblnm.lower())
    col_sql = sql.SQL(", ").join(map(sql.Identifier, cols))
    with conn.cursor() as cur:
        cur.execute(sql.SQL("UPDATE {} SET current_ind = 0, end_dtts = %s WHERE btch_id = %s AND current_ind = 1")
                    .format(core), (now, btch_id))
        disabled = cur.rowcount
        cur.execute(sql.SQL(
            "INSERT INTO {core} ({cols}{sep}btch_id, load_id, current_ind, load_dtts) "
            "SELECT {cols}{sep}btch_id, load_id, 1, %s FROM {stg} WHERE btch_id = %s AND load_id = %s").format(
                core=core, cols=col_sql, sep=sql.SQL(", ") if cols else sql.SQL(""), stg=stg),
            (now, btch_id, load_id))
        appended = cur.rowcount
    if appended != expected_rows:
        raise RowCountMismatch(f"appended {appended} rows for load {load_id}, staged {expected_rows}")
    return PromotionResult(disabled, appended)
