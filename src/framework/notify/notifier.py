"""Notification dispatch (design §7 P13, D-54). Sends unsent, notifiable ExceptionsAudit events.

Messages contain ids, codes and counts only (no file content)."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import psycopg

from ..clock import Clock
from ..config import repository as repo
from ..settings import Settings

log = logging.getLogger(__name__)


@dataclass
class Message:
    subject: str
    body: str
    recipients: list[str]
    sns_topic_arn: Optional[str]


class Channel(ABC):
    @abstractmethod
    def send(self, channel_cd: str, msg: Message) -> None: ...


class LogChannel(Channel):
    def __init__(self):
        self.sent: list[tuple[str, Message]] = []

    def send(self, channel_cd, msg):
        self.sent.append((channel_cd, msg))
        log.warning("NOTIFY[%s] to=%s topic=%s | %s | %s", channel_cd, msg.recipients, msg.sns_topic_arn,
                    msg.subject, msg.body.replace("\n", " / "))


class AwsChannel(Channel):
    def __init__(self, settings: Settings):
        import boto3

        self.settings = settings
        self.ses = boto3.client("ses", region_name=settings.aws_region)
        self.sns = boto3.client("sns", region_name=settings.aws_region)

    def send(self, channel_cd, msg):
        if channel_cd in ("SES", "BOTH") and msg.recipients:
            if not self.settings.notify_from_email:
                raise RuntimeError("FRAMEWORK_NOTIFY_FROM_EMAIL is required for SES")
            self.ses.send_email(Source=self.settings.notify_from_email,
                                Destination={"ToAddresses": msg.recipients},
                                Message={"Subject": {"Data": msg.subject[:200]}, "Body": {"Text": {"Data": msg.body}}})
        if channel_cd in ("SNS", "BOTH") and msg.sns_topic_arn:
            self.sns.publish(TopicArn=msg.sns_topic_arn, Subject=msg.subject[:100], Message=msg.body)


def build_channel(settings: Settings) -> Channel:
    return AwsChannel(settings) if settings.notify_backend == "aws" else LogChannel()


class NotificationDispatcher:
    def __init__(self, conn: psycopg.Connection, clock: Clock, settings: Settings, channel: Channel):
        self.conn, self.clock, self.settings, self.channel = conn, clock, settings, channel

    def run(self, limit: int = 500) -> int:
        rows = self.conn.execute(
            """SELECT a.*, t.Notify_Channel_Cd AS default_channel FROM CMS_ComplianceExceptionsAudit a
                 JOIN ComplianceEventType t ON t.Event_Ty = a.Event_Ty
                WHERE a.Notified_Ind = 0 AND t.Notify_Ind = 1 ORDER BY a.Event_ID LIMIT %s""", (limit,)).fetchall()
        sent = 0
        for r in rows:
            cfg = (repo.file_config(self.conn, r["project_cd"], r["table_nm"], r["src_cd"])
                   if r["project_cd"] and r["table_nm"] and r["src_cd"] else None)
            channel_cd = (cfg.notify_channel_cd if cfg and cfg.notify_channel_cd else r["default_channel"])
            if cfg:
                raw = cfg.failr_email_notfn_id if r["event_ctgy"] == "EXCEPTION" else cfg.sucs_email_notfn_id
                recipients = [x.strip() for x in (raw or "").split(",") if x.strip()]
            else:
                recipients = list(self.settings.default_notify_emails)
            prefix = (cfg.email_subjct_txt + " - ") if cfg and cfg.email_subjct_txt else ""
            subject = f"{prefix}[{r['sevrty']}] {r['event_ty']}"
            body = "\n".join(f"{k}: {r[k]}" for k in (
                "event_id", "event_ty", "event_dtts", "project_cd", "table_nm", "src_cd", "run_ty", "btch_id",
                "req_id", "load_id", "extract_id", "ovrd_id", "trigger_id", "intake_id", "file_ref", "actor",
                "description") if r.get(k) is not None)
            try:
                self.channel.send(channel_cd, Message(subject, body, recipients, cfg.sns_topic_arn if cfg else None))
            except Exception:  # noqa: BLE001 - never roll back pipeline state for a notification
                log.exception("notification for event %s failed", r["event_id"])
                continue
            with self.conn.transaction():
                self.conn.execute("UPDATE CMS_ComplianceExceptionsAudit SET Notified_Ind=1 WHERE Event_ID=%s",
                                  (r["event_id"],))
            sent += 1
        return sent
