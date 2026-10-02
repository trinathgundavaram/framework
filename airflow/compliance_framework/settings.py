"""Runtime settings and the Teradata connection - no metadata tables involved."""
from __future__ import annotations

import functools
import os
import re
import typing
from dataclasses import dataclass, field, fields
from typing import Any, Mapping, Optional

from .common import ConfigError
from .connection_factory import build_teradata_connection
from .db import Connection

PREFIX = "FRAMEWORK_"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


@dataclass
class Settings:
    metadata_schema: str = "cms_compliance"
    aws_region: str = "us-east-1"
    business_tz: str = "America/Chicago"

    object_store: str = "s3"
    local_store_root: str = "./.local_store"
    quarantine_uri: str = "s3://quarantine-bucket-not-configured/"

    filename_case_sensitive: bool = True
    file_effective_date_basis: str = "RPT_START"
    supported_file_types: list[str] = field(default_factory=lambda: [".txt", ".csv"])
    file_encoding: str = "utf-8"
    quote_char: str = '"'
    empty_as_null: bool = True
    trailer_count_check: bool = False
    trailer_count_regex: str = r"(\d+)"
    xlsx_sheet: str = "0"
    xlsx_header_row: int = 0

    file_rules_mode: str = "GATE"
    rule_engine: str = "gre"
    gre_entrypoint: Optional[str] = None

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
        if not _IDENT.match(self.metadata_schema or ""):
            raise ConfigError(f"METADATA_SCHEMA {self.metadata_schema!r} is not a valid identifier")
        for name, allowed in (("file_rules_mode", ("GATE", "ANNOTATE")),
                              ("file_effective_date_basis", ("RPT_START", "RPT_END")),
                              ("notify_backend", ("log", "ses", "airflow")), ("object_store", ("s3", "local"))):
            if getattr(self, name) not in allowed:
                raise ConfigError(f"{name.upper()} must be one of {allowed}, got {getattr(self, name)!r}")
        if self.lock_ttl_minutes < 1:
            raise ConfigError("LOCK_TTL_MINUTES must be >= 1")

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
    if name.endswith(("_mode", "_engine", "_basis")) and name != "rule_engine":
        return text.upper()
    return text
