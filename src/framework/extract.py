"""Extract control (design §7 P9-P10, §10.4, §11): recount + combine + period rules, eligibility,
the SLA sweep, and the trigger that closes batches (the only way a batch closes, D-39).

The extract job, its parameters, the gating mode and the period-rule mode are job-level settings
(EXTRACT_*), passed when the project's evaluate/trigger job is scheduled.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from string import Formatter
from typing import Callable, Optional

import psycopg

from . import config as cfgmod
from . import db
from .adapters import ERROR, FAILED, PASSED, CallResult, ExtractConnector, RuleEngine, build_connector
from .audit import EventLogger
from .batches import batches_of_extract
from .common import (COMPLETED, COMPLETED_WITH_EXCEPTION, DATA_NOT_PROVIDED, EXCEPTION_PENDING,
                     Clock, ConfigError, LockTimeout, TriggerBlocked, TriggerCallFailed, TriggerDeferred,
                     TriggerOutcomeUnknown, check_transition)
from .settings import Settings

log = logging.getLogger(__name__)


# ============================================================================ eligibility (§11.1-11.2), pure
AUTO = "AUTO"
MANUAL_ONLY = "MANUAL_ONLY"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
STRICT = "STRICT_ALL_PASS"
BEST_EFFORT = "BEST_EFFORT"


@dataclass(frozen=True)
class EligibilityInput:
    gating_md: str
    required: int
    received: int
    waived: int
    rules_stat: str
    failed_rules: tuple[str, ...]
    waived_rules: frozenset[str]
    today: date
    earliest_trigger_dt: date


@dataclass(frozen=True)
class Eligibility:
    code: str
    reason: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


def compute_eligibility(inp: EligibilityInput, strict_waiver_auto: bool = False) -> Eligibility:
    if inp.today < inp.earliest_trigger_dt:
        return Eligibility(NOT_ELIGIBLE, f"SLA hold until {inp.earliest_trigger_dt.isoformat()}")
    if inp.required <= 0:
        return Eligibility(NOT_ELIGIBLE, "no sources are required for this period")
    if inp.rules_stat == "PENDING":
        return Eligibility(NOT_ELIGIBLE, "period rules have not been evaluated for the current data")
    if inp.rules_stat == "ERROR":
        return Eligibility(NOT_ELIGIBLE, "period rules failed technically; refresh required")
    rules_clean = inp.rules_stat in ("PASSED", "PASSED_WITH_WARNINGS")
    if inp.received >= inp.required and rules_clean:
        return Eligibility(AUTO, "all sources have data and period rules passed")

    unwaived = tuple(r for r in inp.failed_rules if r not in inp.waived_rules)
    missing = max(inp.required - inp.received, 0)
    unwaived_missing = max(inp.required - inp.received - inp.waived, 0)

    if inp.gating_md == STRICT:
        complete = unwaived_missing == 0
        rules_ok = rules_clean or (inp.rules_stat == "FAILED" and not unwaived)
        if complete and rules_ok:
            code = AUTO if strict_waiver_auto else MANUAL_ONLY
            return Eligibility(code, "eligible through approved waivers "
                               f"({inp.waived} source waiver(s), {len(inp.failed_rules)} rule waiver(s))")
        reasons = []
        if not complete:
            reasons.append(f"{unwaived_missing} source(s) without data or waiver")
        if not rules_ok:
            reasons.append("period rules failed without waiver: " + ", ".join(unwaived))
        return Eligibility(NOT_ELIGIBLE, "; ".join(reasons))

    warnings = []
    if inp.received == 0:
        warnings.append("no source has data (D-49)")
    elif missing:
        warnings.append(f"{missing} of {inp.required} source(s) have no data")
    if inp.rules_stat == "FAILED":
        warnings.append("period rules failed: " + ", ".join(inp.failed_rules))
    return Eligibility(MANUAL_ONLY, "best-effort: manual trigger with acknowledged warnings", tuple(warnings))


# ============================================================================ control: recount / combine / period rules
COMBINE_TRIGGERS = ("EARLY_COMPLETE", "SLA_EVALUATION", "MANUAL_TRIGGER", "REOPEN_PROMOTED",
                    "OVERRIDE_DECISION", "MANUAL_REFRESH")


@dataclass
class ExtractState:
    extract: dict
    eligibility: Eligibility
    btch_ids: list[str]
    load_ids: list[int]


def data_signature(pairs: list[tuple[str, int]]) -> str:
    text = "|".join(f"{b}:{l}" for b, l in sorted(pairs))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ExtractControlService:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings, rule_engine: RuleEngine):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.rules = rule_engine
        self.logger = EventLogger(self.conn, clock)

    def refresh(self, extract_id: int, trigger_cd: str) -> ExtractState:
        with db.held(self.conn, db.extract_key(extract_id), self.settings.lock_timeout_seconds):
            return self.refresh_locked(extract_id, trigger_cd)

    def refresh_locked(self, extract_id: int, trigger_cd: str) -> ExtractState:
        if trigger_cd not in COMBINE_TRIGGERS:
            raise ValueError(f"unknown combine trigger {trigger_cd}")
        now = self.clock.now()
        with self.conn.transaction():
            e = self.conn.execute("SELECT * FROM ComplianceExtractControl WHERE Extract_ID=%s FOR UPDATE",
                                  (extract_id,)).fetchone()
            if e is None:
                raise LookupError(f"extract {extract_id} not found")
            batches = batches_of_extract(self.conn, extract_id)
            received = [b for b in batches if b["resolution_ty"] in ("NEW_FILE", "CARRY_FORWARD")]
            received_ids = {b["req_id"] for b in received}
            waivers = self.conn.execute(
                """SELECT Override_Ty, Req_ID, Rule_Ref FROM ComplianceBatchOverride
                    WHERE Extract_ID=%s AND Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER') AND Apprvl_Stat='APPROVED'""",
                (extract_id,)).fetchall()
            waived_req = {w["req_id"] for w in waivers if w["override_ty"] == "SOURCE_WAIVER"} - received_ids
            waived_rules = frozenset(w["rule_ref"] for w in waivers if w["override_ty"] == "RULE_WAIVER")
            required = e["required_src_cnt"]
            got = len(received) + len(waived_req)
            extract_stat = ("COMPLETE" if required > 0 and got >= required
                            else "PARTIAL" if got > 0 else "PENDING")
            included = sorted(b["src_cd"] for b in received)
            carried = sorted(b["src_cd"] for b in received if b["resolution_ty"] == "CARRY_FORWARD")
            waived_src = sorted(b["src_cd"] for b in batches if b["req_id"] in waived_req)
            missing = sorted(b["src_cd"] for b in batches if b["req_id"] not in received_ids | waived_req)
            pairs = self._data_pairs(received)
            signature = data_signature(pairs)
            btch_ids = [p[0] for p in sorted(pairs)]
            load_ids = [p[1] for p in sorted(pairs)]

            rules_stat = e["extract_rules_stat"]
            failed = tuple(x for x in (e["failed_rule_refs"] or "").split(",") if x)
            combine_cnt = e["combine_run_cnt"]
            combined = False
            wants_combine = trigger_cd != "EARLY_COMPLETE" or (required > 0 and len(received) >= required)
            fresh = (signature == e["data_signature"] and rules_stat not in ("PENDING", "ERROR"))
            if wants_combine and not (fresh and trigger_cd != "MANUAL_REFRESH"):
                rules_stat, failed = self._period_rules(e, btch_ids, load_ids)
                combine_cnt += 1
                combined = True
            elif signature != e["data_signature"]:
                rules_stat, failed = "PENDING", ()       # data changed; previous rule result is stale

            retrigger = e["retrigger_required_ind"]
            if (e["trigger_cnt"] > 0 and e["triggered_data_signature"]
                    and signature != e["triggered_data_signature"]):
                if not retrigger:
                    self.logger.audit("EXTRACT_RETRIGGER_REQUIRED", extract_id=extract_id,
                                      project_cd=e["project_cd"], table_nm=e["table_nm"], run_ty=e["run_ty"],
                                      description="data changed after the last successful trigger")
                retrigger = 1

            el = compute_eligibility(EligibilityInput(
                gating_md=self.settings.extract_gating_mode, required=required, received=len(received), waived=len(waived_req),
                rules_stat=rules_stat, failed_rules=failed, waived_rules=waived_rules,
                today=self.clock.today(self.settings.business_tz), earliest_trigger_dt=e["earliest_trigger_dt"]),
                strict_waiver_auto=self.settings.strict_waiver_auto_trigger)
            if el.code != e["eligibility_cd"]:
                self.logger.audit("EXTRACT_ELIGIBILITY_CHANGED", extract_id=extract_id, project_cd=e["project_cd"],
                                  table_nm=e["table_nm"], run_ty=e["run_ty"],
                                  description=f"{e['eligibility_cd']} -> {el.code}: {el.reason}")
            reason = el.reason + (" | warnings: " + "; ".join(el.warnings) if el.warnings else "")
            row = self.conn.execute(
                """UPDATE ComplianceExtractControl SET
                      Received_Src_Cnt=%s, Waived_Src_Cnt=%s, Included_Src_Cds=%s, Missing_Src_Cds=%s,
                      Waived_Src_Cds=%s, Carried_Src_Cds=%s, Extract_Stat=%s, Extract_Rules_Stat=%s, Failed_Rule_Refs=%s,
                      Eligibility_Cd=%s, Eligibility_Rsn_Txt=%s, Retrigger_Required_Ind=%s,
                      Combine_Run_Cnt=%s,
                      Combine_Last_Run_Dtts = CASE WHEN %s THEN %s ELSE Combine_Last_Run_Dtts END,
                      Combine_Last_Trigger_Cd = CASE WHEN %s THEN %s ELSE Combine_Last_Trigger_Cd END,
                      Combine_Btch_ID_List = CASE WHEN %s THEN %s ELSE Combine_Btch_ID_List END,
                      Data_Signature = CASE WHEN %s THEN %s ELSE Data_Signature END,
                      Updated_Dtts=%s
                    WHERE Extract_ID=%s RETURNING *""",
                (len(received), len(waived_req), ",".join(included), ",".join(missing), ",".join(waived_src),
                 ",".join(carried) or None,
                 extract_stat, rules_stat, ",".join(failed) or None, el.code, reason[:1000], retrigger,
                 combine_cnt, combined, now, combined, trigger_cd, combined, ",".join(btch_ids),
                 combined, signature, now, extract_id)).fetchone()
        return ExtractState(row, el, btch_ids, load_ids)

    def _data_pairs(self, received: list[dict]) -> list[tuple[str, int]]:
        """(Btch_ID, Load_ID) whose current core rows make up the extract. A CARRY_FORWARD batch contributes
        the reused batch's current data (D-70)."""
        pairs = [(b["btch_id"], b["current_load_id"]) for b in received if b["resolution_ty"] == "NEW_FILE"]
        reuse = [b["reuse_btch_id"] for b in received if b["resolution_ty"] == "CARRY_FORWARD"]
        if reuse:
            pairs += [(r["btch_id"], r["current_load_id"]) for r in self.conn.execute(
                """SELECT Btch_ID, Current_Load_ID FROM ComplianceRequestControl
                    WHERE Btch_ID = ANY(%s) AND Current_Load_ID IS NOT NULL""", (reuse,)).fetchall()]
        return sorted(set(pairs))

    def _period_rules(self, e: dict, btch_ids: list[str], load_ids: list[int]) -> tuple[str, tuple[str, ...]]:
        if not btch_ids:
            return PASSED, ()             # nothing to validate (zero data - see D-49)
        bindings = cfgmod.rule_bindings(self.conn, e["project_cd"], e["table_nm"], "*", "PERIOD_LEVEL")
        cfg = cfgmod.file_config(self.conn, e["project_cd"], e["table_nm"])
        outcome = self.rules.run(self.conn, bindings, {
            "scope": "PERIOD_LEVEL", "extract_id": e["extract_id"], "project_cd": e["project_cd"],
            "table_nm": e["table_nm"], "run_ty": e["run_ty"], "rpt_start_dt_key": e["rpt_start_dt_key"],
            "rpt_end_dt_key": e["rpt_end_dt_key"], "btch_id_list": list(btch_ids), "load_id_list": list(load_ids),
            "core_schema_nm": cfg.core_schema_nm if cfg else None, "core_tblnm": e["table_nm"]},
            self.settings.period_rules_mode)
        ctx = dict(extract_id=e["extract_id"], project_cd=e["project_cd"], table_nm=e["table_nm"], run_ty=e["run_ty"])
        if outcome.status == ERROR:
            self.logger.audit("RULES_ENGINE_TECHNICAL_FAILURE", description=f"period rules: {outcome.error}"[:900], **ctx)
            return ERROR, ()
        if outcome.status == FAILED:
            self.logger.audit("PERIOD_RULES_FAILED", **ctx,
                              description=f"failed: {', '.join(outcome.failed_rules)}; "
                                          f"contributing batches: {', '.join(btch_ids)}"[:2000])
        return outcome.status, tuple(outcome.failed_rules)


