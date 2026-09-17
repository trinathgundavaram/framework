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


def test_create_batches_one_batch_per_run_date(conn, tmp_path):
    seed_config(conn)
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    s = create_batches(app)
    assert s.created == 2 and not s.errors
    assert (s.run_date, s.rpt_start, s.rpt_end) == (date(2026, 2, 1), date(2026, 1, 1), date(2026, 1, 31))
    s = create_batches(app)                            # same run date again: nothing new (D-30)
    assert s.created == 0 and s.existing == 2
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl ORDER BY Src_Cd")
    assert [r["btch_id"] for r in rows] == ["20260201_PRJA_tbl_x_S1_MONTHLY_V1_1", "20260201_PRJA_tbl_x_S2_MONTHLY_V1_1"]
    assert (rows[0]["req_dt_key"], rows[0]["req_stat"], rows[0]["created_by"]) == (date(2026, 2, 1), "PENDING", "SCHEDULER")
    e = q1(conn, "SELECT * FROM ComplianceExtractControl")
    assert (e["required_src_cnt"], e["req_dt_key"], e["extract_close_ind"]) == (2, date(2026, 2, 1), 0)
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestFileDetail WHERE Event_Ty='BATCH_CREATED'")["n"] == 2
    # nothing stores an SLA date: the hold is Req_Dt_Key + (SLA_Days - 1), computed when needed
    cols = {r["column_name"] for r in qa(conn, """SELECT column_name FROM information_schema.columns
                                                   WHERE table_name='compliancerequestcontrol'
                                                     AND table_schema = current_schema()""")}
    assert "earliest_close_dt" not in cols and "intake_id" not in cols and "current_load_id" not in cols


def test_daily_runs_of_the_same_period_get_their_own_batch_and_extract(conn, tmp_path):
    """A CMS-style run type: the same report period, one batch and one extract per run date."""
    seed_config(conn, sources=("S1",))
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 2, 13, 0))
    assert create_batches(app, period="CURRENT_CALENDAR_MONTH").created == 1
    clock.set(utc(2026, 2, 3, 13, 0))
    assert create_batches(app, period="CURRENT_CALENDAR_MONTH").created == 1
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl ORDER BY Req_Dt_Key")
    assert [(r["req_dt_key"], r["rpt_start_dt_key"]) for r in rows] == [
        (date(2026, 2, 2), date(2026, 2, 1)), (date(2026, 2, 3), date(2026, 2, 1))]
    assert len({r["extract_id"] for r in rows}) == 2
    assert [r["btch_id"][-1] for r in rows] == ["1", "1"]


def test_missed_run_recreated_with_as_of(conn, tmp_path):
    seed_config(conn, sources=("S1",))
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 3, 5, 12, 0))
    assert create_batches(app).created == 1                        # Feb period
    clock.set(utc(2026, 2, 1, 12, 0))
    assert create_batches(app).created == 1                        # Jan period, run date Feb 1
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


def test_no_batch_added_to_closed_extract(conn, tmp_path):
    seed_config(conn, sources=("S1",))
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    with conn.transaction():
        conn.execute("UPDATE ComplianceExtractControl SET Extract_Close_Ind=1, Extract_Closed_Dtts=now(), "
                     "Closed_By='ops'")
        conn.execute("INSERT INTO ComplianceSourceSystem (Src_Cd, Src_Nm, Src_Ty) VALUES ('S3','s3','VENDOR')")
        conn.execute("""INSERT INTO ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_Cd, Run_Ty,
                        Effective_Start_Dt, Cmplnc_Vrsn) VALUES ('PRJA','tbl_x','S3','MONTHLY','2025-01-01','V1')""")
    s = create_batches(app)
    assert s.created == 0 and s.skipped == 1
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='BATCH_CREATE_SKIPPED_EXTRACT_CLOSED'")["n"] == 1


