from datetime import datetime

import pytest

from framework.common import TriggerBlocked
from framework import db as locks

from .helpers import create_batches, file_name, make_app, put_file, q1, qa, seed_config, utc


def setup(conn, tmp_path, **settings):
    seed_config(conn)
    app, clock, rules, connector = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0), **settings)
    create_batches(app)
    return app, clock, rules, connector


def ingest(app, src, rows=("1|1|a",), ts=datetime(2026, 2, 1, 9, 30)):
    return app.pipeline.process_file("inbound", put_file(app, file_name(src, ts=ts), list(rows)))


def extract(conn):
    return q1(conn, "SELECT * FROM ComplianceExtractControl WHERE Run_Ty='MONTHLY'")


def test_auto_trigger_after_hold_closes_batches(conn, tmp_path):
    app, clock, rules, connector = setup(conn, tmp_path)
    ingest(app, "S1")
    ingest(app, "S2")
    e = extract(conn)
    assert e["extract_stat"] == "COMPLETE" and e["extract_rules_stat"] == "PASSED"      # early combine (D-21)
    assert e["eligibility_cd"] == "NOT_ELIGIBLE" and "SLA hold" in e["eligibility_rsn_txt"]
    s = app.evaluator.run()
    assert s.triggered == [] and connector.calls == []                # still Feb 1 in Chicago
    clock.set(utc(2026, 2, 2, 6, 5))                                  # 00:05 Feb 2 Chicago
    s = app.evaluator.run()
    assert s.triggered == [e["extract_id"]]
    params = dict(connector.calls[0])
    assert params["--PERIOD_START"] == "2026-01-01" and params["--MODE"] == "full"
    assert params["--BATCHES"] == ",".join(sorted(r["btch_id"] for r in qa(conn, "SELECT Btch_ID FROM ComplianceRequestControl WHERE Run_Ty='MONTHLY'")))
    e = extract(conn)
    assert (e["trigger_stat"], e["extract_close_ind"], e["trigger_cnt"]) == ("TRIGGERED", 1, 1)
    assert params["--TRIGGER_ID"] == str(e["last_trigger_id"])
    rows = qa(conn, "SELECT * FROM ComplianceRequestControl WHERE Run_Ty='MONTHLY'")
    assert {(r["batch_close_ind"], r["req_stat"], r["closed_by_trigger_id"]) for r in rows} == {(1, "COMPLETED", e["last_trigger_id"])}
    t = q1(conn, "SELECT * FROM ComplianceExtractTrigger")
    assert t["call_stat"] == "SUCCEEDED" and t["job_run_ref"] == "jr_1" and "--MODE=full" in t["rendered_params_txt"]
    assert t["extract_job_ref"] == "extract_job"
    assert app.evaluator.run().evaluated == 0                          # nothing left to do


def test_strict_partial_blocked_until_source_waiver(conn, tmp_path):
    app, clock, rules, connector = setup(conn, tmp_path)
    ingest(app, "S1")
    clock.set(utc(2026, 2, 3, 12, 0))
    assert app.evaluator.run().not_eligible == 1
    e = extract(conn)
    assert e["eligibility_cd"] == "NOT_ELIGIBLE" and e["extract_rules_stat"] == "PASSED"
    with pytest.raises(TriggerBlocked):
        app.trigger.fire(e["extract_id"], "MANUAL", "jdoe")
    s2 = q1(conn, "SELECT Req_ID FROM ComplianceRequestControl WHERE Src_Cd='S2' AND Run_Ty='MONTHLY'")["req_id"]
    with conn.transaction():
        conn.execute("""INSERT INTO ComplianceBatchOverride (Override_Ty, Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd,
                          Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, History)
                        SELECT 'SOURCE_WAIVER', Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty,
                               Rpt_Start_Dt_Key, Rpt_End_Dt_Key, 'requested' FROM ComplianceRequestControl WHERE Req_ID=%s""", (s2,))
    app.decisions.run()
    with conn.transaction():
        conn.execute("UPDATE ComplianceBatchOverride SET Apprvl_Stat='APPROVED', Apprvd_By='boss', Apprvd_Dtts=now()")
    d = app.decisions.run()
    assert d.invalid == [] and d.processed == 1
    e = extract(conn)
    assert (e["eligibility_cd"], e["waived_src_cnt"], e["extract_stat"]) == ("MANUAL_ONLY", 1, "COMPLETE")
    assert app.evaluator.run().triggered == []                          # waivers -> manual (Q-06 default)
    out = app.trigger.fire(e["extract_id"], "MANUAL", "jdoe")
    assert out.closed_batches == 2
    b2 = q1(conn, "SELECT * FROM ComplianceRequestControl WHERE Req_ID=%s", s2)
    assert (b2["resolution_ty"], b2["req_stat"]) == ("MISSING", "DATA_NOT_PROVIDED")
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='SOURCE_MISSING_AT_CLOSE'")["n"] == 1
    # revoking the waiver after the trigger is invalid (D-48)
    with conn.transaction():
        conn.execute("UPDATE ComplianceBatchOverride SET Apprvl_Stat='REVOKED', Revoked_By='x', Revoked_Dtts=now(), "
                     "Revocation_Rsn='late'")
    assert app.decisions.run().invalid != []


