"""Government Regulatory batch framework - FILE_MOVE. See airflow_resources/docs/AIRFLOW_DEPLOYMENT.md."""
import os
import sys

_DAG_DIR = os.path.dirname(os.path.abspath(__file__))
if _DAG_DIR not in sys.path:
    sys.path.insert(0, _DAG_DIR)

from govt_rgltry_framework.dags import build_file_move_dag  # noqa: E402

dag_id = "GOVT_RGLTRY_UMCM_FILE_MOVE"
variable_key = "govt_rgltry_umcm_file_move_config_var"

dag = build_file_move_dag(dag_id, variable_key)
globals()[dag_id] = dag
