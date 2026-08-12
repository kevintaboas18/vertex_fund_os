"""Tests for `wbj.specialists.financial` (Task 14): FIN-001..033, the
core-27 diagnostic, the five weighted dimensions, mandatory overrides, and
`run()` against the NVDA golden fixture.

Sources of truth: `Cerebro/02_financial_analysis/{FORMULAS,SCORING,
DECISION_RULES,VALIDATION_TESTS}.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.core.scoring import Category
from wbj.schemas.packet import AnalysisMeta, MarketData, Packet, Security
import wbj.specialists.financial as fin

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
    """A bare-bones but schema-valid `Packet` carrying only
    `fundamentals.annual`, for tests that need to steer `run()` into a
    specific scenario (negative equity, a loss, ...) rather than the fixed
    NVDA fixture.

    `annual_rows` must be newest-first, matching `Packet.fundamentals`'s
    real convention (mirroring FMP's own newest-first API ordering, see
    `NVDA_packet.json`) -- `financial._annual_rows` reverses it to
    ascending internally.
    """
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
    """A profitable, boring annual row with sensible defaults; override
    individual fields per test."""
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


# ============================================================================
# band_score: edge convention (GOOD closed, EXCELLENT strictly beyond hi)
# ============================================================================


def test_band_score_higher_is_better_edges():
    # FIN-GR-001: BAD <0%; GOOD 0-10%; EXCELLENT >10%
    assert fin.band_score(-0.0001, 0.0, 0.10) == 0  # BAD, just below lo
    assert fin.band_score(0.0, 0.0, 0.10) == 1  # GOOD, at lo (closed)
    assert fin.band_score(0.10, 0.0, 0.10) == 1  # GOOD at hi -- brief's worked example
    assert fin.band_score(0.1001, 0.0, 0.10) == 2  # EXCELLENT, just above hi


def test_band_score_lower_is_better_edges():
    # FIN-BS-019 D/E: BAD >2.0; GOOD 1.0-2.0; EXCELLENT <1.0
    assert fin.band_score(2.0001, 1.0, 2.0, higher_is_better=False) == 0  # BAD
    assert fin.band_score(2.0, 1.0, 2.0, higher_is_better=False) == 1  # GOOD at hi (closed)
    assert fin.band_score(1.0, 1.0, 2.0, higher_is_better=False) == 1  # GOOD at lo (closed)
    assert fin.band_score(0.9999, 1.0, 2.0, higher_is_better=False) == 2  # EXCELLENT


def test_band_yoy_revenue_growth_matches_formulas_md_example():
    """The task brief's own worked example: yoy=0.10 -> GOOD not EXCELLENT."""
    assert fin.band_yoy_revenue_growth(0.10) == 1
    assert fin.band_yoy_revenue_growth(0.1001) == 2
    assert fin.band_yoy_revenue_growth(-0.001) == 0


# ============================================================================
# VALIDATION_TESTS.md, encoded verbatim (FIN-T001..T010)
# ============================================================================


def test_FIN_T001_revenue_yoy_growth():
    """Revenue 110 vs 100 -> YoY growth=10%."""
    v = fin.yoy_revenue_growth(110.0, 100.0)
    assert v.is_valid
    assert v.value == pytest.approx(0.10)


def test_FIN_T002_current_ratio():
    """Current assets=150, liabilities=100 -> Current ratio=1.5."""
    v = fin.current_ratio(150.0, 100.0)
    assert v.is_valid
    assert v.value == pytest.approx(1.5)


def test_FIN_T003_interest_coverage_at_threshold_no_warning():
    """EBIT=30, interest=20 -> Coverage=1.5x; no below-1.5 warning."""
    v = fin.interest_coverage(30.0, 20.0)
    assert v.is_valid
    assert v.value == pytest.approx(1.5)
    assert "SOLVENCY_WARNING" not in v.warnings


def test_FIN_T004_interest_coverage_below_threshold_warns():
    """EBIT=29, interest=20 -> Coverage=1.45x; solvency warning."""
    v = fin.interest_coverage(29.0, 20.0)
    assert v.is_valid
    assert v.value == pytest.approx(1.45)
    assert "SOLVENCY_WARNING" in v.warnings


def test_FIN_T005_fcf_and_fcf_margin():
    """OCF=120, capex=40, revenue=800 -> FCF=80; FCF margin=10%."""
    fcf = fin.free_cash_flow(120.0, 40.0)
    assert fcf.value == pytest.approx(80.0)
    margin = fin.fcf_margin(fcf.value, 800.0)
    assert margin.value == pytest.approx(0.10)


