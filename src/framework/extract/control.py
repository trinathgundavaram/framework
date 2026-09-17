"""Extract control: recount, combine + period rules, eligibility (design §7 P9, §10.4, §11.2)."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

import psycopg
from psycopg import sql

from .. import locks
from ..audit.event_logger import EventLogger
from ..batches.crc_repository import batches_of_extract
from ..clock import Clock
from ..config import repository as repo
from ..config.models import ExtractPolicy
from ..settings import Settings
from ..validation.gre_adapter import ERROR, FAILED, PASSED, RuleEngine
from . import eligibility as elig

log = logging.getLogger(__name__)

COMBINE_TRIGGERS = ("EARLY_COMPLETE", "SLA_EVALUATION", "MANUAL_TRIGGER", "REOPEN_PROMOTED",
                    "WAIVER_DECISION", "MANUAL_REFRESH")


@dataclass
class ExtractState:
    extract: dict
    eligibility: elig.Eligibility
    btch_ids: list[str]
    load_ids: list[int]
    policy: Optional[ExtractPolicy]


def data_signature(pairs: list[tuple[str, int]]) -> str:
    text = "|".join(f"{b}:{l}" for b, l in sorted(pairs))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def combined_rows(conn: psycopg.Connection, extract: dict, core_schema: str, limit: Optional[int] = None) -> list[dict]:
    """§10.4 combine query - current core rows of every received batch in the extract."""
    q = sql.SQL("""SELECT c.* FROM ComplianceRequestControl r
                    JOIN {core} c ON c.btch_id = r.Btch_ID AND c.current_ind = 1
                   WHERE r.Extract_ID = %s AND r.Resolution_Ty = 'NEW_FILE'""").format(
        core=sql.Identifier(core_schema.lower(), extract["table_nm"].lower()))
    if limit:
        q = q + sql.SQL(" LIMIT {}").format(sql.Literal(limit))
    return conn.execute(q, (extract["extract_id"],)).fetchall()


class ExtractControlService:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings, rule_engine: RuleEngine):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.rules = rule_engine
        self.logger = EventLogger(conn, clock)

    def refresh(self, extract_id: int, trigger_cd: str) -> ExtractState:
        with locks.held(self.conn, locks.extract_key(extract_id), self.settings.lock_timeout_seconds):
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
            policy = repo.extract_policy(self.conn, e["project_cd"], e["table_nm"], e["run_ty"])
            gating = policy.gating_md if policy else elig.STRICT
            period_mode = policy.period_rules_vld_md if policy else "GATE"
            batches = batches_of_extract(self.conn, extract_id)
            received = [b for b in batches if b["resolution_ty"] == "NEW_FILE"]
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
            waived_src = sorted(b["src_cd"] for b in batches if b["req_id"] in waived_req)
            missing = sorted(b["src_cd"] for b in batches if b["req_id"] not in received_ids | waived_req)
            pairs = [(b["btch_id"], b["current_load_id"]) for b in received]
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
                rules_stat, failed = self._period_rules(e, policy, period_mode, btch_ids, load_ids)
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

            tz = repo.business_tz_for_extract(self.conn, e["project_cd"], e["table_nm"], e["run_ty"])
            el = elig.compute(elig.EligibilityInput(
                gating_md=gating, required=required, received=len(received), waived=len(waived_req),
                rules_stat=rules_stat, failed_rules=failed, waived_rules=waived_rules,
                today=self.clock.today(tz), earliest_trigger_dt=e["earliest_trigger_dt"]),
                strict_waiver_auto=self.settings.strict_waiver_auto_trigger)
            if el.code != e["eligibility_cd"]:
                self.logger.audit("EXTRACT_ELIGIBILITY_CHANGED", extract_id=extract_id, project_cd=e["project_cd"],
                                  table_nm=e["table_nm"], run_ty=e["run_ty"],
                                  description=f"{e['eligibility_cd']} -> {el.code}: {el.reason}")
            reason = el.reason + (" | warnings: " + "; ".join(el.warnings) if el.warnings else "")
            row = self.conn.execute(
                """UPDATE ComplianceExtractControl SET
                      Received_Src_Cnt=%s, Waived_Src_Cnt=%s, Included_Src_Cds=%s, Missing_Src_Cds=%s,
                      Waived_Src_Cds=%s, Extract_Stat=%s, Extract_Rules_Stat=%s, Failed_Rule_Refs=%s,
                      Eligibility_Cd=%s, Eligibility_Rsn_Txt=%s, Retrigger_Required_Ind=%s,
                      Combine_Run_Cnt=%s,
                      Combine_Last_Run_Dtts = CASE WHEN %s THEN %s ELSE Combine_Last_Run_Dtts END,
                      Combine_Last_Trigger_Cd = CASE WHEN %s THEN %s ELSE Combine_Last_Trigger_Cd END,
                      Combine_Btch_ID_List = CASE WHEN %s THEN %s ELSE Combine_Btch_ID_List END,
                      Data_Signature = CASE WHEN %s THEN %s ELSE Data_Signature END,
                      Updated_Dtts=%s
                    WHERE Extract_ID=%s RETURNING *""",
                (len(received), len(waived_req), ",".join(included), ",".join(missing), ",".join(waived_src),
                 extract_stat, rules_stat, ",".join(failed) or None, el.code, reason[:1000], retrigger,
                 combine_cnt, combined, now, combined, trigger_cd, combined, ",".join(btch_ids),
                 combined, signature, now, extract_id)).fetchone()
        return ExtractState(row, el, btch_ids, load_ids, policy)

    def _period_rules(self, e: dict, policy: Optional[ExtractPolicy], mode: str, btch_ids: list[str],
                      load_ids: list[int]) -> tuple[str, tuple[str, ...]]:
        if not btch_ids:
            return PASSED, ()             # nothing to validate (zero data - see D-49)
        bindings = repo.rule_bindings(self.conn, e["project_cd"], e["table_nm"], "*", "PERIOD_LEVEL")
        cfg = repo.file_config(self.conn, e["project_cd"], e["table_nm"],
                               self.conn.execute("SELECT Src_Cd FROM ComplianceRequestControl WHERE Btch_ID=%s",
                                                 (btch_ids[0],)).fetchone()["src_cd"])
        outcome = self.rules.run(self.conn, bindings, {
            "scope": "PERIOD_LEVEL", "extract_id": e["extract_id"], "project_cd": e["project_cd"],
            "table_nm": e["table_nm"], "run_ty": e["run_ty"], "rpt_start_dt_key": e["rpt_start_dt_key"],
            "rpt_end_dt_key": e["rpt_end_dt_key"], "btch_id_list": list(btch_ids), "load_id_list": list(load_ids),
            "core_schema_nm": cfg.core_schema_nm if cfg else None, "core_tblnm": e["table_nm"]}, mode)
        ctx = dict(extract_id=e["extract_id"], project_cd=e["project_cd"], table_nm=e["table_nm"], run_ty=e["run_ty"])
        if outcome.status == ERROR:
            self.logger.audit("RULES_ENGINE_TECHNICAL_FAILURE", description=f"period rules: {outcome.error}"[:900], **ctx)
            return ERROR, ()
        if outcome.status == FAILED:
            self.logger.audit("PERIOD_RULES_FAILED", **ctx,
                              description=f"failed: {', '.join(outcome.failed_rules)}; "
                                          f"contributing batches: {', '.join(btch_ids)}"[:2000])
        return outcome.status, tuple(outcome.failed_rules)
