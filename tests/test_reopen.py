from datetime import datetime

import pytest

from framework.batches.scheduler import run_scheduler

from .helpers import approve_reopen, file_name, make_app, put_file, q1, qa, seed_config, utc


@pytest.fixture
def closed(conn, tmp_path):
    """S1 promoted, S2 missing; BEST_EFFORT manual trigger closed both batches."""
    seed_config(conn, gating="BEST_EFFORT")
    app, clock, rules, connector = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    run_scheduler(conn, clock, app.settings)
    app.pipeline.process_file("inbound", put_file(app, file_name("S1"), ["1|1|a", "2|2|b"]))
    clock.set(utc(2026, 2, 2, 12, 0))
    ext = q1(conn, "SELECT Extract_ID FROM ComplianceExtractControl WHERE Run_Ty='MONTHLY'")["extract_id"]
    app.trigger.fire(ext, "MANUAL", "ops", ack_warnings=True)
    clock.set(utc(2026, 2, 5, 12, 0))
    return app, clock, rules, connector, ext


def send(app, src, rows, ts):
    return app.pipeline.process_file("inbound", put_file(app, file_name(src, ts=ts), rows))


def batch(conn, src):
    return q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd=%s AND Run_Ty='MONTHLY'", src)


def test_late_arrival_reopen_approve_promote_and_retrigger(closed, conn, data):
    app, clock, rules, connector, ext = closed
    out = send(app, "S2", ["5|5|e"], datetime(2026, 2, 5, 8, 0))
    assert (out.result, out.rule) == ("PENDING_APPROVAL", "X-1")
    o = q1(conn, "SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", out.ovrd_id)
    assert (o["override_ty"], o["prior_resolution_ty"], o["apprvl_stat"]) == ("LATE_ARRIVAL_REOPEN", "MISSING", "PENDING_REVIEW")
    assert batch(conn, "S2")["req_stat"] == "DATA_NOT_PROVIDED"                   # untouched until approval
    assert app.decisions.run().processed == 0                                      # framework-created row is already processed
    assert approve_reopen(conn, out.ovrd_id, out.load_id + 99) == 0                # stale/incorrect review -> 0 rows
    assert approve_reopen(conn, out.ovrd_id, out.load_id) == 1
    d = app.decisions.run()
    assert d.promoted == [out.ovrd_id] and d.invalid == []
    b = batch(conn, "S2")
    assert (b["batch_close_ind"], b["resolution_ty"], b["req_stat"], b["current_load_id"]) == (1, "NEW_FILE", "COMPLETED", out.load_id)
    assert qa(data, "SELECT id FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", b["btch_id"]) == [{"id": 5}]
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Extract_ID=%s", ext)
    # BEST_EFFORT with both sources now present and rules passed -> AUTO -> automatic RETRIGGER (D-41)
    assert (e["trigger_cnt"], e["retrigger_required_ind"], e["extract_stat"]) == (2, 0, "COMPLETE")
    assert q1(conn, "SELECT Trigger_Ty FROM ComplianceExtractTrigger ORDER BY Trigger_ID DESC LIMIT 1")["trigger_ty"] == "RETRIGGER"
    assert q1(conn, "SELECT Promotion_Stat FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", out.ovrd_id)["promotion_stat"] == "PROMOTED"


def test_correction_flag_cycle_and_x4_keeps_type(closed, conn, data):
    app, clock, rules, connector, ext = closed
    with conn.transaction():
        conn.execute("""INSERT INTO ComplianceRequestInTake (Intake_ID, Project_Cd, Table_Nm, Run_Ty, Src_Cd, Req_Ty,
                        Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Requested_By) VALUES
                        ('C1','PRJA','tbl_x','MONTHLY','S1','CORRECTION_REQUEST','2026-01-01','2026-01-31','analyst')""")
    app.intake.run()
    assert q1(conn, "SELECT Currently_Flagged_For_Correction f FROM vw_crc_current_flags WHERE Req_ID=%s",
              batch(conn, "S1")["req_id"])["f"] is True
    out = send(app, "S1", ["1|100|a"], datetime(2026, 2, 5, 8, 0))
    o = q1(conn, "SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", out.ovrd_id)
    assert o["override_ty"] == "CORRECTION_REOPEN" and o["prior_load_id"] == batch(conn, "S1")["current_load_id"]
    approve_reopen(conn, out.ovrd_id, out.load_id)
    app.decisions.run()
    assert q1(conn, "SELECT Currently_Flagged_For_Correction f FROM vw_crc_current_flags WHERE Req_ID=%s",
              batch(conn, "S1")["req_id"])["f"] is False
    cur = qa(data, "SELECT id, amount FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", batch(conn, "S1")["btch_id"])
    assert [(r["id"], int(r["amount"])) for r in cur] == [(1, 100)]
    # second correction after promotion -> X-4, same row, type kept
    out2 = send(app, "S1", ["1|200|a"], datetime(2026, 2, 6, 8, 0))
    assert (out2.rule, out2.ovrd_id) == ("X-4", out.ovrd_id)
    o = q1(conn, "SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", out.ovrd_id)
    assert (o["apprvl_stat"], o["promotion_stat"], o["candidate_load_id"], o["prior_load_id"]) == (
        "PENDING_REVIEW", "NOT_APPLICABLE", out2.load_id, out.load_id)
    assert q1(conn, "SELECT Load_Stat FROM ComplianceFileLoad WHERE Load_ID=%s", out.load_id)["load_stat"] == "PROMOTED"


