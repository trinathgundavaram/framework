"""Service wiring. Entry points (CLI today, Glue/Step Functions later - D-13) build an App and call one service."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Optional

import psycopg

from .batches.intake_processor import IntakeProcessor
from .clock import Clock
from .db import connect, resolve_dsn
from .extract.connectors import build_connector
from .extract.control import ExtractControlService
from .extract.evaluator import ExtractEvaluator
from .extract.trigger import ExtractTriggerService
from .ingest.pipeline import IngestPipeline
from .load.engine import build_engine
from .notify.notifier import NotificationDispatcher, build_channel
from .overrides.decision_processor import DecisionProcessor
from .settings import Settings
from .storage.object_store import ObjectStore, build_object_store
from .validation.gre_adapter import RuleEngine, build_rule_engine


@dataclass
class App:
    conn: psycopg.Connection
    clock: Clock
    settings: Settings
    store: ObjectStore
    rules: RuleEngine
    engine_factory: object = build_engine
    connector_factory: object = build_connector
    sleep: object = None

    @classmethod
    def from_settings(cls, settings: Settings, clock: Optional[Clock] = None) -> "App":
        return cls(connect(resolve_dsn(settings)), clock or Clock(), settings, build_object_store(settings),
                   build_rule_engine(settings))

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
        return IngestPipeline(self.conn, self.clock, self.settings, self.store, self.rules, self.engine_factory,
                              on_promoted=self._early_refresh)

    @cached_property
    def decisions(self) -> DecisionProcessor:
        return DecisionProcessor(self.conn, self.clock, self.settings, self.store,
                                 after_reopen=self.evaluator.after_reopen_promotion,
                                 refresh_extract=lambda ext: self.control.refresh(ext, "WAIVER_DECISION"))

    @cached_property
    def intake(self) -> IntakeProcessor:
        return IntakeProcessor(self.conn, self.clock, self.settings)

    def notifier(self, channel=None) -> NotificationDispatcher:
        return NotificationDispatcher(self.conn, self.clock, self.settings, channel or build_channel(self.settings))

    def _early_refresh(self, extract_id: Optional[int]) -> None:
        if extract_id:
            self.control.refresh(extract_id, "EARLY_COMPLETE")
