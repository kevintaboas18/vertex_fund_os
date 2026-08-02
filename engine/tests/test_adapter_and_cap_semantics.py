"""Two things a cross-check against Victor's own suite turned up.

Running HIS 36 test files against THIS engine left 30 failures. Most were
explained divergences -- language (his tests expect Spanish narratives),
parameter-qualified cache keys from the engine port, adapters this copy
now implements rather than refusing. Two were real.
"""

from __future__ import annotations

import pytest


# ===========================================================================
# A-06: an adapter nobody classified must not be priced by the default model
# ===========================================================================


def test_an_unrecognised_adapter_is_not_valued_by_the_conventional_model():
    """`industry_adapter="bank_adapter"` -- Victor's own spelling in
    VAL-T010, where this engine says "banks" -- fell through every branch
    and produced `primary=['FCFF_DCF', 'ECONOMIC_PROFIT']` for a BANK: the
    exact model DECISION_RULES.md's selection matrix bars for one.

    An adapter no set classifies is one nobody has checked the conventional
    formulas against. `business.py` already floors its model-fit confidence
    on that test; valuation is where the same unknown does the most damage,
    since it prices the company."""
    from wbj.specialists import valuation as val

    out = val.run(_packet(industry_adapter="bank_adapter"))
    assert out.model_selection.primary == []
    assert "FCFF_DCF" in (out.model_selection.rejected or [])
    assert "ADAPTER_MODELS_NOT_REGISTERED" in out.mandatory_flags


@pytest.mark.parametrize("adapter", ["", "typo_nonfinancial", "banks_v2",
                                     "REITS", "default-nonfinancial"])
def test_any_unclassified_spelling_is_refused(adapter):
    """Renames, typos and case changes all land in the same place."""
    from wbj.specialists import valuation as val

    out = val.run(_packet(industry_adapter=adapter))
    assert out.model_selection.primary == []


def test_the_classified_adapters_still_run_their_own_models():
    """The guard must not swallow the adapters that ARE implemented."""
    from wbj.specialists import valuation as val

    assert val.run(_packet("default_nonfinancial")).model_selection.primary == \
        ["FCFF_DCF", "ECONOMIC_PROFIT"]
    assert val.run(_packet("banks")).model_selection.primary == \
        ["RESIDUAL_INCOME", "DDM"]


def test_the_classifier_covers_every_adapter_the_builder_can_emit():
    """A guard that refused an adapter the packet builder actually assigns
    would take a real company offline."""
    from wbj.core.adapters import is_classified
    from wbj.packet.builder import _ADAPTER_BY_INDUSTRY, _ADAPTER_BY_SECTOR

    for _, adapter in (*_ADAPTER_BY_INDUSTRY, *_ADAPTER_BY_SECTOR):
        assert is_classified(adapter), adapter
    assert is_classified("default_nonfinancial")
    assert not is_classified("bank_adapter")
    assert not is_classified(None)


def _packet(industry_adapter: str):
    from datetime import datetime, timezone

    from wbj.core.nullstates import EvidenceClass, Value
    from wbj.schemas.packet import AnalysisMeta, MarketData, Packet, Security

    row = dict(fiscalYear="2025", date="2025-12-31", filingDate="2026-02-20",
               revenue=1000.0, gross_profit=600.0, ebit=200.0, net_income=150.0,
               operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
               total_debt=200.0, total_equity=600.0, cash=100.0,
               total_assets=1000.0, total_liabilities=400.0,
               income_before_tax=190.0, income_tax_expense=40.0,
               diluted_shares=100.0, stock_based_compensation=10.0,
               changeInWorkingCapital=-15.0)
    prior = dict(row, fiscalYear="2024", date="2024-12-31")
    facts = {k: Value.of(v, unit=u, evidence_class=EvidenceClass.R) for k, v, u in
             [("price", 20.0, "usd_per_share"), ("diluted_shares", 100.0, "shares"),
              ("cash", 100.0, "usd"), ("total_debt", 200.0, "usd"),
              ("revenue", 1000.0, "usd")]}
    return Packet(
        security=Security(ticker="TEST", exchange="NASDAQ",
                          security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter=industry_adapter),
        fundamentals={"annual": [row, prior], "quarterly": []},
        market_data=MarketData(),
        estimates={"risk_free_rate": 0.04, "peers": [], "fmp_analyst_estimates": []},
        capital_structure={"beta": 1.2, "market_cap": 2000.0, "cash": 100.0,
                           "total_debt": 200.0, "diluted_shares": 100.0},
        facts_table=facts, staleness={})


# ===========================================================================
# A-07: a cap is an upper bound, not a band you must sit inside
# ===========================================================================


def test_a_position_below_the_range_does_not_breach_a_maximum():
    """Kevin.md: "Máximo por posición individual: 20% - 30% del capital."

    `profile_fit` tested `lo <= pos <= hi`, so a 10% position reported
    `within_position_cap = False` -- sizing conservatively read as breaking
    a maximum. Latent under the old (0.05, 0.20) default, where nearly any
    real position cleared 5%."""
    from wbj.specialists.risk import PROFILE, profile_fit

    lo, hi = PROFILE["max_position_pct"]
    half = profile_fit(lo / 2.0)
    assert half["within_position_cap"] is True
    assert half["below_intended_sizing"] is True


@pytest.mark.parametrize("fraction,within", [
    (0.0, True), (0.5, True), (0.999, True), (1.0, True), (1.5, False),
])
def test_the_breach_test_is_against_the_top_of_the_range(fraction, within):
    from wbj.specialists.risk import PROFILE, profile_fit

    _, hi = PROFILE["max_position_pct"]
    assert profile_fit(hi * fraction)["within_position_cap"] is within


def test_sitting_exactly_on_the_cap_is_allowed():
    """A maximum includes its own value."""
    from wbj.specialists.risk import PROFILE, profile_fit

    _, hi = PROFILE["max_position_pct"]
    assert profile_fit(hi)["within_position_cap"] is True


def test_no_position_yields_no_verdict():
    from wbj.specialists.risk import profile_fit

    fit = profile_fit(None)
    assert fit["within_position_cap"] is None
    assert fit["below_intended_sizing"] is None
