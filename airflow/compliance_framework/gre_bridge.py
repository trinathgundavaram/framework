"""File rules through the GRE rules engine: GRE_ENTRYPOINT=compliance_framework.gre_bridge:run_gre."""
import json
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


def _configured(env_key: str) -> dict:
    """run_params / text_params / extra_filters of the Variable's gre section."""
    return json.loads(os.environ.get(env_key) or "{}")


def run_gre(conn, rule_group: str, rule_variant: str, run_params: dict) -> list:
    """One GRE rule_group for one staged file -> [{"rule_ref", "passed"}]; raises on a technical failure."""
    meta_db = os.environ.get("GRE_META_DB")
    if not meta_db:
        raise RuntimeError("GRE_META_DB is not set")
    run_key = f"CBF_LOAD_{run_params['load_id']}"
    params = {**_configured("GRE_RUN_PARAMS"), **{k: _text(v) for k, v in run_params.items() if v is not None}}
    variant = None if rule_variant in (None, "", "*") else rule_variant
    outcome, exit_code = _run_rules()(project_name=os.environ.get("GRE_PROJECT_NAME") or None, rule_group=rule_group,
                                      rule_variant=variant, run_key=run_key, run_params=params,
                                      extra_filters=_configured("GRE_EXTRA_FILTERS"),
                                      text_params=_configured("GRE_TEXT_PARAMS"),
                                      log_level=os.environ.get("GRE_LOG_LEVEL") or "ERROR")
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
