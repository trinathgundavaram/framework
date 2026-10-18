"""Airflow: the DAGs of the steps, their tasks and Variables, and the in-process entry points."""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

import pendulum
from airflow.providers.smtp.operators.smtp import EmailOperator
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sdk import DAG, Connection, Param, Variable
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import MultipleCronTriggerTimetable
from croniter import croniter

from .app import App, report, resolve_module
from .cli import _json_default, main as cli_main
from .common import FrameworkError, parse_as_of
from . import db
from .db import Settings


# compliance_task: Airflow bridge: a step's Variable (configuration) + the Teradata and NAS Connections -> the framework.

logger = logging.getLogger(__name__)

DEFAULT_CONNECTION_ID = "compliance_teradata"
TD_CONN_KEY = "td_conn_var"
NAS_CONN_KEY = "wdc_comp_oper_nas_var"
DEFAULT_TIMEZONE = "America/Chicago"
STEPS = ("BATCH_CREATION", "FILE_CHECK", "FILE_LOAD", "SOURCE_LOAD", "EXPORT_LOAD", "FILE_RULES", "OVERRIDE_DECISIONS", "BATCH_CLOSE",
         "NOTIFY")
FAIL_ON_PROBLEMS = {"BATCH_CREATION": True, "FILE_CHECK": True, "FILE_LOAD": True, "SOURCE_LOAD": True, "EXPORT_LOAD": True, "FILE_RULES": True, "OVERRIDE_DECISIONS": False,
                    "BATCH_CLOSE": True, "NOTIFY": True}
SCOPE_KEYS = ("project", "table_group", "run_type", "period", "table", "period_file", "lookback_days", "lookback_weeks",
              "share", "file", "folder", "batch", "release", "hold")
_GRE_ENV = {"environment": "GRE_ENVIRONMENT", "meta_db": "GRE_META_DB", "log_level": "GRE_LOG_LEVEL",
            "log_dir": "GRE_LOG_DIR", "max_parallel_rules": "GRE_MAX_PARALLEL_RULES",
            "package_dir": "GRE_PACKAGE_DIR", "project_name": "GRE_PROJECT_NAME"}
_GRE_PARAMS = {"run_params": "GRE_RUN_PARAMS", "text_params": "GRE_TEXT_PARAMS", "extra_filters": "GRE_EXTRA_FILTERS"}
_MERGED = ("settings", "gre")
QUIET_LOGGERS = ("smbprotocol", "smbclient", "spnego")


def _set_env(key: str, value) -> None:
    if value is None or value == "":
        return
    os.environ[str(key)] = str(value)


def _load_teradata_connection(conn_id: str) -> None:
    conn = Connection.get(conn_id)
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
    conn = Connection.get(conn_id)
    if not conn.host or not conn.login or not conn.password:
        raise ValueError(f"Airflow Connection '{conn_id}' needs host, login and password")
    _set_env("NAS_HOST", conn.host)
    _set_env("NAS_USER", conn.login)
    _set_env("NAS_PASSWORD", conn.password)
    _set_env("NAS_PORT", conn.port)
    logger.info("Loaded NAS connection '%s': host=%s user=%s (password not logged)", conn_id, conn.host, conn.login)


def _keys(value: Optional[dict]) -> dict:
    """A Variable / trigger object with its keys in lower case (keys are not case sensitive)."""
    return {str(k).lower(): v for k, v in (value or {}).items()}


def conf_value(dag_run, params, key: str):
    """A trigger value by key in any case, else the form's parameter (passed on to the next DAG)."""
    value = _keys(getattr(dag_run, "conf", None)).get(key)
    return value if value not in (None, "", []) else (params or {}).get(key) or ""


