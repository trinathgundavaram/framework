"""
compliance_framework_dag.py
---------------------------
Airflow DAGs for the compliance batch framework on Teradata. The only file outside compliance_framework/
(Airflow needs a DAG file to discover); the bridge to the framework is
compliance_framework/compliance_task.py.

Setup in Airflow
    Connection  compliance_teradata   host / login / password of the Teradata system
                                      (extra, optional: {"logmech": "LDAP"})
    Variable    compliance_framework_config  (JSON)
                {"meta_db": "CMS_COMPLIANCE_PROD",
                 "settings": {"QUARANTINE_URI": "s3://bucket/quarantine/", "NOTIFY_BACKEND": "airflow",
                              "DEFAULT_NOTIFY_EMAILS": "ops@example.com"}}
    See compliance_framework/AIRFLOW_DEPLOYMENT.md for every key.

DAGs
    SCHEDULES below: one DAG per entry, running its steps in order for one project (or for every project
    when project is None). Schedule times are in SCHEDULE_TZ and follow daylight saving.
    compliance_manual_run: any steps for any project, by hand ("Trigger DAG w/ config").
    compliance_admin: init-db, validate-config, health, locks, release-lock, close-batch.

A manual trigger's configuration overrides the DAG's own values and the Variable, e.g. a missed run:
    {"as_of": "2026-09-01"}        or        {"variable_key": "compliance_framework_config_qa"}
"""
import os
import sys
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.exceptions import AirflowSkipException
from airflow.models.param import Param
from airflow.operators.python import PythonOperator

_DAG_DIR = os.path.dirname(os.path.abspath(__file__))
if _DAG_DIR not in sys.path:
    sys.path.insert(0, _DAG_DIR)

from compliance_framework.compliance_task import (DEFAULT_VARIABLE_KEY, run_compliance_command,  # noqa: E402
                                                  run_compliance_step)

SCHEDULE_TZ = "America/Chicago"
STEPS = ("BATCH_CREATION", "FILE_LOAD", "OVERRIDE_DECISIONS", "BATCH_CLOSE", "NOTIFY")
STEP_TIMEOUT = {"BATCH_CREATION": timedelta(hours=1), "FILE_LOAD": timedelta(hours=2),
                "OVERRIDE_DECISIONS": timedelta(minutes=30), "BATCH_CLOSE": timedelta(minutes=30),
                "NOTIFY": timedelta(minutes=30)}
ADMIN_COMMANDS = ["validate-config", "health", "init-db", "test-connection", "show-config", "locks",
                  "release-lock", "close-batch"]

