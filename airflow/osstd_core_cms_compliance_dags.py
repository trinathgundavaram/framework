"""Compliance batch framework - one Airflow DAG per step. See airflow_resources/docs/AIRFLOW_DEPLOYMENT.md.

Each DAG is <DAG_PREFIX>_<STEP> and reads the Airflow Variable <VARIABLE_PREFIX>_<step>_config_var. Another project:
copy this file, give the copy its own DAG_PREFIX and VARIABLE_PREFIX, and create its Variables.
"""
import os
import sys

_DAG_DIR = os.path.dirname(os.path.abspath(__file__))
if _DAG_DIR not in sys.path:
    sys.path.insert(0, _DAG_DIR)

from compliance_framework.dags import build_step_dag  # noqa: E402

DAG_PREFIX = "OSSTD_CORE_CMS_COMPLIANCE"
VARIABLE_PREFIX = "compliance"
STEPS = ("BATCH_CREATION", "FILE_CHECK", "FILE_LOAD", "SOURCE_LOAD", "EXPORT_LOAD", "OVERRIDE_DECISIONS",
         "BATCH_CLOSE", "NOTIFY")

for _step in STEPS:
    _dag_id = f"{DAG_PREFIX}_{_step}"
    globals()[_dag_id] = build_step_dag(_dag_id, _step, f"{VARIABLE_PREFIX}_{_step.lower()}_config_var")
