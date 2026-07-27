"""Tests for `wbj.schemas.final_report` (Task 21): the `FinalReport`
pydantic schema and `build_final_report`.

Source of truth: `Cerebro/00_main_agent/FINAL_REPORT_SCHEMA.md`.
"""

from __future__ import annotations

import pytest

from wbj.aggregate.contradiction import CategoryScore10s, contradictions
from wbj.aggregate.gates import CategoryConfidences, CategoryPoints, apply_gates, raw_total
from wbj.aggregate.overrides import AggregateInputs, apply_overrides
from wbj.aggregate.synthesis import LevelReference, LevelSynthesis, synthesize_levels
from wbj.core.nullstates import NullState, Value
from wbj.core.scoring import Dimension
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


# ---------------------------------------------------------------------------
# monitoring_triggers / missing_or_conflicted_data
#
# `missing_or_conflicted_data` llevaba solo los warnings del perfil y los
# mandatory flags: la mitad "missing" faltaba entera. Una categoria podia
# entregar tres dimensiones NOT_SCORABLE y 33% de cobertura y el reporte no
# decia nada — y una dimension no puntuable aporta CERO puntos, o sea que en el
# total se lee igual que una que puntuo mal.
#
# `monitoring_triggers` era `[c.label for c in contradictions]`: sin
# contradicciones el reporte publicaba una seccion vacia, que se lee como "nada
# que vigilar" para una tesis que siempre tiene algo que vigilar.
# ---------------------------------------------------------------------------

def _report_with(inputs, *, contras=None, levels=None):
    overrides = apply_overrides(inputs)
    cats = CategoryPoints(business=16.0, financial=10.5, market=18.0, technical=16.0, risk=9.0, valuation=7.0)
    confidences = CategoryConfidences(business=90, financial=90, market=90, technical=90, risk=90, valuation=90)
    profile = apply_gates(raw_total(cats), cats, confidences, overrides)
    return build_final_report(
        inputs=inputs, profile=profile, contradictions=contras or [],
        levels=levels if levels is not None else synthesize_levels(
            inputs.technical, inputs.valuation, price=100.0, atr=2.0),
        executive_thesis=_executive_thesis(), exchange="NASDAQ", currency="USD",
        analysis_timestamp="2026-07-26T00:00:00+00:00",
    )


def _unscorable_dimension(name: str) -> Dimension:
    return Dimension(
        name=name, max_points=5.0,
        metric_scores=[(1.0, Value.null(NullState.NOT_SCORABLE, unit="score"))],
    )


def test_missing_data_names_the_unscorable_dimensions():
    inputs = AggregateInputs(
        business=make_business(dimensions=[_unscorable_dimension("customer_economics")], coverage=0.40),
        financial=make_financial(), market=make_market(), technical=make_technical(),
        risk=make_risk(), valuation=make_valuation(),
    )
    entries = _report_with(inputs).missing_or_conflicted_data
    assert any("customer_economics" in e for e in entries)
    assert any("NOT_SCORABLE aporta 0 puntos" in e for e in entries)


def test_missing_data_reports_coverage_below_the_policy_bands():
    inputs = AggregateInputs(
        business=make_business(coverage=0.40), financial=make_financial(coverage=0.80),
        market=make_market(), technical=make_technical(), risk=make_risk(), valuation=make_valuation(),
    )
    entries = _report_with(inputs).missing_or_conflicted_data
    assert any("business: cobertura 40%" in e and "no elegible para gates" in e for e in entries)
    assert any("financial: cobertura 80%" in e and "usable con salvedad" in e for e in entries)
    # 0.90 esta por encima de COVERAGE_COMPLETE: no se reporta.
    assert not any(e.startswith("market: cobertura") for e in entries)


def test_missing_data_still_carries_the_mandatory_flags():
    inputs = AggregateInputs(
        business=make_business(mandatory_flags=["VALUE_DESTRUCTION"]), financial=make_financial(),
        market=make_market(), technical=make_technical(), risk=make_risk(), valuation=make_valuation(),
    )
    entries = _report_with(inputs).missing_or_conflicted_data
    assert "business: VALUE_DESTRUCTION" in entries


def test_monitoring_triggers_are_never_empty():
    """Siempre hay algo que vigilar: el re-run de CLAUDE.md es el piso."""
    triggers = _report_with(_aggregate_inputs()).monitoring_triggers
    assert triggers
    assert any("10-K/10-Q" in t and "recalcular" in t for t in triggers)


def test_monitoring_triggers_carry_the_level_confirmation_and_invalidation():
    """PRICE_LEVEL_SYNTHESIS.md #4: el trigger de ruptura y el nivel de fallo."""
    level = LevelReference(
        level_class="resistance", label="Resistencia cercana", source="technical",
        zone_low=104.0, zone_high=106.0,
        confirmation="cierre diario sobre 106.00", invalidation="cierre diario bajo 98.00",
    )
    levels = LevelSynthesis(current_price=100.0, atr14=2.0, levels=[level])
    triggers = _report_with(_aggregate_inputs(), levels=levels).monitoring_triggers
    assert any("confirmación — cierre diario sobre 106.00" in t for t in triggers)
    assert any("invalidación — cierre diario bajo 98.00" in t for t in triggers)


def test_monitoring_triggers_and_missing_data_do_not_repeat_entries():
    inputs = AggregateInputs(
        business=make_business(mandatory_flags=["VALUE_DESTRUCTION", "VALUE_DESTRUCTION"]),
        financial=make_financial(), market=make_market(), technical=make_technical(),
        risk=make_risk(), valuation=make_valuation(),
    )
    report = _report_with(inputs)
    assert len(report.missing_or_conflicted_data) == len(set(report.missing_or_conflicted_data))
    assert len(report.monitoring_triggers) == len(set(report.monitoring_triggers))


def test_mandatory_flags_are_not_repeated_across_both_sections():
    """Un flag es estado, no evento: va en missing_or_conflicted_data y solo ahi."""
    inputs = AggregateInputs(
        business=make_business(mandatory_flags=["VALUE_DESTRUCTION"]), financial=make_financial(),
        market=make_market(), technical=make_technical(), risk=make_risk(), valuation=make_valuation(),
    )
    report = _report_with(inputs)
    assert "business: VALUE_DESTRUCTION" in report.missing_or_conflicted_data
    assert not any("VALUE_DESTRUCTION" in t for t in report.monitoring_triggers)
