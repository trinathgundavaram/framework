"""Batch close: the SLA sweep closes batches with data or in exception; a person closes the rest."""
import os
from datetime import date, datetime

import pytest

from framework import db as locks
from framework.common import CloseBlocked

from .helpers import create_batches, file_name, make_app, put_file, q1, qa, seed_config, utc


def setup(conn, tmp_path, **kw):
    seed_config(conn, **kw)                                                # MONTHLY: SLA_Days = 2
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)                                                    # run date Feb 1 -> hold ends Feb 2
    return app, clock, rules


def ingest(app, src, rows=("1|1|a",)):
    return app.pipeline.process_file("inbound", put_file(app, file_name(src), list(rows)))


def batch(conn, src):
    return q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_ID=%s", src)


def events(conn, event_ty):
    return q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty=%s", event_ty)["n"]


def test_sweep_closes_batches_with_data_after_the_hold_and_leaves_the_rest(conn, tmp_path):
    app, clock, _ = setup(conn, tmp_path)
    ingest(app, "S1")
    assert app.closer.run().evaluated == 0                                 # Feb 1: still inside the hold
    clock.set(utc(2026, 2, 2, 6, 5))                                       # 00:05 Feb 2 Chicago: hold day
    s = app.closer.run()
    s1, s2 = batch(conn, "S1"), batch(conn, "S2")
    assert (s.evaluated, s.closed, s.waiting, s.deferred) == (2, [s1["btch_id"]], [s2["btch_id"]], [])
    assert (s1["batch_close_ind"], s1["req_stat"], s1["resolution_ty"]) == (1, "COMPLETED", "NEW_FILE")
    assert (s2["batch_close_ind"], s2["req_stat"]) == (0, "PENDING")      # no data: waits for a person
    assert [r["event_ty"] for r in qa(conn, "SELECT Event_Ty FROM ComplianceRequestFileDetail WHERE Req_ID=%s "
                                            "ORDER BY Detail_ID", s1["req_id"])][-1] == "BATCH_CLOSED"
    again = app.closer.run()                                               # idempotent
    assert (again.closed, again.waiting) == ([], [s2["btch_id"]])


def test_sweep_closes_a_batch_in_exception(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    rules.file_fail["S1"] = ["R1"]
    assert ingest(app, "S1").result == "RULES_FAILED"
    clock.set(utc(2026, 2, 2, 12, 0))
    assert app.closer.run().closed == [batch(conn, "S1")["btch_id"]]
    b = batch(conn, "S1")
    assert (b["req_stat"], b["resolution_ty"]) == ("COMPLETED_WITH_EXCEPTION", "MISSING")
    assert events(conn, "SOURCE_MISSING_AT_CLOSE") == 1


def test_manual_close_of_a_batch_without_data(conn, tmp_path):
    app, clock, _ = setup(conn, tmp_path)
    s2 = batch(conn, "S2")
    with pytest.raises(CloseBlocked, match="SLA hold until 2026-02-02"):
        app.closer.close(s2["btch_id"], "jdoe")
    clock.set(utc(2026, 2, 2, 12, 0))
    out = app.closer.close(s2["btch_id"], "jdoe")
    assert (out.req_stat, out.resolution_ty) == ("DATA_NOT_PROVIDED", "MISSING")
    assert batch(conn, "S2")["batch_close_ind"] == 1
    assert events(conn, "SOURCE_MISSING_AT_CLOSE") == 1
    assert q1(conn, "SELECT Actor FROM ComplianceRequestFileDetail WHERE Event_Ty='BATCH_CLOSED'")["actor"] == "jdoe"
    with pytest.raises(CloseBlocked, match="already closed"):
        app.closer.close(s2["btch_id"], "jdoe")
    assert events(conn, "BATCH_CLOSE_BLOCKED") == 2
    with pytest.raises(LookupError):
        app.closer.close("no-such-batch", "jdoe")


def test_close_is_deferred_while_another_process_holds_the_batch(conn, tmp_path):
    app, clock, _ = setup(conn, tmp_path)
    ingest(app, "S1")
    clock.set(utc(2026, 2, 2, 12, 0))
    other = locks.connect(os.environ["TEST_DATABASE_URL"])
    try:
        s1 = batch(conn, "S1")["btch_id"]
        assert locks.try_lock(other, locks.batch_key(s1))
        assert app.closer.run().deferred == [s1]
        assert batch(conn, "S1")["batch_close_ind"] == 0 and events(conn, "BATCH_CLOSE_DEFERRED_LOCKED") == 1
    finally:
        other.close()
    assert app.closer.run().closed == [s1]                                  # lock released -> closes


def test_sweep_scope_and_per_run_date_holds(conn, tmp_path):
    """Daily runs of the same period: each batch closes on its own hold (D-77); scope limits the sweep."""
    seed_config(conn, sources=("S1",), sla=2)
    app, clock, _ = make_app(conn, tmp_path, utc(2026, 2, 2, 13, 0))
    create_batches(app, period="CURRENT_CALENDAR_MONTH")

    def send(day):
        app.pipeline.process_file("inbound", put_file(app, file_name(
            "S1", start=date(2026, 2, 1), end=date(2026, 2, 28), ts=datetime(2026, 2, day, 9, 0)), [f"{day}|1|a"]))

    send(2)
    clock.set(utc(2026, 2, 3, 13, 0))
    create_batches(app, period="CURRENT_CALENDAR_MONTH")
    send(3)                                                                # the open batch of the latest run date
    ids = [r["btch_id"] for r in qa(conn, "SELECT Btch_ID FROM ComplianceRequestControl ORDER BY Req_Dt_Key")]
    assert app.closer.run(project_cd="OTHER").evaluated == 0
    assert app.closer.run(project_cd="PRJA", run_ty="MONTHLY").closed == [ids[0]]   # Feb 2 run past its hold
    clock.set(utc(2026, 2, 4, 13, 0))
    assert app.closer.run().closed == [ids[1]]


def test_health_lists_open_batches_past_their_hold(conn, tmp_path):
    app, clock, _ = setup(conn, tmp_path)
    clock.set(utc(2026, 2, 2, 12, 0))                                      # hold day itself: not yet overdue
    assert app.closer.health() == {"batches_past_hold_not_closed": []}
    clock.set(utc(2026, 2, 3, 12, 0))
    overdue = app.closer.health()["batches_past_hold_not_closed"]
    assert sorted(b["src_id"] for b in overdue) == ["S1", "S2"] and overdue[0]["req_stat"] == "PENDING"
