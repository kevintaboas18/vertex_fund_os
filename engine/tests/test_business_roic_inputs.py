"""BUS-IC-012 / BUS-ROIC-013: what actually feeds the ROIC chain.

The formulas matched FORMULAS.md's algebra. The inputs did not match its
caveats. Two substitutions were running undeclared underneath every ROIC,
spread, EVA and moat-persistence number in the agent:

* BUS-IC-012 is "Debt + Equity - Excess cash" with the caveat "Reconcile
  to operating assets minus operating liabilities". The reconciliation
  was never run -- both endpoints were computed without the operating
  view -- and the `excess_cash` argument was filled with *total* cash,
  which moves NVDA's latest ROIC by about seven percentage points.
* the ROIC history fell back to the 21% statutory rate for any year
  without positive reported pretax income. `run()` disclosed that for the
  headline NOPAT but not for the history -- which is exactly what the
  wide-moat gate's "spread >=5pp in >=4/5 years" check reads. A
  loss-making biotech falls back in four years out of six.
"""

import pytest

from wbj.engines.valuation_engine import invested_capital
from wbj.specialists import business as bus


def _balance_sheet(**overrides):
    """A consistent balance sheet: total assets tie to liabilities plus
    equity, which is what makes the two invested-capital views comparable."""
    row = {
        "total_assets": 1000.0, "total_liabilities": 400.0, "total_equity": 600.0,
        "total_debt": 150.0, "cash": 200.0,
        "income_before_tax": 100.0, "income_tax_expense": 15.0, "ebit": 120.0,
    }
    row.update(overrides)
    return row


def test_the_two_invested_capital_views_agree_on_consistent_inputs():
    """`TA = TL + TE` makes the operating view equal the financing view
    once both strip the same cash. A construction that stripped cash plus
    investments from assets while the financing view subtracted only cash
    disagreed by 10-61% on every company tested -- a divergence that was
    an artefact of the definitions, not a fact about the company."""
    row = _balance_sheet()
    ops = bus.operating_view_inputs(row)
    assert ops is not None

    result = invested_capital(row["total_debt"], row["total_equity"], row["cash"],
                              operating_assets=ops[0], operating_liabilities=ops[1])
    assert result.financing_view.value == pytest.approx(550.0)   # 150 + 600 - 200
    assert result.operating_view.value == pytest.approx(550.0)   # 800 - 250
    assert result.reconciled is True


def test_minority_interest_is_the_gap_the_check_tolerates():
    """Real filings diverge by the claims that sit in equity but not in
    the operating base. KO and XOM differ by exactly their minority
    interest, 3.1% and 2.5% -- inside FORMULAS.md's 5% tolerance."""
    row = _balance_sheet(total_equity=620.0, total_liabilities=380.0)
    ops = bus.operating_view_inputs(row)
    result = invested_capital(row["total_debt"], row["total_equity"], row["cash"],
                              operating_assets=ops[0], operating_liabilities=ops[1])
    gap = abs(result.operating_view.value - result.financing_view.value)
    assert gap / result.financing_view.value < 0.05
    assert result.reconciled is True


def test_an_inconsistent_balance_sheet_trips_the_reconciliation():
    """The check has to be able to fail, or it is not a check."""
    row = _balance_sheet(total_assets=1400.0)   # no longer ties to TL + TE
    ops = bus.operating_view_inputs(row)
    result = invested_capital(row["total_debt"], row["total_equity"], row["cash"],
                              operating_assets=ops[0], operating_liabilities=ops[1])
    assert result.reconciled is False
    assert "INVESTED_CAPITAL_VIEWS_DIFFER_GT_5PCT" in result.warnings


def test_the_reconciliation_warning_reaches_the_metric():
    """A warning the engine raises and the specialist drops is a warning
    nobody sees."""
    bad = _balance_sheet(total_assets=1400.0)
    ops = bus.operating_view_inputs(bad)
    v = bus.average_invested_capital(
        bad["total_debt"], bad["total_equity"], bad["cash"],
        bad["total_debt"], bad["total_equity"], bad["cash"],
        operating_begin=ops, operating_end=ops)
    assert "INVESTED_CAPITAL_VIEWS_DIFFER_GT_5PCT" in v.warnings


def test_a_missing_balance_line_is_not_a_zero_balance():
    """Substituting zero for an absent total would silently reshape the
    operating view."""
    assert bus.operating_view_inputs({"total_liabilities": 1.0, "total_debt": 1.0}) is None
    assert bus.operating_view_inputs({"total_assets": 1.0, "total_debt": 1.0}) is None
    assert bus.operating_view_inputs({"total_assets": 1.0, "total_liabilities": 1.0}) is None


