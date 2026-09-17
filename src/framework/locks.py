"""Session-level advisory locks (design §12.2).

Lock order is always EXT -> BTCH. Locks are released automatically if the session dies.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg

from .errors import LockTimeout


def extract_key(extract_id: int) -> str:
    return f"EXT:{extract_id}"


def batch_key(btch_id: str) -> str:
    return f"BTCH:{btch_id}"


def seq_key(project: str, table: str, src: str, run_ty: str) -> str:
    return f"SEQ:{project}|{table}|{src}|{run_ty}"


def try_lock(conn: psycopg.Connection, key: str) -> bool:
    row = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS ok", (key,)).fetchone()
    return bool(row["ok"])


def unlock(conn: psycopg.Connection, key: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


def acquire(conn: psycopg.Connection, key: str, timeout_seconds: float, poll: float = 0.2) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if try_lock(conn, key):
            return
        if time.monotonic() >= deadline:
            raise LockTimeout(f"could not acquire lock {key} within {timeout_seconds}s")
        time.sleep(poll)


@contextmanager
def held(conn: psycopg.Connection, key: str, timeout_seconds: Optional[float]) -> Iterator[None]:
    """timeout_seconds=None -> single non-blocking attempt (raises LockTimeout if busy)."""
    if timeout_seconds is None:
        if not try_lock(conn, key):
            raise LockTimeout(f"lock {key} is busy")
    else:
        acquire(conn, key, timeout_seconds)
    try:
        yield
    finally:
        unlock(conn, key)


def xact_lock(conn: psycopg.Connection, key: str) -> None:
    """Transaction-scoped lock (released at commit/rollback)."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
