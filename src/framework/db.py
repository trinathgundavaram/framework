"""Database helpers: schema install, query helpers and advisory locks.

Connections are autocommit; every unit of work uses an explicit `with conn.transaction():` block.
Batch locks (BTCH) serialise work on one batch (design §12.2). Session locks are released if the session dies.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from importlib import resources
from typing import Iterator, Optional

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .common import LockTimeout
from .settings import Settings


def connect(dsn: str, schema: Optional[str] = None) -> psycopg.Connection:
    """Open a direct autocommit connection from a DSN (tests / ad-hoc use)."""
    kw = {"options": f"-c search_path={schema},public"} if schema else {}
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                           application_name="cms-compliance-framework", **kw)


def fetch_all(conn: psycopg.Connection, query: str, *params) -> list[dict]:
    return conn.execute(query, params).fetchall()


def sql_text(name: str) -> str:
    return resources.files("framework").joinpath("sql", name).read_text(encoding="utf-8")


def schema_exists(conn: psycopg.Connection, schema: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"{schema}.compliancerequestcontrol",)).fetchone()
    return bool(row["ok"])


def init_db(conn: psycopg.Connection, schema: str = "cms_compliance") -> list[str]:
    """Create the metadata schema (if missing) and apply schema.sql once."""
    Settings(metadata_schema=schema).validate()
    with conn.transaction():
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        if schema_exists(conn, schema):
            return ["schema.sql (skipped: schema already initialised)"]
        conn.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema)))
        conn.execute(sql_text("schema.sql"))
    return ["schema.sql"]


# ============================================================================ advisory locks
def batch_key(btch_id: str) -> str:
    return f"BTCH:{btch_id}"


def seq_key(project: str, table: str, src: str, run_ty: str) -> str:
    return f"SEQ:{project}|{table}|{src}|{run_ty}"


def try_lock(conn: psycopg.Connection, key: str) -> bool:
    return bool(conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS ok", (key,)).fetchone()["ok"])


def unlock(conn: psycopg.Connection, key: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


@contextmanager
def held(conn: psycopg.Connection, key: str, timeout_seconds: Optional[float], poll: float = 0.2) -> Iterator[None]:
    """Session lock; timeout_seconds=None -> single non-blocking attempt (raises LockTimeout if busy)."""
    deadline = time.monotonic() + (timeout_seconds or 0)
    while not try_lock(conn, key):
        if timeout_seconds is None or time.monotonic() >= deadline:
            raise LockTimeout(f"could not acquire lock {key}" + (f" within {timeout_seconds}s" if timeout_seconds else ""))
        time.sleep(poll)
    try:
        yield
    finally:
        unlock(conn, key)


def xact_lock(conn: psycopg.Connection, key: str) -> None:
    """Transaction-scoped lock (released at commit/rollback)."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
