"""Tests for `wbj.specialists.market` (Task 16): MKT-001..025, the five
weighted dimensions, mandatory flags/caps, and `run()` against the NVDA
golden fixture.

Sources of truth: `Cerebro/03_market_analysis/{FORMULAS,SCORING,
DECISION_RULES,VALIDATION_TESTS}.md`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.core.scoring import Category
from wbj.schemas.packet import AnalysisMeta, MarketData, Packet, Security
import wbj.specialists.market as mkt

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def nvda_packet() -> Packet:
    data = json.loads(_FIXTURE.read_text())
    return Packet.model_validate(data)


def _minimal_packet(
    annual_rows: list[dict],
    *,
    industry_adapter: str = "default_nonfinancial",
    estimates: dict | None = None,
) -> Packet:
    return Packet(
        security=Security(
            ticker="TEST", exchange="NASDAQ", security_type="operating_company",
            reporting_currency="USD", valuation_currency="USD",
        ),
        analysis=AnalysisMeta(
            knowledge_timestamp="2026-07-16T21:00:00+00:00", industry_adapter=industry_adapter,
        ),
        fundamentals={"annual": annual_rows, "quarterly": []},
        market_data=MarketData(),
        estimates=estimates or {},
        capital_structure={},
        facts_table={},
        staleness={},
    )


def _row(year: int, **overrides) -> dict:
    base = dict(
        calendarYear=str(year), date=f"{year}-12-31", period="FY",
        revenue=1000.0, cogs=600.0, gross_profit=400.0, ebit=200.0,
        income_before_tax=190.0, income_tax_expense=40.0, net_income=150.0,
        operating_cash_flow=180.0, capex=-50.0, cash=300.0,
        total_debt=200.0, total_equity=600.0, diluted_shares=100.0,
    )
    base.update(overrides)
    return base


# ============================================================================
# MKT-VALIDATION_TESTS.md, encoded verbatim (MKT-T001..T008)
# ============================================================================


def test_MKT_T001_tam_cagr():
    """TAM 1000 to 1210 over 2 years -> CAGR=10%."""
    v = mkt.tam_cagr(1210.0, 1000.0, 2.0)
    assert v.value == pytest.approx(0.10)


def test_MKT_T002_penetration():
    """Company relevant revenue=50, TAM=1000 -> Penetration=5%."""
    v = mkt.penetration(50.0, 1000.0)
    assert v.value == pytest.approx(0.05)


def test_MKT_T003_market_share_delta():
    """Share 5.0% to 5.7% -> Delta=+0.7 percentage points."""
    v = mkt.market_share_delta(0.057, 0.05)
    assert v.value == pytest.approx(0.007)


def test_MKT_T004_revision_breadth():
    """8 upward revisions out of 10 -> Breadth=80%."""
    v = mkt.revision_breadth(8, 10)
    assert v.value == pytest.approx(0.80)


def test_MKT_T005_catalyst_expected_impact():
    """Prob=.6, impact=100, evidence=.8, time factor=.5 -> Expected impact index=24."""
    v = mkt.catalyst_expected_impact(0.6, 100.0, 0.8, 0.5)
    assert v.value == pytest.approx(24.0)


def test_MKT_T006_forecast_exceeds_tam_fails_consistency_gate():
    """Forecast revenue exceeds TAM -> Fail consistency gate."""
    assert mkt.forecast_consistency_gate(forecast_revenue=1100.0, tam=1000.0) is False
    assert mkt.forecast_consistency_gate(forecast_revenue=900.0, tam=1000.0) is True


def test_MKT_T007_issuer_only_tam_source_tier_4_caps_score_at_6():
    """Only issuer TAM with no method -> Source tier 4, score cap 6."""
    assert mkt.TAM_TIER_CONFIDENCE[4] == 45.0
    assert mkt.tam_confidence_caps_dimension(mkt.TAM_TIER_CONFIDENCE[4]) is True


def test_MKT_T008_snapshot_after_release_rejects_surprise():
    """Consensus snapshot taken after release -> Reject surprise calculation."""
    v = mkt.earnings_surprise(actual=1.0, pre_release_consensus=0.9, snapshot_before_release=False)
    assert v.is_null
    assert v.state == NullState.CONFLICTED
    v_ok = mkt.earnings_surprise(actual=1.0, pre_release_consensus=0.9, snapshot_before_release=True)
    assert v_ok.is_valid
    assert v_ok.value == pytest.approx((1.0 - 0.9) / 0.9)


# ============================================================================
# MKT-TDEC-020: time decay (brief's own worked example: months=12 -> 0.5)
# ============================================================================


def test_time_decay_12_months_is_half_life():
    v = mkt.time_decay(12.0)
    assert v.value == pytest.approx(0.5)


def test_time_decay_zero_months_is_one():
    v = mkt.time_decay(0.0)
    assert v.value == pytest.approx(1.0)


def test_time_decay_matches_formula_directly():
    months = 6.0
    v = mkt.time_decay(months)
    assert v.value == pytest.approx(math.exp(-math.log(2) * months / 12))


# ============================================================================
# Revision breadth >=5-estimate gate
# ============================================================================


def test_revision_breadth_below_5_estimates_is_not_scorable():
    v = mkt.revision_breadth(2, 4)  # only 4 total estimates
    assert v.is_null
    assert v.state == NullState.NOT_SCORABLE


def test_revision_breadth_at_5_estimates_is_scorable():
    v = mkt.revision_breadth(3, 5)
    assert v.is_valid
    assert v.value == pytest.approx(0.6)


# ============================================================================
# Individual formula behavior
# ============================================================================


def test_sam_and_som():
    sam_v = mkt.sam(1000.0, 0.5, 0.8, 0.9)
    assert sam_v.value == pytest.approx(1000.0 * 0.5 * 0.8 * 0.9)
    som_v = mkt.som(sam_v.value, 0.10)
    assert som_v.value == pytest.approx(sam_v.value * 0.10)


def test_industry_hhi():
    v = mkt.industry_hhi([0.4, 0.3, 0.3])
    assert v.value == pytest.approx(0.16 + 0.09 + 0.09)


def test_growth_capacity():
    v = mkt.growth_capacity(0.5, 0.15)
    assert v.value == pytest.approx(0.075)


def test_runway_years_not_meaningful_when_growth_nonpositive():
    v = mkt.runway_years(target_revenue=200.0, current_revenue=100.0, assumed_growth=0.0)
    assert v.is_null
    assert v.state == NullState.NOT_MEANINGFUL


def test_runway_years_computes_ln_ratio():
    v = mkt.runway_years(target_revenue=200.0, current_revenue=100.0, assumed_growth=0.10)
    assert v.value == pytest.approx(math.log(2.0) / math.log(1.10))


def test_revision_magnitude_sign_change_handling():
    v = mkt.revision_magnitude(current_consensus=110.0, prior_consensus=100.0)
    assert v.value == pytest.approx(0.10)
    v_zero = mkt.revision_magnitude(current_consensus=110.0, prior_consensus=0.0)
    assert v_zero.is_null


def test_estimate_dispersion():
    v = mkt.estimate_dispersion([9.0, 10.0, 11.0])
    assert v.is_valid
    assert v.value > 0.0


def test_backlog_growth_and_revenue_coverage():
    bg = mkt.backlog_growth(120.0, 100.0)
    assert bg.value == pytest.approx(0.20)
    cov = mkt.revenue_coverage(80.0, 100.0)
    assert cov.value == pytest.approx(0.80)


def test_operating_leverage_and_incremental_margin():
    op_lev = mkt.operating_leverage(pct_change_oi=0.20, pct_change_rev=0.10)
    assert op_lev.value == pytest.approx(2.0)
    inc_margin = mkt.incremental_operating_margin(delta_oi=20.0, delta_rev=100.0)
    assert inc_margin.value == pytest.approx(0.20)


def test_adoption_penetration_and_arpu_growth():
    adopt = mkt.adoption_penetration(current_units=1000.0, eventual_units=10000.0)
    assert adopt.value == pytest.approx(0.10)
    arpu = mkt.arpu_growth(arpu_t=110.0, arpu_t1=100.0)
    assert arpu.value == pytest.approx(0.10)


def test_scenario_weighted_outcome_requires_probabilities_sum_to_one():
    v = mkt.scenario_weighted_outcome([(0.5, 100.0), (0.5, 200.0)])
    assert v.value == pytest.approx(150.0)
    v_bad = mkt.scenario_weighted_outcome([(0.5, 100.0), (0.6, 200.0)])
    assert v_bad.is_null
    assert v_bad.state == NullState.CONFLICTED


# ============================================================================
# Dimension caps (the apply_dimension_cap helper is tested in test_common.py;
# the tests below exercise market.py's *use* of it -- TAM/catalyst caps).
# ============================================================================


def test_run_tam_dimension_capped_at_6_with_low_source_tier(nvda_packet):
    # Supply enough TAM-dimension members (sam/som/tam_history/penetration/
    # share/share_delta/hhi/adoption) to clear the 70% usable-coverage gate
    # so this test isolates the source-tier cap, not a coverage gate.
    out = mkt.run(
        nvda_packet,
        overlay={
            "tam": 100000.0,
            "tam_source_tier": 4,
            "sam_inputs": {"geography_share": 0.8, "product_share": 0.9, "reachable_share": 0.7},
            "som_inputs": {"target_share": 0.1},
            "tam_history": [80000.0, 90000.0, 100000.0],
            "company_relevant_revenue": 5000.0,
            "share": {"company_sales": 500.0, "total_market_sales": 10000.0},
            "share_history": [0.04, 0.05],
            "competitor_shares": [0.3, 0.2, 0.1],
            "adoption": {"current_units": 1000.0, "eventual_units": 10000.0},
        },
    )
    tam_dim = next(d for d in out.dimensions if d.name == mkt.DIM_TAM)
    assert tam_dim.score10() <= 6.0 + 1e-9


def test_run_catalysts_dimension_capped_at_3_when_narrative_only(nvda_packet):
    out = mkt.run(
        nvda_packet,
        overlay={
            "catalysts": [{"event": "New product launch", "months_to_event": 6.0}],
            "backlog_history": [100.0, 120.0],
            "ntm_contracted": 80.0,
            "ntm_revenue_estimate": 100.0,
        },
    )
    cat_dim = next(d for d in out.dimensions if d.name == mkt.DIM_CATALYSTS)
    assert cat_dim.score10() <= 3.0 + 1e-9


def test_run_catalysts_dimension_not_capped_when_quantified(nvda_packet):
    out = mkt.run(
        nvda_packet,
        overlay={
            "catalysts": [
                {
                    "event": "New product launch", "months_to_event": 6.0,
                    "probability": 0.7, "impact": 500.0, "evidence_quality": 0.8,
                }
            ]
        },
    )
    cat_dim = next(d for d in out.dimensions if d.name == mkt.DIM_CATALYSTS)
    # not capped at 3 -- a fully quantified catalyst may score above the narrative cap
    assert cat_dim.valid_weight() > 0


# ============================================================================
# Judgment requests
# ============================================================================


def test_run_tam_tier_assignment_is_judgment_request(nvda_packet):
    out = mkt.run(nvda_packet)
    ids = {jr.metric_id for jr in out.judgment_requests}
    assert "tam_source_tier_assignment" in ids
    assert "three_growth_thesis_killers" in ids


def test_run_catalyst_probability_impact_evidence_are_judgment_requests(nvda_packet):
    out = mkt.run(nvda_packet, overlay={"catalysts": [{"event": "X", "months_to_event": 3.0}]})
    ids = {jr.metric_id for jr in out.judgment_requests}
    assert any("catalyst" in i for i in ids)


# ============================================================================
# run() against the NVDA fixture
# ============================================================================


def test_run_nvda_fixture_schema_valid(nvda_packet):
    out = mkt.run(nvda_packet)
    assert out.agent_id == "market_analysis"
    assert out.version == "2.0.0"
    assert out.security.ticker == "NVDA"
    assert out.category.max_points == 20.0
    assert out.status in ("COMPLETE", "INCOMPLETE", "ERROR")
    assert len(out.dimensions) == 5
    assert len(out.metrics) == 25
    for row in out.metrics:
        assert row.metric_id
        assert row.formula_id
        assert row.formula_version
        assert row.score == "NOT_SCORABLE" or isinstance(row.score, float)
        assert 0.0 <= row.confidence <= 100.0
        assert (row.value is None) != (row.state is None)


def test_run_nvda_fixture_category_math_reproduces_from_dimensions(nvda_packet):
    out = mkt.run(nvda_packet)
    recomputed = Category(name=mkt.AGENT_ID, max_points=mkt.MAX_POINTS, dimensions=out.dimensions)
    assert out.category.awarded_points == pytest.approx(recomputed.points(), abs=1e-6)
    assert out.category.score_10 == pytest.approx(recomputed.score10(), abs=1e-6)
    assert out.coverage == pytest.approx(recomputed.coverage(), abs=1e-6)


def test_run_nvda_fixture_category_confidence_computed(nvda_packet):
    out = mkt.run(nvda_packet)
    assert out.category.confidence is not None
    assert 0.0 <= out.category.confidence <= 100.0


def test_run_nvda_fixture_serializes_to_json(nvda_packet):
    out = mkt.run(nvda_packet)
    dumped = out.model_dump(mode="json")
    json.dumps(dumped)
    assert dumped["agent_id"] == "market_analysis"


def test_run_operating_leverage_computed_from_packet_without_overlay():
    """MKT-OPLEV-017/018 are computable directly from
    `packet.fundamentals.annual` (ebit/revenue), unlike most of this
    specialist's inputs -- no overlay needed."""
    rows = [_row(2025, revenue=1200.0, ebit=300.0), _row(2024, revenue=1000.0, ebit=200.0)]
    out = mkt.run(_minimal_packet(rows))
    oplev_row = next(r for r in out.metrics if r.metric_id == "MKT-OPLEV-017")
    assert oplev_row.score != "NOT_SCORABLE"


