"""Manual decisions: reopens (late arrival / correction), waivers and carry-forward (D-70)."""
from datetime import date, datetime

import pytest


from .helpers import create_batches, approve_reopen, file_name, make_app, put_file, q1, qa, seed_config, utc


@pytest.fixture
def closed(conn, tmp_path):
    """S1 promoted, S2 missing; BEST_EFFORT manual trigger closed both batches."""
    seed_config(conn)
    app, clock, rules, connector = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0), extract_gating_mode="BEST_EFFORT")
    create_batches(app)
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


def test_late_arrival_reopen_approve_promote_and_retrigger(closed, conn):
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
    assert qa(conn, "SELECT id FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", b["btch_id"]) == [{"id": 5}]
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Extract_ID=%s", ext)
    # BEST_EFFORT with both sources now present and rules passed -> AUTO -> automatic RETRIGGER (D-41)
    assert (e["trigger_cnt"], e["retrigger_required_ind"], e["extract_stat"]) == (2, 0, "COMPLETE")
    assert q1(conn, "SELECT Trigger_Ty FROM ComplianceExtractTrigger ORDER BY Trigger_ID DESC LIMIT 1")["trigger_ty"] == "RETRIGGER"
    assert q1(conn, "SELECT Promotion_Stat FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", out.ovrd_id)["promotion_stat"] == "PROMOTED"


def test_correction_flag_cycle_and_x4_keeps_type(closed, conn):
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
    cur = qa(conn, "SELECT id, amount FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", batch(conn, "S1")["btch_id"])
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


def test_rejection_and_restage_from_archive(closed, conn):
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
    with conn.transaction():                                                         # staged rows lost
        conn.execute("DELETE FROM stg_t.tbl_x")
    d = app.decisions.run()
    assert d.promoted == [b.ovrd_id]
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='REOPEN_RESTAGED_FROM_ARCHIVE'")["n"] == 1
    assert qa(conn, "SELECT id FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", batch(conn, "S2")["btch_id"]) == [{"id": 6}]


def test_promotion_failure_when_archive_missing(closed, conn):
    app, clock, rules, connector, ext = closed
    b = send(app, "S2", ["6|6|f"], datetime(2026, 2, 5, 9, 0))
    approve_reopen(conn, b.ovrd_id, b.load_id)
    with conn.transaction():
        conn.execute("DELETE FROM stg_t.tbl_x")
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


# ---------------------------------------------------------------- carry-forward (D-70)
def request_carry_forward(conn, src, reuse_btch_id=None, approve=True):
    with conn.transaction():
        oid = conn.execute(
            """INSERT INTO ComplianceBatchOverride (Override_Ty, Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd,
                 Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Btch_ID, Reuse_Btch_ID, History)
               SELECT 'CARRY_FORWARD', Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key,
                      Rpt_End_Dt_Key, Btch_ID, %s, 'requested'
                 FROM ComplianceRequestControl WHERE Src_Cd=%s AND Run_Ty='MONTHLY' AND Batch_Close_Ind=0
               RETURNING Ovrd_ID""", (reuse_btch_id, src)).fetchone()["ovrd_id"]
        if approve:
            conn.execute("UPDATE ComplianceBatchOverride SET Apprvl_Stat='APPROVED', Apprvd_By='boss', "
                         "Apprvd_Dtts=now() WHERE Ovrd_ID=%s", (oid,))
    return oid


@pytest.fixture
def next_month(conn, tmp_path):
    """January closed (S1 and S2 promoted); February batches open, only S1 has sent a file."""
    seed_config(conn, carry_fwd=1)
    app, clock, rules, connector = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    for src in ("S1", "S2"):
        send(app, src, ["1|1|" + src], datetime(2026, 2, 1, 9, 0))
    clock.set(utc(2026, 2, 2, 12, 0))
    assert app.evaluator.run().triggered
    clock.set(utc(2026, 3, 1, 13, 0))
    create_batches(app)
    app.pipeline.process_file("inbound", put_file(app, file_name("S1", start=date(2026, 2, 1), end=date(2026, 2, 28),
                                                                 ts=datetime(2026, 3, 1, 9, 0)), ["2|2|x"]))
    return app, clock, rules, connector


def feb(conn, src):
    return q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd=%s AND Rpt_Start_Dt_Key='2026-02-01'", src)


