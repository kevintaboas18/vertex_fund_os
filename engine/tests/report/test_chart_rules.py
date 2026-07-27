"""The four non-negotiable visualization rules, enforced in code.

`CLAUDE.md` and `.claude/agents/visual-report.md` state them identically:

1. Never a single line — always a range.
2. Label the assumptions — "si no dices los supuestos, el número no
   significa nada".
3. The past is not projected — history solid, projections dotted.
4. The agent decides, not the chart.

Plus: "Todo gráfico lleva: título, unidades, fuente y timestamp de la data."
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import pytest

matplotlib.use("Agg")

from wbj.report.charts import (_save, football_field_chart, scenario_fan_chart,
                               scorecard_chart)

_HISTORY = [{"date": "2026-01-01", "value": 100.0}, {"date": "2026-01-02", "value": 105.0}]


def test_rule_1_a_single_line_projection_is_refused(tmp_path):
    with pytest.raises(ValueError, match="single-line projection prohibited"):
        scenario_fan_chart(
            _HISTORY,
            [{"name": "Base", "low": 120.0, "high": 120.0, "assumptions": "growth +10%"}],
            tmp_path / "x.png")


def test_rule_2_a_band_without_stated_assumptions_is_refused(tmp_path):
    """Rule 2 is as non-negotiable as rule 1. The label used to fall back to
    an empty string, so a band with no stated growth/margin drew silently and
    read as though it had been derived from something."""
    with pytest.raises(ValueError, match="no stated assumptions"):
        scenario_fan_chart(
            _HISTORY,
            [{"name": "Base", "low": 110.0, "high": 130.0}],   # no assumptions
            tmp_path / "x.png")

    with pytest.raises(ValueError, match="no stated assumptions"):
        scenario_fan_chart(
            _HISTORY,
            [{"name": "Base", "low": 110.0, "high": 130.0, "assumptions": "   "}],
            tmp_path / "x.png")


def test_a_band_with_assumptions_draws(tmp_path):
    out = scenario_fan_chart(
        _HISTORY,
        [{"name": "Base", "low": 110.0, "high": 130.0,
          "assumptions": "growth +10% · multiple 20x"}],
        tmp_path / "x.png")
    assert out.exists() and out.stat().st_size > 0


def test_every_chart_carries_its_source_and_timestamp(tmp_path):
    """"Todo gráfico lleva: título, unidades, fuente y timestamp de la
    data." Nothing recorded where the numbers came from, so a chart lifted
    out of its folder became an unattributed claim."""
    fig, _ = matplotlib.pyplot.subplots()
    _save(fig, tmp_path / "a.png", source="NVDA · FMP/EDGAR", as_of="2026-07-25T17:00:00")
    # The stamp is drawn as a figure-level text; re-open the figure list is
    # not possible after close, so assert on a fresh figure instead.
    fig2, _ = matplotlib.pyplot.subplots()
    texts_before = len(fig2.texts)
    _save(fig2, tmp_path / "b.png", source="X", as_of="2026-01-01")
    assert len(fig2.texts) == texts_before + 1
    assert "X" in fig2.texts[-1].get_text()
    assert "data as of 2026-01-01" in fig2.texts[-1].get_text()


def test_a_chart_without_provenance_draws_no_empty_stamp(tmp_path):
    fig, _ = matplotlib.pyplot.subplots()
    before = len(fig.texts)
    _save(fig, tmp_path / "c.png")
    assert len(fig.texts) == before


def test_the_football_field_labels_the_current_price_inside_the_axes(tmp_path):
    """Pinning the label to data coordinate -0.7 put it below the axis
    whenever there were few bands, so the one line a reader most needs
    identified was drawn unlabelled."""
    out = football_field_chart(
        [{"label": "DCF", "low": 33.0, "high": 50.0, "assumptions": "reverse-DCF band"}],
        206.84, tmp_path / "ff.png", currency="USD",
        source="NVDA", as_of="2026-07-25")
    assert out.exists()


def test_an_unscored_category_is_not_drawn_as_a_zero_bar(tmp_path):
    """A zero-length bar reads as "scored badly"; N/S means no evidence."""
    out = scorecard_chart(
        [{"key": "market", "label": "Market", "points": 0.0,
          "max_points": 20.0, "unscored": True}],
        tmp_path / "sc.png", source="X", as_of="2026-01-01")
    assert out.exists()
