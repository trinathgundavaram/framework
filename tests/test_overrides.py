"""Manual overrides: REUSE (carry-forward), LATE_ARRIVAL and CORRECTION (D-70, D-74).

People insert and approve rows with the templates in sql/approvals.sql; `process-decisions` applies
and expires REUSE, while the ingest pipeline reads LATE_ARRIVAL / CORRECTION (see test_ingest.py).
"""
from datetime import date, datetime

import pytest

from .helpers import (add_override, create_batches, file_name, make_app, put_file, q1, qa, seed_config,
                      stop_override, utc)


def send(app, src, rows, ts, start=date(2026, 1, 1), end=date(2026, 1, 31)):
    return app.pipeline.process_file("inbound", put_file(app, file_name(src, start=start, end=end, ts=ts), rows))


def batch(conn, src, start="2026-01-01"):
    return q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd=%s AND Rpt_Start_Dt_Key=%s", src, start)


def feb(conn, src):
    return batch(conn, src, "2026-02-01")


def reuse(conn, req_id, valid_thru=date(2026, 3, 31), **kw):
    return add_override(conn, req_id, "REUSE", valid_thru, **kw)


@pytest.fixture
def next_month(conn, tmp_path):
    """January closed with both sources promoted; February batches open, only S1 has sent a file."""
    seed_config(conn, carry_fwd=1)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    for src in ("S1", "S2"):
        send(app, src, ["1|1|" + src], datetime(2026, 2, 1, 9, 0))
    clock.set(utc(2026, 2, 2, 12, 0))
    assert app.evaluator.run().closed
    clock.set(utc(2026, 3, 1, 13, 0))
    create_batches(app, period="PREV_CALENDAR_MONTH")
    send(app, "S1", ["2|2|x"], datetime(2026, 3, 1, 9, 0), start=date(2026, 2, 1), end=date(2026, 2, 28))
    return app, clock, rules


def test_reuse_carries_the_prior_batch_and_closes_complete(next_month, conn):
    app, clock, rules = next_month
    jan_s2 = batch(conn, "S2")
    oid = reuse(conn, feb(conn, "S2")["req_id"])
    d = app.decisions.run()
    assert (d.applied, d.invalid, d.expired) == ([oid], [], [])
    b = feb(conn, "S2")
    assert (b["resolution_ty"], b["req_stat"], b["reuse_btch_id"]) == ("CARRY_FORWARD", "CARRIED_FORWARD",
                                                                      jan_s2["btch_id"])
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Rpt_Start_Dt_Key='2026-02-01'")
    assert (e["extract_stat"], e["received_src_cnt"], e["carried_src_cds"]) == ("COMPLETE", 2, "S2")
    assert jan_s2["btch_id"] in e["combine_btch_id_list"]                  # the extract reads January's S2 rows
    jan_load = q1(conn, "SELECT Load_ID FROM ComplianceFileLoad WHERE Btch_ID=%s AND Load_Stat='PROMOTED'",
                  jan_s2["btch_id"])["load_id"]
    feb_load = q1(conn, "SELECT Load_ID FROM ComplianceFileLoad WHERE Btch_ID=%s AND Load_Stat='PROMOTED'",
                  feb(conn, "S1")["btch_id"])["load_id"]
    assert rules.calls[-1]["load_id_list"] == sorted([jan_load, feb_load])
    clock.set(utc(2026, 3, 2, 12, 0))
    assert app.evaluator.run().closed == [e["extract_id"]]
    b = feb(conn, "S2")
    assert (b["batch_close_ind"], b["req_stat"], b["resolution_ty"]) == (1, "COMPLETED", "CARRY_FORWARD")
    events = [r["event_ty"] for r in qa(conn, "SELECT Event_Ty FROM ComplianceRequestFileDetail WHERE Req_ID=%s "
                                              "ORDER BY Detail_ID", b["req_id"])]
    assert events == ["BATCH_CREATED", "CARRY_FORWARD_APPLIED", "BATCH_CLOSED"]
    assert app.decisions.run().expired == []                                # a closed batch is left alone


def test_reuse_is_rejected_when_the_run_type_disallows_it(next_month, conn):
    app, *_ = next_month
    with conn.transaction():
        conn.execute("UPDATE ComplianceRunType SET Carry_Fwd_Ind=0")
    oid = reuse(conn, feb(conn, "S2")["req_id"])
    assert app.decisions.run().invalid == [oid]
    assert feb(conn, "S2")["resolution_ty"] is None
    assert "Carry_Fwd_Ind" in q1(conn, "SELECT Description FROM CMS_ComplianceExceptionsAudit "
                                       "WHERE Event_Ty='OVERRIDE_INVALID_DETECTED'")["description"]


def test_reuse_invalid_when_batch_has_data_or_source_is_unknown(next_month, conn):
    app, *_ = next_month
    a = reuse(conn, feb(conn, "S1")["req_id"])                              # S1 already has February data
    b = reuse(conn, feb(conn, "S2")["req_id"], reuse_btch_id="no-such-batch")
    assert sorted(app.decisions.run().invalid) == sorted([a, b])
    with pytest.raises(Exception):                                          # one active override per batch and type
        reuse(conn, feb(conn, "S2")["req_id"], approved=False)


