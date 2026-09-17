import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from framework.batches.scheduler import run_scheduler
from framework.cli import main
from framework.config.models import ExtractPolicy
from framework.config.validator import validate_all
from framework.extract.connectors.glue_job import GlueJobConnector
from framework.extract.connectors.http_api import HttpApiConnector
from framework.notify.notifier import LogChannel

from .helpers import file_name, make_app, put_file, q1, seed_config, utc


def codes(issues, severity="ERROR"):
    return sorted({i.code for i in issues if i.severity == severity})


def test_clean_config_is_valid(conn, tmp_path):
    seed_config(conn)
    app, *_ = make_app(conn, tmp_path, utc(2026, 2, 1))
    issues = validate_all(conn, True, app.conns)
    assert codes(issues) == [] and codes(issues, "WARNING") == []


def test_validator_connection_and_setting_checks(conn, data, tmp_path):
    seed_config(conn)
    app, *_ = make_app(conn, tmp_path, utc(2026, 2, 1))
    with data.transaction():
        data.execute("DROP TABLE core_t.tbl_x")
    with conn.transaction():
        conn.execute("UPDATE ComplianceFrameworkSetting SET Setting_Val='maybe' WHERE Setting_Nm='EMPTY_AS_NULL'")
        conn.execute("INSERT INTO ComplianceFrameworkSetting (Setting_Nm, Setting_Val) VALUES ('NOT_A_SETTING','1')")
        conn.execute("INSERT INTO ComplianceDbConnection (Connection_Nm, Host) VALUES ('NODB', 'h')")  # no db name
    issues = validate_all(conn, True, app.conns)
    assert {"TARGET_TABLE", "SETTING_VALUE", "CONNECTION"} <= set(codes(issues))
    assert "SETTING_UNKNOWN" in codes(issues, "WARNING")
    with conn.transaction():
        conn.execute("UPDATE ComplianceSourceFileConfig SET Target_Connection_Nm='NODB' WHERE Src_Cd='S2'")
    assert "TARGET_CONNECTION_MISMATCH" in codes(validate_all(conn, True, app.conns))


def test_validator_detects_problems(conn):
    seed_config(conn)
    with conn.transaction():
        conn.execute("UPDATE ComplianceSourceFileConfig SET Src_File_Nm_Tmplt='{PROJECT}_{TABLE}.txt' WHERE Src_Cd='S2'")
        conn.execute("UPDATE ComplianceDataSetSourceXwalk SET Schedule_Cron_Expr='not a cron' WHERE Src_Cd='S1' AND Run_Ty='MONTHLY'")
        conn.execute("UPDATE ComplianceDataSetSourceXwalk SET Period_Strategy_Cd='SAME_DAY' WHERE Src_Cd='S2' AND Run_Ty='MONTHLY'")
        conn.execute("DELETE FROM ComplianceExtractJobParam WHERE Run_Ty='ADHOC'")
        conn.execute("DELETE FROM ComplianceExtractPolicy WHERE Run_Ty='ADHOC'")
        conn.execute("INSERT INTO ComplianceExtractJobParam VALUES ('PRJA','tbl_x','MONTHLY','--X','EXTRACT_ATTR','nope',9)")
        conn.execute("UPDATE ComplianceRequestStatus SET Active_Ind=0 WHERE Abstract_State='S_COMPLETE'")
    got = codes(validate_all(conn))
    for c in ("TEMPLATE", "XWALK_CRON", "XWALK_PERIOD_MISMATCH", "XWALK_NO_POLICY", "JOB_PARAM", "STATUS_MODEL"):
        assert c in got, (c, got)


def test_template_overlap_detected(conn):
    seed_config(conn)
    with conn.transaction():
        # S2 names look like PRJA_TBLX_<RUNTY>_MONTHLY_... so an S1 monthly name also matches S2 (RUNTY='S1')
        conn.execute("UPDATE ComplianceSourceFileConfig SET Src_Alias='MONTHLY', "
                     "Src_File_Nm_Tmplt='{PROJECT}_{TABLE}_{RUNTY}_{SRC}_{RPTSTART}_{RPTEND}_{TS}.txt' WHERE Src_Cd='S2'")
    assert "TEMPLATE_OVERLAP" in codes(validate_all(conn))


def test_rules_binding_missing_is_warning(conn):
    seed_config(conn)
    with conn.transaction():
        conn.execute("DELETE FROM ComplianceRuleBinding WHERE Src_Cd='S1'")
    issues = validate_all(conn)
    assert codes(issues) == [] and "RULES_BINDING" in codes(issues, "WARNING")