# --- BUS-ROIC-013: the statutory-rate substitution -------------------------


def test_a_reported_tax_rate_is_used_when_the_filing_has_one():
    row = _balance_sheet(income_before_tax=100.0, income_tax_expense=15.0)
    assert bus._has_reported_tax_rate(row) is True
    assert bus._tax_rate(row, 0.21) == pytest.approx(0.15)


@pytest.mark.parametrize("row", [
    {"income_before_tax": -50.0, "income_tax_expense": 5.0},   # loss-making
    {"income_before_tax": 0.0, "income_tax_expense": 5.0},
    {"income_before_tax": 100.0},                              # no tax line
    {},
])
def test_the_statutory_fallback_is_recognised_as_a_substitution(row):
    """`_has_reported_tax_rate` and `_tax_rate` must never disagree about
    which years were substituted, or the disclosure drifts from the
    number it describes."""
    assert bus._has_reported_tax_rate(row) is False
    assert bus._tax_rate(row, 0.21) == pytest.approx(0.21)


def test_substituted_tax_years_are_counted_for_disclosure():
    """The wide-moat gate reads this history, so the count has to travel
    with it."""
    def year(y, pretax):
        return {"fiscalYear": y, "ebit": 100.0, "total_debt": 150.0,
                "total_equity": 600.0, "cash": 200.0,
                "total_assets": 1000.0, "total_liabilities": 400.0,
                "income_before_tax": pretax, "income_tax_expense": 15.0}

    annual = [year(2021, 100.0), year(2022, 100.0), year(2023, -10.0),
              year(2024, -20.0), year(2025, 100.0)]
    _, _, substituted, _, _ = bus._roic_and_spread_history(annual, 0.09)
    # Year 0 is only a prior-year balance; the two loss years are the
    # substitutions.
    assert substituted == 2


def test_both_input_substitutions_cost_model_fit():
    """FORMULAS.md: "Record any proxy in `warnings` and reduce model-fit
    confidence." These two sat under every ROIC number in the agent."""
    from types import SimpleNamespace

    packet = SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter="default_nonfinancial"),
        staleness={"quarterly_fundamentals": "FRESH"},
    )
    clean = bus._category_confidence(1.0, packet, [])
    cash_only = bus._category_confidence(1.0, packet, [bus.PROXY_TOTAL_CASH_AS_EXCESS_CASH])
    both = bus._category_confidence(
        1.0, packet, [bus.PROXY_TOTAL_CASH_AS_EXCESS_CASH, bus.PROXY_STATUTORY_TAX_RATE])
    assert clean > cash_only > both


# --- DATA_POLICY.md staleness ---------------------------------------------


def _packet(**staleness):
    from types import SimpleNamespace

    base = {"quarterly_fundamentals": "FRESH", "peer_set": "FRESH",
            "daily_market": "FRESH", "consensus": "FRESH"}
    base.update(staleness)
    return SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter="default_nonfinancial"),
        staleness=base)


def test_a_stale_peer_set_costs_freshness_when_peers_are_used():
    """DATA_POLICY.md gives the peer set a 90-day rebuild threshold and
    says "Staleness affects confidence". Freshness read only
    `quarterly_fundamentals`, so a stale peer set cost nothing while its
    ROIC percentile carried half of competitive_position."""
    fresh = bus._freshness(_packet(), uses_peers=True)
    stale = bus._freshness(_packet(peer_set="STALE"), uses_peers=True)
    assert fresh == pytest.approx(100.0)
    assert stale == pytest.approx(75.0)


def test_a_stale_peer_set_is_irrelevant_when_no_peers_were_used():
    """Charging for an input the run never consulted would be as wrong as
    not charging for one it did."""
    assert bus._freshness(_packet(peer_set="STALE"), uses_peers=False) == pytest.approx(100.0)


def test_data_this_specialist_does_not_read_never_moves_its_freshness():
    """Business reads neither daily market data nor consensus."""
    noisy = _packet(daily_market="STALE", consensus="STALE")
    assert bus._freshness(noisy, uses_peers=True) == pytest.approx(100.0)


def test_stale_fundamentals_still_dominate():
    """The original single-key behaviour has to survive: fundamentals are
    the base of every ROIC number here."""
    both = bus._freshness(_packet(quarterly_fundamentals="STALE", peer_set="STALE"),
                          uses_peers=True)
    assert both == pytest.approx(50.0)
    assert bus._freshness(_packet(quarterly_fundamentals="STALE"),
                          uses_peers=False) == pytest.approx(50.0)


