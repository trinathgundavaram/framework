"""Module dispatcher: a module name selects batch creation, file load or the rules trigger (modules.py)."""
import json
import os
from datetime import datetime

import pytest

from framework.cli import main
from framework.common import ConfigError
from framework.modules import MODULES, describe_modules, module_names, resolve_module, run_module

from .helpers import file_name, make_app, put_file, q1, qa, seed_config, utc


# ------------------------------------------------------------------ no database
@pytest.mark.parametrize("name", ["BATCH_CREATION", "batch_creation", "batch-creation", " Batch Creation ",
                                  "create-batches", "BATCHES"])
def test_batch_creation_is_identified(name):
    assert resolve_module(name).name == "BATCH_CREATION"


@pytest.mark.parametrize("name,expected", [("file-load", "FILE_LOAD"), ("INGEST", "FILE_LOAD"),
                                           ("rules_trigger", "RULES_TRIGGER"), ("rules", "RULES_TRIGGER"),
                                           ("batch-intake", "BATCH_INTAKE"), ("intake", "BATCH_INTAKE")])
def test_other_modules_are_identified(name, expected):
    assert resolve_module(name).name == expected


def test_unknown_module_lists_the_available_ones():
    with pytest.raises(ConfigError, match="unknown module 'NOPE'.*BATCH_CREATION.*FILE_LOAD.*RULES_TRIGGER"):
        resolve_module("NOPE")
    with pytest.raises(ConfigError):
        run_module(None, "")


def test_registry_is_consistent():
    assert module_names() == ["BATCH_CREATION", "BATCH_INTAKE", "FILE_LOAD", "RULES_TRIGGER"]
    assert [d["module"] for d in describe_modules()] == list(MODULES)
    for spec in MODULES.values():                       # every required parameter is a declared parameter
        assert {p.name for p in spec.required} <= set(spec.params)


def test_parameters_are_checked_before_anything_runs():
    # app=None: validation must fail before the handler touches the app
    with pytest.raises(ConfigError, match="BATCH_CREATION needs: run_type, period"):
        run_module(None, "BATCH_CREATION", {"project": "PRJA"})
    with pytest.raises(ConfigError, match="does not take: bucket"):
        run_module(None, "BATCH_CREATION", {"project": "P", "run_type": "R", "period": "X", "bucket": "b"})
    with pytest.raises(ConfigError, match="lookback_days must be int"):
        run_module(None, "BATCH_CREATION", {"project": "P", "run_type": "R", "period": "X", "lookback_days": "soon"})
    with pytest.raises(ConfigError, match="FILE_LOAD with --key also needs --bucket"):
        run_module(None, "FILE_LOAD", {"key": "a/b.txt"})
    with pytest.raises(ConfigError, match="not both"):
        run_module(None, "FILE_LOAD", {"bucket": "b", "key": "k", "prefix": "p/"})
    with pytest.raises(ConfigError, match="only applies to a single object"):
        run_module(None, "FILE_LOAD", {"bucket": "b", "prefix": "p/", "version_id": "v1"})
    with pytest.raises(ConfigError, match="RULES_TRIGGER needs --extract-id, or --project"):
        run_module(None, "RULES_TRIGGER", {"table": "tbl_x"})
    with pytest.raises(ConfigError, match="not both"):
        run_module(None, "RULES_TRIGGER", {"extract_id": 1, "project": "PRJA"})


