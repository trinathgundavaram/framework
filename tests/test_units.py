"""Pure unit tests (no database): templates, file reading, object store, eligibility, resolution, Btch_ID,
Req_Stat transitions, settings and period SQL loading."""
from dataclasses import replace
from datetime import date, datetime

import pytest

from framework.adapters import LocalObjectStore, parse_uri
from framework.batches import period_sql
from framework.common import (ConfigError, FileRejected, InvalidStatusTransition, build_btch_id, check_transition,
                              earliest_close_date)
from framework.config import FileConfig, MatchError, TemplateError, TemplateMatcher, parse_template, render
from framework.extract import AUTO, MANUAL_ONLY, NOT_ELIGIBLE, EligibilityInput, compute_eligibility
from framework.ingest import Action, IngestOutcome, PathIngestSummary, ResolutionInput, decide, required_override_ty
from framework.load import read_file, scan_delimited, stage
from framework.settings import Settings, read_env_file


# ---------------------------------------------------------------- templates
T = "{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt"


def cfg(cfg_id=1, tmpl=T, p="PRJA", t="TBLX", s="S1"):
    return FileConfig(cfg_id, "PRJA", "tbl_x", s, tmpl, p, t, s, ".txt", "|", None, True, False, False,
                      "s3://b/in/", "s3://b/ar/", "s3://b/q/", "stg", "tbl_x", "core", "tbl_x")


@pytest.mark.parametrize("bad, why", [
    ("{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}.txt", "exactly once"),
    ("{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}_{TS}.txt", "exactly once"),
    ("{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}_{FOO}.txt", "unknown"),
    ("{PROJECT}{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt", "separated"),
    ("dir/{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt", "no '/'"),
    ("{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt}", "braces"),
])
def test_template_grammar_errors(bad, why):
    with pytest.raises(TemplateError, match=why):
        parse_template(bad)


def test_match_extracts_tokens():
    m = TemplateMatcher([cfg()]).match("PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt")
    assert (m.run_ty, m.rpt_start, m.rpt_end) == ("MONTHLY", date(2026, 1, 1), date(2026, 1, 31))
    assert m.file_ts == datetime(2026, 2, 1, 9, 30)


def test_no_match_and_alias_mismatch():
    mt = TemplateMatcher([cfg()])
    for name in ("PRJA_TBLX_S9_MONTHLY_20260101_20260131_20260201093000.txt",
                 "PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.csv",
                 "PRJA_TBLX_S1_MONTHLY_2026011_20260131_20260201093000.txt",
                 "PRJA_TBLX_S1_MONTH_LY_20260101_20260131_20260201093000.txt"):
        with pytest.raises(MatchError) as e:
            mt.match(name)
        assert e.value.event_ty == "FILE_REJECTED_UNPARSEABLE"


@pytest.mark.parametrize("name", [
    "PRJA_TBLX_S1_MONTHLY_20260231_20260301_20260201093000.txt",   # impossible date
    "PRJA_TBLX_S1_MONTHLY_20260201_20260131_20260201093000.txt",   # end < start
    "PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201996000.txt",   # bad TS
])
def test_invalid_tokens(name):
    with pytest.raises(MatchError) as e:
        TemplateMatcher([cfg()]).match(name)
    assert e.value.event_ty == "FILE_REJECTED_INVALID_TOKEN"


def test_ambiguous():
    a = cfg(1, "{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt", s="S1")
    b = cfg(2, "{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt", s="S1")
    with pytest.raises(MatchError) as e:
        TemplateMatcher([a, b]).match("PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt")
    assert e.value.event_ty == "FILE_REJECTED_AMBIGUOUS_TEMPLATE"


def test_case_insensitive_option_and_regex_escaping():
    c = cfg(p="P.A", t="T+X")
    name = "p.a_t+x_s1_MONTHLY_20260101_20260131_20260201093000.TXT"
    assert TemplateMatcher([c], case_sensitive=False).match(name).run_ty == "MONTHLY"
    with pytest.raises(MatchError):
        TemplateMatcher([c]).match(name)
    with pytest.raises(MatchError):   # '.' must be literal
        TemplateMatcher([c]).match("PXA_T+X_S1_MONTHLY_20260101_20260131_20260201093000.txt")


def test_invalid_template_is_reported_not_raised():
    mt = TemplateMatcher([cfg(tmpl="broken")])
    assert mt.invalid and not mt.configs


def test_render_roundtrip():
    name = render(T, project="PRJA", table="TBLX", src="S1", runty="ADHOC", rpt_start=date(2026, 3, 1),
                  rpt_end=date(2026, 3, 31), ts=datetime(2026, 4, 2, 1, 2, 3))
    assert name == "PRJA_TBLX_S1_ADHOC_20260301_20260331_20260402010203.txt"
    assert TemplateMatcher([cfg()]).match(name).run_ty == "ADHOC"


