"""File rules through the GRE rules engine (the rules_engine Airflow package).

Set in the framework settings:  RULE_ENGINE=gre, GRE_ENTRYPOINT=compliance_framework.gre_bridge:run_gre

For each rule binding of a file the framework calls run_gre(); it runs that GRE rule_group
(and rule_variant) with the file's batch as run parameters, then reads the verdict of every rule from
gre_results. Rule SQL can use {btch_id}, {load_id}, {stg_schema_nm}, {stg_table_nm}, {project_cd},
{table_nm}, {src_id}, {run_ty}, {rpt_start_dt_key}, {rpt_end_dt_key}, {req_dt_key}.

Environment: GRE_META_DB (where gre_results lives), GRE_PACKAGE_DIR (folder holding run_rules.py; default:
the rules_engine folder next to this package), plus what the GRE itself reads (GRE_ENVIRONMENT, TERADATA_*).
"""
import logging
import os
import sys
from datetime import date, datetime

from .db import ident

logger = logging.getLogger(__name__)

PASSING = ("PASS", "WARN")


def _run_rules():
    package_dir = os.environ.get("GRE_PACKAGE_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules_engine")
    if not os.path.isfile(os.path.join(package_dir, "run_rules.py")):
        raise RuntimeError(f"GRE run_rules.py not found in {package_dir}; set GRE_PACKAGE_DIR")
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)
    from run_rules import run_rules

    return run_rules


def _text(value) -> str:
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


def run_gre(conn, rule_group: str, rule_variant: str, run_params: dict) -> list:
    """One GRE rule_group for one staged file -> [{"rule_ref", "passed"}]; raises on a technical failure."""
    meta_db = os.environ.get("GRE_META_DB")
    if not meta_db:
        raise RuntimeError("GRE_META_DB is not set")
    run_key = f"CBF_LOAD_{run_params['load_id']}"
    params = {k: _text(v) for k, v in run_params.items() if v is not None}
    variant = None if rule_variant in (None, "", "*") else rule_variant
    outcome, exit_code = _run_rules()(rule_group=rule_group, rule_variant=variant, run_key=run_key,
                                      run_params=params, log_level=os.environ.get("GRE_LOG_LEVEL") or "ERROR")
    summary = (outcome or {}).get("rule_groups", {}).get(rule_group)
    if summary is None:
        raise RuntimeError(f"GRE returned nothing for rule_group {rule_group} (exit_code={exit_code})")
    if summary["status"] == "NO_RULES":
        logger.warning("GRE rule_group %s (variant %s) has no active rules", rule_group, variant)
        return []
    if summary["status"] != "COMPLETED" or summary.get("errored"):
        raise RuntimeError(f"GRE rule_group {rule_group} did not complete: status={summary['status']} "
                           f"errored={summary.get('errored')}")
    rows = conn.execute(f"SELECT rule_id, status FROM {ident(meta_db)}.gre_results WHERE run_id = %s",
                        (summary["run_id"],)).fetchall()
    if not rows:
        raise RuntimeError(f"GRE run {summary['run_id']} wrote no gre_results rows")
    errors = [r["rule_id"] for r in rows if r["status"] not in (*PASSING, "FAIL")]
    if errors:
        raise RuntimeError(f"GRE rule_group {rule_group}: rule(s) {errors} ended in error")
    return [{"rule_ref": f"{rule_group}:{r['rule_id']}", "passed": r["status"] in PASSING} for r in rows]
