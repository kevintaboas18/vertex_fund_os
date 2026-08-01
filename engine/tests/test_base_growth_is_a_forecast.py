"""The base case is a forecast driver, not a trailing ratio.

DATASET.md makes `forecast_drivers` — "revenue, margins, reinvestment,
ROIC ... explicit model assumptions" — a REQUIRED input. The specialist
manufactured one instead: trailing `capex / NOPAT` times trailing ROIC,
citing VAL-REINV-043.

That inverts the rule it cited. VAL-REINV-043 is "Terminal reinvestment
consistency", `rr = g / TerminalROIC` — a constraint binding the terminal
year's reinvestment to its growth so the model cannot price free growth.
DECISION_RULES.md rule 2 states the same identity as a consistency
condition ON a forecast, not a way to produce one.

Measuring reinvestment as capex alone also breaks for anything
asset-light, where growth is bought with R&D that is expensed. On NVDA it
produced 5.05% for a company that had just grown 65.5%, and valued it near
$41 against a $195 price — while the packet already carried consensus
revenue out to FY2031.
"""

from __future__ import annotations

import pytest

from wbj.specialists.valuation import _consensus_revenue_cagr, _last_reported_date


def _est(date, revenue, analysts=30, spread=0.10):
    """A consensus row with the analysts' own low/high around the mean."""
    return {"date": date, "revenueAvg": revenue,
            "revenueLow": revenue * (1 - spread), "revenueHigh": revenue * (1 + spread),
            "numAnalystsRevenue": analysts}


#: NVDA's shape: reported through FY2026, consensus out to FY2031.
_ROWS = [
    _est("2031-01-25", 1_005_000_000_000, 22),
    _est("2030-01-25", 774_228_304_289, 13),
    _est("2029-01-25", 684_573_570_460, 27),
    _est("2028-01-25", 562_864_133_910, 40),
    _est("2027-01-25", 393_421_082_449, 40),
    _est("2026-01-25", 213_656_385_457, 34),      # already reported
    _est("2025-01-25", 129_426_091_457, 34),      # already reported
    _est("2024-01-25", 59_306_860_332, 28),       # already reported
    _est("2023-01-25", 26_952_415_189, 14),       # already reported
    _est("2022-01-25", 26_662_163_796, 13),       # already reported
]
_REVENUE0 = 215_938_000_000.0


# --- the cutoff between reported and forecast -----------------------------


def _packet(annual):
    from types import SimpleNamespace

    return SimpleNamespace(fundamentals={"annual": annual})


def test_the_cutoff_is_the_newest_reported_period_end():
    assert _last_reported_date(_packet([
        {"date": "2026-01-25", "fiscalYear": "2026"},
        {"date": "2025-01-25", "fiscalYear": "2025"},
    ])) == "2026-01-25"


def test_a_row_without_a_period_end_falls_back_to_its_fiscal_year():
    """The label still sorts correctly against the estimates' dates."""
    assert _last_reported_date(_packet([{"fiscalYear": "2026"}])) == "2026-12-31"
    assert _last_reported_date(_packet([{"calendarYear": "2026"}])) == "2026-12-31"


def test_no_fundamentals_yields_no_cutoff():
    assert _last_reported_date(_packet([])) == ""


def test_an_empty_cutoff_would_treat_reported_years_as_forecast():
    """The regression this replaced. Callers passed
    `getattr(packet.analysis, "as_of", "")`, and `AnalysisMeta` has no
    `as_of` field — so the cutoff was always "", every row counted as
    forward-looking, and the CAGR ran to a year already in the filings.

    With NVDA's rows that reaches FY2026 (213.7bn) against 215.9bn
    reported: a NEGATIVE five-year growth rate for a company compounding
    at 65%."""
    broken = _consensus_revenue_cagr(_ROWS, _REVENUE0, "", 5)
    assert broken["cagr"] < 0

    fixed = _consensus_revenue_cagr(_ROWS, _REVENUE0, "2026-01-25", 5)
    assert fixed["cagr"] > 0


# --- the CAGR itself ------------------------------------------------------