def test_strict_rule_failure_and_rule_waiver(conn, tmp_path):
    app, clock, rules, connector = setup(conn, tmp_path)
    rules.period_fail = ["P_TOTALS"]
    ingest(app, "S1")
    ingest(app, "S2")
    e = extract(conn)
    assert e["extract_rules_stat"] == "FAILED" and e["failed_rule_refs"] == "P_TOTALS"
    assert "contributing batches" in q1(conn, "SELECT Description FROM CMS_ComplianceExceptionsAudit "
                                              "WHERE Event_Ty='PERIOD_RULES_FAILED'")["description"]
    clock.set(utc(2026, 2, 3, 12, 0))
    app.evaluator.run()
    assert extract(conn)["eligibility_cd"] == "NOT_ELIGIBLE"
    with conn.transaction():
        conn.execute("""INSERT INTO ComplianceBatchOverride (Override_Ty, Extract_ID, Project_Cd, Table_Nm, Run_Ty,
                          Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Rule_Ref, History, Apprvl_Stat, Apprvd_By, Apprvd_Dtts)
                        SELECT 'RULE_WAIVER', Extract_ID, Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key,
                               Rpt_End_Dt_Key, 'P_TOTALS', 'x', 'APPROVED', 'boss', now()
                          FROM ComplianceExtractControl WHERE Run_Ty='MONTHLY'""")
    app.decisions.run()
    assert extract(conn)["eligibility_cd"] == "MANUAL_ONLY"
    assert len(rules.calls) == 3          # SLA evaluation and waiver decision reused the rule result


def test_best_effort_requires_ack(conn, tmp_path):
    app, clock, rules, connector = setup(conn, tmp_path, extract_gating_mode="BEST_EFFORT")
    ingest(app, "S1")
    e = extract(conn)
    with pytest.raises(TriggerBlocked, match="SLA hold"):
        app.trigger.fire(e["extract_id"], "MANUAL", "jdoe", ack_warnings=True)
    clock.set(utc(2026, 2, 2, 12, 0))
    with pytest.raises(TriggerBlocked, match="acknowledged"):
        app.trigger.fire(e["extract_id"], "MANUAL", "jdoe")
    out = app.trigger.fire(e["extract_id"], "MANUAL", "jdoe", ack_warnings=True)
    assert out.warnings and out.closed_batches == 2
    t = q1(conn, "SELECT * FROM ComplianceExtractTrigger WHERE Call_Stat='SUCCEEDED'")
    assert t["ack_warnings_ind"] == 1 and "1 of 2" in t["warning_txt"]
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='EXTRACT_TRIGGERED_WITH_WARNINGS'")["n"] == 1
    with pytest.raises(TriggerBlocked, match="already triggered"):
        app.trigger.fire(e["extract_id"], "MANUAL", "jdoe", ack_warnings=True)


def test_best_effort_zero_data_manual(conn, tmp_path):
    app, clock, rules, connector = setup(conn, tmp_path, extract_gating_mode="BEST_EFFORT")
    clock.set(utc(2026, 2, 2, 12, 0))
    e = extract(conn)
    out = app.trigger.fire(e["extract_id"], "MANUAL", "jdoe", ack_warnings=True)
    assert out.closed_batches == 2 and any("no source" in w for w in out.warnings)


