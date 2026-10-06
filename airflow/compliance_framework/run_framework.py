"""Run one framework step or command in-process (no Airflow imports); returns (outcome, exit code)."""
from __future__ import annotations

import contextlib
import io
import json
import logging
from typing import Any, Mapping, Optional

from .app import App
from .cli import _json_default
from .cli import main as cli_main
from .common import FrameworkError, parse_as_of
from .modules import resolve_module
from .settings import Settings

log = logging.getLogger(__name__)

STEPS = ("BATCH_CREATION", "FILE_CHECK", "FILE_LOAD", "FILE_RULES", "OVERRIDE_DECISIONS", "BATCH_CLOSE", "NOTIFY")
STEP_PARAMS = ("project", "run_type", "period", "table", "period_file", "lookback_days", "lookback_weeks",
               "share", "file", "folder")
COMMANDS = ("test-connection", "show-config", "validate-config", "health", "locks", "release-lock",
            "close-batch", "close-batches", "process-decisions", "notify", "list-modules")


def _plain(value: Any) -> Any:
    """A JSON-serialisable copy (dataclasses, dates and sets flattened) - safe to return as an XCom."""
    return json.loads(json.dumps(value, default=_json_default))


def run_step(step: str, *, as_of: Optional[str] = None, settings: Optional[Mapping[str, Any]] = None,
             log_level: str = "INFO", **params) -> tuple[Optional[dict], int]:
    """Run one module for a project; context values the module does not take are ignored."""
    logging.getLogger(__package__).setLevel(log_level.upper())
    try:
        spec = resolve_module(step)
        unknown = sorted(set(params) - set(STEP_PARAMS))
        if unknown:
            raise FrameworkError(f"unknown run parameter(s): {', '.join(unknown)}")
        accepted = {k: v for k, v in params.items() if k in spec.params and v not in (None, "")}
        dropped = sorted(k for k, v in params.items() if k not in spec.params and v not in (None, ""))
        resolved = Settings.load(overrides={k: str(v) for k, v in (settings or {}).items()})
        log.info("step=%s project=%s params=%s%s", spec.name, accepted.get("project", "*"), accepted,
                 f" (not used by {spec.name}: {', '.join(dropped)})" if dropped else "")
        with App.from_settings(resolved, parse_as_of(as_of, resolved.business_tz)) as app:
            out = app.run_module(spec.name, accepted)
    except (FrameworkError, ValueError, LookupError) as e:
        log.error("%s: %s", type(e).__name__, e)
        return {"module": step, "error": f"{type(e).__name__}: {e}"}, 2
    return {"module": out.module, "result": _plain(out.result)}, out.exit_code


def run_command(command: str, *, args: Optional[list] = None, as_of: Optional[str] = None,
                settings: Optional[Mapping[str, Any]] = None, log_level: str = "INFO") -> tuple[Any, int]:
    """Run one CLI command (validate-config, health, close-batch, locks, release-lock, ...)."""
    command = command.strip().lower()
    if command not in COMMANDS:
        raise ValueError(f"unknown command {command!r}; one of {', '.join(COMMANDS)}")
    argv = [command, "--log-level", log_level]
    for name, value in (settings or {}).items():
        argv += ["--set", f"{name}={value}"]
    if as_of:
        argv += ["--as-of", str(as_of)]
    argv += [str(a) for a in (args or [])]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli_main(argv)
    text = buf.getvalue().strip()
    try:
        return json.loads(text) if text else None, code
    except ValueError:
        return text, code


if __name__ == "__main__":
    import sys

    sys.exit(cli_main())
