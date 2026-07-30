"""Tests for `wbj.schemas.final_report` (Task 21): the `FinalReport`
pydantic schema and `build_final_report`.

Source of truth: `Cerebro/00_main_agent/FINAL_REPORT_SCHEMA.md`.
"""

from __future__ import annotations

import pytest

from wbj.aggregate.contradiction import CategoryScore10s, contradictions
from wbj.aggregate.gates import CategoryConfidences, CategoryPoints, apply_gates, raw_total
from wbj.aggregate.overrides import AggregateInputs, apply_overrides
from wbj.aggregate.synthesis import synthesize_levels
from wbj.schemas.final_report import REPORT_VERSION, ExecutiveThesis, FinalReport, build_final_report

from .conftest import (
    make_business,
    make_financial,
    make_market,
    make_risk,
    make_technical,
    make_valuation,
)


def _executive_thesis() -> ExecutiveThesis:
    return ExecutiveThesis(
        business_quality="The company sells enterprise software on a subscription basis.",
        value_creation_durability="Returns on invested capital have stayed above the cost of capital for five years.",
        growth_engine="Growth is funded from operating cash flow, not external capital.",
        market_validation="Relative strength versus the sector has been positive for two quarters.",
        valuation_message="The current price requires high-teens revenue growth to sustain.",
        key_levels_summary="Nearest support and resistance zones bracket the current price within one ATR.",
        primary_risk="A slowdown in enterprise IT spending would compress the growth assumption fastest.",
    )


def _aggregate_inputs() -> AggregateInputs:
    return AggregateInputs(
        business=make_business(points=16.0, three_thesis_killers=["Customer concentration above 30%"]),
        financial=make_financial(points=10.5),
        market=make_market(points=18.0, three_growth_thesis_killers=["TAM estimate unverified"]),
        technical=make_technical(points=16.0),
        risk=make_risk(points=9.0, thesis_killers=[{"description": "Covenant breach risk within 12 months"}]),
        valuation=make_valuation(points=7.0),
    )


def test_final_report_round_trips_through_build_final_report():
    inputs = _aggregate_inputs()
    overrides = apply_overrides(inputs)
    cats = CategoryPoints(business=16.0, financial=10.5, market=18.0, technical=16.0, risk=9.0, valuation=7.0)
    confidences = CategoryConfidences(business=90, financial=90, market=90, technical=90, risk=90, valuation=90)
    raw = raw_total(cats)
    profile = apply_gates(raw, cats, confidences, overrides)

    score10s = CategoryScore10s(
        business=cats.business / 20 * 10, financial=cats.financial / 15 * 10, market=cats.market / 20 * 10,
        technical=cats.technical / 20 * 10, risk=cats.risk / 15 * 10, valuation=cats.valuation / 10 * 10,
    )
    contras = contradictions(score10s, raw)

    levels = synthesize_levels(inputs.technical, inputs.valuation, price=100.0, atr=2.0)

    report = build_final_report(
        inputs=inputs,
        profile=profile,
        contradictions=contras,
        levels=levels,
        executive_thesis=_executive_thesis(),
        exchange="NASDAQ",
        currency="USD",
        analysis_timestamp="2026-07-17T12:00:00+00:00",
        packet_hashes={"packet": "abc123"},
        formula_versions=["2.0.0"],
    )

    assert isinstance(report, FinalReport)
    assert report.report_version == REPORT_VERSION
    assert report.security.ticker == "TEST"
    assert report.profile.raw_score == raw
    assert report.category_scorecard.business.points == 16.0
    assert report.category_scorecard.business.max == 20.0
    assert len(report.executive_thesis.as_sentences()) == 7
    assert "Customer concentration above 30%" in report.thesis_killers
    assert "TAM estimate unverified" in report.thesis_killers
    assert "Covenant breach risk within 12 months" in report.thesis_killers
    assert report.audit.packet_hashes == {"packet": "abc123"}
    assert report.audit.formula_versions == ["2.0.0"]
    assert len(report.important_levels) == len(levels.levels)


def test_final_report_rejects_wrong_report_version():
    with pytest.raises(Exception):
        FinalReport(
            report_version="1.0.0",
            security=dict(
                ticker="X", exchange="NASDAQ", currency="USD",
                analysis_timestamp="2026-01-01T00:00:00+00:00", knowledge_timestamp="2026-01-01T00:00:00+00:00",
            ),
            profile=dict(label="Speculative", raw_score=50.0, total_confidence=50.0),
            category_scorecard=dict(
                business=dict(points=10, max=20, confidence=50),
                financial=dict(points=7, max=15, confidence=50),
                market=dict(points=10, max=20, confidence=50),
                technical=dict(points=10, max=20, confidence=50),
                risk=dict(points=7, max=15, confidence=50),
                valuation=dict(points=5, max=10, confidence=50),
            ),
            executive_thesis=_executive_thesis(),
        )


