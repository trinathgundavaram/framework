"""Airflow DAGs for the compliance batch framework on Teradata. See compliance_framework/AIRFLOW_DEPLOYMENT.md."""
import logging
import os
import sys
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.exceptions import AirflowSkipException
from airflow.models.param import Param
from airflow.operators.email import EmailOperator
from airflow.operators.python import PythonOperator

_DAG_DIR = os.path.dirname(os.path.abspath(__file__))
if _DAG_DIR not in sys.path:
    sys.path.insert(0, _DAG_DIR)

from compliance_framework.compliance_task import (DEFAULT_TIMEZONE, DEFAULT_VARIABLE_KEY, SCOPE_KEYS,  # noqa: E402
                                                  STEPS, load_dag_config, resolve_run,
                                                  run_compliance_command, run_compliance_step, scheduled_runs)

logger = logging.getLogger(__name__)

VARIABLE_KEY = os.environ.get("COMPLIANCE_VARIABLE_KEY", DEFAULT_VARIABLE_KEY)
STEP_TIMEOUT = {"BATCH_CREATION": timedelta(hours=1), "FILE_LOAD": timedelta(hours=2),
                "OVERRIDE_DECISIONS": timedelta(minutes=30), "BATCH_CLOSE": timedelta(minutes=30),
                "NOTIFY": timedelta(minutes=30)}
ADMIN_COMMANDS = ["validate-config", "health", "test-connection", "show-config", "locks",
                  "release-lock", "close-batch"]
RUN_PARAMS = ("project", "run_type", "period", "table", "as_of", "load_duplicate")
BATCH_DAG_ID = "COMPLIANCE_BATCH_FRAMEWORK"
ADMIN_DAG_ID = "COMPLIANCE_ADMIN"

dag_config = load_dag_config(VARIABLE_KEY)
RUNS = scheduled_runs(dag_config)
load_env = dag_config.get("load_env") or ""
email_recipient = dag_config.get("email_recipient")
owner = dag_config.get("owner") or "oss"
assignment_group = dag_config.get("snow_assignment_group") or "D&AE - EDE Govt Compliance"
base_tags = list(dag_config.get("tags") or ["Compliance", "Compliance_Batch_Framework"])

try:
    _START = pendulum.datetime(2026, 1, 1, tz=dag_config.get("schedule_timezone") or DEFAULT_TIMEZONE)
except Exception:  # noqa: BLE001
    _START = pendulum.datetime(2026, 1, 1, tz=DEFAULT_TIMEZONE)
_VARIABLE_PARAM = Param(VARIABLE_KEY, type="string", description="Airflow Variable (JSON) with the configuration")
_TEXT = ["null", "string"]


def failure_alert_ticket(context):
    try:
        from snow import snow_integrations
    except ImportError:
        logger.warning("snow integration is not installed; no ServiceNow incident for %s", context.get("task_instance"))
        return
    snow_integrations.create_incident(context, assignment_group)


default_args = {
    "owner": owner,
    "depends_on_past": False,
    "retries": 0,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": failure_alert_ticket,
}


def _inputs(context) -> tuple:
    params = context["params"]
    dag_run = context.get("dag_run")
    conf = dict(dag_run.conf or {}) if dag_run else {}
    variable_key = conf.pop("variable_key", None) or params["variable_key"]
    for name in (*RUN_PARAMS, "steps", "run"):
        if name not in conf and params.get(name) not in (None, "", []):
            conf[name] = params[name]
    return variable_key, conf


def _run_step(step: str, run: str = None, **context):
    variable_key, conf = _inputs(context)
    config, _ = resolve_run(variable_key, conf, run)
    if not config["steps"]:
        raise ValueError(f"no steps to run: give 'steps' ({', '.join(STEPS)}) or a 'run' that has them")
    if step not in config["steps"]:
        raise AirflowSkipException(f"{step} is not in this run's steps {config['steps']}")
    scope = {k: conf.pop(k) for k in (*SCOPE_KEYS, "as_of") if k in conf}
    return run_compliance_step(step, variable_key=variable_key, overrides=conf, run=run, **scope)


def _run_command(**context):
    variable_key, conf = _inputs(context)
    command = conf.pop("command", None) or context["params"]["command"]
    args = conf.pop("args", None) or context["params"].get("args") or []
    return run_compliance_command(command, args=list(args), variable_key=variable_key, overrides=conf)


