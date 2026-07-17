"""Scoring engine: anchors, peer percentile scoring, dimension/category math.

Sources of truth (Cerebro/):
- shared/SCORING_ENGINE.md: category architecture, continuous (anchor)
  scoring, peer-relative scoring, hybrid score, missing-metric reweighting.
- 00_main_agent/SCORING_AND_GATES.md: fixed category maximums.
- shared/MISSING_DATA_POLICY.md: category coverage formula and the
  complete / usable / incomplete coverage bands.

Discrepancies vs. Cerebro (documented per task instructions, also noted in
the commit message):

1. The Task 4 brief's interface note describes `Category.complete` as
   "coverage >= 0.70" in prose, but also requires the exact constants
   `COVERAGE_COMPLETE = 0.85` and `COVERAGE_USABLE = 0.70` to both exist.
   Cerebro/shared/MISSING_DATA_POLICY.md is unambiguous that "complete"
   specifically means `coverage >= 0.85`, with `0.70 <= coverage < 0.85`
   labeled "usable with caveat" and `coverage < 0.70` labeled "incomplete
   and gate-ineligible". Per the task instructions, Cerebro wins: this
   module implements `Category.complete` as `coverage >= COVERAGE_COMPLETE`
   (0.85), not the brief's inline 0.70. `COVERAGE_USABLE` (0.70) is still
   exposed as the gate-eligibility / dimension-reweighting threshold used
   elsewhere in this module (matching SCORING_ENGINE.md's "INCOMPLETE
   when valid weight is below 70%").
2. SCORING_ENGINE.md's peer-relative scoring section does not pin down a
   specific percentile-rank tie-handling convention. This module uses
   `scipy.stats.percentileofscore(..., kind="mean")` (average of the
   "weak" and "strict" percentile, i.e. ties split the difference), which
   is a standard, symmetric definition consistent with the spec's
   `10 * percentile_rank(x)` formula.
"""

from __future__ import annotations

import math

import numpy as np
from pydantic import BaseModel, ConfigDict
from scipy.stats import percentileofscore

from wbj.core.nullstates import EvidenceClass, NullState, Value

# --- Constants (exact names later tasks depend on) ---

CATEGORY_WEIGHTS: dict[str, int] = {
    "business": 20,
    "financial": 15,
    "market": 20,
    "technical": 20,
    "risk": 15,
    "valuation": 10,
}

COVERAGE_COMPLETE = 0.85
COVERAGE_USABLE = 0.70


# --- Continuous (anchor) scoring ---


