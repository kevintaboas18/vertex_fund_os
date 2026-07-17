"""Source-hierarchy reconciliation between an FMP value and an EDGAR value.

Per Cerebro/shared/SOURCE_HIERARCHY.md, regulatory filing data (EDGAR) is
tier 1 and outranks a market-data feed like FMP (tier 5) by default. When
both sources report a value for the same field, `reconcile` prefers EDGAR
but keeps FMP's value in the audit trail (warnings) per the hierarchy's
conflict-resolution rule "keep both values in the audit trail" — and marks
the metric `CONFLICTED` when the difference is large enough that picking
a value automatically isn't safe (materiality proxy: >5% relative
difference, per the task-8 brief's shares/debt/cash valuation-input proxy
for the source hierarchy's generic 5%-valuation-midpoint materiality rule).

Alignment assumption: inputs to `reconcile` must already be period-,
unit-, and currency-aligned by the caller — the Task 10 packet builder
performs alignment and restatement-preference (SOURCE_HIERARCHY.md
conflict-resolution steps 1-2) before calling `reconcile`. This module
only adjudicates numeric disagreement between already-aligned values.
"""

from __future__ import annotations

from wbj.core.nullstates import NullState, Value

# 0-2%: sources agree, EDGAR used, lineage note only.
# 2-5%: escalated disagreement, EDGAR used, explicit warning.
# >5%: material conflict, do not auto-resolve -> CONFLICTED.
_ESCALATION_THRESHOLD = 0.02
_CONFLICT_THRESHOLD = 0.05


def _relative_diff(fmp_value: float, edgar_value: float) -> float:
    """Relative difference between `fmp_value` and `edgar_value`.

    The denominator is the EDGAR (tier-1) value, per the task-8 brief. If
    EDGAR is 0, falls back to FMP as the denominator so a nonzero-vs-zero
    disagreement isn't divided by zero. If both are 0, the values agree
    (diff 0).
    """
    denom = edgar_value if edgar_value != 0 else fmp_value
    if denom == 0:
        return 0.0
    return abs(fmp_value - edgar_value) / abs(denom)


def _note(field: str, fmp: Value, edgar: Value, diff: float, verdict: str) -> str:
    return (
        f"{field}: FMP={fmp.value!r} EDGAR={edgar.value!r} "
        f"diff={diff:.2%} ({verdict})"
    )


def reconcile(field: str, fmp: Value, edgar: Value) -> Value:
    """Reconcile an FMP-sourced value against an EDGAR (tier-1) value.

    - Both valid, relative diff <= 2%: EDGAR value, with a lineage note
      recording both sources agreed.
    - Both valid, 2% < diff <= 5%: EDGAR value, with a warning describing
      both values and the disagreement.
    - Both valid, diff > 5%: `Value.null(CONFLICTED)`, with both values
      recorded in warnings; the metric is not auto-resolved or scored.
    - Only EDGAR valid: EDGAR value, unchanged.
    - Only FMP valid: FMP value, with a warning noting EDGAR (tier-1) was
      unavailable so a lower source-quality tier was used.
    - Neither valid: EDGAR's null Value (preserves EDGAR's null-state
      lineage as the tier-1 source of truth for *why* there's no number).

    Inputs must already be period/unit/currency-aligned by the caller
    (the Task 10 packet builder performs alignment and
    restatement-preference before calling this); `reconcile` only
    adjudicates numeric disagreement between aligned values.
    """
    if fmp.is_valid and edgar.is_valid:
        diff = _relative_diff(fmp.value, edgar.value)

        if diff <= _ESCALATION_THRESHOLD:
            note = _note(field, fmp, edgar, diff, "sources agree, EDGAR used")
            return edgar.model_copy(update={"warnings": [*edgar.warnings, note]})

        if diff <= _CONFLICT_THRESHOLD:
            note = _note(
                field, fmp, edgar, diff, "disagreement, EDGAR (tier-1) used"
            )
            return edgar.model_copy(update={"warnings": [*edgar.warnings, note]})

        note = _note(field, fmp, edgar, diff, "CONFLICTED, not auto-resolved")
        return Value.null(
            NullState.CONFLICTED,
            unit=edgar.unit or fmp.unit,
            warnings=[note],
        )

    if edgar.is_valid:
        return edgar

    if fmp.is_valid:
        note = (
            f"{field}: EDGAR (tier-1) unavailable; using FMP "
            f"(lower source-quality tier)"
        )
        return fmp.model_copy(update={"warnings": [*fmp.warnings, note]})

    return edgar
