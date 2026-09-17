from datetime import date, datetime

import pytest

from framework.common import TechnicalFailure

from .helpers import add_override, create_batches, file_name, make_app, put_file, q1, qa, seed_config, utc


@pytest.fixture
def env(conn, tmp_path):
    seed_config(conn)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    return app, clock, rules


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
    assert (b["resolution_ty"], b["req_stat"]) == ("NEW_FILE", "PROMOTED")
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
    assert q1(conn, "SELECT count(*) n FROM ComplianceFileLoad WHERE Load_Stat='PROMOTED'")["n"] == 1
    staged = qa(conn, "SELECT load_id FROM stg_t.tbl_x")
    assert {r["load_id"] for r in staged} == {second.load_id}    # D-05 delete by Btch_ID


def test_o3_o4_rules_failures(env, conn):
    app, clock, rules = env
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
    assert (b["req_stat"], b["resolution_ty"]) == ("EXCEPTION_PENDING", "NEW_FILE")
    assert q1(conn, "SELECT Load_ID FROM ComplianceFileLoad WHERE Load_Stat='PROMOTED'")["load_id"] == good.load_id
    assert [r["id"] for r in core_rows(conn) if r["current_ind"] == 1] == [1]       # D-46
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='RULES_VALIDATION_FAILED'")["n"] == 2
    assert rules.calls[0]["mode"] == "GATE" and rules.calls[0]["btch_id"] == b["btch_id"]


def test_annotate_warnings_promote(conn, tmp_path):
    seed_config(conn)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0), file_rules_mode="ANNOTATE")
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
    app, clock, rules = env
    ingest(app, file_name("S1"), ["1|1|a"])
    dup = ingest(app, file_name("S1", ts=datetime(2026, 2, 1, 11, 0)), ["1|1|a"])
    assert (dup.result, dup.event_ty) == ("QUARANTINED", "FILE_REJECTED_DUPLICATE")
    other = ingest(app, file_name("S2"), ["1|1|a"])          # same bytes, other batch -> allowed (D-52)
    assert other.result == "PROMOTED"
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='FILE_SAME_CONTENT_OTHER_BATCH'")["n"] == 1


def test_replay_and_technical_failure_restart(env, conn):
    app, clock, rules = env
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
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
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
    assert q1(conn, "SELECT Load_ID FROM ComplianceFileLoad WHERE Load_Stat='PROMOTED'")["load_id"] == first.load_id
    cur = qa(conn, "SELECT load_id FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", b["btch_id"])
    assert {r["load_id"] for r in cur} == {first.load_id}
    again = app.pipeline.process_file("inbound", "prja/in/" + name)
    assert again.result == "PROMOTED"
    assert [(r["id"], r["current_ind"], r["load_id"]) for r in core_rows(conn)] == [
        (1, 0, first.load_id), (2, 1, again.load_id), (3, 1, again.load_id)]
    assert q1(conn, "SELECT Load_ID FROM ComplianceFileLoad WHERE Load_Stat='PROMOTED'")["load_id"] == again.load_id


def test_file_for_a_closed_batch_needs_an_approved_override(conn, tmp_path):
    """A closed batch only accepts a file when an approved, still-valid override says so (D-74)."""
    seed_config(conn)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0), extract_gating_mode="BEST_EFFORT")
    create_batches(app)
    ingest(app, file_name("S1"), ["1|1|a"])
    clock.set(utc(2026, 2, 2, 12, 0))
    ext = q1(conn, "SELECT Extract_ID FROM ComplianceExtractControl")["extract_id"]
    app.control.close(ext, "ops", ack_warnings=True)
    s2 = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S2'")
    assert (s2["batch_close_ind"], s2["req_stat"], s2["resolution_ty"]) == (1, "DATA_NOT_PROVIDED", "MISSING")

    clock.set(utc(2026, 2, 5, 12, 0))
    blocked_name = file_name("S2", ts=datetime(2026, 2, 5, 8, 0))
    blocked = ingest(app, blocked_name, ["5|5|e"])
    assert (blocked.result, blocked.event_ty) == ("QUARANTINED", "FILE_REJECTED_BATCH_CLOSED")
    assert not app.store.exists("inbound", "prja/in/" + blocked_name)

    add_override(conn, s2["req_id"], "LATE_ARRIVAL", date(2026, 2, 10))
    # the same object delivered again is reprocessed, because that quarantine reason is not final
    key = put_file(app, blocked_name, ["5|5|e"])
    retry = app.pipeline.process_file("inbound", key)
    assert (retry.result, retry.rule, retry.load_id) == ("LATE_PROMOTED", "C-1", blocked.load_id)
    # once that batch has data, a further file needs the CORRECTION type instead (D-74)
    again = ingest(app, file_name("S2", ts=datetime(2026, 2, 5, 9, 0)), ["6|6|f"])
    assert (again.result, again.event_ty) == ("QUARANTINED", "FILE_REJECTED_BATCH_CLOSED")
    b = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s", s2["req_id"])
    assert (b["batch_close_ind"], b["req_stat"], b["resolution_ty"]) == (1, "COMPLETED", "NEW_FILE")
    assert qa(conn, "SELECT id FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", b["btch_id"]) == [{"id": 5}]
    e = q1(conn, "SELECT * FROM ComplianceExtractControl")
    assert e["regenerate_required_ind"] == 1 and e["received_src_cnt"] == 2
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit "
                    "WHERE Event_Ty='EXTRACT_REGENERATE_REQUIRED'")["n"] == 1


