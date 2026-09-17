"""The only writer of ComplianceRequestFileDetail and CMS_ComplianceExceptionsAudit (design §5.3).

Callers pass ids, counts and codes only - never file content (no PHI in audit text).
Writes participate in the caller's open transaction.
"""
from __future__ import annotations

import logging
from typing import Optional

import psycopg

from ..clock import Clock
from ..errors import ConfigError

log = logging.getLogger(__name__)


class EventLogger:
    def __init__(self, conn: psycopg.Connection, clock: Clock):
        self.conn = conn
        self.clock = clock
        self._types: Optional[dict[str, dict]] = None

    def _type(self, event_ty: str, expected_tbl: str) -> dict:
        if self._types is None:
            rows = self.conn.execute("SELECT * FROM ComplianceEventType").fetchall()
            self._types = {r["event_ty"]: r for r in rows}
        et = self._types.get(event_ty)
        if et is None:
            raise ConfigError(f"Event type {event_ty} is not seeded in ComplianceEventType")
        if et["log_tbl_cd"] != expected_tbl:
            raise ConfigError(f"Event type {event_ty} belongs to {et['log_tbl_cd']}, not {expected_tbl}")
        return et

    def batch_event(self, event_ty: str, *, req_id: int, btch_id: str, actor: str = "SYSTEM",
                    entry_ty: str = "AUTO", load_id: Optional[int] = None, ovrd_id: Optional[int] = None,
                    intake_id: Optional[str] = None, trigger_id: Optional[int] = None,
                    file_ref: Optional[str] = None, detail: Optional[str] = None) -> None:
        self._type(event_ty, "FILE_DETAIL")
        self.conn.execute(
            """INSERT INTO ComplianceRequestFileDetail
               (Req_ID, Btch_ID, Load_ID, Ovrd_ID, Intake_ID, Trigger_ID, Event_Ty, Entry_Ty,
                Received_File_Ref, Actor, Detail_Txt, Event_Dtts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (req_id, btch_id, load_id, ovrd_id, intake_id, trigger_id, event_ty, entry_ty,
             file_ref, actor, detail, self.clock.now()))
        log.info("batch_event %s req_id=%s btch_id=%s load_id=%s %s", event_ty, req_id, btch_id, load_id, detail or "")

    def audit(self, event_ty: str, *, actor: str = "SYSTEM", severity: Optional[str] = None,
              description: Optional[str] = None, project_cd: Optional[str] = None,
              table_nm: Optional[str] = None, src_cd: Optional[str] = None, run_ty: Optional[str] = None,
              req_id: Optional[int] = None, load_id: Optional[int] = None, ovrd_id: Optional[int] = None,
              extract_id: Optional[int] = None, intake_id: Optional[str] = None,
              trigger_id: Optional[int] = None, btch_id: Optional[str] = None,
              file_ref: Optional[str] = None) -> None:
        et = self._type(event_ty, "EXCEPTIONS_AUDIT")
        sev = severity or et["default_sevrty"]
        self.conn.execute(
            """INSERT INTO CMS_ComplianceExceptionsAudit
               (Event_Ty, Event_Ctgy, Sevrty, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Req_ID, Load_ID,
                Ovrd_ID, Extract_ID, Intake_ID, Trigger_ID, Btch_ID, File_Ref, Actor, Event_Dtts,
                Description, Notified_Ind)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (event_ty, et["event_ctgy"], sev, project_cd, table_nm, src_cd, run_ty, req_id, load_id,
             ovrd_id, extract_id, intake_id, trigger_id, btch_id, file_ref, actor, self.clock.now(),
             description, 0 if et["notify_ind"] else 1))
        level = logging.ERROR if sev == "ERROR" else logging.WARNING if sev == "WARNING" else logging.INFO
        log.log(level, "audit %s extract=%s req=%s load=%s %s", event_ty, extract_id, req_id, load_id, description or "")
