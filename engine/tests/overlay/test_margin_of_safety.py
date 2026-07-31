"""risk's valuation-compression input, derived Victor's way.

risk/SCORING.md's valuation-compression row: "Use valuation-agent packet;
do not duplicate valuation score." Victor defines the same cross-agent shape
twice more -- financial's `wacc` ("valuation agent input packet") and
market's `roic_reinvestment` ("validated specialist packet") -- and the
codebase already resolves the first one by deriving the figure from the
COMMON packet with the shared engine, never by reading another specialist's
output. This follows that precedent, so ORCHESTRATION.md Phase 2 ("Run the
six agents independently") and CLAUDE.md ("Ningun agente ve ni altera el
score de otro") both hold: what crosses is a number, never a score.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wbj.core.nullstates import EvidenceClass, Value
from wbj.overlay.from_packet import _margin_of_safety_from_packet


def _packet(adapter="default_nonfinancial", price=100.0, **row_overrides):
    row = dict(
        calendarYear="2025", date="2025-12-31", revenue=1000.0, ebit=200.0,
        income_before_tax=190.0, income_tax_expense=40.0, cash=100.0,
        total_debt=200.0, total_equity=600.0, capex=-50.0, diluted_shares=100.0,
    )
    row.update(row_overrides)
    prior = {**row, "calendarYear": "2024", "date": "2024-12-31"}
    facts = {
        "price": Value.of(price, unit="usd_per_share", evidence_class=EvidenceClass.R),
        "revenue": Value.of(row["revenue"], unit="usd", evidence_class=EvidenceClass.R),
        "diluted_shares": Value.of(100.0, unit="shares", evidence_class=EvidenceClass.R),
        "cash": Value.of(row["cash"], unit="usd", evidence_class=EvidenceClass.R),
        "total_debt": Value.of(row["total_debt"], unit="usd", evidence_class=EvidenceClass.R),
    }
    return SimpleNamespace(
        fundamentals={"annual": [row, prior]},
        facts_table=facts,
        estimates={"risk_free_rate": 0.04},
        capital_structure={"beta": 1.2, "market_cap": 100.0 * 100.0,
                           "diluted_shares": 100.0, "total_debt": row["total_debt"]},
        analysis=SimpleNamespace(industry_adapter=adapter),
        market_data=SimpleNamespace(daily=[]),
    )


def test_a_model_replacing_adapter_is_refused():
    """valuation.py refuses a bank outright (ADAPTER_UNSUPPORTED) because
    INDUSTRY_ADAPTERS.md names a different primary model. This derivation is
    the same FCFF model, so it must refuse the same securities -- unguarded
    it priced JPM at a +831% margin of safety, an FCFF valuation of a bank
    whose 'debt' is deposits."""
    for adapter in ("banks", "insurers", "reits", "biotech"):
        assert _margin_of_safety_from_packet(_packet(adapter=adapter)) is None, adapter


def test_a_conventional_company_yields_a_ratio():
    mos = _margin_of_safety_from_packet(_packet())
    assert mos is not None
    assert -20.0 < mos < 1.0  # a ratio, not a percentage or a price


def test_a_higher_price_lowers_the_margin_of_safety():
    """VAL-MOS-040 = (Value - Price)/Value: price up, margin down."""
    cheap = _margin_of_safety_from_packet(_packet(price=50.0))
    dear = _margin_of_safety_from_packet(_packet(price=500.0))
    assert cheap is not None and dear is not None
    assert cheap > dear


def test_missing_inputs_abstain_rather_than_guess():
    assert _margin_of_safety_from_packet(_packet(revenue=0.0)) is None
    no_facts = _packet()
    no_facts.facts_table = {}
    no_facts.fundamentals = {"annual": []}
    assert _margin_of_safety_from_packet(no_facts) is None


def test_the_overlay_carries_it_for_risk():
    """risk.py reads `overlay["margin_of_safety"]`; the wiring must put it
    there under the exact key.

    Producer and consumer have to agree on the spelling, and a rename on
    either side fails silently: risk's `overlay.get(...)` just returns
    None, the metric goes MISSING and the valuation-compression dimension
    stops scoring, with no error raised anywhere. So run the producer and
    read the key back. (The consumer half is covered by
    `tests/specialists/test_risk.py`, which feeds risk the same key.)
    """
    from wbj.config import Settings
    from wbj.overlay.from_packet import build_overlay

    packet = _packet()
    overlay = build_overlay(packet, Settings())

    expected = _margin_of_safety_from_packet(packet)
    assert expected is not None
    assert overlay["margin_of_safety"] == pytest.approx(expected)