def test_run_empty_annual_history_degrades_without_crashing():
    out = mkt.run(_minimal_packet([]))
    assert out.coverage == 0.0
    assert out.category.awarded_points == 0.0
    assert len(out.metrics) == 25


def test_run_nvda_fixture_validation_tests_all_self_checks_pass(nvda_packet):
    out = mkt.run(nvda_packet)
    assert out.validation_tests.failed == 0
    assert out.validation_tests.passed >= 1


def test_run_nvda_fixture_extension_fields_populated(nvda_packet):
    out = mkt.run(nvda_packet)
    assert out.tam_sam_som is not None
    assert out.three_growth_thesis_killers == []
    assert isinstance(out.catalysts, list)


def test_run_external_capital_flag_when_forecast_exceeds_growth_capacity():
    rows = [_row(2025), _row(2024)]
    out = mkt.run(
        _minimal_packet(rows),
        overlay={"target_revenue": 5000.0, "current_revenue": 1000.0, "assumed_growth": 0.50},
    )
    assert "EXTERNAL_CAPITAL_REQUIRED" in out.mandatory_flags


# --- narrative-only catalyst cap ---------------------------------------------


def test_zero_impact_catalyst_is_not_quantified():
    """SCORING.md caps a narrative-only catalyst dimension at 3. A
    catalyst answered with impact=0 is a quantification in form only: it
    adds nothing to the expected-impact sum, so letting it lift the cap
    would raise the score on a number that says nothing happened."""
    from wbj.specialists.market import _is_quantified

    assert _is_quantified({"probability": 0.7, "impact": 0,
                           "evidence_quality": 0.3}) is False