# ---------------------------------------------------------------- ad-hoc intake (P4)
def intake(conn, iid, run_ty="ADHOC", src=None, req_ty="UNIVERSE_PULL", start="2026-03-01", end="2026-03-31",
           req_start="2026-04-02", req_end=None):
    with conn.transaction():
        conn.execute("""INSERT INTO ComplianceRequestInTake (Intake_ID, Project_Cd, Table_Nm, Run_Ty, Src_Cd, Req_Ty,
                        Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key, Requested_By)
                        VALUES (%s,'PRJA','tbl_x',%s,%s,%s,%s,%s,%s,%s,'analyst')""",
                     (iid, run_ty, src, req_ty, start, end, req_start, req_end or req_start))


def stat(conn, iid):
    return q1(conn, "SELECT * FROM ComplianceRequestInTake WHERE Intake_ID=%s", iid)


@pytest.fixture
def app(conn, tmp_path):
    seed_config(conn)
    return make_app(conn, tmp_path, utc(2026, 4, 2, 15, 0))


def test_one_off_request_creates_one_run(app, conn):
    a, clock, _ = app
    intake(conn, "A1")                                       # req start = req end = 2026-04-02
    s = a.intake.run()
    assert (s.created, s.completed, s.failed) == (2, 1, 0)
    row = stat(conn, "A1")
    assert (row["intake_stat"], row["last_created_dt_key"]) == ("COMPLETED", date(2026, 4, 2))
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl WHERE Run_Ty='ADHOC' ORDER BY Src_Cd")
    assert [r["created_by"] for r in rows] == ["ADHOC_INTAKE"] * 2
    assert {r["req_dt_key"] for r in rows} == {date(2026, 4, 2)}
    assert q1(conn, "SELECT Required_Src_Cnt n FROM ComplianceExtractControl WHERE Run_Ty='ADHOC'")["n"] == 2
    assert a.intake.run().created == 0                        # COMPLETED rows are not picked up again


def test_request_window_creates_batches_daily_for_the_same_period(app, conn):
    """10-day window, same report dates: one batch per source per run date, each with its own extract."""
    a, clock, _ = app
    intake(conn, "A2", src="S1", req_start="2026-04-02", req_end="2026-04-11")
    for day in (2, 3, 4):
        clock.set(utc(2026, 4, day, 15, 0))
        s = a.intake.run()
        assert s.created == 1 and s.completed == 0
        assert stat(conn, "A2")["intake_stat"] == "IN_PROGRESS"
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl WHERE Run_Ty='ADHOC' ORDER BY Req_Dt_Key")
    assert [r["req_dt_key"] for r in rows] == [date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 4)]
    assert {(r["rpt_start_dt_key"], r["rpt_end_dt_key"]) for r in rows} == {(date(2026, 3, 1), date(2026, 3, 31))}
    assert len({r["extract_id"] for r in rows}) == 3
    assert [r["btch_id"] for r in rows] == [f"2026040{d}_PRJA_tbl_x_S1_ADHOC_V1_1" for d in (2, 3, 4)]
    a.intake.run()                                            # same day twice: idempotent
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestControl WHERE Run_Ty='ADHOC'")["n"] == 3
    clock.set(utc(2026, 4, 11, 15, 0))
    assert a.intake.run().completed == 1
    assert stat(conn, "A2")["intake_stat"] == "COMPLETED"


def test_request_not_started_yet_is_left_alone(app, conn):
    a, clock, _ = app
    intake(conn, "A3", req_start="2026-04-10", req_end="2026-04-12")
    assert a.intake.run().created == 0
    assert stat(conn, "A3")["intake_stat"] == "NEW"


def test_routine_run_type_and_unknown_source_fail(app, conn):
    a, *_ = app
    intake(conn, "R1", run_ty="MONTHLY")
    intake(conn, "R2", start="2020-01-01", end="2020-01-31")      # before Effective_Start_Dt
    s = a.intake.run()
    assert s.failed == 2
    assert stat(conn, "R1")["intake_stat"] == "FAILED" and "ADHOC" in stat(conn, "R1")["error_txt"]
    assert "crosswalk" in stat(conn, "R2")["error_txt"]
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='INTAKE_FAILED'")["n"] == 2
