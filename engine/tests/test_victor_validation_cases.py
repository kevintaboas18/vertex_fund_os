"""Victor's own acceptance cases, encoded by ID.

Each `Cerebro/*/VALIDATION_TESTS.md` states a table of numbered cases:
inputs on the left, the required result on the right. They are the spec's
own definition of "working".

Twenty of the fifty-seven had no test in this suite naming them. The
BEHAVIOUR was right -- every one of these passed when checked by hand --
but nothing tied the behaviour to the case, so a regression would surface
as an anonymous failure somewhere else, or not at all. These encode the
twenty, citing the ID, so a break says which of Victor's cases broke.
"""

from __future__ import annotations

import pytest

from wbj.engines import valuation_engine as ve


# ===========================================================================
# 06_valuation_analysis
# ===========================================================================


def test_val_t002_wacc_equals_growth_is_refused():
    """VAL-T002 | WACC=8%, g=8% | Reject model: denominator zero"""
    v = ve.gordon_terminal_value(100.0, 0.08, 0.08)
    assert v.is_null
    assert "TERMINAL_GROWTH_GE_WACC" in v.warnings


def test_val_t003_growth_above_wacc_is_refused():
    """VAL-T003 | WACC=7%, g=8% | Reject model: g>=WACC"""
    v = ve.gordon_terminal_value(100.0, 0.08, 0.07)
    assert v.is_null
    assert "TERMINAL_GROWTH_GE_WACC" in v.warnings


def test_val_t004_terminal_reinvestment_is_growth_over_roic():
    """VAL-T004 | ROIC=20%, g=4% | Terminal reinvestment rate=20%"""
    assert ve._terminal_reinvestment_rate(0.04, 0.20) == pytest.approx(0.20)


def test_val_t005_the_equity_bridge_and_per_share():
    """VAL-T005 | EV=1000, cash=100, debt=300, diluted shares=80 |
    Equity=800; value/share=10"""
    equity = ve.equity_bridge(1000.0, 100.0, 0.0, 300.0, 0.0, 0.0, 0.0, 0.0)
    assert equity.value == pytest.approx(800.0)
    assert ve.per_share(equity.value, 80.0).value == pytest.approx(10.0)


def _scenario(probability, growth):
    return ve.ScenarioInput(probability=probability, growth=growth, margin=0.20,
                            wacc=0.10, tv_growth=0.02)


def _common():
    return ve.DCFCommonInputs(revenue0=1000.0, shares=100.0, tax_rate=0.21,
                              roic=0.15, years=5, net_debt=0.0)


def test_val_t006_probabilities_summing_to_one_pass():
    """VAL-T006 | Scenario probabilities 20%,60%,20% | Sum=100%; pass"""
    result = ve.scenarios(_scenario(0.2, 0.03), _scenario(0.6, 0.05),
                          _scenario(0.2, 0.07), _common())
    assert result is not None
    assert result.weighted_value.is_valid


def test_val_t006_probabilities_that_do_not_sum_are_refused():
    """The converse the case implies: Cerebro requires the sum to be 1."""
    with pytest.raises(ValueError):
        ve.scenarios(_scenario(0.2, 0.03), _scenario(0.5, 0.05),
                     _scenario(0.2, 0.07), _common())


def test_val_t007_a_dominant_terminal_value_is_flagged():
    """VAL-T007 | Terminal PV=800, EV=1000 | Terminal share=80%;
    high-sensitivity flag"""
    v = ve.terminal_share(800.0, 1000.0)
    assert v.value == pytest.approx(0.80)
    assert "TERMINAL_VALUE_SHARE_ABOVE_75PCT" in (v.warnings or [])


def test_val_t007_a_share_under_the_threshold_is_not_flagged():
    """It flags, it does not refuse — and only above 75%."""
    v = ve.terminal_share(700.0, 1000.0)
    assert v.value == pytest.approx(0.70)
    assert v.warnings == []


