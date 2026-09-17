"""Runtime settings and the database connection - no metadata tables involved.

Every setting is read, highest precedence first, from:
  1. job arguments       `framework --set NAME=VALUE ...` (Glue/Step Functions pass these per project)
  2. the environment     FRAMEWORK_<NAME>
  3. a .env file         FRAMEWORK_ENV_FILE, default ./.env (local development)
  4. the built-in default below

Database (one PostgreSQL database holds the metadata schema and the staging/core tables):
  * local:  FRAMEWORK_DB_DSN, or FRAMEWORK_DB_HOST / _PORT / _NAME / _USER / _PASSWORD / _SSLMODE in .env
  * AWS:    FRAMEWORK_DB_SECRET_NAME -> Secrets Manager JSON {host, port, dbname, username, password[, sslmode]}
  Explicit FRAMEWORK_DB_* values override values read from the secret.
"""
from __future__ import annotations

import json
import os
import re
import typing
from dataclasses import dataclass, field, fields
from datetime import date
from typing import Any, Callable, Mapping, Optional

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from .common import ConfigError

PREFIX = "FRAMEWORK_"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_DB_FIELDS = {"host": "HOST", "port": "PORT", "dbname": "NAME", "user": "USER", "password": "PASSWORD",
              "sslmode": "SSLMODE", "connect_timeout": "CONNECT_TIMEOUT"}
_SECRET_KEYS = {"host": ("host",), "port": ("port",), "dbname": ("dbname", "database", "db"),
                "user": ("username", "user"), "password": ("password",), "sslmode": ("sslmode",)}


