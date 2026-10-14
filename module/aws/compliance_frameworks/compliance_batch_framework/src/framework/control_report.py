"""The control report of a FILE_LOAD run: one line per file, in <inbound folder>/<CONTROL_REPORT_FOLDER>/."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from .adapters import basename, dirname
from .common import code
from .config import MatchError

log = logging.getLogger(__name__)

HEADER = ("Source_System", "File_Name", "Status", "Total_Rows_In_File", "Total_Rows_Loaded", "File_Submitted_Date",
          "File_Processed_Date", "Error_Message")
ACCEPTED = ("PROMOTED", "SUPERSEDED")
REJECTED = ("QUARANTINED", "RULES_FAILED")
FAILED = ("FAILED_TECHNICAL",)
MESSAGES = {
    "FILE_REJECTED_UNPARSEABLE": "The file name does not follow the agreed naming format.",
    "FILE_REJECTED_AMBIGUOUS_TEMPLATE": "The file name fits more than one file setup, so the file could not be identified.",
    "FILE_REJECTED_INVALID_TOKEN": "The file name has a date or version that is not valid.",
    "FILE_REJECTED_RUNTY_NOT_CONFIGURED": "The run type in the file name is not set up.",
    "FILE_REJECTED_NO_BATCH": "No request exists for this run type and reporting period.",
    "FILE_REJECTED_BATCH_CLOSED": "The request for this reporting period is already closed.",
    "FILE_REJECTED_DUPLICATE": "This file was already received and loaded.",
    "FILE_TYPE_NOT_SUPPORTED": "The file type is not supported.",
    "FILE_ZERO_RECORDS_REJECTED": "The file has no data records.",
    "FILE_COLUMN_COUNT_MISMATCH": "Some records do not have the expected number of columns.",
    "FILE_TRAILER_COUNT_MISMATCH": "The record count in the trailer does not match the records in the file.",
    "FILE_PARSE_ERROR": "The file contains data that is not in the expected format.",
    "FILE_CONTENT_CHECK_FAILED": "The file did not pass the file checks.",
    "RULES_FAILED": "The file did not pass the data quality rules.",
    "FAILED_TECHNICAL": "The file could not be processed because of a system issue. It will be tried again automatically.",
}
DEFAULT_MESSAGE = "The file could not be loaded."
WITH_LINE = ("FILE_PARSE_ERROR", "FILE_COLUMN_COUNT_MISMATCH", "FILE_CONTENT_CHECK_FAILED")
ERROR_LOG_HEADER = "File_Name|Check_Cd|Line_No|Message|Record"
LOCATION = ("s3_bucket", "s3_key")


def _local(value, tz: str) -> str:
    if isinstance(value, datetime):
        return (value.astimezone(ZoneInfo(tz)) if value.tzinfo else value).strftime("%Y-%m-%d %H:%M:%S")
    return "" if value is None else str(value)[:19]


def write_error_log(store, settings, share: str, path: str, rows: list) -> Optional[str]:
    """Write <folder>/<ERROR_LOG_FOLDER>/<file name>.errors.txt: one (check, line, message, record) per row.

    Returns where it was written, or None when error logs are off or the file could not be written.
    """
    if not settings.write_error_file:
        return None
    name = basename(path)
    target = f"{dirname(path)}{settings.error_log_folder}/{name}.errors.txt"
    lines = [ERROR_LOG_HEADER, *("|".join("" if v is None else str(v) for v in (name, *row)) for row in rows)]
    try:
        store.put(share, target, ("\n".join(lines) + "\n").encode(settings.file_encoding, "replace"))
    except Exception as e:  # noqa: BLE001
        log.warning("could not write the error log of %s: %s", name, e)
        return None
    return store.uri(share, target)


def business_message(load: dict) -> str:
    """A plain-language reason for a file that was not loaded; never the technical error text."""
    stat = code(load["load_stat"])
    reason = code(load["quarantine_rsn_cd"]) if stat == "QUARANTINED" else stat
    text = MESSAGES.get(reason, DEFAULT_MESSAGE)
    line = re.search(r"\bline (\d+)\b", str(load["error_txt"] or "")) if reason in WITH_LINE else None
    return f"{text[:-1]} (first at line {line.group(1)})." if line else text


def report_line(conn, load: dict, matcher, tz: str, sources: dict) -> list:
    name = basename(load[LOCATION[1]])
    src = submitted = None
    if load["cfg_id"] is not None:
        cfg = conn.execute("SELECT Src_ID FROM ComplianceSourceFileConfig WHERE Cfg_ID=%s", (load["cfg_id"],)).fetchone()
        src = code(cfg["src_id"]) if cfg else None
    try:
        submitted = matcher.match(name).file_ts
    except MatchError:
        pass
    stat = code(load["load_stat"])
    accepted = stat in ACCEPTED
    rows = load["stg_rcd_cnt"]
    return [sources.get(src, src), name, "ACCEPTED" if accepted else "REJECTED" if stat in REJECTED else "FAILED" if stat in FAILED else stat,
            rows, (rows or 0) if accepted else 0, submitted.date() if submitted else None,
            _local(load["updated_dtts"], tz), None if accepted else business_message(load)]


def write_reports(conn, store, settings, clock, load_ids: list, matcher) -> list[str]:
    """One report per inbound folder for the files this run handled; returns where they were written."""
    if not settings.write_control_report or not load_ids:
        return []
    sources = {code(r["src_id"]): r["src_nm"] for r in conn.execute(
        "SELECT Src_ID, Src_Nm FROM ComplianceSourceSystem").fetchall()}
    by_folder: dict = {}
    for load_id in dict.fromkeys(load_ids):
        load = conn.execute("SELECT * FROM ComplianceFileLoad WHERE Load_ID=%s", (load_id,)).fetchone()
        if load:
            by_folder.setdefault((load[LOCATION[0]], dirname(load[LOCATION[1]])), []).append(
                report_line(conn, load, matcher, settings.business_tz, sources))
    stamp = clock.now().astimezone(ZoneInfo(settings.business_tz)).strftime("%Y%m%d_%H%M%S")
    written = []
    for (share, folder), lines in by_folder.items():
        target = f"{folder}{settings.control_report_folder}/ControlReport_{stamp}.txt"
        text = "\n".join("|".join("" if v is None else str(v) for v in row) for row in [HEADER, *lines]) + "\n"
        try:
            store.put(share, target, text.encode(settings.file_encoding, "replace"))
            written.append(store.uri(share, target))
        except Exception as e:  # noqa: BLE001
            log.warning("could not write the control report %s: %s", target, e)
    return written
