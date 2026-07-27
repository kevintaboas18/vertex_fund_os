"""Tests for `wbj.engines.valuation_engine` (Task 13).

Sources of truth: `Cerebro/special_sauces/INSTITUTIONAL_VALUATION_ENGINE.md`
and `Cerebro/06_valuation_analysis/FORMULAS.md` (VAL-001..044).
"""

from __future__ import annotations

import statistics

import pytest

from wbj.core.formulas import REGISTRY
from wbj.core.nullstates import NullState, Value
from wbj.engines import valuation_engine as ve
from wbj.schemas.valuation import (
    DCFCommonInputs,
    EnsembleModelInput,
    MonteCarloInputs,
    MonteCarloRange,
    ReverseDCFInputs,
    ScenarioInput,
)

# ============================================================================
# Step 1 — brief's closed-form cases, verbatim
# ============================================================================


def test_gordon_terminal_math():
    """single FCFF=100 growing 2%, wacc 10% -> TV = 100*1.02/0.08 = 1275."""
    tv = ve.gordon_terminal_value(fcff_n=100.0, g=0.02, wacc_value=0.10)
    assert tv.is_valid
    assert abs(tv.value - 1275.0) < 1e-9


def test_g_greater_than_wacc_refused():
    tv = ve.gordon_terminal_value(fcff_n=100.0, g=0.10, wacc_value=0.10)
    assert tv.is_null and tv.state == NullState.NOT_MEANINGFUL

    tv2 = ve.gordon_terminal_value(fcff_n=100.0, g=0.12, wacc_value=0.10)
    assert tv2.is_null and tv2.state == NullState.NOT_MEANINGFUL

    result = ve.dcf_value(fcffs=[80.0, 90.0], wacc_value=0.10, terminal_growth=0.10)
    assert result.ev.is_null and result.ev.state == NullState.NOT_MEANINGFUL
    assert result.warnings


def test_terminal_share_warning_above_75pct():
    # Low near-term FCFF, terminal growth close to WACC -> TV dominates EV.
    result = ve.dcf_value(fcffs=[10.0, 10.0], wacc_value=0.10, terminal_growth=0.08)
    assert result.terminal_share.is_valid
    assert result.terminal_share.value > 0.75
    assert "TERMINAL_VALUE_SHARE_ABOVE_75PCT" in result.terminal_share.warnings
    assert result.warnings  # dcf_value surfaces the same warning


def test_terminal_share_below_75pct_no_warning():
    # 10 years of explicit FCFF growing 8%/yr, modest terminal growth
    # relative to WACC -> terminal value no longer dominates EV.
    fcffs = [100.0 * (1.08**t) for t in range(1, 11)]
    result = ve.dcf_value(fcffs=fcffs, wacc_value=0.12, terminal_growth=0.02)
    assert result.terminal_share.value < 0.75
    assert result.warnings == []


def test_wacc():
    """E=800, D=200, Ke=10%, Kd=5%, tax=25% -> 0.8*.10 + 0.2*.05*.75 = 8.75%."""
    w = ve.wacc(e=800.0, d=200.0, ke=0.10, kd=0.05, tax_rate=0.25)
    assert w.is_valid
    assert abs(w.value - 0.0875) < 1e-12


def test_wacc_capital_base_nonpositive_refused():
    w = ve.wacc(e=0.0, d=0.0, ke=0.10, kd=0.05, tax_rate=0.25)
    assert w.is_null and w.state == NullState.NOT_MEANINGFUL


def test_wacc_sensitivity_100bp():
    w = ve.wacc(e=800.0, d=200.0, ke=0.10, kd=0.05, tax_rate=0.25)
    sens = ve.wacc_sensitivity(w, bp=100)
    assert abs(sens.minus_bp.value - 0.0775) < 1e-12
    assert abs(sens.plus_bp.value - 0.0975) < 1e-12
    assert sens.base.value == w.value