@dataclass
class Settings:
    # --- environment / database ---
    metadata_schema: str = "cms_compliance"
    aws_region: str = "us-east-1"
    business_tz: str = "America/Chicago"          # batch dates, SLA hold (was ComplianceDataSetSourceXwalk.Business_Tz)

    # --- object storage ---
    object_store: str = "s3"                      # s3 | local
    local_store_root: str = "./.local_store"
    default_quarantine_uri: str = "s3://quarantine-bucket-not-configured/unmatched/"

    # --- filename matching / file reading ---
    filename_case_sensitive: bool = True
    file_effective_date_basis: str = "RPT_START"  # RPT_START | RPT_END
    supported_file_types: list[str] = field(default_factory=lambda: [".txt", ".csv"])
    file_encoding: str = "utf-8"
    quote_char: str = '"'
    empty_as_null: bool = True
    trailer_count_check: bool = False
    trailer_count_regex: str = r"(\d+)"
    xlsx_sheet: str = "0"
    xlsx_header_row: int = 0

    # --- load / rules (job level; were per file config) ---
    load_engine: str = "PANDAS"                   # PANDAS | SPARK
    file_rules_mode: str = "GATE"                 # GATE | ANNOTATE
    rule_engine: str = "gre"                      # gre | none | module:Class
    gre_entrypoint: Optional[str] = None
    spark_jdbc_url: Optional[str] = None
    spark_write_partitions: int = 4
    spark_batch_size: int = 10000

    # --- locking / health / intake ---
    lock_timeout_seconds: int = 300
    heartbeat_stale_minutes: int = 30
    adhoc_allow_add_source_before_trigger: bool = True
    cycle_init_existing_batch: str = "SKIP"       # SKIP | FAIL

    # --- extract (job level; were ComplianceExtractPolicy / ComplianceExtractJobParam) ---
    extract_gating_mode: str = "STRICT_ALL_PASS"  # STRICT_ALL_PASS | BEST_EFFORT
    period_rules_mode: str = "GATE"               # GATE | ANNOTATE
    extract_job_type: str = "GLUE_JOB"            # GLUE_JOB | HTTP_API
    extract_job_name: Optional[str] = None        # GLUE_JOB
    extract_endpoint_url: Optional[str] = None    # HTTP_API
    extract_http_method: str = "POST"
    extract_auth_secret_name: Optional[str] = None
    extract_call_timeout_sec: int = 60
    extract_max_call_retries: int = 0
    extract_params: dict = field(default_factory=dict)   # JSON {"--NAME": "text with {placeholders}"}
    strict_waiver_auto_trigger: bool = False
    auto_retrigger_after_reopen: bool = True
    retry_failed_triggers_on_sweep: bool = False
    call_retry_backoff_seconds: int = 5
    trigger_reconcile_minutes: int = 15
    http_accepted_status: list[str] = field(default_factory=lambda: ["200-299"])
    param_date_format: str = "%Y-%m-%d"

    # --- notifications ---
    notify_backend: str = "log"                   # log | aws
    notify_from_email: Optional[str] = None
    default_notify_emails: list[str] = field(default_factory=list)
    sns_topic_arn: Optional[str] = None

    # --- provenance (not a setting) ---
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
        if not _IDENT.match(self.metadata_schema or ""):
            raise ConfigError(f"METADATA_SCHEMA {self.metadata_schema!r} is not a valid identifier")
        for name, allowed in (("load_engine", ("PANDAS", "SPARK")), ("file_rules_mode", ("GATE", "ANNOTATE")),
                              ("period_rules_mode", ("GATE", "ANNOTATE")),
                              ("extract_gating_mode", ("STRICT_ALL_PASS", "BEST_EFFORT")),
                              ("extract_job_type", ("GLUE_JOB", "HTTP_API")),
                              ("cycle_init_existing_batch", ("SKIP", "FAIL")),
                              ("file_effective_date_basis", ("RPT_START", "RPT_END"))):
            if getattr(self, name) not in allowed:
                raise ConfigError(f"{name.upper()} must be one of {allowed}, got {getattr(self, name)!r}")

    # ------------------------------------------------------------------ database
    def db_conninfo(self, secret_loader: Optional[Callable[[str], dict]] = None) -> tuple[str, dict]:
        """Returns (conninfo, description-without-password)."""
        env = getattr(self, "env", None) or merged_env()
        values: dict = {}
        secret_name = env.get(PREFIX + "DB_SECRET_NAME")
        if secret_name:
            loader = secret_loader or _secret_loader(self.aws_region)
            try:
                sec = loader(secret_name)
            except Exception as e:  # noqa: BLE001
                raise ConfigError(f"cannot read secret {secret_name!r}: {type(e).__name__}: {e}") from e
            for f, keys in _SECRET_KEYS.items():
                v = next((sec[k] for k in keys if sec.get(k) not in (None, "")), None)
                if v is not None:
                    values[f] = v
        if env.get(PREFIX + "DB_DSN"):
            try:
                values.update({k: v for k, v in conninfo_to_dict(env[PREFIX + "DB_DSN"]).items() if k in _DB_FIELDS})
            except psycopg.ProgrammingError as e:
                raise ConfigError(f"invalid FRAMEWORK_DB_DSN: {e}") from e
        for f, suffix in _DB_FIELDS.items():
            if env.get(PREFIX + "DB_" + suffix) not in (None, ""):
                values[f] = env[PREFIX + "DB_" + suffix]
        if not values.get("dbname"):
            raise ConfigError("no database configured: set FRAMEWORK_DB_DSN / FRAMEWORK_DB_NAME (.env) "
                              "or FRAMEWORK_DB_SECRET_NAME (AWS)")
        desc = {k: v for k, v in values.items() if k != "password"}
        desc["schema"] = self.metadata_schema
        values["options"] = f"-c search_path={self.metadata_schema},public"
        return make_conninfo(**{k: str(v) for k, v in values.items()}), desc

    def connect(self, secret_loader=None) -> psycopg.Connection:
        """Direct autocommit connection - session advisory locks depend on it (D-55)."""
        conninfo, desc = self.db_conninfo(secret_loader)
        try:
            return psycopg.connect(conninfo, autocommit=True, row_factory=dict_row,
                                   application_name="cms-compliance-framework")
        except psycopg.OperationalError as e:
            raise ConfigError(f"cannot connect to {desc}: {str(e).strip().splitlines()[0]}") from e

    def describe(self) -> dict:
        return {n: {"value": getattr(self, n), "source": self.sources.get(n, "default")} for n in self.names()}


class _Env(dict):
    """Environment merged with the .env file; remembers where each value came from."""

    def __init__(self):
        super().__init__()
        self.origin: dict[str, str] = {}

    def source(self, key: str) -> str:
        return self.origin.get(key, "env")


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
    """Minimal .env reader: KEY=VALUE lines, optional quotes, '#' comments, optional 'export '."""
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


def _secret_loader(region: str) -> Callable[[str], dict]:
    def load(name: str) -> dict:
        import boto3

        return json.loads(boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=name)["SecretString"])
    return load


def _coerce(name: str, raw: Any) -> Any:
    """Convert a text value to the declared type of setting `name`."""
    if not isinstance(raw, str):
        return raw
    typ = typing.get_type_hints(Settings)[name]
    text = raw.strip()
    if typing.get_origin(typ) is typing.Union:                     # Optional[X]
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
    if typ is date:
        return date.fromisoformat(text)
    if typing.get_origin(typ) is list:
        items = [x.strip() for x in text.split(",") if x.strip()]
        return [i.lower() for i in items] if name == "supported_file_types" else items
    if typ is dict:
        value = json.loads(text) if text else {}
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        return value
    if name == "quote_char":
        return raw
    if name.endswith(("_mode", "_engine", "_type", "_existing_batch", "_basis")) and name != "rule_engine":
        return text.upper()
    return text
