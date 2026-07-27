"""Banks and insurers get a valuation, on the models their matrix assigns.

DECISION_RULES.md's model-selection matrix, "Bank / insurer": primary residual
income / excess return / DDM, secondary P/B vs ROE, avoid enterprise DCF and
EV/EBITDA. INDUSTRY_ADAPTERS.md: "do not use enterprise-value/EBITDA,
net-debt/EBITDA, or conventional FCFF ... prefer excess-return or
dividend-discount valuation."

The module used to refuse both adapters outright, which was the honest answer
while none of those models existed. VAL-RIV-023 was already implemented and
VAL-DDM-024/VAL-HDDM-025 now are, so the refusal became the thing standing in
the way of Victor's own VAL-T010 ("bank selected -> use residual-income/
excess-return adapter").

What follows from barring the FCFF dimensions is Victor's rule, not a scheme
invented here: SCORING_ENGINE.md says a category whose valid dimension weight
is below 70% is INCOMPLETE, and that an incomplete category "may be displayed
but cannot pass a profile gate". REITs and biotech stay refused -- their
primary models (NAV/AFFO/cap rates; risk-adjusted NPV) are not implemented and
FORMULAS.md does not register them.
"""

import json
from pathlib import Path

import pytest

from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

_DIVIDENDS = {"dividend_per_share": 5.80,
              "dividends_per_share_history": [3.4, 3.6, 3.8, 4.0, 4.1, 4.8, 5.8],
              "dividend_tag": "CommonStockDividendsPerShareDeclared",
              "dividend_period_end": "2025-12-31",
              "risk_free_rate": 0.042, "beta": 1.1}


def _bank(adapter="banks") -> Packet:
    data = json.loads(_FIXTURE.read_text())
    data["analysis"]["industry_adapter"] = adapter
    return Packet.model_validate(data)


def _rows(out):
    return {m.metric_id: m for m in out.metrics}


@pytest.mark.parametrize("adapter", ["banks", "insurers"])
def test_a_financial_is_valued_not_refused(adapter):
    out = val.run(_bank(adapter), _DIVIDENDS)
    assert "ADAPTER_UNSUPPORTED" not in out.mandatory_flags
    assert "ADAPTER_FINANCIAL_MODELS" in out.mandatory_flags


def test_the_matrix_models_are_the_primary_ones():
    out = val.run(_bank(), _DIVIDENDS)
    assert out.model_selection.primary == ["RESIDUAL_INCOME", "DDM"]


def test_the_barred_models_are_rejected():
    """INDUSTRY_ADAPTERS.md bars all three by name."""
    rejected = val.run(_bank(), _DIVIDENDS).model_selection.rejected
    for barred in ("FCFF_DCF", "EV_EBITDA", "EV_SALES"):
        assert barred in rejected


def test_no_fcff_row_is_emitted_for_a_bank():
    """The prohibition enforced, not just declared."""
    rows = _rows(val.run(_bank(), _DIVIDENDS))
    for fcff_id in ("VAL-FCFF-005", "VAL-JEVS-033", "VAL-RDCF-027"):
        assert fcff_id not in rows


def test_the_matrix_rows_are_emitted():
    rows = _rows(val.run(_bank(), _DIVIDENDS))
    for metric_id in ("VAL-RI-022", "VAL-RIV-023", "VAL-DDM-024",
                      "VAL-HDDM-025", "VAL-JPB-031"):
        assert metric_id in rows


def test_the_category_is_incomplete_and_gate_ineligible():
    """SCORING_ENGINE.md: below 70% valid dimension weight is INCOMPLETE, and
    an incomplete category cannot pass a profile gate."""
    out = val.run(_bank(), _DIVIDENDS)
    assert out.coverage < 0.70
    assert out.status == "INCOMPLETE"
    assert "CATEGORY_INCOMPLETE_GATE_INELIGIBLE" in out.mandatory_flags


def test_the_barred_dimensions_carry_no_weight_rather_than_a_zero_score():
    """SCORING_ENGINE.md: "do not assign 5/10 to missing evidence" -- and a
    barred model is not a failed one, so it carries no weight at all."""
    out = val.run(_bank(), _DIVIDENDS)
    by_name = {d.name: d for d in out.dimensions}
    for barred in (val.DIM_MULTIPLES, val.DIM_HIST_PEER, val.DIM_CF_YIELD):
        assert by_name[barred].metric_scores == []


def test_cost_of_equity_is_published_without_a_wacc():
    """A bank's liabilities are its raw material, not its financing."""
    out = val.run(_bank(), _DIVIDENDS)
    assert out.wacc.value is None
    assert out.wacc.components["cost_of_equity"] is not None


@pytest.mark.parametrize("adapter", ["biotech"])
def test_adapters_without_registered_models_produce_no_value(adapter):
    """Their primary models are names in the matrix that no FORMULAS.md
    registers, so there is nothing to run in place of the barred DCF."""
    out = val.run(_bank(adapter), _DIVIDENDS)
    assert "ADAPTER_MODELS_NOT_REGISTERED" in out.mandatory_flags
    assert out.category.awarded_points == 0.0


def test_a_recovery_cagr_is_disclosed_not_smuggled_in():
    """JPM's reported series runs 0.20 -> 5.80: a 23.4% CAGR whose second step
    is 0.20 -> 1.00, a recovery from the post-2009 cut rather than a rate
    anything compounds at. FORMULAS.md admits these models only for stable
    payout and growth, so the spread is shown."""
    overlay = {**_DIVIDENDS,
               "dividends_per_share_history": [0.2, 0.2, 1.0, 1.2, 1.44, 5.8]}
    out = val.run(_bank(), overlay)
    note = [a for a in out.assumptions if "Year-over-year dividend growth" in a]
    assert note, "a CAGR across a recovery must not be presented bare"
    assert "stable payout" in note[0]


def test_the_scoring_rule_is_named_in_the_output():
    out = val.run(_bank(), _DIVIDENDS)
    assert any("INCOMPLETE" in a and "70%" in a for a in out.assumptions)
