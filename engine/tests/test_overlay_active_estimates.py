"""`_active_estimate_count` must read the NEAREST forward year, not row zero.

FMP orders `analyst_estimates` newest-date-FIRST, and analyst coverage thins
out with distance: MSFT carries one EPS analyst on FY2030 against thirty-one
on FY2027. Reading `rows[0]` therefore reported near-zero coverage for the
most heavily covered names alive, which drove MKT-REVBR-011's ">=5 active
estimates" caveat (`03_market_analysis/FORMULAS.md`) to refuse the metric on
exactly the companies the rule was written to admit.

MKT-REVBR-011's window is "30d / 90d", so the estimate under revision is the
nearest fiscal period that has not ended yet.
"""

from wbj.overlay.from_packet import _active_estimate_count


class _FMP:
    def __init__(self, rows):
        self._rows = rows

    def analyst_estimates(self, ticker):
        return self._rows


#: Shaped like FMP's real payload: newest date first, coverage thinning out.
_MSFT = [
    {"date": "2030-06-30", "numAnalystsEps": 1},
    {"date": "2029-06-30", "numAnalystsEps": 4},
    {"date": "2028-06-30", "numAnalystsEps": 26},
    {"date": "2027-06-30", "numAnalystsEps": 31},
    {"date": "2026-06-30", "numAnalystsEps": 30},
]


def test_the_nearest_forward_year_wins_not_the_first_row():
    """The defect in one line: row zero says 1, the truth says 31."""
    assert _active_estimate_count(_FMP(_MSFT), "MSFT", today="2026-07-27") == 31


def test_periods_already_ended_are_skipped():
    """FY2026 ended in June; analysts are no longer revising it."""
    assert _active_estimate_count(_FMP(_MSFT), "MSFT", today="2026-07-27") != 30


def test_a_period_ending_today_still_counts():
    """The comparison is inclusive -- a period ending today has not yet passed
    out of the revision window."""
    assert _active_estimate_count(_FMP(_MSFT), "MSFT", today="2026-06-30") == 30


def test_no_forward_rows_yields_none_rather_than_a_stale_count():
    """MISSING_DATA_POLICY.md: the absence propagates. Falling back to the
    newest past year would gate a live metric on a period nobody is revising."""
    assert _active_estimate_count(_FMP(_MSFT), "MSFT", today="2031-01-01") is None


def test_a_provider_failure_is_absence_not_a_crash():
    class _Boom:
        def analyst_estimates(self, ticker):
            raise RuntimeError("403")

    assert _active_estimate_count(_Boom(), "X", today="2026-07-27") is None


def test_an_empty_payload_yields_none():
    assert _active_estimate_count(_FMP([]), "X", today="2026-07-27") is None
    assert _active_estimate_count(_FMP(None), "X", today="2026-07-27") is None


def test_rows_without_a_date_are_ignored_not_guessed_at():
    rows = [{"numAnalystsEps": 99}, {"date": "2027-01-31", "numAnalystsEps": 25}]
    assert _active_estimate_count(_FMP(rows), "X", today="2026-07-27") == 25


def test_a_missing_or_zero_count_is_absence_not_zero_coverage():
    """Zero would read as "no analysts", which is a claim; None is the honest
    "not reported"."""
    rows = [{"date": "2027-01-31", "numAnalystsEps": 0}]
    assert _active_estimate_count(_FMP(rows), "X", today="2026-07-27") is None
    rows = [{"date": "2027-01-31"}]
    assert _active_estimate_count(_FMP(rows), "X", today="2026-07-27") is None