# project: a Project_Cd of ComplianceProject, or None to run the steps for every project.
# run_type / period apply to BATCH_CREATION (and run_type to BATCH_CLOSE); alert_emails get failure emails.
SCHEDULES = [
    {"dag_id": "compliance_odr_daily_batches", "schedule": "0 6 * * *", "project": "ODR",
     "steps": ["BATCH_CREATION"], "run_type": "DAILY", "period": "PREV_DAY", "alert_emails": []},
    {"dag_id": "compliance_odr_file_load", "schedule": "*/15 * * * *", "project": "ODR",
     "steps": ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"], "alert_emails": []},
    {"dag_id": "compliance_odr_close", "schedule": "0 * * * *", "project": "ODR",
     "steps": ["BATCH_CLOSE", "NOTIFY"], "alert_emails": []},
    {"dag_id": "compliance_universe_daily_batches", "schedule": "0 6 * * *", "project": "UNIVERSE",
     "steps": ["BATCH_CREATION"], "run_type": "CMS", "period": "CURRENT_CALENDAR_MONTH", "alert_emails": []},
    {"dag_id": "compliance_universe_file_load", "schedule": "5-59/15 * * * *", "project": "UNIVERSE",
     "steps": ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"], "alert_emails": []},
    {"dag_id": "compliance_universe_close", "schedule": "30 23 * * *", "project": "UNIVERSE",
     "steps": ["BATCH_CLOSE", "NOTIFY"], "alert_emails": []},
    {"dag_id": "compliance_all_projects_notify", "schedule": "0 * * * *", "project": None,
     "steps": ["NOTIFY"], "alert_emails": []},
]

_VARIABLE_PARAM = Param(DEFAULT_VARIABLE_KEY, type="string",
                        description="Airflow Variable (JSON) holding the connection and framework settings.")
_START = pendulum.datetime(2026, 1, 1, tz=SCHEDULE_TZ)
_SCOPE = ("project", "run_type", "period", "table", "as_of", "period_file", "lookback_days", "lookback_weeks")


def _trigger_conf(context) -> dict:
    dag_run = context.get("dag_run")
    return dict(dag_run.conf or {}) if dag_run else {}


def _default_args(alert_emails) -> dict:
    args = {"owner": "data-engineering", "retries": 1, "retry_delay": timedelta(minutes=5)}
    if alert_emails:
        args.update(email=list(alert_emails), email_on_failure=True, email_on_retry=False)
    return args


def _run_step(step: str, defaults: dict, **context):
    """Run one step with the DAG's scope; the trigger configuration and DAG params override it."""
    params, conf = context["params"], _trigger_conf(context)
    variable_key = conf.pop("variable_key", None) or params["variable_key"]
    steps = conf.pop("steps", None) or params.get("steps") or defaults.get("steps") or []
    steps = [s.strip().upper() for s in ([steps] if isinstance(steps, str) else steps)]
    unknown = sorted(set(steps) - set(STEPS))
    if unknown:
        raise ValueError(f"unknown step(s) {unknown}; valid: {', '.join(STEPS)}")
    if step not in steps:
        raise AirflowSkipException(f"{step} is not in this run's steps {steps}")
    scope = {k: defaults.get(k) for k in _SCOPE}
    scope.update({k: params[k] for k in _SCOPE if params.get(k) not in (None, "")})
    scope.update({k: conf.pop(k) for k in _SCOPE if k in conf})
    return run_compliance_step(step, variable_key=variable_key, overrides=conf, **scope)


def _add_steps(dag: DAG, steps, defaults: dict) -> None:
    previous = None
    for step in steps:
        task = PythonOperator(
            task_id=step.lower(), dag=dag, python_callable=_run_step,
            op_kwargs={"step": step, "defaults": defaults},
            trigger_rule="none_failed", execution_timeout=STEP_TIMEOUT[step])
        if previous is not None:
            previous >> task
        previous = task


for _spec in SCHEDULES:
    _dag = DAG(
        dag_id=_spec["dag_id"],
        description=f"compliance batch framework - {_spec['project'] or 'all projects'}: "
                    f"{' -> '.join(_spec['steps'])}",
        default_args=_default_args(_spec.get("alert_emails")),
        schedule=_spec["schedule"],
        start_date=_START,
        catchup=False,
        max_active_runs=1,
        dagrun_timeout=timedelta(hours=8),
        params={"variable_key": _VARIABLE_PARAM},
        tags=["compliance", "compliance-batch-framework", (_spec["project"] or "all-projects").lower()],
    )
    _add_steps(_dag, _spec["steps"], dict(_spec))
    globals()[_spec["dag_id"]] = _dag


with DAG(
    dag_id="compliance_manual_run",
    description="compliance batch framework - run chosen steps for one project by hand",
    default_args=_default_args(None),
    schedule=None,
    start_date=_START,
    catchup=False,
    max_active_runs=4,
    dagrun_timeout=timedelta(hours=8),
    params={
        "variable_key": _VARIABLE_PARAM,
        "project": Param(None, type=["null", "string"], description="Project_Cd; empty = every project"),
        "steps": Param(["FILE_LOAD"], type="array", description=f"Any of {', '.join(STEPS)}; they run in that order"),
        "run_type": Param(None, type=["null", "string"], description="BATCH_CREATION / BATCH_CLOSE"),
        "period": Param(None, type=["null", "string"], description="BATCH_CREATION, e.g. PREV_DAY"),
        "table": Param(None, type=["null", "string"]),
        "as_of": Param(None, type=["null", "string"], description="ISO date or timestamp used as 'now'"),
    },
    tags=["compliance", "compliance-batch-framework", "manual"],
) as compliance_manual_run:
    _add_steps(compliance_manual_run, STEPS, {})


def _run_command(**context):
    params, conf = context["params"], _trigger_conf(context)
    variable_key = conf.pop("variable_key", None) or params["variable_key"]
    command = conf.pop("command", None) or params["command"]
    args = conf.pop("args", None) or params.get("args") or []
    return run_compliance_command(command, args=list(args), variable_key=variable_key, overrides=conf)


with DAG(
    dag_id="compliance_admin",
    description="compliance batch framework - one-off commands (init-db, validate-config, health, locks, ...)",
    default_args={"owner": "data-engineering", "retries": 0},
    schedule=None,
    start_date=_START,
    catchup=False,
    max_active_runs=1,
    params={
        "variable_key": _VARIABLE_PARAM,
        "command": Param("validate-config", type="string", enum=ADMIN_COMMANDS),
        "args": Param([], type="array",
                      description='Command arguments, e.g. ["--btch-id", "<Btch_ID>", "--closed-by", "jdoe"] '
                                  'for close-batch or ["--key", "<Lock_Key>"] for release-lock'),
    },
    tags=["compliance", "compliance-batch-framework", "admin"],
) as compliance_admin:
    PythonOperator(task_id="run_command", python_callable=_run_command, execution_timeout=timedelta(hours=1))
