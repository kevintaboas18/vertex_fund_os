"""Cerebro 9's cross-check, on a model that can actually pass it.

"A mismatch exposes a modeling error." It did — in this repo, not in the
assumptions. `economic_profit_value(ic0, eps, wacc)` requires ECONOMIC
PROFITS (`NOPAT_t - WACC * IC_{t-1}`), and the valuation specialist was
handing it `NOPAT * (1 - reinvestment_rate) * (1+g)^t`, which is free cash
flow. `IC0` was then added on top of a present value of cash flows, which
double-counts the capital base: on NVDA-shaped inputs the result read 31.6%
above the FCFF DCF.

So `FCFF_ECONOMIC_PROFIT_RECONCILIATION_FAILED` fired on every company, and
`VAL-EVAEV-021` published a number that was neither model.

The other half of DECISION_RULES.md's reconciliation family is financial's
core-27 check, which asks for "an explanation" and restated the two numbers
instead of giving one.
"""

from __future__ import annotations

import pytest

from wbj.engines import valuation_engine as ve


_BASE = dict(growth=0.0505, margin=0.6038, wacc_value=0.1460, tv_growth=0.025,
             revenue0=215_938.0, tax_rate=0.21, roic_value=0.87, years=5)


def _fcff_dcf_ev(**kw):
    """Enterprise value the FCFF DCF prices, from the same forecast."""
    terminal = ve.terminal_year_metrics(**kw)
    w, n = kw["wacc_value"], kw["years"]
    return (sum(f / (1 + w) ** (t + 1) for t, f in enumerate(terminal["explicit_fcffs"]))
            + terminal["terminal_value"] / (1 + w) ** n)


# --- the two routes have to land on one enterprise value ------------------


def test_the_two_models_reconcile():
    """Cerebro 9's whole point: same assumptions, two routes, one answer."""
    ep = ve.economic_profit_ev(ic0=120_000.0, **_BASE)
    assert ep.is_valid
    assert ve.reconciles(ep, ve._ok(_fcff_dcf_ev(**_BASE), unit="usd")) is True


@pytest.mark.parametrize("ic0", [0.0, 50_000.0, 120_000.0, 300_000.0, 900_000.0])
def test_the_capital_base_does_not_move_the_enterprise_value(ic0):
    """The identity's real test. `IC0` enters three times — as the opening
    book, through the WACC charges it generates, and again subtracted from
    the terminal value — and those have to cancel. The old formula added it
    once and cancelled it nowhere, so the answer moved dollar-for-dollar
    with a number that describes the past, not the forecast."""
    assert ve.economic_profit_ev(ic0=ic0, **_BASE).value == \
        pytest.approx(_fcff_dcf_ev(**_BASE), rel=1e-9)


@pytest.mark.parametrize("growth,margin,wacc,tv_growth", [
    (0.02, 0.20, 0.09, 0.02),
    (0.30, 0.65, 0.18, 0.03),
    (0.00, 0.10, 0.07, 0.00),
    (0.12, 0.45, 0.11, 0.025),
])
def test_they_reconcile_across_the_assumption_space(growth, margin, wacc, tv_growth):
    """One company reconciling could be arithmetic luck."""
    kw = {**_BASE, "growth": growth, "margin": margin,
          "wacc_value": wacc, "tv_growth": tv_growth}
    ep = ve.economic_profit_ev(ic0=250_000.0, **kw)
    assert ep.is_valid
    assert ep.value == pytest.approx(_fcff_dcf_ev(**kw), rel=1e-9)


def test_a_terminal_growth_at_or_above_wacc_is_refused_not_priced():
    """The same refusal the DCF makes, at the same boundary — a negative
    Gordon denominator prices nothing."""
    ep = ve.economic_profit_ev(ic0=100_000.0, **{**_BASE, "tv_growth": 0.20})
    assert ep.is_null
    assert "TERMINAL_GROWTH_GE_WACC" in ep.warnings


def test_the_old_primitive_still_prices_a_real_ep_series():
    """`economic_profit_value` is not wrong, it was misused. Given genuine
    economic profits it is still `IC0 + PV(them)`."""
    v = ve.economic_profit_value(1_000.0, [100.0, 100.0], 0.10)
    assert v.value == pytest.approx(1_000.0 + 100 / 1.1 + 100 / 1.21)


def test_only_one_function_claims_the_formula_id():
    """Both used to carry `@register_formula(id="VAL-EVAEV-021")`, and the
    registry keeps whichever is decorated last — so which model the audit
    trail named depended on source order."""
    from wbj.core.formulas import REGISTRY

    assert REGISTRY["VAL-EVAEV-021"].fn is ve.economic_profit_ev


# --- financial's reconciliation has to EXPLAIN, not restate ---------------


def test_the_core27_flag_names_what_drives_the_gap():
    """DECISION_RULES.md asks for "an explanation" when the weighted score
    and the 27-metric score differ by more than 1.5. The flag restated the
    two numbers the reader could already see.

    They diverge for one structural reason: a dimension under
    MISSING_DATA_POLICY.md's 0.70 floor is NOT_SCORABLE but stays
    APPLICABLE, so the weighted average carries it as a zero while the
    core-27 sample simply omits its metrics."""
    from wbj.core.nullstates import NullState, Value
    from wbj.core.scoring import Dimension
    from wbj.specialists.financial import reconciliation_check

    scored = Dimension(name="margins", max_points=3.0,
                       metric_scores=[(1.0, Value.of(9.0, unit="score"))])
    starved = Dimension(
        name="revenue_quality_and_growth", max_points=3.0,
        metric_scores=[(0.6, Value.of(9.0, unit="score")),
                       (0.4, Value.null(NullState.NOT_SCORABLE, unit="score"))])

    flag = reconciliation_check(6.72, 8.80, dimensions=[scored, starved])
    assert flag is not None
    assert "revenue_quality_and_growth" in flag
    assert "0.60" in flag
    # The scored one is not blamed for a gap it did not cause.
    assert "margins" not in flag


def test_within_tolerance_there_is_no_flag_to_explain():
    from wbj.specialists.financial import reconciliation_check

    assert reconciliation_check(8.0, 8.9, dimensions=[]) is None


def test_a_gap_with_every_dimension_scored_says_so():
    """Then it is the band distribution, not missing evidence, and sending
    the reader to look for an unscored dimension would waste their time."""
    from wbj.core.nullstates import Value
    from wbj.core.scoring import Dimension
    from wbj.specialists.financial import reconciliation_check

    scored = Dimension(name="margins", max_points=3.0,
                       metric_scores=[(1.0, Value.of(9.0, unit="score"))])
    flag = reconciliation_check(6.0, 9.0, dimensions=[scored])
    assert "band distribution" in flag