def test_the_cagr_spans_the_explicit_forecast_period():
    out = _consensus_revenue_cagr(_ROWS, _REVENUE0, "2026-01-25", 5)
    assert out["years"] == 5
    assert out["to_date"] == "2031-01-25"
    assert out["cagr"] == pytest.approx((1_005_000_000_000 / _REVENUE0) ** 0.2 - 1)
    assert out["cagr"] == pytest.approx(0.3601, abs=5e-4)
    # The evidence travels with the number, per "sin evidencia, no hay numero".
    assert out["analysts"] == 22


def test_it_never_reaches_past_the_horizon_the_dcf_forecasts():
    """A CAGR taken to a year beyond the explicit period would price
    growth the model never applies."""
    out = _consensus_revenue_cagr(_ROWS, _REVENUE0, "2026-01-25", 3)
    assert out["years"] == 3
    assert out["to_date"] == "2029-01-25"


def test_a_short_consensus_uses_what_there_is():
    rows = [_est("2027-01-25", 393_421_082_449)]
    out = _consensus_revenue_cagr(rows, _REVENUE0, "2026-01-25", 5)
    assert out["years"] == 1
    assert out["cagr"] == pytest.approx(393_421_082_449 / _REVENUE0 - 1)


@pytest.mark.parametrize("estimates,revenue0", [
    ([], 100.0),                                    # no estimates at all
    (_ROWS, 0.0),                                   # no base to grow from
    (_ROWS, None),                                  # no base at all
    ([_est("2020-01-25", 5.0)], 100.0),             # all of it already reported
    ([{"date": "2027-01-25"}], 100.0),              # a row carrying no revenue
    ([_est("2027-01-25", 0.0)], 100.0),             # a zero forecast
])
def test_it_abstains_rather_than_inventing_a_forecast(estimates, revenue0):
    assert _consensus_revenue_cagr(estimates, revenue0, "2026-01-25", 5) is None


def test_a_zero_horizon_has_no_cagr():
    assert _consensus_revenue_cagr(_ROWS, _REVENUE0, "2026-01-25", 0) is None


# --- and the specialist uses it -------------------------------------------