def test_equity_bridge_and_per_share():
    equity = ve.equity_bridge(
        ev=1000.0, cash=50.0, nonop=0.0, debt=200.0, lease_debt_value=0.0,
        preferred=0.0, minority=0.0, pension=0.0,
    )
    assert abs(equity.value - 850.0) < 1e-9

    ps = ve.per_share(equity.value, diluted=100.0)
    assert abs(ps.value - 8.5) < 1e-9


def test_per_share_nonpositive_diluted_refused():
    ps = ve.per_share(equity=850.0, diluted=0.0)
    assert ps.is_null and ps.state == NullState.NOT_MEANINGFUL


# Parameters chosen (see task-13-report.md self-review) so that
# `_constant_growth_per_share` is strictly monotonic increasing in growth
# over the bounds used — the model has a single interior peak (reinvestment
# rate = g/ROIC throttles every explicit year's FCFF at the *same* g used to
# compound revenue), so bounds must stay on one side of it for the solver to
# recover a unique, correct root.
_RDCF_COMMON = dict(revenue0=1000.0, tax_rate=0.25, roic=0.30, years=5, shares=100.0, net_debt=200.0)


def test_reverse_dcf_recovers_known_growth():
    g_star = 0.03
    margin = 0.20
    wacc_value = 0.09
    tv_growth = 0.025

    price = ve._constant_growth_per_share(
        g_star, margin, wacc_value, tv_growth,
        revenue0=_RDCF_COMMON["revenue0"], tax_rate=_RDCF_COMMON["tax_rate"],
        roic_value=_RDCF_COMMON["roic"], years=_RDCF_COMMON["years"],
        shares=_RDCF_COMMON["shares"], net_debt=_RDCF_COMMON["net_debt"],
    )

    inputs = ReverseDCFInputs(
        **_RDCF_COMMON, margin=margin, wacc=wacc_value, tv_growth=tv_growth,
        growth_bounds=(-0.05, 0.055),
    )
    result = ve.reverse_dcf(price=price, shares=_RDCF_COMMON["shares"], base_inputs=inputs)

    assert result.converged
    assert result.implied_growth.is_valid
    assert abs(result.implied_growth.value - g_star) < 1e-4
    # implied margin, solved holding growth at consensus (defaults to the
    # implied growth), should recover the margin used to build the price.
    assert result.implied_margin.is_valid
    assert abs(result.implied_margin.value - margin) < 1e-3


def test_reverse_dcf_no_bracket_returns_not_scorable():
    inputs = ReverseDCFInputs(
        **_RDCF_COMMON, margin=0.20, wacc=0.09, tv_growth=0.025,
        growth_bounds=(0.0, 0.001),  # tiny bracket, unlikely to contain the root
    )
    result = ve.reverse_dcf(price=1e9, shares=_RDCF_COMMON["shares"], base_inputs=inputs)
    assert not result.converged
    assert result.implied_growth.is_null and result.implied_growth.state == NullState.NOT_SCORABLE


def test_monte_carlo_deterministic_given_seed():
    inputs = MonteCarloInputs(
        revenue0=1000.0, shares=100.0, tax_rate=0.25, roic=0.30, years=5, net_debt=200.0,
        growth_range=MonteCarloRange(low=0.0, mode=0.04, high=0.06),
        margin_range=MonteCarloRange(low=0.15, mode=0.20, high=0.25),
        wacc_range=MonteCarloRange(low=0.08, mode=0.09, high=0.11),
        tv_growth=0.025,
    )
    r1 = ve.monte_carlo(inputs, n=500, seed=42)
    r2 = ve.monte_carlo(inputs, n=500, seed=42)

    assert r1.p10.value == r2.p10.value
    assert r1.p25.value == r2.p25.value
    assert r1.median.value == r2.median.value
    assert r1.p75.value == r2.p75.value
    assert r1.p90.value == r2.p90.value
    assert r1.seed == 42 and r1.trials == 500


