"""SCORING.md's "TAM confidence <60 caps score at 6" must reach the points.

`03_market_analysis/SCORING.md`, TAM and industry tailwind row: "TAM
confidence <60 caps score at 6". `DECISION_RULES.md`'s tier table sets that
confidence — tier 1 is 100, tier 4 (issuer estimate) is 45, tier 5
(unattributed) is 0.

The existing coverage asserted only that `tam_confidence_caps_dimension(45)`
returns True. That is the decision, not the effect: a cap the engine decides
on and then never applies to the dimension is the "computed and discarded"
failure this codebase has hit repeatedly. These check the weighted mean the
`Category` point math actually reproduces from.

Note the cap acts on the dimension's weighted MEAN, not on each metric — a
first reading of `max(individual scores)` showed 9.24 at tier 4 and looked
like a breach when nothing was wrong.
"""

import pytest

from wbj.core.nullstates import NullState, Value
from wbj.specialists.common import apply_dimension_cap
from wbj.specialists.market import TAM_TIER_CONFIDENCE, tam_confidence_caps_dimension


def _mean(scores):
    w = sum(wt for wt, v in scores if not v.is_null)
    return sum(wt * v.value for wt, v in scores if not v.is_null) / w if w else 0.0


def _scores(*values, weight=0.2):
    return [(weight, Value.of(v, unit="score")) for v in values]


def test_victors_tier_table_is_the_one_the_engine_holds():
    assert TAM_TIER_CONFIDENCE == {1: 100.0, 2: 85.0, 3: 70.0, 4: 45.0, 5: 0.0}


def test_the_threshold_is_sixty():
    """"<60" — 60 itself does not cap."""
    assert tam_confidence_caps_dimension(59.9) is True
    assert tam_confidence_caps_dimension(60.0) is False


def test_which_tiers_cap():
    got = {t: tam_confidence_caps_dimension(c) for t, c in TAM_TIER_CONFIDENCE.items()}
    assert got == {1: False, 2: False, 3: False, 4: True, 5: True}


def test_the_cap_lands_on_the_weighted_mean():
    """The effect, not the decision: a dimension averaging above 6 comes back
    at exactly 6."""
    scores = _scores(10.0, 9.0, 8.0, 7.0, 9.5)
    assert _mean(scores) > 6.0
    assert _mean(apply_dimension_cap(scores, cap=6.0)) == pytest.approx(6.0)


def test_a_dimension_already_below_the_cap_is_untouched():
    """"caps at 6" is a ceiling, never a floor — it must not lift a weak TAM."""
    scores = _scores(3.0, 4.0, 2.0, 5.0, 3.5)
    before = _mean(scores)
    assert before < 6.0
    assert _mean(apply_dimension_cap(scores, cap=6.0)) == pytest.approx(before)


def test_the_cap_leaves_nulls_null():
    """A capped dimension must not invent a score for a metric that had none."""
    scores = _scores(10.0, 9.0) + [(0.2, Value.null(NullState.MISSING, unit="score"))]
    capped = apply_dimension_cap(scores, cap=6.0)
    assert sum(1 for _, v in capped if v.is_null) == 1


def test_an_empty_dimension_is_a_no_op():
    assert apply_dimension_cap([], cap=6.0) == []