# ---------------------------------------------------------------- file reading / object store
def settings(**kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_xlsx_reader(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    path = tmp_path / "f.xlsx"
    pd.DataFrame([["id", "amount", "name"], [1, 2.5, "a"], [2, None, "b"]]).to_excel(path, header=False, index=False)
    c = replace(cfg(), src_file_ty=".xlsx")
    with pytest.raises(FileRejected, match="not enabled"):
        read_file(str(path), c, 3, settings())
    r = read_file(str(path), c, 3, settings(supported_file_types=[".xlsx"]))
    assert r.rows == [["1", "2.5", "a"], ["2", None, "b"]]
    with pytest.raises(FileRejected) as e:
        read_file(str(path), c, 4, settings(supported_file_types=[".xlsx"]))
    assert e.value.event_ty == "FILE_COLUMN_COUNT_MISMATCH"


def test_delimiters_encoding_and_streaming_scan(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes("a\tb\n1\t2\n".encode())
    c = replace(cfg(), delmtr_cd="TAB")
    assert read_file(str(p), c, 2, settings()).rows == [["1", "2"]]
    assert scan_delimited(str(p), c, 2, settings()) == 1
    with pytest.raises(FileRejected):
        scan_delimited(str(p), c, 3, settings())
    p.write_bytes(b"a\tb\n\xff\xfe\t2\n")
    with pytest.raises(FileRejected) as e:
        read_file(str(p), c, 2, settings())
    assert e.value.event_ty == "FILE_PARSE_ERROR"
    assert read_file(str(p), c, 2, settings(file_encoding="latin-1")).data_row_count == 1


def test_local_store(tmp_path):
    s = LocalObjectStore(str(tmp_path))
    s.put("b", "in/x.txt", b"hello")
    info = s.head("b", "in/x.txt")
    assert info.size == 5 and info.version_id is None
    assert s.move("b", "in/x.txt", "s3://b/archive/", sub_prefix="R/") == "archive/R/x.txt"
    assert s.exists("b", "archive/R/x.txt") and not s.exists("b", "in/x.txt")
    with pytest.raises(ValueError):
        s.put("b", "../../escape.txt", b"x")
    assert parse_uri("s3://bucket/a/b") == ("bucket", "a/b/")
    with pytest.raises(ValueError):
        parse_uri("ftp://x/y")


def test_path_ingest_summary_tally():
    s = PathIngestSummary()
    for result in ("PROMOTED", "LATE_PROMOTED", "CORRECTION_PROMOTED", "QUARANTINED",
                   "REJECTED_CLOSED", "RULES_FAILED", "REPLAY_IGNORED", "IN_PROGRESS"):
        s.tally(IngestOutcome(load_id=1, result=result))
    assert (s.promoted, s.quarantined, s.rejected, s.replayed, s.other) == (3, 1, 2, 1, 1)
    assert len(s.outcomes) == 8


def test_local_store_list_objects(tmp_path):
    s = LocalObjectStore(str(tmp_path))
    s.put("b", "in/x.txt", b"hello")
    s.put("b", "in/y.txt", b"world!")
    s.put("b", "in/sub/z.txt", b"nested")             # not returned: listing is non-recursive (Q-03: root only)
    objs = s.list_objects("b", "in/")
    assert sorted(o.key for o in objs) == ["in/x.txt", "in/y.txt"]
    assert {o.bucket for o in objs} == {"b"}
    assert s.list_objects("b", "does-not-exist/") == []


def test_spark_engine_requires_jdbc_url():
    pytest.importorskip("pyspark", reason="pyspark not installed; Spark engine is exercised in a Spark/Glue environment")
    s = settings()
    s.load_engine = "SPARK"
    with pytest.raises(ConfigError):
        stage(None, s, file_path="x", cfg=cfg(), btch_id="b", load_id=1, src_file_nm="x", loaded_at=None)


# ---------------------------------------------------------------- eligibility
D = date(2026, 2, 2)


def inp(**kw):
    base = dict(gating_md="STRICT_ALL_PASS", required=2, received=2, rules_stat="PASSED",
                failed_rules=(), today=D, earliest_close_dt=D)
    base.update(kw)
    return EligibilityInput(**base)


def test_hold_blocks_everything():
    e = compute_eligibility(inp(today=date(2026, 2, 1)))
    assert e.code == NOT_ELIGIBLE and "SLA hold" in e.reason


@pytest.mark.parametrize("mode", ["STRICT_ALL_PASS", "BEST_EFFORT"])
def test_complete_and_passed_is_auto(mode):
    assert compute_eligibility(inp(gating_md=mode)).code == AUTO
    assert compute_eligibility(inp(gating_md=mode, rules_stat="PASSED_WITH_WARNINGS")).code == AUTO


@pytest.mark.parametrize("stat", ["PENDING", "ERROR"])
def test_rules_not_available(stat):
    assert compute_eligibility(inp(rules_stat=stat)).code == NOT_ELIGIBLE
    assert compute_eligibility(inp(rules_stat=stat, gating_md="BEST_EFFORT")).code == NOT_ELIGIBLE


def test_strict_blocks_missing_sources_and_failed_rules():
    e = compute_eligibility(inp(received=1))
    assert e.code == NOT_ELIGIBLE and "1 of 2" in e.reason
    e = compute_eligibility(inp(rules_stat="FAILED", failed_rules=("R1",)))
    assert e.code == NOT_ELIGIBLE and "R1" in e.reason


def test_best_effort_warnings():
    e = compute_eligibility(inp(gating_md="BEST_EFFORT", received=1, rules_stat="FAILED", failed_rules=("R9",)))
    assert e.code == MANUAL_ONLY
    assert any("1 of 2" in w for w in e.warnings) and any("R9" in w for w in e.warnings)
    z = compute_eligibility(inp(gating_md="BEST_EFFORT", received=0))
    assert z.code == MANUAL_ONLY and any("no source has data" in w for w in z.warnings)


def test_no_required_sources():
    assert compute_eligibility(inp(required=0, received=0)).code == NOT_ELIGIBLE


# ---------------------------------------------------------------- resolution tables
@pytest.mark.parametrize("has_data, passed, action, rule", [
    (False, True, Action.PROMOTE, "O-1"),
    (True, True, Action.PROMOTE_REPLACE, "O-2"),
    (False, False, Action.EXCEPTION_NO_DATA, "O-3"),
    (True, False, Action.EXCEPTION_KEEP_PRIOR, "O-4"),
])
def test_open_batch(has_data, passed, action, rule):
    d = decide(ResolutionInput(batch_closed=False, has_data=has_data, file_passed=passed))
    assert (d.action, d.rule) == (action, rule)


@pytest.mark.parametrize("has_data, override_ty, action, rule", [
    (False, "LATE_ARRIVAL", Action.PROMOTE_LATE, "C-1"),
    (True, "CORRECTION", Action.PROMOTE_CORRECTION, "C-2"),
    (False, None, Action.REJECT_CLOSED, "C-3"),
    (True, None, Action.REJECT_CLOSED, "C-3"),
    (False, "CORRECTION", Action.REJECT_CLOSED, "C-3"),        # wrong type for a batch without data
    (True, "LATE_ARRIVAL", Action.REJECT_CLOSED, "C-3"),       # wrong type for a batch with data
])
def test_closed_batch(has_data, override_ty, action, rule):
    d = decide(ResolutionInput(batch_closed=True, has_data=has_data, file_passed=True, override_ty=override_ty))
    assert (d.action, d.rule) == (action, rule)


@pytest.mark.parametrize("override_ty", [None, "LATE_ARRIVAL", "CORRECTION"])
def test_closed_batch_rules_failure_never_promotes(override_ty):
    d = decide(ResolutionInput(batch_closed=True, has_data=False, file_passed=False, override_ty=override_ty))
    assert (d.action, d.rule) == (Action.REJECT_RULES, "C-4")


def test_required_override_ty():
    assert required_override_ty(False) == "LATE_ARRIVAL" and required_override_ty(True) == "CORRECTION"


# ---------------------------------------------------------------- Btch_ID
def test_build_btch_id():
    assert build_btch_id(date(2026, 2, 1), "PRJA", "tbl_x", "S1", "MONTHLY", "V1", 1) == "20260201_PRJA_tbl_x_S1_MONTHLY_V1_1"
    with pytest.raises(ValueError):
        build_btch_id(date(2026, 2, 1), "P", "T", "S", "R", "V", 0)
    with pytest.raises(ValueError):
        build_btch_id(date(2026, 2, 1), "P" * 300, "T", "S", "R", "V", 1)


@pytest.mark.parametrize("sla, expected", [(1, date(2026, 2, 1)), (2, date(2026, 2, 2)), (5, date(2026, 2, 5))])
def test_hold(sla, expected):
    assert earliest_close_date(date(2026, 2, 1), sla) == expected


def test_hold_requires_positive_sla():
    with pytest.raises(ValueError):
        earliest_close_date(date(2026, 2, 1), 0)


# ---------------------------------------------------------------- Req_Stat, settings, period SQL, job params
def test_status_transitions():
    check_transition("PENDING", "CARRIED_FORWARD")
    check_transition("CARRIED_FORWARD", "COMPLETED")
    check_transition("DATA_NOT_PROVIDED", "COMPLETED")
    with pytest.raises(InvalidStatusTransition):
        check_transition("COMPLETED", "PENDING")


def test_settings_precedence_and_coercion(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# local\nFRAMEWORK_OBJECT_STORE=local\nexport FRAMEWORK_LOCK_TIMEOUT_SECONDS='60'\n"
                        "FRAMEWORK_SUPPORTED_FILE_TYPES=.TXT, .csv  # comment\nFRAMEWORK_DB_NAME=\"fw\"\n")
    assert read_env_file(str(env_file))["FRAMEWORK_DB_NAME"] == "fw"
    s = Settings.load(env={"FRAMEWORK_LOCK_TIMEOUT_SECONDS": "5", "FRAMEWORK_ENV_FILE": str(env_file)},
                      overrides={"extract_gating_mode": "best_effort", "FRAMEWORK_EMPTY_AS_NULL": "no"})
    assert (s.lock_timeout_seconds, s.sources["lock_timeout_seconds"]) == (5, "env")
    assert (s.object_store, s.sources["object_store"]) == ("local", ".env")
    assert s.supported_file_types == [".txt", ".csv"]
    assert (s.extract_gating_mode, s.sources["extract_gating_mode"]) == ("BEST_EFFORT", "argument")
    assert s.empty_as_null is False and s.sources.get("rule_engine") is None
    with pytest.raises(ConfigError):
        Settings.load(env={"FRAMEWORK_METADATA_SCHEMA": "bad-name;drop"})
    with pytest.raises(ConfigError):
        Settings.load(env={}, overrides={"no_such_setting": "1"})
    with pytest.raises(ConfigError):
        Settings.load(env={"FRAMEWORK_EMPTY_AS_NULL": "maybe"})
    with pytest.raises(ConfigError):
        Settings.load(env={"FRAMEWORK_ENV_FILE": str(tmp_path / "missing.env")})


def test_db_conninfo_env_dsn_and_secret(tmp_path):
    empty = tmp_path / "empty.env"
    empty.write_text("")
    secret = {"host": "sechost", "port": 6000, "dbname": "secdb", "username": "secuser", "password": "secpw"}
    s = Settings.load(env={"FRAMEWORK_DB_SECRET_NAME": "fw/db", "FRAMEWORK_DB_HOST": "override",
                           "FRAMEWORK_ENV_FILE": str(empty)})
    info, desc = s.db_conninfo(secret_loader=lambda n: secret)
    assert "host=override" in info and "password=secpw" in info and "dbname=secdb" in info
    assert "password" not in desc and desc["user"] == "secuser" and desc["schema"] == "cms_compliance"
    s = Settings.load(env={"FRAMEWORK_DB_DSN": "host=h dbname=d user=u", "FRAMEWORK_DB_PORT": "5433",
                           "FRAMEWORK_ENV_FILE": str(empty)})
    info, desc = s.db_conninfo()
    assert desc == {"host": "h", "dbname": "d", "user": "u", "port": "5433", "schema": "cms_compliance"}
    with pytest.raises(ConfigError, match="no database"):
        Settings.load(env={"FRAMEWORK_ENV_FILE": str(empty)}).db_conninfo()
    with pytest.raises(ConfigError, match="cannot read secret"):
        Settings.load(env={"FRAMEWORK_DB_SECRET_NAME": "x", "FRAMEWORK_ENV_FILE": str(empty)}).db_conninfo(
            secret_loader=lambda n: (_ for _ in ()).throw(RuntimeError("denied")))


def test_period_sql_lookup(tmp_path):
    assert "date_trunc('month'" in period_sql("prev_calendar_month")
    with pytest.raises(ConfigError, match="unknown period"):
        period_sql("NOPE")
    f = tmp_path / "prj_periods.py"
    f.write_text('PERIOD_SQL = {"FISCAL": "SELECT DATE \'2026-01-01\' AS rpt_start, DATE \'2026-06-30\' AS rpt_end;",'
                 ' "BAD": "SELECT 1; SELECT 2"}\n')
    assert period_sql("FISCAL", str(f)).endswith("rpt_end")
    with pytest.raises(ConfigError, match="single statement"):
        period_sql("BAD", str(f))
    with pytest.raises(ConfigError):
        period_sql("FISCAL", str(tmp_path / "missing.py"))
