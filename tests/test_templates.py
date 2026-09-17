from datetime import date, datetime

import pytest

from framework.config.models import FileConfig
from framework.config.templates import MatchError, TemplateError, TemplateMatcher, parse_template, render

T = "{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt"


def cfg(cfg_id=1, tmpl=T, p="PRJA", t="TBLX", s="S1"):
    return FileConfig(cfg_id, "PRJA", "tbl_x", s, tmpl, p, t, s, ".txt", "|", None, True, False, False, "PANDAS",
                      "GATE", True, (), "s3://b/in/", "s3://b/ar/", "s3://b/q/", "stg", "tbl_x", "core", "tbl_x",
                      None, None, None, None, None, True)


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
