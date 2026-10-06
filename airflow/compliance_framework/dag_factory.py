"""Builds the compliance DAGs: one DAG per framework step, each with its own Airflow Variable."""
import logging
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.models.param import Param
from airflow.operators.email import EmailOperator
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from .compliance_task import (DEFAULT_TIMEZONE, STEPS, load_dag_config, run_compliance_command,
                              run_compliance_step, step_scopes)

logger = logging.getLogger(__name__)

STEP_TIMEOUT = {"BATCH_CREATION": timedelta(hours=1), "FILE_CHECK": timedelta(hours=1), "FILE_LOAD": timedelta(hours=2), "FILE_RULES": timedelta(hours=2),
                "OVERRIDE_DECISIONS": timedelta(minutes=30), "BATCH_CLOSE": timedelta(minutes=30),
                "NOTIFY": timedelta(minutes=30)}
STEP_PARAMS = {
    "BATCH_CREATION": ("project", "run_type", "period", "table", "lookback_days", "lookback_weeks", "as_of"),
    "FILE_CHECK": ("project", "load_duplicate", "as_of"),
    "FILE_LOAD": ("project", "load_duplicate", "as_of"),
    "FILE_RULES": ("project", "as_of"),
    "OVERRIDE_DECISIONS": ("project", "as_of"),
    "BATCH_CLOSE": ("project", "table", "run_type", "as_of"),
    "NOTIFY": ("project", "as_of"),
}
PASSED_ON = ("project", "as_of")
ADMIN_COMMANDS = ["validate-config", "health", "test-connection", "show-config", "locks",
                  "release-lock", "close-batch"]
DEFAULT_ASSIGNMENT_GROUP = "D&AE - EDE Govt Compliance"
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


def _dag(dag_id: str, dag_config: dict, description: str, params: dict, tag: str) -> DAG:
    try:
        start_date = pendulum.datetime(2026, 1, 1, tz=dag_config.get("schedule_timezone") or DEFAULT_TIMEZONE)
    except Exception:  # noqa: BLE001
        start_date = pendulum.datetime(2026, 1, 1, tz=DEFAULT_TIMEZONE)
    default_args = {
        "owner": dag_config.get("owner") or "oss",
        "depends_on_past": False,
        "start_date": start_date,
        "retries": 0,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": _failure_alert_ticket(dag_config.get("snow_assignment_group")
                                                     or DEFAULT_ASSIGNMENT_GROUP),
    }
    return DAG(dag_id=dag_id, description=description, default_args=default_args,
               schedule=dag_config.get("schedule_interval"), catchup=False, max_active_runs=1,
               dagrun_timeout=timedelta(hours=8), params=params,
               tags=[*(dag_config.get("tags") or DEFAULT_TAGS), tag])


def _overrides(context, names) -> dict:
    """What the trigger gave (its configuration, else the form's parameters); empty values are left out."""
    conf = dict(context["dag_run"].conf or {}) if context.get("dag_run") else {}
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
        html_content=f"DAG {dag.dag_id} successfully completed for date: {{{{ ts }}}}")
    fail_email = EmailOperator(
        task_id="fail_email", dag=dag, to=email_recipient, trigger_rule="one_failed",
        subject=f"Airflow EDEG-Compliance {load_env} Failure: {dag.dag_id} DAG",
        html_content=f"DAG {dag.dag_id} failed for date: {{{{ ts }}}}")
    upstream >> success_email
    upstream >> fail_email


def _task_id(step: str, scope: dict, index: int, scopes: list) -> str:
    if len(scopes) == 1:
        return step.lower()
    name = "_".join(str(scope[k]) for k in ("project", "table", "run_type") if scope.get(k)) or str(index + 1)
    return f"{step.lower()}_{''.join(c if c.isalnum() else '_' for c in name).lower()}"


