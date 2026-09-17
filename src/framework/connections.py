"""PostgreSQL connection configuration.

* The **metadata database** (config, control and audit tables) is the bootstrap connection.
  Layers, highest precedence first:
    1. environment: FRAMEWORK_DB_DSN, FRAMEWORK_DB_HOST, _PORT, _NAME, _USER, _PASSWORD,
       _PASSWORD_ENV, _SSLMODE, _CONNECT_TIMEOUT, _SECRET_NAME
    2. config file section [metadata_db]: dsn, host, port, dbname, user, password, password_env,
       sslmode, connect_timeout, secret_name
    3. AWS Secrets Manager secret (JSON: host, port, dbname|database, username|user, password, sslmode)
* **Data databases** (staging/core) are named connections registered in the metadata table
  ComplianceDbConnection and referenced by ComplianceSourceFileConfig.Target_Connection_Nm
  (NULL = the metadata database). Layers, highest first:
    1. environment: FRAMEWORK_CONN_<NAME>_DSN / _HOST / _PORT / _NAME / _USER / _PASSWORD /
       _PASSWORD_ENV / _SSLMODE / _CONNECT_TIMEOUT / _SECRET_NAME
    2. config file section [connection:<name>] (same keys as [metadata_db])
    3. the ComplianceDbConnection row
    4. the Secrets Manager secret named by the first layer that provides one
  Passwords are never stored in metadata: use a secret or a password environment variable.
Within a layer, explicit keys override values parsed from that layer's DSN.
"""
from __future__ import annotations

import configparser
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from .errors import ConfigError
from .settings import Settings

log = logging.getLogger(__name__)

METADATA = "METADATA"
_FIELDS = ("host", "port", "dbname", "user", "password", "sslmode", "connect_timeout")
_ENV_SUFFIX = {"host": "HOST", "port": "PORT", "dbname": "NAME", "user": "USER", "password": "PASSWORD",
               "sslmode": "SSLMODE", "connect_timeout": "CONNECT_TIMEOUT"}
_ROW_COLS = {"host": "host", "port": "port", "dbname": "database_nm", "user": "user_nm", "sslmode": "sslmode",
             "connect_timeout": "connect_timeout_sec"}


@dataclass
class ConnSpec:
    name: str
    host: Optional[str] = None
    port: Optional[int] = None
    dbname: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    sslmode: Optional[str] = None
    connect_timeout: Optional[int] = None
    search_path: Optional[str] = None
    sources: dict = field(default_factory=dict, repr=False)

    def conninfo(self) -> str:
        kw = {k: getattr(self, k) for k in _FIELDS if getattr(self, k) not in (None, "")}
        if self.search_path:
            kw["options"] = f"-c search_path={self.search_path}"
        return make_conninfo(**kw)

    def describe(self) -> str:
        return (f"{self.name}: {self.user or '?'}@{self.host or 'localhost'}:{self.port or 5432}/"
                f"{self.dbname or '?'} sslmode={self.sslmode or 'default'}")

    def jdbc_url(self) -> str:
        url = f"jdbc:postgresql://{self.host or 'localhost'}:{self.port or 5432}/{self.dbname}"
        return url + (f"?sslmode={self.sslmode}" if self.sslmode else "")


def _norm(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _default_secret_loader(region: str) -> Callable[[str], dict]:
    def load(secret_name: str) -> dict:
        import boto3

        raw = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret_name)
        return json.loads(raw["SecretString"])
    return load


