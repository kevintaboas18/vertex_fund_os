"""Confidence engine: combines data quality metrics into a composite 0-100 score.

Sources of truth:
- Cerebro/shared/CONFIDENCE_ENGINE.md: confidence formula, component definitions, labels
- 00_main_agent/SCORING_AND_GATES.md: confidence as gate signal and categorical classifier
"""

from __future__ import annotations

from typing import Literal

from wbj.core.scoring import CATEGORY_WEIGHTS


def confidence(
    coverage: float,
    source_quality: float,
    freshness: float,
    consistency: float,
    model_fit: float,
) -> float:
    """Weighted confidence score from five components.

    Per CONFIDENCE_ENGINE.md: combines data quality signals into a single
    0-100 value that represents our confidence in a category's evidence,
    independent of the economic score itself.

    Formula: 0.30*coverage + 0.25*source_quality + 0.20*freshness
             + 0.15*consistency + 0.10*model_fit

    Args:
        coverage: valid weighted inputs / required weighted inputs, 0-100
        source_quality: regulatory/official > secondary > estimates, 0-100
        freshness: time decay after staleness threshold, 0-100
        consistency: agreement across periods and reconciled sources, 0-100
        model_fit: suitability of formula for security type, 0-100

    Returns:
        Composite confidence score, 0-100.
    """
    return (
        0.30 * coverage
        + 0.25 * source_quality
        + 0.20 * freshness
        + 0.15 * consistency
        + 0.10 * model_fit
    )


def confidence_label(score: float) -> Literal["High", "Medium", "Low", "Insufficient"]:
    """Classify confidence score into a label for decision support.

    Per CONFIDENCE_ENGINE.md labels table and boundary convention:
    check >= comparisons from top (High first).

    Args:
        score: confidence score, typically 0-100 but unbounded

    Returns:
        One of: "High" (≥85), "Medium" (≥70), "Low" (≥50), "Insufficient" (<50)
    """
    if score >= 85:
        return "High"
    elif score >= 70:
        return "Medium"
    elif score >= 50:
        return "Low"
    else:
        return "Insufficient"


def total_confidence(cat_conf: dict[str, float]) -> float:
    """Aggregate category confidences into a single portfolio confidence.

    Per SCORING_AND_GATES.md and brief: weighted sum of category confidences
    divided by 100, using CATEGORY_WEIGHTS as point allocations.

    Args:
        cat_conf: category name → confidence score (0-100).
                  Expected keys: business, financial, market, technical, risk, valuation.

    Returns:
        Weighted total confidence, 0-100.
    """
    total = sum(
        CATEGORY_WEIGHTS.get(cat, 0) * conf
        for cat, conf in cat_conf.items()
    )
    return total / 100.0
