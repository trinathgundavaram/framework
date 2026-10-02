"""File ingest pipeline on Teradata (design §7 P5, §8, §9.4)."""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from functools import partial
from typing import Optional

from . import config as cfgmod
from . import db
from .adapters import ERROR, ObjectInfo, ObjectStore, RuleEngine, basename, dirname, parse_uri, sha256_file
from .audit import EventLogger
from .batches import get_batch, promoted_load
from .common import (COMPLETED, EXCEPTION_PENDING, PROMOTED, Clock, FileRejected, RowCountMismatch,
                     TechnicalFailure, check_transition)
from .config import FileConfig, MatchError, TemplateMatcher
from .db import Connection
from .load import sanitize_db_error, stage, swap
from .settings import Settings

log = logging.getLogger(__name__)

TERMINAL_LOAD_STATS = {"QUARANTINED", "RULES_FAILED", "PROMOTED", "SUPERSEDED"}
ARCHIVE_LOAD_STATS = {"RULES_FAILED", "PROMOTED", "SUPERSEDED"}


class Action(str, Enum):
    PROMOTE = "PROMOTE"
    PROMOTE_REPLACE = "PROMOTE_REPLACE"
    EXCEPTION_NO_DATA = "EXCEPTION_NO_DATA"
    EXCEPTION_KEEP_PRIOR = "EXCEPTION_KEEP_PRIOR"
    PROMOTE_LATE = "PROMOTE_LATE"
    PROMOTE_CORRECTION = "PROMOTE_CORRECTION"
    REJECT_CLOSED = "REJECT_CLOSED"
    REJECT_RULES = "REJECT_RULES"


@dataclass(frozen=True)
class ResolutionInput:
    batch_closed: bool
    has_data: bool
    file_passed: bool
    override_ty: Optional[str] = None


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


@dataclass
class IngestOutcome:
    load_id: Optional[int]
    result: str
    rule: Optional[str] = None
    event_ty: Optional[str] = None
    req_id: Optional[int] = None
    ovrd_id: Optional[int] = None
    message: Optional[str] = None


