"""Extract trigger and batch close (design §11.3-§11.4, D-39 - D-42).

A batch closes only here: after the configured extract job/API accepts the call."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional

import psycopg

from .. import locks
from ..audit.event_logger import EventLogger
from ..batches.crc_repository import batches_of_extract
from ..clock import Clock
from ..common.status import S_COMPLETE, S_COMPLETE_EXCEPTION, S_EXCEPTION, S_NOT_PROVIDED, StatusModel
from ..config import repository as repo
from ..config.models import ExtractPolicy, JobParam
from ..errors import (ConfigError, TriggerBlocked, TriggerCallFailed, TriggerDeferred, TriggerOutcomeUnknown)
from ..settings import Settings
from . import eligibility as elig
from .connectors import CallResult, ExtractConnector, build_connector
from .control import ExtractControlService, ExtractState

log = logging.getLogger(__name__)

EXTRACT_ATTRS = ("extract_id", "project_cd", "table_nm", "run_ty", "rpt_start_dt_key", "rpt_end_dt_key",
                 "extract_stat", "extract_rules_stat", "required_src_cnt", "received_src_cnt", "waived_src_cnt",
                 "included_src_cds", "missing_src_cds", "waived_src_cds", "trigger_cnt", "combine_run_cnt")

_COMBINE_CODE = {"AUTO": "SLA_EVALUATION", "RETRIGGER": "REOPEN_PROMOTED", "MANUAL": "MANUAL_TRIGGER"}


@dataclass
class TriggerOutcome:
    trigger_id: int
    extract_id: int
    job_run_ref: Optional[str]
    closed_batches: int
    warnings: tuple[str, ...]


def render_params(params: list[JobParam], extract: dict, btch_ids: list[str], load_ids: list[int],
                  trigger_id: int, date_format: str) -> list[tuple[str, str]]:
    out = []
    for p in params:
        if p.param_src_cd == "LITERAL":
            val = p.param_val
        elif p.param_src_cd == "EXTRACT_ATTR":
            attr = (p.param_val or "").lower()
            if attr not in EXTRACT_ATTRS:
                raise ConfigError(f"extract job parameter {p.param_nm}: unknown attribute {p.param_val}")
            raw = extract[attr]
            val = raw.strftime(date_format) if isinstance(raw, date) else ("" if raw is None else str(raw))
        elif p.param_src_cd == "BTCH_ID_LIST":
            val = ",".join(btch_ids)
        elif p.param_src_cd == "LOAD_ID_LIST":
            val = ",".join(str(i) for i in load_ids)
        elif p.param_src_cd == "TRIGGER_ID":
            val = str(trigger_id)
        else:
            raise ConfigError(f"unknown Param_Src_Cd {p.param_src_cd}")
        out.append((p.param_nm, val))
    return out


class ExtractTriggerService:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings, control: ExtractControlService,
                 connector_factory: Callable[[str, Settings], ExtractConnector] = build_connector,
                 sleep: Callable[[float], None] = time.sleep):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.control = control
        self.connector_factory = connector_factory
        self.sleep = sleep
        self.logger = EventLogger(conn, clock)

    # ------------------------------------------------------------------ fire
    def fire(self, extract_id: int, trigger_ty: str, requested_by: str, ack_warnings: bool = False,
             audit_blocked: bool = True) -> TriggerOutcome:
        if trigger_ty not in _COMBINE_CODE:
            raise ValueError(f"trigger type {trigger_ty}")
        with locks.held(self.conn, locks.extract_key(extract_id), self.settings.lock_timeout_seconds):
            st = self.control.refresh_locked(extract_id, _COMBINE_CODE[trigger_ty])
            self._check_allowed(st, trigger_ty, ack_warnings, requested_by, audit_blocked)
            if st.policy is None:
                raise ConfigError(f"no ComplianceExtractPolicy for extract {extract_id}")
            batches = batches_of_extract(self.conn, extract_id)
            held: list[str] = []
            try:
                for b in batches:
                    k = locks.batch_key(b["btch_id"])
                    if not locks.try_lock(self.conn, k):
                        with self.conn.transaction():
                            self.logger.audit("BATCH_CLOSE_DEFERRED_LOCKED", extract_id=extract_id,
                                              req_id=b["req_id"], btch_id=b["btch_id"],
                                              description="batch busy; trigger deferred")
                        raise TriggerDeferred(f"batch {b['btch_id']} is locked")
                    held.append(k)
                return self._call_and_close(st, trigger_ty, requested_by, ack_warnings)
            finally:
                for k in held:
                    locks.unlock(self.conn, k)

    def _check_allowed(self, st: ExtractState, trigger_ty: str, ack: bool, who: str, audit_blocked: bool) -> None:
        e, el = st.extract, st.eligibility
        reason = None
        if e["trigger_stat"] == "REQUESTED":
            reason = "a trigger call is already in flight (reconcile it first)"
        elif trigger_ty in ("AUTO", "RETRIGGER") and el.code != elig.AUTO:
            reason = f"not eligible for automatic trigger: {el.code} ({el.reason})"
        elif trigger_ty == "MANUAL" and el.code == elig.NOT_ELIGIBLE:
            reason = f"not eligible: {el.reason}"
        elif trigger_ty == "MANUAL" and el.warnings and not ack:
            reason = "warnings must be acknowledged: " + "; ".join(el.warnings)
        elif trigger_ty == "RETRIGGER" and not e["retrigger_required_ind"]:
            reason = "nothing changed since the last trigger"
        elif trigger_ty in ("AUTO", "MANUAL") and e["trigger_stat"] == "TRIGGERED" and not e["retrigger_required_ind"]:
            reason = "extract already triggered and data unchanged"
        if reason:
            if audit_blocked:
                with self.conn.transaction():
                    self.logger.audit("EXTRACT_TRIGGER_BLOCKED", actor=who, extract_id=e["extract_id"],
                                      project_cd=e["project_cd"], table_nm=e["table_nm"], run_ty=e["run_ty"],
                                      description=f"{trigger_ty}: {reason}")
            raise TriggerBlocked(reason)

    def _call_and_close(self, st: ExtractState, trigger_ty: str, who: str, ack: bool) -> TriggerOutcome:
        e, policy = st.extract, st.policy
        warnings = st.eligibility.warnings if trigger_ty == "MANUAL" else ()
        now = self.clock.now()
        ctx = dict(extract_id=e["extract_id"], project_cd=e["project_cd"], table_nm=e["table_nm"], run_ty=e["run_ty"])
        with self.conn.transaction():
            tid = self.conn.execute(
                """INSERT INTO ComplianceExtractTrigger (Extract_ID, Trigger_Ty, Requested_By, Warning_Txt,
                        Ack_Warnings_Ind, Combine_Run_Nbr, Rendered_Params_Txt, Requested_Dtts)
                   VALUES (%s,%s,%s,%s,%s,%s,'',%s) RETURNING Trigger_ID""",
                (e["extract_id"], trigger_ty, who, "; ".join(warnings) or None, 1 if ack else 0,
                 e["combine_run_cnt"], now)).fetchone()["trigger_id"]
            params = render_params(repo.job_params(self.conn, e["project_cd"], e["table_nm"], e["run_ty"]),
                                   e, st.btch_ids, st.load_ids, tid, self.settings.param_date_format)
            self.conn.execute("UPDATE ComplianceExtractTrigger SET Rendered_Params_Txt=%s WHERE Trigger_ID=%s",
                              ("\n".join(f"{k}={v}" for k, v in params), tid))
            self.conn.execute("UPDATE ComplianceExtractControl SET Trigger_Stat='REQUESTED', Updated_Dtts=%s "
                              "WHERE Extract_ID=%s", (now, e["extract_id"]))
            self.logger.audit("EXTRACT_TRIGGER_REQUESTED", actor=who, trigger_id=tid,
                              description=f"{trigger_ty} via {policy.job_ty}", **ctx)

        result = self._call_with_retries(policy, params)
        if result.accepted:
            closed = self.complete(tid, result.job_run_ref, result.response_txt)
            return TriggerOutcome(tid, e["extract_id"], result.job_run_ref, closed, warnings)
        if result.ambiguous:
            with self.conn.transaction():
                self.conn.execute("UPDATE ComplianceExtractTrigger SET Response_Txt=%s WHERE Trigger_ID=%s",
                                  (result.response_txt, tid))
                self.logger.audit("EXTRACT_TRIGGER_RECONCILE_REQUIRED", trigger_id=tid,
                                  description=f"call outcome unknown: {result.response_txt}", **ctx)
            raise TriggerOutcomeUnknown(f"trigger {tid}: outcome unknown - resolve with `framework resolve-trigger`")
        self.fail(tid, result.response_txt, who)
        raise TriggerCallFailed(f"trigger {tid} rejected: {result.response_txt}")

    def _call_with_retries(self, policy: ExtractPolicy, params) -> CallResult:
        connector = self.connector_factory(policy.job_ty, self.settings)
        attempts = 1 + max(policy.max_call_retry_cnt, 0)
        result = CallResult(False, response_txt="not called")
        for i in range(attempts):
            try:
                result = connector.call(policy, params)
            except Exception as e:  # noqa: BLE001 - connector bug or config problem: not sent
                result = CallResult(False, response_txt=f"{type(e).__name__}: {e}"[:1000])
            if result.accepted or result.ambiguous:
                return result
            if i + 1 < attempts:
                self.sleep(self.settings.call_retry_backoff_seconds * (2 ** i))
        return result

    # ------------------------------------------------------------------ outcomes
    def complete(self, trigger_id: int, job_run_ref: Optional[str], response_txt: Optional[str],
                 actor: str = "SYSTEM") -> int:
        """Mark the call accepted and close every open batch of the extract (§11.3 step 4)."""
        status = StatusModel.load(self.conn)
        now = self.clock.now()
        closed = 0
        with self.conn.transaction():
            t = self.conn.execute("SELECT * FROM ComplianceExtractTrigger WHERE Trigger_ID=%s FOR UPDATE",
                                  (trigger_id,)).fetchone()
            if t is None or t["call_stat"] != "REQUESTED":
                raise TriggerBlocked(f"trigger {trigger_id} is not in REQUESTED state")
            self.conn.execute(
                """UPDATE ComplianceExtractTrigger SET Call_Stat='SUCCEEDED', Job_Run_Ref=%s, Response_Txt=%s,
                          Completed_Dtts=%s WHERE Trigger_ID=%s""", (job_run_ref, response_txt, now, trigger_id))
            e = self.conn.execute("SELECT * FROM ComplianceExtractControl WHERE Extract_ID=%s FOR UPDATE",
                                  (t["extract_id"],)).fetchone()
            self.conn.execute(
                """UPDATE ComplianceExtractControl SET Trigger_Stat='TRIGGERED', Extract_Close_Ind=1,
                          Extract_Closed_Dtts=COALESCE(Extract_Closed_Dtts, %s), Last_Trigger_ID=%s,
                          Trigger_Cnt=Trigger_Cnt+1, Triggered_Combine_Run_Nbr=%s,
                          Triggered_Data_Signature=Data_Signature, Retrigger_Required_Ind=0, Updated_Dtts=%s
                    WHERE Extract_ID=%s""", (now, trigger_id, t["combine_run_nbr"], now, e["extract_id"]))
            for b in self.conn.execute(
                    "SELECT * FROM ComplianceRequestControl WHERE Extract_ID=%s AND Batch_Close_Ind=0 FOR UPDATE",
                    (e["extract_id"],)).fetchall():
                state = status.state(b["req_stat"])
                resolution = b["resolution_ty"]
                if state == S_EXCEPTION:
                    target = S_COMPLETE_EXCEPTION
                    resolution = resolution or "MISSING"
                elif resolution == "NEW_FILE":
                    target = S_COMPLETE
                else:
                    target, resolution = S_NOT_PROVIDED, "MISSING"
                to_code = status.code(target)
                status.check(b["req_stat"], to_code)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Batch_Close_Ind=1, Closed_By_Trigger_ID=%s, Req_Stat=%s,
                              Resolution_Ty=%s, Updated_Dtts=%s WHERE Req_ID=%s""",
                    (trigger_id, to_code, resolution, now, b["req_id"]))
                self.logger.batch_event("BATCH_CLOSED", req_id=b["req_id"], btch_id=b["btch_id"],
                                        trigger_id=trigger_id, actor=actor, detail=f"{to_code} / {resolution}")
                if resolution == "MISSING":
                    self.logger.audit("SOURCE_MISSING_AT_CLOSE", req_id=b["req_id"], btch_id=b["btch_id"],
                                      extract_id=e["extract_id"], trigger_id=trigger_id, project_cd=b["project_cd"],
                                      table_nm=b["table_nm"], src_cd=b["src_cd"], run_ty=b["run_ty"])
                closed += 1
            event = "EXTRACT_TRIGGERED_WITH_WARNINGS" if t["warning_txt"] else "EXTRACT_TRIGGERED"
            self.logger.audit(event, actor=actor, trigger_id=trigger_id, extract_id=e["extract_id"],
                              project_cd=e["project_cd"], table_nm=e["table_nm"], run_ty=e["run_ty"],
                              description=f"{t['trigger_ty']} job_ref={job_run_ref} closed={closed}"
                                          + (f" warnings: {t['warning_txt']}" if t["warning_txt"] else ""))
        return closed

    def fail(self, trigger_id: int, response_txt: Optional[str], actor: str = "SYSTEM") -> None:
        now = self.clock.now()
        with self.conn.transaction():
            t = self.conn.execute("SELECT * FROM ComplianceExtractTrigger WHERE Trigger_ID=%s FOR UPDATE",
                                  (trigger_id,)).fetchone()
            if t is None or t["call_stat"] != "REQUESTED":
                raise TriggerBlocked(f"trigger {trigger_id} is not in REQUESTED state")
            self.conn.execute("""UPDATE ComplianceExtractTrigger SET Call_Stat='FAILED', Response_Txt=%s,
                                        Completed_Dtts=%s WHERE Trigger_ID=%s""", (response_txt, now, trigger_id))
            e = self.conn.execute("UPDATE ComplianceExtractControl SET Trigger_Stat='FAILED', Updated_Dtts=%s "
                                  "WHERE Extract_ID=%s RETURNING *", (now, t["extract_id"])).fetchone()
            self.logger.audit("EXTRACT_TRIGGER_FAILED", actor=actor, trigger_id=trigger_id,
                              extract_id=e["extract_id"], project_cd=e["project_cd"], table_nm=e["table_nm"],
                              run_ty=e["run_ty"], description=(response_txt or "")[:1000])

    # ------------------------------------------------------------------ reconciliation (§11.3 step 6)
    def reconcile(self) -> list[int]:
        """Stale REQUESTED triggers: complete them when a job reference was recorded, otherwise alert."""
        cutoff = self.clock.now() - timedelta(minutes=self.settings.trigger_reconcile_minutes)
        flagged = []
        for t in self.conn.execute("SELECT * FROM ComplianceExtractTrigger WHERE Call_Stat='REQUESTED' "
                                   "AND Requested_Dtts < %s", (cutoff,)).fetchall():
            key = locks.extract_key(t["extract_id"])
            if not locks.try_lock(self.conn, key):
                continue                       # the triggering process is still running
            try:
                if t["job_run_ref"]:
                    self.complete(t["trigger_id"], t["job_run_ref"], "reconciled")
                else:
                    with self.conn.transaction():
                        self.logger.audit("EXTRACT_TRIGGER_RECONCILE_REQUIRED", trigger_id=t["trigger_id"],
                                          extract_id=t["extract_id"],
                                          description="trigger outcome unknown; run `framework resolve-trigger`")
                    flagged.append(t["trigger_id"])
            finally:
                locks.unlock(self.conn, key)
        return flagged

    def resolve(self, trigger_id: int, accepted: bool, actor: str, job_run_ref: Optional[str] = None) -> None:
        """Manual resolution of an in-flight trigger whose outcome is unknown."""
        t = self.conn.execute("SELECT * FROM ComplianceExtractTrigger WHERE Trigger_ID=%s", (trigger_id,)).fetchone()
        if t is None:
            raise LookupError(f"trigger {trigger_id} not found")
        with locks.held(self.conn, locks.extract_key(t["extract_id"]), self.settings.lock_timeout_seconds):
            if accepted:
                self.complete(trigger_id, job_run_ref or t["job_run_ref"], f"resolved accepted by {actor}", actor)
            else:
                self.fail(trigger_id, f"resolved failed by {actor}", actor)