# --- CALCULATION_CONVENTIONS.md -------------------------------------------


def test_an_effective_tax_rate_outside_the_unit_interval_is_not_normalized():
    """CALCULATION_CONVENTIONS.md: "Clamp normalized tax rate to a
    defensible range only through an explicit industry/country adapter;
    never silently." The rate was clamped with min/max, which fired on
    fifteen fiscal years across ten companies checked — wherever a
    valuation-allowance release or R&D credit produced a negative
    effective rate, or a one-time charge pushed it over 100%."""
    benefit = {"income_before_tax": 100.0, "income_tax_expense": -6.3}
    over_100 = {"income_before_tax": 100.0, "income_tax_expense": 173.8}

    assert bus._reported_tax_rate(benefit) is None
    assert bus._reported_tax_rate(over_100) is None
    # Not silently bent into range — routed to the documented substitution.
    assert bus._has_reported_tax_rate(benefit) is False
    assert bus._tax_rate(benefit, 0.21) == pytest.approx(0.21)


def test_a_rate_inside_the_range_is_used_untouched():
    assert bus._reported_tax_rate(
        {"income_before_tax": 100.0, "income_tax_expense": 15.0}) == pytest.approx(0.15)
    # The boundaries themselves are usable rates, not substitutions.
    assert bus._reported_tax_rate(
        {"income_before_tax": 100.0, "income_tax_expense": 0.0}) == pytest.approx(0.0)
    assert bus._reported_tax_rate(
        {"income_before_tax": 100.0, "income_tax_expense": 100.0}) == pytest.approx(1.0)


def test_an_ending_balance_only_roic_is_computed_and_labelled():
    """CALCULATION_CONVENTIONS.md: "If only ending values exist, label the
    result END_BALANCE_PROXY and reduce confidence." Nothing implemented
    the second sentence, so a company with one year of balance-sheet
    history reported no ROIC at all."""
    v = bus.average_invested_capital(None, None, 0.0, 150.0, 600.0, 200.0)
    assert v.is_valid
    assert v.value == pytest.approx(550.0)          # 150 + 600 - 200
    assert bus.WARN_END_BALANCE_PROXY in v.warnings
    # "reduce confidence" — the per-row deduction does that half.
    assert bus._confidence_for(v) < bus._confidence_for(
        bus.average_invested_capital(150.0, 600.0, 200.0, 150.0, 600.0, 200.0))


def test_an_average_is_still_preferred_when_both_ends_exist():
    v = bus.average_invested_capital(100.0, 500.0, 100.0, 150.0, 600.0, 200.0)
    assert v.value == pytest.approx((500.0 + 550.0) / 2)
    assert bus.WARN_END_BALANCE_PROXY not in v.warnings


# --- the tax rule, shared across every specialist -------------------------


def test_no_specialist_clamps_the_tax_rate_silently():
    """CALCULATION_CONVENTIONS.md: "Clamp normalized tax rate to a
    defensible range only through an explicit industry/country adapter;
    never silently." Four modules each implemented the rule separately
    and identically wrong — `min(max(tax_expense / pretax, 0.0), 1.0)` in
    business, financial (twice), valuation and market. The reading now
    lives in `wbj.core.taxes` and every caller reads it."""
    import re
    from pathlib import Path

    specialists = Path(bus.__file__).parent
    offenders = []
    for path in specialists.glob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"min\(\s*max\(.*tax", line, re.I):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, "silent tax clamps remain: " + "; ".join(offenders)


def test_the_shared_reading_matches_what_business_had():
    """The centralised version must not quietly change behaviour."""
    from wbj.core import taxes

    assert taxes.reported_tax_rate(100.0, 15.0) == pytest.approx(0.15)
    assert taxes.reported_tax_rate(100.0, -6.3) is None        # tax benefit
    assert taxes.reported_tax_rate(100.0, 173.8) is None       # over 100%
    assert taxes.reported_tax_rate(-50.0, 5.0) is None         # loss-making
    assert taxes.reported_tax_rate(0.0, 5.0) is None
    assert taxes.reported_tax_rate(None, 5.0) is None
    assert taxes.tax_rate_or_statutory(100.0, -6.3) == pytest.approx(0.21)
    assert taxes.tax_rate_or_statutory(100.0, 15.0) == pytest.approx(0.15)
    # Boundaries are usable rates, not substitutions.
    assert taxes.reported_tax_rate(100.0, 0.0) == pytest.approx(0.0)
    assert taxes.reported_tax_rate(100.0, 100.0) == pytest.approx(1.0)