# ============================================================================ trigger and close (§11.3-11.4)
EXTRACT_ATTRS = ("extract_id", "project_cd", "table_nm", "run_ty", "rpt_start_dt_key", "rpt_end_dt_key",
                 "extract_stat", "extract_rules_stat", "required_src_cnt", "received_src_cnt", "waived_src_cnt",
                 "included_src_cds", "missing_src_cds", "waived_src_cds", "carried_src_cds", "trigger_cnt",
                 "combine_run_cnt")

_COMBINE_CODE = {"AUTO": "SLA_EVALUATION", "RETRIGGER": "REOPEN_PROMOTED", "MANUAL": "MANUAL_TRIGGER"}


@dataclass
class TriggerOutcome:
    trigger_id: int
    extract_id: int
    job_run_ref: Optional[str]
    closed_batches: int
    warnings: tuple[str, ...]


def render_params(templates: dict, extract: dict, btch_ids: list[str], load_ids: list[int],
                  trigger_id: int, date_format: str = "%Y-%m-%d") -> list[tuple[str, str]]:
    """EXTRACT_PARAMS is a JSON object {"--NAME": "text with {placeholders}"} (was ComplianceExtractJobParam).
    Placeholders (case-insensitive): the EXTRACT_ATTRS names, {btch_id_list}, {load_id_list}, {trigger_id}."""
    values = {a: extract[a] for a in EXTRACT_ATTRS}
    values = {k: (v.strftime(date_format) if isinstance(v, date) else "" if v is None else str(v))
              for k, v in values.items()}
    values.update(btch_id_list=",".join(btch_ids), load_id_list=",".join(map(str, load_ids)),
                  trigger_id=str(trigger_id))
    out = []
    for name, template in templates.items():
        fields = {f for _, f, _, _ in Formatter().parse(str(template)) if f is not None}
        unknown = sorted(f for f in fields if f.lower() not in values)
        if unknown:
            raise ConfigError(f"EXTRACT_PARAMS {name}: unknown placeholder(s) {unknown}; allowed: {sorted(values)}")
        out.append((name, str(template).format_map({f: values[f.lower()] for f in fields})))
    return out


