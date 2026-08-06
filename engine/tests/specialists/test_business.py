"""Tests for `wbj.specialists.business` (Task 15): BUS-001..030, the five
weighted dimensions, mandatory flags/caps, and `run()` against the NVDA
golden fixture.

Sources of truth: `Cerebro/01_business_analysis/{FORMULAS,SCORING,
DECISION_RULES,VALIDATION_TESTS}.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.core.scoring import Category
from wbj.schemas.packet import AnalysisMeta, MarketData, Packet, Security
import wbj.specialists.business as bus

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def nvda_packet() -> Packet:
    data = json.loads(_FIXTURE.read_text())
    return Packet.model_validate(data)


def _minimal_packet(
    annual_rows: list[dict],
    *,
    industry_adapter: str = "default_nonfinancial",
    security_type: str = "operating_company",
) -> Packet:
    return Packet(
        security=Security(
            ticker="TEST", exchange="NASDAQ", security_type=security_type,
            reporting_currency="USD", valuation_currency="USD",
        ),
        analysis=AnalysisMeta(
            knowledge_timestamp="2026-07-16T21:00:00+00:00", industry_adapter=industry_adapter,
        ),
        fundamentals={"annual": annual_rows, "quarterly": []},
        market_data=MarketData(),
        estimates={},
        capital_structure={},
        facts_table={},
        staleness={},
    )


def _row(year: int, **overrides) -> dict:
    base = dict(
        calendarYear=str(year),
        date=f"{year}-12-31",
        period="FY",
        revenue=1000.0,
        cogs=600.0,
        gross_profit=400.0,
        ebit=200.0,
        income_before_tax=190.0,
        income_tax_expense=40.0,
        net_income=150.0,
        operating_cash_flow=180.0,
        capex=-50.0,
        cash=300.0,
        inventory=80.0,
        net_receivables=90.0,
        total_current_assets=500.0,
        total_current_liabilities=300.0,
        total_debt=200.0,
        total_equity=600.0,
        total_assets=1200.0,
        total_liabilities=600.0,
        diluted_shares=100.0,
        stock_based_compensation=10.0,
        common_stock_repurchased=0.0,
        dividends_paid=0.0,
        debt_repayment=0.0,
        acquisitions_net=0.0,
    )
    base.update(overrides)
    return base


def _five_year_rows(**per_year_overrides) -> list[dict]:
    """Five newest-first, boring, growing annual rows (2021..2025) with a
    stable 20% operating margin, for tests that need enough history for
    BUS-STAB-009/010, ROIC history, and dilution CAGR."""
    years = [2025, 2024, 2023, 2022, 2021]
    rows = []
    for i, y in enumerate(years):
        rev = 1000.0 + (len(years) - 1 - i) * -100.0  # oldest is smallest -> growth
        row = _row(
            y,
            revenue=rev,
            cogs=rev * 0.6,
            gross_profit=rev * 0.4,
            ebit=rev * 0.2,
            income_before_tax=rev * 0.19,
            income_tax_expense=rev * 0.19 * 0.21,
            net_income=rev * 0.15,
            operating_cash_flow=rev * 0.18,
            capex=-rev * 0.05,
            total_debt=200.0,
            total_equity=600.0 + (len(years) - 1 - i) * -20.0,
            diluted_shares=100.0 + i,  # oldest (i=4) has fewest shares -> mild dilution
        )
        rows.append(row)
    return rows


# ============================================================================
# BUS-VALIDATION_TESTS.md, encoded verbatim (BUS-T001..T008)
# ============================================================================


def test_BUS_T001_nopat_and_roic():
    """EBIT=100, tax=25%, invested capital=500 -> NOPAT=75; ROIC=15%."""
    nopat_v = bus.nopat(100.0, 0.25)
    assert nopat_v.value == pytest.approx(75.0)
    roic_v = bus.roic(nopat_v.value, 500.0)
    assert roic_v.value == pytest.approx(0.15)


def test_BUS_T002_spread_and_eva():
    """ROIC=15%, WACC=10%, IC=500 -> Spread=5pts; economic value=25."""
    spread_v = bus.spread(0.15, 0.10)
    assert spread_v.value == pytest.approx(0.05)
    eva_v = bus.eva(75.0, 0.10, 500.0)
    assert eva_v.value == pytest.approx(25.0)


def test_BUS_T003_margin_range_and_stability():
    """Five margins 20%,21%,19%,22%,20% -> range=3pts; stable signal."""
    margins = [0.20, 0.21, 0.19, 0.22, 0.20]
    range_v = bus.margin_range(margins)
    assert range_v.value == pytest.approx(0.03)
    assert bus.margin_range_is_stable(range_v.value) is True


def test_BUS_T004_largest_customer_concentration_red_flag():
    """Largest customer share=35% -> concentration red flag."""
    v = bus.largest_customer_concentration(35.0, 100.0)
    assert v.value == pytest.approx(0.35)
    assert bus.is_concentration_red_flag(v.value) is True


def test_BUS_T005_cumulative_fcf_conversion():
    """Five-year FCF=500, net income=450 -> FCF conversion=1.111x."""
    v = bus.cumulative_fcf_conversion(500.0, 450.0)
    assert v.value == pytest.approx(500.0 / 450.0, rel=1e-4)


def test_BUS_T006_revenue_cagr_nonpositive_begin_not_meaningful():
    """Beginning revenue <=0 for CAGR -> NOT_MEANINGFUL."""
    v = bus.revenue_cagr(100.0, 0.0, 3.0)
    assert v.is_null
    assert v.state == NullState.NOT_MEANINGFUL


def test_BUS_T007_roic_below_wacc_no_excellent_or_wide_moat():
    """ROIC<WACC with an otherwise-high score -> no Excellent/wide-moat label."""
    assert bus.value_destruction_triggered(roic_value=0.09, wacc_value=0.11) is True
    capped = bus.capped_verdict(
        9.5,
        value_destruction=True,
        excellent_gate_passes=True,
    )
    uncapped = bus.capped_verdict(
        9.5,
        value_destruction=False,
        excellent_gate_passes=True,
    )
    assert capped != "Excellent business"
    assert uncapped == "Excellent business"


def test_BUS_T008_missing_nrr_non_subscription_no_penalty(nvda_packet):
    """Missing NRR for a non-subscription industrial -> use adapter, no
    penalty for N/A: the customer-economics dimension is NOT_SCORABLE
    (excluded from coverage denominator via valid_weight), not scored 0."""
    out = bus.run(nvda_packet)
    customer_dim = next(d for d in out.dimensions if d.name == bus.DIM_CUSTOMER)
    assert customer_dim.valid_weight() == 0.0
    # a NOT_SCORABLE dimension contributes 0 points but the category still
    # reproduces from dimensions -- it must not be silently defaulted.
    recomputed = Category(name=bus.AGENT_ID, max_points=bus.MAX_POINTS, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)


# ============================================================================
# Individual formula behavior
# ============================================================================


def test_gross_and_operating_margin():
    assert bus.gross_margin(400.0, 1000.0).value == pytest.approx(0.40)
    assert bus.operating_margin(200.0, 1000.0).value == pytest.approx(0.20)


def test_margin_stability_stdev():
    v = bus.margin_stability([0.20, 0.21, 0.19, 0.22, 0.20])
    assert v.is_valid
    assert v.value >= 0.0


def test_customer_hhi_and_segment_hhi():
    hhi = bus.customer_hhi([0.5, 0.3, 0.2])
    assert hhi.value == pytest.approx(0.25 + 0.09 + 0.04)
    seg = bus.segment_hhi([1.0])
    assert seg.value == pytest.approx(1.0)


def test_diluted_share_cagr_reuses_core_cagr():
    v = bus.diluted_share_cagr(110.0, 100.0, 5.0)
    assert v.is_valid
    assert v.value == pytest.approx((110.0 / 100.0) ** (1 / 5) - 1)


def test_incremental_roic_and_allocation_spread():
    iroic = bus.incremental_roic(50.0, 400.0)
    assert iroic.value == pytest.approx(0.125)
    alloc = bus.capital_allocation_spread(iroic.value, 0.10)
    assert alloc.value == pytest.approx(0.025)


def test_guidance_accuracy_clipped_to_unit_interval():
    v = bus.guidance_accuracy(actual=1.0, guidance_midpoint=1.0, floor=0.01)
    assert v.value == pytest.approx(1.0)
    v2 = bus.guidance_accuracy(actual=-5.0, guidance_midpoint=1.0, floor=0.01)
    assert v2.value == pytest.approx(0.0)


def test_net_and_gross_revenue_retention():
    nrr = bus.net_revenue_retention(begin=1000.0, expansion=150.0, contraction=50.0, churn=50.0)
    assert nrr.value == pytest.approx((1000.0 + 150.0 - 50.0 - 50.0) / 1000.0)
    grr = bus.gross_revenue_retention(begin=1000.0, contraction=50.0, churn=50.0)
    assert grr.value == pytest.approx((1000.0 - 50.0 - 50.0) / 1000.0)


def test_ltv_cac_and_payback():
    ltv = bus.customer_ltv(arpu=120.0, gross_margin_pct=0.7, customer_life_years=4.0)
    assert ltv.value == pytest.approx(120.0 * 0.7 * 4.0)
    cac = bus.customer_acquisition_cost(spend=10000.0, new_customers=100.0)
    assert cac.value == pytest.approx(100.0)
    ratio = bus.ltv_to_cac(ltv.value, cac.value)
    assert ratio.value == pytest.approx(ltv.value / cac.value)
    payback = bus.cac_payback_months(cac=100.0, monthly_arpu=10.0, gross_margin_pct=0.5)
    assert payback.value == pytest.approx(100.0 / (10.0 * 0.5))


def test_reinvestment_rate_and_sustainable_growth():
    reinv = bus.reinvestment_rate(net_capex=40.0, dnwc=10.0, rd_adjustment=0.0, nopat_value=100.0)
    assert reinv.value == pytest.approx(0.5)
    sg = bus.fundamental_growth(reinv.value, 0.15)
    assert sg.value == pytest.approx(0.075)


# ============================================================================
# Dimension caps (SCORING.md "Gate / cap" column) -- the apply_dimension_cap
# helper itself is now tested once in test_common.py; the tests below
# exercise business.py's *use* of it (moat/durability caps).
# ============================================================================


def test_moat_capped_at_6_without_positive_spread(nvda_packet):
    """No positive ROIC-WACC spread (e.g. WACC not supplied, or spread<=0)
    -> moat dimension score capped at 6/10."""
    out = bus.run(nvda_packet, overlay={"wacc": 0.50})  # deliberately huge WACC -> spread<=0
    moat_dim = next(d for d in out.dimensions if d.name == bus.DIM_MOAT)
    assert moat_dim.score10() <= 6.0 + 1e-9


def test_durability_capped_at_6_with_concentration_red_flag():
    rows = _five_year_rows()
    packet = _minimal_packet(rows)
    # Supply recurring_revenue so all three durability members are valid
    # (>=70% usable coverage) and the dimension itself is scorable, letting
    # this test isolate the concentration cap rather than a coverage gate.
    out = bus.run(
        packet,
        overlay={"wacc": 0.09, "largest_customer_share": 0.35, "recurring_revenue": 500.0},
    )
    assert "CONCENTRATION_RED_FLAG" in out.mandatory_flags
    durability_dim = next(d for d in out.dimensions if d.name == bus.DIM_DURABILITY)
    assert durability_dim.score10() <= 6.0 + 1e-9


def _durability(out):
    return next(d for d in out.dimensions if d.name == bus.DIM_DURABILITY).score10()


def test_durability_cap_lifts_when_contract_protection_is_quantified():
    """SCORING.md: "Largest customer >30% caps at 6 *unless contract
    protection is quantified*." A positive quantified protection lifts the
    cap. The CONCENTRATION_RED_FLAG (DECISION_RULES.md, >30%) has no such
    exception and still fires -- the flag is not the cap."""
    packet = _minimal_packet(_five_year_rows())
    base = {"wacc": 0.09, "largest_customer_share": 0.35, "recurring_revenue": 500.0}
    assert _durability(bus.run(packet, overlay=base)) <= 6.0 + 1e-9  # capped without it

    lifted = bus.run(packet, overlay={**base, "contract_protection": 0.80})
    assert _durability(lifted) > 6.0
    assert "CONCENTRATION_RED_FLAG" in lifted.mandatory_flags
    assert any("cap lifted through its exception" in a for a in lifted.assumptions)


def test_zero_or_absent_contract_protection_leaves_the_cap():
    """"Quantified" protection is a positive quantity; zero or absent is
    not protection and leaves the cap in place."""
    packet = _minimal_packet(_five_year_rows())
    base = {"wacc": 0.09, "largest_customer_share": 0.35, "recurring_revenue": 500.0}
    for overlay in (base, {**base, "contract_protection": 0.0}):
        assert _durability(bus.run(packet, overlay=overlay)) <= 6.0 + 1e-9


def test_stale_peer_set_penalizes_freshness_on_peer_margins_alone():
    """CONFIDENCE_ENGINE.md's freshness must decay for every staleness
    signal the run depended on. competitive_position scores on peer margins
    as well as peer ROIC, so a run resting on peer margins alone must still
    pay for a stale peer set (DATA_POLICY.md rebuilds it after 90 days).
    The signal is whether a peer comparison scored, not whether peer ROIC
    happened to be supplied."""
    peers = [0.10, 0.12, 0.14, 0.15, 0.16, 0.18, 0.20, 0.22, 0.24]  # >=8
    overlay = {"wacc": 0.09, "peer_operating_margin": peers}  # margins only, no peer_roic

    def confidence_with(peer_set_state):
        packet = _minimal_packet(_five_year_rows())
        packet.staleness = {"peer_set": peer_set_state}
        out = bus.run(packet, overlay=overlay)
        # The peer comparison actually scored (margins), so peers were used.
        comp = next(d for d in out.dimensions if d.name == bus.DIM_COMPETITIVE)
        assert comp.score10() is not None
        return out.category.confidence

    assert confidence_with("FRESH") > confidence_with("STALE")


def test_an_unclassified_adapter_is_not_scored_a_perfect_model_fit():
    """INDUSTRY_ADAPTERS.md: adapters.py floors an adapter no set
    classifies — "claiming a good fit for it would be an assertion without
    evidence." Business must not let an unknown adapter fall through to the
    additive (perfect) model_fit; it is floored like a model-replacing one."""
    def confidence_with(adapter):
        packet = _minimal_packet(_five_year_rows(), industry_adapter=adapter)
        return bus.run(packet, overlay={"wacc": 0.09}).category.confidence

    unknown = confidence_with("utilities")            # in no classification set
    additive = confidence_with("default_nonfinancial")
    replacing = confidence_with("banks")
    assert unknown < additive     # not scored a perfect model fit
    assert unknown == replacing   # floored, as a model-replacing adapter is


def test_a_distorted_tax_rate_substitution_is_disclosed_not_silent():
    """CALCULATION_CONVENTIONS.md: substitute the statutory rate "only when
    normalized cash tax data are unavailable and disclose the substitution."
    An effective rate outside [0, 1] — a valuation-allowance release, or a
    one-time charge above 100%, the single items normalization strips — is
    unavailable too. The headline NOPAT substituted 21% for it but disclosed
    only the missing-tax-line case, so a distorted rate was substituted
    silently."""
    def discloses(latest_year_row):
        rows = [latest_year_row] + [_row(y) for y in (2024, 2023, 2022, 2021)]
        out = bus.run(_minimal_packet(rows), overlay={"wacc": 0.09})
        return any("NOPAT-011" in a and "statutory" in a for a in out.assumptions)

    # 150% effective rate (above 100%) and a negative rate (a benefit) both
    # substitute and must both disclose.
    assert discloses(_row(2025, income_before_tax=100.0, income_tax_expense=150.0))
    assert discloses(_row(2025, income_before_tax=100.0, income_tax_expense=-20.0))
    # A valid in-range rate is not a substitution, so it is not disclosed.
    assert not discloses(_row(2025, income_before_tax=100.0, income_tax_expense=21.0))


def test_every_metric_row_carries_a_formula_id_and_version(nvda_packet):
    """QA_CHECKLIST.md: "Formula IDs and versions are present." Both — the
    validation check now fails a row missing either, not only a missing id."""
    out = bus.run(nvda_packet, {"wacc": 0.09})
    assert out.metrics
    for row in out.metrics:
        assert row.formula_id, f"{row.metric_id}: no formula_id"
        assert row.formula_version, f"{row.metric_id}: no formula_version"
    assert out.validation_tests.failed == 0


def test_the_working_capital_term_is_derived_from_the_balance_sheets(nvda_packet):
    """BUS-REINV-018's numerator is "(Net capex + change in non-cash working
    capital + capitalized R&D adjustment)". No packet carries a
    `changeInWorkingCapital` line, so the term fell to zero on every real
    company and the numerator measured net capex alone. MISSING_DATA_POLICY.md
    step 3 applies: calculable from validated components."""
    annual = bus._annual_rows(nvda_packet)
    nwc_end = bus.non_cash_working_capital(annual[-1])
    nwc_begin = bus.non_cash_working_capital(annual[-2])
    assert nwc_end is not None and nwc_begin is not None
    change = nwc_end - nwc_begin
    capex = abs(bus._num(annual[-1], "capex"))
    assert change > capex          # the term dropped was larger than the one kept

    out = bus.run(nvda_packet, overlay={"wacc": 0.09})
    reinv = next(m for m in out.metrics if m.metric_id == "BUS-REINV-018")
    nopat = next(m for m in out.metrics if m.metric_id == "BUS-NOPAT-011").value
    assert reinv.value == pytest.approx((capex + change) / nopat)
    assert any("calculated from the two reported balance sheets" in a
               for a in out.assumptions)


def test_the_fiscal_year_is_read_from_the_field_the_builder_emits(nvda_packet):
    """The builder passes FMP's `calendarYear` through and never emits
    `fiscalYear`, which business read. So on every real packet each metric's
    `period` degraded to "FY?" — OUTPUT_CONTRACT.md requires it "Explicit" —
    the source locator said "latest FY?", and BUS-STAB-009's recession-year
    drawdown had no years to match, disabling the wide-moat gate's
    condition-2 peer-resilience alternative."""
    out = bus.run(nvda_packet, overlay={"wacc": 0.09})
    periods = {r.period for r in out.metrics}
    assert not any("FY?" in (p or "") for p in periods), periods
    assert any((p or "").startswith("FY2") for p in periods), periods
    filings = [r.source for r in out.metrics if "annual-statements" in (r.source or "")]
    assert filings and "FY?" not in filings[0]

    # The drawdown now has years to match: a recession year inside the
    # reported window produces a figure instead of nothing.
    scored = bus.run(nvda_packet, overlay={"wacc": 0.09, "recession_years": [2023]})
    assert scored.margin_stability["recession_year_drawdown"] is not None


def test_eva_is_computed_on_average_invested_capital():
    """BUS-EVA-015 registers "(ROIC - WACC) * Average invested capital". It was
    computed on a freshly-built *beginning* invested capital — the valuation
    engine's VAL-EVA-020 base — so it disagreed with ROIC's own capital base."""
    rows = [_row(y, total_debt=200.0, total_equity=600.0, cash=300.0, ebit=200.0)
            for y in (2025, 2024)]
    out = bus.run(_minimal_packet(rows), overlay={"wacc": 0.09})
    ic = next(m for m in out.metrics if m.metric_id == "BUS-IC-012").value
    roic = next(m for m in out.metrics if m.metric_id == "BUS-ROIC-013").value
    eva = next(m for m in out.metrics if m.metric_id == "BUS-EVA-015").value
    # (ROIC - WACC) * average invested capital — the same IC ROIC was built on.
    assert eva == pytest.approx((roic - 0.09) * ic)


def test_recession_drawdown_separates_missing_inputs_from_no_recession():
    """MISSING_DATA_POLICY.md's decision tree: a missing input (no margins or
    no FRED recession calendar supplied) is MISSING — a source expected to
    report it but absent — while a history that genuinely does not span a
    recession is NOT_APPLICABLE. The two were conflated as NOT_APPLICABLE."""
    margins = [(2007, 0.20), (2008, 0.12), (2009, 0.10), (2010, 0.15)]
    # Calendar not supplied, or no margins: we lack an input -> MISSING.
    assert bus.recession_margin_drawdown(margins, []).state is NullState.MISSING
    assert bus.recession_margin_drawdown([], [2008, 2009]).state is NullState.MISSING
    # Both present, but the history does not span a recession -> NOT_APPLICABLE.
    assert bus.recession_margin_drawdown(
        [(2015, 0.20), (2016, 0.21)], [2008, 2009]).state is NullState.NOT_APPLICABLE
    # Both present and the history spans a recession -> the drawdown computes.
    assert bus.recession_margin_drawdown(margins, [2008, 2009]).value == pytest.approx(0.08)


def test_a_single_year_company_gets_an_ending_balance_roic():
    """CALCULATION_CONVENTIONS.md: "If only ending values exist, label the
    result END_BALANCE_PROXY and reduce confidence." average_invested_capital
    had the fallback, but the BUS-IC-012 call site gated on two years of balance
    sheet, so a recent listing with only the latest year lost invested capital
    and ROIC entirely rather than getting the ending-balance figure."""
    out = bus.run(_minimal_packet([_row(2025, total_debt=200.0, total_equity=600.0,
                                        cash=300.0, ebit=200.0)]), {"wacc": 0.09})
    ic = next(m for m in out.metrics if m.metric_id == "BUS-IC-012")
    roic = next(m for m in out.metrics if m.metric_id == "BUS-ROIC-013")
    assert ic.value is not None                        # computed, not MISSING
    assert "END_BALANCE_PROXY" in ic.warnings           # labelled
    assert roic.score != "NOT_SCORABLE"                # ROIC now scores
    assert "END_BALANCE_PROXY" in roic.warnings         # the return ratio carries it too
    # "reduce confidence" — the ending-balance IC row is less confident than a
    # two-year averaged one.
    ic_two = next(m for m in bus.run(_minimal_packet(
        [_row(y, total_debt=200.0, total_equity=600.0, cash=300.0, ebit=200.0)
         for y in (2025, 2024)]), {"wacc": 0.09}).metrics if m.metric_id == "BUS-IC-012")
    assert ic.confidence < ic_two.confidence


def test_reconciled_cash_reaches_invested_capital_like_debt(nvda_packet):
    """SOURCE_HIERARCHY.md's default order prefers the tier-1 filing over the
    provider feed. `cash` is a reconciled input feeding BUS-IC-012 (its EDGAR
    value sits in facts_table), the same six IC-derived metrics as total_debt.
    A reconciled cash that differs from the raw row must reach invested
    capital — the higher-tier override was wired for debt only, so IC-012
    combined a tier-1 debt with a raw-provider cash."""
    import copy

    def invested_capital(packet):
        out = bus.run(packet, overlay={"wacc": 0.09})
        return next(m for m in out.metrics if m.metric_id == "BUS-IC-012").value

    base = invested_capital(nvda_packet)
    bumped = copy.deepcopy(nvda_packet)
    fact = bumped.facts_table["cash"]
    bumped.facts_table = {**bumped.facts_table,
                          "cash": fact.model_copy(update={"value": fact.value + 2_000_000_000})}
    # BUS-IC-012 averages the begin and end periods and only the latest (end)
    # cash is reconciled, so +2e9 of excess cash lowers invested capital by 1e9.
    assert invested_capital(bumped) == pytest.approx(base - 1_000_000_000)

    # BUS-IROIC-016 builds its own latest-period invested capital, and it must
    # take the same tier-1 cash — it read the raw provider row, leaving that
    # endpoint mixing a reconciled debt with a provider cash.
    def incremental_roic(packet):
        out = bus.run(packet, overlay={"wacc": 0.09})
        return next(m for m in out.metrics if m.metric_id == "BUS-IROIC-016").value

    assert incremental_roic(bumped) != pytest.approx(incremental_roic(nvda_packet))


def _conflicted_debt_packet(fmp_debt: float, edgar_debt: float) -> Packet:
    """A packet whose total_debt conflicts between its two sources, with both
    kept the way the builder keeps them (`<field>:fmp` / `:edgar`)."""
    from wbj.core.nullstates import Value

    rows = [_row(y, total_debt=200.0 + i * 20, total_equity=600.0 + i * 30,
                 cash=300.0, ebit=200.0 + i * 15)
            for i, y in enumerate((2021, 2022, 2023, 2024, 2025))]
    packet = _minimal_packet(list(reversed(rows)))
    packet.facts_table = {
        "total_debt": Value.null(NullState.CONFLICTED, unit="usd", warnings=["FMP vs EDGAR >5%"]),
        "total_debt:fmp": Value.of(fmp_debt, unit="usd"),
        "total_debt:edgar": Value.of(edgar_debt, unit="usd"),
    }
    return packet


def _ic_value(out):
    return next(m for m in out.metrics if m.metric_id == "BUS-IC-012").value


def test_a_material_conflict_still_scores_on_the_tier1_value():
    """SOURCE_HIERARCHY.md step 5's "do not score it" names the contested
    *input* (total_debt), which the packet already carries CONFLICTED and
    business does not score as a metric. The *derived* metrics stay computable
    on the tier-1 (EDGAR) value the default order prefers, so they still score
    even when the conflict is material — and the mandatory red flag the tier-1
    value raises (here VALUE_DESTRUCTION, from the huge EDGAR debt) stands."""
    out = bus.run(_conflicted_debt_packet(fmp_debt=260.0, edgar_debt=6000.0), {"wacc": 0.09})
    assert _ic_value(out) is not None                              # scored, not withheld
    roic = next(m for m in out.metrics if m.metric_id == "BUS-ROIC-013")
    assert roic.score != "NOT_SCORABLE"                            # everything scores
    assert "VALUE_DESTRUCTION" in out.mandatory_flags              # tier-1 ROIC < WACC
    assert any("is MATERIAL" in a for a in out.assumptions)        # and the materiality is disclosed
    # The tier-1 (EDGAR) debt is the one used: IC_end = 6000 + 720 - 300.
    assert _ic_value(out) == pytest.approx(((6000.0 + 720.0 - 300.0) + (260.0 + 690.0 - 300.0)) / 2)


def test_an_immaterial_source_conflict_scores_on_the_tier1_value():
    """The common case — a >5% source gap that is definitional (e.g. leases in
    EDGAR debt) and moves neither the score nor a gate: scored on the tier-1
    (EDGAR) value, with both values kept in the audit trail."""
    out = bus.run(_conflicted_debt_packet(fmp_debt=250.0, edgar_debt=262.0), {"wacc": 0.09})
    assert _ic_value(out) is not None                              # scored, not withheld
    assert any("is immaterial" in a for a in out.assumptions)
    # The EDGAR (tier-1) value is the one used, not FMP: IC averages the latest
    # period (262 + 720 - 300) with the prior (260 + 690 - 300).
    assert _ic_value(out) == pytest.approx(((262.0 + 720.0 - 300.0) + (260.0 + 690.0 - 300.0)) / 2)


def test_a_run_without_peers_ignores_peer_set_staleness():
    """The other half: a run that used no peer comparison must not pay for
    a stale peer set — freshness counts only the signals it depended on."""
    def confidence_with(peer_set_state):
        packet = _minimal_packet(_five_year_rows())
        packet.staleness = {"peer_set": peer_set_state}
        return bus.run(packet, overlay={"wacc": 0.09}).category.confidence

    assert confidence_with("FRESH") == confidence_with("STALE")


def test_unregistered_inputs_and_equal_weighting_are_disclosed_in_the_output():
    """SCORING.md names `rank` and `cyclicality` with no registered formula,
    and registers no per-dimension weights. AGENT.md's audit-trail rule
    means those gaps are disclosed in the output, not just in code."""
    out = bus.run(_minimal_packet(_five_year_rows()), overlay={"wacc": 0.09})
    for line in bus.UNREGISTERED_METHODOLOGY_DISCLOSURES:
        assert line in out.assumptions
    blob = " ".join(out.assumptions)
    assert "rank" in blob and "cyclicality" in blob
    assert "Weights must be registered" in blob


def test_wide_moat_gate_margin_condition_uses_5pp_not_3pp():
    """DECISION_RULES.md wide-moat gate condition 2 says the 5y operating-
    margin range must be 'no more than 5 percentage points' (<=0.05) -- a
    company with a 4pp range must PASS condition 2 (it fails the stricter
    <=0.03 BUS-RANGE-010 'positive moat signal', which is a different
    threshold for a different purpose)."""
    assert bus.wide_moat_margin_range_ok(0.04) is True   # 4pp passes the gate
    assert bus.wide_moat_margin_range_ok(0.05) is True    # exactly 5pp is "no more than 5"
    assert bus.wide_moat_margin_range_ok(0.0501) is False  # above 5pp fails
    # ...while BUS-RANGE-010's own 'positive moat signal' stays at <=0.03:
    assert bus.margin_range_is_stable(0.04) is False


def test_dilution_red_flag_when_diluted_cagr_above_5pct():
    # DECISION_RULES.md requires ">5% CAGR *for three years*", so this
    # needs a genuine three-year span (four annual points). 100 -> 130
    # over three years is ~9.1% CAGR, above the threshold. (A two-point,
    # one-year dilution no longer trips the flag -- covered by
    # test_business_cagr_window.py's duration tests.)
    rows = [
        _row(2025, diluted_shares=130.0),
        _row(2024, diluted_shares=120.0),
        _row(2023, diluted_shares=110.0),
        _row(2022, diluted_shares=100.0),
    ]
    out = bus.run(_minimal_packet(rows))
    assert "DILUTION_RED_FLAG" in out.mandatory_flags


def test_value_destruction_flag_when_roic_below_wacc():
    rows = _five_year_rows()
    packet = _minimal_packet(rows)
    out = bus.run(packet, overlay={"wacc": 0.50})
    assert "VALUE_DESTRUCTION" in out.mandatory_flags


# ============================================================================
# Judgment requests
# ============================================================================


def test_run_moat_classification_and_quantitative_effects_are_judgment_requests(nvda_packet):
    out = bus.run(nvda_packet)
    ids = {jr.metric_id for jr in out.judgment_requests}
    assert "moat_classification" in ids
    assert "moat_quantitative_effects_count" in ids
    assert "three_thesis_killers" in ids
    assert out.moat.classification == "NotScorable"


# ============================================================================
# run() against the NVDA fixture
# ============================================================================


def test_run_nvda_fixture_schema_valid(nvda_packet):
    out = bus.run(nvda_packet)
    assert out.agent_id == "business_analysis"
    assert out.version == "2.0.0"
    assert out.security.ticker == "NVDA"
    assert out.category.max_points == 20.0
    assert out.status in ("COMPLETE", "INCOMPLETE", "ERROR")
    assert len(out.dimensions) == 5
    assert len(out.metrics) == 30
    for row in out.metrics:
        assert row.metric_id
        assert row.formula_id
        assert row.formula_version
        assert row.score == "NOT_SCORABLE" or isinstance(row.score, float)
        assert 0.0 <= row.confidence <= 100.0
        assert (row.value is None) != (row.state is None)


def test_run_nvda_fixture_category_math_reproduces_from_dimensions(nvda_packet):
    out = bus.run(nvda_packet)
    recomputed = Category(name=bus.AGENT_ID, max_points=bus.MAX_POINTS, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)
    assert out.coverage == pytest.approx(recomputed.coverage(), abs=1e-6)


def test_run_nvda_fixture_category_confidence_computed(nvda_packet):
    out = bus.run(nvda_packet)
    assert out.category.confidence is not None
    assert 0.0 <= out.category.confidence <= 100.0


def test_run_nvda_fixture_serializes_to_json(nvda_packet):
    out = bus.run(nvda_packet)
    dumped = out.model_dump(mode="json")
    json.dumps(dumped)
    assert dumped["agent_id"] == "business_analysis"


def test_run_nvda_fixture_extension_fields_populated(nvda_packet):
    out = bus.run(nvda_packet)
    assert out.roic_history is not None
    assert out.roic_wacc_spread_history is not None
    assert out.margin_stability is not None
    assert out.three_thesis_killers == []  # populated later by Task 20's judgment overlay


def test_run_empty_annual_history_degrades_to_error_status_without_crashing():
    out = bus.run(_minimal_packet([]))
    assert out.status == "ERROR"
    assert out.coverage == 0.0
    assert out.category.awarded_points == 0.0
    assert len(out.metrics) == 30


def test_run_nvda_fixture_validation_tests_all_self_checks_pass(nvda_packet):
    out = bus.run(nvda_packet)
    assert out.validation_tests.failed == 0
    assert out.validation_tests.passed >= 1


def test_run_category_reproduces_from_dimensions_even_with_cap():
    rows = _five_year_rows()
    packet = _minimal_packet(rows)
    out = bus.run(packet, overlay={"wacc": 0.09, "largest_customer_share": 0.35})
    recomputed = Category(name=bus.AGENT_ID, max_points=bus.MAX_POINTS, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)


# --- SCORING.md management gate ----------------------------------------------


def test_management_gate_is_enforced_by_the_weights_not_a_cap():
    """SCORING.md says "Qualitative reputation alone cannot exceed 5". No
    explicit cap implements it, and none is needed: capital-return carries
    0.20 of the dimension, so with the three formula slots null the valid
    weight is 0.20 — far under the 70% floor — and the dimension is
    NOT_SCORABLE before a cap could apply. Documented here so the missing
    cap is not "fixed" with unreachable code."""
    from wbj.core.nullstates import NullState, Value
    from wbj.core.scoring import Dimension

    reputation_only = [
        (0.35, Value.null(NullState.NOT_SCORABLE, unit="score")),
        (0.30, Value.null(NullState.NOT_SCORABLE, unit="score")),
        (0.20, Value.of(10.0, unit="score")),
        (0.15, Value.null(NullState.NOT_SCORABLE, unit="score")),
    ]
    assert Dimension(name="m", max_points=4.0,
                     metric_scores=reputation_only).score10_value().is_null


def test_segment_mix_reports_the_supplied_share():
    """BUS-MIX-001 read overlay["segment_shares"] and then overwrote the
    result with MISSING, so it could never report a value."""
    from wbj.specialists import business

    shares = [0.897, 0.074, 0.015, 0.011, 0.003]
    assert max(shares) == 0.897
    assert hasattr(business, "segment_revenue_share")


# --- SCORING.md competitive gate ---------------------------------------------


def test_competitive_cap_holds_without_a_sourced_market_definition():
    """SCORING.md: "Cannot score above 8 if market definition is low
    confidence." No sourced TAM means no market definition at all, which
    is the lowest confidence there is."""
    from wbj.specialists.business import _market_definition_is_confident

    assert _market_definition_is_confident({}) is False
    assert _market_definition_is_confident({"tam_source_tier": None}) is False


def test_competitive_cap_lifts_on_a_tier_1_to_3_market_definition():
    """DECISION_RULES.md's tier table: 1=100, 2=85, 3=70 confidence, all
    above the 60 floor, so the market definition is not low confidence."""
    from wbj.specialists.business import _market_definition_is_confident

    assert _market_definition_is_confident({"tam_source_tier": 1}) is True
    assert _market_definition_is_confident({"tam_source_tier": 3}) is True


def test_an_issuer_estimate_is_still_low_confidence():
    """Tier 4 earns 45, below the floor — a company's own market-size
    claim does not settle how the market is defined. Tier 5 is
    unattributed and scores 0."""
    from wbj.specialists.business import _market_definition_is_confident

    assert _market_definition_is_confident({"tam_source_tier": 4}) is False
    assert _market_definition_is_confident({"tam_source_tier": 5}) is False


def test_proxies_are_declared_in_the_output_not_just_in_comments():
    """FORMULAS.md execution rules: "Record any proxy in `warnings` and
    reduce model-fit confidence." Both business proxies — revenue CAGR
    for share trend, and the binary capital-return read — were documented
    in code comments only, so nothing downstream could tell a substitute
    stood in for the registered input."""
    import re
    import pathlib

    # Anclado al módulo y no al directorio actual: con una ruta relativa este
    # test pasaba desde `engine/` y fallaba desde la raíz del repo, que es
    # justo donde se corre la suite completa.
    src = pathlib.Path(bus.__file__).read_text(encoding="utf-8")
    declared = re.findall(r'assumptions\.append\(\s*\n?\s*"([^"]+)', src)
    joined = " ".join(declared)
    assert "revenue CAGR" in joined
    assert "capital return is a binary read" in joined


# --- BUS-GUIDE-027 robustness ------------------------------------------------


def test_guidance_row_with_null_values_is_skipped_not_fatal():
    """guidance_history is hand-written, so a row can carry the keys with
    null values. Testing only for key presence let abs(None) raise and
    take the whole specialist down."""
    from wbj.specialists.business import guidance_accuracy

    rows = [{"actual": None, "guidance_midpoint": None},
            {"actual": 44.06, "guidance_midpoint": 43.0}]
    accuracies = []
    for g in rows:
        try:
            accuracies.append(
                guidance_accuracy(float(g.get("actual")),
                                  float(g.get("guidance_midpoint"))).value)
        except (TypeError, ValueError):
            continue
    assert len(accuracies) == 1


def test_guidance_accuracy_is_one_when_the_company_hits_its_number():
    from wbj.specialists.business import guidance_accuracy

    assert guidance_accuracy(43.0, 43.0).value == 1.0


def test_guidance_accuracy_penalises_a_miss_in_either_direction():
    """Overshooting guidance is still a forecasting miss — the formula
    takes an absolute difference, so beating by 10% and missing by 10%
    score the same."""
    from wbj.specialists.business import guidance_accuracy

    over = guidance_accuracy(44.0, 40.0).value
    under = guidance_accuracy(36.0, 40.0).value
    assert over == pytest.approx(under)
    assert over == pytest.approx(0.9)


def test_guidance_accuracy_clips_at_zero():
    """A miss larger than the guidance itself cannot go negative."""
    from wbj.specialists.business import guidance_accuracy

    assert guidance_accuracy(0.0, 10.0).value == 0.0
    assert guidance_accuracy(100.0, 10.0).value == 0.0


# --- durability composition --------------------------------------------------


def test_durability_survives_one_absent_metric():
    """SCORING.md names four inputs for this dimension. With three equal
    thirds, one absent metric left 0.667 valid weight against a 0.70
    floor and sank the whole dimension; four quarters survive a single
    absence at 0.75."""
    from wbj.core.nullstates import NullState, Value
    from wbj.core.scoring import Dimension

    def _d(weights):
        return Dimension(name="business_durability", max_points=4.0,
                         metric_scores=weights)

    absent = Value.null(NullState.NOT_SCORABLE, unit="score")
    three = _d([(1 / 3, absent), (1 / 3, Value.of(7.0, unit="score")),
                (1 / 3, Value.of(6.0, unit="score"))])
    four = _d([(0.25, absent), (0.25, Value.of(7.0, unit="score")),
               (0.25, Value.of(6.0, unit="score")), (0.25, Value.of(8.0, unit="score"))])
    assert three.score10_value().is_null is True
    assert four.score10_value().is_null is False


def test_durability_still_fails_on_two_absent_metrics():
    """The fourth slot removes brittleness, it does not manufacture a
    score: two absences still leave 0.50, under the floor."""
    from wbj.core.nullstates import NullState, Value
    from wbj.core.scoring import Dimension

    absent = Value.null(NullState.NOT_SCORABLE, unit="score")
    d = Dimension(name="business_durability", max_points=4.0,
                  metric_scores=[(0.25, absent), (0.25, absent),
                                 (0.25, Value.of(6.0, unit="score")),
                                 (0.25, Value.of(8.0, unit="score"))])
    assert d.valid_weight() == 0.5
    assert d.score10_value().is_null is True


# --- customer economics applicability ----------------------------------------


def _pkt(sector, industry, adapter="default_nonfinancial"):
    from types import SimpleNamespace
    return SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter=adapter),
        security=SimpleNamespace(sector=sector, industry=industry),
    )


def test_subscription_businesses_outside_software_are_in_scope():
    """Keying relief off the adapter alone excused Netflix and Spotify:
    both sit under the default non-financial adapter alongside chip
    makers, yet their revenue is subscription revenue."""
    from wbj.specialists.business import _subscription_business

    for sector, industry in [
        ("Communication Services", "Entertainment"),                   # NFLX
        ("Communication Services", "Internet Content & Information"),  # SPOT
        ("Technology", "Software - Application"),                      # CRM
    ]:
        assert _subscription_business(_pkt(sector, industry), {}) is True, industry


def test_a_transactional_model_is_not_charged_for_subscription_metrics():
    """FORMULAS.md gates BUS-NRR-020 on "Subscription/business-model
    adapter only". Deciding scope by exclusion put these in scope by
    default, so Coca-Cola carried six MISSING rows for net revenue
    retention, logo churn and CAC payback — metrics its business model
    does not produce — and that alone held its coverage below
    MISSING_DATA_POLICY.md's 0.70 gate floor."""
    from wbj.specialists.business import _subscription_business

    for sector, industry in [
        ("Consumer Defensive", "Beverages - Non-Alcoholic"),  # KO
        ("Consumer Cyclical", "Restaurants"),                 # SBUX
        ("Consumer Defensive", "Discount Stores"),            # WMT
        ("Healthcare", "Drug Manufacturers - General"),       # PFE
        ("Financial Services", "Financial - Credit Services"),  # V
    ]:
        assert _subscription_business(_pkt(sector, industry), {}) is False, industry