def test_val_t009_the_two_models_reconcile_under_the_same_assumptions():
    """VAL-T009 | FCFF and EVA differ materially with same assumptions |
    Fail reconciliation; inspect reinvestment/capital

    The case calls a material difference a FAILURE to investigate. It
    fired on every company because the economic-profit model was being fed
    free cash flow (V-06); under one set of assumptions the two must agree.
    """
    kw = dict(growth=0.05, margin=0.20, wacc_value=0.10, tv_growth=0.02,
              revenue0=1000.0, tax_rate=0.21, roic_value=0.15, years=5)
    terminal = ve.terminal_year_metrics(**kw)
    fcff_ev = (sum(f / 1.10 ** (t + 1) for t, f in enumerate(terminal["explicit_fcffs"]))
               + terminal["terminal_value"] / 1.10 ** 5)
    assert ve.reconciles(ve.economic_profit_ev(ic0=600.0, **kw),
                         ve._ok(fcff_ev, unit="usd")) is True


# ===========================================================================
# 04_technical_momentum
# ===========================================================================


def test_tech_t002_true_range_spans_the_prior_close():
    """TECH-T002 | High=12, low=10, prior close=11 | True range=2"""
    import pandas as pd

    from wbj.engines import indicators as ind

    df = pd.DataFrame({"high": [11.5, 12.0], "low": [10.5, 10.0],
                       "close": [11.0, 11.0]})
    assert float(ind.true_range(df).iloc[-1]) == pytest.approx(2.0)


def test_tech_t009_distance_in_atr_is_signed_from_the_price():
    """TECH-T009 | Current close 100, resistance low 102, ATR 2 |
    Distance=1 ATR; nearby resistance"""
    from wbj.aggregate.synthesis import _point_distance

    _, distance_atr = _point_distance(102.0, 100.0, 2.0)
    assert distance_atr == pytest.approx(1.0)


def test_tech_t009_a_level_below_the_price_reads_negative():
    """The same formula the other way: PRICE_LEVEL_SYNTHESIS.md writes one
    signed expression for every level (see A-04)."""
    from wbj.aggregate.synthesis import _point_distance

    _, distance_atr = _point_distance(98.0, 100.0, 2.0)
    assert distance_atr == pytest.approx(-1.0)


# ===========================================================================
# 02_financial_analysis
# ===========================================================================


def test_fin_t006_loss_plus_negative_fcf_plus_issuance_triggers_override_1():
    """FIN-T006 | NI=-10, FCF=-20, equity issuance positive | Bad/Avoid
    override"""
    from wbj.specialists.financial import (is_externally_dependent,
                                           override_1_triggered)

    dependent = is_externally_dependent(0.0, 0.0, 5.0)
    assert dependent is True
    assert override_1_triggered(-10.0, -20.0, dependent) is True


def test_fin_t006_does_not_fire_without_external_dependence():
    """A loss-making company funding itself from cash on hand is a
    different case, and the override must not reach it."""
    from wbj.specialists.financial import override_1_triggered

    assert override_1_triggered(-10.0, -20.0, False) is False


def test_fin_t007_roic_below_wacc_blocks_excellent():
    """FIN-T007 | ROIC=9%, WACC=11% | No Excellent verdict"""
    from wbj.specialists.financial import override_2_triggered

    assert override_2_triggered(0.09, 0.11) is True
    assert override_2_triggered(0.13, 0.11) is False


def test_fin_t007_missing_inputs_never_trigger_the_override():
    """An unmeasured ROIC is not a ROIC below WACC."""
    from wbj.specialists.financial import override_2_triggered

    assert override_2_triggered(None, 0.11) is False
    assert override_2_triggered(0.09, None) is False


def test_fin_t008_twenty_seven_excellent_metrics_score_full_marks():
    """FIN-T008 | 27 valid metrics all Excellent | 54/54=100%"""
    from wbj.specialists.financial import core27_diagnostic

    valid_count, points, maximum, percent, score10 = core27_diagnostic([2] * 27)
    assert valid_count == 27
    assert points == pytest.approx(54.0)
    assert maximum == pytest.approx(54.0)
    assert percent == pytest.approx(100.0)
    assert score10 == pytest.approx(10.0)


