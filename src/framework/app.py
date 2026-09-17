"""Service wiring and the operational health report (design §15.3).

Entry points (CLI today; Glue / Step Functions call the same CLI with job arguments) build an App
from Settings and call one service. One database connection serves metadata, staging and core.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from typing import Optional

import psycopg

from .adapters import (ObjectStore, RuleEngine, build_channel, build_connector, build_object_store,
                       build_rule_engine)
from .audit import NotificationDispatcher
from .batches import IntakeProcessor, ScheduleSummary, create_batches
from .common import Clock, ConfigError
from .db import schema_exists
from .extract import ExtractControlService, ExtractEvaluator, ExtractTriggerService
from .ingest import IngestPipeline
from .overrides import DecisionProcessor
from .settings import Settings


@dataclass
class App:
    conn: psycopg.Connection
    clock: Clock
    settings: Settings
    store: ObjectStore
    rules: RuleEngine
    connector_factory: object = build_connector
    sleep: object = None
    spark: object = None

    @classmethod
    def from_settings(cls, settings: Settings, clock: Optional[Clock] = None, secret_loader=None) -> "App":
        conn = settings.connect(secret_loader)
        try:
            if not schema_exists(conn, settings.metadata_schema):
                raise ConfigError(f"metadata schema {settings.metadata_schema!r} is not initialised - "
                                  "run 'framework init-db'")
            return cls(conn, clock or Clock(), settings, build_object_store(settings), build_rule_engine(settings))
        except BaseException:
            conn.close()
            raise

    def close(self) -> None:
        if not self.conn.closed:
            self.conn.close()

    def __enter__(self) -> "App":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @cached_property
    def control(self) -> ExtractControlService:
        return ExtractControlService(self.conn, self.clock, self.settings, self.rules)

    @cached_property
    def trigger(self) -> ExtractTriggerService:
        kw = {"sleep": self.sleep} if self.sleep else {}
        return ExtractTriggerService(self.conn, self.clock, self.settings, self.control, self.connector_factory, **kw)

    @cached_property
    def evaluator(self) -> ExtractEvaluator:
        return ExtractEvaluator(self.conn, self.clock, self.settings, self.control, self.trigger)

    @cached_property
    def pipeline(self) -> IngestPipeline:
        return IngestPipeline(self.conn, self.clock, self.settings, self.store, self.rules,
                              on_promoted=lambda ext: ext and self.control.refresh(ext, "EARLY_COMPLETE"),
                              spark=self.spark)

    @cached_property
    def decisions(self) -> DecisionProcessor:
        return DecisionProcessor(self.conn, self.clock, self.settings, self.store,
                                 after_reopen=self.evaluator.after_reopen_promotion,
                                 refresh_extract=lambda ext: self.control.refresh(ext, "OVERRIDE_DECISION"))

    @cached_property
    def intake(self) -> IntakeProcessor:
        return IntakeProcessor(self.conn, self.clock, self.settings)

    def create_batches(self, **kw) -> ScheduleSummary:
        return create_batches(self.conn, self.clock, self.settings, **kw)

    def notifier(self, channel=None) -> NotificationDispatcher:
        return NotificationDispatcher(self.conn, self.settings, channel or build_channel(self.settings))

    def health(self) -> dict[str, list[dict]]:
        q = lambda text, *p: self.conn.execute(text, p).fetchall()  # noqa: E731
        now = self.clock.now()
        return {
            "stale_loads": q(
                """SELECT Load_ID, S3_Key, Load_Stat, Heartbeat_Dtts FROM ComplianceFileLoad
                    WHERE Load_Stat IN ('RECEIVED','STAGING','STAGED','RULES_RUNNING','FAILED_TECHNICAL')
                      AND COALESCE(Heartbeat_Dtts, Updated_Dtts) < %s ORDER BY Load_ID""",
                now - timedelta(minutes=self.settings.heartbeat_stale_minutes)),
            "pending_reviews": q("""SELECT Ovrd_ID, Override_Ty, Btch_ID, Candidate_Load_ID, Created_Dtts
                                     FROM ComplianceBatchOverride WHERE Apprvl_Stat='PENDING_REVIEW' ORDER BY Ovrd_ID"""),
            "failed_promotions": q("SELECT Ovrd_ID, Btch_ID FROM ComplianceBatchOverride WHERE Promotion_Stat='FAILED'"),
            "triggers_in_flight": q("""SELECT Trigger_ID, Extract_ID, Requested_Dtts FROM ComplianceExtractTrigger
                                        WHERE Call_Stat='REQUESTED'"""),
            "extracts_past_hold_not_triggered": q(
                """SELECT Extract_ID, Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Eligibility_Cd,
                          Eligibility_Rsn_Txt FROM ComplianceExtractControl
                    WHERE Trigger_Stat IN ('NOT_TRIGGERED','FAILED') AND Earliest_Trigger_Dt < %s
                    ORDER BY Extract_ID""", now.date()),
            "retrigger_required": q("""SELECT Extract_ID, Eligibility_Cd FROM ComplianceExtractControl
                                        WHERE Retrigger_Required_Ind=1"""),
            "quarantine_by_reason": q("""SELECT Quarantine_Rsn_Cd, count(*) AS n FROM ComplianceFileLoad
                                          WHERE Load_Stat='QUARANTINED' GROUP BY Quarantine_Rsn_Cd ORDER BY n DESC"""),
        }
