from datetime import date, datetime

import pytest

from framework.common import TechnicalFailure

from .helpers import create_batches, file_name, make_app, put_file, q1, qa, seed_config, utc


@pytest.fixture
def env(conn, tmp_path):
    seed_config(conn)
    app, clock, rules, connector = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    return app, clock, rules, connector


def ingest(app, name, rows, header=True, **kw):
    key = put_file(app, name, rows, header, **kw)
    return app.pipeline.process_file("inbound", key)


def core_rows(conn, src="S1"):
    return qa(conn, """SELECT c.id, c.amount, c.current_ind, c.load_id FROM core_t.tbl_x c
                        JOIN ComplianceRequestControl b ON b.Btch_ID = c.btch_id
                       WHERE b.Src_Cd = %s ORDER BY c.load_id, c.id""", src)


def test_o1_promote(env, conn):
    app, *_ = env
    out = ingest(app, file_name("S1"), ["1|10.50|a", "2||b"])
    assert (out.result, out.rule) == ("PROMOTED", "O-1")
    b = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S1'")
    assert (b["resolution_ty"], b["req_stat"], b["current_load_id"]) == ("NEW_FILE", "PROMOTED", out.load_id)
    assert [(r["id"], r["current_ind"]) for r in core_rows(conn)] == [(1, 1), (2, 1)]
    assert core_rows(conn)[1]["amount"] is None                     # empty -> NULL
    load = q1(conn, "SELECT * FROM ComplianceFileLoad WHERE Load_ID=%s", out.load_id)
    assert load["load_stat"] == "PROMOTED" and load["stg_rcd_cnt"] == 2 and load["file_sha256"]
    assert app.store.exists("inbound", "prja/archive/" + file_name("S1"))
    assert not app.store.exists("inbound", "prja/in/" + file_name("S1"))
    events = [r["event_ty"] for r in qa(conn, "SELECT Event_Ty FROM ComplianceRequestFileDetail WHERE Req_ID=%s "
                                              "ORDER BY Detail_ID", b["req_id"])]
    assert events == ["BATCH_CREATED", "FILE_RECEIVED", "FILE_PROMOTED"]
    e = q1(conn, "SELECT * FROM ComplianceExtractControl")
    assert e["extract_stat"] == "PARTIAL" and e["received_src_cnt"] == 1 and e["combine_run_cnt"] == 0


def test_o2_replacement_latest_arrival_wins(env, conn):
    app, *_ = env
    first = ingest(app, file_name("S1", ts=datetime(2026, 2, 1, 10, 0, 0)), ["1|1|a", "2|2|b"])
    # older {TS} but arrives later -> still wins (D-37)
    second = ingest(app, file_name("S1", ts=datetime(2026, 2, 1, 9, 0, 0)), ["7|7|z"])
    assert (second.result, second.rule) == ("PROMOTED", "O-2")
    rows = core_rows(conn)
    assert [(r["id"], r["current_ind"], r["load_id"]) for r in rows] == [
        (1, 0, first.load_id), (2, 0, first.load_id), (7, 1, second.load_id)]
    assert q1(conn, "SELECT Load_Stat FROM ComplianceFileLoad WHERE Load_ID=%s", first.load_id)["load_stat"] == "SUPERSEDED"
    staged = qa(conn, "SELECT load_id FROM stg_t.tbl_x")
    assert {r["load_id"] for r in staged} == {second.load_id}    # D-05 delete by Btch_ID


def test_o3_o4_rules_failures(env, conn):
    app, clock, rules, _ = env
    rules.file_fail["S1"] = ["R_NOT_NULL"]
    out = ingest(app, file_name("S1"), ["1|1|a"])
    assert (out.result, out.rule) == ("RULES_FAILED", "O-3")
    b = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S1'")
    assert b["req_stat"] == "EXCEPTION_PENDING" and b["resolution_ty"] is None
    assert core_rows(conn) == []
    rules.file_fail.clear()
    good = ingest(app, file_name("S1", ts=datetime(2026, 2, 1, 11, 0, 0)), ["1|1|a"])
    assert good.rule == "O-1"
    rules.file_fail["S1"] = ["R_NOT_NULL"]
    bad = ingest(app, file_name("S1", ts=datetime(2026, 2, 1, 12, 0, 0)), ["9|9|x"])
    assert bad.rule == "O-4"
    b = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S1'")
    assert (b["req_stat"], b["resolution_ty"], b["current_load_id"]) == ("EXCEPTION_PENDING", "NEW_FILE", good.load_id)
    assert [r["id"] for r in core_rows(conn) if r["current_ind"] == 1] == [1]       # D-46
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='RULES_VALIDATION_FAILED'")["n"] == 2
    assert rules.calls[0]["mode"] == "GATE" and rules.calls[0]["btch_id"] == b["btch_id"]


