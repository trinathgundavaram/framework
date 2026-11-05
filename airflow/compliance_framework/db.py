"""Teradata connection, locks and helpers; connections to the NAS and to source servers; runtime settings."""
from __future__ import annotations

import functools
import hashlib
import logging
import os
import re
import time
import typing
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from compliance_framework.common import BUSINESS_TZ, ConfigError, LockTimeout, resolve_env


# db: Teradata database layer: connection wrapper, transactions, table-based locks.

log = logging.getLogger(__name__)
BUSINESS_ZONE = ZoneInfo(BUSINESS_TZ)

_PARAM = re.compile(r"%\((\w+)\)s|%s")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
_ERROR_CODE = re.compile(r"\[Error (\d+)\]")
_DATABASE_ERROR = re.compile(r"\[Teradata Database\] \[Error (\d+)\].*")
DUPLICATE_KEY_CODES = {2801, 2803}
DATA_ERROR_CODES = {2616, 2617, 2620, 2621, 2665, 2666, 2679, 3520, 3604, 3996, 5317, 6706, 6760, 7454}
INSERT_CHUNK_ROWS = 2000
INSERT_CHUNK_BYTES = 500_000


def error_code(exc: BaseException) -> Optional[int]:
    """The Teradata error number in a driver exception, if any."""
    m = _DATABASE_ERROR.search(str(exc)) or _ERROR_CODE.search(str(exc))
    return int(m.group(1)) if m else None


def error_line(exc: BaseException) -> str:
    """The database's own message in a driver exception (a failed batch reports the driver's error first)."""
    m = _DATABASE_ERROR.search(str(exc))
    return m.group(0) if m else (str(exc).splitlines() or [""])[0]


_NAMED = re.compile(r"\{(\w+)\}")


def log_sql(what: str, body: str, params: Mapping, db_ty: str = "TERADATA") -> None:
    """Log a configured statement as it runs: the SQL as sent (? = a parameter), each {name} parameter with its value,
    and the statement with the values written in (to read or run by hand; the database gets them as parameters)."""
    body = " ".join(body.split())
    typed = db_ty.upper() != "SQLSERVER"
    value = lambda m: _literal(params.get(m.group(1).lower()), typed)
    names = dict.fromkeys(n.lower() for n in _NAMED.findall(body))
    shown = ", ".join(f"{n} = {_literal(params.get(n), typed)}" for n in names) or "none"
    log.info("%s\n    SQL:         %s\n    parameters:  %s\n    with values: %s", what, _NAMED.sub("?", body), shown,
             _NAMED.sub(value, body))


def _literal(v: Any, typed: bool = True) -> str:
    """A value as SQL text: DATE '...' / TIMESTAMP '...' on Teradata, plain '...' on SQL Server."""
    if v is None:
        return "NULL"
    if isinstance(v, (bool, int, float, Decimal)):
        return str(int(v) if isinstance(v, bool) else v)
    if isinstance(v, datetime):
        text = v.replace(tzinfo=None).isoformat(sep=" ")
        return f"TIMESTAMP '{text}'" if typed else f"'{text}'"
    if isinstance(v, date):
        return f"DATE '{v.isoformat()}'" if typed else f"'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def error_text(exc: BaseException) -> str:
    """One line for logs, step outcomes and stored error text: the exception type and the database's message."""
    return f"{type(exc).__name__}: {error_line(exc)}"[:1000]


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
        local = value.astimezone(BUSINESS_ZONE)
        return local.replace(tzinfo=timezone(local.utcoffset()))
    if isinstance(value, str) and type(value) is not str:
        return str(value)
    return value


def bind_row(row: Sequence) -> list:
    """A row from another database, every value made sendable (a source row's values differ from row to row)."""
    return [_bind(v) for v in row]


def _clean(value: Any) -> Any:
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=BUSINESS_ZONE)
    return value


class _Probe(Exception):
    """Raised to roll a trial insert back."""


class FastLoadRejected(Exception):
    """FastLoad refused rows; the message is the database's first error."""


def _plain_types(row: Sequence) -> list[int]:
    """Positions of the values that need converting before they are sent (not text, a whole number or NULL)."""
    return [i for i, v in enumerate(row) if not (v is None or type(v) is str or type(v) is int)]


