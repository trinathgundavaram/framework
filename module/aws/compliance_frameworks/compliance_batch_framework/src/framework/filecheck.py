"""Optional pre-load check of inbound files (FILE_CHECK): reads each file where it is and reports every problem.

It registers no load, moves no file and changes no batch; the result is the step's outcome and one audit row per file.
"""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from . import config as cfgmod
from . import db
from .adapters import ObjectStore, basename, dirname, parse_uri
from .audit import EventLogger
from .common import Clock, ConfigError, btch_id_version, code
from .control_report import write_error_log
from .config import FileConfig, MatchError
from .filecheck_options import CheckOptions, check_options, data_fields, trailer_problems
from .load import (DELIMITED_FILE_TYPES, _file_lines, _lf_lines, _undecodable_line, delimiter_for, file_layout,
                   header_columns, raw_line)
from .settings import Settings

log = logging.getLogger(__name__)

MAX_LISTED = 5
MAX_DETAILS = 5000
TEXT_MAX = 4000
Finding = tuple[str, str]
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


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


def _header_diff(rec: list[str], names: tuple, header_code: Optional[str]) -> list[str]:
    """How a header line differs from the HEADER_NAMES list (record type field left out)."""
    got = [v.strip() for v in rec]
    first = 1
    if header_code:
        first, got = 2, got[1:]
    diffs = [f"column {i}: {g!r}, expected {w!r}" for i, (g, w) in enumerate(zip(got, names), first)
             if g.casefold() != w.casefold()]
    if len(got) != len(names):
        diffs.insert(0, f"{len(got)} names, expected {len(names)}")
    return diffs


def _value_problem(value: str, col, rules: tuple, empty_is_null: bool) -> Optional[str]:
    """Why the target column cannot hold a value of the file, or None."""
    if value == "" and empty_is_null:
        return f"column {col.name} is empty but the target column is NOT NULL" if "REQUIRED" in rules and not col.nullable else None
    if "LENGTH" in rules and col.length and len(value) > col.length:
        return f"column {col.name}: {len(value)} characters, the target column holds {col.length}"
    if "NUMBER" in rules and col.numeric:
        try:
            Decimal(value.strip())
        except InvalidOperation:
            return f"column {col.name}: {value!r} is not a number"
    return None


def error_rows(findings: list, details: list) -> list:
    """The error log rows of a file: one per problem line, plus the problems that have no line."""
    covered = {d[0] for d in details}
    return [(c, None, text, None) for c, text in findings if c not in covered] + details


def file_columns(layout) -> list:
    """The staging column definition of each field of the file, in file order (for COLUMN_VALUES)."""
    by_name = {c.name: c for c in layout.stage}
    return [by_name[n] for n in layout.file_cols]


