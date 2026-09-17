"""Rules-engine adapter (D-08, D-43, D-44, D-63).

GRE call mechanics are open question Q-12. The adapter therefore delegates the actual rule
execution to a configurable entry point (FRAMEWORK_GRE_ENTRYPOINT="package.module:function")
with this contract:

    def run_rules(conn, rule_group: str, rule_variant: str, run_params: dict) -> list[dict]
        # returns one dict per executed rule: {"rule_ref": str, "passed": bool, "detail": str | None}
        # raise any exception for a technical failure

The framework applies GATE/ANNOTATE itself: FILE_LEVEL mode comes from the file config
(Rules_Vld_Md), PERIOD_LEVEL mode from the extract policy (Period_Rules_Vld_Md).
"""
from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import psycopg

from ..config.models import RuleBinding
from ..errors import ConfigError, RuleEngineNotConfigured
from ..settings import Settings

log = logging.getLogger(__name__)

PASSED = "PASSED"
PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"
FAILED = "FAILED"
ERROR = "ERROR"


@dataclass
class RuleOutcome:
    status: str
    failed_rules: list[str] = field(default_factory=list)      # GATE failures
    warned_rules: list[str] = field(default_factory=list)      # ANNOTATE failures
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.status in (PASSED, PASSED_WITH_WARNINGS)


class RuleEngine(ABC):
    @abstractmethod
    def run(self, conn: psycopg.Connection, bindings: Sequence[RuleBinding], run_params: dict,
            mode: str) -> RuleOutcome: ...


def _summarise(results: list[dict], mode: str) -> RuleOutcome:
    failed = [r["rule_ref"] for r in results if not r.get("passed")]
    if not failed:
        return RuleOutcome(PASSED)
    if mode == "GATE":
        return RuleOutcome(FAILED, failed_rules=failed)
    return RuleOutcome(PASSED_WITH_WARNINGS, warned_rules=failed)


class CallableRuleEngine(RuleEngine):
    """Runs each bound GRE group/variant through `fn` and applies the validation mode."""

    def __init__(self, fn: Callable[..., list[dict]]):
        self.fn = fn

    def run(self, conn, bindings, run_params, mode):
        if not bindings:
            return RuleOutcome(PASSED)
        results: list[dict] = []
        try:
            for b in bindings:
                out = self.fn(conn, b.gre_rule_group, b.gre_rule_variant, dict(run_params))
                for r in out:
                    if "rule_ref" not in r or "passed" not in r:
                        raise ValueError(f"rule engine returned an invalid result {r!r}")
                    results.append(r)
        except Exception as e:  # technical failure -> ERROR, never a business failure (E-23)
            log.exception("rule engine technical failure")
            return RuleOutcome(ERROR, error=str(e))
        return _summarise(results, mode)


class GreRuleEngine(CallableRuleEngine):
    def __init__(self, entrypoint: Optional[str]):
        if not entrypoint:
            def _missing(*_a, **_k):
                raise RuleEngineNotConfigured(
                    "FRAMEWORK_GRE_ENTRYPOINT is not set (open question Q-12: GRE call mechanics)")
            super().__init__(_missing)
            return
        module, _, attr = entrypoint.partition(":")
        if not module or not attr:
            raise ConfigError(f"FRAMEWORK_GRE_ENTRYPOINT must be 'module:function', got {entrypoint!r}")
        super().__init__(getattr(importlib.import_module(module), attr))


class NoRulesEngine(RuleEngine):
    """Passes everything. For environments where rules are intentionally disabled."""

    def run(self, conn, bindings, run_params, mode):
        return RuleOutcome(PASSED)


def build_rule_engine(settings: Settings) -> RuleEngine:
    name = settings.rule_engine
    if name == "gre":
        return GreRuleEngine(settings.gre_entrypoint)
    if name == "none":
        return NoRulesEngine()
    module, _, attr = name.partition(":")
    return getattr(importlib.import_module(module), attr)()
