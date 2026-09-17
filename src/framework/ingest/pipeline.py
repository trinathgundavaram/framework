"""File ingest pipeline (design §7 P5, §8, §9.4).

One call processes one S3 object end to end. Business outcomes (quarantine, rules failure,
reopen request...) are returned; technical failures are raised after the load is marked
FAILED_TECHNICAL so the orchestrator can retry (the replay restarts the same Load_ID - C0).
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional

import psycopg

from .. import locks
from ..audit.event_logger import EventLogger
from ..batches.crc_repository import find_batch, get_batch
from ..clock import Clock
from ..common.status import S_EXCEPTION, S_PROMOTED, StatusModel
from ..config import repository as repo
from ..config.models import FileConfig
from ..config.templates import MatchError, TemplateMatcher
from ..errors import FileRejected, RowCountMismatch, TechnicalFailure
from ..ingest.file_reader import sanitize_db_error
from ..load import promoter
from ..load.engine import ExecutionEngine, build_engine
from ..load.tables import staging_business_columns
from ..settings import Settings
from ..storage.object_store import ObjectInfo, ObjectStore, basename, dirname, parse_uri, sha256_file
from ..validation.gre_adapter import ERROR, RuleEngine
from .resolution_engine import Action, ActiveReopen, ResolutionInput, decide

log = logging.getLogger(__name__)

TERMINAL_LOAD_STATS = {"QUARANTINED", "RULES_FAILED", "PENDING_APPROVAL", "PROMOTED", "SUPERSEDED"}
ARCHIVE_LOAD_STATS = {"RULES_FAILED", "PENDING_APPROVAL", "PROMOTED", "SUPERSEDED"}


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
                 rule_engine: RuleEngine,
                 engine_factory: Callable[[str, Settings], ExecutionEngine] = build_engine,
                 on_promoted: Optional[Callable[[int], None]] = None):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.store = store
        self.rules = rule_engine
        self.engine_factory = engine_factory
        self.on_promoted = on_promoted          # extract refresh hook (early completion, D-21)
        self.logger = EventLogger(conn, clock)
        self._status: Optional[StatusModel] = None

    @property
    def status(self) -> StatusModel:
        if self._status is None:
            self._status = StatusModel.load(self.conn)
        return self._status

    # ------------------------------------------------------------------ entry point
    def process_file(self, bucket: str, key: str, version_id: Optional[str] = None) -> IngestOutcome:
        info = self.store.head(bucket, key, version_id)
        load, is_new = self._register(info)
        if not is_new:
            if load["load_stat"] in TERMINAL_LOAD_STATS:
                return self._replay(load, info)
            if load["btch_id"]:
                if not locks.try_lock(self.conn, locks.batch_key(load["btch_id"])):
                    return IngestOutcome(load["load_id"], "IN_PROGRESS", message="another process holds the batch lock")
                locks.unlock(self.conn, locks.batch_key(load["btch_id"]))
            log.info("restarting non-terminal load %s (%s)", load["load_id"], load["load_stat"])
        try:
            return self._process(load["load_id"], info)
        except Exception as e:
            self._mark_technical_failure(load["load_id"], e)
            raise

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
            cfg = repo.file_config_by_id(self.conn, load["cfg_id"]) if load["cfg_id"] else None
            if load["load_stat"] == "QUARANTINED":
                self._move(info, (cfg.s3_quarantine_path if cfg else self.settings.default_quarantine_uri),
                           f"{load['quarantine_rsn_cd']}/", load["load_id"])
            elif cfg and load["load_stat"] in ARCHIVE_LOAD_STATS:
                self._move(info, cfg.src_file_archive_path, "", load["load_id"])
        return IngestOutcome(load["load_id"], "REPLAY_IGNORED", req_id=load["req_id"])

    def _process(self, load_id: int, info: ObjectInfo) -> IngestOutcome:
        name = basename(info.key)
        matcher = TemplateMatcher(repo.active_file_configs(self.conn), self.settings.filename_case_sensitive)
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
        x = repo.effective_xwalk(self.conn, cfg.project_cd, cfg.table_nm, cfg.src_cd, run_ty, ref_date) if run_ty else None
        if x is None:
            return self._quarantine(load_id, info, "FILE_REJECTED_RUNTY_NOT_CONFIGURED",
                                    f"run type {m.run_ty!r} is not configured/effective for "
                                    f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_cd} on {ref_date}", cfg)
        batch = find_batch(self.conn, cfg.project_cd, cfg.table_nm, cfg.src_cd, run_ty, m.rpt_start, m.rpt_end)
        if batch is None:
            return self._quarantine(load_id, info, "FILE_REJECTED_NO_BATCH",
                                    f"no batch for {cfg.project_cd}/{cfg.table_nm}/{cfg.src_cd}/{run_ty} "
                                    f"{m.rpt_start}..{m.rpt_end}", cfg)
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
        with locks.held(self.conn, locks.batch_key(batch["btch_id"]), self.settings.lock_timeout_seconds):
            outcome = self._process_locked(load_id, info, cfg, batch["req_id"])
        if outcome.result == "PROMOTED" and self.on_promoted:
            self.on_promoted(outcome.extract_id)
        return outcome

    def _resolve_run_type(self, token: str) -> Optional[str]:
        codes = repo.run_types(self.conn)
        if token in codes:
            return token
        if not self.settings.filename_case_sensitive:
            for c in codes:
                if c.lower() == token.lower():
                    return c
        return None

    def _process_locked(self, load_id: int, info: ObjectInfo, cfg: FileConfig, req_id: int) -> IngestOutcome:
        batch = get_batch(self.conn, req_id)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, basename(info.key))
            self.store.download(info.bucket, info.key, path, info.version_id)
            sha = sha256_file(path)
            with self.conn.transaction():
                self.conn.execute("UPDATE ComplianceFileLoad SET File_Sha256=%s, Updated_Dtts=%s WHERE Load_ID=%s",
                                  (sha, self.clock.now(), load_id))
            dup = self._duplicate_of(sha, batch, load_id)                            # C11
            if dup:
                return self._quarantine(load_id, info, "FILE_REJECTED_DUPLICATE",
                                        f"identical to load {dup} of batch {batch['btch_id']}", cfg, batch)
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
            stg_cols = staging_business_columns(self.conn, cfg.stg_schema_nm, cfg.stg_tblnm)
            engine = self.engine_factory(cfg.engine_cd, self.settings)
            try:
                staged = engine.load_to_staging(self.conn, file_path=path, cfg=cfg, stg_columns=stg_cols,
                                                btch_id=batch["btch_id"], load_id=load_id,
                                                src_file_nm=basename(info.key), loaded_at=self.clock.now())
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
        elif cfg.rules_required:
            with self.conn.transaction():
                self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='RULES_RUNNING', Heartbeat_Dtts=%s "
                                  "WHERE Load_ID=%s", (self.clock.now(), load_id))
            bindings = repo.rule_bindings(self.conn, cfg.project_cd, cfg.table_nm, cfg.src_cd, "FILE_LEVEL")
            outcome = self.rules.run(self.conn, bindings, {
                "scope": "FILE_LEVEL", "btch_id": batch["btch_id"], "load_id": load_id,
                "project_cd": cfg.project_cd, "table_nm": cfg.table_nm, "src_cd": cfg.src_cd,
                "run_ty": batch["run_ty"], "rpt_start_dt_key": batch["rpt_start_dt_key"],
                "rpt_end_dt_key": batch["rpt_end_dt_key"], "stg_schema_nm": cfg.stg_schema_nm,
                "stg_tblnm": cfg.stg_tblnm}, cfg.rules_vld_md)
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

    def _duplicate_of(self, sha: str, batch: dict, load_id: int) -> Optional[int]:
        ids = [batch["current_load_id"]] if batch["current_load_id"] else []
        cand = self.conn.execute(
            """SELECT Candidate_Load_ID FROM ComplianceBatchOverride WHERE Req_ID=%s
                  AND Override_Ty IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN')
                  AND Apprvl_Stat IN ('PENDING_REVIEW','APPROVED')""", (batch["req_id"],)).fetchone()
        if cand:
            ids.append(cand["candidate_load_id"])
        ids = [i for i in ids if i != load_id]
        if not ids:
            return None
        row = self.conn.execute("SELECT Load_ID FROM ComplianceFileLoad WHERE Load_ID = ANY(%s) AND File_Sha256=%s "
                                "LIMIT 1", (ids, sha)).fetchone()
        return row["load_id"] if row else None

    # ------------------------------------------------------------------ resolution (§8)
    def _resolve(self, load_id, info, cfg, req_id, passed, rules_stat, failure_event, detail) -> IngestOutcome:
        now = self.clock.now()
        ref = s3_ref(info.bucket, info.key)
        with self.conn.transaction():
            b = get_batch(self.conn, req_id, for_update=True)
            ar = self.conn.execute(
                """SELECT * FROM ComplianceBatchOverride WHERE Req_ID=%s
                      AND Override_Ty IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN')
                      AND Apprvl_Stat IN ('PENDING_REVIEW','APPROVED') FOR UPDATE""", (req_id,)).fetchone()
            closed = b["batch_close_ind"] == 1
            d = decide(ResolutionInput(
                batch_closed=closed, resolution_ty=b["resolution_ty"],
                has_prior_promoted=b["current_load_id"] is not None, file_passed=passed,
                active_reopen=ActiveReopen(ar["apprvl_stat"], ar["promotion_stat"], ar["override_ty"]) if ar else None))
            ctx = dict(project_cd=b["project_cd"], table_nm=b["table_nm"], src_cd=b["src_cd"], run_ty=b["run_ty"],
                       req_id=req_id, btch_id=b["btch_id"], load_id=load_id, extract_id=b["extract_id"], file_ref=ref)
            out = IngestOutcome(load_id, "", d.rule, req_id=req_id, extract_id=b["extract_id"])

            if not closed:
                self.logger.batch_event("FILE_RECEIVED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        file_ref=ref, detail=f"rule={d.rule}")
            else:
                recv = "CORRECTION_RECEIVED" if b["resolution_ty"] == "NEW_FILE" else "LATE_ARRIVAL_RECEIVED"
                self.logger.batch_event(recv, req_id=req_id, btch_id=b["btch_id"], load_id=load_id, file_ref=ref,
                                        detail=f"rule={d.rule}")

            if d.action in (Action.PROMOTE, Action.PROMOTE_REPLACE):
                to_code = self.status.code(S_PROMOTED)
                self.status.check(b["req_stat"], to_code)
                load = self.conn.execute("SELECT Stg_Rcd_Cnt FROM ComplianceFileLoad WHERE Load_ID=%s",
                                         (load_id,)).fetchone()
                res = promoter.swap(self.conn, cfg, b["btch_id"], load_id, load["stg_rcd_cnt"], now)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Resolution_Ty='NEW_FILE', Current_Load_ID=%s, Req_Stat=%s,
                              Updated_Dtts=%s WHERE Req_ID=%s""", (load_id, to_code, now, req_id))
                if d.action == Action.PROMOTE_REPLACE:
                    self._supersede(b["current_load_id"])
                    self.logger.batch_event("FILE_REPLACED_BEFORE_CLOSE", req_id=req_id, btch_id=b["btch_id"],
                                            load_id=load_id, detail=f"replaces load {b['current_load_id']}")
                self.conn.execute(
                    """UPDATE ComplianceFileLoad SET Load_Stat='PROMOTED', Rules_Stat=%s, Core_Appended_Cnt=%s,
                              Core_Disabled_Cnt=%s, Promoted_Dtts=%s, Heartbeat_Dtts=NULL, Updated_Dtts=%s
                        WHERE Load_ID=%s""", (rules_stat, res.appended_cnt, res.disabled_cnt, now, now, load_id))
                self.logger.batch_event("FILE_PROMOTED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        detail=f"appended={res.appended_cnt} disabled={res.disabled_cnt}"
                                               + (f"; {detail}" if detail else ""))
                out.result = "PROMOTED"

            elif d.action in (Action.EXCEPTION_NO_DATA, Action.EXCEPTION_KEEP_PRIOR):
                to_code = self.status.code(S_EXCEPTION)
                self.status.check(b["req_stat"], to_code)
                self.conn.execute("UPDATE ComplianceRequestControl SET Req_Stat=%s, Updated_Dtts=%s WHERE Req_ID=%s",
                                  (to_code, now, req_id))
                self._rules_failed(load_id, rules_stat, detail, now)
                self.logger.batch_event("FILE_RULES_FAILED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                self.logger.audit(failure_event, description=detail, **ctx)
                out.result, out.event_ty = "RULES_FAILED", failure_event

            elif d.action == Action.REOPEN_REJECT:                                     # D-45
                self._rules_failed(load_id, rules_stat, detail, now)
                self.logger.batch_event("FILE_RULES_FAILED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                self.logger.audit("REOPEN_REJECTED_RULES_FAILED", description=f"{failure_event}: {detail}", **ctx)
                out.result, out.event_ty = "REOPEN_REJECTED", "REOPEN_REJECTED_RULES_FAILED"

            elif d.action == Action.REOPEN_INSERT:
                row = self.conn.execute(
                    """INSERT INTO ComplianceBatchOverride
                         (Override_Ty, Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key,
                          Rpt_End_Dt_Key, Btch_ID, Candidate_Load_ID, Prior_Load_ID, Prior_Resolution_Ty,
                          Prior_Req_Stat, Apprvl_Stat, Promotion_Stat, Last_Processed_Apprvl_Stat, History,
                          Created_Dtts, Updated_Dtts, Updated_By)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_REVIEW','NOT_APPLICABLE',
                               'PENDING_REVIEW',%s,%s,%s,'SYSTEM')
                       RETURNING Ovrd_ID""",
                    (d.override_ty, req_id, b["extract_id"], b["project_cd"], b["table_nm"], b["src_cd"], b["run_ty"],
                     b["rpt_start_dt_key"], b["rpt_end_dt_key"], b["btch_id"], load_id, b["current_load_id"],
                     b["resolution_ty"], b["req_stat"], f"{now.isoformat()} created PENDING_REVIEW, candidate load {load_id}",
                     now, now)).fetchone()
                self._pending(load_id, rules_stat, now)
                self.logger.audit("REOPEN_CANDIDATE_CREATED", ovrd_id=row["ovrd_id"],
                                  description=f"{d.override_ty} candidate load {load_id}", **ctx)
                out.result, out.ovrd_id = "PENDING_APPROVAL", row["ovrd_id"]

            else:  # REOPEN_REPLACE_PENDING / RESET_APPROVED / RESET_PROMOTED
                old = ar["candidate_load_id"]
                if old != b["current_load_id"]:
                    self._supersede(old)
                refresh_prior = d.action == Action.REOPEN_RESET_PROMOTED
                self.conn.execute(
                    """UPDATE ComplianceBatchOverride
                          SET Candidate_Load_ID=%s, Reviewed_Load_ID=NULL, Apprvl_Stat='PENDING_REVIEW',
                              Apprvd_By=NULL, Apprvd_Dtts=NULL, Promotion_Stat='NOT_APPLICABLE', Promoted_Dtts=NULL,
                              Last_Processed_Apprvl_Stat='PENDING_REVIEW',
                              Prior_Load_ID = CASE WHEN %s THEN %s ELSE Prior_Load_ID END,
                              Prior_Resolution_Ty = CASE WHEN %s THEN %s ELSE Prior_Resolution_Ty END,
                              Prior_Req_Stat = CASE WHEN %s THEN %s ELSE Prior_Req_Stat END,
                              History = History || E'\\n' || %s, Updated_Dtts=%s, Updated_By='SYSTEM'
                        WHERE Ovrd_ID=%s""",
                    (load_id, refresh_prior, b["current_load_id"], refresh_prior, b["resolution_ty"],
                     refresh_prior, b["req_stat"],
                     f"{now.isoformat()} {d.rule}: candidate load {old} replaced by {load_id} "
                     f"(was {ar['apprvl_stat']}/{ar['promotion_stat']}) -> PENDING_REVIEW",
                     now, ar["ovrd_id"]))
                self._pending(load_id, rules_stat, now)
                self.logger.audit("REOPEN_CANDIDATE_REPLACED", ovrd_id=ar["ovrd_id"],
                                  description=f"{d.rule}: load {old} -> {load_id}", **ctx)
                out.result, out.ovrd_id = "PENDING_APPROVAL", ar["ovrd_id"]
        return out

    def _supersede(self, load_id: Optional[int]) -> None:
        if load_id:
            self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='SUPERSEDED', Updated_Dtts=%s "
                              "WHERE Load_ID=%s AND Load_Stat IN ('PROMOTED','PENDING_APPROVAL')",
                              (self.clock.now(), load_id))

    def _rules_failed(self, load_id, rules_stat, detail, now) -> None:
        self.conn.execute(
            """UPDATE ComplianceFileLoad SET Load_Stat='RULES_FAILED', Rules_Stat=%s, Error_Txt=%s,
                      Heartbeat_Dtts=NULL, Updated_Dtts=%s WHERE Load_ID=%s""",
            (rules_stat, detail, now, load_id))

    def _pending(self, load_id, rules_stat, now) -> None:
        self.conn.execute(
            """UPDATE ComplianceFileLoad SET Load_Stat='PENDING_APPROVAL', Rules_Stat=%s, Heartbeat_Dtts=NULL,
                      Updated_Dtts=%s WHERE Load_ID=%s""", (rules_stat, now, load_id))

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