def _load_variable(variable_key: str) -> dict:
    config = Variable.get(variable_key, default=None, deserialize_json=True)
    if config is None:
        raise ValueError(f"Airflow Variable '{variable_key}' is not set")
    if not isinstance(config, dict):
        raise ValueError(f"Airflow Variable '{variable_key}' must be a JSON object, got {type(config).__name__}")
    config = _keys(config)
    if isinstance(config.get("runs"), list):
        config["runs"] = [_keys(r) if isinstance(r, dict) else r for r in config["runs"]]
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


def timed_runs(config: dict) -> bool:
    """Whether the Variable's `runs` have their own schedule_interval: one DAG run per time, each with its entry."""
    try:
        return any(r.get("schedule_interval") for r in step_scopes(config))
    except ValueError:
        return False


def chosen_runs(variable: dict, dag_run) -> list:
    """The `runs` entries a DAG run of a timed DAG works with (None = the Variable's own scope).

    `run` in the trigger's configuration names them (comma separated); otherwise a scheduled run takes the entries
    whose schedule_interval is its scheduled time, and a manual or triggered run the Variable's own scope.
    """
    runs = step_scopes(variable)
    names = [n.strip().upper() for n in str(_keys(dag_run.conf).get("run") or "").split(",") if n.strip()]
    if names:
        by_name = {str(r.get("name") or "").upper(): i for i, r in enumerate(runs)}
        unknown = [n for n in names if n not in by_name]
        if unknown:
            raise ValueError(f"run {unknown[0]} is not a name in runs ({', '.join(n for n in by_name if n) or 'none'})")
        return [by_name[n] for n in names]
    if str(getattr(dag_run.run_type, "value", dag_run.run_type)) != "scheduled":
        return [None]
    when = (dag_run.run_after or dag_run.logical_date).astimezone(ZoneInfo(DEFAULT_TIMEZONE)).replace(tzinfo=None)
    picked = [i for i, r in enumerate(runs) if r.get("schedule_interval") and croniter.match(r["schedule_interval"], when)]
    own = variable.get("schedule_interval")
    if own and croniter.match(own, when):
        picked.append(None)
    if not picked:
        logger.warning("no runs entry is scheduled at %s; the Variable's own scope is used", when)
    return picked or [None]


def resolve_config(variable_key: str, overrides: dict = None, scope_index: int = None) -> tuple:
    """(configuration, framework settings): the Variable, then one entry of its `runs`, then the trigger's values."""
    variable = _load_variable(variable_key)
    scope = dict(step_scopes(variable)[scope_index]) if scope_index is not None else {}
    overrides = {k: v for k, v in _keys(overrides).items() if v not in (None, "", [])}
    layers = (variable, scope, overrides)
    merged = {key: {k: v for layer in layers for k, v in (layer.get(key) or {}).items()} for key in _MERGED}
    config = {k: v for layer in layers for k, v in layer.items() if k not in (*_MERGED, "runs")}
    config["gre"] = merged["gre"]
    if not config.get("meta_db"):
        raise ValueError(f"'meta_db' is required in Airflow Variable '{variable_key}'")
    settings = {str(k).upper(): ",".join(map(str, v)) if isinstance(v, (list, tuple)) else v
                for k, v in merged["settings"].items()}
    settings["METADATA_SCHEMA"] = config["meta_db"]
    environment = config.get("load_env") or config.get("environment") or (config.get("gre") or {}).get("environment")
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
    defaults = {"environment": config.get("load_env"), "meta_db": config.get("meta_db"),
                "log_level": config.get("log_level") or "INFO", "max_parallel_rules": 1, "project_name": config.get("project")}
    for key, env_key in _GRE_ENV.items():
        _set_env(env_key, gre.get(key) if gre.get(key) not in (None, "") else defaults.get(key))
    for key, env_key in _GRE_PARAMS.items():
        if gre.get(key) is not None and not isinstance(gre[key], dict):
            raise ValueError(f"gre.{key} must be a JSON object")
        os.environ[env_key] = json.dumps(gre.get(key) or {})
    _set_env("GRE_META_CONNECTION", gre.get("meta_connection") or "teradata")