def test_list_modules_needs_no_database(capsys):
    assert main(["list-modules"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {m["module"] for m in out} == {"BATCH_CREATION", "BATCH_INTAKE", "FILE_LOAD", "RULES_TRIGGER"}


# ------------------------------------------------------------------ database
BATCH_PARAMS = {"project": "PRJA", "run_type": "MONTHLY", "period": "PREV_CALENDAR_MONTH"}


def setup(conn, tmp_path):
    seed_config(conn)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    return app, clock, rules


def test_batch_creation_module(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    out = run_module(app, "batch-creation", BATCH_PARAMS)
    assert (out.module, out.exit_code, out.result.created) == ("BATCH_CREATION", 0, 2)
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestControl")["n"] == 2
    again = app.run_module("BATCH_CREATION", BATCH_PARAMS)               # idempotent, and via App
    assert (again.result.created, again.result.existing) == (0, 2)
    out = run_module(app, "BATCH_CREATION", {**BATCH_PARAMS, "table": "no_such_table"})
    assert out.exit_code == 1 and out.result.errors                         # nothing effective: completed with problems


def test_batch_intake_module(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    out = run_module(app, "BATCH_INTAKE")
    assert (out.module, out.exit_code, out.result.created) == ("BATCH_INTAKE", 0, 0)


def test_file_load_module_one_object_then_every_location(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    run_module(app, "BATCH_CREATION", BATCH_PARAMS)
    key = put_file(app, file_name("S1"), ["1|1|a"])
    out = run_module(app, "FILE_LOAD", {"bucket": "inbound", "key": key})
    assert (out.module, out.exit_code, out.result.result) == ("FILE_LOAD", 0, "PROMOTED")

    put_file(app, file_name("S2"), ["2|2|b"])
    out = run_module(app, "file-load", {})                                   # no arguments: every configured location
    assert (out.exit_code, out.result.scanned, out.result.promoted, out.result.errors) == (0, 1, 1, [])
    assert {r["req_stat"] for r in qa(conn, "SELECT Req_Stat FROM ComplianceRequestControl")} == {"PROMOTED"}


def test_file_load_module_one_location_and_errors(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    run_module(app, "BATCH_CREATION", BATCH_PARAMS)
    put_file(app, file_name("S1"), ["1|1|a"])
    out = run_module(app, "FILE_LOAD", {"bucket": "inbound", "prefix": "prja/in/"})
    assert (out.result.scanned, out.result.promoted) == (1, 1)
    with pytest.raises(ValueError, match="together"):                        # bucket without prefix
        run_module(app, "FILE_LOAD", {"bucket": "inbound"})


def _ingest_both(app):
    for src in ("S1", "S2"):
        app.pipeline.process_file("inbound", put_file(app, file_name(src, ts=datetime(2026, 2, 1, 9, 30)),
                                                      ["1|1|a"]))


def test_rules_trigger_module_reruns_period_rules(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    run_module(app, "BATCH_CREATION", BATCH_PARAMS)
    _ingest_both(app)
    ext = q1(conn, "SELECT * FROM ComplianceExtractControl")
    assert ext["extract_rules_stat"] == "PASSED"
    before = len([c for c in rules.calls if c["scope"] == "PERIOD_LEVEL"])

    out = run_module(app, "RULES_TRIGGER", {"project": "PRJA", "run_type": "MONTHLY"})
    assert (out.module, out.exit_code, out.result.evaluated, out.result.passed) == ("RULES_TRIGGER", 0, 1, 1)
    assert len([c for c in rules.calls if c["scope"] == "PERIOD_LEVEL"]) == before + 1     # forced, data unchanged
    assert q1(conn, "SELECT Combine_Last_Trigger_Cd c FROM ComplianceExtractControl")["c"] == "MANUAL_REFRESH"

    rules.period_fail = ["P_TOTALS"]                                        # a rule now fails: exit 1, one extract by id
    out = run_module(app, "rules-trigger", {"extract_id": str(ext["extract_id"])})
    assert (out.exit_code, out.result.failed) == (1, 1)
    assert out.result.results[0].failed_rules == ["P_TOTALS"]
    assert q1(conn, "SELECT Extract_Rules_Stat s FROM ComplianceExtractControl")["s"] == "FAILED"


def test_rules_trigger_scope_and_closed_extracts(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    run_module(app, "BATCH_CREATION", BATCH_PARAMS)
    _ingest_both(app)
    ext = q1(conn, "SELECT Extract_ID FROM ComplianceExtractControl")["extract_id"]

    other = run_module(app, "RULES_TRIGGER", {"project": "PRJA", "run_type": "ADHOC"})
    assert other.result.evaluated == 0 and other.exit_code == 0             # nothing of that run type
    with pytest.raises(LookupError):
        run_module(app, "RULES_TRIGGER", {"extract_id": 999999})

    clock.set(utc(2026, 2, 3, 12, 0))
    app.evaluator.run()                                                      # past the hold: auto-closed
    assert q1(conn, "SELECT Extract_Close_Ind i FROM ComplianceExtractControl")["i"] == 1
    calls = len(rules.calls)
    out = run_module(app, "RULES_TRIGGER", {"extract_id": ext})              # closed: never recombined
    assert (out.result.evaluated, out.result.skipped, out.exit_code) == (0, 1, 0)
    assert out.result.results[0].detail == "extract is closed" and len(rules.calls) == calls
    assert run_module(app, "RULES_TRIGGER", {"project": "PRJA"}).result.evaluated == 0   # scope sweeps open runs only


# ------------------------------------------------------------------ CLI
def test_cli_run_module(conn, tmp_path, monkeypatch, capsys):
    from .conftest import SCHEMA
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"FRAMEWORK_DB_DSN={os.environ['TEST_DATABASE_URL']}\n"
                                   f"FRAMEWORK_METADATA_SCHEMA={SCHEMA}\nFRAMEWORK_OBJECT_STORE=local\n"
                                   f"FRAMEWORK_LOCAL_STORE_ROOT={tmp_path / 'store'}\n")
    monkeypatch.setenv("FRAMEWORK_RULE_ENGINE", "none")
    assert main(["init-db"]) == 0
    seed_config(conn)
    asof = ["--as-of", "2026-02-01T13:00:00+00:00"]

    capsys.readouterr()
    assert main(["run", "--module", "BATCH_CREATION", "--project", "PRJA", "--run-type", "MONTHLY",
                 "--period", "PREV_CALENDAR_MONTH", *asof]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["module"] == "BATCH_CREATION" and out["result"]["created"] == 2

    app, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    put_file(app, file_name("S1"), ["1|1|a"])
    put_file(app, file_name("S2"), ["2|2|b"])
    assert main(["run", "--module", "file-load", *asof]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["module"] == "FILE_LOAD" and (out["result"]["scanned"], out["result"]["promoted"]) == (2, 2)

    assert main(["run", "--module", "RULES_TRIGGER", "--project", "PRJA", *asof]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["module"] == "RULES_TRIGGER" and out["result"]["evaluated"] == 1

    # identification errors are exit code 2 and change nothing
    assert main(["run", "--module", "NOPE"]) == 2
    assert main(["run", "--module", "BATCH_CREATION", "--project", "PRJA"]) == 2           # missing run type / period
    assert main(["run", "--module", "FILE_LOAD", "--period", "PREV_CALENDAR_MONTH"]) == 2  # not a FILE_LOAD parameter
    with pytest.raises(SystemExit) as e:                                                    # --module is required
        main(["run"])
    assert e.value.code == 2