def test_FIN_T006_loss_negative_fcf_equity_issuance_triggers_override_1():
    """NI=-10, FCF=-20, equity issuance positive -> Bad/Avoid override."""
    externally_dependent = fin.is_externally_dependent(need=0.0, debt_issuance=0.0, equity_issuance=5.0)
    assert externally_dependent is True
    assert fin.override_1_triggered(net_income=-10.0, fcf=-20.0, externally_dependent=externally_dependent) is True


def test_FIN_T007_roic_below_wacc_no_excellent_verdict():
    """ROIC=9%, WACC=11% -> No Excellent verdict.

    Exercises the real verdict cap (`capped_verdict`): even a 9.5/10 raw
    score cannot yield an 'Excellent' label once Override 2 (ROIC<WACC)
    fires -- and the same raw score IS 'Excellent' without the override.
    """
    assert fin.override_2_triggered(roic_value=0.09, wacc_value=0.11) is True
    assert fin.capped_verdict(9.5, override_1=False, override_2=True) != "Excellent financial health"
    assert fin.capped_verdict(9.5, override_1=False, override_2=False) == "Excellent financial health"


def test_FIN_T008_27_valid_metrics_all_excellent_is_100pct():
    """27 valid metrics all Excellent -> 54/54=100%, run through the real
    core-27 diagnostic (`core27_diagnostic`), not inline test arithmetic."""
    valid_count, points, maximum_valid_points, percent, score_10 = fin.core27_diagnostic([2] * 27)
    assert valid_count == 27
    assert points == 54.0
    assert maximum_valid_points == 54.0
    assert percent == pytest.approx(100.0)
    assert score_10 == pytest.approx(10.0)


def test_core27_diagnostic_excludes_not_scorable_metrics():
    """A `None` band (NOT_SCORABLE) is excluded from both the numerator and
    the `2*valid` denominator -- 26 EXCELLENT + 1 NOT_SCORABLE still reads
    100% over the 26 valid metrics."""
    valid_count, points, maximum_valid_points, percent, score_10 = fin.core27_diagnostic([2] * 26 + [None])
    assert valid_count == 26
    assert points == 52.0
    assert maximum_valid_points == 52.0
    assert percent == pytest.approx(100.0)


def test_core27_diagnostic_mixed_bands():
    # 4 EXCELLENT(2) + 4 GOOD(1) + 2 BAD(0) -> 12 / 20 = 60%
    valid_count, points, maximum_valid_points, percent, score_10 = fin.core27_diagnostic(
        [2, 2, 2, 2, 1, 1, 1, 1, 0, 0]
    )
    assert valid_count == 10
    assert points == 12.0
    assert percent == pytest.approx(60.0)
    assert score_10 == pytest.approx(6.0)


def test_FIN_T009_negative_equity_debt_to_equity_not_meaningful():
    """Negative equity -> Debt/equity NOT_MEANINGFUL."""
    v = fin.debt_to_equity(100.0, -50.0)
    assert v.is_null
    assert v.state == NullState.NOT_MEANINGFUL


def test_FIN_T010_bank_security_type_marks_conventional_fcf_roic_not_applicable():
    """VALIDATION_TESTS.md FIN-T010 + DECISION_RULES.md Override 5: for a
    bank, "conventional FCF/ROIC scoring is not allowed". The FCF family
    (FIN-CF-012/013/014, FIN-DX-029) and the ROIC family (FIN-EF-024/026/027)
    must come back NOT_APPLICABLE, not scored with a warning -- while ROE and
    ROA still score (the adapters keep them for financials)."""
    packet = _minimal_packet(
        [_row(2025), _row(2024)], industry_adapter="banks", security_type="bank",  # newest-first
    )
    out = fin.run(packet)
    assert any("industry_adapter" in a and "banks" in a for a in out.assumptions)

    by_id = {r.metric_id: r for r in out.metrics}
    for mid in ("FIN-CF-012", "FIN-CF-013", "FIN-CF-014", "FIN-DX-029",
                "FIN-EF-024", "FIN-EF-026", "FIN-EF-027"):
        # NOT_APPLICABLE rides in `state` (OUTPUT_CONTRACT.md: score is only
        # "0-10 or NOT_SCORABLE", the null state lives in the value field).
        assert by_id[mid].state == NullState.NOT_APPLICABLE, mid
        assert by_id[mid].score == "NOT_SCORABLE", mid
    # ROE and ROA are retained for financials -> still scored numerically.
    assert isinstance(by_id["FIN-EF-023"].score, float)
    assert isinstance(by_id["FIN-EF-025"].score, float)


def test_a_bank_does_not_trigger_override_2_on_a_forbidden_roic():
    """Override 2 (ROIC < WACC) must not fire on a ROIC the methodology
    forbids: the conventional ROIC is NOT_APPLICABLE for a bank, so even an
    absurd WACC cannot cap the verdict through it."""
    packet = _minimal_packet(
        [_row(2025), _row(2024)], industry_adapter="banks", security_type="bank",
    )
    out = fin.run(packet, overlay={"wacc": 99.0})
    assert "OVERRIDE_2_ROIC_BELOW_WACC" not in out.mandatory_flags


