"""Service wiring (design §15.3 for the operational health report)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Optional

import psycopg

from .adapters import ObjectStore, RuleEngine, build_channel, build_object_store, build_rule_engine
from .audit import NotificationDispatcher
from .batches import IntakeProcessor, ScheduleSummary, create_batches
from .closing import BatchCloser
from .common import Clock, ConfigError
from .db import schema_exists
from .filecheck import FileChecker
from .ingest import IngestPipeline
from .modules import ModuleOutcome, run_module
from .overrides import DecisionProcessor
from .rules import RulesRunner
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
                raise ConfigError(f"the framework tables were not found in {settings.metadata_schema!r}; "
                                  "they are created separately from sql/schema.sql")
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
    def closer(self) -> BatchCloser:
        return BatchCloser(self.conn, self.clock, self.settings)

    @cached_property
    def pipeline(self) -> IngestPipeline:
        return IngestPipeline(self.conn, self.clock, self.settings, self.store, spark=self.spark)

    @cached_property
    def file_checker(self) -> FileChecker:
        return FileChecker(self.conn, self.clock, self.settings, self.store, self.pipeline)

    @cached_property
    def rules_runner(self) -> RulesRunner:
        return RulesRunner(self.conn, self.clock, self.settings, self.rules)

    @cached_property
    def decisions(self) -> DecisionProcessor:
        return DecisionProcessor(self.conn, self.clock, self.settings)

    @cached_property
    def intake(self) -> IntakeProcessor:
        return IntakeProcessor(self.conn, self.clock, self.settings)

    def run_module(self, name: str, params: Optional[dict] = None) -> ModuleOutcome:
        """Run a module by name (see modules.py)."""
        return run_module(self, name, params)

    def create_batches(self, **kw) -> ScheduleSummary:
        return create_batches(self.conn, self.clock, self.settings, **kw)

    def notifier(self, channel=None) -> NotificationDispatcher:
        return NotificationDispatcher(self.conn, self.settings, channel or build_channel(self.settings))

    def health(self) -> dict[str, list[dict]]:
        """Operational report (design §15.3), composed from the service that owns each set of tables."""
        return {**self.pipeline.health(), **self.decisions.health(), **self.closer.health()}
