"""Report-period computation from the SQL files in framework/sql/period_strategies (design §5.1)."""
from __future__ import annotations

from datetime import date
from importlib import resources
from typing import Optional

import psycopg

from ..config.models import PeriodStrategy, XwalkRow
from ..config import repository as repo
from ..errors import ConfigError


def _sql_text(file_name: str) -> str:
    if "/" in file_name or "\\" in file_name or not file_name.endswith(".sql"):
        raise ConfigError(f"invalid period strategy file name {file_name!r}")
    f = resources.files("framework").joinpath("sql", "period_strategies", file_name)
    if not f.is_file():
        raise ConfigError(f"period strategy file {file_name} is not shipped with the package")
    text = f.read_text(encoding="utf-8").strip().rstrip(";")
    if ";" in text:
        raise ConfigError(f"period strategy {file_name} must be a single statement")
    return text


def strategy_file_exists(file_name: str) -> bool:
    try:
        _sql_text(file_name)
        return True
    except ConfigError:
        return False


def compute(conn: psycopg.Connection, strategy: PeriodStrategy, sched_dt: date,
            lookback_days: Optional[int], lookback_weeks: Optional[int]) -> tuple[date, date]:
    if strategy.requires_lookback_days and lookback_days is None:
        raise ConfigError(f"{strategy.code} requires Lookback_Days")
    if strategy.requires_lookback_weeks and lookback_weeks is None:
        raise ConfigError(f"{strategy.code} requires Lookback_Weeks")
    row = conn.execute(_sql_text(strategy.sql_file_nm), {
        "sched_dt": sched_dt, "lookback_days": lookback_days, "lookback_weeks": lookback_weeks}).fetchone()
    start, end = row["rpt_start"], row["rpt_end"]
    if start is None or end is None or end < start:
        raise ConfigError(f"{strategy.code} returned an invalid period {start}..{end} for {sched_dt}")
    return start, end


def period_for(conn: psycopg.Connection, x: XwalkRow, sched_dt: date) -> tuple[date, date]:
    if not x.period_strategy_cd:
        raise ConfigError(f"crosswalk row {x.project_cd}/{x.table_nm}/{x.src_cd}/{x.run_ty} has no period strategy")
    st = repo.period_strategy(conn, x.period_strategy_cd)
    if st is None:
        raise ConfigError(f"unknown period strategy {x.period_strategy_cd}")
    return compute(conn, st, sched_dt, x.lookback_days, x.lookback_weeks)
