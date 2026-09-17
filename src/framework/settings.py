"""Runtime settings.

Precedence for every setting:  environment (FRAMEWORK_<NAME>)  >  config file [settings]  >
metadata table ComplianceFrameworkSetting (Setting_Nm = <NAME>)  >  built-in default.

Bootstrap settings (needed before the metadata database can be reached) come from the
environment or the config file only: CONFIG_FILE, METADATA_SCHEMA, AWS_REGION.
The metadata / data database connections are resolved separately (see connections.py).
"""
from __future__ import annotations

import configparser
import json
import logging
import os
import re
import typing
from dataclasses import dataclass, field, fields
from datetime import date
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)

BOOTSTRAP = ("config_file", "metadata_schema", "aws_region")
DEFAULT_CONFIG_FILE = "framework.ini"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# name -> description (seeded into ComplianceFrameworkSetting by init-db)
DESCRIPTIONS = {
    "object_store": "s3 | local",
    "local_store_root": "root folder when OBJECT_STORE=local (s3://bucket/key -> <root>/bucket/key)",
    "default_quarantine_uri": "quarantine location for files that match no file config",
    "filename_case_sensitive": "Q-03: filename template matching is case-sensitive",
    "file_effective_date_basis": "Q-04: RPT_START | RPT_END - date used to pick the effective crosswalk row",
    "supported_file_types": "Q-02: comma-separated enabled file types",
    "file_encoding": "Q-02: text encoding of delimited files",
    "quote_char": "Q-02: quote character of delimited files",
    "empty_as_null": "load empty strings as NULL",
    "trailer_count_check": "Q-02: validate the trailer record count",
    "trailer_count_regex": "Q-02: regex whose first group is the trailer record count",
    "xlsx_sheet": "Q-02: sheet index or name for xlsx files",
    "xlsx_header_row": "Q-02: 0-based row where the xlsx content starts",
    "scheduler_window_hours": "cron fires newer than this are created by create-batches",
    "catchup_lookback_days": "Q-14: catch-up horizon in days",
    "go_live_date": "Q-14: no batches are created for scheduled dates before this (YYYY-MM-DD)",
    "lock_timeout_seconds": "seconds to wait for a batch/extract advisory lock",
    "heartbeat_stale_minutes": "health: loads without heartbeat for this long are stale",
    "trigger_reconcile_minutes": "REQUESTED triggers older than this are reconciled",
    "strict_waiver_auto_trigger": "Q-06: STRICT extracts satisfied through waivers trigger automatically",
    "auto_retrigger_after_reopen": "D-41/Q-16: re-trigger automatically after a reopen is promoted",
    "retry_failed_triggers_on_sweep": "Q-07: evaluate-extracts re-fires FAILED triggers",
    "call_retry_backoff_seconds": "Q-07: base backoff between extract call retries",
    "http_accepted_status": "Q-08: HTTP status ranges treated as accepted, e.g. 200-299",
    "param_date_format": "strftime format for date extract-job parameters",
    "adhoc_allow_add_source_before_trigger": "Q-05: ad-hoc intake may add a source to an untriggered extract",
    "cycle_init_existing_batch": "Q-18: SKIP | FAIL when a CYCLE_INIT batch already exists",
    "rule_engine": "Q-12: gre | none | module:Class",
    "gre_entrypoint": "Q-12: module:function implementing the GRE call",
    "notify_backend": "log | aws",
    "notify_from_email": "SES sender address",
    "default_notify_emails": "recipients for events without a file config",
    "spark_jdbc_url": "override the JDBC URL derived from the target connection (Spark engine)",
    "spark_jdbc_properties": "extra JDBC properties as JSON (Spark engine)",
    "spark_write_partitions": "Spark JDBC write partitions",
    "spark_batch_size": "Spark JDBC batch size",
}


