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


#: A peer panel the way the packet builder assembles it (DATASET.md's
#: `peer_growth_margin_returns`). Eight rows because SCORING_ENGINE.md refuses a
#: peer comparison below that -- fewer would leave FIN-GR-003 MISSING, which is
#: the state the fixture is already in.
_PEER_PANEL = [{"ticker": t, "revenue_growth": g} for t, g in
               (("AMD", 0.14), ("INTC", -0.02), ("QCOM", 0.09), ("AVGO", 0.22),
                ("TXN", 0.04), ("MU", 0.17), ("ADI", 0.06), ("MRVL", 0.11))]


def _run(overlay=None, peer_panel=None):
    """The specialist over the NVDA fixture.

    `peer_panel` goes into the PACKET, not the overlay, because that is where
    FIN-GR-003 reads it (`packet.estimates["peer_panel"]`). The test that needs
    4 of 5 used to pass a `peer_revenue_growth` overlay key that nothing reads,
    so the dimension stayed at 3/5 and the test skipped itself -- it had been
    asserting nothing for as long as it existed.
    """
    packet = Packet.model_validate(json.loads(_FIXTURE.read_text()))
    if peer_panel is not None:
        packet.estimates = {**(packet.estimates or {}), "peer_panel": peer_panel}
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
    it: `share_history` through the analyst channel and a real peer panel in
    the packet.

    FIN-GR-004 stays dark on purpose -- FORMULAS.md gives it no numeric cutoff,
    so its score comes from the judge, not from here. 4 of 5 is the whole
    margin this dimension has, which is why the fifth one going missing takes
    it from 10.00/10 to nothing at all.
    """
    _, rows, dim = _run({"share_history": [0.28, 0.33, 0.396]},
                        peer_panel=_PEER_PANEL)
    assert rows["FIN-GR-003"].value is not None, "the peer panel did not land"
    assert dim.valid_weight() >= 0.8 - 1e-9, f"only {dim.valid_weight():.0%} valid"
    assert not dim.score10_value().is_null


def test_a_thin_peer_panel_is_refused_rather_than_averaged():
    """SCORING_ENGINE.md's floor of 8 valid peers.

    Seven peers is not "almost enough": comparing against a thin sample is the
    kind of number that looks the same in the report and is not. The metric is
    refused, and the refusal names the count so the report can say why.
    """
    _, rows, _ = _run({"share_history": [0.28, 0.33, 0.396]},
                      peer_panel=_PEER_PANEL[:7])
    assert rows["FIN-GR-003"].value is None
    assert any("PEER_PANEL_BELOW_8_VALID_PEERS" in w
               for w in rows["FIN-GR-003"].warnings), rows["FIN-GR-003"].warnings


def test_a_thin_panel_takes_the_dimension_dark_on_the_usual_filing():
    """The protection the peer floor exists for, on the shape real filings have.

    This case used to be asserted inside the test above as "3 of 5 must not
    score", and it stopped holding when FIN-GR-004 began reporting
    NOT_APPLICABLE instead of NOT_SCORABLE for an issuer that publishes no
    organic-growth bridge ("not having the problem cannot cost coverage").
    That was the right call for the metric and it moved something else: a
    NOT_APPLICABLE metric leaves the DENOMINATOR, so the dimension's coverage
    is now measured over the metrics that apply, not over all five.

    So the floor is unchanged at 70% and the arithmetic under it is different,
    and the honest thing is to test both shapes rather than the old count:

      · **The usual filing** — no bridge, no market-share series. Three metrics
        apply; a thin panel kills the peer one; 2 of 3 is 67% and the dimension
        goes dark. This is the case that protects almost every ticker.
      · **An analyst supplied the share series** (the test below). Four apply,
        three are valid, 75% clears the floor and the dimension scores — on
        revenue growth, growth durability and a real share trend, with the peer
        comparison openly refused. That is coverage doing its job, not evading
        it.
    """
    _, rows, dim = _run(peer_panel=_PEER_PANEL[:7])
    assert rows["FIN-GR-003"].value is None
    assert dim.applicable_weight() == pytest.approx(0.6), (
        "FIN-GR-004/005 should be OUT of the denominator on a filing that "
        "publishes neither")
    assert dim.valid_weight() / dim.applicable_weight() < 0.70
    assert dim.score10_value().is_null, "2 of 3 applicable must not score"


def test_the_thin_panel_still_scores_when_the_share_series_fills_the_gap():
    """The other side of the same arithmetic, stated out loud.

    Not an accident and not a hole: 3 of 4 applicable is 75%, above
    SCORING_ENGINE.md's 70% floor. The peer metric is still refused and still
    says so — what changed is that there is enough else that applies.
    """
    _, rows, dim = _run({"share_history": [0.28, 0.33, 0.396]},
                        peer_panel=_PEER_PANEL[:7])
    assert rows["FIN-GR-003"].value is None
    assert dim.applicable_weight() == pytest.approx(0.8)
    assert dim.valid_weight() / dim.applicable_weight() == pytest.approx(0.75)
    assert not dim.score10_value().is_null


def test_the_judgment_requests_survive_for_when_nobody_supplied_them():
    """Wiring the analyst channel must not remove the judge's route — a run
    with no overlay still has to ask."""
    out, _, _ = _run()
    asked = {r.metric_id for r in out.judgment_requests}
    assert {"FIN-GR-004", "FIN-GR-005"} <= asked
