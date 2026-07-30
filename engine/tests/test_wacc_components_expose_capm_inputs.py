"""`wacc.components` must carry the inputs Victor's WACC rules are about.

`06_valuation_analysis/DECISION_RULES.md` states its WACC rules on the inputs,
not the outputs:

- "Risk-free rate, ERP, cash flows, and terminal growth use the same currency."
- "Use bottom-up beta from comparable operating businesses where possible."

The block published only `cost_of_equity`, `cost_of_debt` and `tax_rate`, so
the risk-free rate, the ERP and the beta behind that cost of equity appeared
nowhere in the output -- and neither rule could be checked by a reader. The
beta in particular is a registered formula pair (VAL-UBETA-009 unlevered /
VAL-LBETA-010 relevered) that the engine implements and the specialist never
surfaced.

The specialist uses the security's own levered beta, which is the rule's
fallback branch, not its preference. An assumption now says so: taking the
fallback silently reads as having followed the preference.
"""

import json
from pathlib import Path

import pytest

from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


@pytest.fixture
def overlay():
    return {"risk_free_rate": 0.042, "beta": 1.35, "erp": 0.045}


def test_capm_inputs_are_published(packet, overlay):
    c = val.run(packet, overlay).wacc.components
    for key in ("risk_free_rate", "beta", "erp"):
        assert key in c, f"{key} is a WACC rule input and must be inspectable"


def test_published_inputs_match_what_was_used(packet, overlay):
    c = val.run(packet, overlay).wacc.components
    assert c["risk_free_rate"] == pytest.approx(0.042)
    assert c["beta"] == pytest.approx(1.35)
    assert c["erp"] == pytest.approx(0.045)


def test_outputs_are_still_published(packet, overlay):
    """Adding inputs must not displace what was already there."""
    c = val.run(packet, overlay).wacc.components
    for key in ("cost_of_equity", "cost_of_debt", "tax_rate"):
        assert key in c


def test_cost_of_equity_reproduces_from_the_published_inputs(packet, overlay):
    """CAPM: ke = rf + beta * erp. Publishing inputs that don't reproduce the
    output would be worse than publishing none."""
    c = val.run(packet, overlay).wacc.components
    if c["cost_of_equity"] is None:
        pytest.skip("cost of equity unavailable in this fixture")
    expected = c["risk_free_rate"] + c["beta"] * c["erp"]
    assert c["cost_of_equity"] == pytest.approx(expected, rel=1e-6)


def test_the_fallback_beta_branch_is_disclosed(packet, overlay):
    """DECISION_RULES.md prefers a bottom-up beta; this is not one."""
    out = val.run(packet, overlay)
    disclosure = [a for a in out.assumptions if "bottom-up beta" in a]
    assert disclosure, "taking the fallback branch silently reads as the preference"
    assert "levered beta" in disclosure[0]


def test_missing_inputs_publish_none_not_absence(packet):
    """A null component still tells the reader the slot exists and is empty."""
    c = val.run(packet, {"risk_free_rate": None, "beta": None}).wacc.components
    assert "beta" in c and c["beta"] is None