def test_monte_carlo_different_seed_differs():
    inputs = MonteCarloInputs(
        revenue0=1000.0, shares=100.0, tax_rate=0.25, roic=0.30, years=5, net_debt=200.0,
        growth_range=MonteCarloRange(low=0.0, mode=0.04, high=0.06),
        margin_range=MonteCarloRange(low=0.15, mode=0.20, high=0.25),
        wacc_range=MonteCarloRange(low=0.08, mode=0.09, high=0.11),
        tv_growth=0.025,
    )
    r1 = ve.monte_carlo(inputs, n=500, seed=1)
    r2 = ve.monte_carlo(inputs, n=500, seed=2)
    assert r1.median.value != r2.median.value
    assert r1.p10.value <= r1.p25.value <= r1.median.value <= r1.p75.value <= r1.p90.value


def test_scenario_probabilities_must_sum_to_1():
    bear = ScenarioInput(probability=0.3, growth=0.01, margin=0.15, wacc=0.10, tv_growth=0.02)
    base = ScenarioInput(probability=0.3, growth=0.04, margin=0.20, wacc=0.09, tv_growth=0.025)
    bull = ScenarioInput(probability=0.3, growth=0.06, margin=0.25, wacc=0.08, tv_growth=0.03)  # sums to 0.9
    common = DCFCommonInputs(revenue0=1000.0, shares=100.0, tax_rate=0.25, roic=0.30, years=5, net_debt=200.0)

    with pytest.raises(ValueError):
        ve.scenarios(bear, base, bull, common)


def test_scenarios_weighted_value_matches_probability_sum():
    bear = ScenarioInput(probability=0.25, growth=0.0, margin=0.15, wacc=0.10, tv_growth=0.02)
    base = ScenarioInput(probability=0.50, growth=0.04, margin=0.20, wacc=0.09, tv_growth=0.025)
    bull = ScenarioInput(probability=0.25, growth=0.06, margin=0.25, wacc=0.08, tv_growth=0.03)
    common = DCFCommonInputs(revenue0=1000.0, shares=100.0, tax_rate=0.25, roic=0.30, years=5, net_debt=200.0)

    result = ve.scenarios(bear, base, bull, common)
    assert result.probabilities_sum == pytest.approx(1.0)
    expected_weighted = (
        0.25 * result.bear_value.value + 0.50 * result.base_value.value + 0.25 * result.bull_value.value
    )
    assert result.weighted_value.value == pytest.approx(expected_weighted)


def test_scenario_offending_branch_refused_others_still_compute():
    """A single scenario with tv_growth >= wacc (e.g. an aggressive bull with
    a low WACC) must refuse *only that branch* (NOT_MEANINGFUL + warning) and
    still compute bear/base — never crash all three with an unhandled
    ValueError from the shared pricing model."""
    bear = ScenarioInput(probability=0.25, growth=0.0, margin=0.15, wacc=0.10, tv_growth=0.02)
    base = ScenarioInput(probability=0.50, growth=0.04, margin=0.20, wacc=0.09, tv_growth=0.025)
    # bull: tv_growth (0.09) >= wacc (0.08) -> offending branch
    bull = ScenarioInput(probability=0.25, growth=0.06, margin=0.25, wacc=0.08, tv_growth=0.09)
    common = DCFCommonInputs(revenue0=1000.0, shares=100.0, tax_rate=0.25, roic=0.30, years=5, net_debt=200.0)

    result = ve.scenarios(bear, base, bull, common)

    assert result.bear_value.is_valid
    assert result.base_value.is_valid
    assert result.bull_value.is_null and result.bull_value.state == NullState.NOT_MEANINGFUL
    assert any("TERMINAL_GROWTH_GE_WACC" in w for w in result.bull_value.warnings)
    # weighted value cannot include a refused branch -> refused too, with a warning.
    assert result.weighted_value.is_null and result.weighted_value.state == NullState.NOT_MEANINGFUL
    assert result.warnings