TRIGGER_KEYS = ("settings", "gre", "log_level", "fail_on_problems")
TRIGGER_FILE_KEYS = ("share", "file", "folder")
TRIGGER_BLOCKED_SETTINGS = ("RULE_ENGINE", "GRE_ENTRYPOINT", "FILE_STORE", "LOCAL_STORE_ROOT", "METADATA_SCHEMA",
                            "ENVIRONMENT", "ENV_VALUE")
TRIGGER_GRE_KEYS = ("run_params", "text_params", "extra_filters")


def trigger_values(step: str, given: Optional[dict]) -> dict:
    """What a trigger may change for one run: the step's own inputs, settings and log level. The database, the
    connections, the file store, the rules engine and code to load (period_file, RULE_ENGINE) stay the Variable's."""
    allowed = {*STEP_PARAMS.get(step, ()), *TRIGGER_KEYS, *(TRIGGER_FILE_KEYS if step == "FILE_LOAD" else ())}
    out, ignored = {}, []
    for key, value in _keys(given).items():
        if key not in allowed:
            ignored.append(key)
            continue
        if key == "settings" and isinstance(value, dict):
            blocked = [k for k in value if str(k).upper().removeprefix("FRAMEWORK_") in TRIGGER_BLOCKED_SETTINGS]
            ignored += [f"settings.{k}" for k in blocked]
            value = {k: v for k, v in value.items() if k not in blocked}
        if key == "gre" and isinstance(value, dict):
            ignored += [f"gre.{k}" for k in value if str(k).lower() not in TRIGGER_GRE_KEYS]
            value = {k: v for k, v in value.items() if str(k).lower() in TRIGGER_GRE_KEYS}
        out[key] = value
    if ignored:
        logger.warning("%s: not taken from the trigger (set them in the Variable): %s", step, ", ".join(sorted(ignored)))
    return out


