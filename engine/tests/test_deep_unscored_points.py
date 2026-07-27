"""A low strict score must be separable from an unmeasured one.

SCORING_ENGINE.md reweights "only within a dimension": a dimension below
70% valid metric weight is NOT_SCORABLE and earns none of its points, with
no rescaling at the category level. So a category score can collapse
without anything having been measured and found wanting -- NVDA's market
category reads 1.1/10 while the single dimension that could be scored came
in at 7.2. `_unscored_dimension_points` is what lets the display say which
is which.
"""

from __future__ import annotations

from pathlib import Path

from wbj.deep import _unscored_dimension_points, is_unscored
from wbj.schemas.packet import Packet
from wbj.specialists import market

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


def _packet() -> Packet:
    return Packet.model_validate_json(_FIXTURE.read_text(encoding="utf-8"))


def test_unscored_dimension_points_counts_the_unmeasurable_maximum():
    out = market.run(_packet(), {})
    unscorable = [d for d in out.dimensions if d.score10_value().is_null]
    expected = round(sum(d.max_points for d in unscorable), 2)
    assert _unscored_dimension_points(out) == expected
    # The fixture carries no market overlay, so most of the category is dark.
    assert expected > 0
    assert expected < out.category.max_points  # but not all of it


def test_a_partly_scored_category_is_not_reported_as_fully_unscored():
    """`is_unscored` is all-or-nothing; the points figure is what carries
    the partial case. Reading a partly-dark category as fully N/S would
    hide the dimension that did score."""
    out = market.run(_packet(), {})
    assert is_unscored(out) is False
    assert _unscored_dimension_points(out) > 0


def test_a_fully_scored_category_has_no_unassessable_points():
    from wbj.core.scoring import Dimension
    from wbj.core.nullstates import Value

    class _Fake:
        dimensions = [
            Dimension(name="d", max_points=5.0,
                      metric_scores=[(1.0, Value.of(8.0, unit="score"))]),
        ]

    assert _unscored_dimension_points(_Fake()) == 0.0
