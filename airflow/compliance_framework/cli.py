"""Command-line entry points (design §3)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime

from .app import App
from .audit import EventLogger
from .common import FrameworkError, parse_as_of
from .config import validate_all
from .db import held_locks, init_db, release_lock, schema_exists
from .modules import describe_modules, run_module
from .settings import Settings

log = logging.getLogger(__package__)


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
    p = argparse.ArgumentParser(prog="compliance_framework",
                                description="CMS compliance framework on Teradata (options go after the command)")
    sub = p.add_subparsers(dest="cmd", required=True)
    add = lambda name, help_: sub.add_parser(name, help=help_, parents=[common])  # noqa: E731

    def scope(sp, project_required=False):
        sp.add_argument("--project", required=project_required)
        sp.add_argument("--table")
        sp.add_argument("--run-type", required=project_required)

    add("init-db", "create the framework tables in the metadata database")
    add("list-modules", "list the modules that 'run --module' can call, with their parameters")
    sp = add("run", "run one module by name (see list-modules)")
    sp.add_argument("--module", required=True, help="module name (see list-modules); case-insensitive")
    scope(sp)
    sp.add_argument("--period", help="BATCH_CREATION: ROUTINE run types only - a built-in period name (or one in --period-file)")
    sp.add_argument("--period-file", help="BATCH_CREATION: project .py file defining PERIOD_SQL")
    sp.add_argument("--lookback-days", type=int, help="BATCH_CREATION")
    sp.add_argument("--lookback-weeks", type=int, help="BATCH_CREATION")
    sp.add_argument("--bucket", help="FILE_LOAD: inbound bucket")
    sp.add_argument("--key", help="FILE_LOAD: one object (with --bucket)")
    sp.add_argument("--prefix", help="FILE_LOAD: one location (with --bucket)")
    sp.add_argument("--version-id", help="FILE_LOAD: object version of --key")
    add("show-config", "print resolved settings (with their source) and the database target")
    add("test-connection", "connect to the database and check the schema")
    add("validate-config", "validate the configuration tables and target tables")
    add("process-decisions", "apply approved reuse overrides and remove expired ones")
    scope(add("close-batches", "close the batches past their SLA hold that have data or are in exception"))
    sp = add("close-batch", "close one batch past its SLA hold, with or without data (manual)")
    sp.add_argument("--btch-id", required=True)
    sp.add_argument("--closed-by", required=True)
    add("notify", "send pending notifications")
    add("locks", "list the locks currently held (a crashed run leaves its locks until they expire)")
    sp = add("release-lock", "remove a lock left behind by a run that no longer exists")
    sp.add_argument("--key", required=True, help="Lock_Key as printed by 'locks'")
    add("health", "print operational health report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        if args.cmd == "list-modules":
            _print(describe_modules())
            return 0
        settings = Settings.load(overrides=dict(args.settings), env_file=args.env_file)
        if args.cmd in ("init-db", "test-connection", "show-config"):
            return _no_app(args, settings)
        with App.from_settings(settings, parse_as_of(args.as_of, settings.business_tz)) as app:
            return _dispatch(app, args)
    except (FrameworkError, ValueError, LookupError) as e:
        log.error("%s: %s", type(e).__name__, e)
        return 2


def _no_app(args, settings: Settings) -> int:
    target = settings.db_target()
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
            with app.conn.transaction():
                EventLogger(app.conn, app.clock).audit(
                    "CONFIG_VALIDATION_FAILED", description="; ".join(f"{i.code}: {i.message}" for i in errors)[:4000])
        return 1 if errors else 0
    if c == "run":
        params = {k: getattr(args, k) for k in ("project", "table", "run_type", "period", "period_file",
                                                "lookback_days", "lookback_weeks", "bucket", "key", "prefix",
                                                "version_id")}
        out = run_module(app, args.module, params)
        _print({"module": out.module, "result": out.result})
        return out.exit_code
    if c == "process-decisions":
        s = app.decisions.run()
        _print(s)
        return 1 if s.invalid else 0
    if c == "close-batches":
        s = app.closer.run(args.project, args.table, args.run_type)
        _print(s)
        return 1 if s.deferred else 0
    if c == "close-batch":
        _print(app.closer.close(args.btch_id, args.closed_by))
        return 0
    if c == "notify":
        notifier = app.notifier()
        _print({"sent": notifier.run(), "failed": len(notifier.failed)})
        return 1 if notifier.failed else 0
    if c == "locks":
        _print(held_locks(app.conn))
        return 0
    if c == "release-lock":
        _print({"released": release_lock(app.conn, args.key)})
        return 0
    if c == "health":
        _print(app.health())
        return 0
    raise AssertionError(c)


if __name__ == "__main__":
    sys.exit(main())
