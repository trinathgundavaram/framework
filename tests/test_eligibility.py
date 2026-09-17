from datetime import date

import pytest

from framework.extract.eligibility import AUTO, MANUAL_ONLY, NOT_ELIGIBLE, EligibilityInput, compute

D = date(2026, 2, 2)


def inp(**kw):
    base = dict(gating_md="STRICT_ALL_PASS", required=2, received=2, waived=0, rules_stat="PASSED",
                failed_rules=(), waived_rules=frozenset(), today=D, earliest_trigger_dt=D)
    base.update(kw)
    return EligibilityInput(**base)


def test_hold_blocks_everything():
    e = compute(inp(today=date(2026, 2, 1)))
    assert e.code == NOT_ELIGIBLE and "SLA hold" in e.reason


@pytest.mark.parametrize("mode", ["STRICT_ALL_PASS", "BEST_EFFORT"])
def test_complete_and_passed_is_auto(mode):
    assert compute(inp(gating_md=mode)).code == AUTO
    assert compute(inp(gating_md=mode, rules_stat="PASSED_WITH_WARNINGS")).code == AUTO


@pytest.mark.parametrize("stat", ["PENDING", "ERROR"])
def test_rules_not_available(stat):
    assert compute(inp(rules_stat=stat)).code == NOT_ELIGIBLE
    assert compute(inp(rules_stat=stat, gating_md="BEST_EFFORT")).code == NOT_ELIGIBLE


def test_strict_missing_source_blocked_until_waiver():
    assert compute(inp(received=1)).code == NOT_ELIGIBLE
    assert compute(inp(received=1, waived=1)).code == MANUAL_ONLY
    assert compute(inp(received=1, waived=1), strict_waiver_auto=True).code == AUTO


def test_strict_failed_rules_need_rule_waivers():
    e = compute(inp(rules_stat="FAILED", failed_rules=("R1", "R2"), waived_rules=frozenset({"R1"})))
    assert e.code == NOT_ELIGIBLE and "R2" in e.reason
    assert compute(inp(rules_stat="FAILED", failed_rules=("R1", "R2"),
                       waived_rules=frozenset({"R1", "R2"}))).code == MANUAL_ONLY


def test_best_effort_warnings():
    e = compute(inp(gating_md="BEST_EFFORT", received=1, rules_stat="FAILED", failed_rules=("R9",)))
    assert e.code == MANUAL_ONLY
    assert any("1 of 2" in w for w in e.warnings) and any("R9" in w for w in e.warnings)
    z = compute(inp(gating_md="BEST_EFFORT", received=0))
    assert z.code == MANUAL_ONLY and any("no source has data" in w for w in z.warnings)


def test_no_required_sources():
    assert compute(inp(required=0, received=0)).code == NOT_ELIGIBLE
