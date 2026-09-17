"""Runtime settings, read from environment variables (prefix FRAMEWORK_).

Nothing here is project-specific. Values that answer still-open design questions are
marked with the question id so they can be changed without code changes.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(f"FRAMEWORK_{name}", default)


def _bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _int(name: str, default: int) -> int:
    raw = _env(name)
    return default if raw is None else int(raw)


def _list(name: str, default: list[str]) -> list[str]:
    raw = _env(name)
    if raw is None:
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    # --- database (D-55: direct connection, no pooler) ---
    db_dsn: Optional[str] = None
    db_secret_name: Optional[str] = None
    aws_region: str = "us-east-1"

    # --- object storage ---
    object_store: str = "s3"                      # s3 | local
    local_store_root: str = "./.local_store"
    default_quarantine_uri: str = "s3://quarantine-bucket-not-configured/unmatched/"

    # --- filename matching (Q-03, Q-04) ---
    filename_case_sensitive: bool = True           # Q-03 proposal
    file_effective_date_basis: str = "RPT_START"   # Q-04 proposal: RPT_START | RPT_END

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

    # --- extract trigger (Q-06, Q-07, Q-08, Q-09, Q-16) ---
    strict_waiver_auto_trigger: bool = False       # Q-06 proposal: waivers -> manual trigger
    auto_retrigger_after_reopen: bool = True       # D-41 (Q-16 may change this)
    retry_failed_triggers_on_sweep: bool = False   # Q-07
    call_retry_backoff_seconds: int = 5            # Q-07
    http_accepted_status: list[str] = field(default_factory=lambda: ["200-299"])  # Q-08
    param_date_format: str = "%Y-%m-%d"

    # --- intake (Q-05, Q-18) ---
    adhoc_allow_add_source_before_trigger: bool = True   # Q-05 proposal
    cycle_init_existing_batch: str = "SKIP"              # Q-18 proposal: SKIP | FAIL

    # --- rules engine (Q-12) ---
    rule_engine: str = "gre"                       # gre | none | "module:Class"
    gre_entrypoint: Optional[str] = None           # "module:function" (Q-12)

    # --- notifications (D-54) ---
    notify_backend: str = "log"                    # log | aws
    notify_from_email: Optional[str] = None
    default_notify_emails: list[str] = field(default_factory=list)

    # --- spark engine ---
    spark_jdbc_url: Optional[str] = None
    spark_jdbc_properties: dict = field(default_factory=dict)
    spark_write_partitions: int = 4
    spark_batch_size: int = 10000

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.db_dsn = _env("DB_DSN")
        s.db_secret_name = _env("DB_SECRET_NAME")
        s.aws_region = _env("AWS_REGION", os.environ.get("AWS_REGION", s.aws_region))
        s.object_store = _env("OBJECT_STORE", s.object_store)
        s.local_store_root = _env("LOCAL_STORE_ROOT", s.local_store_root)
        s.default_quarantine_uri = _env("DEFAULT_QUARANTINE_URI", s.default_quarantine_uri)
        s.filename_case_sensitive = _bool("FILENAME_CASE_SENSITIVE", s.filename_case_sensitive)
        s.file_effective_date_basis = _env("FILE_EFFECTIVE_DATE_BASIS", s.file_effective_date_basis)
        s.supported_file_types = [t.lower() for t in _list("SUPPORTED_FILE_TYPES", s.supported_file_types)]
        s.file_encoding = _env("FILE_ENCODING", s.file_encoding)
        s.quote_char = _env("QUOTE_CHAR", s.quote_char)
        s.empty_as_null = _bool("EMPTY_AS_NULL", s.empty_as_null)
        s.trailer_count_check = _bool("TRAILER_COUNT_CHECK", s.trailer_count_check)
        s.trailer_count_regex = _env("TRAILER_COUNT_REGEX", s.trailer_count_regex)
        s.xlsx_sheet = _env("XLSX_SHEET", s.xlsx_sheet)
        s.xlsx_header_row = _int("XLSX_HEADER_ROW", s.xlsx_header_row)
        s.scheduler_window_hours = _int("SCHEDULER_WINDOW_HOURS", s.scheduler_window_hours)
        s.catchup_lookback_days = _int("CATCHUP_LOOKBACK_DAYS", s.catchup_lookback_days)
        gl = _env("GO_LIVE_DATE")
        s.go_live_date = date.fromisoformat(gl) if gl else None
        s.lock_timeout_seconds = _int("LOCK_TIMEOUT_SECONDS", s.lock_timeout_seconds)
        s.heartbeat_stale_minutes = _int("HEARTBEAT_STALE_MINUTES", s.heartbeat_stale_minutes)
        s.trigger_reconcile_minutes = _int("TRIGGER_RECONCILE_MINUTES", s.trigger_reconcile_minutes)
        s.strict_waiver_auto_trigger = _bool("STRICT_WAIVER_AUTO_TRIGGER", s.strict_waiver_auto_trigger)
        s.auto_retrigger_after_reopen = _bool("AUTO_RETRIGGER_AFTER_REOPEN", s.auto_retrigger_after_reopen)
        s.retry_failed_triggers_on_sweep = _bool("RETRY_FAILED_TRIGGERS_ON_SWEEP", s.retry_failed_triggers_on_sweep)
        s.call_retry_backoff_seconds = _int("CALL_RETRY_BACKOFF_SECONDS", s.call_retry_backoff_seconds)
        s.http_accepted_status = _list("HTTP_ACCEPTED_STATUS", s.http_accepted_status)
        s.param_date_format = _env("PARAM_DATE_FORMAT", s.param_date_format)
        s.adhoc_allow_add_source_before_trigger = _bool(
            "ADHOC_ALLOW_ADD_SOURCE_BEFORE_TRIGGER", s.adhoc_allow_add_source_before_trigger)
        s.cycle_init_existing_batch = _env("CYCLE_INIT_EXISTING_BATCH", s.cycle_init_existing_batch).upper()
        s.rule_engine = _env("RULE_ENGINE", s.rule_engine)
        s.gre_entrypoint = _env("GRE_ENTRYPOINT")
        s.notify_backend = _env("NOTIFY_BACKEND", s.notify_backend)
        s.notify_from_email = _env("NOTIFY_FROM_EMAIL")
        s.default_notify_emails = _list("DEFAULT_NOTIFY_EMAILS", [])
        s.spark_jdbc_url = _env("SPARK_JDBC_URL")
        props = _env("SPARK_JDBC_PROPERTIES")
        s.spark_jdbc_properties = json.loads(props) if props else {}
        s.spark_write_partitions = _int("SPARK_WRITE_PARTITIONS", s.spark_write_partitions)
        s.spark_batch_size = _int("SPARK_BATCH_SIZE", s.spark_batch_size)
        return s