def test_analyst_supplied_data_puts_a_company_in_scope_whatever_its_label():
    """An industry label cannot tell Costco's membership model from
    Walmart's — both are "Discount Stores". Rather than guess, the engine
    treats supplied data as the proof: an analyst who writes a retention
    bridge has established the metric exists for that company."""
    from wbj.specialists.business import _subscription_business

    costco = _pkt("Consumer Defensive", "Discount Stores")
    assert _subscription_business(costco, {}) is False
    assert _subscription_business(
        costco, {"retention": {"begin": 1000.0, "expansion": 50.0,
                               "contraction": 10.0, "churn": 40.0}}) is True


def test_industrials_and_commodities_keep_their_relief():
    """BUS-T008 grants relief to a "non-subscription industrial", and a
    commodity producer has no repeat-customer economics to measure."""
    from wbj.specialists.business import _subscription_business

    assert _subscription_business(_pkt("Technology", "Semiconductors"), {}) is False
    assert _subscription_business(
        _pkt("Energy", "Oil & Gas Integrated", "commodities_cyclicals"), {}) is False
    assert _subscription_business(_pkt("Industrials", "Aerospace & Defense"), {}) is False


def test_analyst_supplied_figures_put_the_dimension_in_scope():
    """A company disclosing retention is answering the question whatever
    its classification says."""
    from wbj.specialists.business import _subscription_business

    assert _subscription_business(
        _pkt("Technology", "Semiconductors"),
        {"retention": {"begin": 100.0}}) is True