def test_zero_probability_catalyst_is_not_quantified():
    from wbj.specialists.market import _is_quantified

    assert _is_quantified({"probability": 0.0, "impact": 1e9,
                           "evidence_quality": 0.5}) is False


def test_a_real_impact_counts_as_quantified():
    from wbj.specialists.market import _is_quantified

    assert _is_quantified({"probability": 0.8, "impact": 22_700_000_000.0,
                           "evidence_quality": 0.7}) is True


def test_a_negative_impact_still_counts():
    """A catalyst that subtracts value is quantified evidence too — the
    cap is about narrative vs measured, not good news vs bad."""
    from wbj.specialists.market import _is_quantified

    assert _is_quantified({"probability": 0.6, "impact": -5e9,
                           "evidence_quality": 0.6}) is True


# ============================================================================
# Audit fixes: anchor disclosure, reinvestment proxy, TAM CAGR window
# ============================================================================


def test_every_scored_metric_has_a_disclosed_anchor():
    """The market FORMULAS.md states no numeric band, so every scored 0-10
    scale is the module's calibration and must be an explicitly disclosed
    assumption (AGENT.md no-speculation rule). The registry must cover
    exactly the scored metrics -- the four diagnostic rows (TAM/SAM/SOM/HHI)
    and MKT-SCEN-025 carry no score and must be absent."""
    scored = {
        "MKT-CAGR-004", "MKT-PEN-005", "MKT-SHARE-006", "MKT-SHDELTA-007",
        "MKT-GCAP-009", "MKT-RUN-010", "MKT-REVBR-011", "MKT-REVMAG-012",
        "MKT-DISP-013", "MKT-SURP-014", "MKT-BACK-015", "MKT-COVER-016",
        "MKT-OPLEV-017", "MKT-INCM-018", "MKT-CAT-019", "MKT-TDEC-020",
        "MKT-ADOPT-021", "MKT-ARPU-022", "MKT-SECB-023", "MKT-RSG-024",
    }
    assert set(mkt.ANCHOR_PROVENANCE) == scored
    for mid in ("MKT-TAM-001", "MKT-SAM-002", "MKT-SOM-003", "MKT-HHI-008", "MKT-SCEN-025"):
        assert mid not in mkt.ANCHOR_PROVENANCE, mid
    out = mkt.run(_minimal_packet([_row(y) for y in range(2025, 2020, -1)]))
    blob = " ".join(out.assumptions)
    assert "Scoring anchors (partly derived)" in blob
    for mid in scored:
        assert mid in blob, f"{mid}: anchor scale not disclosed"


