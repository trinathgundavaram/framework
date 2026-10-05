"""Audit trail and email notifications (design §5.3, P13, D-54)."""
from __future__ import annotations

import logging
from typing import NamedTuple, Optional

from . import config as cfgmod
from . import db
from .adapters import Channel, Message
from .common import Clock, ConfigError, code
from .db import Connection
from .settings import Settings

log = logging.getLogger(__name__)

BATCH_EVENTS = frozenset({
    "BATCH_CREATED", "BATCH_CLOSED", "FILE_RECEIVED", "FILE_REPLACED_BEFORE_CLOSE", "FILE_PROMOTED",
    "FILE_RULES_FAILED", "FILE_RULES_PASSED", "LATE_ARRIVAL_PROMOTED", "CORRECTION_PROMOTED", "CARRY_FORWARD_APPLIED",
    "CARRY_FORWARD_REMOVED",
})


class AuditEvent(NamedTuple):
    category: str
    severity: str
    notify: bool


_EXC_ERROR, _EXC_WARN = AuditEvent("EXCEPTION", "ERROR", True), AuditEvent("EXCEPTION", "WARNING", True)
AUDIT_EVENTS: dict[str, AuditEvent] = {
    "INTAKE_FAILED": _EXC_ERROR,
    **dict.fromkeys(("FILE_REJECTED_UNPARSEABLE", "FILE_REJECTED_AMBIGUOUS_TEMPLATE", "FILE_REJECTED_INVALID_TOKEN",
                     "FILE_REJECTED_RUNTY_NOT_CONFIGURED", "FILE_REJECTED_NO_BATCH", "FILE_REJECTED_BATCH_CLOSED",
                     "FILE_PARSE_ERROR", "FILE_COLUMN_COUNT_MISMATCH", "FILE_TRAILER_COUNT_MISMATCH",
                     "FILE_ZERO_RECORDS_REJECTED", "FILE_TYPE_NOT_SUPPORTED"), _EXC_ERROR),
    "FILE_REJECTED_DUPLICATE": _EXC_WARN,
    "FILE_SAME_CONTENT_OTHER_BATCH": _EXC_WARN,
    "FILE_EVENT_REPLAY_IGNORED": AuditEvent("AUDIT", "INFO", False),
    "FILE_MOVE_FAILED": AuditEvent("EXCEPTION", "WARNING", False),
    "RULES_VALIDATION_FAILED": _EXC_ERROR,
    "RULES_ENGINE_TECHNICAL_FAILURE": _EXC_ERROR,
    "CORE_LOAD_ROWCOUNT_MISMATCH": _EXC_ERROR,
    "FILE_TECHNICAL_FAILURE": _EXC_ERROR,
    "OVERRIDE_APPROVED": AuditEvent("AUDIT", "WARNING", True),
    "OVERRIDE_INVALID_DETECTED": _EXC_ERROR,
    "OVERRIDE_EXPIRED": AuditEvent("AUDIT", "WARNING", True),
    "BATCH_CLOSE_BLOCKED": AuditEvent("AUDIT", "WARNING", False),
    "BATCH_CLOSE_DEFERRED_LOCKED": AuditEvent("AUDIT", "INFO", False),
    "SOURCE_MISSING_AT_CLOSE": _EXC_WARN,
    "CONFIG_VALIDATION_FAILED": _EXC_ERROR,
}


TEXT_MAX = 4000


def _text(value: Optional[str]) -> Optional[str]:
    """Event text fits its VARCHAR column."""
    return value if value is None else value[:TEXT_MAX]