def test_model_fit_follows_the_adapter_treatment_cerebro_documents():
    """`model_fit` is "suitability of the selected formula for the
    security type". INDUSTRY_ADAPTERS.md sorts adapters into three
    treatments, and `confidence_inputs` spaces them evenly over the
    component's 0-100 range — replacing the 90/65/50/40 this module used
    to carry, which came from none of Victor's documents."""
    from wbj.core import adapters as ad
    from wbj.core import confidence_inputs as ci

    def fit(adapter):
        return ci.model_fit(replaces_model=ad.replaces_model(adapter),
                            normalizes_inputs=ad.normalizes_inputs(adapter))

    # An additive adapter leaves the core formulas untouched, so it fits
    # exactly as well as no adapter at all.
    assert fit("saas_subscriptions") == fit("default_nonfinancial")
    # Same formulas, inputs that must be normalized first.
    assert fit("default_nonfinancial") > fit("commodities_cyclicals")
    # A different primary model: these formulas do not suit the security.
    assert fit("commodities_cyclicals") > fit("banks") == fit("reits")




# ============================================================================
# VALUE_DESTRUCTION must not rest on a ROIC the adapter replaces
# ============================================================================


def _adapter_packet(adapter: str):
    """The NVDA fixture re-tagged with a model-replacing adapter."""
    import json
    from pathlib import Path
    from wbj.schemas.packet import Packet

    fixture = Path(__file__).parent.parent / "fixtures" / "packet" / "NVDA_packet.json"
    p = Packet.model_validate_json(fixture.read_text(encoding="utf-8"))
    return p.model_copy(update={
        "analysis": p.analysis.model_copy(update={"industry_adapter": adapter})
    })


