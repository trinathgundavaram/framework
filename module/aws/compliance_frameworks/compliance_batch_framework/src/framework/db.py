"""Database helpers."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg
from psycopg.rows import dict_row

from .common import LockTimeout


def connect(dsn: str, schema: Optional[str] = None) -> psycopg.Connection:
    """Open a direct autocommit connection from a DSN (tests / ad-hoc use)."""
    kw = {"options": f"-c search_path={schema},public"} if schema else {}
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                           application_name="cms-compliance-framework", **kw)


def fetch_all(conn: psycopg.Connection, query: str, *params) -> list[dict]:
    return conn.execute(query, params).fetchall()


def schema_exists(conn: psycopg.Connection, schema: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"{schema}.compliancerequestcontrol",)).fetchone()
    return bool(row["ok"])


def batch_key(btch_id: str) -> str:
    return f"BTCH:{btch_id}"


def object_key(bucket: str, key: str, version_id: Optional[str]) -> str:
    """Lock key of one S3 object version."""
    return f"OBJ:{bucket}/{key}@{version_id or ''}"


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
