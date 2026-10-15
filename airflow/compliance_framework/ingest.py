"""File ingest pipeline on Teradata (design §7 P5, §8, §9.4)."""
from __future__ import annotations

import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from functools import partial
from typing import Optional

from . import config as cfgmod
from . import db
from .adapters import FileInfo, FileStore, basename, dirname, parse_uri, sha256_file
from .audit import EventLogger
from .batches import get_batch, promoted_load
from .common import (COMPLETED, EXCEPTION_PENDING, PROMOTED, Clock, FileRejected, RowCountMismatch, btch_id_version, check_transition,
                     code)
from .config import FileConfig, MatchError, TemplateMatcher
from .control_report import write_error_log, write_reports
from .filecheck import check_content, error_rows, file_columns
from .db import Connection
from .filecheck_options import check_options
from .layout import LoadContext
from .load import file_layout, raw_line, sanitize_db_error, stage, swap
from .settings import Settings

log = logging.getLogger(__name__)

TERMINAL_LOAD_STATS = {"QUARANTINED", "RULES_FAILED", "PROMOTED", "SUPERSEDED"}
UNFINISHED_LOAD_STATS = ("RECEIVED", "STAGING", "STAGED")


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


PROMOTED_RESULTS = ("PROMOTED", "LATE_PROMOTED", "CORRECTION_PROMOTED")
REJECTED_RESULTS = ("REJECTED_CLOSED", "RULES_FAILED")


@dataclass
class PathIngestSummary:
    """Result of one process_path() sweep."""
    scanned: int = 0
    promoted: int = 0
    quarantined: int = 0
    rejected: int = 0
    other: int = 0
    skipped: int = 0
    locations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    outcomes: list[IngestOutcome] = field(default_factory=list)

    def tally(self, outcome: IngestOutcome) -> None:
        self.outcomes.append(outcome)
        if outcome.result in PROMOTED_RESULTS:
            self.promoted += 1
        elif outcome.result == "QUARANTINED":
            self.quarantined += 1
        elif outcome.result in REJECTED_RESULTS:
            self.rejected += 1
        else:
            self.other += 1


def _as_date(value):
    return value.date() if hasattr(value, "hour") else value


