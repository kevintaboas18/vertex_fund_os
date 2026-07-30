import pytest

from wbj.core.scoring import (
    CATEGORY_WEIGHTS,
    COVERAGE_COMPLETE,
    COVERAGE_USABLE,
    Category,
    Dimension,
    anchor_score,
    hybrid_score,
    peer_score,
)
from wbj.core.nullstates import NullState, Value

ANCHORS = [(0.0, 0), (0.05, 3), (0.10, 5), (0.15, 7), (0.25, 10)]


# --- brief Step 1 tests (verbatim) ---


def test_anchor_interpolation():
    assert anchor_score(0.10, ANCHORS) == 5
    assert abs(anchor_score(0.125, ANCHORS) - 6.0) < 1e-9  # halfway 5->7
    assert anchor_score(-0.50, ANCHORS) == 0  # clamped
    assert anchor_score(0.90, ANCHORS) == 10  # clamped


def test_peer_score_needs_8_peers():
    assert peer_score(5.0, [1, 2, 3]).is_null


def test_dimension_reweights_only_above_70pct_valid():
    d = Dimension(
        name="x",
        max_points=5,
        metric_scores=[
            (0.5, Value.of(8, unit="score")),
            (0.3, Value.of(6, unit="score")),
            (0.2, Value.null(NullState.MISSING)),
        ],
    )  # 80% valid weight -> reweight
    assert abs(d.score10() - (0.5 * 8 + 0.3 * 6) / 0.8) < 1e-9


def test_dimension_not_scorable_below_70pct():
    d = Dimension(
        name="x",
        max_points=5,
        metric_scores=[
            (0.5, Value.null(NullState.MISSING)),
            (0.5, Value.of(9, unit="score")),
        ],
    )
    assert d.score10_value().is_null  # 50% < 70%


def test_category_points_math():
    # MAIN-002 spirit: dimension points = max * score/10
    d1 = Dimension(name="a", max_points=5, metric_scores=[(1.0, Value.of(8, unit="score"))])
    d2 = Dimension(name="b", max_points=4, metric_scores=[(1.0, Value.of(5, unit="score"))])
    c = Category(name="business", max_points=20, dimensions=[d1, d2])
    assert abs(c.points() - (5 * 0.8 + 4 * 0.5)) < 1e-9  # 4.0 + 2.0 = 6.0


# --- supplementary coverage ---


def test_anchor_score_exact_anchor_points():
    assert anchor_score(0.0, ANCHORS) == 0
    assert anchor_score(0.05, ANCHORS) == 3
    assert anchor_score(0.25, ANCHORS) == 10


def test_anchor_score_unsorted_anchors_still_interpolate():
    shuffled = [(0.25, 10), (0.0, 0), (0.15, 7), (0.05, 3), (0.10, 5)]
    assert anchor_score(0.10, shuffled) == 5


def test_peer_score_higher_is_better_percentile():
    peers = [1, 2, 3, 4, 5, 6, 7, 8]
    v = peer_score(8.0, peers, higher_is_better=True)
    assert v.is_valid
    assert v.value > 5.0  # 8 is at/near the top of its own peer set


def test_peer_score_lower_is_better_inverts_rank():
    peers = [1, 2, 3, 4, 5, 6, 7, 8]
    high = peer_score(8.0, peers, higher_is_better=True)
    low = peer_score(8.0, peers, higher_is_better=False)
    assert abs((high.value + low.value) - 10.0) < 1e-9


def test_peer_score_not_scorable_carries_warning():
    v = peer_score(5.0, [1, 2, 3])
    assert v.is_null and v.state == NullState.NOT_SCORABLE
    assert v.warnings, "expected a warning explaining insufficient peers"


def test_hybrid_score_weighted_combination():
    assert abs(hybrid_score(8.0, 4.0, 0.6, 0.4) - (0.6 * 8.0 + 0.4 * 4.0)) < 1e-9


