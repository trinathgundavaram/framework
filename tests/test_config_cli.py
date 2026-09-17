import json
import os

from framework.adapters import CallableRuleEngine, LogChannel, build_rule_engine
from framework.cli import main
from framework.config import RuleBinding, validate_all
from framework.settings import Settings

from .helpers import create_batches, file_name, make_app, put_file, q1, seed_config, utc


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
        conn.execute("UPDATE ComplianceSourceFileConfig SET Src_File_Nm_Tmplt='{PROJECT}_{TABLE}.txt' WHERE Src_Cd='S2'")
        conn.execute("UPDATE ComplianceSourceFileConfig SET S3_Quarantine_Path='/tmp/q' WHERE Src_Cd='S1'")
        conn.execute("DELETE FROM ComplianceEventType WHERE Event_Ty='BATCH_CLOSED'")
        conn.execute("UPDATE ComplianceDataSetSourceXwalk SET Active_Ind=0 WHERE Src_Cd='S1'")
        conn.execute("INSERT INTO ComplianceSourceSystem (Src_Cd, Src_Nm, Src_Ty) VALUES ('S3','s3','VENDOR')")
        conn.execute("""INSERT INTO ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Effective_Start_Dt,
                        Cmplnc_Vrsn) VALUES ('PRJA','tbl_x','S3','MONTHLY','2025-01-01','V1')""")
        conn.execute("UPDATE ComplianceRunType SET Active_Ind=0 WHERE Run_Ty='ADHOC'")
    issues = validate_all(conn)
    for c in ("TEMPLATE", "TARGET_TABLE", "PATH", "EVENT_TYPE_MISSING", "FILE_CONFIG_NO_XWALK", "XWALK_NO_FILE_CONFIG"):
        assert c in codes(issues), (c, codes(issues))
    assert "XWALK_RUN_TYPE" in codes(issues, "WARNING")


def test_template_overlap_detected(conn):
    seed_config(conn)
    with conn.transaction():
        # S2 names look like PRJA_TBLX_<RUNTY>_MONTHLY_... so an S1 monthly name also matches S2 (RUNTY='S1')
        conn.execute("UPDATE ComplianceSourceFileConfig SET Src_Alias='MONTHLY', "
                     "Src_File_Nm_Tmplt='{PROJECT}_{TABLE}_{RUNTY}_{SRC}_{RPTSTART}_{RPTEND}_{TS}.txt' WHERE Src_Cd='S2'")
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
    assert unmatched[1].recipients == [] and "FILE_REJECTED_UNPARSEABLE" in unmatched[1].subject
    assert matched[1].recipients == ["ops@example.com"] and "1|2" not in matched[1].body


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
    assert main(["show-config", "--set", "extract_gating_mode=BEST_EFFORT"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["settings"]["rule_engine"] == {"value": "none", "source": "env"}
    assert shown["settings"]["object_store"] == {"value": "local", "source": ".env"}
    assert shown["settings"]["extract_gating_mode"]["source"] == "argument"
    assert "password" not in shown["database"] and shown["database"]["schema"] == SCHEMA
    assert main(["create-batches", "--project", "PRJA", "--run-type", "MONTHLY", "--period", "PREV_CALENDAR_MONTH",
                 "--as-of", "2026-02-01T13:00:00+00:00"]) == 0
    assert json.loads(capsys.readouterr().out)["created"] == 2
    assert main(["create-batches", "--project", "PRJA", "--run-type", "MONTHLY", "--period", "NOPE"]) == 2
    app, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    key = put_file(app, file_name("S1"), ["1|1|a"])
    capsys.readouterr()
    assert main(["ingest-file", "--bucket", "inbound", "--key", key]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "PROMOTED"
    ext = q1(conn, "SELECT Extract_ID FROM ComplianceExtractControl")["extract_id"]
    capsys.readouterr()
    assert main(["close-extract", "--extract-id", str(ext), "--closed-by", "me"]) == 2     # not eligible -> exit 2
    assert main(["refresh-extract", "--extract-id", str(ext)]) == 0
    assert main(["evaluate-extracts", "--project", "PRJA"]) == 0                  # still inside the SLA hold
    capsys.readouterr()
    assert main(["close-extract", "--extract-id", str(ext), "--closed-by", "me", "--ack-warnings",
                 "--as-of", "2026-02-03T13:00:00+00:00",
                 "--set", "extract_gating_mode=BEST_EFFORT"]) == 0
    assert json.loads(capsys.readouterr().out)["closed_batches"] == 2
    assert main(["health"]) == 0
    assert main(["process-decisions"]) == 0
    assert main(["process-intake"]) == 0
    assert main(["notify"]) == 0
    assert main(["show-config", "--set", "NOT_A_SETTING=1"]) == 2


def test_rule_engine_adapter_contract(conn):
    b = [RuleBinding("P", "T", "S", "FILE_LEVEL", "g", "v")]
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
