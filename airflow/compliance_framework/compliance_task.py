"""Airflow bridge: a step's Variable (configuration) + the Teradata and NAS Connections -> the framework."""
import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_CONNECTION_ID = "compliance_teradata"
TD_CONN_KEY = "td_conn_var"
NAS_CONN_KEY = "wdc_comp_oper_nas_var"
DEFAULT_TIMEZONE = "America/Chicago"
STEPS = ("BATCH_CREATION", "FILE_CHECK", "FILE_LOAD", "FILE_RULES", "OVERRIDE_DECISIONS", "BATCH_CLOSE", "NOTIFY")
FAIL_ON_PROBLEMS = {"BATCH_CREATION": True, "FILE_CHECK": True, "FILE_LOAD": False, "FILE_RULES": True, "OVERRIDE_DECISIONS": False,
                    "BATCH_CLOSE": True, "NOTIFY": True}
SCOPE_KEYS = ("project", "run_type", "period", "table", "period_file", "lookback_days", "lookback_weeks",
              "share", "file", "folder")
_GRE_ENV = {"environment": "GRE_ENVIRONMENT", "meta_db": "GRE_META_DB", "log_level": "GRE_LOG_LEVEL",
            "log_dir": "GRE_LOG_DIR", "max_parallel_rules": "GRE_MAX_PARALLEL_RULES",
            "package_dir": "GRE_PACKAGE_DIR", "project_name": "GRE_PROJECT_NAME"}
_GRE_PARAMS = {"run_params": "GRE_RUN_PARAMS", "text_params": "GRE_TEXT_PARAMS", "extra_filters": "GRE_EXTRA_FILTERS"}
_MERGED = ("settings", "gre")


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


def _load_nas_connection(conn_id: str) -> None:
    from airflow.hooks.base import BaseHook

    conn = BaseHook.get_connection(conn_id)
    if not conn.host or not conn.login or not conn.password:
        raise ValueError(f"Airflow Connection '{conn_id}' needs host, login and password")
    _set_env("NAS_HOST", conn.host)
    _set_env("NAS_USER", conn.login)
    _set_env("NAS_PASSWORD", conn.password)
    _set_env("NAS_PORT", conn.port)
    logger.info("Loaded NAS connection '%s': host=%s user=%s (password not logged)", conn_id, conn.host, conn.login)


def _load_variable(variable_key: str) -> dict:
    from airflow.models import Variable

    config = Variable.get(variable_key, deserialize_json=True, default_var=None)
    if config is None:
        raise ValueError(f"Airflow Variable '{variable_key}' is not set")
    if not isinstance(config, dict):
        raise ValueError(f"Airflow Variable '{variable_key}' must be a JSON object, got {type(config).__name__}")
    return config


def load_dag_config(variable_key: str) -> dict:
    """The Variable for the DAG file ({} when missing or invalid, so a bad Variable cannot break parsing)."""
    try:
        return _load_variable(variable_key)
    except Exception as e:  # noqa: BLE001
        logger.warning("compliance DAG configuration: %s", e)
        return {}


def step_scopes(config: dict) -> list:
    """The Variable's `runs` (one task each: project / run type / period ...), or one scope: the Variable itself."""
    runs = config.get("runs")
    if not runs:
        return [{}]
    if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs):
        raise ValueError("'runs' must be a list of JSON objects")
    return runs