def _add_steps(dag: DAG, steps, run: str = None, recipient=None) -> None:
    tasks = []
    for step in steps:
        task = PythonOperator(task_id=step.lower(), dag=dag, python_callable=_run_step,
                              op_kwargs={"step": step, "run": run}, trigger_rule="none_failed",
                              execution_timeout=STEP_TIMEOUT[step])
        if tasks:
            tasks[-1] >> task
        tasks.append(task)
    if not recipient:
        return
    success_email = EmailOperator(
        task_id="success_email", dag=dag, to=recipient, trigger_rule="none_failed_min_one_success",
        subject=f"Airflow EDEG-Compliance {load_env} Success: {dag.dag_id} DAG",
        html_content=f"DAG {dag.dag_id} successfully completed for date: {{{{ ts }}}}")
    fail_email = EmailOperator(
        task_id="fail_email", dag=dag, to=recipient, trigger_rule="one_failed",
        subject=f"Airflow EDEG-Compliance {load_env} Failure: {dag.dag_id} DAG",
        html_content=f"DAG {dag.dag_id} failed for date: {{{{ ts }}}}")
    tasks >> success_email
    tasks >> fail_email


for _name, _run in RUNS.items():
    _dag_id = str(_run.get("dag_id") or f"COMPLIANCE_{_name}".upper())
    _steps = [s for s in STEPS if s in [str(x).strip().upper() for x in _run["steps"]]]
    _dag = DAG(
        dag_id=_dag_id,
        description=f"compliance batch framework - {_run.get('project') or 'all projects'}: {' -> '.join(_steps)}",
        default_args=default_args,
        schedule=_run["schedule_interval"],
        start_date=_START,
        catchup=False,
        max_active_runs=1,
        dagrun_timeout=timedelta(hours=8),
        params={"variable_key": _VARIABLE_PARAM},
        tags=[*base_tags, *(_run.get("tags") or []), str(_run.get("project") or "All_Projects")],
    )
    _add_steps(_dag, _steps, _name, _run.get("email_recipient") or email_recipient)
    globals()[_dag_id] = _dag


with DAG(
    dag_id=BATCH_DAG_ID,
    description="compliance batch framework - every step in order, for a named run of the Variable or for "
                "the parameters given here",
    default_args=default_args,
    schedule=None,
    start_date=_START,
    catchup=False,
    max_active_runs=4,
    dagrun_timeout=timedelta(hours=8),
    params={
        "variable_key": _VARIABLE_PARAM,
        "run": Param(None, type=_TEXT, description="A run name of the Variable's 'runs'; its values are the defaults"),
        "project": Param(None, type=_TEXT, description="Project_Cd; empty = every project"),
        "steps": Param([], type="array", description=f"Any of {', '.join(STEPS)}; empty = the run's own steps"),
        "run_type": Param(None, type=_TEXT, description="BATCH_CREATION / BATCH_CLOSE"),
        "period": Param(None, type=_TEXT, description="BATCH_CREATION, e.g. PREV_DAY"),
        "table": Param(None, type=_TEXT),
        "as_of": Param(None, type=_TEXT, description="ISO date or timestamp used as 'now'"),
        "load_duplicate": Param(None, type=_TEXT, enum=[None, "yes", "no"],
                                description="FILE_LOAD: yes = load a file whose name was already loaded; no = reject it"),
    },
    tags=[*base_tags, "Manual"],
) as compliance_batch_framework:
    _add_steps(compliance_batch_framework, STEPS, recipient=email_recipient)


with DAG(
    dag_id=ADMIN_DAG_ID,
    description="compliance batch framework - validate-config, health, locks, release-lock, close-batch",
    default_args=default_args,
    schedule=None,
    start_date=_START,
    catchup=False,
    max_active_runs=1,
    params={
        "variable_key": _VARIABLE_PARAM,
        "command": Param("validate-config", type="string", enum=ADMIN_COMMANDS),
        "args": Param([], type="array", description='e.g. ["--btch-id", "<Btch_ID>", "--closed-by", "jdoe"]'),
    },
    tags=[*base_tags, "Admin"],
) as compliance_admin:
    PythonOperator(task_id="run_command", python_callable=_run_command, execution_timeout=timedelta(hours=1))
