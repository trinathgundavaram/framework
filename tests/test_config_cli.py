import json
import os

from framework.adapters import CallableRuleEngine, LogChannel, build_rule_engine
from framework.cli import main
from framework.config import RuleBinding, validate_all
from framework.settings import Settings

from .helpers import bind, create_batches, file_name, make_app, put_file, q1, qa, seed_config, utc


def codes(issues, severity="ERROR"):
    return sorted({i.code for i in issues if i.severity == severity})


def test_clean_config_is_valid(conn):
    seed_config(conn)
    issues = validate_all(conn, True)
    assert codes(issues) == [] and codes(issues, "WARNING") == []


def test_validator_detects_problems(conn):
    seed_config(conn)
    with conn.transaction():
        conn.execute("DROP TABLE core_t.tbl_x")
        conn.execute("UPDATE ComplianceSourceFileConfig SET Src_File_Nm_Tmplt='{PROJECT}_{TABLE}.txt' WHERE Src_ID='S2'")
        conn.execute("UPDATE ComplianceSourceFileConfig SET Src_File_Archive_Path='/tmp/q' WHERE Src_ID='S1'")
        conn.execute("""INSERT INTO ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty,
                        Effective_Start_Dt_Key, Cmplnc_Vrsn) VALUES ('PRJA','tbl_x','S2','MONTHLY','2025-06-01','V2')""")
        conn.execute("""INSERT INTO ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days)
                        VALUES ('BAD-1','bad','WEEKLY',0)""")
        conn.execute("UPDATE ComplianceDataSetSourceXwalk SET Active_Ind=0 WHERE Src_ID='S1'")
        conn.execute("INSERT INTO ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty) VALUES ('S3','s3','VENDOR')")
        conn.execute("""INSERT INTO ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key,
                        Cmplnc_Vrsn) VALUES ('PRJA','tbl_x','S3','MONTHLY','2025-01-01','V1')""")
        conn.execute("UPDATE ComplianceRunType SET Active_Ind=0 WHERE Run_Ty='ADHOC'")
    issues = validate_all(conn)
    for c in ("TEMPLATE", "TARGET_TABLE", "PATH", "FILE_CONFIG_NO_XWALK", "XWALK_NO_FILE_CONFIG", "XWALK_OVERLAP",
              "RUN_TYPE"):
        assert c in codes(issues), (c, codes(issues))
    assert "XWALK_RUN_TYPE" in codes(issues, "WARNING")


def test_template_overlap_detected(conn):
    seed_config(conn)
    with conn.transaction():
        # S2 names look like PRJA_TBLX_<RUNTY>_MONTHLY_... so an S1 monthly name also matches S2 (RUNTY='S1')
        conn.execute("UPDATE ComplianceSourceFileConfig "
                     "SET Src_File_Nm_Tmplt='PRJA_TBLX_{RUNTY}_MONTHLY_{RPTSTART}_{RPTEND}_{TS}.txt' WHERE Src_ID='S2'")
    assert "TEMPLATE_OVERLAP" in codes(validate_all(conn))


def test_notifications_sent_once(conn, tmp_path):
    seed_config(conn)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    app.pipeline.process_file("inbound", put_file(app, "junk.txt", ["x"]))
    app.pipeline.process_file("inbound", put_file(app, file_name("S1"), ["1|2"]))
    ch = LogChannel()
    assert app.notifier(ch).run() == 2
    assert app.notifier(ch).run() == 0
    unmatched, matched = ch.sent
    assert unmatched.recipients == [] and "FILE_REJECTED_UNPARSEABLE" in unmatched.subject
    assert matched.recipients == ["ops@example.com"] and "1|2" not in matched.body