def test_value_destruction_is_withheld_under_a_model_replacing_adapter():
    """INDUSTRY_ADAPTERS.md replaces ROIC for banks/insurers/REITs/biotech
    ("Replace ROIC with ROE, ROTCE"), and the moat cap already lifts on that
    condition. VALUE_DESTRUCTION is the same ROIC-below-WACC read, and the
    main agent turns it into mandatory override 2 -- barring Elite/Quality.
    Left unguarded it fired on live JPM and O data, permanently barring every
    bank and REIT on a number the methodology says not to compute for them.
    """
    # A WACC far above any plausible ROIC would trigger the flag outright.
    for adapter in ("banks", "insurers", "reits", "biotech"):
        out = bus.run(_adapter_packet(adapter), {"wacc": 99.0})
        assert "VALUE_DESTRUCTION" not in out.mandatory_flags, adapter
        assert any("VALUE_DESTRUCTION withheld" in a for a in out.assumptions), adapter


def test_value_destruction_still_fires_for_a_conventional_company():
    """The guard is scoped to model-replacing adapters; an operating company
    with ROIC below WACC must still raise it."""
    out = bus.run(_adapter_packet("default_nonfinancial"), {"wacc": 99.0})
    assert "VALUE_DESTRUCTION" in out.mandatory_flags
    assert not any("VALUE_DESTRUCTION withheld" in a for a in out.assumptions)


