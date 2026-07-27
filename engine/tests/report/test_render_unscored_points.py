"""The scorecard must separate "measured and weak" from "never measurable".

SCORING_ENGINE.md reweights "only within a dimension": a dimension whose
valid metric weight falls below 70% is NOT_SCORABLE and earns none of its
points, with no rescaling at the category level. So a category score can
collapse without anything having been measured and found wanting -- NVDA's
market category reads 5.2/20 with 13 of those points behind dimensions that
were never assessable at all. The reader has to be able to see that.
"""

from __future__ import annotations

from tests.aggregate.test_final_report import _aggregate_inputs, _executive_thesis
from wbj.aggregate.gates import (CategoryConfidences, CategoryPoints, apply_gates,
                                 raw_total)
from wbj.aggregate.overrides import apply_overrides
from wbj.aggregate.synthesis import synthesize_levels
from wbj.report.render import render_markdown
from wbj.schemas.final_report import build_final_report


def _report():
    inputs = _aggregate_inputs()
    cats = CategoryPoints(business=16.0, financial=10.5, market=18.0,
                          technical=16.0, risk=9.0, valuation=7.0)
    confs = CategoryConfidences(business=90, financial=90, market=90,
                                technical=90, risk=90, valuation=90)
    profile = apply_gates(raw_total(cats), cats, confs, apply_overrides(inputs))
    levels = synthesize_levels(inputs.technical, inputs.valuation, price=100.0, atr=2.0)
    return build_final_report(
        inputs=inputs, profile=profile, contradictions=[], levels=levels,
        executive_thesis=_executive_thesis(), exchange="NASDAQ", currency="USD",
        analysis_timestamp="2026-07-17T12:00:00+00:00",
    )


def test_the_scorecard_reports_points_that_were_never_assessable():
    md = render_markdown(
        _report(), "en",
        coverage={"market": 0.49},
        unscored_points={"market": 13.0},
    )
    assert "Not assessable" in md
    assert "13.0 / 20" in md
    assert "not a judgment against the company" in md


def test_a_fully_assessed_category_shows_a_dash():
    md = render_markdown(_report(), "en", unscored_points={"market": 0.0})
    assert "Not assessable" in md
    # No category has unassessable points, so every row carries the dash.
    assert "13.0 / 20" not in md


def test_the_spanish_label_is_translated():
    md = render_markdown(_report(), "es", unscored_points={"market": 13.0})
    assert "No evaluable" in md
    assert "no un juicio en contra de la empresa" in md


# ============================================================================
# CLAUDE.md memory protocol step 2: declare a material track-record bias
# ============================================================================


def test_a_material_bias_is_declared_in_the_report():
    """"Lee Memoria/calibracion.md: si reporta sesgo medio > +/-10%,
    decláralo en el reporte y ajusta la confianza de los targets a la baja."
    Nothing read it, so a measured bias never reached a reader."""
    md = render_markdown(_report(), "en",
                         calibration={"sesgo_medio": -0.18, "total": 25, "material": True})
    assert "Track-record bias" in md
    assert "-18.0%" in md
    assert "25 recorded predictions" in md
    assert "optimistic" in md          # negative bias = targets ran ahead of reality
    assert "lower confidence" in md


def test_an_immaterial_bias_is_not_declared():
    """Below +/-10%, or under 10 predictions, the bias is noise -- declaring
    it would be worse than silence."""
    for calib in ({"sesgo_medio": -0.02, "total": 34, "material": False},
                  {"sesgo_medio": -0.30, "total": 3, "material": False},
                  None):
        md = render_markdown(_report(), "en", calibration=calib)
        assert "Track-record bias" not in md


def test_the_bias_direction_reads_correctly_in_spanish():
    md = render_markdown(_report(), "es",
                         calibration={"sesgo_medio": 0.15, "total": 20, "material": True})
    assert "Sesgo del track record" in md
    assert "conservador" in md         # positive bias = reality ran ahead of base