def run_compliance_step(step: str, variable_key: str, overrides: dict = None, scope_index: int = None,
                        connection_id: str = None):
    """Run one framework step. Problems the step reports (exit code 1, e.g. a rejected file) fail the task without a
    retry; a step that could not run (exit code 2) raises RuntimeError, which Airflow retries."""
    step = step.strip().upper()
    config, settings = resolve_config(variable_key, trigger_values(step, overrides), scope_index)
    if step in ("FILE_LOAD", "FILE_CHECK") and str(settings.get("FILE_STORE") or "nas").lower() == "nas" and not config.get(NAS_CONN_KEY):
        raise ValueError(f"'{NAS_CONN_KEY}' (the Airflow Connection of the NAS file server) is required in "
                         f"Airflow Variable '{variable_key}'")
    _connect_environment(config, connection_id)

    params = {k: config.get(k) for k in SCOPE_KEYS if config.get(k) not in (None, "")}
    timeout = int(_minutes(config, "execution_timeout_minutes", STEP_TIMEOUT.get(step, timedelta(hours=1))).total_seconds() // 60)
    try:
        ttl = int(settings.get("LOCK_TTL_MINUTES") or Settings.lock_ttl_minutes)
    except (TypeError, ValueError):
        ttl = None   # the step reports the bad setting
    if ttl is not None and ttl < timeout + 30:
        settings["LOCK_TTL_MINUTES"] = timeout + 30
        logger.info("LOCK_TTL_MINUTES raised from %s to %s (the task's time limit is %s minutes), so a run cannot "
                    "outlive its locks", ttl, timeout + 30, timeout)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    outcome, exit_code = run_step(step, as_of=config.get("as_of") or None, settings=settings,
                                  log_level=config.get("log_level") or "INFO", **params)
    fail_on_problems = config.get("fail_on_problems")
    if not isinstance(fail_on_problems, bool):
        fail_on_problems = FAIL_ON_PROBLEMS.get(step, True)
    text = json.dumps(outcome, indent=2, default=str)
    if exit_code == 2:
        logger.error("%s report:\n%s", step, text)
        raise RuntimeError(f"{step}: {outcome.get('result') or 'failed'} (exit code 2); the report is above")
    if exit_code == 1 and fail_on_problems:
        logger.error("%s report:\n%s", step, text)
        raise AirflowFailException(f"{step}: {outcome.get('result') or 'completed with problems'} (exit code 1); the "
                                   "report is above")
    (logger.warning if exit_code else logger.info)("%s report:\n%s", step, text)
    return outcome


# run_framework: Run one framework step or command in-process (no Airflow imports); returns (outcome, exit code).

log = logging.getLogger(__name__)

def _plain(value: Any) -> Any:
    """A JSON-serialisable copy (dataclasses, dates and sets flattened) - safe to return as an XCom."""
    return json.loads(json.dumps(value, default=_json_default))


def run_step(step: str, *, as_of: Optional[str] = None, settings: Optional[Mapping[str, Any]] = None,
             log_level: str = "INFO", **params) -> tuple[Optional[dict], int]:
    """Run one module for a project; context values the module does not take are ignored."""
    try:
        logging.getLogger(__package__).setLevel(str(log_level or "INFO").upper())
        spec = resolve_module(step)
        unknown = sorted(set(params) - set(SCOPE_KEYS))
        if unknown:
            raise FrameworkError(f"unknown run parameter(s): {', '.join(unknown)}")
        accepted = {k: v for k, v in params.items() if k in spec.params and v not in (None, "")}
        dropped = sorted(k for k, v in params.items() if k not in spec.params and v not in (None, ""))
        resolved = Settings.load(env=db.Env(), overrides={k: str(v) for k, v in (settings or {}).items()})
        log.info("%s starting | project %s | %s%s", spec.name, accepted.get("project", "all projects"),
                 ", ".join(f"{k}={v}" for k, v in accepted.items() if k != "project") or "no other parameters",
                 f" | not used by {spec.name}: {', '.join(dropped)}" if dropped else "")
        with App.from_settings(resolved, parse_as_of(as_of, resolved.business_tz)) as app:
            out = app.run_module(spec.name, accepted)
    except (FrameworkError, ValueError, LookupError) as e:
        log.error("%s: %s", type(e).__name__, e)
        return {"step": step, "result": "COULD NOT RUN", "problem": f"{type(e).__name__}: {e}"}, 2
    except Exception as e:  # noqa: BLE001 - any other failure still ends with the step report
        log.exception("%s failed", step)
        return {"step": step, "result": "COULD NOT RUN", "problem": db.error_text(e)}, 2
    return _plain(report(out)), out.exit_code


if __name__ == "__main__":
    import sys

    sys.exit(cli_main())


# dag_factory: Builds the compliance DAGs: one DAG per framework step, each with its own Airflow Variable.

STEP_TIMEOUT = {"BATCH_CREATION": timedelta(hours=1), "FILE_CHECK": timedelta(hours=1), "FILE_LOAD": timedelta(hours=2), "SOURCE_LOAD": timedelta(hours=2), "EXPORT_LOAD": timedelta(hours=2), "FILE_RULES": timedelta(hours=2),
                "OVERRIDE_DECISIONS": timedelta(minutes=30), "BATCH_CLOSE": timedelta(minutes=30),
                "NOTIFY": timedelta(minutes=30)}
SCOPE = ("project", "table_group", "table", "run_type")
STEP_PARAMS = {
    "BATCH_CREATION": (*SCOPE, "period", "lookback_days", "lookback_weeks", "as_of"),
    "FILE_CHECK": (*SCOPE, "as_of"),
    "FILE_LOAD": (*SCOPE, "as_of"),
    "SOURCE_LOAD": (*SCOPE, "batch", "release", "hold", "as_of"),
    "EXPORT_LOAD": (*SCOPE, "batch", "release", "hold", "as_of"),
    "FILE_RULES": (*SCOPE, "as_of"),
    "OVERRIDE_DECISIONS": (*SCOPE, "as_of"),
    "BATCH_CLOSE": (*SCOPE, "hold", "as_of"),
    "NOTIFY": (*SCOPE, "as_of"),
}
PASSED_ON = (*SCOPE, "as_of")
DEFAULT_ASSIGNMENT_GROUP = "D&AE - EDE Govt Compliance"
DEFAULT_RETRIES = 1
DEFAULT_TAGS = ["Compliance", "Compliance_Batch_Framework"]
_TEXT = ["null", "string"]


def _failure_alert_ticket(assignment_group: str):
    def failure_alert_ticket(context):
        try:
            from snow import snow_integrations
        except ImportError:
            logger.warning("snow integration is not installed; no ServiceNow incident for %s",
                           context.get("task_instance"))
            return
        snow_integrations.create_incident(context, assignment_group)
    return failure_alert_ticket


def _schedule(dag_config: dict, tz: str):
    """The DAG's schedule: its schedule_interval, or with timed runs every time of the entries (and its own)."""
    if not timed_runs(dag_config):
        return dag_config.get("schedule_interval")
    crons = [c for c in [dag_config.get("schedule_interval"), *(r.get("schedule_interval") for r in dag_config["runs"])] if c]
    return MultipleCronTriggerTimetable(*dict.fromkeys(crons), timezone=tz)


def _dag(dag_id: str, dag_config: dict, description: str, params: dict, tag: str) -> DAG:
    tz = dag_config.get("schedule_timezone") or DEFAULT_TIMEZONE
    try:
        start_date = pendulum.datetime(2026, 1, 1, tz=tz)
    except Exception:  # noqa: BLE001
        tz = DEFAULT_TIMEZONE
        start_date = pendulum.datetime(2026, 1, 1, tz=tz)
    default_args = {
        "owner": dag_config.get("owner") or "oss",
        "depends_on_past": False,
        "start_date": start_date,
        "retries": _retries(dag_config),
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": _failure_alert_ticket(dag_config.get("snow_assignment_group")
                                                     or DEFAULT_ASSIGNMENT_GROUP),
    }
    return DAG(dag_id=dag_id, description=description, default_args=default_args,
               schedule=_schedule(dag_config, tz), catchup=False, max_active_runs=1,
               dagrun_timeout=_minutes(dag_config, "dagrun_timeout_minutes", timedelta(hours=8)), params=params,
               user_defined_macros={"conf_value": conf_value}, tags=[*(dag_config.get("tags") or DEFAULT_TAGS), tag])


