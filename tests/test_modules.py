"""Module dispatcher: a module name selects batch creation or file load (modules.py).

BATCH_CREATION is the merged module (D: combine routine + ad-hoc into one project-scoped call): it
creates the routine batches of a project/ROUTINE run type/period when those are given, and *always*
also sweeps that project's pending ad-hoc intake requests (ComplianceRequestInTake) - project-scoped
either way, so one project's trigger never touches another project's rows of either kind.
"""
import json
import os
from datetime import date

import pytest

from framework.cli import main
from framework.common import ConfigError
from framework.modules import MODULES, describe_modules, module_names, resolve_module, run_module

from .helpers import file_name, make_app, put_file, q1, qa, seed_config, utc


def intake(conn, *, project="PRJA", run_ty="ADHOC", src=None, start="2026-03-01", end="2026-03-31",
           req_start="2026-02-01", req_end=None) -> int:
    with conn.transaction():
        return conn.execute("""INSERT INTO ComplianceRequestInTake (Project_Cd, Table_Nm, Run_Ty, Src_ID,
                                 Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
                               VALUES (%s,'tbl_x',%s,%s,%s,%s,%s,%s) RETURNING Intake_ID""",
                            (project, run_ty, src, start, end, req_start, req_end or req_start)).fetchone()["intake_id"]


def last_run(conn, iid):
    return q1(conn, "SELECT Last_Run_Dt_Key d FROM ComplianceRequestInTake WHERE Intake_ID=%s", iid)["d"]


# ------------------------------------------------------------------ no database
@pytest.mark.parametrize("name", ["BATCH_CREATION", "batch_creation", "batch-creation", " Batch Creation "])
def test_batch_creation_name_is_case_and_dash_insensitive(name):
    """Case and `-`/`_` are interchangeable; there are no other names for this module (no aliases)."""
    assert resolve_module(name).name == "BATCH_CREATION"


@pytest.mark.parametrize("name,expected", [("file-load", "FILE_LOAD"), ("file_load", "FILE_LOAD"),
                                           ("File Load", "FILE_LOAD")])
def test_other_modules_are_identified(name, expected):
    assert resolve_module(name).name == expected


@pytest.mark.parametrize("name", ["create-batches", "BATCHES", "batch-intake", "intake", "process-intake",
                                  "adhoc-batches", "CREATE_BATCHES", "BATCH_INTAKE", "INTAKE", "PROCESS_INTAKE",
                                  "ADHOC_BATCHES", "INGEST", "FILE_INGEST", "LOAD", "INGEST_FILE", "INGEST_PATH",
                                  "RULES", "TRIGGER_RULES", "RUN_RULES", "RULES_TRIGGER"])
def test_old_aliases_no_longer_resolve(name):
    """This is a brand-new system: there is no legacy caller to keep these names working for, so a module
    is reached only by its canonical name (case/dash normalised) - nothing else resolves."""
    with pytest.raises(ConfigError, match="unknown module"):
        resolve_module(name)


def test_unknown_module_lists_the_available_ones():
    with pytest.raises(ConfigError, match="unknown module 'NOPE'.*BATCH_CREATION.*FILE_LOAD"):
        resolve_module("NOPE")
    with pytest.raises(ConfigError):
        run_module(None, "")


def test_registry_is_consistent():
    assert module_names() == ["BATCH_CREATION", "FILE_LOAD"]
    assert [d["module"] for d in describe_modules()] == list(MODULES)
    for spec in MODULES.values():                       # every required parameter is a declared parameter
        assert {p.name for p in spec.required} <= set(spec.params)


