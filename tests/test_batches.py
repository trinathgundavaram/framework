from datetime import date

import pytest

from framework.batches import compute_period
from framework.common import ConfigError

from .helpers import create_batches, make_app, q1, qa, seed_config, utc


@pytest.mark.parametrize("name, sched, days, weeks, start, end", [
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
def test_periods(conn, name, sched, days, weeks, start, end):
    assert compute_period(conn, name, sched, days, weeks) == (start, end)


def test_period_missing_param_and_project_file(conn, tmp_path):
    with pytest.raises(ConfigError, match="lookback-days"):
        compute_period(conn, "PREV_N_DAYS", date(2026, 1, 1))
    f = tmp_path / "prja_periods.py"
    f.write_text('PERIOD_SQL = {"FISCAL_H1": "SELECT make_date(extract(year FROM %(sched_dt)s::date)::int, 1, 1) '
                 'AS rpt_start, make_date(extract(year FROM %(sched_dt)s::date)::int, 6, 30) AS rpt_end"}')
    assert compute_period(conn, "FISCAL_H1", date(2026, 7, 2), period_file=str(f)) == (date(2026, 1, 1), date(2026, 6, 30))


def test_create_batches_one_batch_per_period(conn, tmp_path):
    seed_config(conn)
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    s = create_batches(app)
    assert s.created == 2 and not s.errors and (s.rpt_start, s.rpt_end) == (date(2026, 1, 1), date(2026, 1, 31))
    clock.set(utc(2026, 2, 2, 13, 0))                  # a second run in the same month: same period (D-30)
    s = create_batches(app)
    assert s.created == 0 and s.existing == 2
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl ORDER BY Src_Cd")
    assert [r["btch_id"] for r in rows] == ["20260201_PRJA_tbl_x_S1_MONTHLY_V1_1", "20260201_PRJA_tbl_x_S2_MONTHLY_V1_1"]
    assert rows[0]["rpt_start_dt_key"] == date(2026, 1, 1) and rows[0]["rpt_end_dt_key"] == date(2026, 1, 31)
    assert rows[0]["earliest_close_dt"] == date(2026, 2, 2) and rows[0]["req_stat"] == "PENDING"
    assert rows[0]["created_by"] == "SCHEDULER"
    e = q1(conn, "SELECT * FROM ComplianceExtractControl")
    assert e["required_src_cnt"] == 2 and e["earliest_trigger_dt"] == date(2026, 2, 2)
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestFileDetail WHERE Event_Ty='BATCH_CREATED'")["n"] == 2


def test_missed_run_recreated_with_as_of(conn, tmp_path):
    """Catch-up = run the job for the missed date: the period follows that date, Btch_ID the actual day."""
    seed_config(conn, sources=("S1",))
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 3, 5, 12, 0))
    assert create_batches(app).created == 1                        # Feb period
    clock.set(utc(2026, 2, 1, 12, 0))
    assert create_batches(app).created == 1                        # Jan period
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl ORDER BY Rpt_Start_Dt_Key")
    assert [(r["rpt_start_dt_key"], r["req_dt_key"]) for r in rows] == [
        (date(2026, 1, 1), date(2026, 2, 1)), (date(2026, 2, 1), date(2026, 3, 5))]


def test_effective_window_scope_and_errors(conn, tmp_path):
    seed_config(conn)
    with conn.transaction():
        conn.execute("UPDATE ComplianceDataSetSourceXwalk SET Effective_Start_Dt='2026-03-01' "
                     "WHERE Run_Ty='MONTHLY' AND Src_Cd='S2'")
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 12, 0))
    s = create_batches(app)
    assert s.created == 1 and q1(conn, "SELECT Required_Src_Cnt n FROM ComplianceExtractControl")["n"] == 1
    assert create_batches(app, table_nm="other").errors                 # nothing effective in that scope
    with pytest.raises(ConfigError, match="not ROUTINE"):
        app.create_batches(project_cd="PRJA", run_ty="ADHOC", period="SAME_DAY")


def test_no_batch_added_to_triggered_extract(conn, tmp_path):
    seed_config(conn, sources=("S1",))
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    with conn.transaction():
        conn.execute("UPDATE ComplianceExtractControl SET Trigger_Stat='TRIGGERED'")
        conn.execute("""INSERT INTO ComplianceSourceSystem (Src_Cd, Src_Nm, Src_Ty) VALUES ('S3','s3','VENDOR')""")
        conn.execute("""INSERT INTO ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Effective_Start_Dt,
                        Cmplnc_Vrsn) VALUES ('PRJA','tbl_x','S3','MONTHLY','2025-01-01','V1')""")
    s = create_batches(app)
    assert s.created == 0 and s.skipped == 1
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='BATCH_CREATE_SKIPPED_EXTRACT_TRIGGERED'")["n"] == 1