class ExtractTriggerService:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings, control: ExtractControlService,
                 connector_factory: Callable[[Settings], ExtractConnector] = build_connector,
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
        with db.held(self.conn, db.extract_key(extract_id), self.settings.lock_timeout_seconds):
            st = self.control.refresh_locked(extract_id, _COMBINE_CODE[trigger_ty])
            self._check_allowed(st, trigger_ty, ack_warnings, requested_by, audit_blocked)
            self.check_job_config()
            batches = batches_of_extract(self.conn, extract_id)
            held: list[str] = []
            try:
                for b in batches:
                    k = db.batch_key(b["btch_id"])
                    if not db.try_lock(self.conn, k):
                        with self.conn.transaction():
                            self.logger.audit("BATCH_CLOSE_DEFERRED_LOCKED", extract_id=extract_id,
                                              req_id=b["req_id"], btch_id=b["btch_id"],
                                              description="batch busy; trigger deferred")
                        raise TriggerDeferred(f"batch {b['btch_id']} is locked")
                    held.append(k)
                return self._call_and_close(st, trigger_ty, requested_by, ack_warnings)
            finally:
                for k in held:
                    db.unlock(self.conn, k)

    def job_ref(self) -> Optional[str]:
        s = self.settings
        return s.extract_job_name if s.extract_job_type == "GLUE_JOB" else s.extract_endpoint_url

    def check_job_config(self) -> None:
        if not self.job_ref():
            raise ConfigError("extract job not configured: set EXTRACT_JOB_NAME (GLUE_JOB) or "
                              "EXTRACT_ENDPOINT_URL (HTTP_API) for this project's job")

    def _check_allowed(self, st: ExtractState, trigger_ty: str, ack: bool, who: str, audit_blocked: bool) -> None:
        e, el = st.extract, st.eligibility
        reason = None
        if e["trigger_stat"] == "REQUESTED":
            reason = "a trigger call is already in flight (reconcile it first)"
        elif trigger_ty in ("AUTO", "RETRIGGER") and el.code != AUTO:
            reason = f"not eligible for automatic trigger: {el.code} ({el.reason})"
        elif trigger_ty == "MANUAL" and el.code == NOT_ELIGIBLE:
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
        e = st.extract
        warnings = st.eligibility.warnings if trigger_ty == "MANUAL" else ()
        now = self.clock.now()
        ctx = dict(extract_id=e["extract_id"], project_cd=e["project_cd"], table_nm=e["table_nm"], run_ty=e["run_ty"])
        with self.conn.transaction():
            tid = self.conn.execute(
                """INSERT INTO ComplianceExtractTrigger (Extract_ID, Trigger_Ty, Requested_By, Warning_Txt,
                        Ack_Warnings_Ind, Combine_Run_Nbr, Rendered_Params_Txt, Extract_Job_Ref, Requested_Dtts)
                   VALUES (%s,%s,%s,%s,%s,%s,'',%s,%s) RETURNING Trigger_ID""",
                (e["extract_id"], trigger_ty, who, "; ".join(warnings) or None, 1 if ack else 0,
                 e["combine_run_cnt"], self.job_ref(), now)).fetchone()["trigger_id"]
            params = render_params(self.settings.extract_params, e, st.btch_ids, st.load_ids, tid,
                                   self.settings.param_date_format)
            self.conn.execute("UPDATE ComplianceExtractTrigger SET Rendered_Params_Txt=%s WHERE Trigger_ID=%s",
                              ("\n".join(f"{k}={v}" for k, v in params), tid))
            self.conn.execute("UPDATE ComplianceExtractControl SET Trigger_Stat='REQUESTED', Updated_Dtts=%s "
                              "WHERE Extract_ID=%s", (now, e["extract_id"]))
            self.logger.audit("EXTRACT_TRIGGER_REQUESTED", actor=who, trigger_id=tid,
                              description=f"{trigger_ty} via {self.settings.extract_job_type} {self.job_ref()}",
                              **ctx)

        result = self._call_with_retries(params)
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

    def _call_with_retries(self, params) -> CallResult:
        connector = self.connector_factory(self.settings)
        attempts = 1 + max(self.settings.extract_max_call_retries, 0)
        result = CallResult(False, response_txt="not called")
        for i in range(attempts):
            try:
                result = connector.call(self.settings, params)
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
            for b in batches_of_extract(self.conn, e["extract_id"], open_only=True):
                resolution = b["resolution_ty"]
                if b["req_stat"] == EXCEPTION_PENDING:
                    to_code, resolution = COMPLETED_WITH_EXCEPTION, resolution or "MISSING"
                elif resolution in ("NEW_FILE", "CARRY_FORWARD"):
                    to_code = COMPLETED
                else:
                    to_code, resolution = DATA_NOT_PROVIDED, "MISSING"
                check_transition(b["req_stat"], to_code)
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
            key = db.extract_key(t["extract_id"])
            if not db.try_lock(self.conn, key):
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
                db.unlock(self.conn, key)
        return flagged

    def resolve(self, trigger_id: int, accepted: bool, actor: str, job_run_ref: Optional[str] = None) -> None:
        """Manual resolution of an in-flight trigger whose outcome is unknown."""
        t = self.conn.execute("SELECT * FROM ComplianceExtractTrigger WHERE Trigger_ID=%s", (trigger_id,)).fetchone()
        if t is None:
            raise LookupError(f"trigger {trigger_id} not found")
        with db.held(self.conn, db.extract_key(t["extract_id"]), self.settings.lock_timeout_seconds):
            if accepted:
                self.complete(trigger_id, job_run_ref or t["job_run_ref"], f"resolved accepted by {actor}", actor)
            else:
                self.fail(trigger_id, f"resolved failed by {actor}", actor)