def test_call_failure_keeps_batches_open_and_retries(conn, tmp_path):
    app, clock, rules, connector = setup(conn, tmp_path)
    ingest(app, "S1")
    ingest(app, "S2")
    clock.set(utc(2026, 2, 2, 12, 0))
    connector.mode = "reject"
    s = app.evaluator.run()
    assert s.failed == [extract(conn)["extract_id"]] and len(connector.calls) == 2   # 1 + Max_Call_Retry_Cnt
    assert extract(conn)["trigger_stat"] == "FAILED"
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestControl WHERE Batch_Close_Ind=1")["n"] == 0
    assert app.evaluator.run().evaluated == 0          # failed triggers are not re-fired by default (Q-07)
    connector.mode = "reject_then_accept"
    connector.calls.clear()
    app.trigger.fire(extract(conn)["extract_id"], "MANUAL", "ops")
    assert extract(conn)["trigger_stat"] == "TRIGGERED" and len(connector.calls) == 2


def test_ambiguous_call_requires_resolution(conn, tmp_path):
    app, clock, rules, connector = setup(conn, tmp_path)
    ingest(app, "S1")
    ingest(app, "S2")
    clock.set(utc(2026, 2, 2, 12, 0))
    connector.mode = "ambiguous"
    s = app.evaluator.run()
    assert s.reconcile_required
    e = extract(conn)
    assert e["trigger_stat"] == "REQUESTED"
    with pytest.raises(TriggerBlocked, match="in flight"):
        app.trigger.fire(e["extract_id"], "MANUAL", "ops")
    clock.set(utc(2026, 2, 2, 13, 0))
    assert app.trigger.reconcile() == [e["last_trigger_id"] or q1(conn, "SELECT max(Trigger_ID) m FROM ComplianceExtractTrigger")["m"]]
    tid = q1(conn, "SELECT Trigger_ID FROM ComplianceExtractTrigger WHERE Call_Stat='REQUESTED'")["trigger_id"]
    app.trigger.resolve(tid, True, "ops", "jr_manual")
    e = extract(conn)
    assert e["trigger_stat"] == "TRIGGERED" and e["extract_close_ind"] == 1


def test_trigger_deferred_when_batch_locked(conn, tmp_path):
    app, clock, rules, connector = setup(conn, tmp_path)
    ingest(app, "S1")
    ingest(app, "S2")
    clock.set(utc(2026, 2, 2, 12, 0))
    from framework.db import connect
    import os
    other = connect(os.environ["TEST_DATABASE_URL"])
    try:
        btch = q1(conn, "SELECT Btch_ID FROM ComplianceRequestControl WHERE Src_Cd='S1' AND Run_Ty='MONTHLY'")["btch_id"]
        assert locks.try_lock(other, locks.batch_key(btch))
        s = app.evaluator.run()
        assert s.deferred and connector.calls == []
        assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty='BATCH_CLOSE_DEFERRED_LOCKED'")["n"] == 1
    finally:
        other.close()
    assert app.evaluator.run().triggered


def test_period_rules_technical_error(conn, tmp_path):
    app, clock, rules, connector = setup(conn, tmp_path)
    rules.error.add("PERIOD_LEVEL")
    ingest(app, "S1")
    ingest(app, "S2")
    assert extract(conn)["extract_rules_stat"] == "ERROR"
    clock.set(utc(2026, 2, 2, 12, 0))
    assert app.evaluator.run().not_eligible == 1
    rules.error.clear()
    assert app.evaluator.run().triggered            # ERROR forces a fresh combine


def test_sweep_scope_and_missing_job_config(conn, tmp_path):
    from framework.common import ConfigError

    app, clock, rules, connector = setup(conn, tmp_path)
    ingest(app, "S1")
    ingest(app, "S2")
    clock.set(utc(2026, 2, 2, 12, 0))
    assert app.evaluator.run(project_cd="OTHER").evaluated == 0          # another project's job
    app.settings.extract_job_name = None
    with pytest.raises(ConfigError, match="EXTRACT_JOB_NAME"):
        app.evaluator.run(project_cd="PRJA")
    app.settings.extract_job_name = "extract_job"
    assert app.evaluator.run(project_cd="PRJA", run_ty="MONTHLY").triggered