def anchor_score(x: float, anchors: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation between registered anchors, clamped [0, 10].

    Per SCORING_ENGINE.md's "Continuous scoring": use piecewise-linear
    interpolation between anchors rather than jumping between label bands.
    Anchors need not be pre-sorted. Values outside the anchor range hold
    flat at the nearest boundary anchor's score (which, combined with the
    final [0, 10] clamp, implements the spec's "clamped to [0,10]").
    """
    if not anchors:
        raise ValueError("anchor_score requires at least one anchor point")
    pts = sorted(anchors, key=lambda p: p[0])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    y = float(np.interp(x, xs, ys))
    return float(min(10.0, max(0.0, y)))


# --- Peer-relative scoring ---


def peer_score(x: float, peers: list[float], higher_is_better: bool = True) -> Value:
    """`10 * percentile_rank(x)` among `peers`.

    Per SCORING_ENGINE.md's "Peer-relative scoring": requires a minimum of
    8 valid peers; with fewer, the peer component is `NOT_SCORABLE`
    (caller falls back to absolute rules). When `higher_is_better` is
    False, the rank is inverted (`10 * (1 - percentile_rank(x))`), per the
    spec's `peer_score_negative` formula.
    """
    valid_peers = [p for p in peers if p is not None and math.isfinite(p)]
    if len(valid_peers) < 8:
        return Value.null(
            NullState.NOT_SCORABLE,
            unit="score",
            warnings=[
                f"PEER_SCORE_INSUFFICIENT_PEERS: need >=8 valid peers, got {len(valid_peers)}"
            ],
        )
    rank = percentileofscore(valid_peers, x, kind="mean") / 100.0
    if not higher_is_better:
        rank = 1.0 - rank
    return Value.of(10.0 * rank, unit="score", evidence_class=EvidenceClass.C)


# --- Hybrid score ---


def hybrid_score(abs_s: float, peer_s: float, w_abs: float, w_peer: float) -> float:
    """`w_abs * abs_s + w_peer * peer_s`; weights must sum to 1.

    Per SCORING_ENGINE.md's "Hybrid score" — only used when a metric
    definition explicitly registers both weights.
    """
    assert abs(w_abs + w_peer - 1.0) < 1e-9, (
        f"hybrid_score weights must sum to 1, got w_abs={w_abs} w_peer={w_peer}"
    )
    return w_abs * abs_s + w_peer * peer_s


# --- Dimension / Category models ---


class Dimension(BaseModel):
    """A scored dimension: a name, its category-point weight, and its metrics.

    `metric_scores` is a list of `(weight, Value)` pairs, where each
    `Value` is a 0-10 score (or a null state explaining why that metric
    couldn't be scored) and weights are relative importance within this
    dimension (conventionally summing to 1.0).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    max_points: float
    metric_scores: list[tuple[float, Value]]

    def total_weight(self) -> float:
        """Sum of all metric weights registered for this dimension."""
        return sum(w for w, _ in self.metric_scores)

    def valid_weight(self) -> float:
        """Sum of metric weights whose `Value` is valid (non-null)."""
        return sum(w for w, v in self.metric_scores if v.is_valid)

    def score10_value(self) -> Value:
        """Weighted mean over valid metrics, as a null-aware `Value`.

        Per SCORING_ENGINE.md's "Missing metrics": reweight only within a
        dimension when at least 70% (`COVERAGE_USABLE`) of its metric
        weight is valid; otherwise the dimension is `NOT_SCORABLE`.
        """
        total = self.total_weight()
        if total <= 0:
            return Value.null(
                NullState.NOT_SCORABLE,
                unit="score",
                warnings=[f"DIMENSION_NO_METRICS: {self.name!r} has no registered metric weight"],
            )
        valid = self.valid_weight()
        if valid / total < COVERAGE_USABLE:
            return Value.null(
                NullState.NOT_SCORABLE,
                unit="score",
                warnings=[
                    "DIMENSION_BELOW_COVERAGE_USABLE: "
                    f"valid weight {valid / total:.4f} < {COVERAGE_USABLE} for {self.name!r}"
                ],
            )
        weighted_sum = sum(w * v.value for w, v in self.metric_scores if v.is_valid)
        return Value.of(weighted_sum / valid, unit="score", evidence_class=EvidenceClass.C)

    def score10(self) -> float:
        """Unwrap `score10_value()`; raises `ValueError` if the dimension is null.

        Primitive is `score10_value()` (null-aware). Use this only when the
        caller has already established the dimension is scorable, or wants
        the null state to surface as an exception rather than be handled.
        """
        v = self.score10_value()
        if v.is_null:
            raise ValueError(f"Dimension {self.name!r} is not scorable: {v.state}")
        return v.value


class Category(BaseModel):
    """A scored category: a name, its point maximum, and its dimensions."""

    model_config = ConfigDict(frozen=True)

    name: str
    max_points: float
    dimensions: list[Dimension]

    def points(self) -> float:
        """Sum of `dimension.max_points * dimension.score10() / 10`.

        Per SCORING_ENGINE.md: "Dimension points = Dimension max points *
        Dimension score / 10; Category points = sum(Dimension points)."
        A `NOT_SCORABLE` dimension contributes 0 points (its uncertainty is
        instead reflected in `coverage()`, not silently defaulted to a mid
        score).
        """
        total = 0.0
        for d in self.dimensions:
            v = d.score10_value()
            if v.is_null:
                continue
            total += d.max_points * (v.value / 10.0)
        return total

    def score10(self) -> float:
        """`10 * Category points / Category max points`."""
        if self.max_points <= 0:
            return 0.0
        return 10.0 * self.points() / self.max_points

    def coverage(self) -> float:
        """Valid metric weight / applicable metric weight, point-weighted by dimension.

        Per MISSING_DATA_POLICY.md: `coverage = valid_metric_weight /
        applicable_metric_weight`. Each dimension's metric weight is scaled
        by that dimension's `max_points` so a dimension worth more of the
        category counts for more of the category's coverage.
        """
        applicable = sum(d.max_points * d.total_weight() for d in self.dimensions)
        if applicable <= 0:
            return 0.0
        valid = sum(d.max_points * d.valid_weight() for d in self.dimensions)
        return valid / applicable

    @property
    def complete(self) -> bool:
        """`coverage() >= COVERAGE_COMPLETE` (0.85).

        See the module-level discrepancy note: Cerebro's
        MISSING_DATA_POLICY.md defines "complete" as `coverage >= 0.85`,
        distinct from the 0.70 "usable / gate-eligible" threshold.
        """
        return self.coverage() >= COVERAGE_COMPLETE
