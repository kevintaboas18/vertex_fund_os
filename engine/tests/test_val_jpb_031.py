"""VAL-JPB-031: justified price-to-book.

FORMULAS.md: `(ROE - g) / (CostEquity - g)`, "stable model", "requires stable
ROE, payout, and g < Ke".

It reads as one idea: a business earning exactly its cost of equity is worth
book, and the premium or discount to book is the spread between what equity
earns and what that equity costs. DECISION_RULES.md's model-selection matrix
names "P/B vs ROE" as the secondary check for banks and insurers -- this
formula in words -- which is why it exists alongside VAL-JPE-032 rather than
being redundant with it.

Reported, not scored: SCORING.md's dimensions name VAL-PEG-028/JPE-032/
JEVS-033 and VAL-REL-034/ZHIST-035, and 031 falls in the gap between them.
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


def _rows(out):
    return {m.metric_id: m for m in out.metrics}


def test_formula_matches_the_registry_text():
    """(0.20 - 0.04) / (0.10 - 0.04) = 2.667x."""
    v = ve.justified_pb(0.20, 0.04, 0.10)
    assert v.value == pytest.approx(0.16 / 0.06)
    assert v.unit == "x"


def test_earning_exactly_the_cost_of_equity_is_worth_book():
    """The anchor the whole formula is built around: ROE == Ke -> 1.0x."""
    assert ve.justified_pb(0.10, 0.04, 0.10).value == pytest.approx(1.0)


def test_earning_below_the_cost_of_equity_justifies_a_discount():
    assert ve.justified_pb(0.06, 0.04, 0.10).value < 1.0


def test_earning_above_the_cost_of_equity_justifies_a_premium():
    assert ve.justified_pb(0.25, 0.04, 0.10).value > 1.0


def test_a_higher_roe_justifies_a_higher_multiple():
    assert ve.justified_pb(0.25, 0.04, 0.10).value > ve.justified_pb(0.15, 0.04, 0.10).value


def test_growth_at_or_above_the_cost_of_equity_refuses():
    """FORMULAS.md's own condition, g < Ke. The denominator turns zero or
    negative and a negative multiple is arithmetic, not a valuation."""
    for g in (0.10, 0.12):
        v = ve.justified_pb(0.20, g, 0.10)
        assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_negative_roe_refuses():
    """It would return a negative multiple, which reads like a cheap one."""
    v = ve.justified_pb(-0.05, 0.02, 0.10)
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_zero_roe_is_a_deep_discount_not_a_refusal():
    """Unlike VAL-JPE-032, ROE appears only in the numerator here, so zero is
    computable: it justifies a discount, not an undefined multiple."""
    v = ve.justified_pb(0.0, 0.02, 0.10)
    assert v.is_valid and v.value < 0


def test_row_is_emitted(packet):
    rows = _rows(val.run(packet, {}))
    assert "VAL-JPB-031" in rows
    assert rows["VAL-JPB-031"].unit == "x"


def test_row_is_reported_not_scored(packet):
    assert _rows(val.run(packet, {}))["VAL-JPB-031"].score == "NOT_SCORABLE"


def test_the_market_comparison_is_disclosed(packet):
    """A justified multiple with nothing to compare it against is inert."""
    out = val.run(packet, {})
    if _rows(out)["VAL-JPB-031"].value is None:
        pytest.skip("ROE or cost of equity unavailable in this fixture")
    note = [a for a in out.assumptions if "VAL-JPB-031" in a]
    assert note
    assert "trades it at" in note[0]
    assert "stable ROE" in note[0]


def test_the_scope_is_disclosed_for_a_non_financial(packet):
    """DECISION_RULES.md's matrix assigns P/B vs ROE to banks and insurers.
    It matters most where buybacks have compressed book equity: AAPL's 151.9%
    ROE sits on equity its repurchases shrank, so 134.92x is arithmetically
    right and economically weightless. Shown without a scope note, a reader
    takes it for a valuation signal."""
    out = val.run(packet, {})
    if _rows(out)["VAL-JPB-031"].value is None:
        pytest.skip("ROE or cost of equity unavailable in this fixture")
    note = next(a for a in out.assumptions if "VAL-JPB-031" in a)
    if packet.analysis.industry_adapter in ("banks", "insurers"):
        assert "Scope:" not in note
    else:
        assert "banks and insurers" in note
        assert "not a valuation check" in note


def test_the_equity_base_is_shown_so_an_extreme_roe_is_legible(packet):
    """151.9% is only interpretable next to the equity it was earned on."""
    out = val.run(packet, {})
    if _rows(out)["VAL-JPB-031"].value is None:
        pytest.skip("ROE or cost of equity unavailable in this fixture")
    assert "book equity" in next(a for a in out.assumptions if "VAL-JPB-031" in a)