def test_fin_t008_an_unscored_metric_leaves_the_denominator():
    """The companion: the ratio is over VALID metrics, so a metric with no
    band cannot drag the diagnostic down as if it had scored zero."""
    from wbj.specialists.financial import core27_diagnostic

    valid_count, _, maximum, percent, _ = core27_diagnostic([2] * 26 + [None])
    assert valid_count == 26
    assert maximum == pytest.approx(52.0)
    assert percent == pytest.approx(100.0)


# ===========================================================================
# 03_market_analysis
# ===========================================================================


def test_mkt_t007_an_issuer_tam_with_no_method_caps_the_score():
    """MKT-T007 | Only issuer TAM with no method | Source tier 4, score
    cap 6

    SCORING.md states the cap as a confidence condition: "TAM confidence
    <60 caps score at 6". Tier 4 carries a confidence of 45."""
    from wbj.specialists.market import (TAM_TIER_CONFIDENCE,
                                        tam_confidence_caps_dimension)

    assert TAM_TIER_CONFIDENCE[4] == pytest.approx(45.0)
    assert tam_confidence_caps_dimension(TAM_TIER_CONFIDENCE[4]) is True
    # A sourced, method-backed TAM does not get capped.
    assert tam_confidence_caps_dimension(TAM_TIER_CONFIDENCE[1]) is False


def test_mkt_t008_a_consensus_taken_after_the_release_cannot_measure_surprise():
    """MKT-T008 | Consensus snapshot taken after release | Reject surprise
    calculation

    A consensus captured after the print already contains it, so the
    "surprise" would be measured against the answer."""
    from wbj.specialists.market import earnings_surprise

    after = earnings_surprise(110.0, 100.0, snapshot_before_release=False)
    assert after.is_null

    before = earnings_surprise(110.0, 100.0, snapshot_before_release=True)
    assert before.is_valid
    assert before.value == pytest.approx(0.10)


# ===========================================================================
# 05_risk_analysis
# ===========================================================================


def test_rsk_t008_a_risk_floor_caps_the_profile_however_high_the_total():
    """RSK-T008 | Risk category=4/15, raw total=90 | Main profile capped
    Speculative"""
    from wbj.aggregate.gates import (CategoryConfidences, CategoryPoints,
                                     apply_gates)
    from wbj.aggregate.overrides import OVERRIDE_4_RISK_FLOOR, Override

    pts = CategoryPoints(business=20.0, financial=15.0, market=20.0,
                         technical=20.0, risk=4.0, valuation=11.0)
    confs = CategoryConfidences(business=95, financial=95, market=95,
                                technical=95, risk=95, valuation=95)
    # An Elite raw band with every other category at its maximum, and the
    # profile is still capped: the risk floor outranks the total.
    plain = apply_gates(90.0, pts, confs, [])
    assert plain.descriptive_band == "Elite raw score"
    assert plain.label == "Speculative"

    # And it says why, rather than capping silently.
    assert any("risk<=4/15" in w for w in plain.warnings)

    # Passing the override explicitly reaches the same verdict.
    floor = Override(id=OVERRIDE_4_RISK_FLOOR, effect="cap_speculative",
                     reason="Risk 4/15")
    assert apply_gates(90.0, pts, confs, [floor]).label == "Speculative"

    # A healthy risk score at the same total is NOT capped.
    healthy = CategoryPoints(business=20.0, financial=15.0, market=20.0,
                             technical=20.0, risk=11.0, valuation=4.0)
    assert apply_gates(90.0, healthy, confs, []).label != "Speculative"


def test_rsk_t009_a_forensic_screen_is_a_number_not_an_accusation():
    """RSK-T009 | Forensic M-score above screen threshold | Flag only; no
    accusation

    The engine reports the score and lets the gate read it; nothing in the
    output asserts manipulation."""
    from wbj.specialists.risk import beneish_m_score

    flagged = beneish_m_score(1.5, 1.2, 1.1, 1.4, 1.0, 1.0, 0.05, 1.1)
    assert flagged.is_valid
    # Above Beneish's own -1.78 screen.
    assert flagged.value > -1.78


# ===========================================================================
# The five that need the specialists, not the engines
# ===========================================================================