def test_executive_thesis_requires_all_seven_sentences():
    with pytest.raises(Exception):
        ExecutiveThesis(business_quality="x")  # missing the other 6 required fields


# ============================================================================
# Audit fix: ORCHESTRATION.md Phase 3 -- hash each specialist packet
# ============================================================================


def _build(inputs, **overrides_kw):
    """The same assembly the round-trip test does, minus the hash argument."""
    overrides = apply_overrides(inputs)
    cats = CategoryPoints(business=16.0, financial=10.5, market=18.0, technical=16.0, risk=9.0, valuation=7.0)
    confidences = CategoryConfidences(business=90, financial=90, market=90, technical=90, risk=90, valuation=90)
    raw = raw_total(cats)
    profile = apply_gates(raw, cats, confidences, overrides)
    levels = synthesize_levels(inputs.technical, inputs.valuation, price=100.0, atr=2.0)
    return build_final_report(
        inputs=inputs, profile=profile, contradictions=[], levels=levels,
        executive_thesis=_executive_thesis(), exchange="NASDAQ", currency="USD",
        analysis_timestamp="2026-07-17T12:00:00+00:00", **overrides_kw,
    )


def test_packet_hashes_are_computed_when_the_caller_supplies_none():
    """ORCHESTRATION.md Phase 3: "Hash each specialist packet. Any later
    correction creates a new packet version and invalidates the prior
    main-agent calculation." The audit block came back empty unless a caller
    happened to pass hashes -- and the report pipeline never did, so the
    freeze guarantee had no artifact."""
    from wbj.schemas.final_report import specialist_packet_hashes

    inputs = _aggregate_inputs()
    report = _build(inputs)
    assert set(report.audit.packet_hashes) == {
        "business", "financial", "market", "technical", "risk", "valuation"
    }
    for name, digest in report.audit.packet_hashes.items():
        assert len(digest) == 64, name          # sha256 hex
        int(digest, 16)                         # valid hex
    # Deterministic across rebuilds from identical packets.
    assert specialist_packet_hashes(inputs) == report.audit.packet_hashes


def test_a_changed_packet_changes_its_hash():
    """The point of Phase 3: a corrected packet must not reuse the prior
    main-agent calculation's hash."""
    from wbj.schemas.final_report import specialist_packet_hash

    inputs = _aggregate_inputs()
    before = specialist_packet_hash(inputs.business)
    mutated = inputs.business.model_copy(update={"verdict": "changed verdict"})
    assert specialist_packet_hash(mutated) != before


def test_an_explicit_packet_hashes_argument_still_wins():
    report = _build(_aggregate_inputs(), packet_hashes={"business": "deadbeef"})
    assert report.audit.packet_hashes == {"business": "deadbeef"}


def test_formula_versions_are_collected_when_the_caller_supplies_none():
    """ORCHESTRATION.md Phase 8 requires a "formula and source audit" and
    FINAL_REPORT_SCHEMA.md carries `audit.formula_versions`. It came back
    empty for every real report (the renderer printed a bare em-dash), even
    though every MetricRow already records its formula version."""
    from wbj.core.nullstates import NullState
    from wbj.schemas.final_report import collected_formula_versions
    from wbj.specialists.common import MetricRow

    def _row(metric_id: str, version: str) -> MetricRow:
        return MetricRow(
            metric_id=metric_id, state=NullState.MISSING, unit="pct", period="FY2025",
            source="test", formula_id=metric_id, formula_version=version,
            score="NOT_SCORABLE", confidence=0.0,
        )

    base = _aggregate_inputs()
    inputs = AggregateInputs(
        business=base.business.model_copy(update={"metrics": [_row("BUS-ROIC-013", "2026.1")]}),
        financial=base.financial.model_copy(update={"metrics": [_row("FIN-EF-024", "2.0.0")]}),
        market=base.market, technical=base.technical, risk=base.risk,
        valuation=base.valuation,
    )
    report = _build(inputs)
    assert report.audit.formula_versions == ["2.0.0", "2026.1"]  # sorted, distinct
    assert report.audit.formula_versions == collected_formula_versions(inputs)


def test_an_explicit_formula_versions_argument_still_wins():
    report = _build(_aggregate_inputs(), formula_versions=["9.9.9"])
    assert report.audit.formula_versions == ["9.9.9"]
