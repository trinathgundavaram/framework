"""Compliance batch framework - SOURCE_LOAD. See airflow_resources/docs/AIRFLOW_DEPLOYMENT.md."""
import os
import sys

_DAG_DIR = os.path.dirname(os.path.abspath(__file__))
if _DAG_DIR not in sys.path:
    sys.path.insert(0, _DAG_DIR)

from compliance_framework.dags import build_step_dag  # noqa: E402

dag_id = "OSSTD_CORE_CMS_COMPLIANCE_SOURCE_LOAD"
variable_key = "compliance_source_load_config_var"

dag = build_step_dag(dag_id, "SOURCE_LOAD", variable_key)
globals()[dag_id] = dag
