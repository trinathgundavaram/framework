"""Teradata connection from TERADATA_* environment variables (teradatasql)."""
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