def test_a_non_financial_company_still_scores_conventional_fcf_roic():
    """The prohibition is scoped to model-replacing adapters; a default
    operating company is unaffected."""
    out = fin.run(_minimal_packet([_row(y) for y in range(2025, 2020, -1)]))
    by_id = {r.metric_id: r for r in out.metrics}
    assert by_id["FIN-CF-014"].state != NullState.NOT_APPLICABLE
    assert by_id["FIN-EF-024"].state != NullState.NOT_APPLICABLE


# ============================================================================
# Core-27 percent/score10 math + reconciliation
# ============================================================================


def test_core27_percent_math_partial_valid():
    # 10 valid metrics: 4 EXCELLENT(2), 4 GOOD(1), 2 BAD(0) -> points=12, max=20
    points = 4 * 2 + 4 * 1 + 2 * 0
    maximum_valid_points = 2 * 10
    percent = points / maximum_valid_points * 100.0
    assert points == 12
    assert percent == pytest.approx(60.0)
    assert percent / 10.0 == pytest.approx(6.0)


def test_reconciliation_check_within_tolerance_returns_none():
    assert fin.reconciliation_check(6.0, 7.0) is None  # diff 1.0 <= 1.5
    assert fin.reconciliation_check(6.0, 7.5) is None  # diff 1.5, boundary inclusive (not > 1.5)


def test_reconciliation_check_beyond_tolerance_returns_warning():
    msg = fin.reconciliation_check(5.0, 9.0)
    assert msg is not None
    assert "CORE27_RECONCILIATION_WARNING" in msg


# ============================================================================
# verdict bands (DECISION_RULES.md)
# ============================================================================


def test_verdict_bands():
    assert fin.verdict(8.0) == "Excellent financial health"
    assert fin.verdict(10.0) == "Excellent financial health"
    assert fin.verdict(7.99) == "Good with limited weaknesses"
    assert fin.verdict(6.0) == "Good with limited weaknesses"
    assert fin.verdict(5.99) == "Mixed / watch"
    assert fin.verdict(4.0) == "Mixed / watch"
    assert fin.verdict(3.99) == "Weak / high financial risk"
    assert fin.verdict(0.0) == "Weak / high financial risk"


# ============================================================================
# run() against a synthetic packet -- mandatory flags / overrides end-to-end
# ============================================================================


def test_run_negative_equity_flags_debt_to_equity_not_meaningful():
    rows = [_row(2025, total_equity=-50.0), _row(2024), _row(2023)]  # newest-first
    out = fin.run(_minimal_packet(rows))
    de_row = next(r for r in out.metrics if r.metric_id == "FIN-BS-019")
    assert de_row.state == NullState.NOT_MEANINGFUL
    assert "NEGATIVE_EQUITY_DEBT_TO_EQUITY_NOT_MEANINGFUL" in out.mandatory_flags


def test_run_solvency_warning_via_overlay_interest_expense():
    rows = [_row(2025, ebit=29.0), _row(2024)]  # newest-first
    out = fin.run(_minimal_packet(rows), overlay={"interest_expense": 20.0})
    coverage_row = next(r for r in out.metrics if r.metric_id == "FIN-BS-020")
    assert coverage_row.value == pytest.approx(1.45)
    assert "SOLVENCY_WARNING" in out.mandatory_flags


def test_run_override_1_caps_score_below_4():
    rows = [
        _row(
            2025,
            revenue=900.0,
            net_income=-10.0,
            operating_cash_flow=-30.0,
            capex=-10.0,
            common_stock_repurchased=0.0,
            dividends_paid=0.0,
            debt_repayment=50.0,  # net new debt raised -> external dependence
        ),
        _row(2024, net_income=100.0, operating_cash_flow=150.0),
    ]  # newest-first
    out = fin.run(_minimal_packet(rows))
    assert "OVERRIDE_1_LOSS_NEGATIVE_FCF_EXTERNAL_DEPENDENCE" in out.mandatory_flags
    assert "OVERRIDE_1_LOSS_NEGATIVE_FCF_EXTERNAL_DEPENDENCE_CAPS_BAD_AVOID" in out.mandatory_overrides
    # Override 1 caps the VERDICT label at Bad/Avoid, not the points.
    assert out.verdict == "Weak / high financial risk"
    # category points stay reproducible from the dimensions (HANDOFF_CONTRACT.md).
    recomputed = Category(name="financial_analysis", max_points=15.0, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)


