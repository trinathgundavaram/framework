"""Airflow bridge: the only Airflow-aware module of this package.

    1. Read the run's configuration from ONE Airflow Variable (JSON), with `overrides` (dag_run.conf) on top.
    2. Read the Teradata credentials from ONE Airflow Connection and export TERADATA_HOST / TERADATA_USER /
       TERADATA_PASSWORD / TERADATA_LOGMECH, which connection_factory.py reads.
    3. Call run_framework.run_step() / run_command() in-process.
    4. Raise on failure, return the outcome (XCom) on success.

Variable shape (every key optional except meta_db):

    {
      "connection_id": "compliance_teradata",      Airflow Connection with host / login / password
                                                     (extra: {"logmech": "LDAP"})
      "meta_db": "CMS_COMPLIANCE_PROD",             Teradata database holding the framework tables
      "log_level": "INFO",
      "settings": {                                  framework settings, see AIRFLOW_DEPLOYMENT.md
        "QUARANTINE_URI": "s3://bucket/quarantine/",
        "NOTIFY_BACKEND": "airflow",                 airflow | ses | log
        "NOTIFY_FROM_EMAIL": "noreply@example.com",  ses only
        "DEFAULT_NOTIFY_EMAILS": "ops@example.com",
        "RULE_ENGINE": "gre",
        "GRE_ENTRYPOINT": "compliance_framework.gre_bridge:run_gre"
      },
      "gre": {"environment": "PROD", "meta_db": "GRE_META_PROD", "package_dir": "/path/to/rules_engine"},
      "fail_on_problems": {"FILE_LOAD": false},      per step: does exit code 1 fail the task
      "project": "ODR", "run_type": "DAILY", "period": "PREV_DAY", "table": null, "as_of": null
    }

The run's scope (project, run_type, period, table, as_of, ...) normally comes from the DAG (see
compliance_framework_dag.py) or from a manual trigger's configuration, not from the Variable.
"""
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_VARIABLE_KEY = "compliance_framework_config"
DEFAULT_CONNECTION_ID = "compliance_teradata"

FAIL_ON_PROBLEMS = {"BATCH_CREATION": True, "FILE_LOAD": False, "OVERRIDE_DECISIONS": False,
                    "BATCH_CLOSE": True, "NOTIFY": True}
_SCOPE_KEYS = ("project", "run_type", "period", "table", "period_file", "lookback_days", "lookback_weeks",
               "bucket", "key", "prefix", "version_id")
_GRE_ENV = {"environment": "GRE_ENVIRONMENT", "meta_db": "GRE_META_DB", "log_level": "GRE_LOG_LEVEL",
            "log_dir": "GRE_LOG_DIR", "max_parallel_rules": "GRE_MAX_PARALLEL_RULES",
            "package_dir": "GRE_PACKAGE_DIR"}


def _set_env(key: str, value) -> None:
    """Set an env var only when the value is non-empty."""
    if value is None or value == "":
        return
    os.environ[str(key)] = str(value)


def _load_teradata_connection(conn_id: str) -> None:
    """Export TERADATA_* from an Airflow Connection (host / login / password, extra.logmech)."""
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


def _load_runtime_variable(variable_key: str) -> dict:
    from airflow.models import Variable

    config = Variable.get(variable_key, deserialize_json=True, default_var=None)
    if config is None:
        raise ValueError(f"Airflow Variable '{variable_key}' is not set")
    if not isinstance(config, dict):
        raise ValueError(f"Airflow Variable '{variable_key}' must be a JSON object, got {type(config).__name__}")
    return config


def _prepare(variable_key: str, overrides, connection_id) -> tuple:
    """(merged config, framework settings) with the connection and GRE environment exported."""
    config = _load_runtime_variable(variable_key)
    overrides = dict(overrides or {})
    settings = {**(config.get("settings") or {}), **(overrides.pop("settings", None) or {})}
    config = {**config, **overrides}
    if not config.get("meta_db"):
        raise ValueError(f"'meta_db' is required in Airflow Variable '{variable_key}'")
    settings = {str(k).upper(): ",".join(map(str, v)) if isinstance(v, (list, tuple)) else v
                for k, v in settings.items()}
    settings["METADATA_SCHEMA"] = config["meta_db"]
    _load_teradata_connection(connection_id or config.get("connection_id") or DEFAULT_CONNECTION_ID)
    gre = config.get("gre") or {}
    for key, env_key in _GRE_ENV.items():
        _set_env(env_key, gre.get(key))
    if gre:
        _set_env("GRE_META_CONNECTION", gre.get("meta_connection") or "teradata")
    return config, settings


def run_compliance_step(step: str, variable_key: str = DEFAULT_VARIABLE_KEY, overrides: dict = None,
                        connection_id: str = None, **scope):
    """Run one framework step; what an Airflow task calls.

    step : BATCH_CREATION | FILE_LOAD | OVERRIDE_DECISIONS | BATCH_CLOSE | NOTIFY
    overrides : merged over the Variable (typically dag_run.conf of a manual trigger)
    scope : project, run_type, period, table, as_of, ... passed in code; these win over both

    Returns the step's outcome. Raises RuntimeError on a framework error (exit code 2), or on
    "completed with problems" (exit code 1) when the step is configured to fail on problems.
    """
    config, settings = _prepare(variable_key, overrides, connection_id)
    from .run_framework import run_step

    step = step.strip().upper()
    params = {k: config.get(k) for k in _SCOPE_KEYS if config.get(k) not in (None, "")}
    params.update({k: v for k, v in scope.items() if v not in (None, "")})
    as_of = params.pop("as_of", None) or config.get("as_of")
    outcome, exit_code = run_step(step, as_of=as_of, settings=settings,
                                  log_level=config.get("log_level") or "INFO", **params)
    fail_on_problems = {**FAIL_ON_PROBLEMS, **(config.get("fail_on_problems") or {})}.get(step, True)
    if exit_code == 2 or (exit_code == 1 and fail_on_problems):
        raise RuntimeError(f"compliance framework step {step} failed (exit_code={exit_code}). outcome={outcome!r}")
    if exit_code:
        logger.warning("step %s completed with problems (exit_code=%s): %r", step, exit_code, outcome)
    return outcome


def run_compliance_command(command: str, args: list = None, variable_key: str = DEFAULT_VARIABLE_KEY,
                           overrides: dict = None, connection_id: str = None):
    """Run one framework command (init-db, validate-config, health, locks, release-lock, close-batch, ...)."""
    config, settings = _prepare(variable_key, overrides, connection_id)
    from .run_framework import run_command

    outcome, exit_code = run_command(command, args=args, as_of=config.get("as_of"), settings=settings,
                                     log_level=config.get("log_level") or "INFO")
    logger.info("command %s exit_code=%s outcome=%r", command, exit_code, outcome)
    if exit_code != 0:
        raise RuntimeError(f"compliance framework command {command} failed (exit_code={exit_code}). "
                           f"outcome={outcome!r}")
    return outcome
