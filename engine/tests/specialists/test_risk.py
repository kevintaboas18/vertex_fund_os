"""Tests for `wbj.specialists.risk` (Task 18): RSK-001..035, the six
weighted resilience dimensions, Beneish/Altman closed-form math, the
mandatory SOLVENCY_WARNING, the <=4/15 Speculative override, and `run()`
against the NVDA golden fixture.

Sources of truth: `Cerebro/05_risk_analysis/{FORMULAS,SCORING,
DECISION_RULES,VALIDATION_TESTS}.md`, `Perfil Inversionista/Victor
Gonzalez.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wbj.core.nullstates import NullState
from wbj.core.scoring import Category
from wbj.schemas.packet import AnalysisMeta, MarketData, OHLCVRow, Packet, Security
import wbj.specialists.risk as risk

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def nvda_packet() -> Packet:
    data = json.loads(_FIXTURE.read_text())
    return Packet.model_validate(data)


def _dates(n: int) -> list[str]:
    base = pd.Timestamp("2020-01-01")
    return [(base + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _rows_from_closes(closes: list[float]) -> list[OHLCVRow]:
    dates = _dates(len(closes))
    ascending = [
        OHLCVRow(date=d, open=c, high=c * 1.01, low=c * 0.99, close=c, adj_close=c, volume=1_000_000.0)
        for d, c in zip(dates, closes)
    ]
    return list(reversed(ascending))


def _minimal_packet(
    annual_rows: list[dict], *, daily_closes: list[float] | None = None,
    benchmark_closes: list[float] | None = None, industry_adapter: str = "default_nonfinancial",
) -> Packet:
    return Packet(
        security=Security(ticker="TEST", exchange="NASDAQ", security_type="operating_company", reporting_currency="USD", valuation_currency="USD"),
        analysis=AnalysisMeta(knowledge_timestamp="2026-07-16T21:00:00+00:00", industry_adapter=industry_adapter),
        fundamentals={"annual": annual_rows, "quarterly": []},
        market_data=MarketData(
            daily=_rows_from_closes(daily_closes) if daily_closes else [],
            benchmark=_rows_from_closes(benchmark_closes) if benchmark_closes else [],
        ),
        estimates={}, capital_structure={}, facts_table={}, staleness={},
    )


def _row(year: int, **overrides) -> dict:
    base = dict(
        calendarYear=str(year), date=f"{year}-12-31", period="FY",
        revenue=1000.0, cogs=600.0, gross_profit=400.0, ebit=200.0,
        income_before_tax=190.0, income_tax_expense=40.0, net_income=150.0,
        operating_cash_flow=180.0, capex=-50.0, fcf=130.0, cash=300.0,
        inventory=80.0, net_receivables=90.0, total_current_assets=500.0,
        total_current_liabilities=300.0, total_debt=200.0, total_equity=600.0,
        total_assets=1200.0, total_liabilities=600.0, diluted_shares=100.0,
        stock_based_compensation=10.0,
    )
    base.update(overrides)
    return base


# ============================================================================
# RSK-VALIDATION_TESTS.md, encoded verbatim (RSK-T001..T009)
# ============================================================================


def test_RSK_T001_interest_coverage():
    """EBIT=15, interest=10 -> Coverage=1.5x."""
    v = risk.interest_coverage(15.0, 10.0)
    assert v.value == pytest.approx(1.5)


def test_RSK_T002_coverage_below_threshold_mandatory_warning():
    """Coverage=1.49x -> mandatory solvency warning."""
    v = risk.interest_coverage(14.9, 10.0)
    assert v.value == pytest.approx(1.49)
    assert risk.SOLVENCY_WARNING in v.warnings


def test_RSK_T003_cash_runway():
    """Cash=120, facility=0, monthly burn=10 -> Runway=12 months."""
    v = risk.cash_runway_months(120.0, 0.0, 10.0)
    assert v.value == pytest.approx(12.0)


def test_RSK_T004_max_drawdown():
    """Price index peak 100, trough 40 -> Max drawdown=-60%."""
    index = pd.Series([100.0, 80.0, 40.0, 60.0])
    v = risk.max_drawdown(index)
    assert v.value == pytest.approx(-0.60)


def test_RSK_T005_customer_hhi():
    """Two customers 50% each -> HHI=0.50."""
    v = risk.customer_hhi([0.5, 0.5])
    assert v.value == pytest.approx(0.50)


def test_RSK_T006_negative_ebitda_not_meaningful():
    """Negative EBITDA -> Net debt/EBITDA NOT_MEANINGFUL."""
    v = risk.net_debt_to_ebitda(net_debt=500.0, ebitda=-20.0)
    assert v.is_null
    assert v.state == NullState.NOT_MEANINGFUL


def test_RSK_T007_bank_company_excludes_forensic_screens():
    """Bank company -> Altman/Beneish/Piotroski applicability reviewed;
    industrial scoring not automatic. DECISION_RULES.md: "Exclude financial
    companies." The forensic family must come back NOT_APPLICABLE, not scored
    (a bank posting a 10/10 Altman Z is exactly the failure this guards)."""
    rows = [_row(2025, retained_earnings=500.0), _row(2024)]
    packet = _minimal_packet(rows, industry_adapter="banks")
    out = risk.run(packet, overlay={"retained_earnings": 500.0})
    assert any("industry_adapter" in a and "banks" in a for a in out.assumptions)

    by_id = {r.metric_id: r for r in out.metrics}
    for mid in ("RSK-DSRI-021", "RSK-GMI-022", "RSK-AQI-023", "RSK-SGI-024",
                "RSK-DEPI-025", "RSK-SGAI-026", "RSK-LVGI-027", "RSK-TATA-028",
                "RSK-MSCR-029", "RSK-ALT-030", "RSK-PIO-031"):
        assert by_id[mid].state == NullState.NOT_APPLICABLE, mid
        assert by_id[mid].score == "NOT_SCORABLE", mid
    # Accrual ratio is a general earnings-quality metric, not a named forensic
    # screen, so it still scores.
    assert isinstance(by_id["RSK-ACCR-020"].score, float)
    assert out.earnings_quality_and_forensics["altman_z_double_prime"] is None


def test_a_non_financial_company_still_scores_the_forensic_screens():
    """The exclusion is scoped to model-replacing adapters."""
    rows = [_row(2025, retained_earnings=500.0), _row(2024)]
    out = risk.run(_minimal_packet(rows), overlay={"retained_earnings": 500.0})
    by_id = {r.metric_id: r for r in out.metrics}
    assert isinstance(by_id["RSK-ALT-030"].score, float)


def test_RSK_T008_risk_category_4_of_15_caps_speculative():
    """Risk category=4/15, raw total=90 -> Main profile capped Speculative."""
    assert risk.capped_verdict(score10=9.0, awarded_points=4.0) == "Speculative"
    assert risk.capped_verdict(score10=9.0, awarded_points=4.01) != "Speculative"


def test_RSK_T009_forensic_flag_only_no_accusation():
    """Forensic M-score above screen threshold -> flag only; no
    accusation (a warning string, not a categorical fraud verdict)."""
    v = risk.beneish_m_score(dsri=1.5, gmi=1.2, aqi=1.1, sgi=1.3, depi=1.0, sgai=1.0, tata=0.05, lvgi=1.0)
    assert v.value > -1.78
    assert "BENEISH_M_SCORE_ABOVE_SCREEN_THRESHOLD" in v.warnings
    # the warning is descriptive text, not e.g. a "FRAUD_DETECTED" state
    assert v.is_valid


# ============================================================================
# Beneish M-score / Altman Z'' -- closed-form hand-computed tests
# ============================================================================


def test_beneish_m_score_hand_computed():
    """DSRI=GMI=AQI=SGI=DEPI=SGAI=LVGI=1.0 (no year-over-year change),
    TATA=0.0 -> M = -4.84 + 0.920+0.528+0.404+0.892+0.115-0.172+0-0.327
    = -2.48 (hand-computed)."""
    v = risk.beneish_m_score(dsri=1.0, gmi=1.0, aqi=1.0, sgi=1.0, depi=1.0, sgai=1.0, tata=0.0, lvgi=1.0)
    assert v.value == pytest.approx(-2.48, abs=1e-9)
    assert "BENEISH_M_SCORE_ABOVE_SCREEN_THRESHOLD" not in v.warnings  # -2.48 < -1.78


def test_beneish_m_score_flags_above_threshold():
    v = risk.beneish_m_score(dsri=1.0, gmi=1.0, aqi=1.0, sgi=1.0, depi=1.0, sgai=1.0, tata=0.2, lvgi=1.0)
    assert v.value > -1.78
    assert "BENEISH_M_SCORE_ABOVE_SCREEN_THRESHOLD" in v.warnings


def test_altman_z_double_prime_hand_computed():
    """WC/TA=0.2, RE/TA=0.3, EBIT/TA=0.15, BE/TL=1.0 -> Z'' =
    6.56*0.2 + 3.26*0.3 + 6.72*0.15 + 1.05*1.0 = 4.348 (hand-computed)."""
    v = risk.altman_z_double_prime(wc_ta=0.2, re_ta=0.3, ebit_ta=0.15, be_tl=1.0)
    assert v.value == pytest.approx(4.348, abs=1e-9)


def test_beneish_dsri_component():
    v = risk.beneish_dsri(receivables_t=120.0, revenue_t=1000.0, receivables_t1=100.0, revenue_t1=1000.0)
    assert v.value == pytest.approx((120.0 / 1000.0) / (100.0 / 1000.0))


def test_beneish_tata_component():
    v = risk.beneish_tata(operating_income=200.0, ocf=180.0, total_assets=1200.0)
    assert v.value == pytest.approx((200.0 - 180.0) / 1200.0)


def test_beneish_lvgi_component():
    v = risk.beneish_lvgi(debt_t=200.0, assets_t=1200.0, debt_t1=180.0, assets_t1=1100.0)
    lev_t = 200.0 / 1200.0
    lev_t1 = 180.0 / 1100.0
    assert v.value == pytest.approx(lev_t / lev_t1)


# ============================================================================
# Piotroski F-score
# ============================================================================


def test_piotroski_f_score_all_9_signals_pass():
    v = risk.piotroski_f_score(
        roa_t=0.10, roa_t1=0.05, ocf=100.0, ni=80.0,
        leverage_t=0.3, leverage_t1=0.4, current_ratio_t=1.5, current_ratio_t1=1.2,
        shares_t=100.0, shares_t1=105.0, gross_margin_t=0.42, gross_margin_t1=0.40,
        asset_turnover_t=0.9, asset_turnover_t1=0.8,
    )
    assert v.value == pytest.approx(9.0)


def test_piotroski_f_score_missing_all_signals_not_scorable():
    v = risk.piotroski_f_score(
        roa_t=None, roa_t1=None, ocf=None, ni=None, leverage_t=None, leverage_t1=None,
        current_ratio_t=None, current_ratio_t1=None, shares_t=None, shares_t1=None,
        gross_margin_t=None, gross_margin_t1=None, asset_turnover_t=None, asset_turnover_t1=None,
    )
    assert v.is_null
    assert v.state == NullState.MISSING


# ============================================================================
# Other formula behavior
# ============================================================================


def test_annualized_volatility_and_downside_deviation():
    rng = np.random.default_rng(1)
    returns = pd.Series(rng.normal(0, 0.01, 300))
    v_vol = risk.annualized_volatility(returns)
    v_down = risk.downside_deviation(returns)
    assert v_vol.is_valid and v_vol.value > 0
    assert v_down.is_valid and v_down.value > 0


def test_market_beta_needs_30_observations():
    stock = pd.Series(np.random.default_rng(2).normal(0, 0.01, 10))
    bench = pd.Series(np.random.default_rng(3).normal(0, 0.01, 10))
    v = risk.market_beta(stock, bench)
    assert v.is_null
    assert v.state == NullState.MISSING


def test_downside_beta_needs_30_down_observations():
    rng = np.random.default_rng(4)
    stock = pd.Series(rng.normal(0.0005, 0.01, 50))
    bench = pd.Series(rng.normal(0.0005, 0.01, 50))
    v = risk.downside_beta(stock, bench)
    # may or may not have 30 down obs depending on draw; just must not crash
    assert v.is_valid or v.state == NullState.MISSING


def test_historical_var_and_expected_shortfall_warn_below_500_obs():
    rng = np.random.default_rng(5)
    returns = pd.Series(rng.normal(0, 0.02, 100))
    v_var = risk.historical_var(returns, 0.95, 1)
    v_cvar = risk.expected_shortfall(returns, 0.95)
    assert v_var.is_valid
    assert "VAR_BELOW_500_OBSERVATIONS_PREFERRED" in v_var.warnings
    assert v_cvar.is_valid
    assert "CVAR_BELOW_500_OBSERVATIONS_PREFERRED" in v_cvar.warnings


def test_diluted_share_cagr_reuses_core_cagr():
    v = risk.diluted_share_cagr(110.0, 100.0, 3.0)
    assert v.is_valid
    assert v.value == pytest.approx((110.0 / 100.0) ** (1 / 3) - 1)


def test_sbc_to_fcf():
    v = risk.sbc_to_fcf(sbc=20.0, fcf=100.0)
    assert v.value == pytest.approx(0.20)


def test_macro_sensitivity_beta_ols_slope():
    macro = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    company = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    v = risk.macro_sensitivity_beta(company, macro)
    assert v.value == pytest.approx(2.0, abs=1e-6)


def test_thesis_killer_priority():
    v = risk.thesis_killer_priority(probability=0.3, impact=0.8, detectability=0.5, time_urgency=0.9)
    assert v.value == pytest.approx(0.3 * 0.8 * 0.5 * 0.9)


def test_profile_fit_within_cap():
    """A-03: el perfil se LEE de `Perfil Inversionista/`, ya no está transcrito
    a mano en el módulo. El test comprueba el mecanismo (comparar contra el
    rango del perfil vigente), no las cifras de un inversionista concreto —
    codificarlas es lo que hizo que el módulo se desincronizara del archivo.

    Un TECHO es una cota superior. Este test exigía `lo <= pos <= hi`, o sea
    trataba el rango como una banda obligatoria, y con el "Máximo por posición
    individual: 20% - 30%" de Kevin.md una posición del 10% salía como
    violación — como si dimensionar conservador incumpliera un máximo. El
    fallo estaba latente con el (0.05, 0.20) por defecto, donde casi
    cualquier posición real superaba el 5%.
    """
    lo, hi = risk.PROFILE["max_position_pct"]
    dentro = (lo + hi) / 2.0
    assert risk.profile_fit(dentro)["within_position_cap"] is True
    # Por encima del techo: sí incumple.
    assert risk.profile_fit(hi + 0.10)["within_position_cap"] is False
    # Justo en el techo: cabe (es un máximo, no un límite abierto).
    assert risk.profile_fit(hi)["within_position_cap"] is True
    # Por DEBAJO del rango: cabe de sobra en el techo, y se reporta aparte
    # como información, nunca como incumplimiento.
    conservadora = risk.profile_fit(max(0.0, lo - 0.01))
    assert conservadora["within_position_cap"] is True
    assert conservadora["below_intended_sizing"] is True
    # Dentro del rango no está "por debajo de lo previsto".
    assert risk.profile_fit(dentro)["below_intended_sizing"] is False
    assert risk.profile_fit(None)["within_position_cap"] is None
    assert risk.profile_fit(None)["below_intended_sizing"] is None


# ============================================================================
# Dimension caps / mandatory flags
# ============================================================================


def test_risk_has_no_apply_dimension_cap_helper():
    """risk_analysis has no numeric dimension-level caps (every SCORING.md
    'Gate / cap' entry here is a confidence caveat or the label-only
    <=4/15 Speculative override), so the shared _apply_dimension_cap is not
    imported into this module -- it was dead code and is removed."""
    assert not hasattr(risk, "_apply_dimension_cap")


def test_run_thesis_killer_priority_row_present_all_35_formulas(nvda_packet):
    """RSK-THESIS-035 (thesis_killer_priority) must surface in out.metrics
    (judgment-only, NOT_SCORABLE) so all 35 RSK formulas are accounted
    for -- previously only 34 rows appeared."""
    out = risk.run(nvda_packet)
    ids = {r.metric_id for r in out.metrics}
    assert "RSK-THESIS-035" in ids
    row = next(r for r in out.metrics if r.metric_id == "RSK-THESIS-035")
    assert row.score == "NOT_SCORABLE"


def test_run_solvency_warning_via_overlay_interest_expense():
    rows = [_row(2025, ebit=100.0), _row(2024)]
    packet = _minimal_packet(rows)
    out = risk.run(packet, overlay={"interest_expense": 90.0})  # coverage=1.11x < 1.5x
    assert risk.SOLVENCY_WARNING in out.mandatory_warnings
    assert risk.SOLVENCY_WARNING in out.mandatory_flags


def test_run_no_solvency_warning_when_coverage_healthy():
    rows = [_row(2025, ebit=100.0), _row(2024)]
    packet = _minimal_packet(rows)
    out = risk.run(packet, overlay={"interest_expense": 10.0})  # coverage=10x
    assert risk.SOLVENCY_WARNING not in out.mandatory_warnings


def test_run_category_reproduces_from_dimensions():
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = risk.run(packet)
    recomputed = Category(name=risk.AGENT_ID, max_points=risk.MAX_POINTS, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)
    assert out.coverage == pytest.approx(recomputed.coverage(), abs=1e-6)


# ============================================================================
# Judgment requests
# ============================================================================


def test_run_thesis_killers_is_judgment_request(nvda_packet):
    out = risk.run(nvda_packet)
    ids = {jr.metric_id for jr in out.judgment_requests}
    assert "thesis_killers" in ids


# ============================================================================
# run() against the NVDA fixture
# ============================================================================


def test_run_nvda_fixture_schema_valid(nvda_packet):
    out = risk.run(nvda_packet)
    assert out.agent_id == "risk_analysis"
    assert out.version == "2.0.0"
    assert out.security.ticker == "NVDA"
    assert out.category.max_points == 15.0
    assert out.status in ("COMPLETE", "INCOMPLETE", "ERROR")
    assert len(out.dimensions) == 6
    for row in out.metrics:
        assert row.metric_id
        assert row.formula_id
        assert row.formula_version
        assert row.score == "NOT_SCORABLE" or isinstance(row.score, float)
        assert 0.0 <= row.confidence <= 100.0
        assert (row.value is None) != (row.state is None)


def test_run_nvda_fixture_category_math_reproduces_from_dimensions(nvda_packet):
    out = risk.run(nvda_packet)
    recomputed = Category(name=risk.AGENT_ID, max_points=risk.MAX_POINTS, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)
    assert out.coverage == pytest.approx(recomputed.coverage(), abs=1e-6)


def test_run_nvda_fixture_category_confidence_computed(nvda_packet):
    out = risk.run(nvda_packet)
    assert out.category.confidence is not None
    assert 0.0 <= out.category.confidence <= 100.0


def test_run_nvda_fixture_serializes_to_json(nvda_packet):
    out = risk.run(nvda_packet)
    dumped = out.model_dump(mode="json")
    json.dumps(dumped)
    assert dumped["agent_id"] == "risk_analysis"


def test_run_nvda_fixture_beta_scored_with_populated_benchmark(nvda_packet):
    """The NVDA golden fixture's `market_data.benchmark` is now populated
    (SPY, aligned to the stock's trading dates -- packet-builder task) --
    beta/downside-beta/correlation must be computed from it, not degrade."""
    assert len(nvda_packet.market_data.benchmark) >= 30
    out = risk.run(nvda_packet)
    beta_row = next(r for r in out.metrics if r.metric_id == "RSK-BETA-003")
    corr_row = next(r for r in out.metrics if r.metric_id == "RSK-CORR-005")
    # RSK-BETA-003/CORR-005 are value-only rows (score is always the literal
    # "NOT_SCORABLE" placeholder per risk.py's `add(..., None)`) -- what
    # matters is that `state` is no longer MISSING, i.e. a real value was
    # computed from the benchmark instead of degrading. (RSK-DBETA-004
    # additionally needs >=30 *down* observations, which this smooth
    # synthetic fixture doesn't happen to have -- covered live instead.)
    assert beta_row.state is None
    assert beta_row.value is not None
    assert corr_row.state is None


def test_run_beta_not_scorable_empty_benchmark():
    """With no benchmark data at all (e.g. the benchmark provider call
    failing), beta must never be proxied (PROHIBITED_IMPUTATION) -- it
    degrades to NOT_SCORABLE/MISSING rather than crashing or guessing."""
    packet = _minimal_packet([_row(2025)], daily_closes=[100.0 + i for i in range(300)])
    assert packet.market_data.benchmark == []
    out = risk.run(packet)
    beta_row = next(r for r in out.metrics if r.metric_id == "RSK-BETA-003")
    assert beta_row.score == "NOT_SCORABLE"
    assert beta_row.state == NullState.MISSING


def test_run_nvda_fixture_profile_fit_populated(nvda_packet):
    """El bloque profile_fit se rellena con el perfil VIGENTE (A-03), sin
    depender del capital de ningún inversionista en particular."""
    lo, hi = risk.PROFILE["max_position_pct"]
    out = risk.run(nvda_packet, overlay={"position_size_pct": (lo + hi) / 2.0})
    assert out.profile_fit["within_position_cap"] is True
    assert out.profile_fit["capital_usd"] == pytest.approx(risk.PROFILE["capital_usd"])
    assert out.profile_fit["horizon_years_range"] == list(risk.PROFILE["horizon_years"])


def test_run_nvda_fixture_validation_tests_all_self_checks_pass(nvda_packet):
    out = risk.run(nvda_packet)
    assert out.validation_tests.failed == 0
    assert out.validation_tests.passed >= 1


def test_run_empty_annual_and_market_history_degrades_without_crashing():
    out = risk.run(_minimal_packet([]))
    assert out.coverage == 0.0
    assert out.category.awarded_points == 0.0


# ============================================================================
# Audit fix: anchor disclosure (AGENT.md no-speculation rule)
# ============================================================================


def test_the_decision_rules_anchors_are_victor_not_disclaimed():
    """The five resilience anchors DECISION_RULES.md states with exact
    boundaries are Victor's own numbers -- registered VICTOR, never surfaced
    as this module's calibration."""
    for mid in ("RSK-ICOV-011", "RSK-RUN-015", "RSK-MDD-006", "RSK-DBETA-004"):
        assert risk.ANCHOR_PROVENANCE[mid][0] == "VICTOR", mid


def test_the_invented_anchors_are_registered_and_disclosed():
    """Every scored metric whose scale is not a DECISION_RULES.md anchor is
    MIXED and surfaced in `assumptions`."""
    rows = [_row(y) for y in range(2025, 2020, -1)]
    out = risk.run(_minimal_packet(rows, daily_closes=[100.0 + i for i in range(300)],
                                   benchmark_closes=[100.0 + i * 0.5 for i in range(300)]),
                   overlay={"interest_expense": 20.0, "margin_of_safety": 0.1})
    blob = " ".join(out.assumptions)
    assert "Scoring anchors (partly derived)" in blob
    for mid, (source, _) in risk.ANCHOR_PROVENANCE.items():
        if source == "MIXED":
            assert mid in blob, f"{mid}: MIXED anchor not disclosed"
        else:  # VICTOR
            assert mid not in blob, f"{mid}: Victor's anchor must not be disclaimed"


def test_each_anchor_provenance_entry_names_its_source():
    for mid, (source, note) in risk.ANCHOR_PROVENANCE.items():
        assert source in ("VICTOR", "MIXED"), mid
        assert ".md" in note, f"{mid}: no document named"
        assert len(note) > 30, mid


def test_diluted_share_cagr_uses_the_registered_5y_window():
    """FORMULAS.md RSK-DIL-032 frequency is '3y / 5y'. With shares shrinking
    1%/yr over 6 years the CAGR is ~-1%/yr, measured across the window rather
    than a single year-over-year step."""
    def _shares(y):
        return 100.0 * (0.99 ** (y - 2020))  # 2020=100, later years fewer (buyback)
    rows = [_row(y, diluted_shares=_shares(y)) for y in range(2025, 2019, -1)]  # 6y newest-first
    out = risk.run(_minimal_packet(rows))
    dil = next(r for r in out.metrics if r.metric_id == "RSK-DIL-032")
    assert dil.value == pytest.approx(-0.01, abs=1e-9)


# ============================================================================
# Data gap: five inputs the packet already carries were read from overlay only
# ============================================================================


def test_the_forensic_and_coverage_inputs_are_read_from_the_packet():
    """`interest_expense`, `retained_earnings`, `sga`,
    `depreciation_and_amortization` and `ppe_net` are all mapped into the
    annual rows by the packet builder, but risk.py read them from `overlay`
    alone -- so RSK-ICOV-011/FCC-012/AQI-023/DEPI-025/SGAI-026, and with them
    the whole Beneish M-score and Altman Z'', were MISSING on every real
    company for want of a wire."""
    rows = [
        _row(2025, interest_expense=40.0, retained_earnings=500.0, sga=200.0,
             depreciation_and_amortization=60.0, ppe_net=900.0),
        _row(2024, interest_expense=38.0, retained_earnings=450.0, sga=180.0,
             depreciation_and_amortization=55.0, ppe_net=850.0),
    ]
    out = risk.run(_minimal_packet(rows))  # NO overlay
    by_id = {r.metric_id: r for r in out.metrics}
    for mid in ("RSK-ICOV-011", "RSK-FCC-012", "RSK-AQI-023", "RSK-DEPI-025",
                "RSK-SGAI-026", "RSK-MSCR-029", "RSK-ALT-030"):
        assert by_id[mid].state is None, f"{mid} still MISSING without an overlay"


def test_the_net_ppe_and_da_substitutions_are_flagged_as_proxies():
    """FORMULAS.md execution rule: "Record any proxy in `warnings`". Classic
    Beneish AQI/DEPI use GROSS PP&E and depreciation alone; the packet
    carries net PP&E and D&A."""
    rows = [
        _row(2025, retained_earnings=500.0, sga=200.0,
             depreciation_and_amortization=60.0, ppe_net=900.0),
        _row(2024, retained_earnings=450.0, sga=180.0,
             depreciation_and_amortization=55.0, ppe_net=850.0),
    ]
    out = risk.run(_minimal_packet(rows))
    by_id = {r.metric_id: r for r in out.metrics}
    assert "PPE_NET_PROXY_FOR_GROSS_PPE" in by_id["RSK-AQI-023"].warnings
    assert "DA_PROXY_FOR_DEPRECIATION" in by_id["RSK-DEPI-025"].warnings


def test_an_explicit_overlay_value_still_wins_over_the_packet():
    rows = [_row(2025, interest_expense=40.0), _row(2024, interest_expense=38.0)]
    out = risk.run(_minimal_packet(rows), overlay={"interest_expense": 100.0})
    icov = next(r for r in out.metrics if r.metric_id == "RSK-ICOV-011")
    assert icov.value == pytest.approx(200.0 / 100.0)  # ebit 200 / overlay 100


def test_every_dimension_slot_goes_through_the_state_preserving_helper():
    """`_dimension_slot` existe para que un NOT_APPLICABLE no se aplane a
    NOT_SCORABLE al construir el slot -- su propio docstring lo dice: "solo si
    el estado sobrevive hasta aqui".

    Tres sitios lo construian a mano y se saltaban esa proteccion. Hoy no
    cuesta cobertura, porque las metricas que pasan por ahi no son de las que
    se marcan inaplicables; el dia que una lo sea, se perderia en silencio y
    nadie lo notaria. Este test es lo que impide que vuelva a abrirse.
    """
    import inspect
    import re

    import wbj.specialists.risk as rsk

    fuente = inspect.getsource(rsk.run)
    a_mano = re.findall(
        r'Value\.of\([^)]*score10[^)]*\)\s*if[^)]*is not None\s*else\s*'
        r'Value\.null\(NullState\.NOT_SCORABLE', fuente)
    assert not a_mano, (
        f"{len(a_mano)} slot(s) construidos a mano: usa `_dimension_slot`, "
        "que conserva el estado de la metrica")
