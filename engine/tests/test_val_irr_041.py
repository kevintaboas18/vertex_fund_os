"""VAL-IRR-041: implied investor IRR.

FORMULAS.md: "IRR of purchase price, forecast distributions/buybacks *if
modeled*, and terminal per-share value", frequency "scenario", with the note
"terminal value and holding period disclosed".

The "if modeled" clause is what makes this implementable without a dividend
forecast -- and what makes the disclosure mandatory: with no distributions the
series is the two endpoints alone, so the result is a price-appreciation-only
IRR that understates total return for any company that pays out. A reader who
assumed dividends were included would overstate the case.
"""

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.engines import valuation_engine as ve
from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def test_doubling_over_five_years_is_about_fifteen_percent():
    """100 -> 200 in 5y: 2^(1/5) - 1 = 14.87%."""
    v = ve.implied_investor_irr(100.0, 200.0, 5)
    assert v.value == pytest.approx(0.148698, abs=1e-5)
    assert v.unit == "pct"


def test_flat_price_with_no_distributions_is_zero():
    assert ve.implied_investor_irr(100.0, 100.0, 5).value == pytest.approx(0.0, abs=1e-9)


def test_a_terminal_value_below_price_gives_a_negative_irr():
    assert ve.implied_investor_irr(100.0, 50.0, 5).value < 0


def test_distributions_raise_the_irr_above_appreciation_alone():
    """This is exactly why the no-distributions case must be disclosed."""
    without = ve.implied_investor_irr(100.0, 150.0, 5).value
    with_divs = ve.implied_investor_irr(100.0, 150.0, 5, [3.0] * 5).value
    assert with_divs > without


def test_distributions_are_discounted_by_arrival_year():
    """A payout received early is worth more than the same payout late."""
    early = ve.implied_investor_irr(100.0, 150.0, 5, [10.0, 0.0, 0.0, 0.0, 0.0]).value
    late = ve.implied_investor_irr(100.0, 150.0, 5, [0.0, 0.0, 0.0, 0.0, 10.0]).value
    assert early > late


def test_the_solved_rate_zeroes_the_npv():
    """The definition, checked directly rather than against a closed form."""
    price, terminal, years, dists = 100.0, 180.0, 4, [2.0, 2.0, 3.0, 3.0]
    r = ve.implied_investor_irr(price, terminal, years, dists).value
    flows = list(dists)
    flows[-1] += terminal
    npv = -price + sum(cf / (1 + r) ** (t + 1) for t, cf in enumerate(flows))
    assert npv == pytest.approx(0.0, abs=1e-6)


def test_non_positive_price_refuses():
    v = ve.implied_investor_irr(0.0, 150.0, 5)
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_non_positive_holding_period_refuses():
    v = ve.implied_investor_irr(100.0, 150.0, 0)
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_total_loss_refuses_rather_than_returning_minus_one():
    """-100% exactly reads better as a null than as a rate."""
    v = ve.implied_investor_irr(100.0, 0.0, 5)
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_distribution_series_must_match_the_holding_period():
    v = ve.implied_investor_irr(100.0, 150.0, 5, [1.0, 1.0])
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_row_is_emitted_by_the_specialist(packet):
    rows = {m.metric_id: m for m in val.run(packet, {}).metrics}
    assert "VAL-IRR-041" in rows
    assert rows["VAL-IRR-041"].unit == "pct"


def test_row_is_reported_not_scored(packet):
    """SCORING.md gives VAL-IRR-041 no dimension."""
    rows = {m.metric_id: m for m in val.run(packet, {}).metrics}
    assert rows["VAL-IRR-041"].score == "NOT_SCORABLE"


def test_holding_period_and_missing_distributions_are_disclosed(packet):
    """The registry's own note: "terminal value and holding period disclosed"."""
    out = val.run(packet, {})
    rows = {m.metric_id: m for m in out.metrics}
    if rows["VAL-IRR-041"].value is None:
        pytest.skip("scenarios unavailable in this fixture")
    note = [a for a in out.assumptions if "VAL-IRR-041" in a]
    assert note, "an IRR with an undisclosed horizon is not interpretable"
    assert "holding period" in note[0]
    assert "price-appreciation-only" in note[0]


def test_all_three_scenarios_are_disclosed(packet):
    """FORMULAS.md frequency is "scenario", not "one scenario"."""
    out = val.run(packet, {})
    if {m.metric_id: m for m in out.metrics}["VAL-IRR-041"].value is None:
        pytest.skip("scenarios unavailable in this fixture")
    note = next(a for a in out.assumptions if "VAL-IRR-041" in a)
    for name in ("bear", "base", "bull"):
        assert name in note
