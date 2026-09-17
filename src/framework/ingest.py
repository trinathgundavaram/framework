"""File ingest pipeline (design §7 P5, §8, §9.4).

One call processes one S3 object end to end. Business outcomes (quarantine, rules failure, late
arrival) are returned; technical failures are raised after the load is marked FAILED_TECHNICAL so the
orchestrator can retry (the replay restarts the same Load_ID - C0).

Batch selection (D-78): a filename carries the report period but not the run date, so the file is
matched to the **open** batch of its (project, table, source, run type, report period) with the
latest run date. When every batch of that grain is closed, the file is promoted only if an approved,
still-valid override exists for it (LATE_ARRIVAL for a batch with no data, CORRECTION for one that
has data); otherwise it is quarantined so a person can decide.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

import psycopg

from . import config as cfgmod
from . import db
from .adapters import ERROR, ObjectInfo, ObjectStore, RuleEngine, basename, dirname, parse_uri, sha256_file
from .audit import EventLogger
from .batches import get_batch, promoted_load
from .common import (COMPLETED, EXCEPTION_PENDING, PROMOTED, Clock, FileRejected, RowCountMismatch,
                     TechnicalFailure, check_transition)
from .config import FileConfig, MatchError, TemplateMatcher
from .load import sanitize_db_error, stage, swap
from .settings import Settings

log = logging.getLogger(__name__)

TERMINAL_LOAD_STATS = {"QUARANTINED", "RULES_FAILED", "PROMOTED", "SUPERSEDED"}
ARCHIVE_LOAD_STATS = {"RULES_FAILED", "PROMOTED", "SUPERSEDED"}


# ============================================================================ decision tables (§8), pure
class Action(str, Enum):
    PROMOTE = "PROMOTE"                              # O-1: open batch, no data yet
    PROMOTE_REPLACE = "PROMOTE_REPLACE"              # O-2: open batch, replaces its current data
    EXCEPTION_NO_DATA = "EXCEPTION_NO_DATA"          # O-3: open batch, rules failed, no prior data
    EXCEPTION_KEEP_PRIOR = "EXCEPTION_KEEP_PRIOR"    # O-4: open batch, rules failed, prior data kept
    PROMOTE_LATE = "PROMOTE_LATE"                    # C-1: closed batch with no data + LATE_ARRIVAL
    PROMOTE_CORRECTION = "PROMOTE_CORRECTION"        # C-2: closed batch with data + CORRECTION
    REJECT_CLOSED = "REJECT_CLOSED"                  # C-3: closed batch, no approved override
    REJECT_RULES = "REJECT_RULES"                    # C-4: closed batch, file failed its rules


@dataclass(frozen=True)
class ResolutionInput:
    batch_closed: bool
    has_data: bool                    # NEW_FILE promoted load, or an applied CARRY_FORWARD
    file_passed: bool                 # FILE_LEVEL rules passed (incl. warnings) and the zero-record rule is met
    override_ty: Optional[str] = None  # approved, still-valid override for this batch (LATE_ARRIVAL / CORRECTION)


@dataclass(frozen=True)
class ResolutionDecision:
    action: Action
    rule: str


def decide(inp: ResolutionInput) -> ResolutionDecision:
    if not inp.batch_closed:
        if inp.file_passed:
            return ResolutionDecision(Action.PROMOTE_REPLACE, "O-2") if inp.has_data \
                else ResolutionDecision(Action.PROMOTE, "O-1")
        return ResolutionDecision(Action.EXCEPTION_KEEP_PRIOR, "O-4") if inp.has_data \
            else ResolutionDecision(Action.EXCEPTION_NO_DATA, "O-3")
    if not inp.file_passed:
        return ResolutionDecision(Action.REJECT_RULES, "C-4")
    if inp.has_data and inp.override_ty == "CORRECTION":
        return ResolutionDecision(Action.PROMOTE_CORRECTION, "C-2")
    if not inp.has_data and inp.override_ty == "LATE_ARRIVAL":
        return ResolutionDecision(Action.PROMOTE_LATE, "C-1")
    return ResolutionDecision(Action.REJECT_CLOSED, "C-3")


def required_override_ty(has_data: bool) -> str:
    return "CORRECTION" if has_data else "LATE_ARRIVAL"


# ============================================================================ pipeline
@dataclass
class IngestOutcome:
    load_id: int
    result: str
    rule: Optional[str] = None
    event_ty: Optional[str] = None
    req_id: Optional[int] = None
    extract_id: Optional[int] = None
    ovrd_id: Optional[int] = None
    message: Optional[str] = None


def s3_ref(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


class IngestPipeline:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings, store: ObjectStore,
                 rule_engine: RuleEngine, on_promoted: Optional[Callable[[int], None]] = None,
                 on_late_promotion: Optional[Callable[[int], None]] = None, spark=None):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.store = store
        self.rules = rule_engine
        self.on_promoted = on_promoted                  # extract refresh hook (early completion, D-21)
        self.on_late_promotion = on_late_promotion      # refresh + regenerate flag for a closed extract
        self.spark = spark
        self.logger = EventLogger(conn, clock)

    # ------------------------------------------------------------------ entry point
    def process_file(self, bucket: str, key: str, version_id: Optional[str] = None) -> IngestOutcome:
        info = self.store.head(bucket, key, version_id)
        load, is_new = self._register(info)
        if not is_new:
            if load["load_stat"] in TERMINAL_LOAD_STATS and not self._retryable_quarantine(load):
                return self._replay(load, info)
            if load["btch_id"]:
                if not db.try_lock(self.conn, db.batch_key(load["btch_id"])):
                    return IngestOutcome(load["load_id"], "IN_PROGRESS", message="another process holds the batch lock")
                db.unlock(self.conn, db.batch_key(load["btch_id"]))
            log.info("restarting non-terminal load %s (%s)", load["load_id"], load["load_stat"])
        try:
            return self._process(load["load_id"], info)
        except Exception as e:
            self._mark_technical_failure(load["load_id"], e)
            raise

    @staticmethod
    def _retryable_quarantine(load: dict) -> bool:
        """A file quarantined only because its batch was closed is reprocessed when it is delivered
        again: by then an approved LATE_ARRIVAL / CORRECTION override may exist (D-74)."""
        return load["load_stat"] == "QUARANTINED" and load["quarantine_rsn_cd"] == "FILE_REJECTED_BATCH_CLOSED"

    # ------------------------------------------------------------------ steps
    def _register(self, info: ObjectInfo) -> tuple[dict, bool]:
        with self.conn.transaction():
            row = self.conn.execute(
                """INSERT INTO ComplianceFileLoad (S3_Bucket, S3_Key, S3_Version_Id, S3_ETag, File_Size_Byte,
                                                  Received_Dtts, Load_Stat, Created_Dtts, Updated_Dtts)
                   VALUES (%s,%s,%s,%s,%s,%s,'RECEIVED',%s,%s)
                   ON CONFLICT (S3_Bucket, S3_Key, COALESCE(S3_Version_Id, S3_ETag)) DO NOTHING
                   RETURNING *""",
                (info.bucket, info.key, info.version_id, info.etag, info.size, self.clock.now(),
                 self.clock.now(), self.clock.now())).fetchone()
            if row:
                return row, True
            row = self.conn.execute(
                """SELECT * FROM ComplianceFileLoad WHERE S3_Bucket=%s AND S3_Key=%s
                      AND COALESCE(S3_Version_Id, S3_ETag) = COALESCE(%s, %s)""",
                (info.bucket, info.key, info.version_id, info.etag)).fetchone()
            return row, False

    def _replay(self, load: dict, info: ObjectInfo) -> IngestOutcome:
        with self.conn.transaction():
            self.logger.audit("FILE_EVENT_REPLAY_IGNORED", load_id=load["load_id"], req_id=load["req_id"],
                              btch_id=load["btch_id"], file_ref=s3_ref(info.bucket, info.key),
                              description=f"load already {load['load_stat']}")
        # finish an interrupted archive/quarantine move (E-27)
        if self.store.exists(info.bucket, info.key):
            cfg = cfgmod.file_config_by_id(self.conn, load["cfg_id"]) if load["cfg_id"] else None
            if load["load_stat"] == "QUARANTINED":
                self._move(info, (cfg.s3_quarantine_path if cfg else self.settings.default_quarantine_uri),
                           f"{load['quarantine_rsn_cd']}/", load["load_id"])
            elif cfg and load["load_stat"] in ARCHIVE_LOAD_STATS:
                self._move(info, cfg.src_file_archive_path, "", load["load_id"])
        return IngestOutcome(load["load_id"], "REPLAY_IGNORED", req_id=load["req_id"])

    def _process(self, load_id: int, info: ObjectInfo) -> IngestOutcome:
        name = basename(info.key)
        matcher = TemplateMatcher(cfgmod.active_file_configs(self.conn), self.settings.filename_case_sensitive)
        try:
            m = matcher.match(name)
        except MatchError as e:
            return self._quarantine(load_id, info, e.event_ty, str(e), e.cfg)
        cfg = m.cfg
        bucket, prefix = parse_uri(cfg.s3_src_file_path)
        if info.bucket != bucket or dirname(info.key) != prefix:
            return self._quarantine(load_id, info, "FILE_REJECTED_UNPARSEABLE",
                                    f"{name} matched Cfg_ID {cfg.cfg_id} but is not in its inbound location", cfg)
        run_ty = self._resolve_run_type(m.run_ty)
        ref_date = m.rpt_start if self.settings.file_effective_date_basis == "RPT_START" else m.rpt_end
        x = cfgmod.effective_xwalk(self.conn, cfg.project_cd, cfg.table_nm, cfg.src_cd, run_ty, ref_date) if run_ty else None
        if x is None:
            return self._quarantine(load_id, info, "FILE_REJECTED_RUNTY_NOT_CONFIGURED",
                                    f"run type {m.run_ty!r} is not configured/effective for "
                                    f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_cd} on {ref_date}", cfg)
        batch, override = self._select_batch(cfg, run_ty, m.rpt_start, m.rpt_end)
        if batch is None:
            event = "FILE_REJECTED_BATCH_CLOSED" if override == "CLOSED" else "FILE_REJECTED_NO_BATCH"
            detail = ("every batch for this period is closed and no approved, valid override exists"
                      if override == "CLOSED" else "no batch exists for this period")
            return self._quarantine(load_id, info, event,
                                    f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_cd}/{run_ty} "
                                    f"{m.rpt_start}..{m.rpt_end}: {detail}", cfg)
        with self.conn.transaction():
            self.conn.execute(
                """UPDATE ComplianceFileLoad SET Cfg_ID=%s, Req_ID=%s, Btch_ID=%s,
                          Parsed_Project_Alias=%s, Parsed_Table_Alias=%s, Parsed_Src_Alias=%s, Parsed_Run_Ty=%s,
                          Parsed_Rpt_Start_Dt_Key=%s, Parsed_Rpt_End_Dt_Key=%s, Parsed_File_Ts=%s,
                          Load_Stat='STAGING', Attempt_Cnt=Attempt_Cnt+1, Heartbeat_Dtts=%s, Error_Txt=NULL,
                          Updated_Dtts=%s
                    WHERE Load_ID=%s""",
                (cfg.cfg_id, batch["req_id"], batch["btch_id"], cfg.project_alias, cfg.table_alias,
                 cfg.src_alias, run_ty, m.rpt_start, m.rpt_end, m.file_ts, self.clock.now(), self.clock.now(),
                 load_id))
        if info.size == 0 and cfg.has_header:                                     # C1
            return self._quarantine(load_id, info, "FILE_PARSE_ERROR", "zero-byte file but a header is expected",
                                    cfg, batch)
        with db.held(self.conn, db.batch_key(batch["btch_id"]), self.settings.lock_timeout_seconds):
            outcome = self._process_locked(load_id, info, cfg, batch["req_id"])
        if outcome.result == "PROMOTED" and self.on_promoted:
            self.on_promoted(outcome.extract_id)
        elif outcome.result in ("LATE_PROMOTED", "CORRECTION_PROMOTED") and self.on_late_promotion:
            self.on_late_promotion(outcome.extract_id)
        return outcome

    def _resolve_run_type(self, token: str) -> Optional[str]:
        codes = cfgmod.run_types(self.conn)
        if token in codes:
            return token
        if not self.settings.filename_case_sensitive:
            for c in codes:
                if c.lower() == token.lower():
                    return c
        return None

    def _select_batch(self, cfg: FileConfig, run_ty: str, rpt_start, rpt_end) -> tuple[Optional[dict], Optional[str]]:
        """The batch a file belongs to (D-78). Returns (batch, override type) - the override type is
        'CLOSED' when only closed batches exist and none of them may accept the file."""
        rows = self.conn.execute(
            """SELECT * FROM ComplianceRequestControl
                WHERE Project_Cd=%s AND Table_Nm=%s AND Src_Cd=%s AND Run_Ty=%s
                  AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s
                ORDER BY Req_Dt_Key DESC, Req_ID DESC""",
            (cfg.project_cd, cfg.table_nm, cfg.src_cd, run_ty, rpt_start, rpt_end)).fetchall()
        if not rows:
            return None, None
        today = self.clock.today(self.settings.business_tz)
        open_rows = [r for r in rows if r["batch_close_ind"] == 0]
        if open_rows:
            return next((r for r in open_rows if r["req_dt_key"] <= today), open_rows[-1]), None
        for r in rows:                                   # closed: an approved, valid override may accept it
            ovrd = self.active_override(r, required_override_ty(self._has_data(r)), today)
            if ovrd:
                return r, ovrd["override_ty"]
        return None, "CLOSED"

    def active_override(self, batch: dict, override_ty: str, today) -> Optional[dict]:
        return self.conn.execute(
            """SELECT * FROM ComplianceBatchOverride
                WHERE Req_ID=%s AND Override_Ty=%s AND Apprvl_Stat='APPROVED' AND Valid_Thru_Dt_Key >= %s""",
            (batch["req_id"], override_ty, today)).fetchone()

    def _has_data(self, batch: dict) -> bool:
        return batch["resolution_ty"] == "CARRY_FORWARD" or promoted_load(self.conn, batch["btch_id"]) is not None

    def _process_locked(self, load_id: int, info: ObjectInfo, cfg: FileConfig, req_id: int) -> IngestOutcome:
        batch = get_batch(self.conn, req_id)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, basename(info.key))
            self.store.download(info.bucket, info.key, path, info.version_id)
            sha = sha256_file(path)
            with self.conn.transaction():
                self.conn.execute("UPDATE ComplianceFileLoad SET File_Sha256=%s, Updated_Dtts=%s WHERE Load_ID=%s",
                                  (sha, self.clock.now(), load_id))
            current = promoted_load(self.conn, batch["btch_id"])                     # C11
            if current and current["file_sha256"] == sha and current["load_id"] != load_id:
                return self._quarantine(load_id, info, "FILE_REJECTED_DUPLICATE",
                                        f"identical to load {current['load_id']} of batch {batch['btch_id']}",
                                        cfg, batch)
            other = self.conn.execute(
                """SELECT Load_ID, Btch_ID FROM ComplianceFileLoad WHERE File_Sha256=%s AND Btch_ID<>%s
                      AND Load_Stat <> 'QUARANTINED' LIMIT 1""", (sha, batch["btch_id"])).fetchone()
            if other:
                with self.conn.transaction():
                    self.logger.audit("FILE_SAME_CONTENT_OTHER_BATCH", load_id=load_id, req_id=req_id,
                                      btch_id=batch["btch_id"], file_ref=s3_ref(info.bucket, info.key),
                                      project_cd=cfg.project_cd, table_nm=cfg.table_nm, src_cd=cfg.src_cd,
                                      run_ty=batch["run_ty"],
                                      description=f"same content as load {other['load_id']} ({other['btch_id']})")
            try:
                staged = stage(self.conn, self.settings, file_path=path, cfg=cfg, btch_id=batch["btch_id"],
                               load_id=load_id, src_file_nm=basename(info.key), loaded_at=self.clock.now(),
                               spark=self.spark)
            except FileRejected as e:
                return self._quarantine(load_id, info, e.event_ty, str(e), cfg, batch)
        with self.conn.transaction():
            self.conn.execute(
                """UPDATE ComplianceFileLoad SET Load_Stat='STAGED', Src_Rcd_Cnt=%s, Trlr_Rcd_Cnt=%s, Stg_Rcd_Cnt=%s,
                          Heartbeat_Dtts=%s, Updated_Dtts=%s WHERE Load_ID=%s""",
                (staged.data_rows, staged.trailer_count, staged.data_rows, self.clock.now(), self.clock.now(),
                 load_id))

        failure_event = None
        detail = None
        rules_stat = "NOT_RUN"
        if staged.data_rows == 0 and not cfg.allow_zero_records:                      # D-60
            passed, failure_event, detail = False, "FILE_ZERO_RECORDS_REJECTED", "file has no data rows"
        elif bindings := cfgmod.rule_bindings(self.conn, cfg.project_cd, cfg.table_nm, cfg.src_cd, "FILE_LEVEL"):
            with self.conn.transaction():
                self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='RULES_RUNNING', Heartbeat_Dtts=%s "
                                  "WHERE Load_ID=%s", (self.clock.now(), load_id))
            outcome = self.rules.run(self.conn, bindings, {
                "scope": "FILE_LEVEL", "btch_id": batch["btch_id"], "load_id": load_id,
                "project_cd": cfg.project_cd, "table_nm": cfg.table_nm, "src_cd": cfg.src_cd,
                "run_ty": batch["run_ty"], "rpt_start_dt_key": batch["rpt_start_dt_key"],
                "rpt_end_dt_key": batch["rpt_end_dt_key"], "req_dt_key": batch["req_dt_key"],
                "stg_schema_nm": cfg.stg_schema_nm, "stg_tblnm": cfg.stg_tblnm}, self.settings.file_rules_mode)
            if outcome.status == ERROR:
                with self.conn.transaction():
                    self.conn.execute("UPDATE ComplianceFileLoad SET Rules_Stat='ERROR' WHERE Load_ID=%s", (load_id,))
                    self.logger.audit("RULES_ENGINE_TECHNICAL_FAILURE", load_id=load_id, req_id=req_id,
                                      btch_id=batch["btch_id"], description=sanitize_db_error(outcome.error or ""))
                raise TechnicalFailure(f"rules engine failed for load {load_id}: {outcome.error}")
            passed, rules_stat = outcome.passed, outcome.status
            if not passed:
                failure_event, detail = "RULES_VALIDATION_FAILED", "failed GATE rules: " + ", ".join(outcome.failed_rules)
            elif outcome.warned_rules:
                detail = "ANNOTATE warnings: " + ", ".join(outcome.warned_rules)
        else:
            passed = True

        result = self._resolve(load_id, info, cfg, req_id, passed, rules_stat, failure_event, detail)
        self._move(info, cfg.src_file_archive_path, "", load_id)
        return result

    # ------------------------------------------------------------------ resolution (§8)
    def _resolve(self, load_id, info, cfg, req_id, passed, rules_stat, failure_event, detail) -> IngestOutcome:
        now = self.clock.now()
        ref = s3_ref(info.bucket, info.key)
        today = self.clock.today(self.settings.business_tz)
        with self.conn.transaction():
            b = get_batch(self.conn, req_id, for_update=True)
            closed = b["batch_close_ind"] == 1
            has_data = self._has_data(b)
            ovrd = self.active_override(b, required_override_ty(has_data), today) if closed else None
            d = decide(ResolutionInput(batch_closed=closed, has_data=has_data, file_passed=passed,
                                       override_ty=ovrd["override_ty"] if ovrd else None))
            ctx = dict(project_cd=b["project_cd"], table_nm=b["table_nm"], src_cd=b["src_cd"], run_ty=b["run_ty"],
                       req_id=req_id, btch_id=b["btch_id"], load_id=load_id, extract_id=b["extract_id"], file_ref=ref)
            out = IngestOutcome(load_id, "", d.rule, req_id=req_id, extract_id=b["extract_id"],
                                ovrd_id=ovrd["ovrd_id"] if ovrd else None)
            self.logger.batch_event("FILE_RECEIVED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                    file_ref=ref, detail=f"rule={d.rule}")

            if d.action in (Action.PROMOTE, Action.PROMOTE_REPLACE, Action.PROMOTE_LATE, Action.PROMOTE_CORRECTION):
                to_stat = COMPLETED if closed else PROMOTED
                check_transition(b["req_stat"], to_stat)
                staged_cnt = self.conn.execute("SELECT Stg_Rcd_Cnt FROM ComplianceFileLoad WHERE Load_ID=%s",
                                               (load_id,)).fetchone()["stg_rcd_cnt"]
                prior = promoted_load(self.conn, b["btch_id"])
                if prior and prior["load_id"] != load_id:
                    self._supersede(prior["load_id"])                 # before the new row becomes PROMOTED
                res = swap(self.conn, cfg, b["btch_id"], load_id, staged_cnt, now)   # same transaction
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Resolution_Ty='NEW_FILE', Req_Stat=%s, Reuse_Btch_ID=NULL,
                              Updated_Dtts=%s WHERE Req_ID=%s""", (to_stat, now, req_id))
                self.conn.execute(
                    """UPDATE ComplianceFileLoad SET Load_Stat='PROMOTED', Rules_Stat=%s, Core_Appended_Cnt=%s,
                              Core_Disabled_Cnt=%s, Promoted_Dtts=%s, Heartbeat_Dtts=NULL, Updated_Dtts=%s
                        WHERE Load_ID=%s""", (rules_stat, res.appended_cnt, res.disabled_cnt, now, now, load_id))
                if d.action == Action.PROMOTE_REPLACE:
                    self.logger.batch_event(
                        "FILE_REPLACED_BEFORE_CLOSE", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                        detail=(f"replaces load {prior['load_id']}" if prior
                                else f"replaces carried data of {b['reuse_btch_id']}"))
                if b["resolution_ty"] == "CARRY_FORWARD":
                    self._end_carry_forward(b, load_id, now)
                promoted_event = {Action.PROMOTE_LATE: "LATE_ARRIVAL_PROMOTED",
                                  Action.PROMOTE_CORRECTION: "CORRECTION_PROMOTED"}.get(d.action, "FILE_PROMOTED")
                self.logger.batch_event(promoted_event, req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        ovrd_id=out.ovrd_id, entry_ty="MANUAL" if closed else "AUTO",
                                        detail=f"appended={res.appended_cnt} disabled={res.disabled_cnt}"
                                               + (f"; {detail}" if detail else ""))
                out.result = {Action.PROMOTE_LATE: "LATE_PROMOTED",
                              Action.PROMOTE_CORRECTION: "CORRECTION_PROMOTED"}.get(d.action, "PROMOTED")

            elif d.action in (Action.EXCEPTION_NO_DATA, Action.EXCEPTION_KEEP_PRIOR):
                check_transition(b["req_stat"], EXCEPTION_PENDING)
                self.conn.execute("UPDATE ComplianceRequestControl SET Req_Stat=%s, Updated_Dtts=%s WHERE Req_ID=%s",
                                  (EXCEPTION_PENDING, now, req_id))
                self._rules_failed(load_id, rules_stat, detail, now)
                self.logger.batch_event("FILE_RULES_FAILED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                self.logger.audit(failure_event, description=detail, **ctx)
                out.result, out.event_ty = "RULES_FAILED", failure_event

            elif d.action == Action.REJECT_RULES:                                      # closed batch, file failed
                self._rules_failed(load_id, rules_stat, detail, now)
                self.logger.batch_event("FILE_RULES_FAILED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                self.logger.audit(failure_event, description=f"closed batch: {detail}", **ctx)
                out.result, out.event_ty = "RULES_FAILED", failure_event

            else:                                                                      # REJECT_CLOSED
                self._rules_failed(load_id, rules_stat, "batch is closed and has no approved override", now)
                self.logger.audit("FILE_REJECTED_BATCH_CLOSED", description="no approved, valid override", **ctx)
                out.result, out.event_ty = "REJECTED_CLOSED", "FILE_REJECTED_BATCH_CLOSED"
        return out

    def _end_carry_forward(self, b: dict, load_id: int, now) -> None:
        """A real file replaced an applied carry-forward: stop the reuse and record it."""
        row = self.conn.execute(
            """UPDATE ComplianceBatchOverride SET Valid_Thru_Dt_Key=%s, Updated_Dtts=%s, Updated_By='SYSTEM',
                      Rsn = COALESCE(Rsn || ' | ', '') || %s
                WHERE Req_ID=%s AND Override_Ty='REUSE' AND Apprvl_Stat='APPROVED' RETURNING Ovrd_ID""",
            (b["req_dt_key"], now, f"superseded by file load {load_id}", b["req_id"])).fetchone()
        self.logger.batch_event("CARRY_FORWARD_REMOVED", req_id=b["req_id"], btch_id=b["btch_id"], load_id=load_id,
                                ovrd_id=row["ovrd_id"] if row else None,
                                detail=f"reuse of {b['reuse_btch_id']} replaced by load {load_id}")

    def _supersede(self, load_id: Optional[int]) -> None:
        if load_id:
            self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='SUPERSEDED', Updated_Dtts=%s "
                              "WHERE Load_ID=%s AND Load_Stat='PROMOTED'", (self.clock.now(), load_id))

    def _rules_failed(self, load_id, rules_stat, detail, now) -> None:
        self.conn.execute(
            """UPDATE ComplianceFileLoad SET Load_Stat='RULES_FAILED', Rules_Stat=%s, Error_Txt=%s,
                      Heartbeat_Dtts=NULL, Updated_Dtts=%s WHERE Load_ID=%s""",
            (rules_stat, detail, now, load_id))

    # ------------------------------------------------------------------ quarantine / move / failure
    def _quarantine(self, load_id: int, info: ObjectInfo, event_ty: str, message: str,
                    cfg: Optional[FileConfig] = None, batch: Optional[dict] = None) -> IngestOutcome:
        with self.conn.transaction():
            self.conn.execute(
                """UPDATE ComplianceFileLoad SET Load_Stat='QUARANTINED', Quarantine_Rsn_Cd=%s, Error_Txt=%s,
                          Cfg_ID=COALESCE(%s, Cfg_ID), Heartbeat_Dtts=NULL, Updated_Dtts=%s WHERE Load_ID=%s""",
                (event_ty, message, cfg.cfg_id if cfg else None, self.clock.now(), load_id))
            self.logger.audit(
                event_ty, load_id=load_id, file_ref=s3_ref(info.bucket, info.key), description=message,
                project_cd=cfg.project_cd if cfg else None, table_nm=cfg.table_nm if cfg else None,
                src_cd=cfg.src_cd if cfg else None, run_ty=batch["run_ty"] if batch else None,
                req_id=batch["req_id"] if batch else None, btch_id=batch["btch_id"] if batch else None,
                extract_id=batch["extract_id"] if batch else None)
        dest = cfg.s3_quarantine_path if cfg else self.settings.default_quarantine_uri
        self._move(info, dest, f"{event_ty}/", load_id)
        return IngestOutcome(load_id, "QUARANTINED", event_ty=event_ty, message=message,
                             req_id=batch["req_id"] if batch else None)

    def _move(self, info: ObjectInfo, dest_uri: str, sub_prefix: str, load_id: int) -> None:
        try:
            self.store.move(info.bucket, info.key, dest_uri, info.version_id, sub_prefix)
        except Exception as e:  # noqa: BLE001 - retried on the next event replay (C0)
            log.warning("move of %s failed: %s", info.key, e)
            with self.conn.transaction():
                self.logger.audit("FILE_MOVE_FAILED", load_id=load_id, file_ref=s3_ref(info.bucket, info.key),
                                  description=f"{type(e).__name__} moving to {dest_uri}{sub_prefix}")

    def _mark_technical_failure(self, load_id: int, err: Exception) -> None:
        try:
            with self.conn.transaction():
                self.conn.execute(
                    """UPDATE ComplianceFileLoad SET Load_Stat='FAILED_TECHNICAL', Error_Txt=%s, Heartbeat_Dtts=NULL,
                              Updated_Dtts=%s WHERE Load_ID=%s AND Load_Stat <> ALL(%s)""",
                    (sanitize_db_error(f"{type(err).__name__}: {err}"), self.clock.now(), load_id,
                     list(TERMINAL_LOAD_STATS)))
                if isinstance(err, RowCountMismatch):
                    self.logger.audit("CORE_LOAD_ROWCOUNT_MISMATCH", load_id=load_id, description=str(err))
        except Exception:  # noqa: BLE001
            log.exception("could not record technical failure for load %s", load_id)