def _overrides(context, names) -> dict:
    """What the trigger gave (its configuration, else the form's parameters); empty values are left out."""
    conf = _keys(context["dag_run"].conf) if context.get("dag_run") else {}
    for name in names:
        if name not in conf and context["params"].get(name) not in (None, "", []):
            conf[name] = context["params"][name]
    return {k: v for k, v in conf.items() if v not in (None, "", [])}


def _add_emails(dag: DAG, dag_config: dict, upstream: list) -> None:
    email_recipient = dag_config.get("email_recipient")
    if not email_recipient:
        return
    load_env = dag_config.get("load_env") or ""
    success_email = EmailOperator(
        task_id="success_email", dag=dag, to=email_recipient, trigger_rule="none_failed_min_one_success",
        subject=f"Airflow EDEG-Compliance {load_env} Success: {dag.dag_id} DAG",
        html_content=f"DAG {dag.dag_id} run {{{{ run_id }}}} completed successfully")
    fail_email = EmailOperator(
        task_id="fail_email", dag=dag, to=email_recipient, trigger_rule="one_failed",
        subject=f"Airflow EDEG-Compliance {load_env} Failure: {dag.dag_id} DAG",
        html_content=f"DAG {dag.dag_id} run {{{{ run_id }}}} failed")
    upstream >> success_email
    upstream >> fail_email


