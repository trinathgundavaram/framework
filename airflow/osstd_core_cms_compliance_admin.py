"""Compliance batch framework - ADMIN. See compliance_framework/AIRFLOW_DEPLOYMENT.md."""
import os
import sys

_DAG_DIR = os.path.dirname(os.path.abspath(__file__))
if _DAG_DIR not in sys.path:
    sys.path.insert(0, _DAG_DIR)

from compliance_framework.dag_factory import build_admin_dag  # noqa: E402

dag_id = "OSSTD_CORE_CMS_COMPLIANCE_ADMIN"
variable_key = "compliance_admin_config_var"

dag = build_admin_dag(dag_id, variable_key)
globals()[dag_id] = dag
