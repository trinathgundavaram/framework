"""Execution engine interface (D-62): loads one file into staging for one (Btch_ID, Load_ID)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psycopg

from ...config.models import FileConfig
from ...settings import Settings


@dataclass
class StageResult:
    data_rows: int
    trailer_count: Optional[int]


class ExecutionEngine(ABC):
    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def load_to_staging(self, conn: psycopg.Connection, *, file_path: str, cfg: FileConfig,
                        stg_columns: list[str], btch_id: str, load_id: int, src_file_nm: str,
                        loaded_at: datetime) -> StageResult:
        """Delete staging rows for btch_id (D-05) and load the file tagged with load_id."""