def test_reverse_dcf_invalid_inputs_g_ge_wacc_distinct_from_no_bracket():
    """base_inputs with tv_growth >= wacc is an economically invalid input,
    not a 'root not bracketed' failure — it must be diagnosed BEFORE brentq
    is called and reported with its own INVALID_INPUTS_G_GE_WACC warning
    (never mislabeled NO_SIGN_CHANGE_IN_GROWTH_BOUNDS)."""
    inputs = ReverseDCFInputs(
        **_RDCF_COMMON, margin=0.20, wacc=0.05, tv_growth=0.08,  # tv_growth >= wacc
    )
    result = ve.reverse_dcf(price=15.0, shares=_RDCF_COMMON["shares"], base_inputs=inputs)
    assert not result.converged
    assert result.implied_growth.is_null and result.implied_growth.state == NullState.NOT_MEANINGFUL
    assert any("INVALID_INPUTS_G_GE_WACC" in w for w in result.implied_growth.warnings)
    # the diagnosis must NOT be the no-bracket one
    assert not any("NO_SIGN_CHANGE" in w for w in result.implied_growth.warnings)
    assert any("INVALID_INPUTS_G_GE_WACC" in w for w in result.warnings)


def test_economic_profit_reconciles_with_fcff():
    """IC0=1000, ROIC=15%, WACC=10%, constant g=5% (< WACC) throughout,
    reinvestment_rate = g/ROIC = 1/3 every year -> FCFF-DCF EV must
    reconcile to invested-capital-plus-PV(economic-profit) EV within 1%."""
    ic0, roic_value, wacc_value, g = 1000.0, 0.15, 0.10, 0.05
    reinvestment_rate = g / roic_value

    nopat_1 = roic_value * ic0
    reinvestment_1 = nopat_1 * reinvestment_rate
    fcff_1 = nopat_1 - reinvestment_1
    ic1 = ic0 + reinvestment_1

    nopat_2 = roic_value * ic1
    reinvestment_2 = nopat_2 * reinvestment_rate
    fcff_2 = nopat_2 - reinvestment_2

    dcf = ve.dcf_value(fcffs=[fcff_1, fcff_2], wacc_value=wacc_value, terminal_growth=g)
    assert dcf.ev.is_valid

    ep_1 = ve.eva(nopat_1, wacc_value, ic0)
    ep_2 = ve.eva(nopat_2, wacc_value, ic1)
    assert abs(ep_1.value - (nopat_1 - wacc_value * ic0)) < 1e-9

    explicit_ep_ev = ve.economic_profit_value(ic0, [ep_1.value, ep_2.value], wacc_value)
    terminal_ep = ep_2.value * (1 + g)
    tv_ep = terminal_ep / (wacc_value - g)
    total_ep_ev = explicit_ep_ev.value + tv_ep / (1 + wacc_value) ** 2

    assert ve.reconciles(dcf.ev, Value.of(total_ep_ev, unit="usd"), tol=0.01)
    # and directly, since dcf.ev and total_ep_ev should both land near 2000.0
    assert abs(dcf.ev.value - total_ep_ev) / dcf.ev.value < 0.01


# ============================================================================
# Supplementary coverage: remaining interface functions
# ============================================================================


def test_normalized_ebit():
    result = ve.normalized_ebit(reported=1000.0, unusual_gains=50.0, nonrecurring=30.0, misclassified=-20.0)
    assert result.value == pytest.approx(1000 - 50 + 30 - 20)


def test_rd_capitalize_hand_computed():
    rd_history = [80.0, 90.0, 100.0]  # oldest -> newest; [-1] is current year
    life = 3
    result = ve.rd_capitalize(rd_history, life, reported_ebit=500.0)

    expected_asset = 100 * (1 - 0 / 3) + 90 * (1 - 1 / 3) + 80 * (1 - 2 / 3)
    expected_amortization = (100 + 90 + 80) / 3
    expected_adjusted_ebit = 500.0 + 100.0 - expected_amortization

    assert result.asset.value == pytest.approx(expected_asset)
    assert result.amortization.value == pytest.approx(expected_amortization)
    assert result.adjusted_ebit.value == pytest.approx(expected_adjusted_ebit)


