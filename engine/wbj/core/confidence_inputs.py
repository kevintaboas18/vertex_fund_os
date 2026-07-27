"""The confidence components, derived from Cerebro instead of guessed.

CONFIDENCE_ENGINE.md fixes the formula and the weights:

    Confidence = 0.30*coverage + 0.25*source_quality + 0.20*freshness
               + 0.15*consistency + 0.10*model_fit

and describes each component in one line. It gives no numbers for them,
so three arrived as flat constants — `source_quality = 85.0`,
`consistency = 80.0`, and a per-adapter `model_fit` of 90/65/50/40. None
of those figures appears in any of the eighty-three documents, and the
first two measured nothing at all: the same 85 and 80 were reported
whether a metric rested on an audited filing or an analyst's estimate,
and whether the two data sources agreed or contradicted each other.

Each component's one-line description names a signal Cerebro already
defines elsewhere, so each can be measured rather than asserted:

    source_quality  "regulatory/official evidence scores higher than
                     secondary estimates"
                    -> DATA_POLICY.md's five evidence classes, which are
                       exactly that ordering.

    consistency     "agreement across periods and reconciled sources"
                    -> the packet's own FMP-vs-EDGAR reconciliation, and
                       the per-period balance-sheet reconciliation
                       FORMULAS.md requires of invested capital.

    model_fit       "suitability of the selected formula for the security
                     type"
                    -> INDUSTRY_ADAPTERS.md, which sorts adapters into
                       ones that replace the model, ones that only
                       normalize its inputs, and ones that add to it.

## Why evenly spaced

Cerebro gives orderings without magnitudes. Five evidence classes ranked
best to worst; three adapter treatments. Mapping an ordering onto the
component's stated 0-100 range without inventing anything leaves one
choice: even spacing. Any other spacing asserts that some gaps are wider
than others, which is a claim the documents do not make — the same
reasoning that makes a dimension's unranked inputs weigh equally.
"""

from typing import Any, Iterable

#: DATA_POLICY.md's evidence classes, best to worst, in its own list
#: order. Its one explicit comparison agrees: CONFIDENCE_ENGINE.md says
#: "regulatory/official evidence scores higher than secondary estimates",
#: and R is "reported directly in a filing or official release" while E
#: is "external estimate".
EVIDENCE_CLASS_ORDER = ("R", "C", "E", "A", "Q")

#: The five classes evenly spaced across the component's 0-100 range.
EVIDENCE_CLASS_QUALITY: dict[str, float] = {
    name: 100.0 - (100.0 * i / (len(EVIDENCE_CLASS_ORDER) - 1))
    for i, name in enumerate(EVIDENCE_CLASS_ORDER)
}

#: INDUSTRY_ADAPTERS.md's three treatments, evenly spaced.
#:
#:   replaced    the methodology names a different primary model, so the
#:               formulas actually run do not suit this security at all
#:               ("Replace ROIC with ROE, ROTCE...", "Use risk-adjusted
#:               NPV by program").
#:   normalized  the same formulas apply to inputs that must first be
#:               normalized ("Normalize price/deck and margins through a
#:               cycle").
#:   additive    the formulas are unchanged and the adapter only adds
#:               metrics ("Add ARR growth, NRR, GRR, churn..."), or no
#:               adapter applies.
MODEL_FIT_REPLACED = 0.0
MODEL_FIT_NORMALIZED = 50.0
MODEL_FIT_ADDITIVE = 100.0


def source_quality(evidence_classes: Iterable[Any]) -> float | None:
    """Mean quality of the evidence actually behind the scored metrics.

    Returns None when nothing scored, so the caller can tell "no evidence
    to judge" from "poor evidence" rather than reporting a floor.
    """
    scores = []
    for cls in evidence_classes:
        name = getattr(cls, "value", cls)
        if name in EVIDENCE_CLASS_QUALITY:
            scores.append(EVIDENCE_CLASS_QUALITY[name])
    if not scores:
        return None
    return sum(scores) / len(scores)


def consistency(checks_passed: int, checks_run: int) -> float | None:
    """Share of the consistency checks that agreed, as a 0-100 value.

    A check is a place where two independent readings of the same fact
    can be compared: a figure reconciled across FMP and EDGAR, or the
    financing and operating views of invested capital in one period. A
    fact with only one source is not a check — there is nothing to
    agree — and is excluded rather than counted as either.

    Returns None when no check could run at all.
    """
    if checks_run <= 0:
        return None
    return 100.0 * max(0, min(checks_passed, checks_run)) / checks_run


def model_fit(*, replaces_model: bool, normalizes_inputs: bool) -> float:
    """Suitability of the conventional formulas for this security type."""
    if replaces_model:
        return MODEL_FIT_REPLACED
    if normalizes_inputs:
        return MODEL_FIT_NORMALIZED
    return MODEL_FIT_ADDITIVE
