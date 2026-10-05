"""File rules, run after the core load (design §9.4); they never block or undo a load."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from . import config as cfgmod
from . import db
from .adapters import ERROR, RuleEngine
from .audit import EventLogger
from .batches import get_batch
from .common import Clock, code
from .load import sanitize_db_error
from .settings import Settings

log = logging.getLogger(__name__)

PENDING_RULES_STATS = ("NOT_RUN", "ERROR")


@dataclass
class RulesSummary:
    """Result of one FILE_RULES run: Load_IDs by outcome."""
    evaluated: int = 0
    passed: list[int] = field(default_factory=list)
    warned: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    deferred: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class RulesRunner:
    """Runs the bound rules for every loaded file that has not had them yet."""

    def __init__(self, conn, clock: Clock, settings: Settings, rule_engine: RuleEngine):
        self.conn, self.clock, self.settings, self.rules = conn, clock, settings, rule_engine
        self.logger = EventLogger(conn, clock)

    def run(self, project_cd: Optional[str] = None) -> RulesSummary:
        """One project's loaded files, or every project's when project_cd is None."""
        s = RulesSummary()
        where, params = "", []
        if project_cd is not None:
            where, params = " AND c.Project_Cd = %s", [code(project_cd)]
        rows = self.conn.execute(
            "SELECT f.Load_ID, f.Btch_ID FROM ComplianceFileLoad f JOIN ComplianceRequestControl c ON c.Req_ID = f.Req_ID "
            "WHERE f.Load_Stat = 'PROMOTED' AND f.Rules_Stat IN ('NOT_RUN', 'ERROR')" + where + " ORDER BY f.Load_ID",
            params).fetchall()
        for r in rows:
            key = db.batch_key(r["btch_id"])
            if not db.try_lock(self.conn, key):
                s.deferred.append(r["load_id"])
                continue
            try:
                self._run_load(r["load_id"], s)
            except Exception as e:  # noqa: BLE001
                log.exception("rules for load %s failed", r["load_id"])
                s.errors.append(f"load {r['load_id']}: {type(e).__name__}: {e}")
            finally:
                db.unlock(self.conn, key)
        return s

    def _run_load(self, load_id: int, s: RulesSummary) -> None:
        load = self.conn.execute("SELECT * FROM ComplianceFileLoad WHERE Load_ID=%s", (load_id,)).fetchone()
        if load["load_stat"] != "PROMOTED" or load["rules_stat"] not in PENDING_RULES_STATS:
            return
        s.evaluated += 1
        b = get_batch(self.conn, load["req_id"])
        ctx = dict(project_cd=b["project_cd"], table_nm=b["table_nm"], src_id=b["src_id"], run_ty=b["run_ty"],
                   req_id=b["req_id"], btch_id=b["btch_id"], load_id=load_id)
        bindings = cfgmod.rule_bindings(self.conn, b["project_cd"], b["table_nm"], b["src_id"], b["run_ty"])
        cfg = cfgmod.file_config(self.conn, b["project_cd"], b["table_nm"], b["src_id"], settings=self.settings)
        if not bindings or cfg is None:
            self._set(load_id, "SKIPPED")
            s.skipped.append(load_id)
            return
        outcome = self.rules.run(self.conn, bindings, {
            "scope": "FILE_LEVEL", "btch_id": b["btch_id"], "load_id": load_id,
            "project_cd": b["project_cd"], "table_nm": b["table_nm"], "src_id": b["src_id"], "run_ty": b["run_ty"],
            "rpt_start_dt_key": b["rpt_start_dt_key"], "rpt_end_dt_key": b["rpt_end_dt_key"],
            "req_dt_key": b["req_dt_key"], "stg_schema_nm": cfg.stg_schema_nm, "stg_table_nm": cfg.stg_table_nm,
            "core_schema_nm": cfg.core_schema_nm, "core_table_nm": cfg.table_nm}, self.settings.file_rules_mode)
        with self.conn.transaction():
            self._set(load_id, outcome.status)
            if outcome.status == ERROR:
                detail = sanitize_db_error(outcome.error or "")
                if load["rules_stat"] != ERROR:
                    self.logger.audit("RULES_ENGINE_TECHNICAL_FAILURE", description=detail, **ctx)
                s.errors.append(f"load {load_id}: {detail}")
            elif not outcome.passed:
                detail = "failed rules: " + ", ".join(outcome.failed_rules)
                self.logger.batch_event("FILE_RULES_FAILED", req_id=b["req_id"], btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                self.logger.audit("RULES_VALIDATION_FAILED", description=detail, **ctx)
                s.failed.append(load_id)
            else:
                detail = "warnings: " + ", ".join(outcome.warned_rules) if outcome.warned_rules else None
                self.logger.batch_event("FILE_RULES_PASSED", req_id=b["req_id"], btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                (s.warned if outcome.warned_rules else s.passed).append(load_id)

    def _set(self, load_id: int, rules_stat: str) -> None:
        self.conn.execute("UPDATE ComplianceFileLoad SET Rules_Stat=%s, Updated_Dtts=%s WHERE Load_ID=%s",
                          (rules_stat, self.clock.now(), load_id))
