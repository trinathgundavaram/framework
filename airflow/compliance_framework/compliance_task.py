"""Airflow bridge: one Variable (configuration) + one Connection (Teradata) -> the framework."""
import logging
import os
import re

logger = logging.getLogger(__name__)

DEFAULT_VARIABLE_KEY = "compliance_framework_config"
DEFAULT_CONNECTION_ID = "compliance_teradata"
DEFAULT_TIMEZONE = "America/Chicago"
STEPS = ("BATCH_CREATION", "FILE_LOAD", "OVERRIDE_DECISIONS", "BATCH_CLOSE", "NOTIFY")
FAIL_ON_PROBLEMS = {"BATCH_CREATION": True, "FILE_LOAD": False, "OVERRIDE_DECISIONS": False,
                    "BATCH_CLOSE": True, "NOTIFY": True}
SCOPE_KEYS = ("project", "run_type", "period", "table", "period_file", "lookback_days", "lookback_weeks",
              "bucket", "key", "prefix", "version_id")
_GRE_ENV = {"environment": "GRE_ENVIRONMENT", "meta_db": "GRE_META_DB", "log_level": "GRE_LOG_LEVEL",
            "log_dir": "GRE_LOG_DIR", "max_parallel_rules": "GRE_MAX_PARALLEL_RULES",
            "package_dir": "GRE_PACKAGE_DIR"}
_RUN_NAME = re.compile(r"^[A-Za-z0-9_]{1,200}$")


def _set_env(key: str, value) -> None:
    if value is None or value == "":
        return
    os.environ[str(key)] = str(value)


def _load_teradata_connection(conn_id: str) -> None:
    from airflow.hooks.base import BaseHook

    conn = BaseHook.get_connection(conn_id)
    if not conn.host or not conn.login or not conn.password:
        raise ValueError(f"Airflow Connection '{conn_id}' needs host, login and password")
    _set_env("TERADATA_HOST", conn.host)
    _set_env("TERADATA_USER", conn.login)
    _set_env("TERADATA_PASSWORD", conn.password)
    extra = conn.extra_dejson or {}
    _set_env("TERADATA_LOGMECH", extra.get("logmech") or os.environ.get("TERADATA_LOGMECH") or "LDAP")
    logger.info("Loaded Teradata connection '%s': host=%s user=%s logmech=%s (password not logged)",
                conn_id, conn.host, conn.login, os.environ.get("TERADATA_LOGMECH"))


def _load_variable(variable_key: str) -> dict:
    from airflow.models import Variable

    config = Variable.get(variable_key, deserialize_json=True, default_var=None)
    if config is None:
        raise ValueError(f"Airflow Variable '{variable_key}' is not set")
    if not isinstance(config, dict):
        raise ValueError(f"Airflow Variable '{variable_key}' must be a JSON object, got {type(config).__name__}")
    return config


def _steps(value) -> list:
    steps = [value] if isinstance(value, str) else list(value or [])
    steps = [str(s).strip().upper() for s in steps]
    unknown = sorted(set(steps) - set(STEPS))
    if unknown:
        raise ValueError(f"unknown step(s) {unknown}; valid: {', '.join(STEPS)}")
    return steps


def scheduled_runs(variable_key: str = DEFAULT_VARIABLE_KEY) -> tuple:
    """(schedule timezone, {run name: run}) for the DAG file; never raises, so a bad Variable cannot break parsing."""
    try:
        config = _load_variable(variable_key)
    except Exception as e:  # noqa: BLE001
        logger.warning("no scheduled compliance DAGs: %s", e)
        return DEFAULT_TIMEZONE, {}
    out = {}
    for name, run in (config.get("runs") or {}).items():
        try:
            if not _RUN_NAME.match(str(name)) or not isinstance(run, dict):
                raise ValueError("the name must be letters, digits or _ and the value a JSON object")
            if run.get("schedule") and not _steps(run.get("steps")):
                raise ValueError("a scheduled run needs steps")
        except ValueError as e:
            logger.warning("compliance run %r ignored: %s", name, e)
            continue
        if run.get("schedule"):
            out[str(name)] = run
    return config.get("schedule_timezone") or DEFAULT_TIMEZONE, out


