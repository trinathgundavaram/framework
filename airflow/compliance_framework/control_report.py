"""The control report of a FILE_LOAD run: one line per file, in <inbound folder>/<CONTROL_REPORT_FOLDER>/."""
from __future__ import annotations

import logging
from datetime import datetime
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
LOCATION = ("file_share", "file_path")


def _local(value, tz: str) -> str:
    if isinstance(value, datetime):
        return (value.astimezone(ZoneInfo(tz)) if value.tzinfo else value).strftime("%Y-%m-%d %H:%M:%S")
    return "" if value is None else str(value)[:19]


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
            _local(load["updated_dtts"], tz), None if accepted else " ".join(str(load["error_txt"] or "").split())]


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