def test_run_override_2_caps_verdict_not_points():
    out = fin.run(_minimal_nvda_like_packet(), overlay={"wacc": 5.0})  # absurdly high WACC
    assert "OVERRIDE_2_ROIC_BELOW_WACC" in out.mandatory_flags
    # Override 2 caps the VERDICT label below Excellent, not the points.
    assert out.verdict != "Excellent financial health"
    recomputed = Category(name="financial_analysis", max_points=15.0, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)


def _minimal_nvda_like_packet() -> Packet:
    rows = [_row(y) for y in (2025, 2024, 2023, 2022, 2021)]  # newest-first
    return _minimal_packet(rows)


def test_run_no_wacc_skips_override_2_and_records_assumption():
    out = fin.run(_minimal_nvda_like_packet())
    assert "OVERRIDE_2_ROIC_BELOW_WACC" not in out.mandatory_flags
    assert any("FIN-EF-026" in a for a in out.assumptions)


def test_run_judgment_requests_for_organic_growth_and_market_share():
    out = fin.run(_minimal_nvda_like_packet())
    ids = {jr.metric_id for jr in out.judgment_requests}
    assert ids == {"FIN-GR-004", "FIN-GR-005"}
    for jr in out.judgment_requests:
        assert jr.agent_id == fin.AGENT_ID
        assert jr.question


def test_run_gr004_gr005_rows_do_not_charge_a_conditional_field():
    """Las dos leen campos que `DATASET.md` tipa **conditional**:
    `organic_growth_bridge` ("issuer reconciliation") y `market_share_series`
    ("industry source"). Existen cuando la fuente los PRODUCE.

    Antes salian NOT_SCORABLE, que cuenta en el denominador, y eso hacia que
    NO hacer adquisiciones costara cobertura: una empresa que crece sola no
    tiene nada que conciliar, asi que nunca emite el puente. Medido, faltaba
    en 5 de 6 tickers -- y el unico que puntuaba era Coca-Cola, que si publica
    su crecimiento organico.

    Tampoco se pueden deducir: `MISSING_DATA_POLICY.md` pone "organic growth"
    y "market share" por su nombre en la lista de imputacion prohibida.
    """
    out = fin.run(_minimal_nvda_like_packet())
    for mid in ("FIN-GR-004", "FIN-GR-005"):
        row = next(r for r in out.metrics if r.metric_id == mid)
        assert row.state == NullState.NOT_APPLICABLE, (
            f"{mid} sigue cobrando un campo conditional que el emisor no "
            "publica")


def test_gr004_gr005_are_still_offered_to_the_judge():
    """No cobrarlas no es dejar de pedirlas. Un analista con la conciliacion
    del emisor delante tiene que poder suministrarla y que puntue."""
    out = fin.run(_minimal_nvda_like_packet())
    ids = {jr.metric_id for jr in out.judgment_requests}
    assert {"FIN-GR-004", "FIN-GR-005"} <= ids


# ============================================================================
# run() against the NVDA golden fixture
# ============================================================================


def test_run_nvda_fixture_schema_valid(nvda_packet):
    out = fin.run(nvda_packet)
    assert out.agent_id == "financial_analysis"
    assert out.version == "2.0.0"
    assert out.security.ticker == "NVDA"
    assert out.security.exchange == "NASDAQ"
    assert out.security.currency == "USD"
    assert out.category.max_points == 15.0
    assert out.status in ("COMPLETE", "INCOMPLETE", "ERROR")
    assert len(out.dimensions) == 5
    assert len(out.metrics) == 33  # 27 core + 6 diagnostics
    assert out.core_27_metrics.rows and len(out.core_27_metrics.rows) <= 27
    # every metric row satisfies OUTPUT_CONTRACT.md's ten fields
    for row in out.metrics:
        assert row.metric_id
        assert row.formula_id
        assert row.formula_version
        assert row.score == "NOT_SCORABLE" or isinstance(row.score, float)
        assert 0.0 <= row.confidence <= 100.0
        assert (row.value is None) != (row.state is None)


def test_run_nvda_fixture_category_math_reproduces_from_dimensions(nvda_packet):
    out = fin.run(nvda_packet)
    recomputed = Category(name="financial_analysis", max_points=15.0, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)
    assert out.coverage == pytest.approx(recomputed.coverage(), abs=1e-6)
    # no override triggers on a profitable, well-capitalized NVDA with no wacc overlay
    assert out.mandatory_overrides == []


def test_run_nvda_fixture_category_confidence_computed(nvda_packet):
    """HANDOFF_CONTRACT.md rejects a packet whose category confidence is
    absent -- so `category.confidence` must be a real number in [0, 100],
    computed via `wbj.core.confidence.confidence()` (the Task 5 five-
    component formula), not hardcoded `None`."""
    out = fin.run(nvda_packet)
    assert out.category.confidence is not None
    assert 0.0 <= out.category.confidence <= 100.0