def test_each_anchor_provenance_entry_names_its_direction_source():
    for mid, (source, note) in mkt.ANCHOR_PROVENANCE.items():
        assert source == "MIXED", mid
        assert ".md" in note, f"{mid}: no document named for the direction"
        assert len(note) > 30, mid


def test_growth_capacity_prefers_the_validated_packet_reinvestment():
    """DATASET.md sources roic_reinvestment from a Business/Financial packet;
    an overlay value wins and carries no proxy warning."""
    out = mkt.run(
        _minimal_packet([_row(y) for y in range(2025, 2020, -1)]),
        overlay={"reinvestment_rate": 0.4, "roic": 0.25},
    )
    gcap = next(r for r in out.metrics if r.metric_id == "MKT-GCAP-009")
    assert gcap.value == pytest.approx(0.4 * 0.25)
    assert "REINVESTMENT_RATE_PROXY_CAPEX_OVER_NOPAT" not in gcap.warnings


def test_growth_capacity_flags_the_local_reinvestment_proxy():
    """Absent a validated packet, the local capex/NOPAT reinvestment rate is
    a proxy: FORMULAS.md's execution rule requires a warning, and it is
    disclosed in assumptions."""
    out = mkt.run(_minimal_packet([_row(y) for y in range(2025, 2020, -1)]))  # no overlay
    gcap = next(r for r in out.metrics if r.metric_id == "MKT-GCAP-009")
    assert "REINVESTMENT_RATE_PROXY_CAPEX_OVER_NOPAT" in gcap.warnings
    assert any("MKT-GCAP-009" in a and "proxy" in a for a in out.assumptions)


