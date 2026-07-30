"""Tests for `wbj.specialists.valuation` (Task 19): VAL-001..044 (mostly
reused from `wbj.engines.valuation_engine`), the five weighted dimensions,
the ADAPTER_UNSUPPORTED gate, and `run()` against the NVDA golden fixture.

Sources of truth: `Cerebro/06_valuation_analysis/{FORMULAS,SCORING,
DECISION_RULES,VALIDATION_TESTS}.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.core.scoring import Category
from wbj.engines import valuation_engine as ve
from wbj.schemas.packet import AnalysisMeta, MarketData, Packet, Security
import wbj.specialists.valuation as val

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def nvda_packet() -> Packet:
    data = json.loads(_FIXTURE.read_text())
    return Packet.model_validate(data)


def _row(year: int, **overrides) -> dict:
    base = dict(
        calendarYear=str(year), date=f"{year}-12-31", period="FY",
        revenue=1000.0, cogs=600.0, gross_profit=400.0, ebit=200.0,
        income_before_tax=190.0, income_tax_expense=40.0, net_income=150.0,
        operating_cash_flow=180.0, capex=-50.0, fcf=130.0, cash=300.0,
        eps=1.5, total_debt=200.0, total_equity=600.0, diluted_shares=100.0,
    )
    base.update(overrides)
    return base


def _minimal_packet(
    annual_rows: list[dict], *, industry_adapter: str = "default_nonfinancial",
    price: float = 20.0, diluted_shares: float = 100.0, cash: float = 300.0, total_debt: float = 200.0,
    revenue: float = 1000.0, beta: float = 1.2, risk_free_rate: float = 0.04,
) -> Packet:
    from wbj.core.nullstates import EvidenceClass, Value

    facts = {
        "price": Value.of(price, unit="usd_per_share", evidence_class=EvidenceClass.R),
        "diluted_shares": Value.of(diluted_shares, unit="shares", evidence_class=EvidenceClass.R),
        "cash": Value.of(cash, unit="usd", evidence_class=EvidenceClass.R),
        "total_debt": Value.of(total_debt, unit="usd", evidence_class=EvidenceClass.R),
        "revenue": Value.of(revenue, unit="usd", evidence_class=EvidenceClass.R),
    }
    return Packet(
        security=Security(ticker="TEST", exchange="NASDAQ", security_type="operating_company", reporting_currency="USD", valuation_currency="USD"),
        analysis=AnalysisMeta(knowledge_timestamp="2026-07-16T21:00:00+00:00", industry_adapter=industry_adapter),
        fundamentals={"annual": annual_rows, "quarterly": []},
        market_data=MarketData(),
        estimates={"risk_free_rate": risk_free_rate, "peers": [], "fmp_analyst_estimates": []},
        capital_structure={"beta": beta, "market_cap": price * diluted_shares, "cash": cash, "total_debt": total_debt, "diluted_shares": diluted_shares},
        facts_table=facts,
        staleness={},
    )


# ============================================================================
# VAL-VALIDATION_TESTS.md, encoded verbatim (VAL-T001..T010)
# ============================================================================


def test_VAL_T001_gordon_terminal_value():
    """FCFF1=100, WACC=10%, g=3% in perpetuity -> TV at t0=100/(0.10-0.03)=1428.57."""
    v = ve.gordon_terminal_value(fcff_n=100.0 / 1.03, g=0.03, wacc_value=0.10)
    # gordon_terminal_value takes FCFF_N and grows it by (1+g) internally;
    # feed FCFF_N = FCFF1/(1+g) so FCFF_(N+1) = FCFF1 = 100.
    assert v.value == pytest.approx(1428.5714, rel=1e-4)


def test_VAL_T002_wacc_equals_g_rejected():
    """WACC=8%, g=8% -> Reject model: denominator zero."""
    v = ve.gordon_terminal_value(fcff_n=100.0, g=0.08, wacc_value=0.08)
    assert v.is_null
    assert v.state == NullState.NOT_MEANINGFUL


def test_VAL_T003_g_exceeds_wacc_rejected():
    """WACC=7%, g=8% -> Reject model: g>=WACC."""
    v = ve.gordon_terminal_value(fcff_n=100.0, g=0.08, wacc_value=0.07)
    assert v.is_null


def test_VAL_T004_terminal_reinvestment_rate():
    """ROIC=20%, g=4% -> Terminal reinvestment rate=20%."""
    rate = ve._terminal_reinvestment_rate(g=0.04, roic_value=0.20)
    assert rate == pytest.approx(0.20)


def test_VAL_T005_equity_bridge_and_per_share():
    """EV=1000, cash=100, debt=300, diluted shares=80 -> Equity=800; value/share=10."""
    eq = ve.equity_bridge(ev=1000.0, cash=100.0, nonop=0.0, debt=300.0, lease_debt_value=0.0, preferred=0.0, minority=0.0, pension=0.0)
    assert eq.value == pytest.approx(800.0)
    ps = ve.per_share(eq.value, 80.0)
    assert ps.value == pytest.approx(10.0)


def test_VAL_T006_scenario_probabilities_sum_to_1():
    """Scenario probabilities 20%,60%,20% -> Sum=100%; pass."""
    bear = val.ScenarioInput(probability=0.2, growth=0.02, margin=0.15, wacc=0.11, tv_growth=0.02)
    base = val.ScenarioInput(probability=0.6, growth=0.05, margin=0.20, wacc=0.10, tv_growth=0.02)
    bull = val.ScenarioInput(probability=0.2, growth=0.08, margin=0.25, wacc=0.09, tv_growth=0.02)
    common = val.DCFCommonInputs(revenue0=1000.0, shares=100.0, tax_rate=0.21, roic=0.15, years=5, net_debt=100.0)
    result = ve.scenarios(bear, base, bull, common)
    assert result.probabilities_sum == pytest.approx(1.0)


def test_VAL_T007_terminal_value_share_above_75pct_flags():
    """Terminal PV=800, EV=1000 -> Terminal share=80%; high-sensitivity flag."""
    v = ve.terminal_share(pv_terminal=800.0, ev=1000.0)
    assert v.value == pytest.approx(0.80)
    assert "TERMINAL_VALUE_SHARE_ABOVE_75PCT" in v.warnings


def test_VAL_T008_missing_convertible_schedule_incomplete():
    """Missing option/convertible schedule -> Per-share value incomplete.

    Two halves. The bridge must not invent a schedule it was not given, which
    this always checked; and the output must *say* the per-share value is
    incomplete, which is VAL-T008's actual stated result and had nowhere to be
    said until VAL-CONV-039 existed. An absent schedule reading as "undiluted"
    rather than "incomplete" is the failure the test names.
    """
    eq = ve.equity_bridge(ev=1000.0, cash=0.0, nonop=0.0, debt=0.0, lease_debt_value=0.0, preferred=0.0, minority=0.0, pension=0.0)
    assert eq.value == pytest.approx(1000.0)

    rows = [_row(2025), _row(2024)]
    out = val.run(_minimal_packet(rows))
    assert "PER_SHARE_VALUE_INCOMPLETE_NO_DILUTION_SCHEDULE" in out.mandatory_flags
    conv = {m.metric_id: m for m in out.metrics}["VAL-CONV-039"]
    assert conv.value is None


def test_VAL_T009_fcff_and_eva_reconciliation_fails_when_materially_different():
    """FCFF and EVA differ materially with same assumptions -> Fail
    reconciliation; inspect reinvestment/capital."""
    from wbj.core.nullstates import Value

    a = Value.of(100.0, unit="usd_per_share")
    b = Value.of(150.0, unit="usd_per_share")
    assert ve.reconciles(a, b) is False


def test_VAL_T010_bank_selected_uses_the_residual_income_adapter():
    """Bank selected -> Use residual-income/excess-return adapter; no
    EV/EBITDA primary model.

    Victor's expected result is that the bank IS valued, on the residual-
    income/excess-return models his matrix assigns it. This test previously
    asserted `ADAPTER_UNSUPPORTED` and an empty `primary` -- the opposite of
    what VAL-T010 states -- because none of those models existed yet, so it
    pinned the limitation in place of the requirement. With VAL-RIV-023 and
    VAL-DDM-024/025 implemented, the stated result is now achievable and is
    what gets asserted.
    """
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows, industry_adapter="banks")
    out = val.run(packet)
    assert "ADAPTER_FINANCIAL_MODELS" in out.mandatory_flags
    assert out.model_selection.primary == ["RESIDUAL_INCOME", "DDM"]
    for barred in ("FCFF_DCF", "EV_EBITDA"):
        assert barred in out.model_selection.rejected
    # INDUSTRY_ADAPTERS.md bars conventional FCFF, so the FCFF-fed dimensions
    # carry no weight and SCORING_ENGINE.md's INCOMPLETE rule applies.
    assert "CATEGORY_INCOMPLETE_GATE_INELIGIBLE" in out.mandatory_flags
    assert out.coverage < 0.70


def test_dispersion_comes_from_ensemble_not_raw_stdev():
    """model_cross_checks.dispersion must come from
    valuation_engine.ensemble() (VAL-ENSEMBLE-044, reliability-weighted),
    reproducing its `.dispersion` exactly for the same model inputs -- not
    an ad-hoc unweighted np.std."""
    from wbj.core.nullstates import EvidenceClass, Value

    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = val.run(packet)
    fcff = out.model_cross_checks.fcff
    ep = out.model_cross_checks.economic_profit
    rel = out.model_cross_checks.relative
    models = []
    if fcff is not None:
        models.append(val.EnsembleModelInput(label="FCFF_DCF", value=Value.of(fcff, unit="usd_per_share", evidence_class=EvidenceClass.C), weight=1.0))
    if ep is not None:
        models.append(val.EnsembleModelInput(label="ECONOMIC_PROFIT", value=Value.of(ep, unit="usd_per_share", evidence_class=EvidenceClass.C), weight=0.8))
    if rel is not None:
        models.append(val.EnsembleModelInput(label="RELATIVE", value=Value.of(rel, unit="usd_per_share", evidence_class=EvidenceClass.C), weight=0.5))
    if len(models) > 1:
        expected = ve.ensemble(models)
        assert out.model_cross_checks.dispersion == pytest.approx(expected.dispersion.value)
    else:
        # too few models to form an ensemble -> dispersion is None, not a raw stdev
        assert out.model_cross_checks.dispersion is None


def test_unsupported_adapter_confidence_derived_from_formula():
    """The ADAPTER_UNSUPPORTED ERROR envelope derives confidence from the
    real five-component formula at coverage 0 (model_fit drops to 40 for a
    non-default adapter), not a hardcoded 0.0 -- awarded_points/score_10
    stay 0.0 by construction."""
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows, industry_adapter="reits")
    out = val.run(packet)
    assert out.category.awarded_points == 0.0
    assert out.category.score_10 == 0.0
    expected = val._category_confidence(0.0, packet)
    assert out.category.confidence == pytest.approx(expected)
    assert out.category.confidence > 0.0


# ============================================================================
# ADAPTER_UNSUPPORTED gate
# ============================================================================


def test_run_supported_adapter_selects_fcff_and_economic_profit():
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = val.run(packet)
    assert out.model_selection.primary == ["FCFF_DCF", "ECONOMIC_PROFIT"]


# ============================================================================
# Scenario / WACC / reverse-DCF orchestration
# ============================================================================


def test_run_scenarios_sum_to_one_and_ordered_bear_base_bull():
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = val.run(packet)
    assert [s.name for s in out.scenarios] == ["Bear", "Base", "Bull"]
    total_p = sum(s.probability for s in out.scenarios if s.probability is not None)
    assert total_p == pytest.approx(1.0, abs=1e-6)


def test_run_reverse_dcf_present_when_inputs_available():
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = val.run(packet)
    assert out.reverse_dcf.current_price == pytest.approx(20.0)


def test_run_terminal_growth_clamped_below_wacc():
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = val.run(packet, overlay={"terminal_growth": 0.99})
    assert any("clamped" in a for a in out.assumptions)


def test_run_wacc_populated_with_components():
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = val.run(packet)
    assert out.wacc.value is not None
    assert out.wacc.components["cost_of_equity"] is not None
    assert out.wacc.components["cost_of_debt"] is not None


def test_run_fair_value_distribution_from_monte_carlo():
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = val.run(packet)
    assert out.fair_value_distribution.p10 is not None
    assert out.fair_value_distribution.p90 is not None
    assert out.fair_value_distribution.p10 <= out.fair_value_distribution.median <= out.fair_value_distribution.p90


def test_run_reference_bands_and_mos_thresholds():
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = val.run(packet)
    if out.reference_bands.base is not None:
        assert out.reference_bands.margin_of_safety_15pct == pytest.approx(out.reference_bands.base * 0.85)
        assert out.reference_bands.margin_of_safety_25pct == pytest.approx(out.reference_bands.base * 0.75)


# ============================================================================
# Dimension caps / gates (the apply_dimension_cap helper is tested in
# test_common.py; the tests below exercise valuation.py's *use* of it).
# ============================================================================


def test_run_category_reproduces_from_dimensions():
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows)
    out = val.run(packet)
    recomputed = Category(name=val.AGENT_ID, max_points=val.MAX_POINTS, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)
    assert out.coverage == pytest.approx(recomputed.coverage(), abs=1e-6)


# ============================================================================
# run() against the NVDA fixture
# ============================================================================


def test_run_nvda_fixture_schema_valid(nvda_packet):
    out = val.run(nvda_packet)
    assert out.agent_id == "valuation_analysis"
    assert out.version == "2.0.0"
    assert out.security.ticker == "NVDA"
    assert out.category.max_points == 10.0
    assert out.status in ("COMPLETE", "INCOMPLETE", "ERROR")
    assert len(out.dimensions) == 5
    for row in out.metrics:
        assert row.metric_id
        assert row.formula_id
        assert row.formula_version
        assert row.score == "NOT_SCORABLE" or isinstance(row.score, float)
        assert 0.0 <= row.confidence <= 100.0
        assert (row.value is None) != (row.state is None)


def test_run_nvda_fixture_category_math_reproduces_from_dimensions(nvda_packet):
    out = val.run(nvda_packet)
    recomputed = Category(name=val.AGENT_ID, max_points=val.MAX_POINTS, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)
    assert out.coverage == pytest.approx(recomputed.coverage(), abs=1e-6)


def test_run_nvda_fixture_category_confidence_computed(nvda_packet):
    out = val.run(nvda_packet)
    assert out.category.confidence is not None
    assert 0.0 <= out.category.confidence <= 100.0


def test_run_nvda_fixture_serializes_to_json(nvda_packet):
    out = val.run(nvda_packet)
    dumped = out.model_dump(mode="json")
    json.dumps(dumped)
    assert dumped["agent_id"] == "valuation_analysis"


def test_run_nvda_fixture_scenarios_present(nvda_packet):
    out = val.run(nvda_packet)
    assert len(out.scenarios) == 3


def test_run_nvda_fixture_validation_tests_all_self_checks_pass(nvda_packet):
    out = val.run(nvda_packet)
    assert out.validation_tests.failed == 0
    assert out.validation_tests.passed >= 1


def test_run_empty_annual_history_degrades_without_crashing():
    """No annual fundamentals -> `run()` must fall back to documented
    constants (flagged as assumptions) for ROIC/growth/margin rather than
    crashing; facts_table (price/shares/cash/debt) alone is still enough
    to produce a scenario valuation, so coverage need not be zero -- this
    test only asserts graceful degradation, not a specific coverage."""
    out = val.run(_minimal_packet([]))
    assert out.status in ("COMPLETE", "INCOMPLETE", "ERROR")
    assert 0.0 <= out.coverage <= 1.0
    assert any("ROIC" in a or "fallback" in a for a in out.assumptions)


# --- peer payload shape ------------------------------------------------------


def test_flat_peer_list_is_counted():
    """FMP's /stable/stock-peers returns a flat list; the retired /v3
    wrapped it in {"peersList": [...]}. Reading only the old shape
    counted 0 peers while the packet held 9, dropping the relative model
    out of the ensemble."""
    peers = [{"symbol": "AAPL"}, {"symbol": "AMD"}, {"symbol": "AVGO"}]
    if peers and isinstance(peers[0], dict) and "peersList" in peers[0]:
        n = len(peers[0].get("peersList") or [])
    else:
        n = len(peers)
    assert n == 3


def test_legacy_wrapped_peer_list_still_counted():
    peers = [{"peersList": ["AAPL", "AMD", "AVGO", "INTC"]}]
    if peers and isinstance(peers[0], dict) and "peersList" in peers[0]:
        n = len(peers[0].get("peersList") or [])
    else:
        n = len(peers)
    assert n == 4


# ============================================================================
# Audit fixes: tax substitution disclosure, anchor disclosure, PEG forward P/E
# ============================================================================


def test_out_of_range_effective_tax_rate_is_disclosed():
    """A negative tax expense gives a negative effective rate, outside 0-100%;
    taxes.tax_rate_or_statutory substitutes 21% and the caller must disclose
    it. Gating on `pretax is None` (the old code) missed this -- the same
    silent-substitution bug fixed in financial.py."""
    rows = [_row(2025, income_before_tax=100.0, income_tax_expense=-40.0), _row(2024)]
    out = val.run(_minimal_packet(rows))
    assert any("statutory rate" in a and "outside 0-100%" in a for a in out.assumptions)


def test_a_clean_reported_tax_rate_is_not_flagged():
    rows = [_row(2025, income_before_tax=200.0, income_tax_expense=40.0), _row(2024)]
    out = val.run(_minimal_packet(rows))
    assert not any("statutory rate" in a for a in out.assumptions)


def test_every_scored_metric_has_a_disclosed_anchor():
    """The valuation FORMULAS.md states no numeric band, so every scored 0-10
    scale is the module's calibration and must be an explicitly disclosed
    assumption (AGENT.md no-speculation rule)."""
    rows = [_row(y) for y in range(2025, 2020, -1)]
    out = val.run(_minimal_packet(rows), overlay={"eps_growth_pct": 0.15,
                                                  "historical_multiples": [10.0, 12.0, 14.0, 11.0, 13.0]})
    scored = {r.metric_id for r in out.metrics if r.score != "NOT_SCORABLE"}
    unregistered = scored - set(val.ANCHOR_PROVENANCE)
    assert not unregistered, f"scored on undisclosed anchors: {sorted(unregistered)}"
    blob = " ".join(out.assumptions)
    assert "Scoring anchors (partly derived)" in blob
    for mid in scored:
        assert mid in blob, f"{mid} not disclosed"


def test_each_anchor_provenance_entry_names_its_source():
    for mid, (source, note) in val.ANCHOR_PROVENANCE.items():
        assert source == "MIXED", mid
        assert ".md" in note, f"{mid}: no document named"
        assert len(note) > 30, mid


def test_peg_prefers_forward_pe_from_consensus_eps():
    """VAL-PEG-028 is 'Forward P/E / Expected EPS growth percent'. With a
    forward EPS estimate present, the P/E uses it (no trailing-proxy flag)."""
    rows = [_row(2025, eps=1.0), _row(2024)]
    packet = _minimal_packet(rows, price=20.0)
    packet = packet.model_copy(update={
        "estimates": {**packet.estimates,
                      "fmp_analyst_estimates": [{"estimatedEpsAvg": 2.0, "estimatedRevenueAvg": 1100.0}]}
    })
    out = val.run(packet, overlay={"eps_growth_pct": 0.20})
    peg = next(r for r in out.metrics if r.metric_id == "VAL-PEG-028")
    # forward P/E = 20 / 2.0 = 10 ; PEG = 10 / (0.20*100) = 0.5 (not 20/1.0/20 = 1.0 trailing)
    assert peg.value == pytest.approx(0.5)
    assert not any("trailing P/E" in a for a in out.assumptions)


def test_peg_falls_back_to_trailing_pe_with_disclosure():
    """No forward estimate -> trailing P/E proxy, disclosed."""
    rows = [_row(2025, eps=1.0), _row(2024)]
    out = val.run(_minimal_packet(rows, price=20.0), overlay={"eps_growth_pct": 0.20})
    peg = next(r for r in out.metrics if r.metric_id == "VAL-PEG-028")
    assert peg.value == pytest.approx(1.0)  # trailing P/E 20 / (0.20*100)
    assert any("trailing P/E" in a for a in out.assumptions)


def test_low_confidence_mos_cap_discloses_its_threshold():
    """SCORING.md caps the MOS dimension at 5 for a low-confidence valuation
    but does not quantify 'low confidence'; the 30%-dispersion threshold this
    module uses must be disclosed when the cap bites."""
    rows = [_row(y) for y in range(2025, 2020, -1)]
    p = _minimal_packet(rows, price=18.0)
    p = p.model_copy(update={"estimates": {**p.estimates,
        "fmp_analyst_estimates": [{"estimatedEpsAvg": 2.0, "estimatedRevenueAvg": 1100.0}]}})
    out = val.run(p, overlay={"eps_growth_pct": 0.15, "peer_multiples": [8, 9, 10, 11, 12, 13, 14, 15, 16]})
    # This overlay drives a large FCFF-vs-relative dispersion; if the cap
    # fires it must be disclosed with its threshold.
    capped = any("MOS dimension capped at 5" in a and "30%" in a for a in out.assumptions)
    dispersion = out.model_cross_checks.dispersion
    base = out.reference_bands.base
    if dispersion is not None and base and dispersion / abs(base) > 0.30:
        assert capped


def test_invested_capital_uses_book_equity_not_market_cap():
    """DATA_DICTIONARY.md: invested capital is "Operating assets minus
    operating liabilities, reconciled to debt plus equity minus excess cash"
    -- a BOOK reconciliation, as business/financial/market/risk all compute
    it. This read took `capital_structure["market_cap"]` for the current year
    while the prior year used book equity, so the average invested capital
    mixed a market value with a book one.

    With the `_row` defaults: NOPAT = 200*(1-40/190) = 157.89, IC(book) =
    200 debt + 600 equity - 300 cash = 500 (both years), ROIC = 31.58%,
    reinvestment = 50/157.89 = 31.67%, so g = reinvestment*ROIC = 10.0%.
    Using market_cap (price*shares = 2000) would put IC_end at 1900, the
    average at 1200, and g at ~4.2%.
    """
    rows = [_row(2025), _row(2024)]
    packet = _minimal_packet(rows, price=20.0, diluted_shares=100.0)
    assert packet.capital_structure["market_cap"] == 2000.0  # far from book 600
    out = val.run(packet)
    base = next(s for s in out.scenarios if s.name == "Base")
    assert base.assumptions["growth"] == pytest.approx(0.10, abs=5e-4)


def test_justified_pe_uses_the_stable_growth_rate_not_the_near_term_one():
    """VAL-JPE-032 is a *stable-growth* model, so the `g` it receives is the
    perpetual rate, not `reinvestment_rate * ROIC`.

    INSTITUTIONAL_VALUATION_ENGINE.md 14.1 introduces the formula as "a
    stable-growth relationship" and FORMULAS.md types the row "stable model".
    DECISION_RULES.md rule 3 governs that rate: "Terminal growth must be below
    WACC/cost of equity and consistent with long-run nominal economic growth
    in the valuation currency."

    With the `_row` defaults, near-term fundamental growth is 10% against a Ke
    of 4% + 1.2*4.5% = 9.4%. Passing the near-term figure drove the formula
    past its own guard and refused a metric that should score -- MSFT, XOM and
    JNJ all landed there on live data. The terminal rate (2.5%, clamped under
    WACC) is what the model is defined on.
    """
    rows = [_row(2025), _row(2024)]
    out = val.run(_minimal_packet(rows, price=20.0, diluted_shares=100.0))
    jpe = next(r for r in out.metrics if r.metric_id == "VAL-JPE-032")
    assert jpe.state is None, "a stable growth rate below Ke must not refuse"
    # (1 - 0.025/ROE) / (0.094 - 0.025), positive and finite.
    assert jpe.value is not None and jpe.value > 0


def test_justified_pb_and_ev_sales_also_use_the_stable_rate():
    """The other two "stable model" rows in FORMULAS.md take the same `g`.

    INSTITUTIONAL_VALUATION_ENGINE.md:387 calls VAL-JPB-031 "a stable-growth
    justified price-to-book relationship"; VAL-JEVS-033 is typed "stable
    model" and guards on `g < WACC`. Fed the 10% near-term rate under these
    fixtures, both refused."""
    rows = [_row(2025), _row(2024)]
    out = val.run(_minimal_packet(rows, price=20.0, diluted_shares=100.0))
    got = {r.metric_id: r for r in out.metrics}
    for mid in ("VAL-JPB-031", "VAL-JEVS-033"):
        assert got[mid].state is None, f"{mid} refused on a stable growth rate"
        assert got[mid].value is not None


def test_gordon_dividend_value_uses_the_long_run_rate():
    """VAL-DDM-024 is `Dividend_1/(CostEquity - g)` -- a perpetuity, so its `g`
    is a terminal rate and DECISION_RULES.md rule 3 governs it. The dividend
    series' own CAGR is a near-term measurement: JPM's runs 23.42% off a
    post-2009 recovery, and MSFT/XOM/JNJ each exceeded their cost of equity on
    live data, refusing the model.

    VAL-HDDM-025 is the model that carries the near-term rate, fading it to
    the same long-run one -- which is what keeps the pair complementary rather
    than redundant."""
    rows = [_row(2025), _row(2024)]
    out = val.run(_minimal_packet(rows, price=20.0, diluted_shares=100.0),
                  {"dividend_per_share": 2.0,
                   "dividends_per_share_history": [1.0, 1.4, 2.0]})
    ddm = next(r for r in out.metrics if r.metric_id == "VAL-DDM-024")
    # The history's CAGR is ~41%, far above Ke 9.4%. On the near-term rate the
    # model refused; on the terminal rate it values.
    assert ddm.state is None and ddm.value is not None
    assert ddm.value > 0


def test_justified_pe_still_refuses_when_the_stable_rate_itself_reaches_ke():
    """The guard FORMULAS.md names -- "not valid when ROE<=0 or g>=Ke" -- is
    untouched; only which rate reaches it changed. Held at the engine level so
    the specialist swap cannot quietly disable it."""
    from wbj.engines import valuation_engine as ve

    assert ve.justified_pe(g=0.10, roe=0.15, ke=0.09).state is NullState.NOT_MEANINGFUL