def test_rd_capitalize_without_reported_ebit_is_not_applicable():
    result = ve.rd_capitalize([80.0, 90.0, 100.0], 3)
    assert result.adjusted_ebit.is_null and result.adjusted_ebit.state == NullState.NOT_APPLICABLE


def test_rd_life_nonpositive_refused():
    assert ve.rd_asset([100.0], 0).is_null
    assert ve.rd_amortization([100.0], -1).is_null


def test_lease_debt_pv():
    commitments = [100.0, 100.0, 100.0]
    pretax_kd = 0.06
    result = ve.lease_debt(commitments, pretax_kd)
    expected = sum(c / (1 + pretax_kd) ** (t + 1) for t, c in enumerate(commitments))
    assert result.value == pytest.approx(expected)


def test_nopat():
    assert ve.nopat(200.0, 0.25).value == pytest.approx(150.0)


def test_invested_capital_reconciled_within_5pct():
    result = ve.invested_capital(
        debt=200.0, equity=800.0, excess_cash=50.0, debt_like_claims=0.0,
        operating_assets=970.0, operating_liabilities=0.0,
    )
    assert result.financing_view.value == pytest.approx(950.0)
    assert result.operating_view.value == pytest.approx(970.0)
    assert result.reconciled is True
    assert result.warnings == []


def test_invested_capital_warns_when_views_differ_over_5pct():
    result = ve.invested_capital(
        debt=200.0, equity=800.0, excess_cash=50.0, debt_like_claims=0.0,
        operating_assets=1200.0, operating_liabilities=200.0,
    )
    assert result.financing_view.value == pytest.approx(950.0)
    assert result.operating_view.value == pytest.approx(1000.0)
    assert result.reconciled is False
    assert "INVESTED_CAPITAL_VIEWS_DIFFER_GT_5PCT" in result.warnings


def test_invested_capital_financing_only_no_reconciliation():
    result = ve.invested_capital(debt=200.0, equity=800.0, excess_cash=50.0)
    assert result.operating_view is None
    assert result.reconciled is None


def test_roic():
    assert ve.roic(150.0, 1000.0).value == pytest.approx(0.15)


def test_roic_nonpositive_capital_refused():
    r = ve.roic(150.0, 0.0)
    assert r.is_null and r.state == NullState.NOT_MEANINGFUL


def test_spread():
    assert ve.spread(0.15, 0.0875).value == pytest.approx(0.0625)


def test_eva():
    result = ve.eva(nopat_value=150.0, wacc_value=0.0875, beginning_ic=1000.0)
    assert result.value == pytest.approx(150.0 - 0.0875 * 1000.0)


def test_incremental_roic():
    assert ve.incremental_roic(15.0, 100.0).value == pytest.approx(0.15)


def test_incremental_roic_zero_delta_ic_refused():
    r = ve.incremental_roic(15.0, 0.0)
    assert r.is_null and r.state == NullState.NOT_MEANINGFUL


def test_fundamental_growth():
    assert ve.fundamental_growth(0.4, 0.125).value == pytest.approx(0.05)


def test_unlever_relever_beta_roundtrip():
    levered = 1.2
    tax_rate = 0.25
    de = 0.5
    unlevered = ve.unlever_beta(levered, tax_rate, de)
    assert unlevered.value == pytest.approx(1.2 / (1 + 0.75 * 0.5))

    relevered = ve.relever_beta(unlevered.value, tax_rate, de)
    assert relevered.value == pytest.approx(levered)  # same D/E -> roundtrip


def test_cost_of_equity():
    ke = ve.cost_of_equity(rf=0.04, beta=1.2, erp=0.055, crp=0.0)
    assert ke.value == pytest.approx(0.04 + 1.2 * 0.055)


def test_synthetic_kd_high_coverage_no_warning():
    kd = ve.synthetic_kd(rf=0.04, interest_coverage=10.0)
    assert kd.value == pytest.approx(0.04 + 0.0069)
    assert kd.warnings == []