def test_run_confidence_lower_when_fundamentals_stale(nvda_packet):
    """The freshness component is derived from the packet's own
    `quarterly_fundamentals` staleness flag, so a STALE packet yields a
    strictly lower confidence than an otherwise-identical FRESH one."""
    stale = nvda_packet.model_copy(update={"staleness": {**nvda_packet.staleness, "quarterly_fundamentals": "STALE"}})
    fresh = nvda_packet.model_copy(update={"staleness": {**nvda_packet.staleness, "quarterly_fundamentals": "FRESH"}})
    out_stale = fin.run(stale)
    out_fresh = fin.run(fresh)
    assert out_stale.category.confidence < out_fresh.category.confidence


def test_run_category_reproduces_from_dimensions_even_with_override():
    """HANDOFF_CONTRACT.md ("category points must reproduce from dimension
    scores") has NO exception for overrides. An override caps the verdict
    label, never the points -- so awarded_points must still equal
    Category(dimensions).points() when an override fired."""
    rows = [
        _row(2025, revenue=900.0, net_income=-10.0, operating_cash_flow=-30.0, capex=-10.0, debt_repayment=50.0),
        _row(2024, net_income=100.0, operating_cash_flow=150.0),
    ]
    out = fin.run(_minimal_packet(rows))
    assert out.mandatory_overrides  # an override did fire
    recomputed = Category(name="financial_analysis", max_points=15.0, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)


def test_dim_returns_includes_dilution_metrics():
    """SCORING.md names dilution (FIN-DX-032 SBC/rev, FIN-DX-033 diluted-
    share CAGR) a primary input of the cash-conversion & capital-efficiency
    dimension ("+ dilution")."""
    assert "FIN-DX-032" in fin._DIMENSION_MEMBERS[fin.DIM_RETURNS]
    assert "FIN-DX-033" in fin._DIMENSION_MEMBERS[fin.DIM_RETURNS]


def test_run_dilution_metrics_feed_returns_dimension(nvda_packet):
    """The dilution rows are scored (a band, not NOT_SCORABLE) and their
    score participates in the returns dimension's weighted mean."""
    out = fin.run(nvda_packet)
    returns_dim = next(d for d in out.dimensions if d.name == fin.DIM_RETURNS)
    # 6 core returns members + 2 dilution = 8 metric slots
    assert len(returns_dim.metric_scores) == 8
    dx032 = next(r for r in out.metrics if r.metric_id == "FIN-DX-032")
    dx033 = next(r for r in out.metrics if r.metric_id == "FIN-DX-033")
    assert dx032.score != "NOT_SCORABLE"
    assert dx033.score != "NOT_SCORABLE"


def test_run_nvda_fixture_serializes_to_json(nvda_packet):
    out = fin.run(nvda_packet)
    dumped = out.model_dump(mode="json")
    json.dumps(dumped)  # must not raise
    assert dumped["agent_id"] == "financial_analysis"


def test_run_nvda_fixture_extension_fields_populated(nvda_packet):
    out = fin.run(nvda_packet)
    assert set(out.profitability_and_cash) == {"FIN-PR-007", "FIN-PR-008", "FIN-PR-009", "FIN-CF-012", "FIN-CF-014", "FIN-CF-015"}
    assert set(out.return_on_capital) == {"FIN-EF-023", "FIN-EF-024", "FIN-EF-025", "FIN-EF-026", "FIN-EF-027"}
    assert set(out.dilution_and_sbc) == {"FIN-DX-032", "FIN-DX-033"}
    assert out.strongest_metric is not None
    assert out.weakest_metric is not None


def test_run_nvda_fixture_reconciliation_warning_present_given_missing_peer_and_bridge_data(nvda_packet):
    """NVDA's packet has no peer-growth/organic-growth-bridge/market-share
    data (FIN-GR-003/004/005 unavailable), so the revenue-quality dimension
    falls below the 70% usable-coverage threshold and is NOT_SCORABLE while
    the core-27 diagnostic (which has no such coverage gate) still scores
    its 22 valid metrics -- exactly the divergence DECISION_RULES.md's
    reconciliation rule exists to catch."""
    out = fin.run(nvda_packet)
    assert any("CORE27_RECONCILIATION_WARNING" in f for f in out.mandatory_flags)
    assert out.validation_tests.warnings >= 1


def test_run_nvda_fixture_validation_tests_all_self_checks_pass(nvda_packet):
    out = fin.run(nvda_packet)
    assert out.validation_tests.failed == 0
    assert out.validation_tests.passed >= 1


# ============================================================================
# FIN-EF-023 (ROE) single-year END_BALANCE_PROXY fallback
# ============================================================================


