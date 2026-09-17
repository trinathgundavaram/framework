"""Extract control: combine, eligibility, the SLA sweep and the close that ends a run (D-39, D-76)."""
import os
from datetime import date, datetime

import pytest

from framework import db as locks
from framework.common import CloseBlocked

from .helpers import create_batches, file_name, make_app, put_file, q1, qa, seed_config, utc


def setup(conn, tmp_path, **settings):
    seed_config(conn)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0), **settings)
    create_batches(app)
    return app, clock, rules


def ingest(app, src, rows=("1|1|a",), ts=datetime(2026, 2, 1, 9, 30)):
    return app.pipeline.process_file("inbound", put_file(app, file_name(src, ts=ts), list(rows)))


def extract(conn):
    return q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Run_Ty='MONTHLY'")


def test_auto_close_after_hold_closes_every_batch(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    ingest(app, "S1")
    ingest(app, "S2")
    e = extract(conn)
    assert e["extract_stat"] == "COMPLETE" and e["extract_rules_stat"] == "PASSED"      # early combine (D-21)
    assert e["eligibility_cd"] == "NOT_ELIGIBLE" and "SLA hold" in e["eligibility_rsn_txt"]
    assert app.evaluator.run().closed == []                            # still Feb 1 in Chicago
    clock.set(utc(2026, 2, 2, 6, 5))                                   # 00:05 Feb 2 Chicago
    s = app.evaluator.run()
    assert s.closed == [e["extract_id"]] and s.deferred == []
    e = extract(conn)
    assert (e["extract_close_ind"], e["closed_by"], e["eligibility_cd"]) == (1, "SYSTEM", "AUTO")
    assert e["closed_data_signature"] == e["data_signature"] and e["close_warning_txt"] is None
    assert e["combine_btch_id_list"] == ",".join(sorted(
        r["btch_id"] for r in qa(conn, "SELECT Btch_ID FROM ComplianceRequestControl WHERE Run_Ty='MONTHLY'")))
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl WHERE Run_Ty='MONTHLY'")
    assert {(r["batch_close_ind"], r["req_stat"], r["resolution_ty"]) for r in rows} == {(1, "COMPLETED", "NEW_FILE")}
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='EXTRACT_CLOSED'")["n"] == 1
    assert app.evaluator.run().evaluated == 0                          # nothing left to do


def test_strict_partial_is_never_closed(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    ingest(app, "S1")
    clock.set(utc(2026, 2, 3, 12, 0))
    assert app.evaluator.run().not_eligible == 1
    e = extract(conn)
    assert e["eligibility_cd"] == "NOT_ELIGIBLE" and e["extract_rules_stat"] == "PASSED"
    with pytest.raises(CloseBlocked, match="1 of 2 source"):
        app.control.close(e["extract_id"], "jdoe", ack_warnings=True)
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='EXTRACT_CLOSE_BLOCKED'")["n"] == 1
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestControl WHERE Batch_Close_Ind=1")["n"] == 0


def test_strict_rule_failure_blocks_close(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    rules.period_fail = ["P_TOTALS"]
    ingest(app, "S1")
    ingest(app, "S2")
    e = extract(conn)
    assert e["extract_rules_stat"] == "FAILED" and e["failed_rule_refs"] == "P_TOTALS"
    assert "contributing batches" in q1(conn, "SELECT Description FROM CMS_ComplianceExceptionsAudit "
                                              "WHERE Event_Ty='PERIOD_RULES_FAILED'")["description"]
    clock.set(utc(2026, 2, 3, 12, 0))
    assert app.evaluator.run().not_eligible == 1
    assert extract(conn)["eligibility_cd"] == "NOT_ELIGIBLE"
    rules.period_fail = []
    app.control.refresh(e["extract_id"], "MANUAL_REFRESH")             # re-run the rules against the same data
    assert extract(conn)["eligibility_cd"] == "AUTO"
    assert app.evaluator.run().closed == [e["extract_id"]]


def test_best_effort_manual_close_requires_ack(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path, extract_gating_mode="BEST_EFFORT")
    ingest(app, "S1")
    e = extract(conn)
    with pytest.raises(CloseBlocked, match="SLA hold"):
        app.control.close(e["extract_id"], "jdoe", ack_warnings=True)
    clock.set(utc(2026, 2, 2, 12, 0))
    assert app.evaluator.run().not_eligible == 1                       # MANUAL_ONLY is never auto-closed
    with pytest.raises(CloseBlocked, match="acknowledged"):
        app.control.close(e["extract_id"], "jdoe")
    out = app.control.close(e["extract_id"], "jdoe", ack_warnings=True)
    assert out.warnings and out.closed_batches == 2 and out.warnings_acknowledged
    assert "1 of 2" in extract(conn)["close_warning_txt"]
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='EXTRACT_CLOSED_WITH_WARNINGS'")["n"] == 1
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='SOURCE_MISSING_AT_CLOSE'")["n"] == 1
    with pytest.raises(CloseBlocked, match="already closed"):
        app.control.close(e["extract_id"], "jdoe", ack_warnings=True)


def test_best_effort_zero_data_manual_close(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path, extract_gating_mode="BEST_EFFORT")
    clock.set(utc(2026, 2, 2, 12, 0))
    e = extract(conn)
    out = app.control.close(e["extract_id"], "jdoe", ack_warnings=True)
    assert out.closed_batches == 2 and any("no source" in w for w in out.warnings)
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl WHERE Run_Ty='MONTHLY'")
    assert {(r["req_stat"], r["resolution_ty"]) for r in rows} == {("DATA_NOT_PROVIDED", "MISSING")}


def test_close_deferred_when_a_batch_is_locked(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    ingest(app, "S1")
    ingest(app, "S2")
    clock.set(utc(2026, 2, 2, 12, 0))
    other = locks.connect(os.environ["TEST_DATABASE_URL"])
    try:
        btch = q1(conn, "SELECT Btch_ID FROM ComplianceRequestControl WHERE Src_Cd='S1'")["btch_id"]
        assert locks.try_lock(other, locks.batch_key(btch))
        s = app.evaluator.run()
        assert s.deferred == [extract(conn)["extract_id"]] and extract(conn)["extract_close_ind"] == 0
        assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                        "WHERE Event_Ty='EXTRACT_CLOSE_DEFERRED_LOCKED'")["n"] == 1
    finally:
        other.close()
    assert app.evaluator.run().closed                                   # lock released -> closes


def test_period_rules_technical_error(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    rules.error.add("PERIOD_LEVEL")
    ingest(app, "S1")
    ingest(app, "S2")
    assert extract(conn)["extract_rules_stat"] == "ERROR"
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='RULES_ENGINE_TECHNICAL_FAILURE'")["n"] == 1
    clock.set(utc(2026, 2, 2, 12, 0))
    assert app.evaluator.run().not_eligible == 1
    rules.error.clear()
    assert app.evaluator.run().closed                                   # ERROR forces a fresh combine


def test_sweep_scope_and_auto_close_switch(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    ingest(app, "S1")
    ingest(app, "S2")
    clock.set(utc(2026, 2, 2, 12, 0))
    assert app.evaluator.run(project_cd="OTHER").evaluated == 0          # another project's job
    app.settings.auto_close_extracts = False
    s = app.evaluator.run(project_cd="PRJA")
    assert (s.evaluated, s.closed, s.not_eligible) == (1, [], 1)        # refresh only; a person closes
    app.settings.auto_close_extracts = True
    assert app.evaluator.run(project_cd="PRJA", run_ty="MONTHLY").closed


def test_one_extract_per_run_date_closes_on_its_own_sla(conn, tmp_path):
    """Daily runs of the same report period each get their own extract and their own hold (D-77)."""
    seed_config(conn, sources=("S1",), sla=2)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 2, 13, 0))
    create_batches(app, period="CURRENT_CALENDAR_MONTH")

    def send(day, row):
        app.pipeline.process_file("inbound", put_file(app, file_name(
            "S1", start=date(2026, 2, 1), end=date(2026, 2, 28), ts=datetime(2026, 2, day, 9, 0)), [row]))

    send(2, "2|2|b")
    clock.set(utc(2026, 2, 3, 13, 0))
    create_batches(app, period="CURRENT_CALENDAR_MONTH")
    ids = [r["extract_id"] for r in qa(conn, "SELECT Extract_ID FROM ComplianceExtractControl ORDER BY Req_Dt_Key")]
    assert len(ids) == 2
    assert app.evaluator.run().closed == [ids[0]]                       # Feb 2 run is past its hold, Feb 3 is not
    assert q1(conn, "SELECT Extract_Close_Ind i FROM ComplianceExtractControl WHERE Extract_ID=%s", ids[1])["i"] == 0
    send(3, "3|3|c")                                                    # the open batch of the latest run date
    clock.set(utc(2026, 2, 4, 13, 0))
    assert app.evaluator.run().closed == [ids[1]]


def test_regenerate_flag_is_reported_by_the_sweep(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    ingest(app, "S1")
    ingest(app, "S2")
    clock.set(utc(2026, 2, 2, 12, 0))
    ext = app.evaluator.run().closed[0]
    with conn.transaction():
        conn.execute("UPDATE ComplianceExtractControl SET Regenerate_Required_Ind=1 WHERE Extract_ID=%s", (ext,))
    assert app.evaluator.run().regenerate_required == [ext]