def test_synthetic_kd_below_1_5x_solvency_warning():
    kd = ve.synthetic_kd(rf=0.04, interest_coverage=1.0)
    assert "SOLVENCY_WARNING_INTEREST_COVERAGE_BELOW_1_5X" in kd.warnings


def test_fcff_reconciles_with_nopat_minus_reinvestment():
    ebit, tax_rate, dna, capex, dnwc = 200.0, 0.25, 30.0, 45.0, 10.0
    direct = ve.fcff(ebit, tax_rate, dna, capex, dnwc)

    nopat_value = ve.nopat(ebit, tax_rate).value
    reinvestment = capex + dnwc - dna
    via_nopat = ve.fcff_via_nopat(nopat_value, reinvestment)

    assert direct.value == pytest.approx(via_nopat.value)
    assert direct.value == pytest.approx(125.0)


def test_enterprise_value_matches_dcf_value_ev():
    fcffs = [100.0, 105.0]
    wacc_value = 0.10
    terminal_growth = 0.03
    tv = ve.gordon_terminal_value(fcffs[-1], terminal_growth, wacc_value)
    ev_direct = ve.enterprise_value(fcffs, wacc_value, tv.value)
    dcf = ve.dcf_value(fcffs, wacc_value, terminal_growth)
    assert ev_direct.value == pytest.approx(dcf.ev.value)


def test_fcfe_and_fcfe_value():
    fcfe1 = ve.fcfe(net_income=100.0, dna=20.0, capex=35.0, dnwc=10.0, net_borrowing=5.0)
    assert fcfe1.value == pytest.approx(100 + 20 - 35 - 10 + 5)

    fcfes = [80.0, 84.0]
    ke, g = 0.11, 0.03
    result = ve.fcfe_value(fcfes, ke, g)
    expected = sum(f / (1 + ke) ** (t + 1) for t, f in enumerate(fcfes))
    expected += (fcfes[-1] * (1 + g) / (ke - g)) / (1 + ke) ** len(fcfes)
    assert result.value == pytest.approx(expected)


def test_fcfe_value_refuses_when_terminal_growth_ge_cost_of_equity():
    result = ve.fcfe_value([80.0, 84.0], cost_equity_value=0.05, terminal_growth=0.05)
    assert result.is_null and result.state == NullState.NOT_MEANINGFUL


def test_residual_income_and_value():
    ri1 = ve.residual_income(net_income=60.0, cost_equity_value=0.10, beginning_book_equity=500.0)
    assert ri1.value == pytest.approx(60 - 0.10 * 500)

    ris = [20.0, 22.0]
    ke = 0.10
    result = ve.residual_income_value(book_equity0=500.0, ris=ris, cost_equity_value=ke)
    expected = 500.0 + sum(r / (1 + ke) ** (t + 1) for t, r in enumerate(ris))
    assert result.value == pytest.approx(expected)


def test_justified_pe():
    result = ve.justified_pe(g=0.03, roe=0.15, ke=0.09)
    expected = (1 - 0.03 / 0.15) / (0.09 - 0.03)
    assert result.value == pytest.approx(expected)


def test_justified_pe_nonpositive_roe_refused():
    r = ve.justified_pe(g=0.03, roe=0.0, ke=0.09)
    assert r.is_null and r.state == NullState.NOT_MEANINGFUL


def test_justified_pe_growth_ge_ke_refused():
    r = ve.justified_pe(g=0.10, roe=0.15, ke=0.09)
    assert r.is_null and r.state == NullState.NOT_MEANINGFUL


def test_justified_ev_sales():
    result = ve.justified_ev_sales(margin=0.20, tax_rate=0.25, g=0.03, roic_value=0.15, wacc_value=0.09)
    expected = (0.20 * 0.75) * (1 - 0.03 / 0.15) / (0.09 - 0.03)
    assert result.value == pytest.approx(expected)
    assert result.value == pytest.approx(2.0)


