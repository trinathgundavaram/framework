"""Optional pre-load check of inbound files (FILE_CHECK): reads each file where it is and reports every problem.

It registers no load, moves no file and changes no batch; the result is the step's outcome and one audit row per file.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from . import config as cfgmod
from .adapters import ObjectStore, basename, dirname, parse_uri
from .audit import EventLogger
from .common import Clock, ConfigError, FileRejected, code
from .config import FileConfig, MatchError
from .load import (DELIMITED_FILE_TYPES, _file_lines, _lf_lines, _undecodable_line, delimiter_for, read_file,
                   staging_business_columns)
from .settings import Settings

log = logging.getLogger(__name__)

MAX_LISTED = 5
TEXT_MAX = 4000
TRAILER_FIELDS = ("RECORD_TYPE", "FILE_NAME", "DATA_ROW_COUNT", "TOTAL_ROW_COUNT", "TIMESTAMP", "SKIP")
OPTION_KEYS = ("record_types", "trailer_fields", "header")
_TIMESTAMP_FORMATS = {8: "%Y%m%d", 12: "%Y%m%d%H%M", 14: "%Y%m%d%H%M%S"}
Finding = tuple[str, str]


@dataclass(frozen=True)
class CheckOptions:
    record_types: Optional[tuple[str, str, str]] = None
    trailer_fields: tuple[str, ...] = ()
    header: Optional[str] = None


def _names(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(i).strip() for i in items if str(i).strip()]


def check_options(cfg: FileConfig, settings: Settings) -> CheckOptions:
    """The content checks of a file config: its File_Check_Txt (JSON) over the CHECK_* settings of the run."""
    raw = {"record_types": settings.check_record_types, "trailer_fields": settings.check_trailer_fields, "header": None}
    if cfg.file_check_txt:
        what = f"File_Check_Txt of Cfg_ID {cfg.cfg_id}"
        try:
            own = json.loads(cfg.file_check_txt)
        except ValueError as e:
            raise ConfigError(f"{what} is not valid JSON: {e}") from None
        if not isinstance(own, dict):
            raise ConfigError(f"{what} must be a JSON object")
        unknown = sorted(k for k in own if str(k).lower() not in OPTION_KEYS)
        if unknown:
            raise ConfigError(f"{what} has unknown key {unknown[0]!r}; accepted: {', '.join(OPTION_KEYS)}")
        raw.update({str(k).lower(): v for k, v in own.items()})
    types = _names(raw["record_types"])
    if types and len(types) != 3:
        raise ConfigError("record_types must be three values: header, detail, trailer (for example H,D,T)")
    fields = tuple(f.upper() for f in _names(raw["trailer_fields"]))
    bad = [f for f in fields if f not in TRAILER_FIELDS]
    if bad:
        raise ConfigError(f"trailer_fields has unknown value {bad[0]!r}; accepted: {', '.join(TRAILER_FIELDS)}")
    header = raw["header"]
    if header is not None and not isinstance(header, str):
        raise ConfigError("header must be text: the expected header line")
    return CheckOptions(tuple(types) if types else None, fields, header.strip() if header and header.strip() else None)


class _Lines:
    """How many lines have a problem, and the first few of them."""

    def __init__(self):
        self.count = 0
        self.first: list[str] = []

    def add(self, line_no: int, detail: Optional[str] = None) -> None:
        self.count += 1
        if len(self.first) < MAX_LISTED:
            self.first.append(f"{line_no}" if detail is None else f"{line_no} ({detail})")

    def text(self) -> str:
        more = f" and {self.count - len(self.first)} more" if self.count > len(self.first) else ""
        return f"line {', '.join(self.first)}{more}"


def _is_timestamp(value: str) -> bool:
    fmt = _TIMESTAMP_FORMATS.get(len(value))
    if not fmt or not value.isdigit():
        return False
    try:
        datetime.strptime(value, fmt)
    except ValueError:
        return False
    return True


def _header_diff(rec: list[str], expected: str, delim: str, header_code: Optional[str]) -> list[str]:
    want = [v.strip() for v in expected.split(delim if delim in expected else ",")]
    got = [v.strip() for v in rec]
    first = 1
    if header_code:
        first, got = 2, got[1:]
        if want[0] == header_code:
            want = want[1:]
    diffs = [f"column {i}: {g!r}, expected {w!r}" for i, (g, w) in enumerate(zip(got, want), first)
             if g.casefold() != w.casefold()]
    if len(got) != len(want):
        diffs.insert(0, f"{len(got)} names, expected {len(want)}")
    return diffs


def check_content(path: str, name: str, cfg: FileConfig, expected_cols: int, settings: Settings,
                  opts: CheckOptions) -> list[Finding]:
    """Every content problem of a downloaded file; nothing is loaded anywhere."""
    out: list[Finding] = []
    if cfg.src_file_ty not in DELIMITED_FILE_TYPES or cfg.src_file_ty not in settings.supported_file_types:
        try:
            rows = sum(1 for _ in read_file(path, cfg, expected_cols, settings))
        except FileRejected as e:
            return [(e.event_ty.removeprefix("FILE_"), str(e))]
        if rows == 0 and not cfg.allow_zero_records:
            out.append(("ZERO_RECORDS", "file has no data rows"))
        return out

    delim = delimiter_for(cfg)
    header_code, detail_code, trailer_code = opts.record_types or (None, None, None)
    blank, wrong_type, wrong_cols = _Lines(), _Lines(), _Lines()
    counts = {"content": 0, "data": 0}

    def detail(line_no: int, rec: list[str]) -> None:
        counts["data"] += 1
        if detail_code and rec[0] != detail_code:
            wrong_type.add(line_no, repr(rec[0]))
        if len(rec) != expected_cols:
            wrong_cols.add(line_no, f"{len(rec)}")

    reader = csv.reader(_lf_lines(_file_lines(path, settings.file_encoding)), delimiter=delim,
                        quotechar=settings.quote_char or None, strict=True)
    header = held = None
    pending_blank: list[int] = []
    try:
        for rec in reader:
            if not rec or (len(rec) == 1 and not rec[0].strip()):
                pending_blank.append(reader.line_num)
                continue
            for line_no in pending_blank:
                blank.add(line_no)
            pending_blank = []
            counts["content"] += 1
            if counts["content"] == 1 and cfg.has_header:
                header = (reader.line_num, rec)
                continue
            if held:
                detail(*held)
            held = (reader.line_num, rec)
    except UnicodeDecodeError as e:
        line = _undecodable_line(path, settings.file_encoding)
        return [("ENCODING", f"file is not valid {settings.file_encoding}{f' at line {line}' if line else ''}: {e.reason}")]
    except csv.Error as e:
        return [("MALFORMED", f"line {reader.line_num}: {e}")]
    trailer = None
    if cfg.has_trailer:
        trailer = held
    elif held:
        detail(*held)

    if cfg.has_header and header is None:
        out.append(("HEADER", "header expected but the file has no lines"))
    elif header:
        line_no, rec = header
        if header_code and rec[0] != header_code:
            out.append(("RECORD_TYPE", f"line {line_no}: header record type {rec[0]!r}, expected {header_code!r}"))
        if len(rec) != expected_cols:
            out.append(("HEADER", f"line {line_no}: header has {len(rec)} columns, expected {expected_cols}"))
        if opts.header:
            diffs = _header_diff(rec, opts.header, delim, header_code)
            if diffs:
                more = f" and {len(diffs) - MAX_LISTED} more" if len(diffs) > MAX_LISTED else ""
                out.append(("HEADER", f"line {line_no}: header is not the expected one: "
                                      f"{'; '.join(diffs[:MAX_LISTED])}{more}"))
    if blank.count:
        out.append(("BLANK_LINE", f"{blank.count} blank line(s) inside the file: {blank.text()}"))
    if wrong_type.count:
        out.append(("RECORD_TYPE", f"{wrong_type.count} row(s) whose record type is not {detail_code!r}: "
                                   f"{wrong_type.text()}"))
    if wrong_cols.count:
        out.append(("COLUMN_COUNT", f"{wrong_cols.count} row(s) without {expected_cols} columns: {wrong_cols.text()}"))
    if cfg.has_trailer and trailer is None:
        out.append(("TRAILER", "trailer expected but not found"))
    elif trailer:
        out.extend(_check_trailer(trailer, name, delim, trailer_code, opts, settings, counts))
    if counts["data"] == 0 and not cfg.allow_zero_records:
        out.append(("ZERO_RECORDS", "file has no data rows"))
    return out


def _check_trailer(trailer, name: str, delim: str, trailer_code, opts: CheckOptions, settings: Settings,
                   counts: dict) -> list[Finding]:
    line_no, rec = trailer
    out: list[Finding] = []
    if trailer_code and rec[0] != trailer_code:
        out.append(("RECORD_TYPE", f"line {line_no}: trailer record type {rec[0]!r}, expected {trailer_code!r}"))
    if opts.trailer_fields:
        if len(rec) != len(opts.trailer_fields):
            out.append(("TRAILER", f"line {line_no}: trailer has {len(rec)} fields, expected "
                                   f"{len(opts.trailer_fields)} ({', '.join(opts.trailer_fields)})"))
        for value, kind in zip(rec, opts.trailer_fields):
            value = value.strip()
            if kind == "FILE_NAME":
                same = value == name if settings.filename_case_sensitive else value.casefold() == name.casefold()
                if not same:
                    out.append(("TRAILER", f"line {line_no}: trailer file name {value!r} is not the name of the file"))
            elif kind in ("DATA_ROW_COUNT", "TOTAL_ROW_COUNT"):
                actual, unit = (counts["data"], "data rows") if kind == "DATA_ROW_COUNT" else (counts["content"], "lines")
                if not value.isdigit() or int(value) != actual:
                    out.append(("TRAILER", f"line {line_no}: trailer count {value!r}, the file has {actual} {unit}"))
            elif kind == "TIMESTAMP" and not _is_timestamp(value):
                out.append(("TRAILER", f"line {line_no}: trailer timestamp {value!r} is not a valid "
                                       "YYYYMMDD, YYYYMMDDHHMM or YYYYMMDDHHMMSS"))
    elif settings.trailer_count_check:
        m = re.search(settings.trailer_count_regex, delim.join(rec))
        if not m:
            out.append(("TRAILER", f"line {line_no}: trailer record count not found"))
        elif int(m.group(1)) != counts["data"]:
            out.append(("TRAILER", f"line {line_no}: trailer count {m.group(1)}, the file has {counts['data']} data rows"))
    return out


@dataclass
class FileCheckSummary:
    run_date: Optional[date] = None
    checked: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    locations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)


class FileChecker:
    def __init__(self, conn, clock: Clock, settings: Settings, store: ObjectStore, pipeline):
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self.store = store
        self.pipeline = pipeline
        self.logger = EventLogger(conn, clock)

    def run(self, project_cd: Optional[str] = None) -> FileCheckSummary:
        """Check the files waiting in one project's inbound folders, or in every configured folder."""
        project_cd = code(project_cd)
        summary = FileCheckSummary(run_date=self.clock.today(self.settings.business_tz))
        own, shared = None, set()
        if project_cd:
            locations, shared, own = self.pipeline._project_locations(project_cd)
            if not locations:
                summary.errors.append(f"project {project_cd} has no active file config")
        else:
            locations = self.pipeline._configured_locations()
        summary.locations = [f"{s}/{f}" for s, f in locations]
        matcher, run_type_codes = self.pipeline._load_config()
        for share, folder in locations:
            try:
                files = self.store.list_objects(share, folder)
            except Exception as e:  # noqa: BLE001
                summary.errors.append(f"{share}/{folder}: {type(e).__name__}: {e}")
                continue
            for info in files:
                if (share, folder) in shared and not own.candidates(basename(info.key)):
                    summary.skipped += 1
                    continue
                try:
                    result = self.check_file(info, matcher, run_type_codes)
                except Exception as e:  # noqa: BLE001
                    log.warning("could not check %s/%s", share, info.key, exc_info=True)
                    summary.errors.append(f"{share}/{info.key}: {type(e).__name__}: {e}")
                    continue
                summary.checked += 1
                summary.passed += result["result"] == "PASSED"
                summary.failed += result["result"] == "FAILED"
                summary.files.append(result)
        return summary

    def check_file(self, info, matcher, run_type_codes) -> dict:
        name = basename(info.key)
        findings: list[Finding] = []
        cfg = batch = run_ty = x = None
        notes: list[str] = []
        try:
            m = matcher.match(name)
        except MatchError as e:
            m, cfg = None, e.cfg
            findings.append(("FILE_NAME", str(e)))
        if m:
            cfg = m.cfg
            share, folder = parse_uri(cfg.s3_src_file_path)
            if info.bucket != share or dirname(info.key) != folder:
                findings.append(("FILE_NAME", f"{name} matched Cfg_ID {cfg.cfg_id} but is not in its inbound location"))
            run_ty = self.pipeline._resolve_run_type(m.run_ty, run_type_codes)
            ref_date = m.rpt_start if self.settings.file_effective_date_basis == "RPT_START" else m.rpt_end
            where = f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id}"
            if run_ty:
                x = cfgmod.effective_xwalk(self.conn, cfg.project_cd, cfg.table_nm, cfg.src_id, run_ty, ref_date)
            if x is None:
                findings.append(("RUN_TYPE", f"run type {m.run_ty!r} is not configured/effective for {where} on {ref_date}"))
            else:
                batch, why = self.pipeline._select_batch(cfg, run_ty, m.rpt_start, m.rpt_end)
                if batch is None:
                    detail = ("every batch for this period is closed and no approved, valid override exists"
                              if why == "CLOSED" else "no batch exists for this period")
                    findings.append(("BATCH", f"{where}/{run_ty} {m.rpt_start}..{m.rpt_end}: {detail}"))
                elif why:
                    notes.append(f"batch {batch['btch_id']} is closed ({batch['req_stat']}); the file matches it only "
                                 f"because an approved {why} override is valid")
                elif self.pipeline._has_data(batch):
                    notes.append(f"batch {batch['btch_id']} already has data ({batch['req_stat']}); "
                                 "loading this file replaces it")
            if not self.settings.load_duplicate:
                loaded = self.conn.execute(
                    """SELECT Load_ID FROM ComplianceFileLoad WHERE S3_Bucket=%s AND S3_Key=%s
                          AND Load_Stat IN ('PROMOTED','SUPERSEDED') ORDER BY Load_ID DESC""",
                    (info.bucket, info.key)).fetchone()
                if loaded:
                    findings.append(("DUPLICATE", f"{name} was already loaded (load {loaded['load_id']})"))
        if cfg is not None and self.settings.check_content:
            findings.extend(self._content(info, name, cfg))
        result = {"file": self.store.uri(info.bucket, info.key),
                  "config": f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id}" if cfg else None,
                  "run_type": run_ty, "version": x.cmplnc_vrsn if x else None,
                  "rpt_start": str(m.rpt_start) if m else None, "rpt_end": str(m.rpt_end) if m else None,
                  "batch": batch["btch_id"] if batch else None, "req_id": batch["req_id"] if batch else None,
                  "result": "FAILED" if findings else "PASSED",
                  "findings": [f"{c}: {text}" for c, text in findings], "notes": notes}
        self._record(result, cfg, batch, run_ty)
        return result

    def _content(self, info, name: str, cfg: FileConfig) -> list[Finding]:
        try:
            opts = check_options(cfg, self.settings)
            expected_cols = len(staging_business_columns(self.conn, cfg.stg_schema_nm, cfg.stg_table_nm))
        except (ConfigError, LookupError) as e:
            return [("CONFIG", str(e))]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            self.store.download(info.bucket, info.key, path)
            return check_content(path, name, cfg, expected_cols, self.settings, opts)

    def _record(self, result: dict, cfg: Optional[FileConfig], batch: Optional[dict], run_ty: Optional[str]) -> None:
        """One audit row per file and outcome; a repeat of the same outcome for the same file adds nothing."""
        event = f"FILE_CHECK_{result['result']}"
        text = ("; ".join(result["findings"]) or "all checks passed")[:TEXT_MAX]
        last = self.conn.execute(
            """SELECT Event_Ty, Event_Txt FROM CMS_ComplianceExceptionsAudit WHERE File_Ref=%s
                  AND Event_Ty IN ('FILE_CHECK_PASSED','FILE_CHECK_FAILED') ORDER BY Event_ID DESC""",
            (result["file"],)).fetchone()
        if last and last["event_ty"] == event and last["event_txt"] == text:
            return
        with self.conn.transaction():
            self.logger.audit(event, description=text, file_ref=result["file"],
                              project_cd=cfg.project_cd if cfg else None, table_nm=cfg.table_nm if cfg else None,
                              src_id=cfg.src_id if cfg else None, run_ty=run_ty,
                              req_id=batch["req_id"] if batch else None, btch_id=batch["btch_id"] if batch else None)
