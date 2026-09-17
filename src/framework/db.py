"""Database helpers. Connections are autocommit; every unit of work uses an explicit
`with conn.transaction():` block so the transaction boundary is visible in code.
Connection configuration lives in connections.py."""
from __future__ import annotations

from importlib import resources
from typing import Iterable, Optional

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .settings import DESCRIPTIONS, BOOTSTRAP, Settings


def connect(dsn: str, schema: Optional[str] = None) -> psycopg.Connection:
    """Open a direct autocommit connection from a DSN (tests / ad-hoc use)."""
    kw = {"options": f"-c search_path={schema},public"} if schema else {}
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                           application_name="cms-compliance-framework", **kw)


def _sql_files(sub: str) -> Iterable[tuple[str, str]]:
    base = resources.files("framework").joinpath("sql").joinpath(sub)
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".sql"):
            yield entry.name, entry.read_text(encoding="utf-8")


def schema_exists(conn: psycopg.Connection, schema: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"{schema}.compliancerequestcontrol",)).fetchone()
    return bool(row["ok"])


def _ensure_btree_gist(conn: psycopg.Connection, schema: str) -> str:
    """btree_gist (Q-11) is installed once per database, preferably in `public`, so dropping one metadata
    schema never cascades into the exclusion constraints of another. Falls back to the metadata schema
    when `public` is not writable (PostgreSQL 15+ default privileges)."""
    row = conn.execute("SELECT extnamespace::regnamespace::text AS ns FROM pg_extension "
                       "WHERE extname = 'btree_gist'").fetchone()
    if row:
        return f"extension btree_gist (already installed in {row['ns']})"
    try:
        with conn.transaction():
            conn.execute("CREATE EXTENSION btree_gist SCHEMA public")
        return "extension btree_gist (installed in public)"
    except psycopg.errors.InsufficientPrivilege:
        conn.execute(sql.SQL("CREATE EXTENSION btree_gist SCHEMA {}").format(sql.Identifier(schema)))
        return f"extension btree_gist (installed in {schema}: no CREATE privilege on public)"


def init_db(conn: psycopg.Connection, schema: str = "cms_compliance", *,
            include_provisional_status: bool = True) -> list[str]:
    """Create the metadata schema (if missing), apply DDL once, and apply idempotent seed data."""
    Settings(metadata_schema=schema).validate_bootstrap()
    applied = []
    with conn.transaction():
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema)))
        applied.append(_ensure_btree_gist(conn, schema))
        exists = schema_exists(conn, schema)
        for name, text in _sql_files("ddl"):
            if exists:
                applied.append(f"ddl/{name} (skipped: schema already initialised)")
                continue
            conn.execute(text)
            applied.append(f"ddl/{name}")
        for name, text in _sql_files("seed"):
            if "PROVISIONAL" in name and not include_provisional_status:
                continue
            conn.execute(text)
            applied.append(f"seed/{name}")
        defaults = Settings()
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO ComplianceFrameworkSetting (Setting_Nm, Setting_Val, Setting_Desc)
                   VALUES (%s, %s, %s) ON CONFLICT (Setting_Nm) DO NOTHING""",
                [(n.upper(), defaults.as_text(n), DESCRIPTIONS.get(n))
                 for n in Settings.names() if n not in BOOTSTRAP])
        applied.append("seed/framework settings (defaults)")
    return applied


def load_metadata_settings(conn: psycopg.Connection) -> dict[str, Optional[str]]:
    rows = conn.execute("SELECT Setting_Nm, Setting_Val FROM ComplianceFrameworkSetting WHERE Active_Ind = 1").fetchall()
    return {r["setting_nm"]: r["setting_val"] for r in rows}