class ConnectionResolver:
    def __init__(self, settings: Settings, config: Optional[configparser.ConfigParser] = None,
                 env: Optional[Mapping[str, str]] = None,
                 secret_loader: Optional[Callable[[str], dict]] = None):
        self.settings = settings
        self.config = config if config is not None else configparser.ConfigParser(interpolation=None)
        self.env = os.environ if env is None else env
        self.secret_loader = secret_loader or _default_secret_loader(settings.aws_region)

    # ------------------------------------------------------------------ layers
    def _layer_env(self, prefix: str) -> dict:
        out: dict = {}
        dsn = self.env.get(prefix + "DSN")
        if dsn:
            out.update(self._from_dsn(dsn))
        for f, suffix in _ENV_SUFFIX.items():
            if prefix + suffix in self.env:
                out[f] = self.env[prefix + suffix]
        pw_env = self.env.get(prefix + "PASSWORD_ENV")
        if pw_env and "password" not in out and pw_env in self.env:
            out["password"] = self.env[pw_env]
        if self.env.get(prefix + "SECRET_NAME"):
            out["_secret"] = self.env[prefix + "SECRET_NAME"]
        return out

    def _layer_file(self, section: str) -> dict:
        if not self.config.has_section(section):
            return {}
        sec = dict(self.config.items(section))
        out: dict = {}
        if sec.get("dsn"):
            out.update(self._from_dsn(sec["dsn"]))
        for f in _FIELDS:
            key = "database" if f == "dbname" and "database" in sec and "dbname" not in sec else f
            if sec.get(key):
                out[f] = sec[key]
        if sec.get("password_env") and "password" not in out and sec["password_env"] in self.env:
            out["password"] = self.env[sec["password_env"]]
        if sec.get("secret_name"):
            out["_secret"] = sec["secret_name"]
        return out

    def _layer_row(self, row: Optional[Mapping]) -> dict:
        if not row:
            return {}
        out = {f: row[c] for f, c in _ROW_COLS.items() if row.get(c) not in (None, "")}
        pw_env = row.get("password_env_var")
        if pw_env and pw_env in self.env:
            out["password"] = self.env[pw_env]
        if row.get("secret_nm"):
            out["_secret"] = row["secret_nm"]
        return out

    def _layer_secret(self, secret_name: Optional[str]) -> dict:
        if not secret_name:
            return {}
        try:
            sec = self.secret_loader(secret_name)
        except Exception as e:  # noqa: BLE001
            raise ConfigError(f"cannot read secret {secret_name!r}: {type(e).__name__}: {e}") from e
        mapping = {"host": ("host",), "port": ("port",), "dbname": ("dbname", "database", "db"),
                   "user": ("username", "user"), "password": ("password",), "sslmode": ("sslmode",)}
        out = {}
        for f, keys in mapping.items():
            for k in keys:
                if sec.get(k) not in (None, ""):
                    out[f] = sec[k]
                    break
        return out

    @staticmethod
    def _from_dsn(dsn: str) -> dict:
        try:
            d = conninfo_to_dict(dsn)
        except psycopg.ProgrammingError as e:
            raise ConfigError(f"invalid DSN: {e}") from e
        return {k: v for k, v in d.items() if k in _FIELDS}

    def _merge(self, name: str, layers: list[tuple[str, dict]]) -> ConnSpec:
        """layers are given highest precedence first."""
        secret = next((l["_secret"] for _, l in layers if l.get("_secret")), None)
        ordered = [("secret", self._layer_secret(secret))] + list(reversed(layers))
        spec = ConnSpec(name)
        for label, layer in ordered:
            for f in _FIELDS:
                if f in layer:
                    setattr(spec, f, layer[f])
                    spec.sources[f] = label
        if spec.port is not None:
            spec.port = int(spec.port)
        if spec.connect_timeout is not None:
            spec.connect_timeout = int(spec.connect_timeout)
        if not spec.dbname:
            raise ConfigError(f"connection {name}: no database name configured in any layer "
                              f"({', '.join(label for label, _ in layers)} or secret)")
        return spec

    # ------------------------------------------------------------------ public
    def metadata_spec(self) -> ConnSpec:
        spec = self._merge(METADATA, [("env", self._layer_env("FRAMEWORK_DB_")),
                                      ("file", self._layer_file("metadata_db"))])
        spec.search_path = f"{self.settings.metadata_schema},public"
        return spec

    def has_external_definition(self, name: str) -> bool:
        return bool(self._layer_env(f"FRAMEWORK_CONN_{_norm(name)}_")) or self.config.has_section(f"connection:{name}")

    def named_spec(self, name: str, row: Optional[Mapping]) -> ConnSpec:
        if name.upper() == METADATA:
            return self.metadata_spec()
        return self._merge(name, [("env", self._layer_env(f"FRAMEWORK_CONN_{_norm(name)}_")),
                                  ("file", self._layer_file(f"connection:{name}")),
                                  ("metadata", self._layer_row(row))])