def s3_ref(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


PROMOTED_RESULTS = ("PROMOTED", "LATE_PROMOTED", "CORRECTION_PROMOTED")
REJECTED_RESULTS = ("REJECTED_CLOSED", "RULES_FAILED")


@dataclass
class PathIngestSummary:
    """Result of one process_path() sweep."""
    scanned: int = 0
    promoted: int = 0
    quarantined: int = 0
    rejected: int = 0
    replayed: int = 0
    other: int = 0
    skipped: int = 0
    locations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    outcomes: list[IngestOutcome] = field(default_factory=list)

    def tally(self, outcome: IngestOutcome) -> None:
        self.outcomes.append(outcome)
        if outcome.result in PROMOTED_RESULTS:
            self.promoted += 1
        elif outcome.result == "QUARANTINED":
            self.quarantined += 1
        elif outcome.result in REJECTED_RESULTS:
            self.rejected += 1
        elif outcome.result == "REPLAY_IGNORED":
            self.replayed += 1
        else:
            self.other += 1


class IngestPipeline:
    def __init__(self, conn: Connection, clock: Clock, settings: Settings, store: ObjectStore,
                 rule_engine: RuleEngine):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.store = store
        self.rules = rule_engine
        self.logger = EventLogger(conn, clock)
        self._sweep_config: Optional[tuple[TemplateMatcher, tuple[str, ...]]] = None
        self._scope_project: Optional[str] = None

    def process_file(self, bucket: str, key: str, version_id: Optional[str] = None, *,
                     listed: bool = False) -> IngestOutcome:
        """Process one object; `listed` = it came from a folder listing, so another run may have moved it since."""
        obj = db.object_key(bucket, key, version_id)
        if not db.try_lock(self.conn, obj):
            return IngestOutcome(None, "IN_PROGRESS", message="another process is loading this object")
        try:
            if listed and not self.store.exists(bucket, key):
                return IngestOutcome(None, "GONE", message="already moved by another run")
            return self._process_object(bucket, key, version_id)
        finally:
            db.unlock(self.conn, obj)

    def _process_object(self, bucket: str, key: str, version_id: Optional[str]) -> IngestOutcome:
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

    def process_path(self, bucket: Optional[str] = None, prefix: Optional[str] = None,
                     project_cd: Optional[str] = None) -> PathIngestSummary:
        """Process every object at one location, a project's locations, or every configured location."""
        if bool(bucket) != bool(prefix):
            raise ValueError("bucket and prefix must be given together, or both omitted")
        if project_cd and bucket:
            raise ValueError("a project sweep covers that project's configured locations; omit bucket and prefix")
        own = None
        if project_cd:
            locations, shared, own = self._project_locations(project_cd)
        else:
            locations = [(bucket, prefix if not prefix or prefix.endswith("/") else prefix + "/")] if bucket \
                else self._configured_locations()
            shared = set()
        summary = PathIngestSummary(locations=[f"{b}/{p}" for b, p in locations])
        if project_cd and not locations:
            summary.errors.append(f"project {project_cd} has no active file config")
        self._sweep_config = self._load_config()
        self._scope_project = project_cd
        try:
            self._sweep(locations, summary, shared, own)
        finally:
            self._sweep_config = self._scope_project = None
        return summary

    def _project_locations(self, project_cd: str) -> tuple[list[tuple[str, str]], set, TemplateMatcher]:
        """(the project's inbound locations, the ones other projects also use, its own templates)."""
        cfgs = cfgmod.active_file_configs(self.conn)
        mine = [c for c in cfgs if c.project_cd == project_cd]
        locations = sorted({parse_uri(c.s3_src_file_path) for c in mine})
        shared = {parse_uri(c.s3_src_file_path) for c in cfgs if c.project_cd != project_cd} & set(locations)
        return locations, shared, TemplateMatcher(mine, self.settings.filename_case_sensitive)

    def _sweep(self, locations: list[tuple[str, str]], summary: PathIngestSummary, shared: set,
               own: Optional[TemplateMatcher]) -> None:
        for loc_bucket, loc_prefix in locations:
            try:
                objects = self.store.list_objects(loc_bucket, loc_prefix)
            except Exception as e:  # noqa: BLE001
                log.warning("could not list %s/%s: %s", loc_bucket, loc_prefix, e)
                summary.errors.append(f"{loc_bucket}/{loc_prefix}: {type(e).__name__}: {e}")
                continue
            for info in objects:
                summary.scanned += 1
                if (loc_bucket, loc_prefix) in shared and not own.candidates(basename(info.key)):
                    summary.skipped += 1
                    continue
                try:
                    out = self.process_file(loc_bucket, info.key, info.version_id, listed=True)
                except Exception as e:  # noqa: BLE001
                    summary.errors.append(f"{loc_bucket}/{info.key}: {type(e).__name__}: {e}")
                    continue
                if out.result == "GONE":
                    summary.skipped += 1
                    continue
                summary.tally(out)

    def _load_config(self) -> tuple[TemplateMatcher, tuple[str, ...]]:
        """The compiled filename templates of every active file config, and the run type codes."""
        return (TemplateMatcher(cfgmod.active_file_configs(self.conn), self.settings.filename_case_sensitive),
                tuple(cfgmod.run_types(self.conn)))

    def _configured_locations(self) -> list[tuple[str, str]]:
        """The distinct (bucket, prefix) inbound locations of every active file config."""
        return sorted({parse_uri(c.s3_src_file_path) for c in cfgmod.active_file_configs(self.conn)})

    def health(self) -> dict[str, list[dict]]:
        """Loads stuck mid-pipeline, and current quarantine counts by reason (design §15.3)."""
        q = partial(db.fetch_all, self.conn)
        stale_before = self.clock.now() - timedelta(minutes=self.settings.heartbeat_stale_minutes)
        return {
            "stale_loads": q(
                """SELECT Load_ID, S3_Key, Load_Stat, Updated_Dtts FROM ComplianceFileLoad
                    WHERE Load_Stat IN ('RECEIVED','STAGING','STAGED','RULES_RUNNING','FAILED_TECHNICAL')
                      AND Updated_Dtts < %s ORDER BY Load_ID""", stale_before),
            "quarantine_by_reason": q("""SELECT Quarantine_Rsn_Cd, count(*) AS n FROM ComplianceFileLoad
                                          WHERE Load_Stat='QUARANTINED' GROUP BY Quarantine_Rsn_Cd ORDER BY n DESC"""),
        }

    @staticmethod
    def _retryable_quarantine(load: dict) -> bool:
        """A file quarantined only because its batch was closed is reprocessed when it is delivered again."""
        return load["load_stat"] == "QUARANTINED" and load["quarantine_rsn_cd"] == "FILE_REJECTED_BATCH_CLOSED"

    def _register(self, info: ObjectInfo) -> tuple[dict, bool]:
        """The load row of this object version (the caller holds the object lock); True when it is new."""
        obj_hash = db.object_hash(info.bucket, info.key, info.version_id or info.etag)
        find = "SELECT * FROM ComplianceFileLoad WHERE Obj_Hash=%s"
        row = self.conn.execute(find, (obj_hash,)).fetchone()
        if row:
            return row, False
        now = self.clock.now()
        try:
            self.conn.execute(
                """INSERT INTO ComplianceFileLoad (Obj_Hash, S3_Bucket, S3_Key, S3_Version_Id, S3_ETag,
                                                  File_Size_Byte, Load_Stat, Rules_Stat, Created_Dtts, Updated_Dtts)
                   VALUES (%s,%s,%s,%s,%s,%s,'RECEIVED','NOT_RUN',%s,%s)""",
                (obj_hash, info.bucket, info.key, info.version_id, info.etag, info.size, now, now))
        except Exception as e:  # noqa: BLE001
            if not db.is_duplicate_key(e):
                raise
            return self.conn.execute(find, (obj_hash,)).fetchone(), False
        return self.conn.execute(find, (obj_hash,)).fetchone(), True

    def _replay(self, load: dict, info: ObjectInfo) -> IngestOutcome:
        with self.conn.transaction():
            self.logger.audit("FILE_EVENT_REPLAY_IGNORED", load_id=load["load_id"], req_id=load["req_id"],
                              btch_id=load["btch_id"], file_ref=s3_ref(info.bucket, info.key),
                              description=f"load already {load['load_stat']}")
        if self.store.exists(info.bucket, info.key):
            if load["load_stat"] == "QUARANTINED":
                self._move(info, self.settings.quarantine_uri, f"{load['quarantine_rsn_cd']}/", load["load_id"])
            elif load["load_stat"] in ARCHIVE_LOAD_STATS and (cfg := cfgmod.file_config_by_id(self.conn, load["cfg_id"])):
                self._move(info, cfg.src_file_archive_path, "", load["load_id"])
        return IngestOutcome(load["load_id"], "REPLAY_IGNORED", req_id=load["req_id"])

    def _process(self, load_id: int, info: ObjectInfo) -> IngestOutcome:
        name = basename(info.key)
        matcher, run_type_codes = self._sweep_config or self._load_config()
        try:
            m = matcher.match(name)
        except MatchError as e:
            return self._quarantine(load_id, info, e.event_ty, str(e), e.cfg)
        cfg = m.cfg
        bucket, prefix = parse_uri(cfg.s3_src_file_path)
        if info.bucket != bucket or dirname(info.key) != prefix:
            return self._quarantine(load_id, info, "FILE_REJECTED_UNPARSEABLE",
                                    f"{name} matched Cfg_ID {cfg.cfg_id} but is not in its inbound location", cfg)
        run_ty = self._resolve_run_type(m.run_ty, run_type_codes)
        ref_date = m.rpt_start if self.settings.file_effective_date_basis == "RPT_START" else m.rpt_end
        x = cfgmod.effective_xwalk(self.conn, cfg.project_cd, cfg.table_nm, cfg.src_id, run_ty, ref_date) if run_ty else None
        if x is None:
            return self._quarantine(load_id, info, "FILE_REJECTED_RUNTY_NOT_CONFIGURED",
                                    f"run type {m.run_ty!r} is not configured/effective for "
                                    f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id} on {ref_date}", cfg)
        batch, override = self._select_batch(cfg, run_ty, m.rpt_start, m.rpt_end)
        if batch is None:
            event = "FILE_REJECTED_BATCH_CLOSED" if override == "CLOSED" else "FILE_REJECTED_NO_BATCH"
            detail = ("every batch for this period is closed and no approved, valid override exists"
                      if override == "CLOSED" else "no batch exists for this period")
            return self._quarantine(load_id, info, event,
                                    f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id}/{run_ty} "
                                    f"{m.rpt_start}..{m.rpt_end}: {detail}", cfg)
        with self.conn.transaction():
            self.conn.execute(
                """UPDATE ComplianceFileLoad SET Cfg_ID=%s, Req_ID=%s, Btch_ID=%s, Load_Stat='STAGING', Error_Txt=NULL,
                          Updated_Dtts=%s WHERE Load_ID=%s""",
                (cfg.cfg_id, batch["req_id"], batch["btch_id"], self.clock.now(), load_id))
        if info.size == 0 and cfg.has_header:
            return self._quarantine(load_id, info, "FILE_PARSE_ERROR", "zero-byte file but a header is expected",
                                    cfg, batch)
        with db.held(self.conn, db.batch_key(batch["btch_id"]), self.settings.lock_timeout_seconds):
            return self._process_locked(load_id, info, cfg, batch["req_id"])

    def _resolve_run_type(self, token: str, codes: tuple[str, ...]) -> Optional[str]:
        if token in codes:
            return token
        if not self.settings.filename_case_sensitive:
            for c in codes:
                if c.lower() == token.lower():
                    return c
        return None

    def _select_batch(self, cfg: FileConfig, run_ty: str, rpt_start, rpt_end) -> tuple[Optional[dict], Optional[str]]:
        """The batch a file belongs to (D-78)."""
        rows = self.conn.execute(
            """SELECT * FROM ComplianceRequestControl
                WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s AND Run_Ty=%s
                  AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s
                ORDER BY Req_Dt_Key DESC, Req_ID DESC""",
            (cfg.project_cd, cfg.table_nm, cfg.src_id, run_ty, rpt_start, rpt_end)).fetchall()
        if not rows:
            return None, None
        today = self.clock.today(self.settings.business_tz)
        open_rows = [r for r in rows if r["batch_close_ind"] == 0]
        if open_rows:
            return next((r for r in open_rows if db.as_date(r["req_dt_key"]) <= today), open_rows[-1]), None
        for r in rows:
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
            current = promoted_load(self.conn, batch["btch_id"])
            if current and current["file_sha256"] == sha and current["load_id"] != load_id:
                return self._quarantine(load_id, info, "FILE_REJECTED_DUPLICATE",
                                        f"identical to load {current['load_id']} of batch {batch['btch_id']}",
                                        cfg, batch)
            other = self.conn.execute(
                """SELECT TOP 1 Load_ID, Btch_ID FROM ComplianceFileLoad WHERE File_Sha256=%s AND Btch_ID<>%s
                      AND Load_Stat <> 'QUARANTINED'""", (sha, batch["btch_id"])).fetchone()
            if other:
                with self.conn.transaction():
                    self.logger.audit("FILE_SAME_CONTENT_OTHER_BATCH", load_id=load_id, req_id=req_id,
                                      btch_id=batch["btch_id"], file_ref=s3_ref(info.bucket, info.key),
                                      project_cd=cfg.project_cd, table_nm=cfg.table_nm, src_id=cfg.src_id,
                                      run_ty=batch["run_ty"],
                                      description=f"same content as load {other['load_id']} ({other['btch_id']})")
            try:
                staged_rows = stage(self.conn, self.settings, file_path=path, cfg=cfg, btch_id=batch["btch_id"],
                                    load_id=load_id, src_file_nm=basename(info.key), loaded_at=self.clock.now())
            except FileRejected as e:
                return self._quarantine(load_id, info, e.event_ty, str(e), cfg, batch)
        with self.conn.transaction():
            self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='STAGED', Stg_Rcd_Cnt=%s, Updated_Dtts=%s "
                              "WHERE Load_ID=%s", (staged_rows, self.clock.now(), load_id))

        failure_event = None
        detail = None
        rules_stat = "NOT_RUN"
        if staged_rows == 0 and not cfg.allow_zero_records:
            passed, failure_event, detail = False, "FILE_ZERO_RECORDS_REJECTED", "file has no data rows"
        elif bindings := cfgmod.rule_bindings(self.conn, cfg.project_cd, cfg.table_nm, cfg.src_id, batch["run_ty"]):
            with self.conn.transaction():
                self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='RULES_RUNNING', Updated_Dtts=%s "
                                  "WHERE Load_ID=%s", (self.clock.now(), load_id))
            outcome = self.rules.run(self.conn, bindings, {
                "scope": "FILE_LEVEL", "btch_id": batch["btch_id"], "load_id": load_id,
                "project_cd": cfg.project_cd, "table_nm": cfg.table_nm, "src_id": cfg.src_id,
                "run_ty": batch["run_ty"], "rpt_start_dt_key": batch["rpt_start_dt_key"],
                "rpt_end_dt_key": batch["rpt_end_dt_key"], "req_dt_key": batch["req_dt_key"],
                "stg_schema_nm": cfg.stg_schema_nm, "stg_table_nm": cfg.stg_table_nm}, self.settings.file_rules_mode)
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

    def _resolve(self, load_id, info, cfg, req_id, passed, rules_stat, failure_event, detail) -> IngestOutcome:
        now = self.clock.now()
        ref = s3_ref(info.bucket, info.key)
        today = self.clock.today(self.settings.business_tz)
        with self.conn.transaction():
            b = get_batch(self.conn, req_id)
            closed = b["batch_close_ind"] == 1
            has_data = self._has_data(b)
            ovrd = self.active_override(b, required_override_ty(has_data), today) if closed else None
            d = decide(ResolutionInput(batch_closed=closed, has_data=has_data, file_passed=passed,
                                       override_ty=ovrd["override_ty"] if ovrd else None))
            ctx = dict(project_cd=b["project_cd"], table_nm=b["table_nm"], src_id=b["src_id"], run_ty=b["run_ty"],
                       req_id=req_id, btch_id=b["btch_id"], load_id=load_id, file_ref=ref)
            out = IngestOutcome(load_id, "", d.rule, req_id=req_id, ovrd_id=ovrd["ovrd_id"] if ovrd else None)
            self.logger.batch_event("FILE_RECEIVED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                    detail=f"{ref} rule={d.rule}")

            if d.action in (Action.PROMOTE, Action.PROMOTE_REPLACE, Action.PROMOTE_LATE, Action.PROMOTE_CORRECTION):
                to_stat = COMPLETED if closed else PROMOTED
                check_transition(b["req_stat"], to_stat)
                staged_cnt = self.conn.execute("SELECT Stg_Rcd_Cnt FROM ComplianceFileLoad WHERE Load_ID=%s",
                                               (load_id,)).fetchone()["stg_rcd_cnt"]
                prior = promoted_load(self.conn, b["btch_id"])
                if prior and prior["load_id"] != load_id:
                    self._supersede(prior["load_id"])
                res = swap(self.conn, cfg, b["btch_id"], load_id, staged_cnt, now)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Resolution_Ty='NEW_FILE', Req_Stat=%s, Reuse_Btch_ID=NULL,
                              Updated_Dtts=%s WHERE Req_ID=%s""", (to_stat, now, req_id))
                self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='PROMOTED', Rules_Stat=%s, Updated_Dtts=%s "
                                  "WHERE Load_ID=%s", (rules_stat, now, load_id))
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
                                        ovrd_id=out.ovrd_id, detail=f"appended={res.appended_cnt} disabled={res.disabled_cnt}"
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

            elif d.action == Action.REJECT_RULES:
                self._rules_failed(load_id, rules_stat, detail, now)
                self.logger.batch_event("FILE_RULES_FAILED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                self.logger.audit(failure_event, description=f"closed batch: {detail}", **ctx)
                out.result, out.event_ty = "RULES_FAILED", failure_event

            else:
                self._rules_failed(load_id, rules_stat, "batch is closed and has no approved override", now)
                self.logger.audit("FILE_REJECTED_BATCH_CLOSED", description="no approved, valid override", **ctx)
                out.result, out.event_ty = "REJECTED_CLOSED", "FILE_REJECTED_BATCH_CLOSED"
        return out

    def _end_carry_forward(self, b: dict, load_id: int, now) -> None:
        """A real file replaced an applied carry-forward."""
        row = self.conn.execute("SELECT Ovrd_ID FROM ComplianceBatchOverride WHERE Req_ID=%s "
                                "AND Override_Ty='REUSE' AND Apprvl_Stat='APPROVED'", (b["req_id"],)).fetchone()
        self.conn.execute(
            """UPDATE ComplianceBatchOverride SET Valid_Thru_Dt_Key=%s, Updated_Dtts=%s, Updated_By='SYSTEM'
                WHERE Req_ID=%s AND Override_Ty='REUSE' AND Apprvl_Stat='APPROVED'""",
            (db.as_date(b["req_dt_key"]), now, b["req_id"]))
        self.logger.batch_event("CARRY_FORWARD_REMOVED", req_id=b["req_id"], btch_id=b["btch_id"], load_id=load_id,
                                ovrd_id=row["ovrd_id"] if row else None,
                                detail=f"reuse of {b['reuse_btch_id']} replaced by load {load_id}")

    def _supersede(self, load_id: int) -> None:
        self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='SUPERSEDED', Updated_Dtts=%s "
                          "WHERE Load_ID=%s AND Load_Stat='PROMOTED'", (self.clock.now(), load_id))

    def _rules_failed(self, load_id, rules_stat, detail, now) -> None:
        self.conn.execute(
            """UPDATE ComplianceFileLoad SET Load_Stat='RULES_FAILED', Rules_Stat=%s, Error_Txt=%s, Updated_Dtts=%s
                WHERE Load_ID=%s""",
            (rules_stat, detail[:4000] if detail else detail, now, load_id))

    def _quarantine(self, load_id: int, info: ObjectInfo, event_ty: str, message: str,
                    cfg: Optional[FileConfig] = None, batch: Optional[dict] = None) -> IngestOutcome:
        with self.conn.transaction():
            self.conn.execute(
                """UPDATE ComplianceFileLoad SET Load_Stat='QUARANTINED', Quarantine_Rsn_Cd=%s, Error_Txt=%s,
                          Updated_Dtts=%s WHERE Load_ID=%s""", (event_ty, message[:4000], self.clock.now(), load_id))
            if cfg:
                self.conn.execute("UPDATE ComplianceFileLoad SET Cfg_ID=%s WHERE Load_ID=%s", (cfg.cfg_id, load_id))
            ctx = {"project_cd": self._scope_project} if self._scope_project else {}
            if cfg:
                ctx.update(project_cd=cfg.project_cd, table_nm=cfg.table_nm, src_id=cfg.src_id)
            if batch:
                ctx.update(run_ty=batch["run_ty"], req_id=batch["req_id"], btch_id=batch["btch_id"])
            self.logger.audit(event_ty, load_id=load_id, file_ref=s3_ref(info.bucket, info.key),
                              description=message, **ctx)
        self._move(info, self.settings.quarantine_uri, f"{event_ty}/", load_id)
        return IngestOutcome(load_id, "QUARANTINED", event_ty=event_ty, message=message,
                             req_id=batch["req_id"] if batch else None)

    def _move(self, info: ObjectInfo, dest_uri: str, sub_prefix: str, load_id: int) -> None:
        try:
            self.store.move(info.bucket, info.key, dest_uri, info.version_id, sub_prefix)
        except Exception as e:  # noqa: BLE001
            log.warning("move of %s failed: %s", info.key, e)
            with self.conn.transaction():
                self.logger.audit("FILE_MOVE_FAILED", load_id=load_id, file_ref=s3_ref(info.bucket, info.key),
                                  description=f"{type(e).__name__} moving to {dest_uri}{sub_prefix}")

    def _mark_technical_failure(self, load_id: int, err: Exception) -> None:
        try:
            with self.conn.transaction():
                marks, terminal = db.in_list(sorted(TERMINAL_LOAD_STATS))
                self.conn.execute(
                    f"""UPDATE ComplianceFileLoad SET Load_Stat='FAILED_TECHNICAL', Error_Txt=%s, Updated_Dtts=%s
                        WHERE Load_ID=%s AND Load_Stat NOT IN ({marks})""",
                    [sanitize_db_error(f"{type(err).__name__}: {err}"), self.clock.now(), load_id, *terminal])
                if isinstance(err, RowCountMismatch):
                    self.logger.audit("CORE_LOAD_ROWCOUNT_MISMATCH", load_id=load_id, description=str(err))
                self._report_technical_failure(load_id, err)
        except Exception:  # noqa: BLE001
            log.exception("could not record technical failure for load %s", load_id)

    def _report_technical_failure(self, load_id: int, err: Exception) -> None:
        """One FILE_TECHNICAL_FAILURE event (emailed) per load."""
        if self.conn.execute("SELECT 1 AS ok FROM CMS_ComplianceExceptionsAudit WHERE Load_ID=%s "
                             "AND Event_Ty='FILE_TECHNICAL_FAILURE'", (load_id,)).fetchone():
            return
        row = self.conn.execute(
            """SELECT f.S3_Bucket, f.S3_Key, f.Req_ID, f.Btch_ID, c.Project_Cd, c.Table_Nm, c.Src_ID, c.Run_Ty
                 FROM ComplianceFileLoad f LEFT JOIN ComplianceRequestControl c ON c.Req_ID = f.Req_ID
                WHERE f.Load_ID=%s""", (load_id,)).fetchone()
        self.logger.audit("FILE_TECHNICAL_FAILURE", load_id=load_id, req_id=row["req_id"], btch_id=row["btch_id"],
                          file_ref=s3_ref(row["s3_bucket"], row["s3_key"]),
                          project_cd=row["project_cd"] or self._scope_project, table_nm=row["table_nm"],
                          src_id=row["src_id"], run_ty=row["run_ty"],
                          description=sanitize_db_error(f"{type(err).__name__}: {err}")[:500])
