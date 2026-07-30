"""A ratio's two sides must come from the same fiscal year.

AAPL stopped tagging `InterestExpense` after FY2023 while its operating income
runs through FY2025. Reading each side at its own latest built an interest
coverage out of FY2025 EBIT over FY2023 interest — a number for a year that
never happened.

`DATA_POLICY.md`: "Do not mix annual, trailing-twelve-month, and quarterly
periods without labeling and reconciliation." `SOURCE_HIERARCHY.md` opens
conflict resolution with "Verify period, currency, scale, split adjustment,
and continuing-operations treatment."
"""

import pytest

from wbj.quick import _at_period


def _s(*pairs) -> list[dict]:
    return [{"end": end, "val": val} for end, val in pairs]


#: AAPL's real shape: interest stops two years before operating income.
_INTEREST = _s(("2022-09-24", 2_931_000_000.0), ("2023-09-30", 3_933_000_000.0))


def test_a_period_with_no_value_reads_as_absent_not_as_an_older_year():
    """The AAPL case. FY2025 has no interest figure, so the answer is None —
    never FY2023's 3.9B wearing FY2025's label."""
    assert _at_period(_INTEREST, "2025-09-27") is None


def test_the_matching_period_is_returned():
    assert _at_period(_INTEREST, "2023-09-30") == 3_933_000_000.0
    assert _at_period(_INTEREST, "2022-09-24") == 2_931_000_000.0


def test_a_missing_period_label_yields_absence():
    """Without a period to match on there is no aligned value to return."""
    assert _at_period(_INTEREST, None) is None


def test_an_empty_series_yields_absence():
    assert _at_period([], "2025-09-27") is None
    assert _at_period(None, "2025-09-27") is None


def test_the_latest_filed_value_wins_a_duplicated_period():
    """A 10-K restates what a 10-Q first reported; the series carries both in
    order, so the later entry is the one that stands."""
    dup = _s(("2024-09-28", 100.0), ("2024-09-28", 120.0))
    assert _at_period(dup, "2024-09-28") == 120.0


def test_alignment_does_not_break_a_company_that_reports_both_sides():
    """MSFT tags interest through the same year as operating income, so the
    fix must be invisible there — it withholds only what was never reported."""
    msft = _s(("2024-06-30", 2_935_000_000.0), ("2025-06-30", 2_385_000_000.0))
    assert _at_period(msft, "2025-06-30") == 2_385_000_000.0


def test_an_unreported_interest_expense_is_missing_not_meaningless():
    """AAPL's case from FY2024 on: it stopped tagging `InterestExpense` while
    carrying $112B of debt. NOT_MEANINGFUL would state as fact that the
    company has no interest cost. MISSING_DATA_POLICY.md step 2 -- "Is the
    source expected to report it? If yes but absent, use `MISSING`"."""
    from wbj.core.nullstates import NullState
    from wbj.quick import _interest_coverage

    v = _interest_coverage(130.0, None)
    assert v.is_null and v.state is NullState.MISSING


def test_a_reported_zero_leaves_the_ratio_undefined():
    """A genuine zero denominator is the other answer: the ratio does not
    exist. That is NOT_MEANINGFUL, and it must stay distinguishable."""
    from wbj.core.nullstates import NullState
    from wbj.quick import _interest_coverage

    v = _interest_coverage(130.0, 0.0)
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_a_reported_figure_still_computes():
    from wbj.quick import _interest_coverage

    assert _interest_coverage(130.0, 10.0).value == pytest.approx(13.0)


def test_an_absent_numerator_is_missing_too():
    """No operating income, no ratio -- and absence propagates rather than
    borrowing the zero branch's meaning."""
    from wbj.quick import _interest_coverage

    assert _interest_coverage(None, 10.0).is_null