def resolve_run(variable_key: str = DEFAULT_VARIABLE_KEY, overrides: dict = None, run: str = None) -> tuple:
    """(configuration, framework settings): the Variable, then its named run, then the overrides."""
    config = _load_variable(variable_key)
    overrides = dict(overrides or {})
    run = overrides.pop("run", None) or run
    run_config = {}
    if run:
        runs = config.get("runs") or {}
        if run not in runs:
            raise ValueError(f"run '{run}' is not in Airflow Variable '{variable_key}' (runs: {', '.join(sorted(runs))})")
        run_config = dict(runs[run])
    settings = {**(config.get("settings") or {}), **(run_config.pop("settings", None) or {}),
                **(overrides.pop("settings", None) or {})}
    fail_on = {**FAIL_ON_PROBLEMS, **(config.get("fail_on_problems") or {}),
               **(run_config.pop("fail_on_problems", None) or {}), **(overrides.pop("fail_on_problems", None) or {})}
    config = {**{k: v for k, v in config.items() if k != "runs"}, **run_config, **overrides}
    if not config.get("meta_db"):
        raise ValueError(f"'meta_db' is required in Airflow Variable '{variable_key}'")
    config["run"] = run
    config["steps"] = _steps(config.get("steps"))
    config["fail_on_problems"] = fail_on
    settings = {str(k).upper(): ",".join(map(str, v)) if isinstance(v, (list, tuple)) else v
                for k, v in settings.items()}
    settings["METADATA_SCHEMA"] = config["meta_db"]
    return config, settings


def _connect_environment(config: dict, connection_id: str = None) -> None:
    _load_teradata_connection(connection_id or config.get("connection_id") or DEFAULT_CONNECTION_ID)
    gre = config.get("gre") or {}
    for key, env_key in _GRE_ENV.items():
        _set_env(env_key, gre.get(key))
    if gre:
        _set_env("GRE_META_CONNECTION", gre.get("meta_connection") or "teradata")


def run_compliance_step(step: str, variable_key: str = DEFAULT_VARIABLE_KEY, overrides: dict = None,
                        connection_id: str = None, run: str = None, **scope):
    """Run one framework step; raises RuntimeError when it fails, returns its outcome otherwise."""
    config, settings = resolve_run(variable_key, overrides, run)
    _connect_environment(config, connection_id)
    from .run_framework import run_step

    step = step.strip().upper()
    params = {k: config.get(k) for k in SCOPE_KEYS if config.get(k) not in (None, "")}
    params.update({k: v for k, v in scope.items() if v not in (None, "")})
    as_of = params.pop("as_of", None) or config.get("as_of")
    outcome, exit_code = run_step(step, as_of=as_of, settings=settings,
                                  log_level=config.get("log_level") or "INFO", **params)
    if exit_code == 2 or (exit_code == 1 and config["fail_on_problems"].get(step, True)):
        raise RuntimeError(f"compliance framework step {step} failed (exit_code={exit_code}). outcome={outcome!r}")
    if exit_code:
        logger.warning("step %s completed with problems (exit_code=%s): %r", step, exit_code, outcome)
    return outcome


def run_compliance_command(command: str, args: list = None, variable_key: str = DEFAULT_VARIABLE_KEY,
                           overrides: dict = None, connection_id: str = None):
    """Run one framework command (init-db, validate-config, health, locks, release-lock, close-batch, ...)."""
    config, settings = resolve_run(variable_key, overrides)
    _connect_environment(config, connection_id)
    from .run_framework import run_command

    outcome, exit_code = run_command(command, args=args, as_of=config.get("as_of"), settings=settings,
                                     log_level=config.get("log_level") or "INFO")
    logger.info("command %s exit_code=%s outcome=%r", command, exit_code, outcome)
    if exit_code != 0:
        raise RuntimeError(f"compliance framework command {command} failed (exit_code={exit_code}). "
                           f"outcome={outcome!r}")
    return outcome