def _batches(rows: Iterable[Sequence], batch_rows: int, batch_bytes: int) -> Iterator[list]:
    """Rows in batches of at most `batch_rows` rows or about `batch_bytes` bytes.

    Only the positions whose first value is not text, a whole number or NULL are converted (a staging row's
    derived values are the same on every row); everything else is sent as it is.
    """
    convert = None
    chunk, size = [], 0
    for row in rows:
        if convert is None:
            convert = _plain_types(row)
        if convert:
            row = list(row)
            for i in convert:
                row[i] = _bind(row[i])
        chunk.append(row)
        size += sum(len(v) if type(v) is str else 8 for v in row)
        if len(chunk) >= batch_rows or size >= batch_bytes:
            yield chunk
            chunk, size = [], 0
    if chunk:
        yield chunk


class Result:
    def __init__(self, rows: Optional[list[dict]], rowcount: int, columns: Optional[list[str]] = None):
        self._rows = rows
        self.rowcount = rowcount
        self.columns = columns or []

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
            return Result(rows, len(rows), [c[0] for c in cur.description])
        finally:
            cur.close()

    def executemany(self, query: str, rows: Iterable[Sequence], batch_rows: int = INSERT_CHUNK_ROWS,
                    batch_bytes: int = INSERT_CHUNK_BYTES) -> int:
        """Batched parameterised DML; returns the number of rows sent."""
        text = _PARAM.sub("?", query)
        sent, chunk = 0, []
        cur = self._raw.cursor()
        try:
            for batch in _batches(rows, batch_rows, batch_bytes):
                chunk = batch
                cur.executemany(text, chunk)
                sent, chunk = sent + len(chunk), []
        except Exception as e:
            if chunk and not hasattr(e, "failed_rows"):
                e.failed_rows, e.failed_offset = chunk, sent
            raise
        finally:
            cur.close()
        return sent

    def fastload(self, query: str, rows: Iterable[Sequence], batch_rows: int = INSERT_CHUNK_ROWS,
                 batch_bytes: int = INSERT_CHUNK_BYTES) -> int:
        """Insert with Teradata FastLoad into an empty table, as one unit; returns the number of rows sent.

        FastLoad is required: when it is not possible (not permitted, no utility slot, ...) the driver raises an error
        instead of inserting row by row. FastLoadRejected = the database refused rows (they are not loaded; nothing is
        committed).
        """
        if self.in_transaction:
            raise RuntimeError("FastLoad runs outside a transaction")
        text = "{fn teradata_require_fastload}" + _PARAM.sub("?", query)
        sent = 0
        self._autocommit(False)
        cur = self._raw.cursor()
        try:
            for chunk in _batches(rows, batch_rows, batch_bytes):
                cur.executemany(text, chunk)
                sent += len(chunk)
            cur.execute("{fn teradata_nativesql}{fn teradata_get_errors}" + text)
            errors = [str(r[0]) for r in cur.fetchall() if r and r[0]]
            if errors:
                raise FastLoadRejected(errors[0])
            self._raw.commit()
        except BaseException:
            try:
                self._raw.rollback()
            except Exception:  # noqa: BLE001
                log.debug("rollback after a failed FastLoad", exc_info=True)
            raise
        finally:
            cur.close()
            self._autocommit(True)
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


def seq_key(project: str, table: str, run_ty: str) -> str:
    """Lock of the batch IDs of a table and run type (shared by every source)."""
    return f"SEQ:{project}|{table}|{run_ty}"


def req_key(b: dict) -> str:
    """Lock of one batch row (a source of a table-level batch)."""
    return f"REQ:{b['req_id']}"


def row_key(kind: str, row_id) -> str:
    """Lock key that claims one row (kind: EVT, INTAKE)."""
    return f"{kind}:{row_id}"


def try_lock(conn: Connection, key: str) -> bool:
    """Take the lock row for `key`; False when another process holds it. Expired locks are replaced."""
    if conn.in_transaction:
        raise RuntimeError("locks are taken outside a transaction")
    now = datetime.now(BUSINESS_ZONE)
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
    with ExitStack() as stack:
        for key in sorted(set(keys)):
            stack.enter_context(held(conn, key, timeout_seconds))
        yield


def as_date(value) -> Optional[date]:
    """DATE columns come back as date (or datetime on some driver versions)."""
    return value.date() if isinstance(value, datetime) else value


