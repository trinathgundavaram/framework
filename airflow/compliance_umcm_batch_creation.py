"""Compliance batch framework - BATCH_CREATION. See airflow_resources/docs/AIRFLOW_DEPLOYMENT.md."""
import os
import sys

_DAG_DIR = os.path.dirname(os.path.abspath(__file__))
if _DAG_DIR not in sys.path:
    sys.path.insert(0, _DAG_DIR)

from compliance_framework.dags import build_step_dag  # noqa: E402

dag_id = "COMPLIANCE_UMCM_BATCH_CREATION"
variable_key = "compliance_umcm_batch_creation_config_var"

dag = build_step_dag(dag_id, "BATCH_CREATION", variable_key)
globals()[dag_id] = dag