def test_an_additive_adapter_does_not_lift_the_flag():
    """SaaS and commodities keep the conventional formulas, so the flag stands."""
    for adapter in ("saas_subscriptions", "commodities_cyclicals"):
        out = bus.run(_adapter_packet(adapter), {"wacc": 99.0})
        assert "VALUE_DESTRUCTION" in out.mandatory_flags, adapter


def test_the_concentration_flag_is_strictly_above_thirty_percent():
    """`DECISION_RULES.md`: "CONCENTRATION_RED_FLAG when one customer/product
    exceeds 30% of revenue". "Exceeds" is strict — exactly 30% is not a flag,
    and the durability cap that rides on it must not fire there either.

    The boundary is where an off-by-one hides: existing coverage tested 0.35
    (clearly over) and nothing at the edge.
    """
    assert bus.is_concentration_red_flag(0.30) is False
    assert bus.is_concentration_red_flag(0.3000001) is True
    assert bus.is_concentration_red_flag(0.29) is False


def test_recurring_revenue_is_the_third_durability_slot():
    """`business_durability` holds BUS-REC-002, BUS-CONC-003 and BUS-STAB-009.
    Without the recurring share it sits at 2 of 3 — below SCORING_ENGINE.md's
    70% reweight floor — so the whole 4-point dimension scores nothing.

    Measured on NVDA: supplying it takes the dimension from NOT_SCORABLE to
    4.80/10 and Business from 11.39 to 13.64 of 20.
    """
    rows = [_row(2025), _row(2024), _row(2023)]
    packet = _minimal_packet(rows)
    base = {"largest_customer_share": 0.22}

    without = bus.run(packet, overlay=base)
    with_rec = bus.run(packet, overlay={**base, "recurring_revenue_share": 0.25})

    dim_a = next(d for d in without.dimensions if d.name == "business_durability")
    dim_b = next(d for d in with_rec.dimensions if d.name == "business_durability")
    assert dim_b.valid_weight() > dim_a.valid_weight()
