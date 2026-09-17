"""Service wiring (design §15.3 for the operational health report).

Entry points (CLI today; Glue / Step Functions call the same CLI with job arguments) build an App
from Settings and call one service. One database connection serves metadata, staging and core.

App itself carries no table-specific SQL: `health()` composes the report from each service that owns
the tables it queries (`ingest.IngestPipeline`, `overrides.DecisionProcessor`,
`extract.ExtractControlService`), the same ownership split as design §15.1's "who writes which table" -
generic wiring here, business/table-specific logic in the domain modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Optional

import psycopg

from .adapters import ObjectStore, RuleEngine, build_channel, build_object_store, build_rule_engine
from .audit import NotificationDispatcher
from .batches import IntakeProcessor, ScheduleSummary, create_batches
from .common import Clock, ConfigError
from .db import schema_exists
from .extract import ExtractControlService, ExtractEvaluator
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
    def evaluator(self) -> ExtractEvaluator:
        return ExtractEvaluator(self.conn, self.clock, self.settings, self.control)

    @cached_property
    def pipeline(self) -> IngestPipeline:
        return IngestPipeline(self.conn, self.clock, self.settings, self.store, self.rules,
                              on_promoted=lambda ext: ext and self.control.refresh(ext, "EARLY_COMPLETE"),
                              on_late_promotion=lambda ext: ext and self.evaluator.after_late_promotion(ext),
                              spark=self.spark)

    @cached_property
    def decisions(self) -> DecisionProcessor:
        return DecisionProcessor(self.conn, self.clock, self.settings,
                                 refresh_extract=lambda ext: self.control.refresh(ext, "OVERRIDE_DECISION"))

    @cached_property
    def intake(self) -> IntakeProcessor:
        return IntakeProcessor(self.conn, self.clock, self.settings)

    def create_batches(self, **kw) -> ScheduleSummary:
        return create_batches(self.conn, self.clock, self.settings, **kw)

    def notifier(self, channel=None) -> NotificationDispatcher:
        return NotificationDispatcher(self.conn, self.settings, channel or build_channel(self.settings))

    def health(self) -> dict[str, list[dict]]:
        """Operational report (design §15.3), composed from the service that owns each set of tables:
        stale loads and quarantine counts from the ingest pipeline, pending/expiring overrides from the
        decision processor, and open-past-hold/regenerate-required runs from extract control."""
        report: dict[str, list[dict]] = {}
        report.update(self.pipeline.health())
        report.update(self.decisions.health())
        report.update(self.control.health())
        return report
