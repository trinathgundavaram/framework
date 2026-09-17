"""Command-line entry points (design §3, Phase 1). Each command calls one service and sets the exit code.

Examples:
  framework init-db
  framework show-config
  framework test-connections
  framework validate-config
  framework create-batches --as-of 2026-02-01T11:00:00Z
  framework ingest-file --bucket inbound --key prja/in/PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt
  framework evaluate-extracts
  framework trigger-extract --extract-id 12 --requested-by jdoe --ack-warnings
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime

from .app import App
from .batches.scheduler import run_catchup, run_scheduler
from .clock import parse_as_of
from .config.validator import validate_all
from .connections import ConnectionManager, ConnectionResolver, open_connection
from .db import init_db, load_metadata_settings, schema_exists
from .errors import FrameworkError
from . import health
from .settings import Settings, read_config_file

log = logging.getLogger("framework")


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    if is_dataclass(o):
        return asdict(o)
    return str(o)


def _print(obj) -> None:
    print(json.dumps(asdict(obj) if is_dataclass(obj) else obj, default=_json_default, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="framework", description="CMS compliance framework")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_, as_of=False):
        sp = sub.add_parser(name, help=help_)
        if as_of:
            sp.add_argument("--as-of", help="ISO-8601 timestamp used as 'now' (default: current time)")
        return sp

    sp = add("init-db", "apply schema DDL and seed data")
    sp.add_argument("--no-provisional-status", action="store_true",
                    help="do not load the provisional Req_Stat values (Q-01)")
    add("show-config", "print resolved settings and connections (with their source; no passwords)")
    add("test-connections", "connect to the metadata database and every registered data connection")
    add("validate-config", "validate configuration tables")
    add("create-batches", "create batches for cron fires in the scheduler window", True)
    add("catchup", "create batches for missed cron fires within the lookback", True)
    add("process-intake", "process NEW intake requests", True)
    sp = add("ingest-file", "process one inbound object", True)
    sp.add_argument("--bucket", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--version-id")
    add("process-decisions", "act on manual approvals/rejections/waivers", True)
    add("evaluate-extracts", "refresh extracts past their SLA hold and auto-trigger eligible ones", True)
    sp = add("refresh-extract", "recount / combine / evaluate one extract", True)
    sp.add_argument("--extract-id", type=int, required=True)
    sp = add("trigger-extract", "manually trigger an extract", True)
    sp.add_argument("--extract-id", type=int, required=True)
    sp.add_argument("--requested-by", required=True)
    sp.add_argument("--ack-warnings", action="store_true")
    sp = add("resolve-trigger", "resolve an in-flight trigger whose outcome is unknown", True)
    sp.add_argument("--trigger-id", type=int, required=True)
    sp.add_argument("--outcome", choices=["accepted", "failed"], required=True)
    sp.add_argument("--actor", required=True)
    sp.add_argument("--job-run-ref")
    add("notify", "send pending notifications", True)
    add("health", "print operational health report", True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = Settings.load()
        if args.cmd == "init-db":
            resolver = ConnectionResolver(settings, read_config_file(settings.config_file))
            spec = resolver.metadata_spec()
            with open_connection(spec) as conn:
                applied = init_db(conn, settings.metadata_schema,
                                  include_provisional_status=not args.no_provisional_status)
            _print({"database": spec.describe(), "schema": settings.metadata_schema, "applied": applied})
            return 0
        if args.cmd == "test-connections":
            return _test_connections(settings)
        clock = parse_as_of(getattr(args, "as_of", None))
        with App.from_settings(settings, clock) as app:
            return _dispatch(app, args)
    except (FrameworkError, ValueError, FileNotFoundError) as e:
        log.error("%s: %s", type(e).__name__, e)
        return 2


def _test_connections(settings: Settings) -> int:
    """Each connection is reported independently so one bad connection does not hide the others."""
    results = []
    resolver = ConnectionResolver(settings, read_config_file(settings.config_file))
    try:
        spec = resolver.metadata_spec()
        meta = open_connection(spec)
    except FrameworkError as e:
        _print([{"connection": "METADATA", "ok": False, "error": str(e)}])
        return 1
    with meta:
        ok = schema_exists(meta, settings.metadata_schema)
        results.append({"connection": "METADATA", "target": spec.describe(), "sources": spec.sources, "ok": ok,
                        "error": None if ok else f"schema {settings.metadata_schema} not initialised (run init-db)"})
        if ok:
            try:
                settings.apply_metadata(load_metadata_settings(meta))
            except ValueError as e:
                results.append({"connection": "METADATA", "ok": False, "error": f"ComplianceFrameworkSetting: {e}"})
            names = [r["connection_nm"] for r in meta.execute(
                """SELECT Connection_Nm FROM ComplianceDbConnection WHERE Active_Ind = 1
                   UNION SELECT DISTINCT Target_Connection_Nm FROM ComplianceSourceFileConfig
                          WHERE Active_Ind = 1 AND Target_Connection_Nm IS NOT NULL ORDER BY 1""").fetchall()]
            conns = ConnectionManager(resolver, meta)
            for n in names:
                try:
                    s = conns.spec(n)
                    with open_connection(s) as c:
                        c.execute("SELECT 1")
                    results.append({"connection": n, "target": s.describe(), "sources": s.sources, "ok": True})
                except FrameworkError as e:
                    results.append({"connection": n, "ok": False, "error": str(e)})
    _print(results)
    return 0 if all(r["ok"] for r in results) else 1


def _dispatch(app: App, args) -> int:
    c = args.cmd
    if c == "show-config":
        _print({"settings": {n: {"value": getattr(app.settings, n),
                                 "source": app.settings.sources.get(n, "default")} for n in Settings.names()},
                "metadata_database": app.conns.spec(None).describe(),
                "metadata_database_sources": app.conns.spec(None).sources})
        return 0
    if c == "validate-config":
        issues = validate_all(app.conn, app.settings.filename_case_sensitive, app.conns)
        _print([asdict(i) for i in issues])
        errors = [i for i in issues if i.severity == "ERROR"]
        if errors:
            with app.conn.transaction():
                from .audit.event_logger import EventLogger
                EventLogger(app.conn, app.clock).audit(
                    "CONFIG_VALIDATION_FAILED", description="; ".join(f"{i.code}: {i.message}" for i in errors)[:4000])
        return 1 if errors else 0
    if c == "create-batches":
        s = run_scheduler(app.conn, app.clock, app.settings)
        _print(s)
        return 1 if s.errors else 0
    if c == "catchup":
        s = run_catchup(app.conn, app.clock, app.settings)
        _print(s)
        return 1 if s.errors else 0
    if c == "process-intake":
        _print(app.intake.run())
        return 0
    if c == "ingest-file":
        _print(app.pipeline.process_file(args.bucket, args.key, args.version_id))
        return 0
    if c == "process-decisions":
        s = app.decisions.run()
        _print(s)
        return 1 if s.promotion_failed or s.invalid else 0
    if c == "evaluate-extracts":
        s = app.evaluator.run()
        _print(s)
        return 1 if s.failed or s.reconcile_required else 0
    if c == "refresh-extract":
        st = app.control.refresh(args.extract_id, "MANUAL_REFRESH")
        _print({"extract": st.extract, "eligibility": st.eligibility})
        return 0
    if c == "trigger-extract":
        _print(app.trigger.fire(args.extract_id, "MANUAL", args.requested_by, args.ack_warnings))
        return 0
    if c == "resolve-trigger":
        app.trigger.resolve(args.trigger_id, args.outcome == "accepted", args.actor, args.job_run_ref)
        _print({"trigger_id": args.trigger_id, "resolved": args.outcome})
        return 0
    if c == "notify":
        _print({"sent": app.notifier().run()})
        return 0
    if c == "health":
        _print(health.report(app.conn, app.clock, app.settings))
        return 0
    raise AssertionError(c)


if __name__ == "__main__":
    sys.exit(main())