def resolve_config(variable_key: str, overrides: dict = None, scope_index: int = None) -> tuple:
    """(configuration, framework settings): the Variable, then one entry of its `runs`, then the trigger's values."""
    variable = _load_variable(variable_key)
    scope = dict(step_scopes(variable)[scope_index]) if scope_index is not None else {}
    overrides = {k: v for k, v in (overrides or {}).items() if v not in (None, "", [])}
    layers = (variable, scope, overrides)
    merged = {key: {k: v for layer in layers for k, v in (layer.get(key) or {}).items()} for key in _MERGED}
    config = {k: v for layer in layers for k, v in layer.items() if k not in (*_MERGED, "runs")}
    config["gre"] = merged["gre"]
    if not config.get("meta_db"):
        raise ValueError(f"'meta_db' is required in Airflow Variable '{variable_key}'")
    settings = {str(k).upper(): ",".join(map(str, v)) if isinstance(v, (list, tuple)) else v
                for k, v in merged["settings"].items()}
    settings["METADATA_SCHEMA"] = config["meta_db"]
    if config.get("load_duplicate") not in (None, ""):
        settings["LOAD_DUPLICATE"] = config["load_duplicate"]
    environment = config.get("load_env") or config.get("environment") or config["gre"].get("environment")
    if environment:
        settings.setdefault("ENVIRONMENT", environment)
    return config, settings


def _connect_environment(config: dict, connection_id: str = None) -> None:
    gre = config.get("gre") or {}
    _load_teradata_connection(connection_id or config.get(TD_CONN_KEY) or config.get("connection_id")
                              or gre.get("connection_id") or DEFAULT_CONNECTION_ID)
    if config.get(NAS_CONN_KEY):
        _load_nas_connection(config[NAS_CONN_KEY])
    if gre and str(gre.get("connection_type") or "teradata").lower() != "teradata":
        raise ValueError("gre.connection_type must be 'teradata': the rules run on the framework's Teradata connection")
    for key, env_key in _GRE_ENV.items():
        _set_env(env_key, gre.get(key))
    for key, env_key in _GRE_PARAMS.items():
        if gre.get(key) is not None and not isinstance(gre[key], dict):
            raise ValueError(f"gre.{key} must be a JSON object")
        os.environ[env_key] = json.dumps(gre.get(key) or {})
    if gre:
        _set_env("GRE_META_CONNECTION", gre.get("meta_connection") or "teradata")


def run_compliance_step(step: str, variable_key: str, overrides: dict = None, scope_index: int = None,
                        connection_id: str = None):
    """Run one framework step; raises RuntimeError when it fails, returns its outcome otherwise."""
    step = step.strip().upper()
    config, settings = resolve_config(variable_key, overrides, scope_index)
    if step in ("FILE_LOAD", "FILE_CHECK") and str(settings.get("FILE_STORE") or "nas").lower() == "nas" and not config.get(NAS_CONN_KEY):
        raise ValueError(f"'{NAS_CONN_KEY}' (the Airflow Connection of the NAS file server) is required in "
                         f"Airflow Variable '{variable_key}'")
    _connect_environment(config, connection_id)
    from .run_framework import run_step

    params = {k: config.get(k) for k in SCOPE_KEYS if config.get(k) not in (None, "")}
    outcome, exit_code = run_step(step, as_of=config.get("as_of") or None, settings=settings,
                                  log_level=config.get("log_level") or "INFO", **params)
    fail_on_problems = config.get("fail_on_problems")
    if not isinstance(fail_on_problems, bool):
        fail_on_problems = FAIL_ON_PROBLEMS.get(step, True)
    if exit_code == 2 or (exit_code == 1 and fail_on_problems):
        raise RuntimeError(f"compliance framework step {step} failed (exit_code={exit_code}). outcome={outcome!r}")
    if exit_code:
        logger.warning("step %s completed with problems (exit_code=%s): %r", step, exit_code, outcome)
    return outcome


def run_compliance_command(command: str, variable_key: str, args: list = None, overrides: dict = None,
                           connection_id: str = None):
    """Run one framework command (validate-config, health, locks, release-lock, close-batch, ...)."""
    config, settings = resolve_config(variable_key, overrides)
    _connect_environment(config, connection_id)
    from .run_framework import run_command

    outcome, exit_code = run_command(command, args=args, as_of=config.get("as_of") or None, settings=settings,
                                     log_level=config.get("log_level") or "INFO")
    logger.info("command %s exit_code=%s outcome=%r", command, exit_code, outcome)
    if exit_code != 0:
        raise RuntimeError(f"compliance framework command {command} failed (exit_code={exit_code}). "
                           f"outcome={outcome!r}")
    return outcome
