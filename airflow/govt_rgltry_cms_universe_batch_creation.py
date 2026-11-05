"""Government Regulatory batch framework - BATCH_CREATION. See airflow_resources/docs/AIRFLOW_DEPLOYMENT.md."""
import os
import sys

_DAG_DIR = os.path.dirname(os.path.abspath(__file__))
if _DAG_DIR not in sys.path:
    sys.path.insert(0, _DAG_DIR)

from govt_rgltry_framework.dags import build_step_dag  # noqa: E402

dag_id = "GOVT_RGLTRY_CMS_UNIVERSE_BATCH_CREATION"
variable_key = "govt_rgltry_cms_universe_batch_creation_config_var"

dag = build_step_dag(dag_id, "BATCH_CREATION", variable_key)
globals()[dag_id] = dag