def test_correction_needs_its_own_override_type_and_expires(env, conn):
    app, clock, rules = env
    first = ingest(app, file_name("S1"), ["1|1|a"])
    ingest(app, file_name("S2"), ["2|2|b"])
    clock.set(utc(2026, 2, 2, 12, 0))
    ext = q1(conn, "SELECT Extract_ID FROM ComplianceExtractControl")["extract_id"]
    app.control.close(ext, "SYSTEM", automatic=True)
    s1 = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S1'")

    clock.set(utc(2026, 2, 4, 12, 0))
    add_override(conn, s1["req_id"], "LATE_ARRIVAL", date(2026, 2, 10))       # wrong type: the batch has data
    wrong = ingest(app, file_name("S1", ts=datetime(2026, 2, 4, 8, 0)), ["9|9|z"])
    assert (wrong.result, wrong.event_ty) == ("QUARANTINED", "FILE_REJECTED_BATCH_CLOSED")
    with conn.transaction():                                                  # make room for the right type
        conn.execute("UPDATE ComplianceBatchOverride SET Apprvl_Stat='REJECTED', Rejected_By='ops', "
                     "Rejected_Dtts=now() WHERE Req_ID=%s", (s1["req_id"],))
    add_override(conn, s1["req_id"], "CORRECTION", date(2026, 2, 5))
    ok = ingest(app, file_name("S1", ts=datetime(2026, 2, 4, 9, 0)), ["1|100|a"])
    assert (ok.result, ok.rule) == ("CORRECTION_PROMOTED", "C-2")
    cur = qa(conn, "SELECT id, amount FROM core_t.tbl_x WHERE btch_id=%s AND current_ind=1", s1["btch_id"])
    assert [(r["id"], int(r["amount"])) for r in cur] == [(1, 100)]
    assert q1(conn, "SELECT Load_Stat FROM ComplianceFileLoad WHERE Load_ID=%s", first.load_id)["load_stat"] == "SUPERSEDED"
    assert q1(conn, "SELECT Req_Stat FROM ComplianceRequestControl WHERE Req_ID=%s", s1["req_id"])["req_stat"] == "COMPLETED"

    clock.set(utc(2026, 2, 6, 12, 0))                                          # override has run out
    late = ingest(app, file_name("S1", ts=datetime(2026, 2, 6, 9, 0)), ["1|200|a"])
    assert (late.result, late.event_ty) == ("QUARANTINED", "FILE_REJECTED_BATCH_CLOSED")


def test_process_path_scans_one_location_multiple_configs_and_batches(env, conn):
    """Two files for two different sources - different configs, different batches - sitting in the
    same inbound location are both picked up and promoted by one process_path() call. Each file is
    still resolved to exactly one config and one batch (D-26), same as a separate ingest-file each."""
    app, *_ = env
    put_file(app, file_name("S1"), ["1|1|a"])
    put_file(app, file_name("S2"), ["2|2|b"])
    summary = app.pipeline.process_path("inbound", "prja/in/")
    assert (summary.scanned, summary.promoted, summary.errors) == (2, 2, [])
    assert {o.result for o in summary.outcomes} == {"PROMOTED"}
    s1 = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S1'")
    s2 = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Src_Cd='S2'")
    assert s1["req_stat"] == "PROMOTED" and s2["req_stat"] == "PROMOTED"
    assert s1["btch_id"] != s2["btch_id"]                     # separate batches, one file each (D-26, D-33)