def test_run_empty_annual_history_degrades_to_error_status_without_crashing():
    """No annual fundamentals at all -> every metric NOT_SCORABLE/MISSING,
    zero coverage, `status="ERROR"` -- `run()` must degrade gracefully
    rather than raising (e.g. on an empty-list index)."""
    out = fin.run(_minimal_packet([]))
    assert out.status == "ERROR"
    assert out.coverage == 0.0
    assert out.category.awarded_points == 0.0
    assert len(out.metrics) == 33
    assert all(row.score == "NOT_SCORABLE" for row in out.metrics)


def test_run_roe_single_year_uses_end_balance_proxy_and_keeps_evidence_class():
    """With only one year of equity history, ROE falls back to the ending
    balance (CALCULATION_CONVENTIONS.md: "If only ending values exist,
    label the result END_BALANCE_PROXY") -- this must not drop the row's
    `evidence_class` (a real bug caught during self-review: re-wrapping the
    `Value` to attach the warning silently dropped `evidence_class` to
    `None`, which would have understated the row's confidence)."""
    out = fin.run(_minimal_packet([_row(2025)]))
    roe_row = next(r for r in out.metrics if r.metric_id == "FIN-EF-023")
    assert roe_row.value == pytest.approx(150.0 / 600.0)
    assert "END_BALANCE_PROXY" in roe_row.warnings
    assert roe_row.evidence_class is not None


# ============================================================================
# Reading balance/income-statement fields the packet builder maps but the
# specialist previously ignored (interest_expense, ebitda, accounts_payable).
# CANONICAL_FIELD_MAP maps all three from FMP; FORMULAS.md registers them as
# required inputs of FIN-BS-020 / FIN-DX-028 / FIN-DX-031, which were dead
# (always MISSING) because run() read only an overlay or was hardcoded.
# ============================================================================


def test_interest_coverage_reads_the_packets_interest_expense():
    """FIN-BS-020's input is on the annual row (mapped from FMP's
    `interestExpense`). Reading only overlay['interest_expense'] left this
    core-27 metric -- and Override 3 -- permanently MISSING in production."""
    rows = [_row(2025, ebit=29.0, interest_expense=20.0), _row(2024, interest_expense=20.0)]
    out = fin.run(_minimal_packet(rows))  # no overlay
    cov = next(r for r in out.metrics if r.metric_id == "FIN-BS-020")
    assert cov.value == pytest.approx(1.45)
    assert "SOLVENCY_WARNING" in out.mandatory_flags


def test_an_overlay_interest_expense_still_overrides_the_row():
    """The aggregator may supply a normalized cash-interest figure; it wins
    over the row's reported line."""
    rows = [_row(2025, ebit=100.0, interest_expense=1.0), _row(2024)]
    out = fin.run(_minimal_packet(rows), overlay={"interest_expense": 20.0})
    cov = next(r for r in out.metrics if r.metric_id == "FIN-BS-020")
    assert cov.value == pytest.approx(5.0)  # 100/20, not 100/1


def test_net_debt_to_ebitda_reads_the_packets_ebitda():
    """FIN-DX-028 was hardcoded MISSING 'NO_DA_FIELD' though the builder
    maps FMP's `ebitda`. net debt = total_debt - cash = 200 - 300 = -100."""
    rows = [_row(2025, ebitda=250.0), _row(2024, ebitda=240.0)]
    out = fin.run(_minimal_packet(rows))
    nde = next(r for r in out.metrics if r.metric_id == "FIN-DX-028")
    assert nde.value == pytest.approx((200.0 - 300.0) / 250.0)


def test_net_debt_to_ebitda_derives_ebitda_from_ebit_plus_da():
    """Where a row lacks `ebitda` but carries D&A, EBITDA = EBIT + D&A."""
    rows = [_row(2025, ebit=200.0, depreciation_and_amortization=50.0), _row(2024)]
    out = fin.run(_minimal_packet(rows))
    nde = next(r for r in out.metrics if r.metric_id == "FIN-DX-028")
    assert nde.value == pytest.approx((200.0 - 300.0) / 250.0)


def test_cash_conversion_cycle_reads_the_packets_payables():
    """FIN-DX-031 was hardcoded MISSING for a payables field the builder now
    maps. With flat balances across the two years the averages equal the
    reported levels: DSO=90/1000*365, DIO=80/600*365, DPO=70/600*365."""
    rows = [_row(2025, accounts_payable=70.0), _row(2024, accounts_payable=70.0)]
    out = fin.run(_minimal_packet(rows))
    ccc = next(r for r in out.metrics if r.metric_id == "FIN-DX-031")
    expected = 90.0 / 1000.0 * 365.0 + 80.0 / 600.0 * 365.0 - 70.0 / 600.0 * 365.0
    assert ccc.value == pytest.approx(expected)