class IngestPipeline:
    def __init__(self, conn: Connection, clock: Clock, settings: Settings, store: FileStore):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.store = store
        self.logger = EventLogger(conn, clock)
        self._sweep_config: Optional[TemplateMatcher] = None
        self._handled: list[int] = []
        self._scope_project: Optional[str] = None
        self._load: tuple = ()

    def process_file(self, share: str, path: str, *, listed: bool = False) -> IngestOutcome:
        """Process one file; `listed` = it came from a folder listing, so another run may have moved it since."""
        obj = db.file_key(share, path)
        if not db.try_lock(self.conn, obj):
            return IngestOutcome(None, "IN_PROGRESS", message="another process is loading this file")
        try:
            if listed and not self.store.exists(share, path):
                return IngestOutcome(None, "GONE", message="already moved by another run")
            return self._process_registered(share, path)
        finally:
            db.unlock(self.conn, obj)

    def _process_registered(self, share: str, path: str) -> IngestOutcome:
        info = self.store.head(share, path)
        load_id = self._register(info)
        self._handled.append(load_id)
        try:
            return self._process(load_id, info)
        except Exception as e:
            self._mark_technical_failure(load_id, e)
            raise

    def process_path(self, share: Optional[str] = None, folder: Optional[str] = None,
                     project_cd: Optional[str] = None) -> PathIngestSummary:
        """Process every file in one folder, a project's folders, or every configured folder."""
        project_cd = code(project_cd)
        if bool(share) != bool(folder):
            raise ValueError("share and folder must be given together, or both omitted")
        if project_cd and share:
            raise ValueError("a project sweep covers that project's configured locations; omit share and folder")
        own = None
        if project_cd:
            locations, shared, own = self._project_locations(project_cd)
        else:
            locations = [(share, folder if not folder or folder.endswith("/") else folder + "/")] if share \
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
            summary.reports = self.control_report()
            self._sweep_config = self._scope_project = None
        return summary

    def control_report(self) -> list[str]:
        """Write the control report of the files handled since the last one."""
        handled, self._handled = self._handled, []
        try:
            return write_reports(self.conn, self.store, self.settings, self.clock, handled,
                                 self._sweep_config or self._load_config())
        except Exception as e:  # noqa: BLE001
            log.warning("could not write the control report: %s", e)
            return []

    def _project_locations(self, project_cd: str) -> tuple[list[tuple[str, str]], set, TemplateMatcher]:
        """(the project's inbound locations, the ones other projects also use, its own templates)."""
        cfgs = cfgmod.active_file_configs(self.conn, self.settings)
        mine = [c for c in cfgs if c.project_cd == project_cd]
        locations = sorted({parse_uri(c.src_file_path) for c in mine})
        shared = {parse_uri(c.src_file_path) for c in cfgs if c.project_cd != project_cd} & set(locations)
        return locations, shared, TemplateMatcher(mine, self.settings.filename_case_sensitive)

    def _sweep(self, locations: list[tuple[str, str]], summary: PathIngestSummary, shared: set,
               own: Optional[TemplateMatcher]) -> None:
        for loc_share, loc_folder in locations:
            try:
                files = self.store.list_files(loc_share, loc_folder)
            except Exception as e:  # noqa: BLE001
                log.warning("could not list %s/%s: %s", loc_share, loc_folder, e)
                summary.errors.append(f"{loc_share}/{loc_folder}: {db.error_text(e)}")
                continue
            for info in files:
                summary.scanned += 1
                if (loc_share, loc_folder) in shared and not own.candidates(basename(info.path)):
                    summary.skipped += 1
                    continue
                try:
                    out = self.process_file(loc_share, info.path, listed=True)
                except Exception as e:  # noqa: BLE001
                    summary.errors.append(f"{loc_share}/{info.path}: {db.error_text(e)}")
                    continue
                if out.result == "GONE":
                    summary.skipped += 1
                    continue
                summary.tally(out)

    def _load_config(self) -> TemplateMatcher:
        """The compiled filename templates of every active file config."""
        return TemplateMatcher(cfgmod.active_file_configs(self.conn, self.settings), self.settings.filename_case_sensitive)

    def _configured_locations(self) -> list[tuple[str, str]]:
        """The distinct (share, folder) inbound locations of every active file config."""
        return sorted({parse_uri(c.src_file_path) for c in cfgmod.active_file_configs(self.conn, self.settings)})

    def health(self) -> dict[str, list[dict]]:
        """Loads stuck mid-pipeline, and current quarantine counts by reason (design §15.3)."""
        q = partial(db.fetch_all, self.conn)
        stale_before = self.clock.now() - timedelta(minutes=self.settings.heartbeat_stale_minutes)
        return {
            "stale_loads": q(
                """SELECT Load_ID, File_Path, Load_Stat, Updated_Dtts FROM ComplianceFileLoad
                    WHERE Load_Stat IN ('RECEIVED','STAGING','STAGED','RULES_RUNNING','FAILED_TECHNICAL')
                      AND Updated_Dtts < %s ORDER BY Load_ID""", stale_before),
            "quarantine_by_reason": q("""SELECT Quarantine_Rsn_Cd, count(*) AS n FROM ComplianceFileLoad
                                          WHERE Load_Stat='QUARANTINED' GROUP BY Quarantine_Rsn_Cd ORDER BY n DESC"""),
        }

    def _register(self, info: FileInfo) -> int:
        """A new load row for this pickup of the file (the caller holds the file lock).

        Every pickup is a new delivery, whatever happened to the file before. A load of the same file left unfinished
        (a run that stopped) is closed as FAILED_TECHNICAL.
        """
        now = self.clock.now()
        key = db.file_hash(info.share, info.path, f"{info.version}#{uuid.uuid4().hex}")
        marks, unfinished = db.in_list(UNFINISHED_LOAD_STATS)
        with self.conn.transaction():
            self.conn.execute(
                f"""UPDATE ComplianceFileLoad SET Load_Stat='FAILED_TECHNICAL', Error_Txt=%s, Updated_Dtts=%s
                     WHERE File_Share=%s AND File_Path=%s AND Load_Stat IN ({marks})""",
                ["not finished; the file was picked up again", now, info.share, info.path, *unfinished])
            self.conn.execute(
                """INSERT INTO ComplianceFileLoad (File_Hash, File_Share, File_Path, File_Version,
                                                  File_Size_Byte, Load_Stat, Rules_Stat, Created_Dtts, Updated_Dtts)
                   VALUES (%s,%s,%s,%s,%s,'RECEIVED','NOT_RUN',%s,%s)""",
                (key, info.share, info.path, info.version, info.size, now, now))
            return self.conn.execute("SELECT Load_ID FROM ComplianceFileLoad WHERE File_Hash=%s", (key,)).fetchone()["load_id"]

    def _process(self, load_id: int, info: FileInfo) -> IngestOutcome:
        name = basename(info.path)
        matcher = self._sweep_config or self._load_config()
        try:
            m = matcher.match(name)
        except MatchError as e:
            return self._quarantine(load_id, info, e.event_ty, str(e), e.cfg)
        cfg = m.cfg
        share, folder = parse_uri(cfg.src_file_path)
        if info.share != share or dirname(info.path) != folder:
            return self._quarantine(load_id, info, "FILE_REJECTED_UNPARSEABLE",
                                    f"{name} matched Cfg_ID {cfg.cfg_id} but is not in its inbound location", cfg)
        run_ty = code(m.run_ty)
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
                """UPDATE ComplianceFileLoad SET Cfg_ID=%s, Req_ID=%s, Btch_ID=%s, Load_Stat='STAGING', Quarantine_Rsn_Cd=NULL, Error_Txt=NULL,
                          Updated_Dtts=%s WHERE Load_ID=%s""",
                (cfg.cfg_id, batch["req_id"], batch["btch_id"], self.clock.now(), load_id))
        if info.size == 0 and cfg.has_header:
            return self._quarantine(load_id, info, "FILE_PARSE_ERROR", "zero-byte file but a header is expected",
                                    cfg, batch)
        stage_table = db.batch_key(f"STAGE {cfg.stg_schema_nm}.{cfg.stg_table_nm}".upper())
        with db.held(self.conn, db.batch_key(batch["btch_id"]), self.settings.lock_timeout_seconds), \
                db.held(self.conn, stage_table, self.settings.lock_timeout_seconds):
            return self._process_locked(load_id, info, cfg, batch["req_id"])

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
                return r, code(ovrd["override_ty"])
        return None, "CLOSED"

    def active_override(self, batch: dict, override_ty: str, today) -> Optional[dict]:
        return self.conn.execute(
            """SELECT * FROM ComplianceBatchOverride
                WHERE Req_ID=%s AND UPPER(Override_Ty)=%s AND UPPER(Apprvl_Stat)='APPROVED' AND Valid_Thru_Dt_Key >= %s""",
            (batch["req_id"], override_ty, today)).fetchone()

    def _has_data(self, batch: dict) -> bool:
        return batch["resolution_ty"] == "CARRY_FORWARD" or promoted_load(self.conn, batch["btch_id"]) is not None

    def _process_locked(self, load_id: int, info: FileInfo, cfg: FileConfig, req_id: int) -> IngestOutcome:
        """The caller holds the batch lock and the lock of the staging table (stage and core load are one unit)."""
        batch = get_batch(self.conn, req_id)
        opts = check_options(cfg, cfgmod.file_checks(self.conn))
        layout = file_layout(self.conn, cfg, cfgmod.target_columns(self.conn), opts)
        version = btch_id_version(batch["btch_id"], cfg.project_cd, cfg.table_nm, cfg.src_id, batch["run_ty"])
        ctx = LoadContext(batch["btch_id"], load_id, batch["run_ty"], version, cfg.src_id, basename(info.path),
                          self.clock.now(), cfg.project_cd, cfg.table_nm, _as_date(batch["rpt_start_dt_key"]),
                          _as_date(batch["rpt_end_dt_key"]), req_id, self.settings.business_tz)
        self._load = (layout, ctx)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, basename(info.path))
            sha = self.store.download(info.share, info.path, path) or sha256_file(path)
            with self.conn.transaction():
                self.conn.execute("UPDATE ComplianceFileLoad SET File_Sha256=%s, Updated_Dtts=%s WHERE Load_ID=%s",
                                  (sha, self.clock.now(), load_id))
            other = self.conn.execute(
                """SELECT TOP 1 Load_ID, Btch_ID FROM ComplianceFileLoad WHERE File_Sha256=%s AND Btch_ID<>%s
                      AND Load_Stat <> 'QUARANTINED'""", (sha, batch["btch_id"])).fetchone()
            if other:
                with self.conn.transaction():
                    self.logger.audit("FILE_SAME_CONTENT_OTHER_BATCH", load_id=load_id, req_id=req_id,
                                      btch_id=batch["btch_id"], file_ref=self.store.uri(info.share, info.path),
                                      project_cd=cfg.project_cd, table_nm=cfg.table_nm, src_id=cfg.src_id,
                                      run_ty=batch["run_ty"],
                                      description=f"same content as load {other['load_id']} ({other['btch_id']})")
            if self.settings.check_on_load:
                details: list = []
                found = check_content(path, basename(info.path), cfg, len(layout.file_cols) + (1 if opts.record_types else 0),
                                      self.settings, opts, details, file_columns(layout) if opts.value_rules else None)
                if found:
                    return self._quarantine(load_id, info, "FILE_CONTENT_CHECK_FAILED",
                                            f"{len(found)} problem(s); first: {found[0][0]}: {found[0][1]}", cfg, batch,
                                            rows=error_rows(found, details))
            try:
                staged_rows = stage(self.conn, self.settings, file_path=path, cfg=cfg, layout=layout, ctx=ctx,
                                    opts=opts)
            except FileRejected as e:
                return self._quarantine(load_id, info, e.event_ty, str(e), cfg, batch, local_path=path)
        with self.conn.transaction():
            self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='STAGED', Stg_Rcd_Cnt=%s, Updated_Dtts=%s "
                              "WHERE Load_ID=%s", (staged_rows, self.clock.now(), load_id))

        failure = None
        if staged_rows == 0 and not cfg.allow_zero_records:
            failure = ("FILE_ZERO_RECORDS_REJECTED", "file has no data rows")
        result = self._resolve(load_id, info, cfg, req_id, failure)
        self._archive(info, load_id, rejected=result.result not in PROMOTED_RESULTS)
        return result

    def _resolve(self, load_id, info, cfg, req_id, failure: Optional[tuple[str, str]]) -> IngestOutcome:
        """Promote the staged file, or record why it is not promoted; `failure` = (event, detail) of a file that
        failed after staging (today: an empty file that is not allowed)."""
        failure_event, detail = failure or (None, None)
        now = self.clock.now()
        ref = self.store.uri(info.share, info.path)
        today = self.clock.today(self.settings.business_tz)
        with self.conn.transaction():
            b = get_batch(self.conn, req_id)
            closed = b["batch_close_ind"] == 1
            has_data = self._has_data(b)
            ovrd = self.active_override(b, required_override_ty(has_data), today) if closed else None
            d = decide(ResolutionInput(batch_closed=closed, has_data=has_data, file_passed=failure is None,
                                       override_ty=code(ovrd["override_ty"]) if ovrd else None))
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
                res = swap(self.conn, cfg, *self._load, staged_cnt)
                self.conn.execute(
                    """UPDATE ComplianceRequestControl SET Resolution_Ty='NEW_FILE', Req_Stat=%s, Reuse_Btch_ID=NULL,
                              Updated_Dtts=%s WHERE Req_ID=%s""", (to_stat, now, req_id))
                self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='PROMOTED', Rules_Stat='NOT_RUN', Updated_Dtts=%s "
                                  "WHERE Load_ID=%s", (now, load_id))
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
                                        ovrd_id=out.ovrd_id, detail=f"appended={res.appended_cnt} deleted={res.deleted_cnt}"
                                               + (f"; {detail}" if detail else ""))
                out.result = {Action.PROMOTE_LATE: "LATE_PROMOTED",
                              Action.PROMOTE_CORRECTION: "CORRECTION_PROMOTED"}.get(d.action, "PROMOTED")

            elif d.action in (Action.EXCEPTION_NO_DATA, Action.EXCEPTION_KEEP_PRIOR):
                check_transition(b["req_stat"], EXCEPTION_PENDING)
                self.conn.execute("UPDATE ComplianceRequestControl SET Req_Stat=%s, Updated_Dtts=%s WHERE Req_ID=%s",
                                  (EXCEPTION_PENDING, now, req_id))
                self._rules_failed(load_id, detail, now)
                self.logger.batch_event("FILE_RULES_FAILED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                self.logger.audit(failure_event, description=detail, **ctx)
                out.result, out.event_ty = "RULES_FAILED", failure_event

            elif d.action == Action.REJECT_RULES:
                self._rules_failed(load_id, detail, now)
                self.logger.batch_event("FILE_RULES_FAILED", req_id=req_id, btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                self.logger.audit(failure_event, description=f"closed batch: {detail}", **ctx)
                out.result, out.event_ty = "RULES_FAILED", failure_event

            else:
                self._rules_failed(load_id, "batch is closed and has no approved override", now)
                self.logger.audit("FILE_REJECTED_BATCH_CLOSED", description="no approved, valid override", **ctx)
                out.result, out.event_ty = "REJECTED_CLOSED", "FILE_REJECTED_BATCH_CLOSED"
        return out

    def _end_carry_forward(self, b: dict, load_id: int, now) -> None:
        """A real file replaced an applied carry-forward."""
        row = self.conn.execute("SELECT Ovrd_ID FROM ComplianceBatchOverride WHERE Req_ID=%s "
                                "AND UPPER(Override_Ty)='REUSE' AND UPPER(Apprvl_Stat)='APPROVED'", (b["req_id"],)).fetchone()
        self.conn.execute(
            """UPDATE ComplianceBatchOverride SET Valid_Thru_Dt_Key=%s, Updated_Dtts=%s, Updated_By='SYSTEM'
                WHERE Req_ID=%s AND UPPER(Override_Ty)='REUSE' AND UPPER(Apprvl_Stat)='APPROVED'""",
            (db.as_date(b["req_dt_key"]), now, b["req_id"]))
        self.logger.batch_event("CARRY_FORWARD_REMOVED", req_id=b["req_id"], btch_id=b["btch_id"], load_id=load_id,
                                ovrd_id=row["ovrd_id"] if row else None,
                                detail=f"reuse of {b['reuse_btch_id']} replaced by load {load_id}")

    def _supersede(self, load_id: int) -> None:
        self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='SUPERSEDED', Updated_Dtts=%s "
                          "WHERE Load_ID=%s AND Load_Stat='PROMOTED'", (self.clock.now(), load_id))

    def _rules_failed(self, load_id, detail, now) -> None:
        self.conn.execute(
            """UPDATE ComplianceFileLoad SET Load_Stat='RULES_FAILED', Rules_Stat='NOT_RUN', Error_Txt=%s, Updated_Dtts=%s
                WHERE Load_ID=%s""",
            (detail[:4000] if detail else detail, now, load_id))

    def _quarantine(self, load_id: int, info: FileInfo, event_ty: str, message: str,
                    cfg: Optional[FileConfig] = None, batch: Optional[dict] = None,
                    local_path: Optional[str] = None, rows: Optional[list] = None) -> IngestOutcome:
        error_file = self._error_file(info, event_ty, message, local_path, rows)
        if error_file:
            message = f"{message}; details in {error_file}"
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
            self.logger.audit(event_ty, load_id=load_id, file_ref=self.store.uri(info.share, info.path),
                              description=message, **ctx)
        self._archive(info, load_id, rejected=True)
        return IngestOutcome(load_id, "QUARANTINED", event_ty=event_ty, message=message,
                             req_id=batch["req_id"] if batch else None)

    def _error_file(self, info, event_ty: str, message: str, local_path: Optional[str],
                    rows: Optional[list] = None) -> Optional[str]:
        """The error log of a rejected file: `rows`, else the reason with its line and record."""
        if rows is None:
            found = re.search(r"\bline (\d+)\b", message)
            line_no = int(found.group(1)) if found else None
            record = raw_line(local_path, line_no, self.settings.file_encoding) if local_path else None
            rows = [(event_ty, line_no, message, record)]
        return write_error_log(self.store, self.settings, info.share, info.path, rows)

    def _archive(self, info: FileInfo, load_id: int, rejected: bool = False) -> None:
        """A loaded file moves to <its folder>/Archive/, one that was not loaded to <its folder>/Error/."""
        folder = self.settings.error_folder if rejected else self.settings.archive_folder
        if not rejected and self.settings.archive_by_year:
            folder = f"{folder}/{self.clock.today(self.settings.business_tz):%Y}"
        try:
            self.store.archive(info.share, info.path, folder)
        except Exception as e:  # noqa: BLE001
            log.warning("moving %s to %s/ failed: %s", info.path, folder, e)
            with self.conn.transaction():
                self.logger.audit("FILE_MOVE_FAILED", load_id=load_id, file_ref=self.store.uri(info.share, info.path),
                                  description=f"{type(e).__name__} moving to {folder}/")

    def _mark_technical_failure(self, load_id: int, err: Exception) -> None:
        try:
            with self.conn.transaction():
                marks, terminal = db.in_list(sorted(TERMINAL_LOAD_STATS))
                self.conn.execute(
                    f"""UPDATE ComplianceFileLoad SET Load_Stat='FAILED_TECHNICAL', Error_Txt=%s, Updated_Dtts=%s
                        WHERE Load_ID=%s AND Load_Stat NOT IN ({marks})""",
                    [sanitize_db_error(db.error_text(err)), self.clock.now(), load_id, *terminal])
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
            """SELECT f.File_Share, f.File_Path, f.Req_ID, f.Btch_ID, c.Project_Cd, c.Table_Nm, c.Src_ID, c.Run_Ty
                 FROM ComplianceFileLoad f LEFT JOIN ComplianceRequestControl c ON c.Req_ID = f.Req_ID
                WHERE f.Load_ID=%s""", (load_id,)).fetchone()
        self.logger.audit("FILE_TECHNICAL_FAILURE", load_id=load_id, req_id=row["req_id"], btch_id=row["btch_id"],
                          file_ref=self.store.uri(row["file_share"], row["file_path"]),
                          project_cd=row["project_cd"] or self._scope_project, table_nm=row["table_nm"],
                          src_id=row["src_id"], run_ty=row["run_ty"],
                          description=sanitize_db_error(db.error_text(err))[:500])
