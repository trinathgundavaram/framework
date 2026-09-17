"""Service wiring. Entry points (CLI today, Glue/Step Functions later - D-13) build an App and call one service.

Start-up order:
  1. Settings from environment + config file (bootstrap values: CONFIG_FILE, METADATA_SCHEMA, AWS_REGION)
  2. metadata connection (connections.py precedence: env > config file > Secrets Manager)
  3. remaining settings from ComplianceFrameworkSetting (env and config file still win)
  4. data connections are opened lazily per file config (ComplianceSourceFileConfig.Target_Connection_Nm)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from typing import Optional

import psycopg

from .batches.intake_processor import IntakeProcessor
from .clock import Clock
from .connections import ConnectionManager, ConnectionResolver
from .db import load_metadata_settings, schema_exists
from .errors import ConfigError
from .extract.connectors import build_connector
from .extract.control import ExtractControlService
from .extract.evaluator import ExtractEvaluator
from .extract.trigger import ExtractTriggerService
from .ingest.pipeline import IngestPipeline
from .load.engine import build_engine
from .notify.notifier import NotificationDispatcher, build_channel
from .overrides.decision_processor import DecisionProcessor
from .settings import Settings, read_config_file
from .storage.object_store import ObjectStore, build_object_store
from .validation.gre_adapter import RuleEngine, build_rule_engine

log = logging.getLogger(__name__)


def bootstrap(settings: Optional[Settings] = None, config=None, env=None,
              secret_loader=None) -> tuple[Settings, ConnectionManager, list[str]]:
    """Resolve settings and connections; returns (settings, connections, unknown metadata setting names)."""
    if settings is None:
        settings = Settings.load(env=env, config=config)
    if config is None:
        config = read_config_file(settings.config_file)
    conns = ConnectionManager(ConnectionResolver(settings, config, env, secret_loader))
    try:
        meta = conns.meta
        if not schema_exists(meta, settings.metadata_schema):
            raise ConfigError(f"metadata schema {settings.metadata_schema!r} is not initialised in "
                              f"{conns.spec(None).describe()} - run 'framework init-db'")
        try:
            unknown = settings.apply_metadata(load_metadata_settings(meta))
        except ValueError as e:
            raise ConfigError(f"invalid value in ComplianceFrameworkSetting: {e}") from e
    except BaseException:
        conns.close()
        raise
    for n in unknown:
        log.warning("ComplianceFrameworkSetting %s is not a known setting (ignored)", n)
    return settings, conns, unknown


@dataclass
class App:
    conns: ConnectionManager
    clock: Clock
    settings: Settings
    store: ObjectStore
    rules: RuleEngine
    engine_factory: object = build_engine
    connector_factory: object = build_connector
    sleep: object = None

    @property
    def conn(self) -> psycopg.Connection:
        """Metadata / control / audit database."""
        return self.conns.meta

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None, clock: Optional[Clock] = None, **kw) -> "App":
        settings, conns, _ = bootstrap(settings, **kw)
        return cls(conns, clock or Clock(), settings, build_object_store(settings), build_rule_engine(settings))

    def close(self) -> None:
        self.conns.close()

    def __enter__(self) -> "App":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @cached_property
    def control(self) -> ExtractControlService:
        return ExtractControlService(self.conns, self.clock, self.settings, self.rules)

    @cached_property
    def trigger(self) -> ExtractTriggerService:
        kw = {"sleep": self.sleep} if self.sleep else {}
        return ExtractTriggerService(self.conn, self.clock, self.settings, self.control, self.connector_factory, **kw)

    @cached_property
    def evaluator(self) -> ExtractEvaluator:
        return ExtractEvaluator(self.conn, self.clock, self.settings, self.control, self.trigger)

    @cached_property
    def pipeline(self) -> IngestPipeline:
        return IngestPipeline(self.conns, self.clock, self.settings, self.store, self.rules, self.engine_factory,
                              on_promoted=self._early_refresh)

    @cached_property
    def decisions(self) -> DecisionProcessor:
        return DecisionProcessor(self.conns, self.clock, self.settings, self.store,
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