def test_mkt_t006_a_forecast_above_tam_fails_the_consistency_gate():
    """MKT-T006 | Forecast revenue exceeds TAM | Fail consistency gate"""
    from wbj.specialists.market import forecast_consistency_gate

    assert forecast_consistency_gate(120.0, 100.0) is False
    assert forecast_consistency_gate(80.0, 100.0) is True
    # The boundary is inclusive: taking exactly the whole market is
    # implausible but not arithmetically inconsistent.
    assert forecast_consistency_gate(100.0, 100.0) is True


def test_tech_t004_touches_must_be_five_sessions_apart():
    """TECH-T004 | Two highs only 2 sessions apart | Count as one swing
    touch

    Two highs two sessions apart are one swing seen twice, so the
    confirmation rule the report prints states the separation it requires.
    """
    from wbj.engines.levels_engine import _confirmation_rule

    rule = _confirmation_rule("resistance")
    assert ">=5 sessions apart daily" in rule
    assert ">=2 independent touches" in rule


def test_tech_t010_an_unadjusted_history_is_rejected():
    """TECH-T010 | Price history not split adjusted | Reject technical
    packet

    A split makes an unadjusted series discontinuous, so every pivot,
    zone and moving average built on it is wrong. AGENT.md's boundary is
    to refuse, not to score it anyway."""
    from datetime import datetime, timezone

    from wbj.schemas.packet import AnalysisMeta, MarketData, Packet, Security
    from wbj.specialists import technical as tech

    def _packet(adjusted: bool) -> Packet:
        return Packet(
            security=Security(ticker="T", exchange="X",
                              security_type="operating_company",
                              reporting_currency="USD", valuation_currency="USD"),
            analysis=AnalysisMeta(
                knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
                industry_adapter="default_nonfinancial"),
            fundamentals={"annual": []}, facts_table={},
            market_data=MarketData(daily=[], adjusted=adjusted),
            estimates={}, capital_structure={}, staleness={})

    out = tech.run(_packet(adjusted=False), {})
    assert out.status == "ERROR"
    assert out.coverage == 0.0
    assert out.category.awarded_points == 0.0


def test_tech_t012_missing_volume_leaves_the_dimension_unscored():
    """TECH-T012 | Volume missing | Volume dimension capped; breakout
    cannot confirm

    SCORING.md's cap is a ceiling on a real score, never a licence to
    invent one: with no volume at all every member is NOT_SCORABLE, so the
    dimension scores nothing rather than being handed a synthetic middle.
    """
    from wbj.core.nullstates import NullState, Value
    from wbj.core.scoring import Dimension

    absent = Dimension(
        name="volume_and_institutional_demand", max_points=3.0,
        metric_scores=[(0.25, Value.null(NullState.NOT_SCORABLE, unit="score"))
                       for _ in range(4)])
    assert absent.score10_value().is_null
    assert absent.valid_weight() == pytest.approx(0.0)


def test_tech_t011_a_pivot_is_only_known_k_sessions_after_it_printed():
    """TECH-T011 | Pivot k=3 at historical date | Pivot availability
    delayed 3 sessions

    A symmetric pivot needs k bars on BOTH sides, so the bar that makes it
    a pivot has not closed yet when the pivot prints. Treating it as known
    on the day would be look-ahead: the level would appear in a backtest
    three sessions before anyone could have drawn it.
    """
    import pandas as pd

    from wbj.engines.levels_engine import find_pivots

    k = 3
    highs = [10, 11, 12, 20, 12, 11, 10, 11, 12, 13]
    df = pd.DataFrame({
        "date": [f"2026-01-{d:02d}" for d in range(1, len(highs) + 1)],
        "high": [float(h) for h in highs],
        "low": [float(h) - 2.0 for h in highs],
        "close": [float(h) - 1.0 for h in highs],
    })

    peak = next(p for p in find_pivots(df, k) if p.kind == "high" and p.index == 3)
    assert peak.confirmed_index == peak.index + k

    # And it is not returned at all until those k bars exist.
    assert not [p for p in find_pivots(df.iloc[: 3 + k], k)
                if p.kind == "high" and p.index == 3]
    assert [p for p in find_pivots(df.iloc[: 3 + k + 1], k)
            if p.kind == "high" and p.index == 3]
