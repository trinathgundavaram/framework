import pytest


from .helpers import make_app, q1, qa, seed_config, utc


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
