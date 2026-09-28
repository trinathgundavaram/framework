"""Pure unit tests (no database): templates, file reading, object store, close rules, resolution, Btch_ID,
Req_Stat transitions, settings and period SQL loading."""
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from framework.adapters import LocalObjectStore, parse_uri
from framework.batches import period_sql
from framework.closing import close_resolution, closes_automatically
from framework.common import (ConfigError, FileRejected, InvalidStatusTransition, build_btch_id, check_transition,
                              earliest_close_date, parse_as_of)
from framework.config import FileConfig, MatchError, TemplateError, TemplateMatcher, parse_template, render
from framework.ingest import Action, IngestOutcome, PathIngestSummary, ResolutionInput, decide, required_override_ty
from framework.load import read_file, scan_delimited, stage
from framework.settings import Settings, read_env_file


# ---------------------------------------------------------------- templates
TOKENS = "{RUNTY}_{RPTSTART}_{RPTEND}_{TS}"


def cfg(cfg_id=1, tmpl=None, p="PRJA", t="TBLX", s="S1"):
    return FileConfig(cfg_id, "PRJA", "tbl_x", s, tmpl or f"{p}_{t}_{s}_{TOKENS}.txt", "|", True, False, False,
                      "s3://b/in/", "s3://b/ar/", "stg", "tbl_x", "core")


@pytest.mark.parametrize("bad, why", [
    ("P_{RUNTY}_{RPTSTART}_{RPTEND}.txt", "exactly once"),
    ("P_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}_{TS}.txt", "exactly once"),
    ("P_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}_{FOO}.txt", "unknown"),
    ("{PROJECT}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt", "unknown"),          # project/table/source are literal text
    ("P_{RUNTY}{RPTSTART}_{RPTEND}_{TS}.txt", "separated"),
    ("dir/P_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt", "no '/'"),
    ("P_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt}", "braces"),
])
def test_template_grammar_errors(bad, why):
    with pytest.raises(TemplateError, match=why):
        parse_template(bad)


def test_match_extracts_tokens():
    m = TemplateMatcher([cfg()]).match("PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt")
    assert (m.run_ty, m.rpt_start, m.rpt_end) == ("MONTHLY", date(2026, 1, 1), date(2026, 1, 31))
    assert m.file_ts == datetime(2026, 2, 1, 9, 30)


def test_file_type_is_the_template_extension():
    assert cfg().src_file_ty == ".txt"
    assert cfg(tmpl=f"X_{TOKENS}.XLSX").src_file_ty == ".xlsx"
    assert cfg(tmpl=f"X_{TOKENS}").src_file_ty == ""


def test_no_match():
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
    a, b = cfg(1), cfg(2)
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
    name = render(cfg().src_file_nm_tmplt, runty="ADHOC", rpt_start=date(2026, 3, 1), rpt_end=date(2026, 3, 31),
                  ts=datetime(2026, 4, 2, 1, 2, 3))
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
    c = cfg(tmpl=f"X_{TOKENS}.xlsx")
    with pytest.raises(FileRejected, match="not enabled"):
        read_file(str(path), c, 3, settings())
    rows = read_file(str(path), c, 3, settings(supported_file_types=[".xlsx"]))
    assert rows == [["1", "2.5", "a"], ["2", None, "b"]]
    with pytest.raises(FileRejected) as e:
        read_file(str(path), c, 4, settings(supported_file_types=[".xlsx"]))
    assert e.value.event_ty == "FILE_COLUMN_COUNT_MISMATCH"