def _task_id(step: str, scope: dict, index: int, scopes: list) -> str:
    if len(scopes) == 1:
        return step.lower()
    name = "_".join(str(scope[k]) for k in ("project", "table_group", "table", "run_type") if scope.get(k)) or str(index + 1)
    return f"{step.lower()}_{''.join(c if c.isalnum() else '_' for c in name).lower()}"


def _retries(dag_config: dict) -> int:
    """How often a failed task is tried again (Variable key `retries`, default 1; 0 = never)."""
    value = dag_config.get("retries")
    try:
        return max(0, int(value)) if value not in (None, "") else DEFAULT_RETRIES
    except (TypeError, ValueError):
        logger.warning("retries must be a whole number; using %s", DEFAULT_RETRIES)
        return DEFAULT_RETRIES


def _minutes(dag_config: dict, key: str, default: timedelta) -> timedelta:
    """A time limit from the Variable in minutes (`execution_timeout_minutes`, `dagrun_timeout_minutes`), else the default."""
    try:
        value = int(dag_config.get(key) or 0)
    except (TypeError, ValueError):
        logger.warning("%s must be a whole number of minutes; using %s", key, default)
        return default
    return timedelta(minutes=value) if value > 0 else default


def build_step_dag(dag_id: str, step: str, variable_key: str) -> DAG:
    """The DAG of one framework step; everything about it comes from its own Variable."""
    step = step.strip().upper()
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; valid: {', '.join(STEPS)}")
    dag_config = load_dag_config(variable_key)
    timed = timed_runs(dag_config)
    names = (*STEP_PARAMS[step], "run") if timed else STEP_PARAMS[step]
    params = {name: Param(None, type=_TEXT) for name in names}
    dag = _dag(dag_id, dag_config, f"compliance batch framework - {step}", params, step.title())

    def run_step(scope_index: int, **context):
        return run_compliance_step(step, variable_key, overrides=_overrides(context, names), scope_index=scope_index)

    def run_timed(**context):
        """A timed DAG run: the step once for each entry it works with (see chosen_runs)."""
        overrides = {k: v for k, v in _overrides(context, names).items() if k != "run"}
        outcomes, failed = [], []
        for index in chosen_runs(_load_variable(variable_key), context["dag_run"]):
            entry = "the Variable's own scope" if index is None else f"runs entry {index + 1}"
            logger.info("%s with %s", step, entry)
            try:
                outcomes.append(run_compliance_step(step, variable_key, overrides=overrides, scope_index=index))
            except Exception as e:  # noqa: BLE001
                logger.error("%s with %s failed: %s", step, entry, e)
                failed.append(f"{entry}: {e}")
        if failed:
            raise RuntimeError(f"{step}: {len(failed)} of {len(failed) + len(outcomes)} run(s) failed: {'; '.join(failed)}")
        return outcomes

    try:
        scopes = step_scopes(dag_config)
    except ValueError as e:
        logger.warning("%s: %s", dag_id, e)
        scopes = [{}]
    timeout = _minutes(dag_config, "execution_timeout_minutes", STEP_TIMEOUT[step])
    if timed:
        tasks = [PythonOperator(task_id=step.lower(), dag=dag, python_callable=run_timed, execution_timeout=timeout,
                                show_return_value_in_logs=False)]
    else:
        tasks = [PythonOperator(task_id=_task_id(step, scope, i, scopes), dag=dag, python_callable=run_step,
                                op_kwargs={"scope_index": i if dag_config.get("runs") else None},
                                execution_timeout=timeout, show_return_value_in_logs=False)
                 for i, scope in enumerate(scopes)]
    given = "{{ conf_value(dag_run, params, '%s') }}"
    triggers = [TriggerDagRunOperator(task_id=f"trigger_{str(target).lower()}", dag=dag, trigger_dag_id=str(target),
                                      trigger_rule="all_success", conf={k: given % k for k in PASSED_ON})
                for target in dag_config.get("trigger_dags") or []]
    for trigger in triggers:
        tasks >> trigger
    _add_emails(dag, dag_config, [*tasks, *triggers])
    return dag
