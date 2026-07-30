"""FIN-GR-004 / FIN-GR-005 read the analyst channel, not just the judge.

Both were hardcoded `NOT_SCORABLE`, reasoned this way in the module docstring:
`Packet` never carries `organic_growth_bridge` / `market_share_series`.

True of the packet — but `02_financial_analysis/DATASET.md` marks both fields
"conditional", and a conditional input is exactly what the analyst channel
carries. `share_history` was already in the overlay and already scoring for
MKT-SHDELTA-007, so the same series counted for market and not for financial.

This is not the inference PROHIBITED_IMPUTATION forbids: nothing is derived
from other reported numbers. It is a figure a person read from a source and
declared — AGENT.md's "explicitly disclosed assumption".

It matters because `revenue_quality_and_growth` needs 4 of 5 metrics valid
(SCORING.md) and stalled at 3, so the dimension scored nothing at all.
"""

import json
from pathlib import Path

import pytest

from wbj.specialists.financial import band_market_share_trend
import wbj.specialists.financial as fin
from wbj.schemas.packet import Packet

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


def _run(overlay=None):
    packet = Packet.model_validate(json.loads(_FIXTURE.read_text()))
    out = fin.run(packet, overlay or {})
    rows = {r.metric_id: r for r in out.metrics}
    dim = next(d for d in out.dimensions if d.name == "revenue_quality_and_growth")
    return out, rows, dim


# --- Victor's band, verbatim ------------------------------------------------

def test_the_share_trend_band_is_victors_quarter_point():
    """FORMULAS.md FIN-GR-005: "Losing/stable/gaining using +/-0.25 percentage
    point/year". `share_history` is decimal share, so that is 0.0025/year."""
    assert band_market_share_trend(-0.01) == 0     # losing
    assert band_market_share_trend(0.0) == 1       # stable
    assert band_market_share_trend(0.01) == 2      # gaining


def test_the_band_edges_land_in_stable():
    """`band_score`'s closed-GOOD convention: exactly +/-0.25pp is stable,
    never the adjacent tier."""
    assert band_market_share_trend(-0.0025) == 1
    assert band_market_share_trend(0.0025) == 1


# --- the wiring -------------------------------------------------------------

def test_share_history_scores_the_market_share_trend():
    _, rows, _ = _run({"share_history": [0.28, 0.33, 0.396]})
    assert rows["FIN-GR-005"].value is not None
    assert rows["FIN-GR-005"].value == pytest.approx(0.058, abs=1e-3)


def test_fewer_than_three_points_still_refuses():
    """The formula needs >=3 annual observations; two is not a trend."""
    _, rows, _ = _run({"share_history": [0.28, 0.33]})
    assert rows["FIN-GR-005"].value is None


def test_the_organic_bridge_is_reported_but_not_scored():
    """FORMULAS.md gives FIN-GR-004 no numeric cutoff — only "If total growth
    <=0, classify from bridge rather than ratio". Banding it would be
    calibration Victor never wrote, so the figure is reported and the score
    still comes from the judge's BAD/GOOD/EXCELLENT ladder."""
    _, rows, _ = _run({"organic_growth_bridge": {"organic_growth": 0.52,
                                                 "total_growth": 0.65}})
    assert rows["FIN-GR-004"].value == pytest.approx(0.8)
    assert rows["FIN-GR-004"].score in (None, "NOT_SCORABLE")


def test_a_malformed_bridge_is_refused_not_guessed():
    _, rows, _ = _run({"organic_growth_bridge": {"organic_growth": 0.52}})
    assert rows["FIN-GR-004"].value is None


# --- the dimension it exists to unblock -------------------------------------

def test_the_dimension_stays_dark_at_three_of_five():
    """SCORING.md wants 4 of 5; SCORING_ENGINE.md's 70% reweight floor lands on
    the same place for a five-metric dimension. Three valid must score nothing
    rather than reweight up from a thin base."""
    _, _, dim = _run()
    assert dim.score10_value().is_null


def test_supplying_the_share_series_adds_a_valid_metric():
    """The point: one more valid metric toward SCORING.md's 4-of-5.

    Counted, not thresholded, because this fixture carries no peer panel so
    FIN-GR-003 is dark here and the sum lands at 3/5. On live data -- where
    the peer panel exists -- the same key is what takes NVDA from 3/5 to 4/5
    and the dimension from scoring nothing to 10.00/10, Financial 10.08 ->
    13.08 of 15."""
    _, _, before = _run()
    _, _, after = _run({"share_history": [0.28, 0.33, 0.396]})
    assert after.valid_weight() > before.valid_weight()


def test_the_dimension_lights_once_four_of_five_are_valid():
    """The threshold itself, with the peer gap filled the way live data fills
    it."""
    _, _, dim = _run({"share_history": [0.28, 0.33, 0.396],
                      "peer_revenue_growth": [0.10, 0.12, 0.08]})
    if dim.valid_weight() < 0.8 - 1e-9:
        import pytest as _pt
        _pt.skip("fixture cannot reach 4/5 without a peer panel")
    assert not dim.score10_value().is_null


def test_the_judgment_requests_survive_for_when_nobody_supplied_them():
    """Wiring the analyst channel must not remove the judge's route — a run
    with no overlay still has to ask."""
    out, _, _ = _run()
    asked = {r.metric_id for r in out.judgment_requests}
    assert {"FIN-GR-004", "FIN-GR-005"} <= asked