def test_tam_cagr_uses_the_registered_5y_window_not_all_history():
    """FORMULAS.md MKT-CAGR-004 frequency is '3y / 5y'. A 11-point TAM series
    growing 8%/yr must still yield a ~8% 5-year CAGR, not an 10-year one."""
    deep = [1000.0 * (1.08 ** i) for i in range(11)]      # 11 points
    shallow = [1000.0 * (1.08 ** i) for i in range(6)]    # 6 points (5y)
    out_deep = mkt.run(_minimal_packet([_row(2025)]), overlay={"tam_history": deep})
    out_shallow = mkt.run(_minimal_packet([_row(2025)]), overlay={"tam_history": shallow})
    d = next(r for r in out_deep.metrics if r.metric_id == "MKT-CAGR-004")
    s = next(r for r in out_shallow.metrics if r.metric_id == "MKT-CAGR-004")
    assert d.value == pytest.approx(0.08, abs=1e-9)
    assert d.value == pytest.approx(s.value, abs=1e-9)


def test_an_additive_saas_adapter_is_not_warned_or_penalised():
    """INDUSTRY_ADAPTERS.md (via wbj.core.adapters): SaaS is additive -- it
    changes nothing about the core formulas. The blunt `!= default` test this
    replaced mis-warned it and cut its model-fit confidence. A SaaS company
    must draw no adapter caveat and keep the default model-fit."""
    saas = mkt.run(_minimal_packet([_row(y) for y in range(2025, 2020, -1)],
                                   industry_adapter="saas_subscriptions"))
    default = mkt.run(_minimal_packet([_row(y) for y in range(2025, 2020, -1)]))
    assert not any("industry_adapter" in a for a in saas.assumptions)
    assert saas.category.confidence == pytest.approx(default.category.confidence)