def test_x2_x3_candidate_replacement(closed, conn):
    app, clock, rules, connector, ext = closed
    a = send(app, "S2", ["5|5|e"], datetime(2026, 2, 5, 8, 0))
    b = send(app, "S2", ["6|6|f"], datetime(2026, 2, 5, 9, 0))
    assert (b.rule, b.ovrd_id) == ("X-2", a.ovrd_id)
    assert approve_reopen(conn, a.ovrd_id, a.load_id) == 0                         # reviewer saw the old file
    assert approve_reopen(conn, a.ovrd_id, b.load_id) == 1
    c = send(app, "S2", ["7|7|g"], datetime(2026, 2, 5, 10, 0))                   # arrives before promotion
    assert c.rule == "X-3"
    o = q1(conn, "SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", a.ovrd_id)
    assert (o["apprvl_stat"], o["candidate_load_id"], o["reviewed_load_id"]) == ("PENDING_REVIEW", c.load_id, None)
    d = app.decisions.run()
    assert d.promoted == [] and d.invalid == []                                    # approval was reset, nothing promoted
    states = {r["load_id"]: r["load_stat"] for r in qa(conn, "SELECT Load_ID, Load_Stat FROM ComplianceFileLoad")}
    assert states[a.load_id] == "SUPERSEDED" and states[b.load_id] == "SUPERSEDED" and states[c.load_id] == "PENDING_APPROVAL"


def test_x5_reopen_rejected_when_rules_fail(closed, conn):
    app, clock, rules, connector, ext = closed
    rules.file_fail["S2"] = ["R1"]
    out = send(app, "S2", ["5|5|e"], datetime(2026, 2, 5, 8, 0))
    assert (out.result, out.rule) == ("REOPEN_REJECTED", "X-5")
    assert q1(conn, "SELECT count(*) n FROM ComplianceBatchOverride WHERE Override_Ty LIKE '%%REOPEN'")["n"] == 0


def test_rejection_and_restage_from_archive(closed, conn, data):
    app, clock, rules, connector, ext = closed
    a = send(app, "S2", ["5|5|e"], datetime(2026, 2, 5, 8, 0))
    with conn.transaction():
        conn.execute("UPDATE ComplianceBatchOverride SET Apprvl_Stat='REJECTED', Rejected_By='boss', "
                     "Rejected_Dtts=now(), Rejection_Rsn='wrong file' WHERE Ovrd_ID=%s", (a.ovrd_id,))
    app.decisions.run()
    assert q1(conn, "SELECT Load_Stat FROM ComplianceFileLoad WHERE Load_ID=%s", a.load_id)["load_stat"] == "SUPERSEDED"
    b = send(app, "S2", ["6|6|f"], datetime(2026, 2, 5, 9, 0))
    assert b.rule == "X-1" and b.ovrd_id != a.ovrd_id
    approve_reopen(conn, b.ovrd_id, b.load_id)
    with data.transaction():                                                         # staged rows lost
        data.execute("DELETE FROM stg_t.tbl_x")
    d = app.decisions.run()
    assert d.promoted == [b.ovrd_id]
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='REOPEN_RESTAGED_FROM_ARCHIVE'")["n"] == 1
    assert qa(data, "SELECT id FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", batch(conn, "S2")["btch_id"]) == [{"id": 6}]


def test_promotion_failure_when_archive_missing(closed, conn, data):
    app, clock, rules, connector, ext = closed
    b = send(app, "S2", ["6|6|f"], datetime(2026, 2, 5, 9, 0))
    approve_reopen(conn, b.ovrd_id, b.load_id)
    with data.transaction():
        data.execute("DELETE FROM stg_t.tbl_x")
    app.store.delete("inbound", "prja/archive/" + file_name("S2", ts=datetime(2026, 2, 5, 9, 0)))
    d = app.decisions.run()
    assert d.promotion_failed == [b.ovrd_id]
    assert q1(conn, "SELECT Promotion_Stat FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", b.ovrd_id)["promotion_stat"] == "FAILED"
    assert batch(conn, "S2")["req_stat"] == "DATA_NOT_PROVIDED"


def test_invalid_manual_changes_detected(closed, conn):
    app, clock, rules, connector, ext = closed
    a = send(app, "S2", ["5|5|e"], datetime(2026, 2, 5, 8, 0))
    approve_reopen(conn, a.ovrd_id, a.load_id)
    app.decisions.run()
    with conn.transaction():   # reopens cannot be revoked (DB CHECK)
        with pytest.raises(Exception):
            with conn.transaction():
                conn.execute("UPDATE ComplianceBatchOverride SET Apprvl_Stat='REVOKED', Revoked_By='x', "
                             "Revoked_Dtts=now(), Revocation_Rsn='r' WHERE Ovrd_ID=%s", (a.ovrd_id,))
    with conn.transaction():   # a hand-inserted reopen is flagged
        conn.execute("""INSERT INTO ComplianceBatchOverride (Override_Ty, Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd,
                          Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Btch_ID, Candidate_Load_ID, Reviewed_Load_ID,
                          Apprvl_Stat, Apprvd_By, Apprvd_Dtts, History)
                        SELECT 'CORRECTION_REOPEN', Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty,
                               Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Btch_ID, Current_Load_ID, Current_Load_ID,
                               'APPROVED', 'x', now(), 'manual'
                          FROM ComplianceRequestControl WHERE Src_Cd='S1' AND Run_Ty='MONTHLY'""")
    d = app.decisions.run()
    assert len(d.invalid) == 1 and d.promoted == []