def test_justified_ev_sales_growth_ge_wacc_refused():
    r = ve.justified_ev_sales(margin=0.20, tax_rate=0.25, g=0.10, roic_value=0.15, wacc_value=0.09)
    assert r.is_null and r.state == NullState.NOT_MEANINGFUL


def test_hist_zscore_robust():
    history = [10.0, 12.0, 11.0, 13.0, 9.0]
    median = statistics.median(history)
    mad = statistics.median(abs(x - median) for x in history)
    result = ve.hist_zscore(14.0, history)
    assert result.value == pytest.approx((14.0 - median) / (1.4826 * mad))


def test_hist_zscore_zero_mad_refused():
    r = ve.hist_zscore(10.0, [5.0, 5.0, 5.0])
    assert r.is_null and r.state == NullState.NOT_MEANINGFUL


def test_ensemble_weighted_value_and_dispersion():
    models = [
        EnsembleModelInput(label="dcf", value=Value.of(10.0, unit="usd_per_share"), weight=2.0),
        EnsembleModelInput(label="multiples", value=Value.of(12.0, unit="usd_per_share"), weight=1.0),
        EnsembleModelInput(label="ep", value=Value.of(8.0, unit="usd_per_share"), weight=1.0),
    ]
    result = ve.ensemble(models)
    assert result.value.value == pytest.approx((10 * 2 + 12 * 1 + 8 * 1) / 4)
    assert result.dispersion.value == pytest.approx(statistics.pstdev([10.0, 12.0, 8.0]))


def test_ensemble_excludes_null_models():
    models = [
        EnsembleModelInput(label="dcf", value=Value.of(10.0, unit="usd_per_share"), weight=1.0),
        EnsembleModelInput(label="broken", value=Value.null(NullState.NOT_SCORABLE), weight=1.0),
    ]
    result = ve.ensemble(models)
    assert result.value.value == pytest.approx(10.0)


def test_ensemble_all_null_returns_not_scorable():
    models = [EnsembleModelInput(label="broken", value=Value.null(NullState.NOT_SCORABLE), weight=1.0)]
    result = ve.ensemble(models)
    assert result.value.is_null and result.value.state == NullState.NOT_SCORABLE


def test_margin_of_safety():
    result = ve.margin_of_safety(value=100.0, price=80.0)
    assert result.value == pytest.approx(0.20)


def test_margin_of_safety_negative_when_price_above_value():
    result = ve.margin_of_safety(value=80.0, price=100.0)
    assert result.value == pytest.approx(-0.25)


def test_margin_of_safety_zero_value_refused():
    r = ve.margin_of_safety(value=0.0, price=10.0)
    assert r.is_null and r.state == NullState.NOT_MEANINGFUL


# ============================================================================
# REGISTRY compliance
# ============================================================================

_EXPECTED_VAL_IDS = [
    "VAL-NORM-001", "VAL-RD-002", "VAL-RDA-003", "VAL-LEASE-004", "VAL-FCFF-005",
    "VAL-WACC-007", "VAL-KE-008", "VAL-UBETA-009", "VAL-LBETA-010", "VAL-KD-011",
    "VAL-TVG-012", "VAL-EV-014", "VAL-EQ-015", "VAL-PS-016", "VAL-FCFE-017",
    "VAL-FCFEV-018", "VAL-EVA-020", "VAL-EVAEV-021", "VAL-RI-022", "VAL-RIV-023",
    "VAL-RDCF-027", "VAL-JPE-032", "VAL-JEVS-033", "VAL-ZHIST-035", "VAL-SCEN-036",
    "VAL-MC-037", "VAL-MOS-040", "VAL-TVS-042", "VAL-REINV-043", "VAL-ENSEMBLE-044",
]


@pytest.mark.parametrize("val_id", _EXPECTED_VAL_IDS)
def test_every_interface_formula_registered(val_id):
    assert val_id in REGISTRY
    assert REGISTRY[val_id].version == "2.0.0"