def test_a_model_replacing_adapter_is_warned():
    """A bank/insurer/REIT draws the growth-capacity/ROIC caveat."""
    bank = mkt.run(_minimal_packet([_row(y) for y in range(2025, 2020, -1)],
                                   industry_adapter="banks"))
    assert any("industry_adapter" in a and "banks" in a for a in bank.assumptions)


def test_penetration_and_revision_dashboards_are_populated():
    """DECISION_RULES.md mandatory output: penetration/share and revision
    breadth/magnitude must be listed. The dedicated OUTPUT_SCHEMA fields were
    returned empty even when the metrics were computed."""
    out = mkt.run(
        _minimal_packet([_row(y) for y in range(2025, 2020, -1)]),
        overlay={
            "company_relevant_revenue": 50.0, "tam": 1000.0,
            "share": {"company_sales": 50.0, "total_market_sales": 1000.0},
            "share_history": [0.04, 0.05],
            "estimates": {"upward": 8, "total": 10, "current_consensus": 110.0,
                          "prior_consensus": 100.0,
                          "individual_estimates": [108.0, 110.0, 112.0]},
        },
    )
    assert "MKT-PEN-005" in out.penetration_and_share
    assert out.penetration_and_share["MKT-PEN-005"]["value"] == pytest.approx(0.05)
    assert "MKT-SHDELTA-007" in out.penetration_and_share
    assert "MKT-REVBR-011" in out.revision_dashboard
    assert out.revision_dashboard["MKT-REVBR-011"]["value"] == pytest.approx(0.80)