# ============================================================================ SLA sweep (P10, D-40, D-41)
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

    def run(self, project_cd: Optional[str] = None, table_nm: Optional[str] = None,
            run_ty: Optional[str] = None) -> EvaluationSummary:
        """Sweep the extracts in the job's scope (a project, optionally one table / run type)."""
        s = EvaluationSummary()
        s.reconcile_required = self.trigger.reconcile()
        rows = self.conn.execute(
            """SELECT Extract_ID, Trigger_Stat, Trigger_Cnt, Retrigger_Required_Ind FROM ComplianceExtractControl
                WHERE Earliest_Trigger_Dt <= %s
                  AND (%s::text IS NULL OR Project_Cd = %s) AND (%s::text IS NULL OR Table_Nm = %s)
                  AND (%s::text IS NULL OR Run_Ty = %s)
                  AND (Trigger_Stat = 'NOT_TRIGGERED'
                       OR (Trigger_Stat = 'FAILED' AND %s)
                       OR (Retrigger_Required_Ind = 1 AND Trigger_Stat <> 'REQUESTED' AND %s))
                ORDER BY Extract_ID""",
            (self.clock.today(self.settings.business_tz), project_cd, project_cd, table_nm, table_nm, run_ty, run_ty,
             self.settings.retry_failed_triggers_on_sweep, self.settings.auto_retrigger_after_reopen)).fetchall()
        if rows:
            self.trigger.check_job_config()
        for r in rows:
            s.evaluated += 1
            ttype = "RETRIGGER" if r["retrigger_required_ind"] and r["trigger_cnt"] > 0 else "AUTO"
            self._attempt(r["extract_id"], ttype, s)
        return s

    def after_reopen_promotion(self, extract_id: int) -> None:
        """D-41: re-run combine/rules; re-trigger now when still AUTO-eligible and this process has the
        extract job configured (otherwise the project's next evaluate-extracts run does it)."""
        st = self.control.refresh(extract_id, "REOPEN_PROMOTED")
        if (st.extract["trigger_cnt"] > 0 and st.extract["retrigger_required_ind"]
                and self.settings.auto_retrigger_after_reopen and self.trigger.job_ref()):
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