def test_process_path_defaults_to_every_configured_location(env, conn):
    """With no bucket/prefix, every active file config's inbound location is scanned (D-27)."""
    app, *_ = env
    put_file(app, file_name("S1"), ["1|1|a"])
    put_file(app, file_name("S2"), ["2|2|b"])
    summary = app.pipeline.process_path()
    assert summary.locations == ["inbound/prja/in/"]           # S1 and S2 share one configured folder
    assert (summary.scanned, summary.promoted) == (2, 2)


def test_process_path_mixed_outcomes_keep_going(env, conn):
    """A quarantined file does not stop a sibling file in the same sweep from being promoted."""
    app, *_ = env
    put_file(app, "junk.txt", ["x"])                           # matches no template
    put_file(app, file_name("S1"), ["1|1|a"])
    summary = app.pipeline.process_path("inbound", "prja/in/")
    assert (summary.scanned, summary.promoted, summary.quarantined) == (2, 1, 1)
    assert sorted(o.result for o in summary.outcomes) == ["PROMOTED", "QUARANTINED"]


def test_process_path_requires_bucket_and_prefix_together(env):
    app, *_ = env
    with pytest.raises(ValueError):
        app.pipeline.process_path("inbound", None)
    with pytest.raises(ValueError):
        app.pipeline.process_path(None, "prja/in/")


def test_process_path_empty_location_is_a_noop(env):
    app, *_ = env
    summary = app.pipeline.process_path("inbound", "prja/nothing-here/")
    assert (summary.scanned, summary.outcomes) == (0, [])


def test_process_path_records_a_listing_error_and_continues(env, conn):
    """A location that cannot be listed (here: a path escaping the local store root) is recorded as
    an error rather than raising, so a problem with one configured location does not sink the sweep."""
    app, *_ = env
    put_file(app, file_name("S1"), ["1|1|a"])
    summary = app.pipeline.process_path("inbound", "../../evil/")
    assert summary.scanned == 0 and summary.outcomes == []
    assert len(summary.errors) == 1 and "ValueError" in summary.errors[0]
    # the good location is untouched - the file is still sitting there, unprocessed
    assert q1(conn, "SELECT count(*) n FROM ComplianceFileLoad")["n"] == 0


def test_process_path_records_a_technical_failure_and_continues(env, conn):
    """A file that fails technically (here: the rules engine errors) is recorded in `errors` and the
    sweep still goes on to the next object instead of stopping."""
    app, clock, rules = env
    rules.error.add("FILE_LEVEL")
    put_file(app, file_name("S1"), ["1|1|a"])
    put_file(app, file_name("S2"), ["2|2|b"])
    summary = app.pipeline.process_path("inbound", "prja/in/")
    assert summary.scanned == 2 and summary.promoted == 0
    assert len(summary.errors) == 2 and all("TechnicalFailure" in e for e in summary.errors)
    assert {r["load_stat"] for r in qa(conn, "SELECT Load_Stat FROM ComplianceFileLoad")} == {"FAILED_TECHNICAL"}


def test_file_matches_the_open_batch_of_the_latest_run_date(conn, tmp_path):
    """Daily runs of one period: an arriving file belongs to the open batch with the latest run date (D-78)."""
    seed_config(conn, sources=("S1",))
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 2, 13, 0))
    create_batches(app, period="CURRENT_CALENDAR_MONTH")
    clock.set(utc(2026, 2, 3, 13, 0))
    create_batches(app, period="CURRENT_CALENDAR_MONTH")
    name = file_name("S1", start=date(2026, 2, 1), end=date(2026, 2, 28), ts=datetime(2026, 2, 3, 9, 0))
    out = app.pipeline.process_file("inbound", put_file(app, name, ["3|3|c"]))
    assert out.result == "PROMOTED"
    b = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s", out.req_id)
    assert b["req_dt_key"] == date(2026, 2, 3)
    assert q1(conn, "SELECT Req_Stat FROM ComplianceRequestControl WHERE Req_Dt_Key='2026-02-02'")["req_stat"] == "PENDING"