def test_cli_end_to_end(conn, tmp_path, monkeypatch, capsys):
    from .conftest import SCHEMA
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"FRAMEWORK_DB_DSN={os.environ['TEST_DATABASE_URL']}\n"
                                   f"FRAMEWORK_METADATA_SCHEMA={SCHEMA}\nFRAMEWORK_OBJECT_STORE=local\n"
                                   f"FRAMEWORK_LOCAL_STORE_ROOT={tmp_path / 'store'}\n")
    monkeypatch.setenv("FRAMEWORK_RULE_ENGINE", "none")
    assert main(["init-db"]) == 0                     # idempotent re-run on an initialised schema
    seed_config(conn)
    assert main(["validate-config"]) == 0
    assert main(["test-connection"]) == 0
    capsys.readouterr()
    assert main(["show-config", "--set", "file_rules_mode=ANNOTATE"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["settings"]["rule_engine"] == {"value": "none", "source": "env"}
    assert shown["settings"]["object_store"] == {"value": "local", "source": ".env"}
    assert shown["settings"]["file_rules_mode"] == {"value": "ANNOTATE", "source": "argument"}
    assert "password" not in shown["database"] and shown["database"]["schema"] == SCHEMA
    assert main(["run", "--module", "BATCH_CREATION", "--project", "PRJA", "--run-type", "MONTHLY",
                 "--period", "PREV_CALENDAR_MONTH", "--as-of", "2026-02-01T13:00:00+00:00"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["scheduled"]["created"] == 2
    assert main(["run", "--module", "BATCH_CREATION", "--project", "PRJA", "--run-type", "MONTHLY",
                 "--period", "NOPE"]) == 2
    app, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    key = put_file(app, file_name("S1"), ["1|1|a"])
    capsys.readouterr()
    assert main(["run", "--module", "FILE_LOAD", "--bucket", "inbound", "--key", key]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["result"] == "PROMOTED"
    s2 = q1(conn, "SELECT Btch_ID FROM ComplianceRequestControl WHERE Src_ID='S2'")["btch_id"]
    feb1, feb3 = ["--as-of", "2026-02-01T13:00:00+00:00"], ["--as-of", "2026-02-03T13:00:00+00:00"]
    assert main(["close-batch", "--btch-id", s2, "--closed-by", "me", *feb1]) == 2   # SLA hold -> exit 2
    capsys.readouterr()
    assert main(["close-batches", "--project", "PRJA", *feb1]) == 0                # still inside the SLA hold
    assert json.loads(capsys.readouterr().out)["evaluated"] == 0
    assert main(["close-batches", "--project", "PRJA", *feb3]) == 0                # S1 has data: closed
    out = json.loads(capsys.readouterr().out)
    assert (len(out["closed"]), out["waiting"]) == (1, [s2])                       # S2 has none: a person decides
    assert main(["close-batch", "--btch-id", s2, "--closed-by", "me", *feb3]) == 0
    assert json.loads(capsys.readouterr().out)["req_stat"] == "DATA_NOT_PROVIDED"
    assert main(["health"]) == 0                        # composed from ingest/overrides/batch close (§15.3)
    assert set(json.loads(capsys.readouterr().out)) == {
        "stale_loads", "quarantine_by_reason", "pending_reviews", "overrides_expiring_soon",
        "batches_past_hold_not_closed"}
    assert main(["process-decisions"]) == 0
    assert main(["run", "--module", "BATCH_CREATION", "--project", "PRJA"]) == 0  # ad-hoc sweep only
    assert main(["notify"]) == 0
    assert main(["show-config", "--set", "NOT_A_SETTING=1"]) == 2


def test_cli_file_load_path(conn, tmp_path, monkeypatch, capsys):
    """`run --module FILE_LOAD` with no `--key`: one CLI call picks up files for two different sources -
    two configs, two batches - sitting at the same configured inbound location."""
    from .conftest import SCHEMA
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"FRAMEWORK_DB_DSN={os.environ['TEST_DATABASE_URL']}\n"
                                   f"FRAMEWORK_METADATA_SCHEMA={SCHEMA}\nFRAMEWORK_OBJECT_STORE=local\n"
                                   f"FRAMEWORK_LOCAL_STORE_ROOT={tmp_path / 'store'}\n")
    monkeypatch.setenv("FRAMEWORK_RULE_ENGINE", "none")
    assert main(["init-db"]) == 0
    seed_config(conn)
    assert main(["run", "--module", "BATCH_CREATION", "--project", "PRJA", "--run-type", "MONTHLY",
                 "--period", "PREV_CALENDAR_MONTH", "--as-of", "2026-02-01T13:00:00+00:00"]) == 0
    app, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    put_file(app, file_name("S1"), ["1|1|a"])
    put_file(app, file_name("S2"), ["2|2|b"])

    capsys.readouterr()
    assert main(["run", "--module", "FILE_LOAD"]) == 0        # no --bucket/--prefix: every configured location
    out = json.loads(capsys.readouterr().out)["result"]
    assert (out["scanned"], out["promoted"], out["errors"]) == (2, 2, [])
    assert out["locations"] == ["inbound/prja/in/"]
    assert {r["req_stat"] for r in qa(conn, "SELECT Req_Stat FROM ComplianceRequestControl")} == {"PROMOTED"}

    capsys.readouterr()                                       # nothing left to pick up
    assert main(["run", "--module", "FILE_LOAD", "--bucket", "inbound", "--prefix", "prja/in/"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["scanned"] == 0

    assert main(["run", "--module", "FILE_LOAD", "--bucket", "inbound"]) == 2   # --bucket without --prefix


def test_rule_engine_adapter_contract(conn):
    b = [RuleBinding("P", "T", "S", "*", "g", "v")]
    seen = []
    ok = CallableRuleEngine(lambda c, g, v, p: seen.append((c, g, v)) or [{"rule_ref": "R1", "passed": True}])
    assert ok.run(conn, b, {}, "GATE").status == "PASSED" and seen == [(conn, "g", "v")]
    bad = CallableRuleEngine(lambda c, g, v, p: [{"rule_ref": "R1", "passed": False}])
    assert bad.run(conn, b, {}, "GATE").failed_rules == ["R1"]
    assert bad.run(conn, b, {}, "ANNOTATE").status == "PASSED_WITH_WARNINGS"
    assert CallableRuleEngine(lambda *a: [{"oops": 1}]).run(conn, b, {}, "GATE").status == "ERROR"
    missing = build_rule_engine(Settings())                                       # Q-12 not configured
    assert missing.run(conn, b, {}, "GATE").status == "ERROR"
    assert missing.run(conn, [], {}, "GATE").status == "PASSED"
    assert build_rule_engine(Settings(rule_engine="none")).run(conn, b, {}, "GATE").status == "PASSED"


# ---------------------------------------------------------------- rule binding levels (additive)
def rules_for(conn, src, run_ty):
    from framework.config import rule_bindings
    return [f"{b.gre_rule_group}:{b.gre_rule_variant}" for b in rule_bindings(conn, "PRJA", "tbl_x", src, run_ty)]


def test_rule_bindings_apply_at_every_level_and_add_up(conn):
    seed_config(conn, rules=False)
    with conn.transaction():
        bind(conn, "g", "project", table="*")                                  # every table of the project
        bind(conn, "g", "table")                                               # every source of tbl_x
        bind(conn, "g", "adhoc_only", run_ty="ADHOC")                          # tbl_x, ADHOC runs only
        bind(conn, "g", "s1_only", src="S1")                                   # one source
        bind(conn, "g", "table", src="S1")                                     # same rule at two levels: runs once
        bind(conn, "g", "other_table", table="tbl_other")                      # a different table
        conn.execute("UPDATE ComplianceRuleBinding SET Active_Ind=0 WHERE Gre_Rule_Variant='other_table'")
    assert rules_for(conn, "S1", "MONTHLY") == ["g:project", "g:s1_only", "g:table"]
    assert rules_for(conn, "S2", "MONTHLY") == ["g:project", "g:table"]
    assert rules_for(conn, "S2", "ADHOC") == ["g:adhoc_only", "g:project", "g:table"]


def test_table_level_rules_reach_the_rules_engine(conn, tmp_path):
    """Universe-style: a rule bound once at project/table level runs for every source's file."""
    seed_config(conn, rules=False)
    with conn.transaction():
        conn.execute("DELETE FROM ComplianceRuleBinding")
        bind(conn, "u_file", "all_sources")
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    for src in ("S1", "S2"):
        assert app.pipeline.process_file("inbound", put_file(app, file_name(src), ["1|1|a"])).result == "PROMOTED"
    assert [(c["src_id"], c["rules"]) for c in rules.calls] == [("S1", ["u_file:all_sources"]),
                                                               ("S2", ["u_file:all_sources"])]


def test_validator_checks_rule_bindings(conn):
    seed_config(conn)
    assert codes(validate_all(conn)) == []
    with conn.transaction():
        bind(conn, "g", "typo", run_ty="WEEKLY")                               # no such run type in the crosswalk
        bind(conn, "g", "typo", table="tbl_nope")                              # no such table
        bind(conn, "g", "typo", src="S9")                                      # no such source
    issues = validate_all(conn)
    assert codes(issues) == ["RULE_BINDING_NO_XWALK"] and len(issues) == 3
