"""Extract job/API connectors (D-39, D-42). The framework only needs to know whether the call
was accepted; it never tracks the extract job's own outcome."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ...config.models import ExtractPolicy


@dataclass
class CallResult:
    accepted: bool
    job_run_ref: Optional[str] = None
    response_txt: Optional[str] = None
    ambiguous: bool = False      # request may have been received; do not blindly retry (§11.3 step 6)


class ExtractConnector(ABC):
    @abstractmethod
    def call(self, policy: ExtractPolicy, params: list[tuple[str, str]]) -> CallResult: ...