# ============================================================================
# Deep re-audit: OPLEV sign change, forecast-consistency gate part 1
# ============================================================================


def test_operating_leverage_is_not_meaningful_across_an_ebit_sign_change():
    """FORMULAS.md MKT-OPLEV-017: "Not meaningful across loss sign change;
    use incremental margin instead." A loss->profit swing (EBIT -100 -> 50)
    used to score a spurious 10/10 leverage because abs(EBIT) masked the
    crossing. It must be NOT_MEANINGFUL, while MKT-INCM-018 still computes."""
    rows = [_row(2025, ebit=50.0, revenue=1000.0), _row(2024, ebit=-100.0, revenue=800.0)]
    out = mkt.run(_minimal_packet(rows))
    op = next(r for r in out.metrics if r.metric_id == "MKT-OPLEV-017")
    inc = next(r for r in out.metrics if r.metric_id == "MKT-INCM-018")
    assert op.state == NullState.NOT_MEANINGFUL
    assert op.score == "NOT_SCORABLE"
    assert inc.value == pytest.approx(0.75)  # (50 - -100) / (1000 - 800)


def test_operating_leverage_still_computes_when_ebit_keeps_its_sign():
    """The guard must not fire on an ordinary profit->profit period."""
    rows = [_row(2025, ebit=300.0, revenue=1200.0), _row(2024, ebit=200.0, revenue=1000.0)]
    out = mkt.run(_minimal_packet(rows))
    op = next(r for r in out.metrics if r.metric_id == "MKT-OPLEV-017")
    # %oi = 100/200 = 0.5 ; %rev = 200/1000 = 0.2 ; leverage = 2.5
    assert op.value == pytest.approx(2.5)


def test_penetration_above_100pct_fails_the_consistency_gate():
    """DECISION_RULES.md gate part 1: "Company revenue <= TAM." A revenue
    mapped against a narrower TAM (penetration > 1) is the definition
    mismatch FORMULAS.md warns against and must raise the flag."""
    out = mkt.run(
        _minimal_packet([_row(2025), _row(2024)]),
        overlay={"company_relevant_revenue": 1500.0, "tam": 1000.0},  # 150% penetration
    )
    assert "CONSISTENCY_GATE_FAIL_REVENUE_EXCEEDS_TAM" in out.mandatory_flags


def test_penetration_within_tam_does_not_flag():
    out = mkt.run(
        _minimal_packet([_row(2025), _row(2024)]),
        overlay={"company_relevant_revenue": 50.0, "tam": 1000.0},  # 5%
    )
    assert "CONSISTENCY_GATE_FAIL_REVENUE_EXCEEDS_TAM" not in out.mandatory_flags


def test_runway_caps_the_target_by_tam():
    """FORMULAS.md MKT-RUN-010: "cap target by TAM." A target above the TAM is
    unreachable; runway is measured to 100% of TAM. With current=1000,
    TAM=5000, growth=50%: years = ln(5) / ln(1.5) ~= 3.97."""
    import math
    out = mkt.run(
        _minimal_packet([_row(2025), _row(2024)]),
        overlay={"tam": 5000.0, "target_revenue": 999999.0,  # absurd, > TAM
                 "current_revenue": 1000.0, "assumed_growth": 0.50},
    )
    run_row = next(r for r in out.metrics if r.metric_id == "MKT-RUN-010")
    assert run_row.value == pytest.approx(math.log(5.0) / math.log(1.5))
    assert any("cap target by TAM" in a for a in out.assumptions)