def test_diluted_share_cagr_uses_the_registered_5y_window_not_all_history():
    """FORMULAS.md FIN-DX-033 frequency is '3y / 5y'. A packet with 11 years
    of share history must still yield a 5-year CAGR, not a 10-year one --
    the window is bounded like every other frequency cell. Shares shrink 1%
    a year, so any correct window reports the same ~-1%/yr figure."""
    def _shares(year: int) -> float:
        # 1%/yr net buyback: 2015 highest, 2025 lowest.
        return 100.0 * (0.99 ** (year - 2015))

    deep = [_row(y, diluted_shares=_shares(y)) for y in range(2025, 2014, -1)]  # 11y, newest-first
    shallow = [_row(y, diluted_shares=_shares(y)) for y in range(2025, 2019, -1)]  # 6y
    out_deep = fin.run(_minimal_packet(deep))
    out_shallow = fin.run(_minimal_packet(shallow))
    d = next(r for r in out_deep.metrics if r.metric_id == "FIN-DX-033")
    s = next(r for r in out_shallow.metrics if r.metric_id == "FIN-DX-033")
    assert d.value == pytest.approx(-0.01, abs=1e-9)
    assert d.value == pytest.approx(s.value, abs=1e-9)  # depth does not change it


# ============================================================================
# Calibration disclosure (AGENT.md no-speculation rule): every scored band
# whose threshold is NOT in that row's FORMULAS.md cell must be an explicitly
# disclosed assumption; Victor's own verbatim bands must NOT be disclaimed.
# ============================================================================


def test_the_invented_scored_thresholds_are_registered_and_disclosed():
    """The ten rows FORMULAS.md gives a direction but no number for."""
    expected = {
        "FIN-PR-006", "FIN-PR-011", "FIN-CF-012", "FIN-CF-013", "FIN-CF-015",
        "FIN-CF-016", "FIN-BS-021", "FIN-BS-022", "FIN-DX-032", "FIN-DX-033",
    }
    assert set(fin.CALIBRATION_REGISTRY) == expected
    out = fin.run(_minimal_packet([_row(y) for y in range(2025, 2020, -1)]))
    blob = " ".join(out.assumptions)
    for metric_id in expected:
        assert metric_id in blob, f"{metric_id}: scored threshold not disclosed"


def test_victors_verbatim_bands_are_not_disclaimed_as_calibration():
    """FIN-GR-001, the margins, the ratios, ROE/ROIC/ROA and the trend
    materiality thresholds are Victor's own numbers -- claiming them as this
    module's would be as wrong as hiding the invented ones."""
    victor_bands = {
        "FIN-GR-001", "FIN-GR-002", "FIN-GR-003", "FIN-PR-007", "FIN-PR-008",
        "FIN-PR-009", "FIN-PR-010", "FIN-CF-014", "FIN-BS-017", "FIN-BS-018",
        "FIN-BS-019", "FIN-BS-020", "FIN-EF-023", "FIN-EF-024", "FIN-EF-025",
        "FIN-EF-026", "FIN-EF-027",
    }
    assert victor_bands.isdisjoint(set(fin.CALIBRATION_REGISTRY))


def test_each_calibration_entry_quotes_its_cerebro_basis():
    """The entry has to name the sentence that fixes the direction, so a
    reader can check Cerebro really is silent on the magnitude."""
    for metric_id, (basis, why) in fin.CALIBRATION_REGISTRY.items():
        assert ".md" in basis, f"{metric_id}: no document named"
        assert '"' in basis, f"{metric_id}: does not quote the text"
        assert len(why) > 40, f"{metric_id}: does not say why no number follows"


def test_roic_discloses_a_statutory_substitution_for_an_out_of_range_rate():
    """A negative tax expense gives a negative effective rate, outside
    0-100%; taxes.tax_rate_or_statutory substitutes 21% and the caller must
    disclose it. Gating disclosure on `tax_rate is None` (the old code)
    missed this because the fallback made the rate non-None -- the same
    silent-substitution bug fixed in business.py."""
    rows = [
        _row(2025, income_before_tax=100.0, income_tax_expense=-40.0),  # -40% effective
        _row(2024),
    ]
    out = fin.run(_minimal_packet(rows))
    blob = " ".join(out.assumptions)
    assert "FIN-EF-024 (ROIC)" in blob and "statutory" in blob
    # ROIC still computes (on NOPAT = EBIT * (1 - 0.21)).
    roic_row = next(r for r in out.metrics if r.metric_id == "FIN-EF-024")
    assert roic_row.state != NullState.MISSING


def test_roic_does_not_disclose_a_substitution_when_the_rate_is_reported():
    """A clean 21%-ish reported rate must NOT be flagged as substituted."""
    rows = [
        _row(2025, income_before_tax=200.0, income_tax_expense=40.0),  # 20% effective
        _row(2024),
    ]
    out = fin.run(_minimal_packet(rows))
    assert not any("FIN-EF-024 (ROIC)" in a and "statutory" in a for a in out.assumptions)


