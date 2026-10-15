"""Teradata connection (TERADATA_*) and NAS / SMB session settings (NAS_*) from environment variables."""
import logging
import os

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


def source_connection(conn_cd: str, db_ty: str):
    """A DB-API connection to an internal source, from its Airflow Connection `conn_cd` (no credential is logged)."""
    if conn_cd in SOURCE_OPENERS:
        return SOURCE_OPENERS[conn_cd]()
    from airflow.hooks.base import BaseHook

    c = BaseHook.get_connection(conn_cd)
    extra = c.extra_dejson or {}
    logger.info("Connecting to source %s: %s host=%s database=%s", conn_cd, db_ty, c.host, c.schema or "-")
    if db_ty == "SQLSERVER":
        try:
            import pyodbc
        except ImportError:
            raise ImportError("pyodbc is required for SQL Server sources. Install with: pip install pyodbc")
        parts = {"DRIVER": "{" + extra.get("driver", "ODBC Driver 18 for SQL Server") + "}",
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