def test_carry_forward_reuses_prior_batch_and_closes_complete(next_month, conn):
    app, clock, rules, connector = next_month
    jan_s2 = batch(conn, "S2")
    oid = request_carry_forward(conn, "S2")
    d = app.decisions.run()
    assert d.invalid == [] and d.processed == 1
    b = feb(conn, "S2")
    assert (b["resolution_ty"], b["req_stat"], b["reuse_btch_id"], b["current_load_id"]) == (
        "CARRY_FORWARD", "CARRIED_FORWARD", jan_s2["btch_id"], None)
    assert q1(conn, "SELECT Reuse_Btch_ID FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", oid)["reuse_btch_id"] == jan_s2["btch_id"]
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Rpt_Start_Dt_Key='2026-02-01'")
    assert (e["extract_stat"], e["received_src_cnt"], e["carried_src_cds"]) == ("COMPLETE", 2, "S2")
    assert jan_s2["btch_id"] in e["combine_btch_id_list"]                     # combine reads January's S2 rows
    assert rules.calls[-1]["load_id_list"] == sorted([feb(conn, "S1")["current_load_id"], jan_s2["current_load_id"]])
    clock.set(utc(2026, 3, 2, 12, 0))
    assert app.evaluator.run().triggered == [e["extract_id"]]
    assert "--BATCHES" in dict(connector.calls[-1]) and jan_s2["btch_id"] in dict(connector.calls[-1])["--BATCHES"]
    b = feb(conn, "S2")
    assert (b["batch_close_ind"], b["req_stat"], b["resolution_ty"]) == (1, "COMPLETED", "CARRY_FORWARD")
    events = [r["event_ty"] for r in qa(conn, "SELECT Event_Ty FROM ComplianceRequestFileDetail WHERE Req_ID=%s "
                                              "ORDER BY Detail_ID", b["req_id"])]
    assert events == ["BATCH_CREATED", "CARRY_FORWARD_APPLIED", "BATCH_CLOSED"]


def test_carry_forward_rejected_when_run_type_disallows(next_month, conn):
    app, *_ = next_month
    with conn.transaction():
        conn.execute("UPDATE ComplianceRunType SET Carry_Fwd_Ind=0")
    request_carry_forward(conn, "S2")
    d = app.decisions.run()
    assert len(d.invalid) == 1 and feb(conn, "S2")["resolution_ty"] is None
    assert "Carry_Fwd_Ind" in q1(conn, "SELECT Description FROM CMS_ComplianceExceptionsAudit "
                                       "WHERE Event_Ty='APPROVAL_INVALID_DETECTED'")["description"]


def test_carry_forward_invalid_cases(next_month, conn):
    app, *_ = next_month
    request_carry_forward(conn, "S1")                                        # S1 already has February data
    request_carry_forward(conn, "S2", reuse_btch_id="no-such-batch")
    assert len(app.decisions.run().invalid) == 2
    with pytest.raises(Exception):                                           # one waiver / carry-forward per batch
        with conn.transaction():
            request_carry_forward(conn, "S2", approve=False)
            request_carry_forward(conn, "S2", approve=False)


def test_file_after_carry_forward_replaces_it(next_month, conn):
    app, *_ = next_month
    oid = request_carry_forward(conn, "S2")
    app.decisions.run()
    out = app.pipeline.process_file("inbound", put_file(app, file_name("S2", start=date(2026, 2, 1),
                                                                       end=date(2026, 2, 28),
                                                                       ts=datetime(2026, 3, 1, 10, 0)), ["3|3|y"]))
    assert (out.result, out.rule) == ("PROMOTED", "O-2")
    b = feb(conn, "S2")
    assert (b["resolution_ty"], b["req_stat"], b["reuse_btch_id"], b["current_load_id"]) == (
        "NEW_FILE", "PROMOTED", None, out.load_id)
    o = q1(conn, "SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", oid)
    assert (o["apprvl_stat"], o["revoked_by"], o["last_processed_apprvl_stat"]) == ("REVOKED", "SYSTEM", "REVOKED")
    assert app.decisions.run().processed == 0
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Rpt_Start_Dt_Key='2026-02-01'")
    assert e["carried_src_cds"] is None and e["received_src_cnt"] == 2


def test_carry_forward_revoked_before_trigger(next_month, conn):
    app, *_ = next_month
    oid = request_carry_forward(conn, "S2")
    app.decisions.run()
    with conn.transaction():
        conn.execute("UPDATE ComplianceBatchOverride SET Apprvl_Stat='REVOKED', Revoked_By='boss', "
                     "Revoked_Dtts=now(), Revocation_Rsn='vendor will send' WHERE Ovrd_ID=%s", (oid,))
    d = app.decisions.run()
    assert d.invalid == []
    b = feb(conn, "S2")
    assert (b["resolution_ty"], b["req_stat"], b["reuse_btch_id"]) == (None, "PENDING", None)
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Rpt_Start_Dt_Key='2026-02-01'")
    assert (e["extract_stat"], e["carried_src_cds"]) == ("PARTIAL", None)


def test_late_file_after_carried_close_is_late_arrival_reopen(next_month, conn):
    app, clock, *_ = next_month
    request_carry_forward(conn, "S2")
    app.decisions.run()
    clock.set(utc(2026, 3, 2, 12, 0))
    app.evaluator.run()
    out = app.pipeline.process_file("inbound", put_file(app, file_name("S2", start=date(2026, 2, 1),
                                                                       end=date(2026, 2, 28),
                                                                       ts=datetime(2026, 3, 3, 10, 0)), ["4|4|z"]))
    o = q1(conn, "SELECT * FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", out.ovrd_id)
    assert (out.rule, o["override_ty"], o["prior_resolution_ty"]) == ("X-1", "LATE_ARRIVAL_REOPEN", "CARRY_FORWARD")
    approve_reopen(conn, out.ovrd_id, out.load_id)
    assert app.decisions.run().promoted == [out.ovrd_id]
    b = feb(conn, "S2")
    assert (b["resolution_ty"], b["req_stat"], b["reuse_btch_id"], b["current_load_id"]) == (
        "NEW_FILE", "COMPLETED", None, out.load_id)