def test_annotate_warnings_promote(conn, tmp_path):
    seed_config(conn)
    app, clock, rules, _ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0), file_rules_mode="ANNOTATE")
    create_batches(app)
    rules.file_fail["S1"] = ["R_WARN"]
    out = ingest(app, file_name("S1"), ["1|1|a"])
    assert out.result == "PROMOTED"
    assert q1(conn, "SELECT Rules_Stat FROM ComplianceFileLoad WHERE Load_ID=%s", out.load_id)["rules_stat"] == "PASSED_WITH_WARNINGS"


@pytest.mark.parametrize("name, key_prefix, event", [
    ("garbage.txt", "prja/in/", "FILE_REJECTED_UNPARSEABLE"),
    (file_name("S1"), "prja/other/", "FILE_REJECTED_UNPARSEABLE"),           # wrong location
    (file_name("S1", runty="WEEKLY"), "prja/in/", "FILE_REJECTED_RUNTY_NOT_CONFIGURED"),
    (file_name("S1", start=date(2026, 2, 1), end=date(2026, 2, 28)), "prja/in/", "FILE_REJECTED_NO_BATCH"),
    (file_name("S1", start=date(2026, 1, 2), end=date(2026, 1, 31)), "prja/in/", "FILE_REJECTED_NO_BATCH"),
    ("PRJA_TBLX_S1_MONTHLY_20260132_20260131_20260201093000.txt", "prja/in/", "FILE_REJECTED_INVALID_TOKEN"),
])
def test_quarantine_prechecks(env, conn, name, key_prefix, event):
    app, *_ = env
    out = ingest(app, name, ["1|1|a"], key_prefix=key_prefix)
    assert (out.result, out.event_ty) == ("QUARANTINED", event)
    load = q1(conn, "SELECT * FROM ComplianceFileLoad WHERE Load_ID=%s", out.load_id)
    assert load["load_stat"] == "QUARANTINED" and load["quarantine_rsn_cd"] == event
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty=%s", event)["n"] == 1
    assert q1(conn, "SELECT count(*) n FROM ComplianceBatchOverride")["n"] == 0
    assert not app.store.exists("inbound", key_prefix + name)


@pytest.mark.parametrize("rows, header, event", [
    (["1|1"], True, "FILE_COLUMN_COUNT_MISMATCH"),
    (["1|1|a|extra"], True, "FILE_COLUMN_COUNT_MISMATCH"),
    (["x|1|a"], True, "FILE_PARSE_ERROR"),                      # cast failure
    (['1|1|"unterminated'], True, "FILE_PARSE_ERROR"),
])
def test_structural_failures(env, conn, rows, header, event):
    app, *_ = env
    out = ingest(app, file_name("S1"), rows, header)
    assert (out.result, out.event_ty) == ("QUARANTINED", event)
    desc = q1(conn, "SELECT Description FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty=%s", event)["description"]
    assert '"x"' not in desc                                     # no file content in audit text
    b = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S1'")
    assert b["req_stat"] == "PENDING"


def test_zero_byte_with_header_is_parse_error(env, conn):
    app, *_ = env
    key = "prja/in/" + file_name("S1")
    app.store.put("inbound", key, b"")
    out = app.pipeline.process_file("inbound", key)
    assert out.event_ty == "FILE_PARSE_ERROR"


def test_zero_records(conn, tmp_path):
    seed_config(conn, allow_zero=0)
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    out = ingest(app, file_name("S1"), [])
    assert (out.result, out.event_ty) == ("RULES_FAILED", "FILE_ZERO_RECORDS_REJECTED")
    with conn.transaction():
        conn.execute("UPDATE ComplianceSourceFileConfig SET Allow_Zero_Rcd_Ind=1")
    out = ingest(app, file_name("S1", ts=datetime(2026, 2, 1, 11, 0)), ["", ""])   # header + blank lines
    assert out.result == "PROMOTED"
    b = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S1'")
    assert b["resolution_ty"] == "NEW_FILE"


def test_duplicate_and_same_content_other_batch(env, conn):
    app, clock, *_ = env
    ingest(app, file_name("S1"), ["1|1|a"])
    dup = ingest(app, file_name("S1", ts=datetime(2026, 2, 1, 11, 0)), ["1|1|a"])
    assert (dup.result, dup.event_ty) == ("QUARANTINED", "FILE_REJECTED_DUPLICATE")
    other = ingest(app, file_name("S2"), ["1|1|a"])          # same bytes, other batch -> allowed (D-52)
    assert other.result == "PROMOTED"
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='FILE_SAME_CONTENT_OTHER_BATCH'")["n"] == 1


