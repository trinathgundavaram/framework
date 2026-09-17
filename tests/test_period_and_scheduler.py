from datetime import date

import pytest

from framework.batches.period_strategies import compute
from framework.batches.scheduler import run_catchup, run_scheduler
from framework.config import repository as repo
from framework.config.models import PeriodStrategy

from .helpers import make_app, q1, qa, seed_config, utc


@pytest.mark.parametrize("code, sched, days, weeks, start, end", [
    ("SAME_DAY", date(2026, 3, 15), None, None, date(2026, 3, 15), date(2026, 3, 15)),
    ("PREV_DAY", date(2026, 3, 1), None, None, date(2026, 2, 28), date(2026, 2, 28)),
    ("PREV_N_DAYS", date(2026, 3, 8), 7, None, date(2026, 3, 1), date(2026, 3, 7)),
    ("PREV_WEEK_SAME_DAY", date(2026, 3, 11), None, 2, date(2026, 2, 25), date(2026, 3, 10)),
    ("PREV_CALENDAR_WEEK", date(2026, 3, 11), None, None, date(2026, 3, 2), date(2026, 3, 8)),
    ("CURRENT_CALENDAR_MONTH", date(2024, 2, 10), None, None, date(2024, 2, 1), date(2024, 2, 29)),
    ("PREV_CALENDAR_MONTH", date(2026, 3, 1), None, None, date(2026, 2, 1), date(2026, 2, 28)),
    ("ROLLING_1_MONTH", date(2026, 3, 31), None, None, date(2026, 2, 28), date(2026, 3, 30)),
    ("PREV_CALENDAR_QUARTER", date(2026, 5, 20), None, None, date(2026, 1, 1), date(2026, 3, 31)),
    ("PREV_CALENDAR_YEAR", date(2026, 1, 1), None, None, date(2025, 1, 1), date(2025, 12, 31)),
])
def test_strategies(conn, code, sched, days, weeks, start, end):
    st = repo.period_strategy(conn, code)
    assert compute(conn, st, sched, days, weeks) == (start, end)


def test_strategy_missing_param(conn):
    from framework.errors import ConfigError
    with pytest.raises(ConfigError):
        compute(conn, repo.period_strategy(conn, "PREV_N_DAYS"), date(2026, 1, 1), None, None)
    with pytest.raises(ConfigError):
        compute(conn, PeriodStrategy("X", "../../etc/passwd", False, False), date(2026, 1, 1), None, None)


def test_scheduler_creates_one_batch_per_period(conn, tmp_path):
    seed_config(conn, cron="0 6 * * *")               # daily cron, monthly period (D-30)
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    s = run_scheduler(conn, clock, app.settings)
    assert s.created == 2 and not s.errors
    clock.set(utc(2026, 2, 2, 13, 0))
    s = run_scheduler(conn, clock, app.settings)
    assert s.created == 0 and s.existing == 2
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl ORDER BY Src_Cd")
    assert [r["btch_id"] for r in rows] == ["20260201_PRJA_tbl_x_S1_MONTHLY_V1_1", "20260201_PRJA_tbl_x_S2_MONTHLY_V1_1"]
    assert rows[0]["rpt_start_dt_key"] == date(2026, 1, 1) and rows[0]["rpt_end_dt_key"] == date(2026, 1, 31)
    assert rows[0]["earliest_close_dt"] == date(2026, 2, 2) and rows[0]["req_stat"] == "PENDING"
    e = q1(conn, "SELECT * FROM ComplianceExtractControl")
    assert e["required_src_cnt"] == 2 and e["earliest_trigger_dt"] == date(2026, 2, 2)
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestFileDetail WHERE Event_Ty='BATCH_CREATED'")["n"] == 2


def test_catchup_uses_scheduled_period_and_actual_date(conn, tmp_path):
    seed_config(conn, cron="0 6 1 * *")
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 3, 5, 12, 0), catchup_lookback_days=40)
    s = run_catchup(conn, clock, app.settings)
    assert s.created == 4          # Feb 1 and Mar 1 fires x 2 sources
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S1' ORDER BY Rpt_Start_Dt_Key")
    assert [(r["rpt_start_dt_key"], r["req_dt_key"], r["created_by"]) for r in rows] == [
        (date(2026, 1, 1), date(2026, 3, 5), "CATCHUP"), (date(2026, 2, 1), date(2026, 3, 5), "CATCHUP")]
    assert [r["btch_id"][-1] for r in rows] == ["1", "2"]          # Seq keeps Btch_ID unique on one day


def test_effective_window_and_go_live(conn, tmp_path):
    seed_config(conn, sources=("S1",))
    with conn.transaction():
        conn.execute("UPDATE ComplianceDataSetSourceXwalk SET Effective_Start_Dt='2026-03-01' WHERE Run_Ty='MONTHLY'")
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 3, 5, 12, 0), catchup_lookback_days=40)
    assert run_catchup(conn, clock, app.settings).created == 1        # Feb 1 fire is before the effective start
    app.settings.go_live_date = date(2026, 4, 1)
    clock.set(utc(2026, 4, 2, 12, 0))
    assert run_catchup(conn, clock, app.settings).created == 1        # Apr 1 only


def test_no_batch_added_to_triggered_extract(conn, tmp_path):
    seed_config(conn, sources=("S1",))
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    run_scheduler(conn, clock, app.settings)
    with conn.transaction():
        conn.execute("UPDATE ComplianceExtractControl SET Trigger_Stat='TRIGGERED'")
        conn.execute("""INSERT INTO ComplianceSourceSystem (Src_Cd, Src_Nm, Src_Ty) VALUES ('S3','s3','VENDOR')""")
        conn.execute("""INSERT INTO ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Effective_Start_Dt,
                        Cmplnc_Vrsn, Period_Strategy_Cd, Schedule_Cron_Expr, Business_Tz)
                        VALUES ('PRJA','tbl_x','S3','MONTHLY','2025-01-01','V1','PREV_CALENDAR_MONTH','0 6 1 * *','America/Chicago')""")
    s = run_scheduler(conn, clock, app.settings)
    assert s.created == 0 and s.skipped == 1
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='BATCH_CREATE_SKIPPED_EXTRACT_TRIGGERED'")["n"] == 1
