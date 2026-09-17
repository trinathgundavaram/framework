from datetime import date

import pytest

from framework.common.btch_id import build, earliest_close_date


def test_build():
    assert build(date(2026, 2, 1), "PRJA", "tbl_x", "S1", "MONTHLY", "V1", 1) == "20260201_PRJA_tbl_x_S1_MONTHLY_V1_1"
    with pytest.raises(ValueError):
        build(date(2026, 2, 1), "P", "T", "S", "R", "V", 0)
    with pytest.raises(ValueError):
        build(date(2026, 2, 1), "P" * 300, "T", "S", "R", "V", 1)


@pytest.mark.parametrize("sla, expected", [(1, date(2026, 2, 1)), (2, date(2026, 2, 2)), (5, date(2026, 2, 5))])
def test_hold(sla, expected):
    assert earliest_close_date(date(2026, 2, 1), sla) == expected


def test_hold_requires_positive_sla():
    with pytest.raises(ValueError):
        earliest_close_date(date(2026, 2, 1), 0)
