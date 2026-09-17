"""Postgres connectivity. Connections are autocommit; every unit of work uses an explicit
`with conn.transaction():` block so the transaction boundary is visible in code."""
from __future__ import annotations

import json
from importlib import resources
from typing import Iterable, Optional

import psycopg
from psycopg.rows import dict_row

from .errors import ConfigError
from .settings import Settings

SCHEMA = "cms_compliance"


def _dsn_from_secret(secret_name: str, region: str) -> str:
    import boto3  # lazy: only needed on AWS

    raw = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret_name)
    sec = json.loads(raw["SecretString"])
    return psycopg.conninfo.make_conninfo(
        host=sec["host"], port=sec.get("port", 5432), dbname=sec.get("dbname") or sec.get("database"),
        user=sec["username"], password=sec["password"], sslmode=sec.get("sslmode", "require"),
    )


def resolve_dsn(settings: Settings) -> str:
    if settings.db_dsn:
        return settings.db_dsn
    if settings.db_secret_name:
        return _dsn_from_secret(settings.db_secret_name, settings.aws_region)
    raise ConfigError("Set FRAMEWORK_DB_DSN or FRAMEWORK_DB_SECRET_NAME")


def connect(dsn: str) -> psycopg.Connection:
    """Direct (non-pooled) connection; session advisory locks depend on it (D-55)."""
    return psycopg.connect(
        dsn, autocommit=True, row_factory=dict_row, options=f"-c search_path={SCHEMA},public",
        application_name="cms-compliance-framework",
    )


def fetch_one(conn: psycopg.Connection, sql, params=None) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_all(conn: psycopg.Connection, sql, params=None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def execute(conn: psycopg.Connection, sql, params=None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def _sql_files(sub: str) -> Iterable[tuple[str, str]]:
    base = resources.files("framework").joinpath("sql").joinpath(sub)
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".sql"):
            yield entry.name, entry.read_text(encoding="utf-8")


def init_db(conn: psycopg.Connection, *, include_provisional_status: bool = True) -> list[str]:
    """Apply the schema DDL (only if the schema is not initialised yet) and the idempotent seed data."""
    applied = []
    with conn.transaction():
        exists = conn.execute(
            f"SELECT to_regclass('{SCHEMA}.compliancerequestcontrol') IS NOT NULL AS ok").fetchone()["ok"]
        for name, text in _sql_files("ddl"):
            if exists:
                applied.append(f"ddl/{name} (skipped: schema exists)")
                continue
            conn.execute(text)
            applied.append(f"ddl/{name}")
        for name, text in _sql_files("seed"):
            if "PROVISIONAL" in name and not include_provisional_status:
                continue
            conn.execute(text)
            applied.append(f"seed/{name}")
    return applied