def test_notifications_sent_once(conn, tmp_path):
    seed_config(conn)
    app, clock, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    run_scheduler(conn, clock, app.settings)
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
    monkeypatch.chdir(tmp_path)                       # no stray ./framework.ini
    monkeypatch.setenv("FRAMEWORK_DB_DSN", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("FRAMEWORK_METADATA_SCHEMA", SCHEMA)
    monkeypatch.setenv("FRAMEWORK_OBJECT_STORE", "local")
    monkeypatch.setenv("FRAMEWORK_LOCAL_STORE_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("FRAMEWORK_RULE_ENGINE", "none")
    assert main(["init-db"]) == 0                     # idempotent re-run on an initialised schema
    seed_config(conn)
    assert main(["validate-config"]) == 0
    assert main(["test-connections"]) == 0
    assert main(["show-config"]) == 0
    shown = json.loads(capsys.readouterr().out.split("\n]\n", 1)[1])
    assert shown["settings"]["rule_engine"] == {"value": "none", "source": "env"}
    assert shown["settings"]["object_store"]["source"] == "env"
    assert shown["settings"]["lock_timeout_seconds"]["source"] == "metadata"
    assert main(["create-batches", "--as-of", "2026-02-01T13:00:00+00:00"]) == 0
    capsys.readouterr()
    app, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    key = put_file(app, file_name("S1"), ["1|1|a"])
    assert main(["ingest-file", "--bucket", "inbound", "--key", key]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "PROMOTED"
    ext = q1(conn, "SELECT Extract_ID FROM ComplianceExtractControl")["extract_id"]
    assert main(["trigger-extract", "--extract-id", str(ext), "--requested-by", "me"]) == 2   # blocked -> exit 2
    assert main(["refresh-extract", "--extract-id", str(ext)]) == 0
    assert main(["health"]) == 0
    assert main(["process-decisions"]) == 0
    assert main(["notify"]) == 0


def test_rule_engine_adapter_contract(conn):
    from framework.config.models import RuleBinding
    from framework.validation.gre_adapter import CallableRuleEngine, GreRuleEngine

    b = [RuleBinding("P", "T", "S", "FILE_LEVEL", "g", "v")]
    seen = []
    ok = CallableRuleEngine(lambda d, m, g, v, p: seen.append((d, m)) or [{"rule_ref": "R1", "passed": True}])
    assert ok.run("data", conn, b, {}, "GATE").status == "PASSED" and seen == [("data", conn)]
    bad = CallableRuleEngine(lambda d, m, g, v, p: [{"rule_ref": "R1", "passed": False}])
    assert bad.run(conn, conn, b, {}, "GATE").failed_rules == ["R1"]
    assert bad.run(conn, conn, b, {}, "ANNOTATE").status == "PASSED_WITH_WARNINGS"
    assert CallableRuleEngine(lambda *a: [{"oops": 1}]).run(conn, conn, b, {}, "GATE").status == "ERROR"
    assert GreRuleEngine(None).run(conn, conn, b, {}, "GATE").status == "ERROR"          # Q-12 not configured
    assert GreRuleEngine(None).run(conn, conn, [], {}, "GATE").status == "PASSED"


POLICY = ExtractPolicy("P", "T", "R", "STRICT_ALL_PASS", "GATE", "HTTP_API", None, "", "POST", None, 5, 0, True)


class _Handler(BaseHTTPRequestHandler):
    status = 202
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append((dict(self.headers), body))
        self.send_response(_Handler.status)
        self.end_headers()
        self.wfile.write(json.dumps({"job_run_id": "abc"}).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def http_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/extract"
    srv.shutdown()


def test_http_connector(http_server):
    from dataclasses import replace
    pol = replace(POLICY, endpoint_url=http_server, auth_secret_nm="sec")
    con = HttpApiConnector("us-east-1", ["200-299"], secret_loader=lambda n: {"header_name": "X-Api-Key", "header_value": "k"})
    _Handler.status = 202
    r = con.call(pol, [("period", "2026-01-01")])
    assert r.accepted and r.job_run_ref == "abc"
    headers, body = _Handler.seen[-1]
    assert body == {"period": "2026-01-01"} and headers["X-Api-Key"] == "k"
    _Handler.status = 500
    r = con.call(pol, [])
    assert not r.accepted and not r.ambiguous
    r = con.call(replace(pol, endpoint_url="http://127.0.0.1:1/x"), [])
    assert not r.accepted


def test_glue_connector():
    pytest.importorskip("botocore")
    from botocore.exceptions import ClientError

    class Client:
        def __init__(self, exc=None):
            self.exc, self.kw = exc, None

        def start_job_run(self, **kw):
            self.kw = kw
            if self.exc:
                raise self.exc
            return {"JobRunId": "jr_9"}

    from dataclasses import replace
    pol = replace(POLICY, job_ty="GLUE_JOB", job_nm="extract_job")
    c = Client()
    r = GlueJobConnector("us-east-1", c).call(pol, [("--A", "1")])
    assert r.accepted and r.job_run_ref == "jr_9" and c.kw == {"JobName": "extract_job", "Arguments": {"--A": "1"}}
    r = GlueJobConnector("us-east-1", Client(ClientError({"Error": {"Code": "X"}}, "StartJobRun"))).call(pol, [])
    assert not r.accepted and not r.ambiguous
    r = GlueJobConnector("us-east-1", Client(TimeoutError("read timeout"))).call(pol, [])
    assert r.ambiguous