# connection_factory: Teradata connection (TERADATA_*) and NAS / SMB session settings (NAS_*) from environment variables.

logger = logging.getLogger(__name__)

try:
    import teradatasql
    _TERADATA_AVAILABLE = True
except ImportError:
    _TERADATA_AVAILABLE = False


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Required env var '{key}' is not set.")
    return val


def build_teradata_connection(database: str = None):
    """A new teradatasql connection; `database` becomes the session's default database."""
    if not _TERADATA_AVAILABLE:
        raise ImportError("teradatasql is required. Install with: pip install teradatasql")
    host = _require("TERADATA_HOST")
    user = _require("TERADATA_USER")
    logmech = os.getenv("TERADATA_LOGMECH", "LDAP")
    logger.info("Connecting to Teradata: host=%s user=%s logmech=%s database=%s", host, user, logmech, database)
    kwargs = {"host": host, "user": user, "password": _require("TERADATA_PASSWORD"), "logmech": logmech}
    if database:
        kwargs["database"] = database
    return teradatasql.connect(**kwargs)


SOURCE_OPENERS: dict = {}


def _odbc_value(value) -> str:
    text = str(value)
    return "{" + text.replace("}", "}}") + "}" if any(c in text for c in ";{}=") else text


def sqlserver_driver(installed: Iterable[str]) -> str:
    """The newest Microsoft 'ODBC Driver NN for SQL Server' installed on the worker (18 when none is found)."""
    found = [(int(m.group(1)), d) for d in installed if (m := re.fullmatch(r"ODBC Driver (\d+) for SQL Server", d.strip()))]
    return max(found)[1] if found else "ODBC Driver 18 for SQL Server"


def source_connection(conn_cd: str, db_ty: str):
    """A DB-API connection to an internal source, from its Airflow Connection `conn_cd` (no credential is logged)."""
    if conn_cd in SOURCE_OPENERS:
        return SOURCE_OPENERS[conn_cd]()
    from airflow.sdk import Connection

    c = Connection.get(conn_cd)
    extra = c.extra_dejson or {}
    logger.info("Connecting to source %s: %s host=%s database=%s", conn_cd, db_ty, c.host, c.schema or "-")
    if db_ty == "SQLSERVER":
        try:
            import pyodbc
        except ImportError:
            raise ImportError("pyodbc is required for SQL Server sources. Install with: pip install pyodbc")
        driver = extra.get("driver") or sqlserver_driver(pyodbc.drivers())
        logger.info("Source %s uses ODBC driver %s", conn_cd, driver)
        parts = {"DRIVER": "{" + driver + "}",
                 "SERVER": f"{c.host},{c.port}" if c.port else c.host, "DATABASE": c.schema, "UID": c.login,
                 "PWD": c.password, "Encrypt": extra.get("encrypt", "yes"),
                 "TrustServerCertificate": extra.get("trust_server_certificate", "no"),
                 "Authentication": extra.get("authentication")}
        text = ";".join(f"{k}={v if k == 'DRIVER' else _odbc_value(v)}" for k, v in parts.items() if v not in (None, ""))
        return pyodbc.connect(text, autocommit=True, timeout=int(extra.get("login_timeout", 30)))
    if not _TERADATA_AVAILABLE:
        raise ImportError("teradatasql is required. Install with: pip install teradatasql")
    kwargs = {"host": c.host, "user": c.login, "password": c.password, "logmech": extra.get("logmech", "LDAP")}
    if c.schema:
        kwargs["database"] = c.schema
    return teradatasql.connect(**kwargs)


def nas_settings() -> dict:
    """SMB server and credentials of the NAS connection."""
    return {"server": _require("NAS_HOST").lower(), "username": _require("NAS_USER"),
            "password": _require("NAS_PASSWORD"), "port": int(os.getenv("NAS_PORT") or 445)}


def smb_client():
    """The smbclient module (smbprotocol)."""
    try:
        import smbclient
    except ImportError:
        raise ImportError("smbprotocol is required for NAS file paths. Install with: pip install smbprotocol")
    return smbclient


# settings: Runtime settings and the Teradata connection - no metadata tables involved.

PREFIX = "FRAMEWORK_"
_SCHEMA_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


