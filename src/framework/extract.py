"""Extract control (design §7 P9-P10, §10.4, §11): recount + combine + period rules, eligibility,
the SLA sweep, and the close that ends every batch of a run (D-39).

The framework does **not** call the extract job (D-76): it decides when a run's data is complete and
closes it. The project's job chain generates the extract afterwards, reading the closed extract row
and its `Combine_Btch_ID_List`. A late arrival or correction promoted after the close sets
`Regenerate_Required_Ind`, which is the signal to generate that run again.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import psycopg

from . import config as cfgmod
from . import db
from .adapters import ERROR, FAILED, PASSED, RuleEngine
from .audit import EventLogger
from .batches import batches_of_extract
from .common import (COMPLETED, COMPLETED_WITH_EXCEPTION, DATA_NOT_PROVIDED, EXCEPTION_PENDING, Clock,
                     CloseBlocked, CloseDeferred, LockTimeout, check_transition, earliest_close_date)
from .settings import Settings

log = logging.getLogger(__name__)

COMBINE_TRIGGERS = ("EARLY_COMPLETE", "SLA_EVALUATION", "MANUAL_CLOSE", "LATE_PROMOTION",
                    "OVERRIDE_DECISION", "MANUAL_REFRESH")

AUTO = "AUTO"
MANUAL_ONLY = "MANUAL_ONLY"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
STRICT = "STRICT_ALL_PASS"
BEST_EFFORT = "BEST_EFFORT"


# ============================================================================ eligibility (§11.2), pure
@dataclass(frozen=True)
class EligibilityInput:
    gating_md: str
    required: int
    received: int
    rules_stat: str
    failed_rules: tuple[str, ...]
    today: date
    earliest_close_dt: date          # Req_Dt_Key + (SLA_Days - 1), computed, never stored


@dataclass(frozen=True)
class Eligibility:
    code: str
    reason: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


def compute_eligibility(inp: EligibilityInput) -> Eligibility:
    if inp.today < inp.earliest_close_dt:
        return Eligibility(NOT_ELIGIBLE, f"SLA hold until {inp.earliest_close_dt.isoformat()}")
    if inp.required <= 0:
        return Eligibility(NOT_ELIGIBLE, "no sources are required for this run")
    if inp.rules_stat == "PENDING":
        return Eligibility(NOT_ELIGIBLE, "period rules have not been evaluated for the current data")
    if inp.rules_stat == "ERROR":
        return Eligibility(NOT_ELIGIBLE, "period rules failed technically; refresh required")
    rules_clean = inp.rules_stat in ("PASSED", "PASSED_WITH_WARNINGS")
    if inp.received >= inp.required and rules_clean:
        return Eligibility(AUTO, "all sources have data and period rules passed")

    missing = max(inp.required - inp.received, 0)
    if inp.gating_md == STRICT:
        reasons = []
        if missing:
            reasons.append(f"{missing} of {inp.required} source(s) without data")
        if not rules_clean:
            reasons.append("period rules failed: " + ", ".join(inp.failed_rules))
        return Eligibility(NOT_ELIGIBLE, "; ".join(reasons))

    warnings = []
    if inp.received == 0:
        warnings.append("no source has data (D-49)")
    elif missing:
        warnings.append(f"{missing} of {inp.required} source(s) have no data")
    if inp.rules_stat == "FAILED":
        warnings.append("period rules failed: " + ", ".join(inp.failed_rules))
    return Eligibility(MANUAL_ONLY, "best-effort: manual close with acknowledged warnings", tuple(warnings))


# ============================================================================ control: recount / combine / rules
@dataclass
class ExtractState:
    extract: dict
    eligibility: Eligibility
    btch_ids: list[str]
    load_ids: list[int]


def data_signature(pairs: list[tuple[str, Optional[int]]]) -> str:
    return hashlib.sha256("|".join(f"{b}:{load}" for b, load in sorted(pairs)).encode("utf-8")).hexdigest()


class ExtractControlService:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings, rule_engine: RuleEngine):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.rules = rule_engine
        self.logger = EventLogger(conn, clock)

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
            required = e["required_src_cnt"]
            extract_stat = ("COMPLETE" if required > 0 and len(received) >= required
                            else "PARTIAL" if received else "PENDING")
            received_ids = {b["req_id"] for b in received}
            included = sorted(b["src_cd"] for b in received)
            carried = sorted(b["src_cd"] for b in received if b["resolution_ty"] == "CARRY_FORWARD")
            missing = sorted(b["src_cd"] for b in batches if b["req_id"] not in received_ids)
            pairs = self._data_pairs(received)
            signature = data_signature(pairs)
            btch_ids = [p[0] for p in pairs]
            load_ids = [p[1] for p in pairs if p[1] is not None]

            rules_stat = e["extract_rules_stat"]
            failed = tuple(x for x in (e["failed_rule_refs"] or "").split(",") if x)
            combine_cnt = e["combine_run_cnt"]
            combined = False
            wants_combine = trigger_cd != "EARLY_COMPLETE" or (required > 0 and len(received) >= required)
            fresh = signature == e["data_signature"] and rules_stat not in ("PENDING", "ERROR")
            if wants_combine and not (fresh and trigger_cd != "MANUAL_REFRESH"):
                rules_stat, failed = self._period_rules(e, btch_ids, load_ids)
                combine_cnt += 1
                combined = True
            elif signature != e["data_signature"]:
                rules_stat, failed = "PENDING", ()       # data changed; the previous rule result is stale

            regenerate = e["regenerate_required_ind"]
            if e["extract_close_ind"] == 1 and e["closed_data_signature"] and signature != e["closed_data_signature"]:
                if not regenerate:
                    self.logger.audit("EXTRACT_REGENERATE_REQUIRED", extract_id=extract_id, project_cd=e["project_cd"],
                                      table_nm=e["table_nm"], run_ty=e["run_ty"],
                                      description="data changed after the extract was closed")
                regenerate = 1

            el = compute_eligibility(EligibilityInput(
                gating_md=self.settings.extract_gating_mode, required=required, received=len(received),
                rules_stat=rules_stat, failed_rules=failed, today=self.clock.today(self.settings.business_tz),
                earliest_close_dt=self.hold_date(e)))
            if el.code != e["eligibility_cd"]:
                self.logger.audit("EXTRACT_ELIGIBILITY_CHANGED", extract_id=extract_id, project_cd=e["project_cd"],
                                  table_nm=e["table_nm"], run_ty=e["run_ty"],
                                  description=f"{e['eligibility_cd']} -> {el.code}: {el.reason}")
            reason = el.reason + (" | warnings: " + "; ".join(el.warnings) if el.warnings else "")
            row = self.conn.execute(
                """UPDATE ComplianceExtractControl SET
                      Received_Src_Cnt=%s, Included_Src_Cds=%s, Missing_Src_Cds=%s, Carried_Src_Cds=%s,
                      Extract_Stat=%s, Extract_Rules_Stat=%s, Failed_Rule_Refs=%s,
                      Eligibility_Cd=%s, Eligibility_Rsn_Txt=%s, Regenerate_Required_Ind=%s, Combine_Run_Cnt=%s,
                      Combine_Last_Run_Dtts = CASE WHEN %s THEN %s ELSE Combine_Last_Run_Dtts END,
                      Combine_Last_Trigger_Cd = CASE WHEN %s THEN %s ELSE Combine_Last_Trigger_Cd END,
                      Combine_Btch_ID_List = CASE WHEN %s THEN %s ELSE Combine_Btch_ID_List END,
                      Data_Signature = CASE WHEN %s THEN %s ELSE Data_Signature END,
                      Updated_Dtts=%s
                    WHERE Extract_ID=%s RETURNING *""",
                (len(received), ",".join(included), ",".join(missing), ",".join(carried) or None,
                 extract_stat, rules_stat, ",".join(failed) or None, el.code, reason[:1000], regenerate,
                 combine_cnt, combined, now, combined, trigger_cd, combined, ",".join(btch_ids),
                 combined, signature, now, extract_id)).fetchone()
        return ExtractState(row, el, btch_ids, load_ids)

    def hold_date(self, extract: dict) -> date:
        """SLA hold of the run, computed from its run date and the run type's SLA_Days (D-38, D-77)."""
        rt = cfgmod.run_type(self.conn, extract["run_ty"])
        return earliest_close_date(extract["req_dt_key"], rt.sla_days if rt else 1)

    def _data_pairs(self, received: list[dict]) -> list[tuple[str, Optional[int]]]:
        """(Btch_ID, promoted Load_ID) whose current core rows make up the extract. A CARRY_FORWARD batch
        contributes the reused batch's current data (D-70)."""
        btch_ids = [b["reuse_btch_id"] if b["resolution_ty"] == "CARRY_FORWARD" else b["btch_id"] for b in received]
        if not btch_ids:
            return []
        loads = {r["btch_id"]: r["load_id"] for r in self.conn.execute(
            "SELECT Btch_ID, Load_ID FROM ComplianceFileLoad WHERE Btch_ID = ANY(%s) AND Load_Stat='PROMOTED'",
            (btch_ids,)).fetchall()}
        return sorted({(b, loads.get(b)) for b in btch_ids})

    def _period_rules(self, e: dict, btch_ids: list[str], load_ids: list[int]) -> tuple[str, tuple[str, ...]]:
        if not btch_ids:
            return PASSED, ()             # nothing to validate (zero data - see D-49)
        bindings = cfgmod.rule_bindings(self.conn, e["project_cd"], e["table_nm"], "*", "PERIOD_LEVEL")
        cfg = cfgmod.file_config(self.conn, e["project_cd"], e["table_nm"])
        outcome = self.rules.run(self.conn, bindings, {
            "scope": "PERIOD_LEVEL", "extract_id": e["extract_id"], "project_cd": e["project_cd"],
            "table_nm": e["table_nm"], "run_ty": e["run_ty"], "rpt_start_dt_key": e["rpt_start_dt_key"],
            "rpt_end_dt_key": e["rpt_end_dt_key"], "req_dt_key": e["req_dt_key"],
            "btch_id_list": list(btch_ids), "load_id_list": list(load_ids),
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

    # ------------------------------------------------------------------ close (§11.3, D-39)
    def close(self, extract_id: int, closed_by: str, ack_warnings: bool = False,
              automatic: bool = False) -> "CloseOutcome":
        """Close the run: every open batch of the extract is resolved and closed. The extract job that
        generates the submission runs afterwards, outside the framework."""
        with db.held(self.conn, db.extract_key(extract_id), self.settings.lock_timeout_seconds):
            st = self.refresh_locked(extract_id, "SLA_EVALUATION" if automatic else "MANUAL_CLOSE")
            e, el = st.extract, st.eligibility
            reason = None
            if e["extract_close_ind"] == 1:
                reason = "extract is already closed"
            elif automatic and el.code != AUTO:
                reason = f"not eligible for automatic close: {el.code} ({el.reason})"
            elif el.code == NOT_ELIGIBLE:
                reason = f"not eligible: {el.reason}"
            elif not automatic and el.warnings and not ack_warnings:
                reason = "warnings must be acknowledged: " + "; ".join(el.warnings)
            if reason:
                if not automatic:
                    with self.conn.transaction():
                        self.logger.audit("EXTRACT_CLOSE_BLOCKED", actor=closed_by, extract_id=extract_id,
                                          project_cd=e["project_cd"], table_nm=e["table_nm"], run_ty=e["run_ty"],
                                          description=reason)
                raise CloseBlocked(reason)

            held: list[str] = []
            try:
                for b in batches_of_extract(self.conn, extract_id):
                    if b["batch_close_ind"] == 1:
                        continue
                    key = db.batch_key(b["btch_id"])
                    if not db.try_lock(self.conn, key):
                        with self.conn.transaction():
                            self.logger.audit("EXTRACT_CLOSE_DEFERRED_LOCKED", extract_id=extract_id,
                                              req_id=b["req_id"], btch_id=b["btch_id"],
                                              description="batch busy; close deferred")
                        raise CloseDeferred(f"batch {b['btch_id']} is locked")
                    held.append(key)
                return self._close_locked(st, closed_by, ack_warnings)
            finally:
                for key in held:
                    db.unlock(self.conn, key)

    def _close_locked(self, st: ExtractState, closed_by: str, ack: bool) -> "CloseOutcome":
        e = st.extract
        warnings = st.eligibility.warnings
        now = self.clock.now()
        closed = 0
        with self.conn.transaction():
            for b in batches_of_extract(self.conn, e["extract_id"], open_only=True):
                resolution = b["resolution_ty"]
                if b["req_stat"] == EXCEPTION_PENDING:
                    to_stat, resolution = COMPLETED_WITH_EXCEPTION, resolution or "MISSING"
                elif resolution in ("NEW_FILE", "CARRY_FORWARD"):
                    to_stat = COMPLETED
                else:
                    to_stat, resolution = DATA_NOT_PROVIDED, "MISSING"
                check_transition(b["req_stat"], to_stat)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Batch_Close_Ind=1, Req_Stat=%s, Resolution_Ty=%s,
                              Updated_Dtts=%s WHERE Req_ID=%s""", (to_stat, resolution, now, b["req_id"]))
                self.logger.batch_event("BATCH_CLOSED", req_id=b["req_id"], btch_id=b["btch_id"], actor=closed_by,
                                        entry_ty="AUTO" if closed_by == "SYSTEM" else "MANUAL",
                                        detail=f"{to_stat} / {resolution}")
                if resolution == "MISSING":
                    self.logger.audit("SOURCE_MISSING_AT_CLOSE", req_id=b["req_id"], btch_id=b["btch_id"],
                                      extract_id=e["extract_id"], project_cd=b["project_cd"], table_nm=b["table_nm"],
                                      src_cd=b["src_cd"], run_ty=b["run_ty"])
                closed += 1
            row = self.conn.execute(
                """UPDATE ComplianceExtractControl SET Extract_Close_Ind=1, Extract_Closed_Dtts=%s, Closed_By=%s,
                          Close_Warning_Txt=%s, Closed_Data_Signature=Data_Signature, Regenerate_Required_Ind=0,
                          Updated_Dtts=%s WHERE Extract_ID=%s RETURNING *""",
                (now, closed_by, "; ".join(warnings) or None, now, e["extract_id"])).fetchone()
            event = "EXTRACT_CLOSED_WITH_WARNINGS" if warnings else "EXTRACT_CLOSED"
            self.logger.audit(event, actor=closed_by, extract_id=e["extract_id"], project_cd=e["project_cd"],
                              table_nm=e["table_nm"], run_ty=e["run_ty"],
                              description=f"closed={closed} batches={row['combine_btch_id_list'] or ''}"
                                          + (f" warnings: {'; '.join(warnings)}" if warnings else ""))
        return CloseOutcome(e["extract_id"], closed, warnings, row["combine_btch_id_list"], ack)


@dataclass
class CloseOutcome:
    extract_id: int
    closed_batches: int
    warnings: tuple[str, ...]
    btch_id_list: Optional[str]
    warnings_acknowledged: bool


# ============================================================================ SLA sweep (P10, D-40)
@dataclass
class EvaluationSummary:
    evaluated: int = 0
    closed: list[int] = field(default_factory=list)
    not_eligible: int = 0
    deferred: list[int] = field(default_factory=list)
    regenerate_required: list[int] = field(default_factory=list)


class ExtractEvaluator:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings, control: ExtractControlService):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.control = control

    def run(self, project_cd: Optional[str] = None, table_nm: Optional[str] = None,
            run_ty: Optional[str] = None) -> EvaluationSummary:
        """Sweep the open extracts in the job's scope (a project, optionally one table / run type).

        The SLA hold is `Req_Dt_Key + (SLA_Days - 1)` of the run type, joined here rather than stored.
        """
        s = EvaluationSummary()
        today = self.clock.today(self.settings.business_tz)
        rows = self.conn.execute(
            """SELECT e.Extract_ID FROM ComplianceExtractControl e
                 JOIN ComplianceRunType r ON r.Run_Ty = e.Run_Ty
                WHERE e.Extract_Close_Ind = 0
                  AND (e.Req_Dt_Key + (r.SLA_Days - 1)) <= %s
                  AND (%s::text IS NULL OR e.Project_Cd = %s) AND (%s::text IS NULL OR e.Table_Nm = %s)
                  AND (%s::text IS NULL OR e.Run_Ty = %s)
                ORDER BY e.Extract_ID""",
            (today, project_cd, project_cd, table_nm, table_nm, run_ty, run_ty)).fetchall()
        for r in rows:
            s.evaluated += 1
            self._attempt(r["extract_id"], s)
        s.regenerate_required = [r["extract_id"] for r in self.conn.execute(
            """SELECT Extract_ID FROM ComplianceExtractControl
                WHERE Regenerate_Required_Ind = 1
                  AND (%s::text IS NULL OR Project_Cd = %s) AND (%s::text IS NULL OR Table_Nm = %s)
                  AND (%s::text IS NULL OR Run_Ty = %s) ORDER BY Extract_ID""",
            (project_cd, project_cd, table_nm, table_nm, run_ty, run_ty)).fetchall()]
        return s

    def _attempt(self, extract_id: int, s: EvaluationSummary) -> None:
        try:
            if not self.settings.auto_close_extracts:
                self.control.refresh(extract_id, "SLA_EVALUATION")
                s.not_eligible += 1
                return
            self.control.close(extract_id, "SYSTEM", automatic=True)
            s.closed.append(extract_id)
        except CloseBlocked:
            s.not_eligible += 1
        except (CloseDeferred, LockTimeout):
            s.deferred.append(extract_id)

    def after_late_promotion(self, extract_id: int) -> None:
        """A late arrival or correction was promoted into a closed batch: recombine so the extract's
        batch list and signature are current, and flag the run for regeneration (D-41)."""
        self.control.refresh(extract_id, "LATE_PROMOTION")
