"""SLA evaluation sweep and automatic triggering (design §7 P10, D-40, D-41)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import psycopg

from ..clock import Clock
from ..errors import (LockTimeout, TriggerBlocked, TriggerCallFailed, TriggerDeferred, TriggerOutcomeUnknown)
from ..settings import Settings
from .control import ExtractControlService
from .trigger import ExtractTriggerService

log = logging.getLogger(__name__)


@dataclass
class EvaluationSummary:
    evaluated: int = 0
    triggered: list[int] = field(default_factory=list)
    not_eligible: int = 0
    deferred: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    reconcile_required: list[int] = field(default_factory=list)


class ExtractEvaluator:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings,
                 control: ExtractControlService, trigger: ExtractTriggerService):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.control, self.trigger = control, trigger

    def run(self) -> EvaluationSummary:
        s = EvaluationSummary()
        s.reconcile_required = self.trigger.reconcile()
        horizon = (self.clock.now() + timedelta(days=1)).date()   # exact per-timezone check happens in eligibility
        rows = self.conn.execute(
            """SELECT Extract_ID, Trigger_Stat, Trigger_Cnt, Retrigger_Required_Ind FROM ComplianceExtractControl
                WHERE Earliest_Trigger_Dt <= %s
                  AND (Trigger_Stat = 'NOT_TRIGGERED'
                       OR (Trigger_Stat = 'FAILED' AND %s)
                       OR (Retrigger_Required_Ind = 1 AND Trigger_Stat <> 'REQUESTED' AND %s))
                ORDER BY Extract_ID""",
            (horizon, self.settings.retry_failed_triggers_on_sweep, self.settings.auto_retrigger_after_reopen)).fetchall()
        for r in rows:
            s.evaluated += 1
            ttype = "RETRIGGER" if r["retrigger_required_ind"] and r["trigger_cnt"] > 0 else "AUTO"
            self._attempt(r["extract_id"], ttype, s)
        return s

    def after_reopen_promotion(self, extract_id: int) -> None:
        """D-41: re-run combine/rules and re-trigger automatically when still AUTO-eligible."""
        st = self.control.refresh(extract_id, "REOPEN_PROMOTED")
        if st.extract["trigger_cnt"] > 0 and st.extract["retrigger_required_ind"] and self.settings.auto_retrigger_after_reopen:
            self._attempt(extract_id, "RETRIGGER", EvaluationSummary())

    def _attempt(self, extract_id: int, ttype: str, s: EvaluationSummary) -> None:
        try:
            self.trigger.fire(extract_id, ttype, "SYSTEM", audit_blocked=False)
            s.triggered.append(extract_id)
        except TriggerBlocked:
            s.not_eligible += 1
        except (TriggerDeferred, LockTimeout):
            s.deferred.append(extract_id)
        except TriggerOutcomeUnknown:
            s.reconcile_required.append(extract_id)
        except TriggerCallFailed:
            s.failed.append(extract_id)
