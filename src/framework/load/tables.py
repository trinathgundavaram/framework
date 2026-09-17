"""Target-table introspection (design §5.4, §10.2, D-61)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import psycopg

STAGING_FRAMEWORK_COLS = ("btch_id", "load_id", "src_file_nm", "stg_load_dtts")
CORE_FRAMEWORK_COLS = ("btch_id", "load_id", "current_ind", "load_dtts", "end_dtts")


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    is_identity: bool
    is_generated: bool
    default: Optional[str]


def columns(conn: psycopg.Connection, schema: str, table: str) -> list[Column]:
    rows = conn.execute(
        """SELECT column_name, data_type, is_identity, is_generated, column_default
             FROM information_schema.columns
            WHERE table_schema = lower(%s) AND table_name = lower(%s)
            ORDER BY ordinal_position""", (schema, table)).fetchall()
    return [Column(r["column_name"], r["data_type"], r["is_identity"] == "YES",
                   r["is_generated"] == "ALWAYS", r["column_default"]) for r in rows]


def staging_business_columns(conn: psycopg.Connection, schema: str, table: str) -> list[str]:
    cols = columns(conn, schema, table)
    if not cols:
        raise LookupError(f"staging table {schema}.{table} not found")
    missing = [c for c in STAGING_FRAMEWORK_COLS if c not in {x.name for x in cols}]
    if missing:
        raise LookupError(f"staging table {schema}.{table} lacks framework columns {missing}")
    return [c.name for c in cols if c.name not in STAGING_FRAMEWORK_COLS]


def core_insert_columns(conn: psycopg.Connection, stg_schema: str, stg_table: str, core_schema: str,
                        core_table: str, exclude: tuple[str, ...]) -> list[str]:
    """Staging business columns that exist in core, minus identity/generated/serial columns and the
    configured exclude list (D-61). Order follows the staging table."""
    core = columns(conn, core_schema, core_table)
    if not core:
        raise LookupError(f"core table {core_schema}.{core_table} not found")
    missing = [c for c in CORE_FRAMEWORK_COLS if c not in {x.name for x in core}]
    if missing:
        raise LookupError(f"core table {core_schema}.{core_table} lacks framework columns {missing}")
    auto_skip = {c.name for c in core
                 if c.is_identity or c.is_generated or "nextval(" in (c.default or "")}
    core_names = {c.name for c in core}
    excl = {e.lower() for e in exclude}
    return [c for c in staging_business_columns(conn, stg_schema, stg_table)
            if c in core_names and c not in auto_skip and c not in excl and c not in CORE_FRAMEWORK_COLS]

