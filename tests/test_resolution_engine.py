import pytest

from framework.ingest.resolution_engine import Action, ActiveReopen, ResolutionInput, decide


@pytest.mark.parametrize("prior, passed, action, rule", [
    (False, True, Action.PROMOTE, "O-1"),
    (True, True, Action.PROMOTE_REPLACE, "O-2"),
    (False, False, Action.EXCEPTION_NO_DATA, "O-3"),
    (True, False, Action.EXCEPTION_KEEP_PRIOR, "O-4"),
])
def test_open_batch(prior, passed, action, rule):
    d = decide(ResolutionInput(False, "NEW_FILE" if prior else None, prior, passed))
    assert (d.action, d.rule) == (action, rule)


@pytest.mark.parametrize("resolution, expected", [("MISSING", "LATE_ARRIVAL_REOPEN"), ("NEW_FILE", "CORRECTION_REOPEN")])
def test_x1_type_from_resolution(resolution, expected):
    d = decide(ResolutionInput(True, resolution, resolution == "NEW_FILE", True))
    assert (d.action, d.rule, d.override_ty) == (Action.REOPEN_INSERT, "X-1", expected)


@pytest.mark.parametrize("stat, promo, action, rule", [
    ("PENDING_REVIEW", "NOT_APPLICABLE", Action.REOPEN_REPLACE_PENDING, "X-2"),
    ("APPROVED", "PENDING", Action.REOPEN_RESET_APPROVED, "X-3"),
    ("APPROVED", "FAILED", Action.REOPEN_RESET_APPROVED, "X-3"),
    ("APPROVED", "PROMOTED", Action.REOPEN_RESET_PROMOTED, "X-4"),
])
def test_active_reopen(stat, promo, action, rule):
    d = decide(ResolutionInput(True, "NEW_FILE", True, True, ActiveReopen(stat, promo, "LATE_ARRIVAL_REOPEN")))
    assert (d.action, d.rule) == (action, rule)
    assert d.override_ty == "LATE_ARRIVAL_REOPEN"      # D-47 type kept


def test_x5_reopen_rejected_on_failure_regardless_of_override():
    for ar in (None, ActiveReopen("PENDING_REVIEW", "NOT_APPLICABLE", "CORRECTION_REOPEN")):
        assert decide(ResolutionInput(True, "MISSING", False, False, ar)).action == Action.REOPEN_REJECT


def test_closed_unresolved_is_a_bug():
    with pytest.raises(ValueError):
        decide(ResolutionInput(True, None, False, True))