def _packet_with(estimates, capex):
    """A packet complete enough for the scenario DCF to actually run."""
    from wbj.core.nullstates import EvidenceClass, Value
    from wbj.schemas.packet import AnalysisMeta, MarketData, Packet, Security

    shares = 2.4e10
    price = 195.04
    row = dict(fiscalYear="2026", date="2026-01-25", filingDate="2026-02-25",
               revenue=_REVENUE0, gross_profit=_REVENUE0 * 0.75,
               ebit=_REVENUE0 * 0.60, net_income=_REVENUE0 * 0.50,
               operating_cash_flow=_REVENUE0 * 0.55, capex=capex,
               fcf=_REVENUE0 * 0.5, total_debt=1e10, total_equity=1e11,
               cash=6e10, total_assets=1.4e11, total_liabilities=3e10,
               income_before_tax=_REVENUE0 * 0.58,
               income_tax_expense=_REVENUE0 * 0.08, diluted_shares=shares,
               stock_based_compensation=5e9, changeInWorkingCapital=-1e9)
    prior = dict(row, fiscalYear="2025", date="2025-01-25",
                 revenue=_REVENUE0 * 0.6, total_equity=7e10)
    facts = {
        "price": Value.of(price, unit="usd_per_share", evidence_class=EvidenceClass.R),
        "diluted_shares": Value.of(shares, unit="shares", evidence_class=EvidenceClass.R),
        "cash": Value.of(6e10, unit="usd", evidence_class=EvidenceClass.R),
        "total_debt": Value.of(1e10, unit="usd", evidence_class=EvidenceClass.R),
        "revenue": Value.of(_REVENUE0, unit="usd", evidence_class=EvidenceClass.R),
    }
    return Packet(
        security=Security(ticker="T", exchange="NASDAQ",
                          security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Semiconductors"),
        analysis=AnalysisMeta(knowledge_timestamp="2026-07-31T21:00:00+00:00",
                              industry_adapter="default_nonfinancial"),
        fundamentals={"annual": [row, prior], "quarterly": []},
        market_data=MarketData(),
        estimates={"risk_free_rate": 0.04, "peers": [],
                   "fmp_analyst_estimates": estimates},
        capital_structure={"beta": 2.2, "market_cap": price * shares,
                           "cash": 6e10, "total_debt": 1e10,
                           "diluted_shares": shares},
        facts_table=facts, staleness={})


def _base_growth_of(out):
    return next(s.assumptions["growth"] for s in out.scenarios if s.name == "Base")


def test_the_consensus_forecast_beats_the_trailing_capex_ratio():
    """The whole point: a company that grew 65% is not forecast at 5%
    because its capex line happens to be small."""
    from wbj.specialists import valuation as val

    out = val.run(_packet_with(_ROWS, capex=-6.04e9), {"wacc": 0.146})
    assert _base_growth_of(out) == pytest.approx(0.3601, abs=5e-3)


def test_without_consensus_it_falls_back_and_says_so():
    """Abstaining entirely would drop the whole category; the fallback is
    legitimate, it just has to be labelled as one."""
    from wbj.specialists import valuation as val

    out = val.run(_packet_with([], capex=-6.04e9), {"wacc": 0.146})
    with_consensus = val.run(_packet_with(_ROWS, capex=-6.04e9), {"wacc": 0.146})

    # It is a different number, reached a different way, and labelled.
    assert _base_growth_of(out) != pytest.approx(_base_growth_of(with_consensus))
    assert any("falls back to fundamental growth capacity" in a
               for a in out.assumptions)
    assert not any("consensus revenue CAGR" in a for a in out.assumptions)


def test_the_fallback_scales_with_capex_because_that_is_what_it_measures():
    """Its known weakness, stated as a property: with no consensus the
    forecast is driven by the capex line, which is why an asset-light
    company that grows on expensed R&D comes out too low."""
    from wbj.specialists import valuation as val

    lean = val.run(_packet_with([], capex=-6.04e9), {"wacc": 0.146})
    heavy = val.run(_packet_with([], capex=-6.04e10), {"wacc": 0.146})
    assert _base_growth_of(heavy) > _base_growth_of(lean)


def test_the_declared_assumption_carries_both_numbers():
    """AGENT.md allows a figure that is not Victor's only as an
    "explicitly disclosed assumption", and DECISION_RULES.md's reverse-DCF
    section asks for the comparison against fundamental growth capacity."""
    from wbj.specialists import valuation as val

    out = val.run(_packet_with(_ROWS, capex=-6.04e9), {"wacc": 0.146})
    declared = next(a for a in out.assumptions
                    if "Base-case revenue growth" in a)
    assert "consensus revenue CAGR" in declared
    assert "22 analysts" in declared
    assert "Fundamental growth capacity" in declared
    assert "expensed R&D" in declared


def test_an_analyst_override_still_wins():
    """`forecast_drivers` is an analyst input first; consensus is only the
    packet's automatic stand-in for it."""
    from wbj.specialists import valuation as val

    out = val.run(_packet_with(_ROWS, capex=-6.04e9),
                  {"wacc": 0.146, "scenarios": {"base": {"growth": 0.12}}})
    assert _base_growth_of(out) == pytest.approx(0.12)


# --- the override path had a crash in it ----------------------------------


def test_overriding_only_the_base_case_does_not_kill_the_category():
    """`scenarios.base.growth` is the override this module documents, and
    using it alone used to raise.

    Bear and bull default to `base*0.5` and `base*1.5`, and overrides are
    applied per scenario — so overriding only the base leaves bear and bull
    bracketing the OLD base. A 12% base under a 36% consensus gave
    low=18%, mode=12%; numpy's `triangular` raises `left > mode`, the
    exception escaped `run()`, and the whole valuation came back ERROR.
    """
    from wbj.specialists import valuation as val

    out = val.run(_packet_with(_ROWS, capex=-6.04e9),
                  {"wacc": 0.146, "scenarios": {"base": {"growth": 0.12}}})
    assert out.status != "ERROR"
    assert _base_growth_of(out) == pytest.approx(0.12)


@pytest.mark.parametrize("base_override", [0.01, 0.12, 0.36, 0.80, 2.0])
def test_any_base_override_keeps_the_simulation_range_ordered(base_override):
    """A triangular distribution needs `low <= mode <= high`. Taking the
    endpoints by position let an override put the mode outside them; taking
    them as the extremes of the three scenarios makes that unrepresentable."""
    from wbj.specialists import valuation as val

    out = val.run(_packet_with(_ROWS, capex=-6.04e9),
                  {"wacc": 0.146, "scenarios": {"base": {"growth": base_override}}})
    assert out.status != "ERROR"


def test_the_range_takes_the_extremes_not_the_positions():
    from wbj.specialists.valuation import _mc_range

    inverted = _mc_range(0.18, 0.12, 0.54)      # bear above the overridden base
    assert inverted.low == pytest.approx(0.12)
    assert inverted.mode == pytest.approx(0.12)
    assert inverted.high == pytest.approx(0.54)

    ordered = _mc_range(0.18, 0.36, 0.54)       # an explicit three-way override
    assert (ordered.low, ordered.mode, ordered.high) == \
        pytest.approx((0.18, 0.36, 0.54))


# --- bear and bull are sourced too ----------------------------------------


def test_the_scenarios_take_the_analysts_own_low_and_high():
    """DECISION_RULES.md's scenario framework: each scenario "separately
    defines" its revenue growth path. Bear and bull were `base*0.5` and
    `base*1.5` — this module's calibration, tuned when the base was a small
    trailing ratio."""
    out = _consensus_revenue_cagr(_ROWS, _REVENUE0, "2026-01-25", 5)
    assert out["cagr_low"] < out["cagr"] < out["cagr_high"]

    hi = 1_005_000_000_000 * 1.10
    assert out["cagr_high"] == pytest.approx((hi / _REVENUE0) ** 0.2 - 1)


def test_a_row_without_a_low_high_leaves_them_unset():
    """Then the multipliers are the only thing left, and the assumption
    line has to say so rather than implying a dispersion nobody published."""
    bare = [{"date": "2031-01-25", "revenueAvg": 1_005_000_000_000}]
    out = _consensus_revenue_cagr(bare, _REVENUE0, "2026-01-25", 5)
    assert out["cagr"] is not None
    assert out["cagr_low"] is None and out["cagr_high"] is None


def test_a_hypergrowth_consensus_no_longer_invents_a_bull_case():
    """The case that forced this. PLTR's consensus CAGR is ~73%; times the
    old 1.5 multiplier that is 109% — revenue growing FORTY-FOLD in five
    years, an assumption no analyst made. The published high was 77%."""
    from wbj.specialists import valuation as val

    hyper = [_est("2031-01-25", _REVENUE0 * 15.0, 8, spread=0.05),
             _est("2030-01-25", _REVENUE0 * 9.0, 8, spread=0.05),
             _est("2029-01-25", _REVENUE0 * 5.5, 9, spread=0.05),
             _est("2028-01-25", _REVENUE0 * 3.3, 10, spread=0.05),
             _est("2027-01-25", _REVENUE0 * 1.9, 12, spread=0.05)]
    out = val.run(_packet_with(hyper, capex=-6.04e9), {"wacc": 0.146})

    growths = {s.name: s.assumptions["growth"] for s in out.scenarios}
    base = growths["Base"]
    assert growths["Bear"] < base < growths["Bull"]
    # The bull is the published high, not half again the base.
    assert growths["Bull"] < base * 1.5
    assert growths["Bull"] == pytest.approx((15.0 * 1.05) ** 0.2 - 1, abs=1e-6)


def test_the_multipliers_still_cover_a_consensus_with_no_dispersion():
    from wbj.specialists import valuation as val

    bare = [{"date": f"{y}-01-25", "revenueAvg": _REVENUE0 * m}
            for y, m in [(2027, 1.2), (2028, 1.4), (2029, 1.6),
                         (2030, 1.8), (2031, 2.0)]]
    out = val.run(_packet_with(bare, capex=-6.04e9), {"wacc": 0.146})
    growths = {s.name: s.assumptions["growth"] for s in out.scenarios}
    assert growths["Bear"] == pytest.approx(growths["Base"] * 0.5)
    assert growths["Bull"] == pytest.approx(growths["Base"] * 1.5)
    assert any("no low/high" in a for a in out.assumptions)


def test_the_assumption_says_which_kind_of_range_it_used():
    from wbj.specialists import valuation as val

    out = val.run(_packet_with(_ROWS, capex=-6.04e9), {"wacc": 0.146})
    declared = next(a for a in out.assumptions if "Base-case revenue growth" in a)
    assert "sourced dispersion rather than a multiple of the base" in declared