def build_step_dag(dag_id: str, step: str, variable_key: str) -> DAG:
    """The DAG of one framework step; everything about it comes from its own Variable."""
    step = step.strip().upper()
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; valid: {', '.join(STEPS)}")
    dag_config = load_dag_config(variable_key)
    names = STEP_PARAMS[step]
    params = {name: Param(None, type=_TEXT) for name in names}
    dag = _dag(dag_id, dag_config, f"compliance batch framework - {step}", params, step.title())

    def run_step(scope_index: int, **context):
        return run_compliance_step(step, variable_key, overrides=_overrides(context, names), scope_index=scope_index)

    try:
        scopes = step_scopes(dag_config)
    except ValueError as e:
        logger.warning("%s: %s", dag_id, e)
        scopes = [{}]
    tasks = [PythonOperator(task_id=_task_id(step, scope, i, scopes), dag=dag, python_callable=run_step,
                            op_kwargs={"scope_index": i if dag_config.get("runs") else None},
                            execution_timeout=STEP_TIMEOUT[step])
             for i, scope in enumerate(scopes)]
    given = "{{ (dag_run.conf or {}).get('%s') or params.get('%s') or '' }}"
    triggers = [TriggerDagRunOperator(task_id=f"trigger_{str(target).lower()}", dag=dag, trigger_dag_id=str(target),
                                      trigger_rule="all_success", conf={k: given % (k, k) for k in PASSED_ON})
                for target in dag_config.get("trigger_dags") or []]
    for trigger in triggers:
        tasks >> trigger
    _add_emails(dag, dag_config, [*tasks, *triggers])
    return dag


def build_module_dag(dag_id: str, variable_key: str) -> DAG:
    """The generic DAG: runs whichever step its `module` parameter names, with the parameters given."""
    dag_config = load_dag_config(variable_key)
    names = sorted({name for step_names in STEP_PARAMS.values() for name in step_names})
    params = {"module": Param(dag_config.get("module"), type=_TEXT, description="The step to run: " + ", ".join(STEPS)),
              **{name: Param(None, type=_TEXT) for name in names}}
    dag = _dag(dag_id, dag_config, "compliance batch framework - any step, named by the 'module' parameter",
               params, "Run_Module")

    def run_module(**context):
        conf = _overrides(context, ("module", *names))
        module = str(conf.pop("module", "") or "").strip().upper()
        if module not in STEPS:
            raise ValueError(f"'module' must be one of {', '.join(STEPS)}; got {module or 'nothing'!r}")
        unused = sorted(k for k in conf if k in names and k not in STEP_PARAMS[module])
        if unused:
            raise ValueError(f"{module} does not take: {', '.join(unused)}; it takes: {', '.join(STEP_PARAMS[module])}")
        return run_compliance_step(module, variable_key, overrides=conf)

    task = PythonOperator(task_id="run_module", dag=dag, python_callable=run_module,
                          execution_timeout=max(STEP_TIMEOUT.values()))
    _add_emails(dag, dag_config, [task])
    return dag


def build_admin_dag(dag_id: str, variable_key: str) -> DAG:
    """validate-config, health, locks, release-lock, close-batch: run by hand."""
    dag_config = {**load_dag_config(variable_key), "schedule_interval": None}
    params = {"command": Param("validate-config", type="string", description=", ".join(ADMIN_COMMANDS)),
              "args": Param([], type="array", description='e.g. ["--btch-id", "<Btch_ID>", "--closed-by", "jdoe"]')}
    dag = _dag(dag_id, dag_config, "compliance batch framework - validate-config, health, locks, release-lock, "
                                   "close-batch", params, "Admin")

    def run_command(**context):
        conf = _overrides(context, ("command", "args"))
        command, args = str(conf.pop("command")).strip().lower(), conf.pop("args", [])
        return run_compliance_command(command, variable_key, args=list(args), overrides=conf)

    PythonOperator(task_id="run_command", dag=dag, python_callable=run_command, execution_timeout=timedelta(hours=1))
    return dag
