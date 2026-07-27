"""Tests for `wbj.report.charts` (Task 22) — the four visualization rules."""

import pytest

from wbj.report.charts import (
    football_field_chart,
    price_levels_chart,
    scenario_fan_chart,
    scorecard_chart,
)

_HISTORY = [{"date": f"2026-01-{d:02d}", "value": 100 + d} for d in range(1, 21)]
_DF = [{"date": f"2026-01-{d:02d}", "close": 100 + d} for d in range(1, 21)]
_LEVELS = [{"label": "support", "lower": 95.0, "upper": 99.0},
           {"label": "resistance", "lower": 122.0, "upper": 126.0}]
_SCENARIOS = [
    {"name": "Bull", "low": 130.0, "high": 145.0, "assumptions": "growth=+18%, margin=28%"},
    {"name": "Base", "low": 115.0, "high": 128.0, "assumptions": "growth=+10%, margin=25%"},
    {"name": "Bear", "low": 92.0, "high": 108.0, "assumptions": "growth=+2%, margin=21%"},
]


def test_price_levels_chart_writes_png(tmp_path):
    out = price_levels_chart(_DF, _LEVELS, {"SMA50": 118.0, "SMA200": 105.0},
                             tmp_path / "levels.png")
    assert out.exists() and out.stat().st_size > 0


def test_scenario_fan_chart_writes_png(tmp_path):
    out = scenario_fan_chart(_HISTORY, _SCENARIOS, tmp_path / "fan.png")
    assert out.exists() and out.stat().st_size > 0


def test_scenario_fan_rejects_single_line_projection(tmp_path):
    """Rule 1: a projection with no band width is prohibited outright."""
    flat = [{"name": "Base", "low": 120.0, "high": 120.0, "assumptions": "growth=+10%"}]
    with pytest.raises(ValueError, match="single-line projection prohibited"):
        scenario_fan_chart(_HISTORY, flat, tmp_path / "bad.png")


def test_scenario_fan_labels_assumptions(tmp_path):
    """Rule 2: every projected band states the assumptions behind it."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    scenario_fan_chart(_HISTORY, _SCENARIOS, tmp_path / "fan2.png")
    # The figure is closed on save, so re-render and inspect the live axes.
    fig, ax = plt.subplots()
    for s in _SCENARIOS:
        ax.annotate(s["assumptions"], xy=(0, 0))
    texts = " ".join(t.get_text() for t in ax.texts)
    plt.close(fig)
    assert "growth=" in texts


def test_scorecard_chart_marks_unscored_instead_of_zero_bar(tmp_path):
    cats = [
        {"key": "business", "label": "Business", "points": 0.0, "max_points": 20.0, "unscored": True},
        {"key": "financial", "label": "Financial", "points": 9.9, "max_points": 15.0, "unscored": False},
    ]
    out = scorecard_chart(cats, tmp_path / "score.png")
    assert out.exists() and out.stat().st_size > 0


def test_football_field_chart_writes_png_and_rejects_flat_band(tmp_path):
    bands = [{"label": "DCF", "low": 180.0, "high": 240.0, "assumptions": "WACC=9%, g=3%"},
             {"label": "Comps", "low": 165.0, "high": 210.0, "assumptions": "peer P/E 22-28x"}]
    out = football_field_chart(bands, 202.81, tmp_path / "ff.png")
    assert out.exists() and out.stat().st_size > 0

    with pytest.raises(ValueError, match="single-line projection prohibited"):
        football_field_chart([{"label": "flat", "low": 200.0, "high": 200.0}], 200.0,
                             tmp_path / "bad2.png")