def test_parameters_are_checked_before_anything_runs():
    # app=None: validation must fail before the handler touches the app
    with pytest.raises(ConfigError, match="BATCH_CREATION needs: project"):
        run_module(None, "BATCH_CREATION", {})
    with pytest.raises(ConfigError, match="does not take: bucket"):
        run_module(None, "BATCH_CREATION", {"project": "P", "bucket": "b"})
    with pytest.raises(ConfigError, match="lookback_days must be int"):
        run_module(None, "BATCH_CREATION", {"project": "P", "run_type": "R", "period": "X", "lookback_days": "soon"})
    with pytest.raises(ConfigError, match="need --run-type"):                          # period with no run_type
        run_module(None, "BATCH_CREATION", {"project": "P", "period": "X"})
    with pytest.raises(ConfigError, match="FILE_LOAD with --key also needs --bucket"):
        run_module(None, "FILE_LOAD", {"key": "a/b.txt"})
    with pytest.raises(ConfigError, match="not both"):
        run_module(None, "FILE_LOAD", {"bucket": "b", "key": "k", "prefix": "p/"})
    with pytest.raises(ConfigError, match="only applies to a single object"):
        run_module(None, "FILE_LOAD", {"bucket": "b", "prefix": "p/", "version_id": "v1"})


def test_list_modules_needs_no_database(capsys):
    assert main(["list-modules"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {m["module"] for m in out} == {"BATCH_CREATION", "FILE_LOAD"}


# ------------------------------------------------------------------ database
BATCH_PARAMS = {"project": "PRJA", "run_type": "MONTHLY", "period": "PREV_CALENDAR_MONTH"}


def setup(conn, tmp_path):
    seed_config(conn)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    return app, clock, rules


def test_batch_creation_module_routine_and_ad_hoc_together(conn, tmp_path):
    """One call, both kinds: routine batches created AND that project's pending ad-hoc request processed."""
    app, *_ = setup(conn, tmp_path)
    a1 = intake(conn)                                                    # PRJA, req window opens 2026-02-01
    out = run_module(app, "batch-creation", BATCH_PARAMS)
    assert out.module == "BATCH_CREATION" and out.exit_code == 0
    assert out.result.scheduled.created == 2                             # the routine part (unchanged behaviour)
    assert out.result.adhoc.created == 2                                 # the ad-hoc part, in the SAME call
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestControl")["n"] == 4
    assert last_run(conn, a1) == date(2026, 2, 1)

    again = app.run_module("BATCH_CREATION", BATCH_PARAMS)               # idempotent, and via App
    assert (again.result.scheduled.created, again.result.scheduled.existing) == (0, 2)
    assert again.result.adhoc.handled == 0                               # A1 already handled today

    out = run_module(app, "BATCH_CREATION", {**BATCH_PARAMS, "table": "no_such_table"})
    assert out.exit_code == 1 and out.result.scheduled.errors            # nothing effective: completed with problems


def test_batch_creation_project_only_runs_ad_hoc_only(conn, tmp_path):
    """No --run-type/--period: only the ad-hoc sweep runs; `scheduled` is None, nothing routine happens."""
    app, *_ = setup(conn, tmp_path)
    intake(conn)
    out = run_module(app, "BATCH_CREATION", {"project": "PRJA"})
    assert out.result.scheduled is None and out.result.adhoc.created == 2 and out.exit_code == 0
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestControl")["n"] == 2            # ad-hoc batches only
    assert q1(conn, "SELECT count(*) n FROM ComplianceRequestControl WHERE Run_Ty='MONTHLY'")["n"] == 0


def test_batch_creation_with_adhoc_run_type_scopes_the_sweep_and_rejects_period(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    intake(conn, run_ty="ADHOC")
    with pytest.raises(ConfigError, match="is ADHOC; --period"):
        run_module(app, "BATCH_CREATION", {"project": "PRJA", "run_type": "ADHOC", "period": "SAME_DAY"})
    out = run_module(app, "BATCH_CREATION", {"project": "PRJA", "run_type": "ADHOC"})
    assert out.result.scheduled is None and out.result.adhoc.created == 2


def test_batch_creation_is_scoped_to_the_input_project_for_both_kinds(conn, tmp_path):
    """One trigger per project: PRJB has its own crosswalk rows (routine AND ad-hoc) and its own intake
    row, but a PRJA run never touches PRJB's rows, and vice versa - proven for both halves of the merge."""
    app, *_ = setup(conn, tmp_path)
    with conn.transaction():
        conn.execute("""INSERT INTO ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty,
                          Effective_Start_Dt_Key, Cmplnc_Vrsn)
                        VALUES ('PRJB','tbl_y','S1','MONTHLY','2025-01-01','V2'),
                               ('PRJB','tbl_y','S2','MONTHLY','2025-01-01','V2'),
                               ('PRJB','tbl_x','S1','ADHOC','2025-01-01','V1'),
                               ('PRJB','tbl_x','S2','ADHOC','2025-01-01','V1')""")
    a1 = intake(conn, project="PRJA")
    b1 = intake(conn, project="PRJB")
    by_project = lambda: {r["project_cd"]: r["n"] for r in qa(   # noqa: E731
        conn, "SELECT Project_Cd, count(*) n FROM ComplianceRequestControl GROUP BY 1")}

    a = run_module(app, "BATCH_CREATION", BATCH_PARAMS)                       # PRJA trigger
    assert a.result.scheduled.created == 2 and a.result.adhoc.created == 2    # routine tbl_x + A1's own ad-hoc
    assert by_project() == {"PRJA": 4}                                        # PRJB doesn't exist yet
    assert last_run(conn, b1) is None

    b = run_module(app, "BATCH_CREATION", {**BATCH_PARAMS, "project": "PRJB"})  # PRJB trigger
    assert b.result.scheduled.created == 2 and b.result.scheduled.errors == []
    assert b.result.adhoc.created == 2
    assert sorted(r["btch_id"] for r in qa(conn, "SELECT Btch_ID FROM ComplianceRequestControl "
                                                 "WHERE Project_Cd='PRJB' AND Run_Ty='MONTHLY'")) \
        == ["20260201_PRJB_tbl_y_S1_MONTHLY_V2_1", "20260201_PRJB_tbl_y_S2_MONTHLY_V2_1"]
    assert by_project() == {"PRJA": 4, "PRJB": 4}                             # PRJA's 4 rows: untouched
    assert last_run(conn, a1) == last_run(conn, b1) == date(2026, 2, 1)

    none = run_module(app, "BATCH_CREATION", {**BATCH_PARAMS, "project": "PRJC"})   # unknown project
    assert none.exit_code == 1 and none.result.scheduled.created == 0 and "PRJC" in none.result.scheduled.errors[0]
    assert none.result.adhoc.created == 0                                            # nothing pending for PRJC either
    assert by_project() == {"PRJA": 4, "PRJB": 4}                                     # PRJC created nothing at all


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
    intake(conn)
    asof = ["--as-of", "2026-02-01T13:00:00+00:00"]

    capsys.readouterr()
    assert main(["run", "--module", "BATCH_CREATION", "--project", "PRJA", "--run-type", "MONTHLY",
                 "--period", "PREV_CALENDAR_MONTH", *asof]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["module"] == "BATCH_CREATION"
    assert out["result"]["scheduled"]["created"] == 2 and out["result"]["adhoc"]["created"] == 2

    app, *_ = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    put_file(app, file_name("S1"), ["1|1|a"])
    put_file(app, file_name("S2"), ["2|2|b"])
    assert main(["run", "--module", "file-load", *asof]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["module"] == "FILE_LOAD" and (out["result"]["scanned"], out["result"]["promoted"]) == (2, 2)


    # identification errors are exit code 2 and change nothing
    assert main(["run", "--module", "NOPE"]) == 2
    assert main(["run", "--module", "BATCH_CREATION"]) == 2                                 # missing --project
    assert main(["run", "--module", "FILE_LOAD", "--period", "PREV_CALENDAR_MONTH"]) == 2  # not a FILE_LOAD parameter
    with pytest.raises(SystemExit) as e:                                                    # --module is required
        main(["run"])
    assert e.value.code == 2
