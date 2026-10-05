"""Teradata database layer: connection wrapper, transactions, table-based locks."""
from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence

from .common import ConfigError, LockTimeout

log = logging.getLogger(__name__)

_PARAM = re.compile(r"%\((\w+)\)s|%s")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
_ERROR_CODE = re.compile(r"\[Error (\d+)\]")
DUPLICATE_KEY_CODES = {2801, 2803}
DATA_ERROR_CODES = {2616, 2617, 2620, 2621, 2665, 2666, 2679, 3520, 3996, 6706, 6760}
INSERT_CHUNK_ROWS = 2000
INSERT_CHUNK_BYTES = 500_000


def error_code(exc: BaseException) -> Optional[int]:
    """The Teradata error number in a driver exception, if any."""
    m = _ERROR_CODE.search(str(exc))
    return int(m.group(1)) if m else None


def is_duplicate_key(exc: BaseException) -> bool:
    return error_code(exc) in DUPLICATE_KEY_CODES or "duplicate unique prime key" in str(exc).lower()


def is_data_error(exc: BaseException) -> bool:
    return error_code(exc) in DATA_ERROR_CODES


def ident(name: str) -> str:
    """A validated, double-quoted identifier."""
    if not _IDENT.match(name or ""):
        raise ConfigError(f"{name!r} is not a valid identifier")
    return f'"{name}"'


def qualified(database: str, table: str) -> str:
    return f"{ident(database)}.{ident(table)}"


def _to_qmark(query: str, params) -> tuple[str, list]:
    """psycopg-style %s / %(name)s placeholders -> teradatasql '?' with an ordered value list."""
    if params is None:
        return query, []
    values: list = []
    if isinstance(params, Mapping):
        def named(m):
            if m.group(1) is None:
                raise ValueError("mixed %s and %(name)s placeholders")
            values.append(params[m.group(1)])
            return "?"
        return _PARAM.sub(named, query), [_bind(v) for v in values]
    params = list(params)
    text = _PARAM.sub("?", query)
    if text.count("?") != len(params):
        raise ValueError(f"query has {text.count('?')} placeholders but {len(params)} parameters")
    return text, [_bind(v) for v in params]


def _bind(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and type(value) is not str:
        return str(value)
    return value


def _clean(value: Any) -> Any:
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class _Probe(Exception):
    """Raised to roll a trial insert back."""


class Result:
    def __init__(self, rows: Optional[list[dict]], rowcount: int):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self) -> Optional[dict]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return list(self._rows or [])