def open_connection(spec: ConnSpec) -> psycopg.Connection:
    """Direct (non-pooled) autocommit connection - session advisory locks depend on it (D-55)."""
    try:
        return psycopg.connect(spec.conninfo(), autocommit=True, row_factory=dict_row,
                               application_name="cms-compliance-framework")
    except psycopg.OperationalError as e:
        raise ConfigError(f"cannot connect to {spec.describe()}: {str(e).strip().splitlines()[0]}") from e


def spec_from_connection(name: str, conn: psycopg.Connection) -> ConnSpec:
    """Describe an already-open connection (injected connections have no resolver definition)."""
    p = conn.info.get_parameters()
    spec = ConnSpec(name, host=p.get("host"), port=int(p["port"]) if p.get("port") else None,
                    dbname=p.get("dbname"), user=p.get("user"), password=conn.info.password,
                    sslmode=p.get("sslmode"))
    spec.sources = {f: "connection" for f in _FIELDS if getattr(spec, f) is not None}
    return spec


class ConnectionManager:
    """Opens and caches the metadata connection and the named data connections."""

    def __init__(self, resolver: ConnectionResolver, metadata_conn: Optional[psycopg.Connection] = None):
        self.resolver = resolver
        self._meta = metadata_conn
        self._conns: dict[str, psycopg.Connection] = {}
        self._specs: dict[str, ConnSpec] = {}

    @property
    def meta(self) -> psycopg.Connection:
        if self._meta is None:
            spec = self.resolver.metadata_spec()
            self._specs[METADATA] = spec
            self._meta = open_connection(spec)
        return self._meta

    def _row(self, name: str) -> Optional[dict]:
        return self.meta.execute(
            "SELECT * FROM ComplianceDbConnection WHERE Connection_Nm = %s AND Active_Ind = 1", (name,)).fetchone()

    def spec(self, name: Optional[str]) -> ConnSpec:
        if not name or name.upper() == METADATA:
            if METADATA not in self._specs:
                self._specs[METADATA] = (spec_from_connection(METADATA, self._meta) if self._meta is not None
                                         else self.resolver.metadata_spec())
            return self._specs[METADATA]
        if name not in self._specs and name in self._conns:
            self._specs[name] = spec_from_connection(name, self._conns[name])
        if name not in self._specs:
            row = self._row(name)
            if row is None and not self.resolver.has_external_definition(name):
                raise ConfigError(f"connection {name!r} is not registered in ComplianceDbConnection "
                                  f"(or is inactive) and has no env/config-file definition")
            self._specs[name] = self.resolver.named_spec(name, row)
        return self._specs[name]

    def get(self, name: Optional[str]) -> psycopg.Connection:
        if not name or name.upper() == METADATA:
            return self.meta
        conn = self._conns.get(name)
        if conn is None or conn.closed:
            conn = open_connection(self.spec(name))
            self._conns[name] = conn
        return conn

    def register(self, name: str, conn: psycopg.Connection, spec: Optional[ConnSpec] = None) -> None:
        """Inject an already-open connection (tests, embedding)."""
        self._conns[name] = conn
        if spec is not None:
            self._specs[name] = spec

    def for_config(self, cfg) -> psycopg.Connection:
        return self.get(cfg.target_connection_nm)

    def is_metadata(self, name: Optional[str]) -> bool:
        return not name or name.upper() == METADATA

    def close(self) -> None:
        for c in self._conns.values():
            if not c.closed:
                c.close()
        self._conns.clear()
        if self._meta is not None and not self._meta.closed:
            self._meta.close()