def test_runway_leaves_a_within_tam_target_uncapped():
    import math
    out = mkt.run(
        _minimal_packet([_row(2025), _row(2024)]),
        overlay={"tam": 5000.0, "target_revenue": 2000.0,  # < TAM
                 "current_revenue": 1000.0, "assumed_growth": 0.50},
    )
    run_row = next(r for r in out.metrics if r.metric_id == "MKT-RUN-010")
    assert run_row.value == pytest.approx(math.log(2.0) / math.log(1.5))
    assert not any("cap target by TAM" in a for a in out.assumptions)


def test_industry_hhi_labels_a_lower_bound_when_residual_missing():
    """FORMULAS.md MKT-HHI-008: "residual market must be represented or result
    is a lower bound." Shares summing to <1 leave a residual unrepresented."""
    partial = mkt.industry_hhi([0.3, 0.2, 0.1])  # sums to 0.6
    assert partial.value == pytest.approx(0.09 + 0.04 + 0.01)
    assert "HHI_LOWER_BOUND_RESIDUAL_MARKET_NOT_REPRESENTED" in partial.warnings
    full = mkt.industry_hhi([0.5, 0.3, 0.2])  # sums to 1.0
    assert "HHI_LOWER_BOUND_RESIDUAL_MARKET_NOT_REPRESENTED" not in full.warnings


def test_the_external_capital_flag_stays_quiet_below_the_five_point_gap():
    """The other half of DECISION_RULES.md's forecast-consistency gate:
    "Forecast growth > growth capacity by >5 pts requires external-capital
    explanation". The existing coverage only proved the flag fires; a flag
    that fires on every company says nothing.

    `_row`'s defaults put growth capacity near 8.6%, so a 10% forecast is
    inside the gate and must not raise it.
    """
    rows = [_row(2025), _row(2024)]
    out = mkt.run(
        _minimal_packet(rows),
        overlay={"target_revenue": 5000.0, "current_revenue": 1000.0,
                 "assumed_growth": 0.10},
    )
    assert "EXTERNAL_CAPITAL_REQUIRED" not in out.mandatory_flags


def test_the_gate_compares_against_growth_capacity_not_a_fixed_number():
    """The threshold is relative: the same forecast passes or fails depending
    on what the company can finance internally. Supplying a higher
    reinvestment/ROIC pair lifts capacity and should quiet the flag."""
    rows = [_row(2025), _row(2024)]
    base = {"target_revenue": 5000.0, "current_revenue": 1000.0,
            "assumed_growth": 0.30}
    loud = mkt.run(_minimal_packet(rows), overlay=base)
    quiet = mkt.run(_minimal_packet(rows),
                    overlay={**base, "reinvestment_rate": 0.9, "roic": 0.40})
    assert "EXTERNAL_CAPITAL_REQUIRED" in loud.mandatory_flags
    assert "EXTERNAL_CAPITAL_REQUIRED" not in quiet.mandatory_flags


def test_supplying_reinvestment_and_roic_removes_the_growth_capacity_proxy():
    """MKT-GCAP-009 falls back to capex/NOPAT when the validated
    Business/Financial packet figures are absent, and FORMULAS.md's execution
    rule makes that a proxy: "Record any proxy in warnings and reduce
    model-fit confidence". Supplying `reinvestment_rate` and `roic`
    (DATASET.md `roic_reinvestment`) must clear the warning."""
    rows = [_row(2025), _row(2024)]
    proxied = {r.metric_id: r for r in mkt.run(_minimal_packet(rows)).metrics}
    supplied = {r.metric_id: r for r in mkt.run(
        _minimal_packet(rows),
        overlay={"reinvestment_rate": 0.35, "roic": 0.22}).metrics}

    assert "REINVESTMENT_RATE_PROXY_CAPEX_OVER_NOPAT" in " ".join(
        proxied["MKT-GCAP-009"].warnings or [])
    assert "REINVESTMENT_RATE_PROXY_CAPEX_OVER_NOPAT" not in " ".join(
        supplied["MKT-GCAP-009"].warnings or [])
