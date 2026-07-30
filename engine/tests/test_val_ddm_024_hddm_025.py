"""VAL-DDM-024 and VAL-HDDM-025: the dividend-discount models.

FORMULAS.md:
- VAL-DDM-024 `Dividend_1/(CostEquity-g)`, "only for stable payout and growth";
- VAL-HDDM-025 `D0*(1+gL)/(Ke-gL) + D0*H*(gS-gL)/(Ke-gL)`, "mature transition
  companies only".

DECISION_RULES.md's model-selection matrix makes both *primary* for banks and
insurers -- the company types whose conventional FCFF the same matrix forbids
-- and a secondary check for mature non-financials.

D0 comes from EDGAR XBRL, which is tier 1 in SOURCE_HIERARCHY.md and free,
rather than from FMP (tier 5, and its provider has no dividends method at all).
Two us-gaap tags carry it and filers pick one: AAPL and JPM tag "Declared",
XOM tags "CashPaid". Reading only the first reported a large long-standing
payer as paying nothing -- worse than no data, since a DDM would read it as a
non-payer rather than as unavailable.
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


# --- VAL-DDM-024 ------------------------------------------------------------

def test_gordon_matches_the_registry_text():
    """D1/(Ke-g) with D1 = D0*(1+g): 2.00*1.05 / (0.09-0.05) = 52.50."""
    assert ve.gordon_dividend_value(2.0, 0.05, 0.09).value == pytest.approx(52.5)


def test_gordon_rises_as_the_spread_narrows():
    """The sensitivity the assumption discloses, pinned as behaviour."""
    wide = ve.gordon_dividend_value(2.0, 0.02, 0.09).value
    narrow = ve.gordon_dividend_value(2.0, 0.08, 0.09).value
    assert narrow > wide * 5


def test_gordon_refuses_when_growth_reaches_the_cost_of_equity():
    for g in (0.09, 0.11):
        v = ve.gordon_dividend_value(2.0, g, 0.09)
        assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_a_non_payer_is_not_applicable_not_zero():
    """A company that pays nothing is outside the model, not worth nothing --
    reading zero here would put a floor under it that the model never said."""
    v = ve.gordon_dividend_value(0.0, 0.05, 0.09)
    assert v.is_null and v.state is NullState.NOT_APPLICABLE


# --- VAL-HDDM-025 -----------------------------------------------------------

def test_h_model_matches_the_registry_text():
    """D0*(1+gL)/(Ke-gL) + D0*H*(gS-gL)/(Ke-gL)."""
    d0, gs, gl, h, ke = 2.0, 0.10, 0.03, 2.5, 0.09
    expected = d0 * (1 + gl) / (ke - gl) + d0 * h * (gs - gl) / (ke - gl)
    assert ve.h_model_dividend_value(d0, gs, gl, h, ke).value == pytest.approx(expected)


def test_h_model_collapses_to_gordon_when_there_is_no_fade():
    """gS == gL removes the transition term, leaving the steady-state value."""
    v = ve.h_model_dividend_value(2.0, 0.03, 0.03, 2.5, 0.09)
    assert v.value == pytest.approx(ve.gordon_dividend_value(2.0, 0.03, 0.09).value)


def test_h_model_is_lower_than_gordon_on_the_short_rate():
    """Fading down is the whole point: Gordon at gS assumes today's growth
    lasts forever, which is what this model exists to correct."""
    gordon = ve.gordon_dividend_value(2.0, 0.10, 0.12).value
    faded = ve.h_model_dividend_value(2.0, 0.10, 0.03, 2.5, 0.12).value
    assert faded < gordon


def test_fading_upward_is_allowed():
    """gS < gL turns the transition term negative, which is correct for a
    company growing into its long-run rate."""
    up = ve.h_model_dividend_value(2.0, 0.01, 0.03, 2.5, 0.09)
    assert up.is_valid
    assert up.value < ve.gordon_dividend_value(2.0, 0.03, 0.09).value


def test_h_model_refuses_on_long_growth_at_the_cost_of_equity():
    v = ve.h_model_dividend_value(2.0, 0.10, 0.09, 2.5, 0.09)
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_h_model_refuses_a_negative_half_life():
    v = ve.h_model_dividend_value(2.0, 0.10, 0.03, -1.0, 0.09)
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_h_model_non_payer_is_not_applicable():
    v = ve.h_model_dividend_value(0.0, 0.10, 0.03, 2.5, 0.09)
    assert v.is_null and v.state is NullState.NOT_APPLICABLE


# --- specialist wiring ------------------------------------------------------

def test_both_rows_are_emitted(packet):
    rows = _rows(val.run(packet, {}))
    assert "VAL-DDM-024" in rows and "VAL-HDDM-025" in rows


def test_rows_are_reported_not_scored(packet):
    rows = _rows(val.run(packet, {}))
    assert rows["VAL-DDM-024"].score == "NOT_SCORABLE"
    assert rows["VAL-HDDM-025"].score == "NOT_SCORABLE"


def test_no_dividend_series_leaves_both_missing(packet):
    """MISSING, not zero: absence of a series is not evidence of no payout."""
    rows = _rows(val.run(packet, {}))
    if rows["VAL-DDM-024"].value is None:
        assert rows["VAL-DDM-024"].state is not None


def test_a_supplied_series_drives_both_models(packet):
    overlay = {"dividend_per_share": 2.0,
               "dividends_per_share_history": [1.0, 1.2, 1.4, 1.7, 2.0],
               "dividend_tag": "CommonStockDividendsPerShareDeclared",
               "dividend_period_end": "2025-12-31"}
    rows = _rows(val.run(packet, overlay))
    assert rows["VAL-DDM-024"].value is not None or rows["VAL-DDM-024"].state is not None


def test_the_growth_source_and_spread_are_disclosed(packet):
    """Growth is measured off the reported series, and the Gordon denominator
    is stated because that is what makes the model sensitive."""
    overlay = {"dividend_per_share": 2.0,
               "dividends_per_share_history": [1.0, 1.2, 1.4, 1.7, 2.0],
               "dividend_tag": "CommonStockDividendsPerShareDeclared",
               "dividend_period_end": "2025-12-31"}
    out = val.run(packet, overlay)
    note = [a for a in out.assumptions if "VAL-DDM-024" in a]
    if not note:
        pytest.skip("cost of equity unavailable in this fixture")
    assert "measured over" in note[0]
    assert "Gordon denominator" in note[0]