# ---------------------------------------------------------------- intake (P4)
def intake(conn, iid, req_ty, run_ty, src=None, start="2026-03-01", end="2026-03-31"):
    with conn.transaction():
        conn.execute("""INSERT INTO ComplianceRequestInTake (Intake_ID, Project_Cd, Table_Nm, Run_Ty, Src_Cd, Req_Ty,
                        Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Requested_By, Requested_Dtts)
                        VALUES (%s,'PRJA','tbl_x',%s,%s,%s,%s,%s,'analyst', now())""",
                     (iid, run_ty, src, req_ty, start, end))


def stat(conn, iid):
    return q1(conn, "SELECT Intake_Stat, Error_Txt FROM ComplianceRequestInTake WHERE Intake_ID=%s", iid)


@pytest.fixture
def app(conn, tmp_path):
    seed_config(conn)
    return make_app(conn, tmp_path, utc(2026, 4, 2, 15, 0))[0]


def test_adhoc_fan_out_and_duplicate(app, conn):
    intake(conn, "A1", "ADHOC_REQUEST", "ADHOC")
    app.intake.run()
    assert stat(conn, "A1")["intake_stat"] == "PROCESSED"
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl WHERE Run_Ty='ADHOC' ORDER BY Src_Cd")
    assert [r["created_by"] for r in rows] == ["ADHOC_INTAKE"] * 2 and rows[0]["intake_id"] == "A1"
    assert rows[0]["earliest_close_dt"] == rows[0]["req_dt_key"]                   # ADHOC SLA 1 = same day
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Run_Ty='ADHOC'")
    assert e["required_src_cnt"] == 2
    intake(conn, "A2", "ADHOC_REQUEST", "ADHOC", src="S1")
    app.intake.run()
    assert stat(conn, "A2")["intake_stat"] == "FAILED"                              # D-35
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='INTAKE_DUPLICATE_PERIOD'")["n"] == 1


def test_adhoc_adds_source_before_trigger_but_not_after(app, conn):
    intake(conn, "A1", "ADHOC_REQUEST", "ADHOC", src="S1")
    app.intake.run()
    intake(conn, "A2", "ADHOC_REQUEST", "ADHOC", src="S2")
    app.intake.run()
    assert stat(conn, "A2")["intake_stat"] == "PROCESSED"
    assert q1(conn, "SELECT Required_Src_Cnt n FROM ComplianceExtractControl WHERE Run_Ty='ADHOC'")["n"] == 2
    with conn.transaction():
        conn.execute("UPDATE ComplianceExtractControl SET Trigger_Stat='TRIGGERED' WHERE Run_Ty='ADHOC'")
        conn.execute("DELETE FROM ComplianceRequestFileDetail WHERE Req_ID IN (SELECT Req_ID FROM ComplianceRequestControl WHERE Src_Cd='S2' AND Run_Ty='ADHOC')")
        conn.execute("DELETE FROM ComplianceRequestControl WHERE Src_Cd='S2' AND Run_Ty='ADHOC'")
    intake(conn, "A3", "ADHOC_REQUEST", "ADHOC", src="S2")
    app.intake.run()
    assert stat(conn, "A3")["intake_stat"] == "FAILED" and "triggered" in stat(conn, "A3")["error_txt"]


def test_cycle_init_and_category_checks(app, conn):
    intake(conn, "C1", "CYCLE_INIT", "MONTHLY")
    intake(conn, "C2", "CYCLE_INIT", "ADHOC")
    intake(conn, "C3", "ADHOC_REQUEST", "MONTHLY")
    app.intake.run()
    assert stat(conn, "C1")["intake_stat"] == "PROCESSED"
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestControl WHERE Created_By='CYCLE_INIT'")["n"] == 2
    for i in ("C2", "C3"):
        assert stat(conn, i)["intake_stat"] == "FAILED"
    intake(conn, "C5", "CYCLE_INIT", "MONTHLY")
    app.intake.run()
    assert stat(conn, "C5")["intake_stat"] == "PROCESSED"          # Q-18 default SKIP
    app.settings.cycle_init_existing_batch = "FAIL"
    intake(conn, "C6", "CYCLE_INIT", "MONTHLY")
    app.intake.run()
    assert stat(conn, "C6")["intake_stat"] == "FAILED"


def test_no_effective_source_and_correction_without_batch(app, conn):
    intake(conn, "N1", "ADHOC_REQUEST", "ADHOC", start="2020-01-01", end="2020-01-31")   # before Effective_Start_Dt
    intake(conn, "N2", "CORRECTION_REQUEST", "MONTHLY", src="S1")
    app.intake.run()
    assert stat(conn, "N1")["intake_stat"] == "FAILED"
    assert stat(conn, "N2")["intake_stat"] == "FAILED"
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='INTAKE_FAILED'")["n"] == 2
