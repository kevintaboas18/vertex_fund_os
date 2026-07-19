"""Pydantic output schemas for `wbj.engines.valuation_engine`.

Mirrors the Task-13 brief's interface list and
`Cerebro/special_sauces/INSTITUTIONAL_VALUATION_ENGINE.md` /
`Cerebro/06_valuation_analysis/FORMULAS.md` (VAL-001..044): every composite
valuation result (DCF, reverse DCF, scenarios, Monte Carlo, ensemble) is a
small pydantic model whose numeric leaves are `wbj.core.nullstates.Value`,
so a refusal (e.g. Gordon terminal value with `g >= WACC`) or a warning
(e.g. terminal-value share above 75%) survives all the way to the caller
instead of being silently coerced into a number.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from wbj.core.nullstates import Value


class RDCapitalizeResult(BaseModel):
    """Capitalized R&D asset, amortization, and adjusted EBIT (Cerebro 3.2,
    VAL-RD-002 / VAL-RDA-003)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    asset: Value
    amortization: Value
    adjusted_ebit: Value


class InvestedCapitalResult(BaseModel):
    """Financing-view and operating-view invested capital, reconciled per
    Cerebro 4.2 ("Two equivalent reconciliations should approximately
    agree"). `reconciled` is `True` when both views are available and
    differ by <= 5%; `None` when only the financing view was computable."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    financing_view: Value
    operating_view: Value | None = None
    reconciled: bool | None = None
    warnings: list[str] = Field(default_factory=list)


class DCFResult(BaseModel):
    """FCFF DCF result (Cerebro 6.3/6.4/6.6, VAL-EV-014/VAL-TVG-012/VAL-TVS-042):
    `{ev, pv_explicit, pv_terminal, terminal_share}` plus the raw terminal
    value and any warnings (e.g. terminal share > 75%)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    ev: Value
    pv_explicit: Value
    pv_terminal: Value
    terminal_value: Value
    terminal_share: Value
    warnings: list[str] = Field(default_factory=list)


class WaccSensitivityResult(BaseModel):
    """WACC +/- basis-point sensitivity (Cerebro 5.5)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    base: Value
    minus_bp: Value
    plus_bp: Value
    bp: int


class DCFCommonInputs(BaseModel):
    """Drivers shared across scenario/Monte Carlo/reverse-DCF trials that do
    *not* vary by scenario or trial: starting revenue, share count, tax
    rate, a constant ROIC used to derive the reinvestment rate consistent
    with each trial's growth (`reinvestment_rate = g / ROIC`, Cerebro 4.6 /
    VAL-REINV-043), the explicit forecast horizon in years, and net debt for
    the enterprise-to-equity bridge.
    """

    model_config = ConfigDict(frozen=True)

    revenue0: float
    shares: float
    tax_rate: float
    roic: float
    years: int
    net_debt: float = 0.0


class ScenarioInput(BaseModel):
    """One Bear/Base/Bull scenario driver set (Cerebro 16.1, VAL-SCEN-036)."""

    model_config = ConfigDict(frozen=True)

    probability: float
    growth: float
    margin: float
    wacc: float
    tv_growth: float


class ScenarioResult(BaseModel):
    """Bear/Base/Bull values plus the probability-weighted value
    (VAL-SCEN-036: `weighted = sum(p_i * v_i)`)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    bear_value: Value
    base_value: Value
    bull_value: Value
    weighted_value: Value
    probabilities_sum: float
    warnings: list[str] = Field(default_factory=list)


class MonteCarloRange(BaseModel):
    """Triangular-distribution bounds `(low, mode, high)` for one Monte
    Carlo driver."""

    model_config = ConfigDict(frozen=True)

    low: float
    mode: float
    high: float


class MonteCarloInputs(DCFCommonInputs):
    """`monte_carlo`'s `base_inputs`: the shared DCF drivers plus bounded
    triangular ranges for growth, margin, and WACC (Cerebro 16.2: "Use
    bounded distributions"). `tv_growth` is held fixed across trials."""

    model_config = ConfigDict(frozen=True)

    growth_range: MonteCarloRange
    margin_range: MonteCarloRange
    wacc_range: MonteCarloRange
    tv_growth: float


class MonteCarloResult(BaseModel):
    """Percentile summary of a seeded Monte Carlo run (VAL-MC-037)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    p10: Value
    p25: Value
    median: Value
    p75: Value
    p90: Value
    seed: int
    trials: int


class ReverseDCFInputs(DCFCommonInputs):
    """`reverse_dcf`'s `base_inputs`: the shared DCF drivers plus the base
    margin/WACC/terminal-growth assumptions held fixed while solving for
    implied revenue growth, and the consensus growth rate held fixed while
    solving for implied margin (Cerebro 13, VAL-RDCF-027 — "solve for one or
    more unknowns" with the others fixed)."""

    model_config = ConfigDict(frozen=True)

    margin: float
    wacc: float
    tv_growth: float
    consensus_growth: float | None = None
    growth_bounds: tuple[float, float] = (-0.5, 1.0)
    margin_bounds: tuple[float, float] = (-0.5, 0.9)


class ReverseDCFResult(BaseModel):
    """Implied revenue CAGR (solved holding margin at `base_inputs.margin`)
    and implied terminal operating margin (solved holding growth at
    `consensus_growth`) that reconcile modeled per-share value to the
    observed market price (VAL-RDCF-027)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    implied_growth: Value
    implied_margin: Value
    consensus_growth_used: float
    converged: bool
    warnings: list[str] = Field(default_factory=list)


class EnsembleModelInput(BaseModel):
    """One model's value and reliability weight, for `ensemble`
    (VAL-ENSEMBLE-044)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    label: str
    value: Value
    weight: float


class EnsembleResult(BaseModel):
    """Reliability-weighted ensemble value plus dispersion across the
    contributing model values (Cerebro 19: "If dispersion is wide, report
    low confidence rather than forcing precision")."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    value: Value
    dispersion: Value
    models: list[EnsembleModelInput]