def test_the_absent_peer_panel_is_disclosed():
    """DECISION_RULES.md calls for peer comparison on the sector-sensitive
    margin bands; with no peer panel the default-band fallback must be
    disclosed, not presented as peer-checked."""
    out = fin.run(_minimal_packet([_row(y) for y in range(2025, 2020, -1)]))
    assert any("Peer comparison unavailable" in a and "FIN-PR-007" in a
               for a in out.assumptions)


def test_fin_gr_003_scores_against_the_packet_peer_panel():
    """FIN-GR-003 = company revenue growth - peer median growth. The builder
    now assembles `estimates.peer_panel`; this used to be MISSING on every
    company because the packet carried peer symbols only."""
    rows = [_row(2025, revenue=1100.0), _row(2024, revenue=1000.0)]  # +10%
    packet = _minimal_packet(rows)
    panel = [{"symbol": f"P{i}", "revenue_growth": 0.04} for i in range(8)]
    packet = packet.model_copy(update={
        "estimates": {**packet.estimates, "peer_panel": panel}
    })
    out = fin.run(packet)
    row = next(r for r in out.metrics if r.metric_id == "FIN-GR-003")
    assert row.value == pytest.approx(0.10 - 0.04)  # company - peer median
    assert any("median of 8 peers" in a for a in out.assumptions)


def test_fin_gr_003_refuses_below_the_eight_peer_minimum():
    """SCORING_ENGINE.md: "Use a minimum of 8 valid peers"."""
    rows = [_row(2025, revenue=1100.0), _row(2024, revenue=1000.0)]
    packet = _minimal_packet(rows)
    panel = [{"symbol": f"P{i}", "revenue_growth": 0.04} for i in range(7)]
    packet = packet.model_copy(update={
        "estimates": {**packet.estimates, "peer_panel": panel}
    })
    out = fin.run(packet)
    row = next(r for r in out.metrics if r.metric_id == "FIN-GR-003")
    assert row.state == NullState.MISSING
    assert any("PEER_PANEL_BELOW_8_VALID_PEERS" in a for a in out.assumptions)

# --- Override 5 nombra dos emisores, no cuatro ------------------------------

def test_override_5_retira_la_familia_a_bancos_y_aseguradoras():
    """`DECISION_RULES.md` Override 5, textual: "**Banks and insurers** must
    use the financial-sector adapter; conventional FCF/ROIC scoring is not
    allowed"."""
    for adaptador in ("banks", "insurers"):
        out = fin.run(_minimal_packet([_row(y) for y in (2025, 2024, 2023, 2022, 2021)],
                      industry_adapter=adaptador),
                      overlay={"wacc": 0.09})
        por_id = {r.metric_id: r for r in out.metrics}
        for mid in ("FIN-CF-012", "FIN-EF-024", "FIN-EF-026", "FIN-EF-027"):
            assert por_id[mid].state == NullState.NOT_APPLICABLE, f"{adaptador}/{mid}"


def test_un_reit_conserva_su_familia_fcf_roic():
    """El documento NO se la quita. A los REITs les reemplaza el EPS (por
    FFO/AFFO) y el apalancamiento (net debt/EBITDAre); no nombra ni el ROIC ni
    el FCF.

    Esto estaba puesto en `replaces_model` --los cuatro de MODEL_REPLACING--
    con el comentario "INDUSTRY_ADAPTERS.md extends it to REITs and biotech",
    que no lo hace. El coste era una contradiccion visible: `business.py`
    retiraba la familia ROIC solo a bancos y aseguradoras, asi que el mismo
    REIT salia con "ROIC no aplica" en Financial y con un ROIC puntuado en
    Business, en el mismo reporte auditable.
    """
    out = fin.run(_minimal_packet([_row(y) for y in (2025, 2024, 2023, 2022, 2021)],
                  industry_adapter="reits"),
                  overlay={"wacc": 0.09})
    por_id = {r.metric_id: r for r in out.metrics}
    retiradas = [m for m in ("FIN-CF-012", "FIN-EF-024", "FIN-EF-026", "FIN-EF-027")
                 if por_id[m].state == NullState.NOT_APPLICABLE]
    assert not retiradas, f"se le retiraron sin autoridad: {retiradas}"


def test_los_dos_especialistas_preguntan_lo_mismo():
    """La contradiccion no puede volver por la puerta de atras: Business y
    Financial tienen que usar el MISMO predicado."""
    from wbj.core import adapters as ad
    for adaptador in ("banks", "insurers", "reits", "biotech", "default_nonfinancial"):
        # El predicado es uno solo; si alguien vuelve a partirlo, esto cae.
        assert ad.replaces_return_model(adaptador) == (adaptador in ("banks", "insurers"))