def test_pending_reuse_is_not_applied_until_approved(next_month, conn):
    app, *_ = next_month
    oid = reuse(conn, feb(conn, "S2")["req_id"], approved=False)
    assert app.decisions.run().applied == []
    assert feb(conn, "S2")["req_stat"] == "PENDING"
    with conn.transaction():
        conn.execute("UPDATE ComplianceBatchOverride SET Apprvl_Stat='APPROVED', Apprvd_By='boss', "
                     "Apprvd_Dtts=now(), Valid_Thru_Dt_Key='2026-03-31' WHERE Ovrd_ID=%s", (oid,))
    assert app.decisions.run().applied == [oid]
    assert feb(conn, "S2")["req_stat"] == "CARRIED_FORWARD"


def test_reuse_expires_when_its_validity_date_passes(next_month, conn):
    app, clock, _ = next_month
    oid = reuse(conn, feb(conn, "S2")["req_id"], valid_thru=date(2026, 3, 3))
    app.decisions.run()
    stop_override(conn, oid, date(2026, 2, 28))                             # "stop using that batch" (template 6)
    d = app.decisions.run()
    assert d.expired == [oid] and d.applied == []
    b = feb(conn, "S2")
    assert (b["resolution_ty"], b["req_stat"], b["reuse_btch_id"]) == (None, "PENDING", None)
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Rpt_Start_Dt_Key='2026-02-01'")
    assert (e["extract_stat"], e["carried_src_cds"], e["received_src_cnt"]) == ("PARTIAL", None, 1)
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='OVERRIDE_EXPIRED'")["n"] == 1
    clock.set(utc(2026, 3, 4, 13, 0))                                       # nothing left to expire or apply
    assert app.decisions.run() == app.decisions.run()


def test_a_file_replaces_a_carried_forward_batch(next_month, conn):
    app, clock, _ = next_month
    oid = reuse(conn, feb(conn, "S2")["req_id"])
    app.decisions.run()
    out = send(app, "S2", ["3|3|y"], datetime(2026, 3, 1, 10, 0), start=date(2026, 2, 1), end=date(2026, 2, 28))
    assert (out.result, out.rule) == ("PROMOTED", "O-2")   # replaces the carried data
    b = feb(conn, "S2")
    assert (b["resolution_ty"], b["req_stat"], b["reuse_btch_id"]) == ("NEW_FILE", "PROMOTED", None)
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Rpt_Start_Dt_Key='2026-02-01'")
    assert e["carried_src_cds"] is None and e["received_src_cnt"] == 2
    assert app.decisions.run().applied == []                                # the batch has data now
    assert q1(conn, "SELECT Apprvl_Stat FROM ComplianceBatchOverride WHERE Ovrd_ID=%s", oid)["apprvl_stat"] == "APPROVED"


def test_reuse_follows_a_chain_back_to_real_data(conn, tmp_path):
    """February carried January; March reuses February and lands on January's rows."""
    seed_config(conn, sources=("S1",), carry_fwd=1)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    send(app, "S1", ["1|1|jan"], datetime(2026, 2, 1, 9, 0))
    jan = batch(conn, "S1")
    clock.set(utc(2026, 2, 2, 12, 0))
    app.evaluator.run()
    clock.set(utc(2026, 3, 1, 13, 0))
    create_batches(app)
    reuse(conn, feb(conn, "S1")["req_id"], valid_thru=date(2026, 4, 30))
    app.decisions.run()
    clock.set(utc(2026, 3, 2, 12, 0))
    app.evaluator.run()
    clock.set(utc(2026, 4, 1, 13, 0))
    create_batches(app)
    mar = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Rpt_Start_Dt_Key='2026-03-01'")
    reuse(conn, mar["req_id"], valid_thru=date(2026, 4, 30))
    assert app.decisions.run().invalid == []
    assert q1(conn, "SELECT Reuse_Btch_ID r FROM ComplianceRequestControl WHERE Req_ID=%s",
              mar["req_id"])["r"] == jan["btch_id"]
    e = q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Rpt_Start_Dt_Key='2026-03-01'")
    assert e["combine_btch_id_list"] == jan["btch_id"] and e["extract_stat"] == "COMPLETE"


def test_late_arrival_and_correction_rows_are_left_to_the_pipeline(next_month, conn):
    """process-decisions neither applies nor expires the two ingest-side override types."""
    app, clock, _ = next_month
    jan_s2 = batch(conn, "S2")
    add_override(conn, jan_s2["req_id"], "CORRECTION", date(2026, 3, 5))
    d = app.decisions.run()
    assert (d.applied, d.expired, d.invalid) == ([], [], [])
    assert batch(conn, "S2")["req_stat"] == "COMPLETED"
