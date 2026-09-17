"""Scheduled and catch-up batch creation (design §7 P2, P3)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..audit.event_logger import EventLogger
from ..clock import Clock
from ..common.status import StatusModel
from ..config import repository as repo
from ..errors import ConfigError
from ..settings import Settings
from . import cron
from .crc_repository import create_batch
from .period_strategies import period_for

log = logging.getLogger(__name__)


@dataclass
class ScheduleSummary:
    created: int = 0
    existing: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _run(conn: psycopg.Connection, clock: Clock, settings: Settings, window: timedelta, created_by: str) -> ScheduleSummary:
    summary = ScheduleSummary()
    status = StatusModel.load(conn)
    logger = EventLogger(conn, clock)
    run_types = repo.run_types(conn)
    now = clock.now()
    for x in repo.routine_xwalk_rows(conn):
        if not x.schedule_cron_expr:
            summary.errors.append(f"{x.project_cd}/{x.table_nm}/{x.src_cd}/{x.run_ty}: no cron")
            continue
        rt = run_types[x.run_ty]
        req_dt = now.astimezone(ZoneInfo(x.business_tz)).date()          # D-29: actual creation date
        try:
            fires = cron.fire_times(x.schedule_cron_expr, x.business_tz, now - window, now)
        except (ValueError, KeyError) as e:
            summary.errors.append(f"{x.project_cd}/{x.table_nm}/{x.src_cd}/{x.run_ty}: {e}")
            continue
        for ft in fires:
            sched_dt = ft.date()                                          # D-29b: period from scheduled date
            if not x.effective_on(sched_dt):
                continue
            if settings.go_live_date and sched_dt < settings.go_live_date:
                continue
            try:
                start, end = period_for(conn, x, sched_dt)
                required = len(repo.effective_sources(conn, x.project_cd, x.table_nm, x.run_ty, sched_dt))
                res = create_batch(conn, clock, status, logger, xwalk=x, run_type=rt, rpt_start=start,
                                   rpt_end=end, req_dt=req_dt, created_by=created_by, required_cnt=required)
            except ConfigError as e:
                summary.errors.append(str(e))
                log.error("batch creation failed: %s", e)
                continue
            if res.created:
                summary.created += 1
            elif res.skipped_reason == "EXISTS":
                summary.existing += 1
            else:
                summary.skipped += 1
    return summary


def run_scheduler(conn: psycopg.Connection, clock: Clock, settings: Settings) -> ScheduleSummary:
    return _run(conn, clock, settings, timedelta(hours=settings.scheduler_window_hours), "SCHEDULER")


def run_catchup(conn: psycopg.Connection, clock: Clock, settings: Settings) -> ScheduleSummary:
    """Fire times older than the scheduler window, back to the lookback horizon (Q-14)."""
    summary = _run(conn, clock, settings, timedelta(days=settings.catchup_lookback_days), "CATCHUP")
    return summary