def test_hybrid_score_requires_weights_sum_to_one():
    with pytest.raises(AssertionError):
        hybrid_score(8.0, 4.0, 0.5, 0.6)


def test_dimension_score10_raises_when_not_scorable():
    d = Dimension(
        name="x",
        max_points=5,
        metric_scores=[
            (0.5, Value.null(NullState.MISSING)),
            (0.5, Value.of(9, unit="score")),
        ],
    )
    with pytest.raises(ValueError):
        d.score10()


def test_dimension_fully_valid_no_reweight_needed():
    d = Dimension(
        name="x",
        max_points=5,
        metric_scores=[(0.5, Value.of(4, unit="score")), (0.5, Value.of(6, unit="score"))],
    )
    assert abs(d.score10() - 5.0) < 1e-9


def test_category_treats_not_scorable_dimension_as_zero_points():
    scorable = Dimension(name="a", max_points=5, metric_scores=[(1.0, Value.of(8, unit="score"))])
    not_scorable = Dimension(
        name="b",
        max_points=4,
        metric_scores=[
            (0.5, Value.null(NullState.MISSING)),
            (0.5, Value.of(9, unit="score")),
        ],
    )
    c = Category(name="business", max_points=20, dimensions=[scorable, not_scorable])
    assert abs(c.points() - 4.0) < 1e-9  # only the scorable dimension contributes


def test_category_coverage_reflects_invalid_weight():
    d1 = Dimension(name="a", max_points=5, metric_scores=[(1.0, Value.of(8, unit="score"))])
    d2 = Dimension(
        name="b",
        max_points=5,
        metric_scores=[
            (0.5, Value.null(NullState.MISSING)),
            (0.5, Value.of(9, unit="score")),
        ],
    )
    c = Category(name="business", max_points=20, dimensions=[d1, d2])
    # valid weight: 5*1.0 + 5*0.5 = 7.5 ; applicable weight: 5*1.0 + 5*1.0 = 10
    assert abs(c.coverage() - 0.75) < 1e-9
    assert c.points() == 4.0  # d2 is NOT_SCORABLE (50% < 70%) -> contributes 0


def test_category_score10():
    d1 = Dimension(name="a", max_points=5, metric_scores=[(1.0, Value.of(8, unit="score"))])
    d2 = Dimension(name="b", max_points=5, metric_scores=[(1.0, Value.of(4, unit="score"))])
    c = Category(name="business", max_points=20, dimensions=[d1, d2])
    # points = 5*0.8 + 5*0.4 = 4 + 2 = 6 ; score10 = 10*6/20 = 3.0
    assert abs(c.score10() - 3.0) < 1e-9


def test_category_complete_uses_085_threshold():
    # fully valid dimensions -> coverage 1.0 -> complete
    d1 = Dimension(name="a", max_points=10, metric_scores=[(1.0, Value.of(8, unit="score"))])
    c_complete = Category(name="business", max_points=10, dimensions=[d1])
    assert c_complete.coverage() == 1.0
    assert c_complete.complete is True

    # coverage between USABLE and COMPLETE -> not complete
    d2 = Dimension(
        name="b",
        max_points=10,
        metric_scores=[
            (0.80, Value.of(8, unit="score")),
            (0.20, Value.null(NullState.MISSING)),
        ],
    )
    c_usable = Category(name="business", max_points=10, dimensions=[d2])
    assert COVERAGE_USABLE <= c_usable.coverage() < COVERAGE_COMPLETE
    assert c_usable.complete is False


def test_category_weights_constant():
    assert CATEGORY_WEIGHTS == {
        "business": 20,
        "financial": 15,
        "market": 20,
        "technical": 20,
        "risk": 15,
        "valuation": 10,
    }
    assert sum(CATEGORY_WEIGHTS.values()) == 100


def test_coverage_constants():
    assert COVERAGE_COMPLETE == 0.85
    assert COVERAGE_USABLE == 0.70
