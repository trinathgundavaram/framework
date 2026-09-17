"""Command-line entry points (design §3). Each command calls one service and sets the exit code.

Any setting can be passed as a job argument with --set NAME=VALUE (see settings.py), so one Glue job
definition per project carries its own period and gating configuration. The framework closes a run
when its data is complete; the project's job chain generates the extract afterwards (D-76).

Examples:
  framework init-db
  framework show-config
  framework test-connection
  framework validate-config
  framework create-batches --project PRJA --run-type MONTHLY --period PREV_CALENDAR_MONTH --as-of 2026-02-01
  framework ingest-file --bucket inbound --key prja/in/PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt
  framework process-intake
  framework evaluate-extracts --project PRJA --set EXTRACT_GATING_MODE=BEST_EFFORT
  framework close-extract --extract-id 12 --closed-by jdoe --ack-warnings
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime

from .app import App
from .common import FrameworkError, parse_as_of
from .config import validate_all
from .db import init_db, schema_exists
from .settings import Settings

log = logging.getLogger("framework")


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    return asdict(o) if is_dataclass(o) else str(o)


def _print(obj) -> None:
    print(json.dumps(asdict(obj) if is_dataclass(obj) else obj, default=_json_default, indent=2))


def _setting(text: str) -> tuple[str, str]:
    name, sep, value = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"expected NAME=VALUE, got {text!r}")
    return name.strip(), value


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log-level", default="INFO")
    common.add_argument("--env-file", help="settings file (default FRAMEWORK_ENV_FILE or ./.env)")
    common.add_argument("--set", dest="settings", action="append", type=_setting, default=[], metavar="NAME=VALUE",
                        help="override a setting for this run (repeatable)")
    common.add_argument("--as-of", help="ISO-8601 date/timestamp used as 'now' (default: current time)")
    p = argparse.ArgumentParser(prog="framework", description="CMS compliance framework (options go after the command)")
    sub = p.add_subparsers(dest="cmd", required=True)
    add = lambda name, help_: sub.add_parser(name, help=help_, parents=[common])  # noqa: E731

    def scope(sp, project_required=False):
        sp.add_argument("--project", required=project_required)
        sp.add_argument("--table")
        sp.add_argument("--run-type", required=project_required)

    add("init-db", "apply schema and seed data")
    add("show-config", "print resolved settings (with their source) and the database target")
    add("test-connection", "connect to the database and check the schema")
    add("validate-config", "validate the configuration tables and target tables")
    sp = add("create-batches", "create the batches of one project/run type for the period of the run date")
    scope(sp, project_required=True)
    sp.add_argument("--period", required=True, help="name in period_sql.py (or in --period-file)")
    sp.add_argument("--period-file", help="project .py file defining PERIOD_SQL")
    sp.add_argument("--lookback-days", type=int)
    sp.add_argument("--lookback-weeks", type=int)
    add("process-intake", "process NEW intake requests")
    sp = add("ingest-file", "process one inbound object")
    sp.add_argument("--bucket", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--version-id")
    add("process-decisions", "apply approved reuse overrides and remove expired ones")
    scope(add("evaluate-extracts", "refresh extracts past their SLA hold and close the eligible ones"))
    sp = add("refresh-extract", "recount / combine / evaluate one extract")
    sp.add_argument("--extract-id", type=int, required=True)
    sp = add("close-extract", "close one extract and its batches (manual)")
    sp.add_argument("--extract-id", type=int, required=True)
    sp.add_argument("--closed-by", required=True)
    sp.add_argument("--ack-warnings", action="store_true")
    add("notify", "send pending notifications")
    add("health", "print operational health report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = Settings.load(overrides=dict(args.settings), env_file=args.env_file)
        if args.cmd in ("init-db", "test-connection", "show-config"):
            return _no_app(args, settings)
        with App.from_settings(settings, parse_as_of(args.as_of)) as app:
            return _dispatch(app, args)
    except (FrameworkError, ValueError, LookupError) as e:
        log.error("%s: %s", type(e).__name__, e)
        return 2


def _no_app(args, settings: Settings) -> int:
    _, target = settings.db_conninfo()
    if args.cmd == "show-config":
        _print({"database": target, "settings": settings.describe()})
        return 0
    with settings.connect() as conn:
        if args.cmd == "init-db":
            _print({"database": target, "applied": init_db(conn, settings.metadata_schema)})
            return 0
        ok = schema_exists(conn, settings.metadata_schema)
        _print({"database": target, "ok": ok, "error": None if ok else "schema not initialised (run init-db)"})
        return 0 if ok else 1


def _dispatch(app: App, args) -> int:
    c = args.cmd
    if c == "validate-config":
        issues = validate_all(app.conn, app.settings.filename_case_sensitive)
        _print([asdict(i) for i in issues])
        errors = [i for i in issues if i.severity == "ERROR"]
        if errors:
            from .audit import EventLogger
            with app.conn.transaction():
                EventLogger(app.conn, app.clock).audit(
                    "CONFIG_VALIDATION_FAILED", description="; ".join(f"{i.code}: {i.message}" for i in errors)[:4000])
        return 1 if errors else 0
    if c == "create-batches":
        s = app.create_batches(project_cd=args.project, table_nm=args.table, run_ty=args.run_type,
                               period=args.period, period_file=args.period_file,
                               lookback_days=args.lookback_days, lookback_weeks=args.lookback_weeks)
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
        return 1 if s.invalid else 0
    if c == "evaluate-extracts":
        s = app.evaluator.run(args.project, args.table, args.run_type)
        _print(s)
        return 1 if s.deferred or s.regenerate_required else 0
    if c == "refresh-extract":
        st = app.control.refresh(args.extract_id, "MANUAL_REFRESH")
        _print({"extract": st.extract, "eligibility": st.eligibility})
        return 0
    if c == "close-extract":
        _print(app.control.close(args.extract_id, args.closed_by, args.ack_warnings))
        return 0
    if c == "notify":
        _print({"sent": app.notifier().run()})
        return 0
    if c == "health":
        _print(app.health())
        return 0
    raise AssertionError(c)


if __name__ == "__main__":
    sys.exit(main())