class Connection:
    """Wraps a teradatasql connection with the small interface the framework uses."""

    def __init__(self, raw, schema: str, lock_ttl_seconds: int = 4 * 3600):
        self._raw = raw
        self.schema = schema
        self.lock_ttl_seconds = lock_ttl_seconds
        self.owner_id = uuid.uuid4().hex
        self._depth = 0
        self.closed = False

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    def execute(self, query: str, params=None) -> Result:
        return self.execute_qmark(*_to_qmark(query, params))

    def execute_qmark(self, text: str, values: Sequence = ()) -> Result:
        """Run a statement that already uses '?' markers; its text is sent as written."""
        values = [_bind(v) for v in values]
        if text.lstrip()[:6].upper() == "SELECT":
            text = "LOCKING ROW FOR ACCESS " + text.lstrip()
        cur = self._raw.cursor()
        try:
            cur.execute(text, values) if values else cur.execute(text)
            if not cur.description:
                return Result(None, cur.rowcount)
            names = [c[0].lower() for c in cur.description]
            rows = [dict(zip(names, map(_clean, r))) for r in cur.fetchall()]
            return Result(rows, len(rows))
        finally:
            cur.close()

    def executemany(self, query: str, rows: Iterable[Sequence]) -> int:
        """Batched parameterised DML, chunked by row count and size; returns the number of rows sent."""
        text = _PARAM.sub("?", query)
        sent, chunk, size = 0, [], 0
        cur = self._raw.cursor()
        try:
            for row in rows:
                row = [_bind(v) for v in row]
                chunk.append(row)
                size += sum(len(v) if isinstance(v, str) else 8 for v in row)
                if len(chunk) >= INSERT_CHUNK_ROWS or size >= INSERT_CHUNK_BYTES:
                    cur.executemany(text, chunk)
                    sent, chunk, size = sent + len(chunk), [], 0
            if chunk:
                cur.executemany(text, chunk)
                sent += len(chunk)
        except Exception as e:
            if chunk and not hasattr(e, "failed_rows"):
                e.failed_rows, e.failed_offset = chunk, sent
            raise
        finally:
            cur.close()
        return sent

    def first_rejected(self, query: str, rows: list) -> Optional[int]:
        """Index of the first row of a failed batch that the database refuses; every probe is rolled back."""
        if self.in_transaction or not rows:
            return None
        text = _PARAM.sub("?", query)

        def refused(part: list) -> bool:
            cur = self._raw.cursor()
            try:
                with self.transaction():
                    cur.executemany(text, part)
                    raise _Probe()
            except _Probe:
                return False
            except Exception as e:
                if not is_data_error(e):
                    raise
                return True
            finally:
                cur.close()

        if not refused(rows):
            return None
        lo, hi = 0, len(rows)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if refused(rows[lo:mid]):
                hi = mid
            else:
                lo = mid
        return lo

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """One atomic unit; nested blocks join the outer transaction (Teradata has no savepoints)."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._autocommit(False)
        self._depth = 1
        try:
            yield
        except BaseException:
            self._depth = 0
            try:
                self._raw.rollback()
            except Exception:  # noqa: BLE001
                log.debug("rollback after a failed statement", exc_info=True)
            finally:
                self._autocommit(True)
            raise
        else:
            self._depth = 0
            try:
                self._raw.commit()
            finally:
                self._autocommit(True)

    def _autocommit(self, on: bool) -> None:
        if hasattr(type(self._raw), "autocommit"):
            self._raw.autocommit = on
            return
        cur = self._raw.cursor()
        try:
            cur.execute("{fn teradata_nativesql}{fn teradata_autocommit_" + ("on" if on else "off") + "}")
        finally:
            cur.close()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._raw.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def fetch_all(conn: Connection, query: str, *params) -> list[dict]:
    return conn.execute(query, params).fetchall()


def in_list(values: Sequence) -> tuple[str, list]:
    """('%s,%s,...', values) for an IN (...) clause; values must not be empty."""
    values = list(values)
    if not values:
        raise ValueError("IN list is empty")
    return ",".join(["%s"] * len(values)), values


def table_exists(conn: Connection, database: str, table: str) -> bool:
    return conn.execute("SELECT 1 AS ok FROM DBC.TablesV WHERE UPPER(DatabaseName) = UPPER(%s) "
                        "AND UPPER(TableName) = UPPER(%s)", (database, table)).fetchone() is not None


def schema_exists(conn: Connection, schema: str) -> bool:
    return table_exists(conn, schema, "ComplianceRequestControl")


def batch_key(btch_id: str) -> str:
    return f"BTCH:{btch_id}"


def file_key(share: str, path: str) -> str:
    """Lock key of one file."""
    return "OBJ:" + file_hash(share, path, "")


def file_hash(share: str, path: str, version: str) -> str:
    return hashlib.sha256(f"{share}/{path}@{version}".encode("utf-8")).hexdigest()


def seq_key(project: str, table: str, src: str, run_ty: str) -> str:
    return f"SEQ:{project}|{table}|{src}|{run_ty}"


def row_key(kind: str, row_id) -> str:
    """Lock key that claims one row (kind: EVT, INTAKE)."""
    return f"{kind}:{row_id}"


def try_lock(conn: Connection, key: str) -> bool:
    """Take the lock row for `key`; False when another process holds it. Expired locks are replaced."""
    if conn.in_transaction:
        raise RuntimeError("locks are taken outside a transaction")
    now = datetime.now(timezone.utc)
    conn.execute("DELETE FROM ComplianceLock WHERE Lock_Key = %s AND Expires_Dtts < %s", (key, now))
    try:
        conn.execute("INSERT INTO ComplianceLock (Lock_Key, Owner_ID, Acquired_Dtts, Expires_Dtts) "
                     "VALUES (%s,%s,%s,%s)",
                     (key, conn.owner_id, now, now + timedelta(seconds=conn.lock_ttl_seconds)))
    except Exception as e:  # noqa: BLE001
        if is_duplicate_key(e):
            return False
        raise
    return True


def unlock(conn: Connection, key: str) -> None:
    if conn.in_transaction:
        raise RuntimeError("locks are released outside a transaction")
    conn.execute("DELETE FROM ComplianceLock WHERE Lock_Key = %s AND Owner_ID = %s", (key, conn.owner_id))


def held_locks(conn: Connection) -> list[dict]:
    """Every lock row, oldest first (a crashed run leaves its locks until they expire)."""
    return conn.execute("SELECT Lock_Key, Owner_ID, Acquired_Dtts, Expires_Dtts FROM ComplianceLock "
                        "ORDER BY Acquired_Dtts").fetchall()


def release_lock(conn: Connection, key: str) -> int:
    """Operator action: remove a lock left by a run that no longer exists; returns rows removed."""
    return conn.execute("DELETE FROM ComplianceLock WHERE Lock_Key = %s", (key,)).rowcount


@contextmanager
def held(conn: Connection, key: str, timeout_seconds: Optional[float], poll: float = 1.0) -> Iterator[None]:
    """Hold a lock; timeout_seconds=None -> single non-blocking attempt (raises LockTimeout if busy)."""
    deadline = time.monotonic() + (timeout_seconds or 0)
    while not try_lock(conn, key):
        if timeout_seconds is None or time.monotonic() >= deadline:
            raise LockTimeout(f"could not acquire lock {key}" + (f" within {timeout_seconds}s" if timeout_seconds else ""))
        time.sleep(poll)
    try:
        yield
    finally:
        unlock(conn, key)


@contextmanager
def held_all(conn: Connection, keys: Iterable[str], timeout_seconds: Optional[float]) -> Iterator[None]:
    """Hold several locks, always taken in sorted order."""
    keys = sorted(set(keys))
    taken: list[str] = []
    try:
        for key in keys:
            deadline = time.monotonic() + (timeout_seconds or 0)
            while not try_lock(conn, key):
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"could not acquire lock {key} within {timeout_seconds}s")
                time.sleep(1.0)
            taken.append(key)
        yield
    finally:
        for key in reversed(taken):
            unlock(conn, key)


def as_date(value) -> Optional[date]:
    """DATE columns come back as date (or datetime on some driver versions)."""
    return value.date() if isinstance(value, datetime) else value
