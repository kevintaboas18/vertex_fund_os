"""MKT-REVBR-011 — the ">=5" gate counts analysts, not revisions.

`03_market_analysis/FORMULAS.md`:

| MKT-REVBR-011 | Positive revision breadth | Number of upward estimate
  revisions / Total revisions | ... | Minimum 5 active estimates; otherwise
  low confidence. |

Two different quantities appear in that one row. The FORMULA divides
revisions by revisions. The CAVEAT gates on *active estimates* — the analyst
coverage count. A stock can have eight analysts covering it of whom four
revised in the window, and Victor's rule admits that: coverage is eight.

The engine had applied the gate to the revision total, which refused
MKT-REVBR-011 on any company where fewer than five analysts happened to move
in the window — withholding a metric the methodology allows and dropping the
`earnings_and_revenue_revisions` dimension below SCORING_ENGINE.md's 70%
reweight threshold, so it scored nothing at all.
"""

import pytest

from wbj.core.nullstates import NullState
from wbj.specialists.market import revision_breadth


def test_four_revisions_score_when_eight_analysts_cover_the_name():
    """The AAPL case that exposed the defect: 4/4 upward, 8 active estimates.

    Coverage clears Victor's minimum, so the metric is scorable even though
    only four analysts revised."""
    v = revision_breadth(4, 4, active_estimates=8)
    assert not v.is_null
    assert v.value == pytest.approx(1.0)


def test_the_gate_still_refuses_genuinely_thin_coverage():
    """MSFT returned a single active estimate. Below five, Victor's caveat
    applies and the metric must stay unscored -- the fix widens what counts
    as coverage, it does not remove the floor."""
    v = revision_breadth(1, 1, active_estimates=1)
    assert v.is_null
    assert v.state is NullState.NOT_SCORABLE


def test_exactly_five_active_estimates_passes():
    """"Minimum 5" is inclusive; XOM sits exactly on it."""
    v = revision_breadth(1, 4, active_estimates=5)
    assert not v.is_null
    assert v.value == pytest.approx(0.25)


def test_four_active_estimates_fails():
    v = revision_breadth(4, 4, active_estimates=4)
    assert v.is_null


def test_the_ratio_is_upward_over_total_revisions_not_over_coverage():
    """The denominator stays what the formula names -- total *revisions*.
    Dividing by the analyst count instead would understate breadth on every
    name where some analysts did not move."""
    v = revision_breadth(3, 4, active_estimates=10)
    assert v.value == pytest.approx(0.75), "denominator must be revisions, not coverage"


def test_without_a_coverage_count_the_revision_total_stands_in():
    """Some providers serve revision counts but no analyst count. Falling
    back to the revision total is the conservative direction: it can only
    withhold a score, never grant one the coverage does not support."""
    assert revision_breadth(4, 4).is_null
    assert not revision_breadth(5, 6).is_null


def test_the_null_note_names_active_estimates_so_the_reader_can_act():
    """INSTITUTIONAL_VALUATION_ENGINE.md wants outputs another analyst can
    challenge. A note saying "needs 5 revisions" would send that analyst
    looking for the wrong number."""
    v = revision_breadth(1, 1, active_estimates=2)
    text = " ".join(v.warnings or [])
    assert "ACTIVE_ESTIMATES" in text
    assert "2" in text, "the warning must report the coverage actually found"