def test_replay_and_technical_failure_restart(env, conn):
    app, clock, rules, _ = env
    name = file_name("S1")
    key = put_file(app, name, ["1|1|a"])
    rules.error.add("FILE_LEVEL")
    with pytest.raises(TechnicalFailure):
        app.pipeline.process_file("inbound", key)
    load = q1(conn, "SELECT * FROM ComplianceFileLoad")
    assert load["load_stat"] == "FAILED_TECHNICAL"
    assert q1(conn, "SELECT Req_Stat FROM ComplianceRequestControl WHERE Src_Cd='S1'")["req_stat"] == "PENDING"
    assert app.store.exists("inbound", key)                  # not archived; will be retried
    rules.error.clear()
    out = app.pipeline.process_file("inbound", key)           # same object -> same Load_ID restarted (C0)
    assert out.result == "PROMOTED" and out.load_id == load["load_id"]
    # replay of a finished object is ignored
    app.store.put("inbound", key, ("id|amount|name\n1|1|a\n").encode())
    again = app.pipeline.process_file("inbound", key)
    assert again.result == "REPLAY_IGNORED"
    assert not app.store.exists("inbound", key)              # interrupted archive completed


def test_unsupported_file_type(conn, tmp_path):
    seed_config(conn, sources=("S1",))
    with conn.transaction():
        conn.execute("UPDATE ComplianceSourceFileConfig SET Src_File_Ty='.xlsx', "
                     "Src_File_Nm_Tmplt=replace(Src_File_Nm_Tmplt, '.txt', '.xlsx')")
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    out = ingest(app, file_name("S1").replace(".txt", ".xlsx"), ["1|1|a"])
    assert out.event_ty == "FILE_TYPE_NOT_SUPPORTED"


def test_trailer_count(conn, tmp_path):
    seed_config(conn, sources=("S1",))
    with conn.transaction():
        conn.execute("UPDATE ComplianceSourceFileConfig SET Src_File_Has_Trlr_Ind=1")
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0), trailer_count_check=True)
    create_batches(app)
    bad = ingest(app, file_name("S1"), ["1|1|a", "TRL|5"])
    assert bad.event_ty == "FILE_TRAILER_COUNT_MISMATCH"
    good = ingest(app, file_name("S1", ts=datetime(2026, 2, 1, 11, 0)), ["1|1|a", "TRL|1"])
    assert good.result == "PROMOTED"


def test_no_rule_binding_skips_file_rules(conn, tmp_path):
    seed_config(conn, sources=("S1",), rules=False)
    app, clock, rules, _ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    out = ingest(app, file_name("S1"), ["1|1|a"])
    assert out.result == "PROMOTED" and "FILE_LEVEL" not in [c["scope"] for c in rules.calls]
    assert q1(conn, "SELECT Rules_Stat FROM ComplianceFileLoad")["rules_stat"] == "NOT_RUN"


def test_failure_during_promotion_rolls_back_core_and_control(env, conn, monkeypatch):
    """Metadata and core share one database: a failure after the swap undoes both; the restart promotes once."""
    app, *_ = env
    first = ingest(app, file_name("S1", ts=datetime(2026, 2, 1, 9, 0, 0)), ["1|1|a"])
    name = file_name("S1", ts=datetime(2026, 2, 1, 10, 0, 0))
    original = app.pipeline.logger.batch_event
    calls = {"n": 0}

    def flaky(event, **kw):
        if event == "FILE_PROMOTED" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("database went away")
        return original(event, **kw)
    monkeypatch.setattr(app.pipeline.logger, "batch_event", flaky)
    with pytest.raises(RuntimeError):
        ingest(app, name, ["2|2|b", "3|3|c"])
    b = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S1' AND Run_Ty='MONTHLY'")
    assert b["current_load_id"] == first.load_id
    cur = qa(conn, "SELECT load_id FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", b["btch_id"])
    assert {r["load_id"] for r in cur} == {first.load_id}
    again = app.pipeline.process_file("inbound", "prja/in/" + name)
    assert again.result == "PROMOTED"
    assert [(r["id"], r["current_ind"], r["load_id"]) for r in core_rows(conn)] == [
        (1, 0, first.load_id), (2, 1, again.load_id), (3, 1, again.load_id)]