class EventLogger:
    def __init__(self, conn: Connection, clock: Clock):
        self.conn = conn
        self.clock = clock

    def batch_event(self, event_ty: str, *, req_id: int, btch_id: str, actor: str = "SYSTEM",
                    load_id: Optional[int] = None, ovrd_id: Optional[int] = None, intake_id: Optional[int] = None,
                    detail: Optional[str] = None) -> None:
        if event_ty not in BATCH_EVENTS:
            raise ConfigError(f"{event_ty} is not a batch timeline event")
        self.conn.execute(
            """INSERT INTO ComplianceRequestFileDetail (Req_ID, Btch_ID, Load_ID, Ovrd_ID, Intake_ID, Event_Ty, Actor,
                 Event_Txt, Event_Dtts) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (req_id, btch_id, load_id, ovrd_id, intake_id, event_ty, actor, _text(detail), self.clock.now()))
        log.info("batch_event %s req_id=%s btch_id=%s load_id=%s %s", event_ty, req_id, btch_id, load_id, detail or "")

    def audit(self, event_ty: str, *, actor: str = "SYSTEM", severity: Optional[str] = None,
              description: Optional[str] = None, project_cd: Optional[str] = None,
              table_nm: Optional[str] = None, src_id: Optional[str] = None, run_ty: Optional[str] = None,
              req_id: Optional[int] = None, load_id: Optional[int] = None, ovrd_id: Optional[int] = None,
              intake_id: Optional[int] = None,
              btch_id: Optional[str] = None, file_ref: Optional[str] = None) -> None:
        et = AUDIT_EVENTS.get(event_ty)
        if et is None:
            raise ConfigError(f"{event_ty} is not an exceptions-audit event")
        sev = severity or et.severity
        self.conn.execute(
            """INSERT INTO CMS_ComplianceExceptionsAudit (Event_Ty, Sevrty, Project_Cd, Table_Nm, Src_ID, Run_Ty,
                 Req_ID, Load_ID, Ovrd_ID, Intake_ID, Btch_ID, File_Ref, Actor, Event_Txt, Event_Dtts, Notified_Ind)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (event_ty, sev, project_cd, table_nm, src_id, run_ty, req_id, load_id, ovrd_id, intake_id,
             btch_id, file_ref, actor, _text(description), self.clock.now(), 0 if et.notify else 1))
        level = logging.ERROR if sev == "ERROR" else logging.WARNING if sev == "WARNING" else logging.INFO
        log.log(level, "audit %s req=%s load=%s %s", event_ty, req_id, load_id, description or "")


_BODY_KEYS = ("event_id", "event_ty", "event_dtts", "project_cd", "table_nm", "src_id", "run_ty", "btch_id",
              "req_id", "load_id", "ovrd_id", "intake_id", "file_ref", "actor", "event_txt")


class NotificationDispatcher:
    """Emails unsent exceptions-audit events (ids, codes and counts only)."""

    def __init__(self, conn: Connection, settings: Settings, channel: Channel):
        self.conn, self.settings, self.channel = conn, settings, channel
        self.failed: list[int] = []

    def run(self, limit: int = 500, project_cd: Optional[str] = None) -> int:
        """Send up to `limit` events - of one project, or of every project (and none) when project_cd is None."""
        sent, self.failed = 0, []
        configs: dict[tuple, Optional[cfgmod.FileConfig]] = {}
        where, params = "Notified_Ind = 0", []
        if project_cd is not None:
            where, params = where + " AND Project_Cd = %s", [code(project_cd)]
        pending = self.conn.execute(f"SELECT TOP {int(limit)} Event_ID FROM CMS_ComplianceExceptionsAudit "
                                    f"WHERE {where} ORDER BY Event_Dtts, Event_ID", params).fetchall()
        for p in pending:
            key = db.row_key("EVT", p["event_id"])
            if not db.try_lock(self.conn, key):
                continue
            try:
                r = self.conn.execute("SELECT * FROM CMS_ComplianceExceptionsAudit WHERE Event_ID=%s "
                                      "AND Notified_Ind = 0", (p["event_id"],)).fetchone()
                if r is None:
                    continue
                if self._send(r, configs):
                    self.conn.execute("UPDATE CMS_ComplianceExceptionsAudit SET Notified_Ind=1 WHERE Event_ID=%s",
                                      (r["event_id"],))
                    sent += 1
                else:
                    self.failed.append(r["event_id"])
            finally:
                db.unlock(self.conn, key)
        return sent

    def _send(self, r: dict, configs: dict) -> bool:
        key = (r["project_cd"], r["table_nm"], r["src_id"])
        if key not in configs:
            configs[key] = cfgmod.file_config(self.conn, *key, settings=self.settings) if all(key) else None
        cfg = configs[key]
        if cfg:
            failure = AUDIT_EVENTS.get(r["event_ty"], _EXC_ERROR).category == "EXCEPTION"
            raw = cfg.failr_email_notfn_id if failure else cfg.sucs_email_notfn_id
            recipients = [x.strip() for x in (raw or "").split(",") if x.strip()]
        else:
            recipients = list(self.settings.default_notify_emails)
        prefix = f"{cfg.email_subjct_txt} - " if cfg and cfg.email_subjct_txt else ""
        msg = Message(f"{prefix}[{r['sevrty']}] {r['event_ty']}",
                      "\n".join(f"{k}: {r[k]}" for k in _BODY_KEYS if r.get(k) is not None), recipients)
        try:
            self.channel.send(msg)
        except Exception:  # noqa: BLE001
            log.exception("notification for event %s failed", r["event_id"])
            return False
        return True