@dataclass
class Settings:
    # --- bootstrap (env / config file only) ---
    config_file: Optional[str] = None
    metadata_schema: str = "cms_compliance"
    aws_region: str = "us-east-1"

    # --- object storage ---
    object_store: str = "s3"
    local_store_root: str = "./.local_store"
    default_quarantine_uri: str = "s3://quarantine-bucket-not-configured/unmatched/"

    # --- filename matching (Q-03, Q-04) ---
    filename_case_sensitive: bool = True
    file_effective_date_basis: str = "RPT_START"

    # --- file reading (Q-02) ---
    supported_file_types: list[str] = field(default_factory=lambda: [".txt", ".csv"])
    file_encoding: str = "utf-8"
    quote_char: str = '"'
    empty_as_null: bool = True
    trailer_count_check: bool = False
    trailer_count_regex: str = r"(\d+)"
    xlsx_sheet: str = "0"
    xlsx_header_row: int = 0

    # --- scheduling (Q-14) ---
    scheduler_window_hours: int = 2
    catchup_lookback_days: int = 35
    go_live_date: Optional[date] = None

    # --- locking / health ---
    lock_timeout_seconds: int = 300
    heartbeat_stale_minutes: int = 30
    trigger_reconcile_minutes: int = 15

    # --- extract trigger (Q-06, Q-07, Q-08, Q-16) ---
    strict_waiver_auto_trigger: bool = False
    auto_retrigger_after_reopen: bool = True
    retry_failed_triggers_on_sweep: bool = False
    call_retry_backoff_seconds: int = 5
    http_accepted_status: list[str] = field(default_factory=lambda: ["200-299"])
    param_date_format: str = "%Y-%m-%d"

    # --- intake (Q-05, Q-18) ---
    adhoc_allow_add_source_before_trigger: bool = True
    cycle_init_existing_batch: str = "SKIP"

    # --- rules engine (Q-12) ---
    rule_engine: str = "gre"
    gre_entrypoint: Optional[str] = None

    # --- notifications (D-54) ---
    notify_backend: str = "log"
    notify_from_email: Optional[str] = None
    default_notify_emails: list[str] = field(default_factory=list)

    # --- spark engine ---
    spark_jdbc_url: Optional[str] = None
    spark_jdbc_properties: dict = field(default_factory=dict)
    spark_write_partitions: int = 4
    spark_batch_size: int = 10000

    # --- provenance (not a setting) ---
    sources: dict = field(default_factory=dict, repr=False, compare=False)

    # ------------------------------------------------------------------ loading
    @classmethod
    def names(cls) -> list[str]:
        return [f.name for f in fields(cls) if f.name != "sources"]

    @classmethod
    def load(cls, env: Optional[Mapping[str, str]] = None,
             config: Optional[configparser.ConfigParser] = None) -> "Settings":
        """Environment + config file layers (metadata is applied later with apply_metadata)."""
        env = os.environ if env is None else env
        s = cls()
        path = env.get("FRAMEWORK_CONFIG_FILE")
        if config is None:
            config = read_config_file(path)
        s.config_file = path or (DEFAULT_CONFIG_FILE if os.path.isfile(DEFAULT_CONFIG_FILE) else None)
        file_values = dict(config.items("settings")) if config.has_section("settings") else {}
        for name in cls.names():
            if name == "config_file":
                continue
            if name in file_values:
                s._set(name, file_values[name], "file")
            env_key = f"FRAMEWORK_{name.upper()}"
            if env_key in env:
                s._set(name, env[env_key], "env")
        s.validate_bootstrap()
        return s

    # kept for backwards compatibility
    @classmethod
    def from_env(cls) -> "Settings":
        return cls.load()

    def validate_bootstrap(self) -> None:
        if not _IDENT.match(self.metadata_schema or ""):
            raise ValueError(f"METADATA_SCHEMA {self.metadata_schema!r} is not a valid identifier")

    def apply_metadata(self, rows: Mapping[str, Optional[str]]) -> list[str]:
        """Apply ComplianceFrameworkSetting values for settings not set by env/file. Returns unknown names."""
        unknown = []
        known = set(self.names())
        for raw_name, value in rows.items():
            name = raw_name.lower()
            if name not in known:
                unknown.append(raw_name)
                continue
            if name in BOOTSTRAP or self.sources.get(name) in ("env", "file") or value is None:
                continue
            self._set(name, value, "metadata")
        return unknown

    def _set(self, name: str, raw: Any, source: str) -> None:
        setattr(self, name, coerce(name, raw))
        self.sources[name] = source

    def as_text(self, name: str) -> Optional[str]:
        v = getattr(self, name)
        if v is None:
            return None
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, list):
            return ",".join(v)
        if isinstance(v, dict):
            return json.dumps(v)
        return v.isoformat() if isinstance(v, date) else str(v)


def _hints() -> dict:
    return typing.get_type_hints(Settings)


def coerce(name: str, raw: Any) -> Any:
    """Convert a text value to the declared type of setting `name` (raises ValueError)."""
    typ = _hints()[name]
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    origin = typing.get_origin(typ)
    args = typing.get_args(typ)
    if origin is typing.Union and type(None) in args:           # Optional[X]
        if text == "" or text.lower() in ("none", "null"):
            return None
        typ = next(a for a in args if a is not type(None))
        origin = typing.get_origin(typ)
    if typ is bool:
        if text.lower() in ("1", "true", "yes", "y", "on"):
            return True
        if text.lower() in ("0", "false", "no", "n", "off"):
            return False
        raise ValueError(f"{name}: {raw!r} is not a boolean")
    if typ is int:
        return int(text)
    if typ is date:
        return date.fromisoformat(text)
    if origin is list or typ is list:
        items = [x.strip() for x in text.split(",") if x.strip()]
        return [i.lower() for i in items] if name == "supported_file_types" else items
    if typ is dict or origin is dict:
        return json.loads(text) if text else {}
    if name == "cycle_init_existing_batch":
        return text.upper()
    return raw if name in ("quote_char",) else text


def read_config_file(path: Optional[str]) -> configparser.ConfigParser:
    """INI config file. Missing explicit path is an error; the default ./framework.ini is optional."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str.lower
    if path:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"FRAMEWORK_CONFIG_FILE {path} does not exist")
        cp.read(path, encoding="utf-8")
    elif os.path.isfile(DEFAULT_CONFIG_FILE):
        cp.read(DEFAULT_CONFIG_FILE, encoding="utf-8")
    return cp