def test_delimiters_encoding_and_streaming_scan(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes("a\tb\n1\t2\n".encode())
    c = replace(cfg(), delmtr_cd="TAB")
    assert read_file(str(p), c, 2, settings()) == [["1", "2"]]
    assert scan_delimited(str(p), c, 2, settings()) == 1
    with pytest.raises(FileRejected):
        scan_delimited(str(p), c, 3, settings())
    p.write_bytes(b"a\tb\n\xff\xfe\t2\n")
    with pytest.raises(FileRejected) as e:
        read_file(str(p), c, 2, settings())
    assert e.value.event_ty == "FILE_PARSE_ERROR"
    assert len(read_file(str(p), c, 2, settings(file_encoding="latin-1"))) == 1


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


# ---------------------------------------------------------------- batch close rules
@pytest.mark.parametrize("req_stat, resolution, expected, auto", [
    ("PROMOTED", "NEW_FILE", ("COMPLETED", "NEW_FILE"), True),
    ("CARRIED_FORWARD", "CARRY_FORWARD", ("COMPLETED", "CARRY_FORWARD"), True),
    ("EXCEPTION_PENDING", None, ("COMPLETED_WITH_EXCEPTION", "MISSING"), True),
    ("EXCEPTION_PENDING", "NEW_FILE", ("COMPLETED_WITH_EXCEPTION", "NEW_FILE"), True),
    ("PENDING", None, ("DATA_NOT_PROVIDED", "MISSING"), False),                 # no data: a person closes it
])
def test_close_resolution(req_stat, resolution, expected, auto):
    b = {"req_stat": req_stat, "resolution_ty": resolution}
    assert close_resolution(b) == expected and closes_automatically(b) is auto
    check_transition(req_stat, expected[0])


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


# ---------------------------------------------------------------- --as-of / business_tz
def test_as_of_with_explicit_offset_is_honoured_exactly():
    c = parse_as_of("2026-02-01T18:00:00+00:00", "America/Chicago")     # offset given: business_tz is ignored
    assert c.now() == datetime(2026, 2, 1, 18, 0, tzinfo=timezone.utc)
    assert c.today("America/Chicago") == date(2026, 2, 1)               # 12:00 CST


def test_as_of_bare_date_is_midnight_in_business_tz_not_utc():
    """A bare date used to be read as UTC midnight - the previous evening in Chicago (CST, UTC-6) -
    which silently ran the wrong (December) report period. It must resolve to that calendar date."""
    c = parse_as_of("2026-02-01", "America/Chicago")
    assert c.now() == datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)   # CST midnight = 06:00 UTC
    assert c.today("America/Chicago") == date(2026, 2, 1)               # the intended run date
    assert c.today("UTC") == date(2026, 2, 1)                           # not Jan 31 anywhere now


def test_as_of_bare_timestamp_uses_business_tz_too():
    c = parse_as_of("2026-02-01T09:30:00", "America/Chicago")           # no offset, has a time
    assert c.now() == datetime(2026, 2, 1, 15, 30, tzinfo=timezone.utc)
    assert c.today("America/Chicago") == date(2026, 2, 1)


def test_as_of_default_business_tz_is_utc_unchanged():
    c = parse_as_of("2026-02-01")                                       # business_tz omitted: old default
    assert c.now() == datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)


def test_as_of_respects_dst_boundary():
    # 2026-03-08 is the US spring-forward date; CDT (UTC-5) starts at 02:00 local that day
    before = parse_as_of("2026-03-08", "America/Chicago")               # midnight is still CST (UTC-6)
    assert before.now() == datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)
    after = parse_as_of("2026-03-09", "America/Chicago")                # next midnight is CDT (UTC-5)
    assert after.now() == datetime(2026, 3, 9, 5, 0, tzinfo=timezone.utc)


def test_as_of_none_returns_live_clock():
    assert type(parse_as_of(None, "America/Chicago")).__name__ == "Clock"


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
                      overrides={"file_rules_mode": "annotate", "FRAMEWORK_EMPTY_AS_NULL": "no"})
    assert (s.lock_timeout_seconds, s.sources["lock_timeout_seconds"]) == (5, "env")
    assert (s.object_store, s.sources["object_store"]) == ("local", ".env")
    assert s.supported_file_types == [".txt", ".csv"]
    assert (s.file_rules_mode, s.sources["file_rules_mode"]) == ("ANNOTATE", "argument")
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