@dataclass
class Settings:
    metadata_schema: str = "cms_compliance"
    environment: str = "DEV"
    env_value: Optional[str] = None
    business_tz: str = "America/Chicago"

    file_store: str = "nas"
    local_store_root: str = "./.local_store"
    archive_folder: str = "Archive"
    error_folder: str = "Error"
    control_report_folder: str = "ControlReport"
    error_log_folder: str = "ErrorLog"
    nas_min_age_seconds: int = 120

    supported_file_types: list[str] = field(default_factory=lambda: [".txt", ".csv"])
    file_encoding: str = "utf-8"
    quote_char: str = '"'
    empty_as_null: bool = True
    check_content: bool = True
    write_error_file: bool = True
    write_control_report: bool = True
    check_move_failed: bool = False
    archive_by_year: bool = False
    check_on_load: bool = False

    load_engine: str = "BATCH"
    fastload_min_bytes: int = 100_000_000
    insert_batch_rows: int = 2000
    insert_batch_bytes: int = 500000

    file_rules_mode: str = "GATE"
    rule_engine: str = "gre"
    gre_entrypoint: Optional[str] = "compliance_framework.export:run_gre"

    release_time: Optional[str] = None

    lock_timeout_seconds: int = 300
    lock_ttl_minutes: int = 240
    heartbeat_stale_minutes: int = 30

    notify_backend: str = "log"
    notify_from_email: Optional[str] = None
    default_notify_emails: list[str] = field(default_factory=list)

    sources: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def names(cls) -> list[str]:
        return [f.name for f in fields(cls) if f.name != "sources"]

    @classmethod
    def load(cls, env: Optional[Mapping[str, str]] = None, overrides: Optional[Mapping[str, str]] = None,
             env_file: Optional[str] = None) -> "Settings":
        env = merged_env(env, env_file)
        s = cls()
        for name in cls.names():
            key = PREFIX + name.upper()
            if key in env:
                s.set(name, env[key], env.source(key))
        for raw, value in (overrides or {}).items():
            name = raw.lower().removeprefix("framework_").replace("-", "_")
            if name not in cls.names():
                raise ConfigError(f"unknown setting {raw!r}")
            s.set(name, value, "argument")
        s.validate()
        s.env = env
        return s

    def set(self, name: str, raw: Any, source: str = "code") -> None:
        try:
            setattr(self, name, _coerce(name, raw))
        except (ValueError, TypeError) as e:
            raise ConfigError(f"setting {name}: {e}") from e
        self.sources[name] = source

    def validate(self) -> None:
        if not _SCHEMA_IDENT.match(self.metadata_schema or ""):
            raise ConfigError(f"METADATA_SCHEMA {self.metadata_schema!r} is not a valid identifier")
        self.environment = (self.environment or "").strip().upper()
        if not re.match(r"^[A-Z0-9_]{1,20}$", self.environment):
            raise ConfigError(f"ENVIRONMENT {self.environment!r} must be letters, digits or _ (e.g. DEV, TEST, PROD)")
        if self.env_value is not None and not re.match(r"^[A-Za-z0-9_]{0,20}$", self.env_value):
            raise ConfigError(f"ENV_VALUE {self.env_value!r} must be letters, digits or _")
        for name in ("file_rules_mode", "load_engine"):
            if isinstance(getattr(self, name, None), str):
                setattr(self, name, getattr(self, name).strip().upper())
        for name in ("notify_backend", "file_store"):
            if isinstance(getattr(self, name, None), str):
                setattr(self, name, getattr(self, name).strip().lower())
        if self.rule_engine.strip().lower() in ("gre", "none"):
            self.rule_engine = self.rule_engine.strip().lower()
        for name, allowed in (("file_rules_mode", ("GATE", "ANNOTATE")),
                              ("load_engine", ("BATCH", "FASTLOAD", "AUTO")),
                              ("notify_backend", ("log", "airflow")), ("file_store", ("nas", "local"))):
            if getattr(self, name) not in allowed:
                raise ConfigError(f"{name.upper()} must be one of {allowed}, got {getattr(self, name)!r}")
        for name in ("archive_folder", "error_folder", "control_report_folder", "error_log_folder"):
            if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_ .-]{0,99}$", getattr(self, name) or ""):
                raise ConfigError(f"{name.upper()} {getattr(self, name)!r} must be a plain folder name")
        if self.nas_min_age_seconds < 0:
            raise ConfigError("NAS_MIN_AGE_SECONDS must be >= 0")
        if self.fastload_min_bytes < 0:
            raise ConfigError("FASTLOAD_MIN_BYTES must be >= 0")
        if self.insert_batch_rows < 1 or self.insert_batch_bytes < 1:
            raise ConfigError("INSERT_BATCH_ROWS and INSERT_BATCH_BYTES must be >= 1")
        if self.lock_ttl_minutes < 1:
            raise ConfigError("LOCK_TTL_MINUTES must be >= 1")
        if self.release_time and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(self.release_time).strip()):
            raise ConfigError(f"RELEASE_TIME must be HH:MM (24 hours), got {self.release_time!r}")

    def db_target(self) -> dict:
        """Where this run connects (no credentials)."""
        return {"host": os.environ.get("TERADATA_HOST"), "user": os.environ.get("TERADATA_USER"),
                "logmech": os.environ.get("TERADATA_LOGMECH", "LDAP"), "database": self.metadata_schema}

    def connect(self) -> Connection:
        """Teradata connection whose default database is the metadata database."""
        try:
            raw = build_teradata_connection(self.metadata_schema)
        except (EnvironmentError, ImportError) as e:
            raise ConfigError(str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise ConfigError(f"cannot connect to {self.db_target()}: {str(e).strip().splitlines()[0]}") from e
        return Connection(raw, self.metadata_schema, self.lock_ttl_minutes * 60)

    def resolve_env(self, text: Optional[str], identifier: bool = True) -> Optional[str]:
        """$env tokens of a configured name -> this environment's value."""
        return resolve_env(text, self.environment, self.env_value, identifier)

    def describe(self) -> dict:
        return {n: {"value": getattr(self, n), "source": self.sources.get(n, "default")} for n in self.names()}


class _Env(dict):
    """Environment merged with the .env file; remembers where each value came from."""

    def __init__(self):
        super().__init__()
        self.origin: dict[str, str] = {}

    def source(self, key: str) -> str:
        return self.origin.get(key, "env")


Env = _Env   # Env() = no environment: on Airflow the settings come from the Variable only (no .env, no FRAMEWORK_*)


def merged_env(env: Optional[Mapping[str, str]] = None, env_file: Optional[str] = None) -> _Env:
    if isinstance(env, _Env):
        return env
    base = os.environ if env is None else env
    out = _Env()
    path = env_file or base.get(PREFIX + "ENV_FILE") or ".env"
    if os.path.isfile(path):
        for k, v in read_env_file(path).items():
            out[k], out.origin[k] = v, ".env"
    elif env_file or base.get(PREFIX + "ENV_FILE"):
        raise ConfigError(f"env file {path} does not exist")
    for k, v in base.items():
        out[k], out.origin[k] = v, "env"
    return out


def read_env_file(path: str) -> dict[str, str]:
    """Minimal .env reader."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.removeprefix("export ").split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            elif " #" in v:
                v = v.split(" #", 1)[0].rstrip()
            out[k.strip()] = v
    return out


@functools.cache
def _type_hints() -> dict[str, Any]:
    return typing.get_type_hints(Settings)


def _coerce(name: str, raw: Any) -> Any:
    """Convert a text value to the declared type of setting `name`."""
    if not isinstance(raw, str):
        return raw
    typ = _type_hints()[name]
    text = raw.strip()
    if typing.get_origin(typ) is typing.Union:
        if text == "" or text.lower() in ("none", "null"):
            return None
        typ = next(a for a in typing.get_args(typ) if a is not type(None))
    if typ is bool:
        if text.lower() in ("1", "true", "yes", "y", "on"):
            return True
        if text.lower() in ("0", "false", "no", "n", "off"):
            return False
        raise ValueError(f"{raw!r} is not a boolean")
    if typ is int:
        return int(text)
    if typing.get_origin(typ) is list:
        items = [x.strip() for x in text.split(",") if x.strip()]
        return [i.lower() for i in items] if name == "supported_file_types" else items
    if name == "quote_char":
        return raw
    if name.endswith(("_mode", "_engine")) and name != "rule_engine":
        return text.upper()
    return text