def check_content(path: str, name: str, cfg: FileConfig, expected_cols: Optional[int], settings: Settings,
                  opts: CheckOptions, details: Optional[list] = None, columns: Optional[list] = None) -> list[Finding]:
    """Every content problem of a downloaded file; nothing is loaded anywhere.

    `details` collects one (check, line number, message, record) per problem line, for the error file.
    """
    out: list[Finding] = []
    details = details if details is not None else []
    delim = delimiter_for(cfg) if cfg.src_file_ty in DELIMITED_FILE_TYPES else ","

    def note(check: str, line_no: Optional[int], message: str, rec: Optional[list] = None) -> None:
        if len(details) < MAX_DETAILS:
            details.append((check, line_no, message, rec if rec is None or isinstance(rec, str) else delim.join(rec)))
    if cfg.src_file_ty not in DELIMITED_FILE_TYPES or cfg.src_file_ty not in settings.supported_file_types:
        return [("TYPE_NOT_SUPPORTED", f"file type {cfg.src_file_ty} is not supported")]
    names = header_columns(cfg, opts)

    header_code, detail_code, _ = opts.record_types or (None, None, None)
    blank, wrong_type, wrong_cols, wrong_chars, wrong_values = _Lines(), _Lines(), _Lines(), _Lines(), _Lines()

    def characters(line_no: int, rec: list[str]) -> None:
        text = delim.join(rec)
        if text.isascii() and text.isprintable():
            return
        control = _CONTROL.search(text)
        if control:
            wrong_chars.add(line_no, f"U+{ord(control.group()):04X}")
            note("CHARACTER_SET", line_no, f"control character U+{ord(control.group()):04X}", rec)
        try:
            text.encode(opts.charset)
        except UnicodeEncodeError as e:
            ch = e.object[e.start]
            wrong_chars.add(line_no, repr(ch))
            note("CHARACTER_SET", line_no, f"character {ch!r} (U+{ord(ch):04X}) is not in {opts.charset}", rec)

    rules = opts.value_rules if columns else ()
    shortest = min((c.length for c in columns or () if c.length), default=None) if "LENGTH" in rules else None
    required = "REQUIRED" in rules and settings.empty_as_null and any(not c.nullable for c in columns or ())
    offset = 1 if opts.record_types else 0
    numeric = [i + offset for i, c in enumerate(columns or ()) if c.numeric] if "NUMBER" in rules else []

    def suspicious(rec: list[str]) -> bool:
        """A cheap test of the whole row; only a row that fails it is checked value by value."""
        if shortest is not None and max(map(len, rec)) > shortest:
            return True
        if required and "" in rec:
            return True
        return any(_value_problem(rec[i], columns[i - offset], ("NUMBER",), settings.empty_as_null) for i in numeric)
    counts = {"content": 0, "data": 0}

    def detail(line_no: int, rec: list[str]) -> None:
        counts["data"] += 1
        if detail_code and rec[0] != detail_code:
            wrong_type.add(line_no, repr(rec[0]))
            note("RECORD_TYPE", line_no, f"record type {rec[0]!r}, expected {detail_code!r}", rec)
        if expected_cols is not None and len(rec) != expected_cols:
            wrong_cols.add(line_no, f"{len(rec)}")
            note("COLUMN_COUNT", line_no, f"{len(rec)} columns, expected {expected_cols}", rec)
        elif rules and len(rec) == len(columns) + offset and suspicious(rec):
            problems = [p for p in (_value_problem(v, c, rules, settings.empty_as_null)
                                    for v, c in zip(rec[offset:], columns)) if p]
            if problems:
                wrong_values.add(line_no, problems[0].split(":")[0].replace("column ", "").split(" ")[0])
                for problem in problems:
                    note("COLUMN_VALUES", line_no, problem, rec)

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
                note("BLANK_LINE", line_no, "blank line inside the file")
            pending_blank = []
            counts["content"] += 1
            if opts.charset:
                characters(reader.line_num, rec)
            if counts["content"] == 1 and cfg.has_header:
                header = (reader.line_num, rec)
                continue
            if held:
                detail(*held)
            held = (reader.line_num, rec)
    except UnicodeDecodeError as e:
        line = _undecodable_line(path, settings.file_encoding)
        note("ENCODING", line, f"not valid {settings.file_encoding}: {e.reason}", raw_line(path, line, settings.file_encoding) or "")
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
        before = len(out)
        if header_code and rec[0] != header_code:
            out.append(("RECORD_TYPE", f"line {line_no}: header record type {rec[0]!r}, expected {header_code!r}"))
        if expected_cols is not None and len(rec) != expected_cols:
            out.append(("HEADER", f"line {line_no}: header has {len(rec)} columns, expected {expected_cols}"))
        if names:
            diffs = _header_diff(rec, names, header_code)
            if diffs:
                more = f" and {len(diffs) - MAX_LISTED} more" if len(diffs) > MAX_LISTED else ""
                out.append(("HEADER", f"line {line_no}: header is not the expected one: "
                                      f"{'; '.join(diffs[:MAX_LISTED])}{more}"))
        for check, text in out[before:]:
            note(check, line_no, text.split(": ", 1)[1], rec)
    if blank.count:
        out.append(("BLANK_LINE", f"{blank.count} blank line(s) inside the file: {blank.text()}"))
    if wrong_type.count:
        out.append(("RECORD_TYPE", f"{wrong_type.count} row(s) whose record type is not {detail_code!r}: "
                                   f"{wrong_type.text()}"))
    if wrong_chars.count:
        out.append(("CHARACTER_SET", f"{wrong_chars.count} row(s) with a control character or a character that is not in {opts.charset}: "
                                     f"{wrong_chars.text()}"))
    if wrong_values.count:
        out.append(("COLUMN_VALUES", f"{wrong_values.count} row(s) with a value the target column cannot hold: "
                                     f"{wrong_values.text()}"))
    if wrong_cols.count:
        out.append(("COLUMN_COUNT", f"{wrong_cols.count} row(s) without {expected_cols} columns: {wrong_cols.text()}"))
    if cfg.has_trailer and trailer is None:
        out.append(("TRAILER", "trailer expected but not found"))
    elif trailer:
        found = trailer_problems(trailer[1], trailer[0], name, opts, settings.filename_case_sensitive, counts["data"],
                                 counts["content"])
        for check, text in found:
            note(check, trailer[0], text.split(": ", 1)[1], trailer[1])
        out.extend(found)
    if counts["data"] == 0 and not cfg.allow_zero_records:
        out.append(("ZERO_RECORDS", "file has no data rows"))
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
        self._sources: Optional[dict] = None
        self._checks: Optional[list] = None
        self._config_errors: dict = {}

    def run(self, project_cd: Optional[str] = None) -> FileCheckSummary:
        """Check the files waiting in one project's inbound folders, or in every configured folder."""
        project_cd = code(project_cd)
        self._sources = self._checks = None
        self._config_errors = {}
        summary = FileCheckSummary(run_date=self.clock.today(self.settings.business_tz))
        own, shared = None, set()
        if project_cd:
            locations, shared, own = self.pipeline._project_locations(project_cd)
            if not locations:
                summary.errors.append(f"project {project_cd} has no active file config")
        else:
            locations = self.pipeline._configured_locations()
        summary.locations = [f"{s}/{f}" for s, f in locations]
        matcher = self.pipeline._load_config()
        for share, folder in locations:
            try:
                files = self.store.list_objects(share, folder)
            except Exception as e:  # noqa: BLE001
                summary.errors.append(f"{share}/{folder}: {db.error_text(e)}")
                continue
            for info in files:
                if (share, folder) in shared and not own.candidates(basename(info.key)):
                    summary.skipped += 1
                    continue
                try:
                    result = self.check_file(info, matcher)
                except Exception as e:  # noqa: BLE001
                    log.warning("could not check %s/%s", share, info.key, exc_info=True)
                    summary.errors.append(f"{share}/{info.key}: {db.error_text(e)}")
                    continue
                if result["result"] == "NOT_CHECKED":
                    summary.skipped += 1
                    continue
                summary.checked += 1
                summary.passed += result["result"] == "PASSED"
                summary.failed += result["result"] == "FAILED"
                summary.files.append(result)
        summary.errors.extend(f"{where} is not set up correctly, its files were not checked: {text}"
                              for where, text in self._config_errors.items())
        return summary

    def check_file(self, info, matcher) -> dict:
        name = basename(info.key)
        findings: list[Finding] = []
        details: list = []
        cfg = batch = run_ty = None
        checked = True
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
            run_ty = code(m.run_ty)
            where = f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id}"
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
        if cfg is not None:
            findings.extend(self._source(cfg))
        if cfg is not None and self.settings.check_content:
            found = self._content(info, name, cfg, details)
            checked = found is not None
            findings.extend(found or [])
        version = (btch_id_version(batch["btch_id"], cfg.project_cd, cfg.table_nm, cfg.src_id, run_ty)
                   if batch else None)
        result = {"file": self.store.uri(info.bucket, info.key),
                  "config": f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id}" if cfg else None,
                  "run_type": run_ty, "version": version,
                  "rpt_start": str(m.rpt_start) if m else None, "rpt_end": str(m.rpt_end) if m else None,
                  "batch": batch["btch_id"] if batch else None, "req_id": batch["req_id"] if batch else None,
                  "result": "FAILED" if findings else "PASSED" if checked else "NOT_CHECKED",
                  "findings": [f"{c}: {text}" for c, text in findings], "notes": notes}
        if findings:
            self._after_failure(info, name, result, findings, details)
        if checked or findings:
            self._record(result, cfg, batch, run_ty)
        return result

    def _after_failure(self, info, name: str, result: dict, findings: list, details: list) -> None:
        """Write the error log (one line per problem, with the record) and, with CHECK_MOVE_FAILED, move the file."""
        written = write_error_log(self.store, self.settings, info.bucket, info.key, error_rows(findings, details))
        if written:
            result["error_file"] = written
        if self.settings.check_move_failed:
            try:
                result["moved_to"] = self.store.uri(info.bucket, self.store.archive(info.bucket, info.key, self.settings.error_folder))
            except Exception as e:  # noqa: BLE001
                log.warning("could not move %s to %s/: %s", name, self.settings.error_folder, e)

    def _source(self, cfg: FileConfig) -> list[Finding]:
        if self._sources is None:
            self._sources = {code(r["src_id"]): r["active_ind"] == 1 for r in self.conn.execute(
                "SELECT Src_ID, Active_Ind FROM ComplianceSourceSystem").fetchall()}
        active = self._sources.get(cfg.src_id)
        if active is None:
            return [("SOURCE", f"source {cfg.src_id} of {cfg.project_cd}/{cfg.table_nm} is not in ComplianceSourceSystem")]
        return [] if active else [("SOURCE", f"source {cfg.src_id} of {cfg.project_cd}/{cfg.table_nm} is inactive")]

    def _content(self, info, name: str, cfg: FileConfig, details: list) -> Optional[list[Finding]]:
        """The problems in the file itself; None = its configuration is wrong, which is not a problem of the file."""
        try:
            if self._checks is None:
                self._checks = cfgmod.file_checks(self.conn)
            opts = check_options(cfg, self._checks)
            delimiter_for(cfg)
            expected_cols = data_fields(opts, header_columns(cfg, opts))
            columns = None
            if opts.value_rules:
                layout = file_layout(self.conn, cfg, cfgmod.target_columns(self.conn), opts)
                columns = file_columns(layout)
                expected_cols = expected_cols or len(columns) + (1 if opts.record_types else 0)
        except (ConfigError, LookupError) as e:
            self._config_errors.setdefault(f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id}", str(e))
            return None
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            self.store.download(info.bucket, info.key, path)
            return check_content(path, name, cfg, expected_cols, self.settings, opts, details, columns)

    def _record(self, result: dict, cfg: Optional[FileConfig], batch: Optional[dict], run_ty: Optional[str]) -> None:
        """One audit row per file and outcome; a repeat of the same outcome for the same file adds nothing."""
        event = f"FILE_CHECK_{result['result']}"
        found = result["findings"]
        text = "all checks passed"
        if found:
            text = f"{len(found)} problem(s); first: {found[0]}"
            if result.get("error_file"):
                text += f"; details in {result['error_file']}"
        text = text[:TEXT_MAX]
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
