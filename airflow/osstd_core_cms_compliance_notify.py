"""Compliance batch framework - NOTIFY. See compliance_framework/AIRFLOW_DEPLOYMENT.md."""
import os
import sys

_DAG_DIR = os.path.dirname(os.path.abspath(__file__))
if _DAG_DIR not in sys.path:
    sys.path.insert(0, _DAG_DIR)

from compliance_framework.dag_factory import build_step_dag  # noqa: E402

dag_id = "OSSTD_CORE_CMS_COMPLIANCE_NOTIFY"
variable_key = "compliance_notify_config_var"

dag = build_step_dag(dag_id, "NOTIFY", variable_key)
globals()[dag_id] = dag
