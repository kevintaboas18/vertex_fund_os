"""Null-state `Value` type and lineage.

Every metric that flows through the wbj compute engine is wrapped in a
`Value`: either a valid number with lineage, or an explicit null state
explaining *why* there is no number. Downstream code must never coerce a
missing value into a number (e.g. 0.0) — arithmetic on `Value` propagates
null states via `Value.map2` instead.

Sources of truth (Cerebro/shared/):
- DATA_POLICY.md: evidence classes (R/C/E/A/Q).
- DATA_DICTIONARY.md: allowed null states.
- MISSING_DATA_POLICY.md: prohibited-imputation metric list.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Callable

from pydantic import BaseModel, ConfigDict, model_validator


class NullState(StrEnum):
    """Reasons a `Value` may carry no number.

    Per Cerebro/shared/DATA_DICTIONARY.md "Null states" — use only these.
    """

    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_MEANINGFUL = "NOT_MEANINGFUL"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"
    NOT_SCORABLE = "NOT_SCORABLE"


class EvidenceClass(StrEnum):
    """Provenance class every value must carry, per DATA_POLICY.md.

    R - reported directly in a filing or official release.
    C - calculated mechanically from reported or market data.
    E - external estimate, such as consensus revenue or market size.
    A - explicit model assumption, such as terminal growth.
    Q - qualitative evidence that is not scored unless a conversion rule exists.
    """

    R = "R"
    C = "C"
    E = "E"
    A = "A"
    Q = "Q"


class Value(BaseModel):
    """A single scalar metric: either a valid number, or an explicit null state.

    Exactly one of `value` / `state` must be set — a `Value` is never both
    a number and a null reason, and never neither.
    """

    model_config = ConfigDict(frozen=True)

    value: float | None = None
    state: NullState | None = None
    unit: str = ""
    period: str | None = None
    as_of: str | None = None
    source_name: str | None = None
    source_locator: str | None = None
    evidence_class: EvidenceClass | None = None
    warnings: list[str] = []

    @model_validator(mode="after")
    def _exactly_one_of_value_or_state(self) -> "Value":
        if (self.value is None) == (self.state is None):
            raise ValueError(
                "Value requires exactly one of `value` or `state` to be set, "
                f"got value={self.value!r} state={self.state!r}"
            )
        return self

    @property
    def is_valid(self) -> bool:
        """True when this Value carries a real number."""
        return self.value is not None

    @property
    def is_null(self) -> bool:
        """True when this Value carries a null state instead of a number."""
        return self.state is not None

    @classmethod
    def of(cls, x: float, unit: str, **lineage: object) -> "Value":
        """Construct a valid `Value` wrapping the number `x`.

        Any lineage field accepted by `Value` (period, as_of, source_name,
        source_locator, evidence_class, warnings, ...) may be passed as a
        keyword argument.
        """
        return cls(value=float(x), state=None, unit=unit, **lineage)

    @classmethod
    def null(cls, state: NullState, unit: str = "", **lineage: object) -> "Value":
        """Construct a null `Value` explaining why there is no number."""
        return cls(value=None, state=state, unit=unit, **lineage)

    @staticmethod
    def map2(a: "Value", b: "Value", fn: Callable[[float, float], float]) -> "Value":
        """Combine two Values, propagating the first null state encountered.

        If `a` is null, returns `a` unchanged. Else if `b` is null, returns
        `b` unchanged. Else returns a valid `Value` wrapping `fn(a.value,
        b.value)`.
        """
        if a.is_null:
            return a
        if b.is_null:
            return b
        unit = a.unit if a.unit == b.unit else ""
        return Value.of(fn(a.value, b.value), unit=unit)


# Cerebro/shared/MISSING_DATA_POLICY.md "Prohibited imputation" — verbatim,
# reconciled to snake_case metric ids.
PROHIBITED_IMPUTATION: frozenset[str] = frozenset(
    {
        "customer_concentration",
        "organic_growth",
        "market_share",
        "debt_maturities",
        "earnings_revisions",
        "technical_volume",
        "beta",
        "terminal_growth",
        "wacc",
        "support_resistance_touches",
    }
)
