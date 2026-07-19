"""Institutional valuation engine: DCF, WACC, reverse DCF, scenarios, Monte Carlo.

Implements `Cerebro/special_sauces/INSTITUTIONAL_VALUATION_ENGINE.md`
(sections 3-19) and `Cerebro/06_valuation_analysis/FORMULAS.md` (VAL-001..044)
— the authoritative sources for every formula below; see the Task-13 commit
message for the handful of places this module documents a decision Cerebro
leaves underspecified (the synthetic cost-of-debt spread table, and the
constant-growth pricing model used internally by `reverse_dcf`/`scenarios`/
`monte_carlo`).

Unlike `wbj.engines.indicators`/`levels_engine` (plain pandas/numpy over
OHLCV series, no `Value` wrapping — see `wbj.engines`'s package docstring),
every public function here returns a `wbj.core.nullstates.Value` or a small
pydantic model from `wbj.schemas.valuation` whose numeric leaves are
`Value`. Valuation math needs explicit null-state refusals (Gordon terminal
value with `g >= WACC`, a zero ROE in the justified-P/E formula, ...) that a
plain float cannot express, so this engine follows the brief's interface
list ("each returns Value or a small pydantic model") rather than the
OHLCV-engine convention.

Every formula with a canonical id in FORMULAS.md is registered into
`wbj.core.formulas.REGISTRY` via `@register_formula` for bookkeeping
(id/version/unit/inputs), but is called *directly* in this module and in
tests rather than through `run_formula` — `run_formula` unconditionally
wraps a successful return in `Value.of(float(raw_result), ...)`, which
cannot express a domain-specific null state such as `NOT_MEANINGFUL`, and
these formulas already return `Value` themselves. A handful of building
blocks Cerebro describes only in prose (section 4: NOPAT, invested capital,
ROIC, ROIC spread, incremental ROIC) have no distinct VAL-xxx-### id in
FORMULAS.md and are therefore plain, unregistered functions — the same
precedent `wbj.core.formulas.yoy`/`cagr`/`avg_balance` already set for
calculation-convention helpers that aren't themselves a numbered formula.

Every formula here takes plain `float` inputs (not `Value`) — the brief's
own closed-form Step-1 tests pass plain numbers ("E=800, D=200, Ke=10%,
Kd=5%, tax=25%"), and `Value`-wrapping ~10 inputs across 30 formulas would
balloon scope without being exercised by any required test. Each formula
still *returns* `Value`, refusing with an explicit `NullState` for the
specific undefined cases Cerebro flags (`g >= WACC`, non-positive ROE,
zero/negative denominators, ...).
"""

from __future__ import annotations

import statistics
from typing import Sequence

import numpy as np
from scipy.optimize import brentq

from wbj.core.formulas import register_formula
from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.schemas.valuation import (
    DCFCommonInputs,
    DCFResult,
    EnsembleModelInput,
    EnsembleResult,
    InvestedCapitalResult,
    MonteCarloInputs,
    MonteCarloResult,
    RDCapitalizeResult,
    ReverseDCFInputs,
    ReverseDCFResult,
    ScenarioInput,
    ScenarioResult,
    WaccSensitivityResult,
)

__all__ = [
    "normalized_ebit",
    "rd_asset",
    "rd_amortization",
    "rd_capitalize",
    "lease_debt",
    "nopat",
    "invested_capital",
    "roic",
    "spread",
    "eva",
    "incremental_roic",
    "fundamental_growth",
    "unlever_beta",
    "relever_beta",
    "cost_of_equity",
    "synthetic_kd",
    "wacc",
    "wacc_sensitivity",
    "fcff",
    "fcff_via_nopat",
    "gordon_terminal_value",
    "enterprise_value",
    "terminal_share",
    "dcf_value",
    "equity_bridge",
    "per_share",
    "fcfe",
    "fcfe_value",
    "residual_income",
    "residual_income_value",
    "economic_profit_value",
    "justified_pe",
    "justified_ev_sales",
    "hist_zscore",
    "reverse_dcf",
    "scenarios",
    "monte_carlo",
    "ensemble",
    "margin_of_safety",
    "reconciles",
]

_VERSION = "2.0.0"


def _ok(x: float, unit: str) -> Value:
    return Value.of(x, unit=unit, evidence_class=EvidenceClass.C)


def _null(state: NullState, unit: str, *warnings: str) -> Value:
    return Value.null(state, unit=unit, warnings=list(warnings))


# --- 3.1 Normalized operating income (VAL-NORM-001) -------------------------


@register_formula(
    id="VAL-NORM-001", version=_VERSION, unit="usd",
    inputs=["reported", "unusual_gains", "nonrecurring", "misclassified"],
)
def normalized_ebit(reported: float, unusual_gains: float, nonrecurring: float, misclassified: float) -> Value:
    """Normalized EBIT (Cerebro 3.1): `Reported EBIT - unusual operating
    gains + truly non-recurring operating charges removed from the base
    case + recurring costs incorrectly labeled non-recurring`. `nonrecurring`
    and `misclassified` are signed adjustment amounts supplied by the
    caller, added directly per Cerebro's literal formula."""
    return _ok(reported - unusual_gains + nonrecurring + misclassified, unit="usd")


# --- 3.2 Capitalized R&D (VAL-RD-002 / VAL-RDA-003) --------------------------


@register_formula(id="VAL-RD-002", version=_VERSION, unit="usd", inputs=["rd_history", "life"])
def rd_asset(rd_history: Sequence[float], life: int) -> Value:
    """Capitalized R&D asset (Cerebro 3.2, VAL-RD-002):
    `sum(R&D_(t-j) * (1 - j/L), j=0..L-1)`. `rd_history` is ordered oldest
    to newest; `rd_history[-1]` is the current year's R&D (`R&D_t`)."""
    if life <= 0:
        return _null(NullState.NOT_MEANINGFUL, "usd", "RD_LIFE_NONPOSITIVE")
    n = min(life, len(rd_history))
    total = sum(rd_history[-1 - j] * (1 - j / life) for j in range(n))
    return _ok(total, unit="usd")


@register_formula(id="VAL-RDA-003", version=_VERSION, unit="usd", inputs=["rd_history", "life"])
def rd_amortization(rd_history: Sequence[float], life: int) -> Value:
    """R&D amortization (Cerebro 3.2, VAL-RDA-003): straight-line —
    `sum(historical R&D cohorts within the useful life) / L`."""
    if life <= 0:
        return _null(NullState.NOT_MEANINGFUL, "usd", "RD_LIFE_NONPOSITIVE")
    n = min(life, len(rd_history))
    total = sum(rd_history[-1 - j] for j in range(n))
    return _ok(total / life, unit="usd")


def rd_capitalize(rd_history: Sequence[float], life: int, reported_ebit: float | None = None) -> RDCapitalizeResult:
    """Capitalized R&D asset + amortization + adjusted EBIT (Cerebro 3.2):
    `Adjusted EBIT = Reported EBIT + Current R&D - R&D amortization`.
    `adjusted_ebit` is `NOT_APPLICABLE` when `reported_ebit` isn't supplied
    (the brief's two-argument sketch doesn't carry reported EBIT; see the
    module docstring on interface lists being prose, not literal
    signatures)."""
    asset = rd_asset(rd_history, life)
    amortization = rd_amortization(rd_history, life)
    if reported_ebit is None or amortization.is_null:
        adjusted_ebit = _null(NullState.NOT_APPLICABLE, "usd", "REPORTED_EBIT_NOT_SUPPLIED") if reported_ebit is None else amortization
    else:
        current_rd = rd_history[-1] if rd_history else 0.0
        adjusted_ebit = _ok(reported_ebit + current_rd - amortization.value, unit="usd")
    return RDCapitalizeResult(asset=asset, amortization=amortization, adjusted_ebit=adjusted_ebit)


# --- 3.3 Operating lease debt (VAL-LEASE-004) --------------------------------


@register_formula(id="VAL-LEASE-004", version=_VERSION, unit="usd", inputs=["commitments", "pretax_kd"])
def lease_debt(commitments: Sequence[float], pretax_kd: float) -> Value:
    """PV of future operating-lease commitments at the pre-tax cost of debt
    (Cerebro 3.3, VAL-LEASE-004)."""
    if pretax_kd <= -1:
        return _null(NullState.NOT_MEANINGFUL, "usd", "DISCOUNT_RATE_LE_NEG100PCT")
    pv = sum(c / (1 + pretax_kd) ** (t + 1) for t, c in enumerate(commitments))
    return _ok(pv, unit="usd")


# --- 4. ROIC, WACC, economic value creation ----------------------------------
# NOPAT/invested-capital/ROIC/spread/incremental-ROIC are described only in
# prose in Cerebro section 4 with no distinct VAL-xxx-### id in FORMULAS.md
# (VAL-EVA-020's formula folds NOPAT and ROIC in as sub-terms) — plain,
# unregistered helpers, same precedent as wbj.core.formulas.yoy/cagr.


def nopat(norm_ebit: float, tax_rate: float) -> Value:
    """NOPAT (Cerebro 4.1): `Normalized EBIT * (1 - normalized cash tax rate)`."""
    return _ok(norm_ebit * (1 - tax_rate), unit="usd")


def invested_capital(
    debt: float,
    equity: float,
    excess_cash: float,
    debt_like_claims: float = 0.0,
    operating_assets: float | None = None,
    operating_liabilities: float | None = None,
) -> InvestedCapitalResult:
    """Invested capital, financing view reconciled to operating view
    (Cerebro 4.2): `Debt + Equity - Excess cash + Debt-like operating
    claims` vs. `Operating assets - Operating liabilities`. Warns if the two
    views differ by more than 5% of the financing view."""
    financing = debt + equity - excess_cash + debt_like_claims
    financing_v = _ok(financing, unit="usd")
    if operating_assets is None or operating_liabilities is None:
        return InvestedCapitalResult(financing_view=financing_v, operating_view=None, reconciled=None)
    operating = operating_assets - operating_liabilities
    operating_v = _ok(operating, unit="usd")
    warnings: list[str] = []
    reconciled = True
    if financing != 0 and abs(operating - financing) / abs(financing) > 0.05:
        reconciled = False
        warnings.append("INVESTED_CAPITAL_VIEWS_DIFFER_GT_5PCT")
    return InvestedCapitalResult(financing_view=financing_v, operating_view=operating_v, reconciled=reconciled, warnings=warnings)


def roic(nopat_value: float, avg_invested_capital: float) -> Value:
    """ROIC (Cerebro 4.3): `NOPAT / Average invested capital`."""
    if avg_invested_capital <= 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "INVESTED_CAPITAL_NONPOSITIVE")
    return _ok(nopat_value / avg_invested_capital, unit="pct")


def spread(roic_value: float, wacc_value: float) -> Value:
    """ROIC spread (Cerebro 4.4): `ROIC - WACC`."""
    return _ok(roic_value - wacc_value, unit="pct")


@register_formula(id="VAL-EVA-020", version=_VERSION, unit="usd", inputs=["nopat_value", "wacc_value", "beginning_ic"])
def eva(nopat_value: float, wacc_value: float, beginning_ic: float) -> Value:
    """Economic profit / EVA (Cerebro 4.4, VAL-EVA-020): `(ROIC_t - WACC_t)
    * InvestedCapital_(t-1)`, equivalently `NOPAT_t - WACC_t *
    InvestedCapital_(t-1)` (the form used here, since it needs no separate
    average-capital input)."""
    return _ok(nopat_value - wacc_value * beginning_ic, unit="usd")


def incremental_roic(delta_nopat: float, delta_ic: float) -> Value:
    """Incremental ROIC (Cerebro 4.5): `change in NOPAT / change in invested
    capital`, over a 3-5 year window per Cerebro's guidance."""
    if delta_ic == 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "DELTA_IC_ZERO")
    return _ok(delta_nopat / delta_ic, unit="pct")


@register_formula(id="VAL-REINV-043", version=_VERSION, unit="pct", inputs=["reinvestment_rate", "roic_value"])
def fundamental_growth(reinvestment_rate: float, roic_value: float) -> Value:
    """Fundamental growth (Cerebro 4.6, VAL-REINV-043): `Reinvestment rate *
    ROIC` — the same terminal-reinvestment-consistency relationship as
    FORMULAS.md's `Terminal reinvestment rate = g / Terminal ROIC`,
    rearranged to solve for `g` given a reinvestment rate instead of for the
    reinvestment rate given `g` (see `_terminal_reinvestment_rate` for the
    latter direction, used internally by the scenario/Monte Carlo/reverse-DCF
    pricing model)."""
    return _ok(reinvestment_rate * roic_value, unit="pct")


def _terminal_reinvestment_rate(g: float, roic_value: float) -> float:
    """`g / Terminal ROIC` (Cerebro 4.6 / VAL-REINV-043) — prevents "free
    growth" in the constant-growth pricing model below."""
    return g / roic_value if roic_value else 0.0


# --- 5. Discount rate ---------------------------------------------------------


@register_formula(id="VAL-UBETA-009", version=_VERSION, unit="", inputs=["levered_beta", "tax_rate", "de"])
def unlever_beta(levered_beta: float, tax_rate: float, de: float) -> Value:
    """Unlevered beta (Cerebro 5.3, VAL-UBETA-009): `LeveredBeta / (1 +
    (1-tax)*D/E)`."""
    denom = 1 + (1 - tax_rate) * de
    if denom == 0:
        return _null(NullState.NOT_MEANINGFUL, "", "UNLEVER_DENOMINATOR_ZERO")
    return _ok(levered_beta / denom, unit="")


@register_formula(id="VAL-LBETA-010", version=_VERSION, unit="", inputs=["unlevered_beta", "tax_rate", "target_de"])
def relever_beta(unlevered_beta: float, tax_rate: float, target_de: float) -> Value:
    """Relevered bottom-up beta (Cerebro 5.3, VAL-LBETA-010):
    `MedianUnleveredBeta * (1 + (1-tax)*TargetD/E)`."""
    return _ok(unlevered_beta * (1 + (1 - tax_rate) * target_de), unit="")


@register_formula(id="VAL-KE-008", version=_VERSION, unit="pct", inputs=["rf", "beta", "erp", "crp"])
def cost_of_equity(rf: float, beta: float, erp: float, crp: float = 0.0) -> Value:
    """Cost of equity (Cerebro 5.2, VAL-KE-008): `RiskFreeRate +
    BottomUpBeta*ERP + justified country risk premium`."""
    return _ok(rf + beta * erp + crp, unit="pct")


# Interest-coverage -> default-spread mapping. Cerebro 5.4 requires this
# table but does not itself publish spread values ("The mapping table must
# be dated and sourced" — no numbers given); the bands below follow the
# publicly documented Damodaran synthetic-rating methodology Cerebro cites
# in section 23 references, and are this module's own dated construction
# (2.0.0, matching the Cerebro doc version) rather than a value taken
# directly from Cerebro. Document any revision to this table in the
# module's own commit history.
_SYNTHETIC_SPREAD_TABLE: tuple[tuple[float, float], ...] = (
    (8.5, 0.0069),
    (6.5, 0.0085),
    (5.5, 0.0102),
    (4.25, 0.0126),
    (3.0, 0.0154),
    (2.5, 0.0181),
    (2.25, 0.0284),
    (2.0, 0.0355),
    (1.75, 0.0426),
    (1.5, 0.0497),
    (1.25, 0.0710),
    (0.8, 0.0994),
    (0.5, 0.1200),
    (float("-inf"), 0.1500),
)


def _default_spread(interest_coverage: float) -> float:
    for lower_bound, spread_bps in _SYNTHETIC_SPREAD_TABLE:
        if interest_coverage >= lower_bound:
            return spread_bps
    return _SYNTHETIC_SPREAD_TABLE[-1][1]


@register_formula(id="VAL-KD-011", version=_VERSION, unit="pct", inputs=["rf", "interest_coverage"])
def synthetic_kd(rf: float, interest_coverage: float) -> Value:
    """Pre-tax cost of debt from a synthetic rating (Cerebro 5.4,
    VAL-KD-011): `RiskFreeRate + DefaultSpread(InterestCoverage)`. Below
    1.5x coverage is a mandatory solvency warning per Cerebro 5.4, carried
    as a `Value.warnings` entry rather than a refusal."""
    spread_bps = _default_spread(interest_coverage)
    warnings = ["SOLVENCY_WARNING_INTEREST_COVERAGE_BELOW_1_5X"] if interest_coverage < 1.5 else []
    return Value.of(rf + spread_bps, unit="pct", evidence_class=EvidenceClass.C, warnings=warnings)


@register_formula(id="VAL-WACC-007", version=_VERSION, unit="pct", inputs=["e", "d", "ke", "kd", "tax_rate"])
def wacc(e: float, d: float, ke: float, kd: float, tax_rate: float) -> Value:
    """WACC (Cerebro 5.1, VAL-WACC-007): `E/(D+E)*Ke + D/(D+E)*Kd*(1-tax)`."""
    total = d + e
    if total <= 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "CAPITAL_BASE_NONPOSITIVE")
    return _ok(e / total * ke + d / total * kd * (1 - tax_rate), unit="pct")


def wacc_sensitivity(w: Value, bp: int = 100) -> WaccSensitivityResult:
    """WACC +/- basis-point sensitivity (Cerebro 5.5)."""
    if w.is_null:
        return WaccSensitivityResult(base=w, minus_bp=w, plus_bp=w, bp=bp)
    delta = bp / 10000.0
    return WaccSensitivityResult(
        base=w,
        minus_bp=_ok(w.value - delta, unit=w.unit),
        plus_bp=_ok(w.value + delta, unit=w.unit),
        bp=bp,
    )


# --- 6. FCFF DCF ---------------------------------------------------------------


@register_formula(id="VAL-FCFF-005", version=_VERSION, unit="usd", inputs=["ebit", "tax_rate", "dna", "capex", "dnwc"])
def fcff(ebit: float, tax_rate: float, dna: float, capex: float, dnwc: float) -> Value:
    """FCFF (Cerebro 6.1, VAL-FCFF-005): `EBIT*(1-tax) + D&A - Capex -
    change in non-cash NWC`."""
    return _ok(ebit * (1 - tax_rate) + dna - capex - dnwc, unit="usd")


def fcff_via_nopat(nopat_value: float, reinvestment: float) -> Value:
    """FCFF, alternate form (Cerebro 6.1): `NOPAT - Reinvestment`. The two
    forms reconcile when `Reinvestment = Capex + ChangeNWC - D&A`; see
    `test_fcff_reconciles_with_nopat_minus_reinvestment`."""
    return _ok(nopat_value - reinvestment, unit="usd")


@register_formula(id="VAL-TVG-012", version=_VERSION, unit="usd", inputs=["fcff_n", "g", "wacc_value"])
def gordon_terminal_value(fcff_n: float, g: float, wacc_value: float) -> Value:
    """Gordon terminal value (Cerebro 6.4, VAL-TVG-012): `FCFF_(N+1) /
    (WACC-g)`, `FCFF_(N+1) = FCFF_N*(1+g)`. Refuses (`NOT_MEANINGFUL`) when
    `g >= WACC` per Cerebro's required check."""
    if g >= wacc_value:
        return _null(NullState.NOT_MEANINGFUL, "usd", "TERMINAL_GROWTH_GE_WACC")
    fcff_n1 = fcff_n * (1 + g)
    return _ok(fcff_n1 / (wacc_value - g), unit="usd")


@register_formula(id="VAL-EV-014", version=_VERSION, unit="usd", inputs=["fcffs", "wacc_value", "terminal_value"])
def enterprise_value(fcffs: Sequence[float], wacc_value: float, terminal_value: float) -> Value:
    """FCFF enterprise value (Cerebro 6.3, VAL-EV-014):
    `sum(FCFF_t/(1+WACC)^t) + TerminalValue/(1+WACC)^N`."""
    n = len(fcffs)
    pv_explicit = sum(f / (1 + wacc_value) ** (t + 1) for t, f in enumerate(fcffs))
    pv_terminal = terminal_value / (1 + wacc_value) ** n if n else terminal_value
    return _ok(pv_explicit + pv_terminal, unit="usd")


@register_formula(id="VAL-TVS-042", version=_VERSION, unit="pct", inputs=["pv_terminal", "ev"])
def terminal_share(pv_terminal: float, ev: float) -> Value:
    """Terminal-value share (Cerebro 6.6, VAL-TVS-042): `PV(TerminalValue) /
    EnterpriseValue`. Flags (does not refuse) shares above 75% per Cerebro's
    "high-sensitivity warning" rule."""
    if ev <= 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "EV_NONPOSITIVE")
    share = pv_terminal / ev
    warnings = ["TERMINAL_VALUE_SHARE_ABOVE_75PCT"] if share > 0.75 else []
    return Value.of(share, unit="pct", evidence_class=EvidenceClass.C, warnings=warnings)


def dcf_value(fcffs: Sequence[float], wacc_value: float, terminal_growth: float) -> DCFResult:
    """Full FCFF DCF (Cerebro 6.3/6.4/6.6): explicit FCFF path + Gordon
    terminal value -> `{ev, pv_explicit, pv_terminal, terminal_share}`.
    Refuses with `NOT_MEANINGFUL` end to end when `terminal_growth >=
    wacc_value`, and carries a warning when the terminal-value share exceeds
    75%."""
    tv = gordon_terminal_value(fcffs[-1], terminal_growth, wacc_value)
    if tv.is_null:
        null = _null(tv.state, "usd", "TERMINAL_GROWTH_GE_WACC")
        return DCFResult(
            ev=null, pv_explicit=null, pv_terminal=null, terminal_value=tv,
            terminal_share=_null(tv.state, "pct", "TERMINAL_GROWTH_GE_WACC"),
            warnings=["g >= WACC: terminal value refused"],
        )
    n = len(fcffs)
    pv_explicit = sum(f / (1 + wacc_value) ** (t + 1) for t, f in enumerate(fcffs))
    pv_terminal = tv.value / (1 + wacc_value) ** n
    ev = pv_explicit + pv_terminal
    ts = terminal_share(pv_terminal, ev)
    return DCFResult(
        ev=_ok(ev, "usd"),
        pv_explicit=_ok(pv_explicit, "usd"),
        pv_terminal=_ok(pv_terminal, "usd"),
        terminal_value=tv,
        terminal_share=ts,
        warnings=list(ts.warnings),
    )


@register_formula(
    id="VAL-EQ-015", version=_VERSION, unit="usd",
    inputs=["ev", "cash", "nonop", "debt", "lease_debt_value", "preferred", "minority", "pension"],
)
def equity_bridge(
    ev: float, cash: float, nonop: float, debt: float, lease_debt_value: float,
    preferred: float, minority: float, pension: float,
) -> Value:
    """Enterprise-to-equity bridge (Cerebro 6.7, VAL-EQ-015): `EV + Cash +
    NonOperatingAssets - Debt - LeaseDebt - Preferred - Minority - Pension`."""
    return _ok(ev + cash + nonop - debt - lease_debt_value - preferred - minority - pension, unit="usd")


@register_formula(id="VAL-PS-016", version=_VERSION, unit="usd_per_share", inputs=["equity", "diluted"])
def per_share(equity: float, diluted: float) -> Value:
    """Fully diluted per-share value (Cerebro 6.8, VAL-PS-016): `Equity
    value / Fully diluted shares`."""
    if diluted <= 0:
        return _null(NullState.NOT_MEANINGFUL, "usd_per_share", "DILUTED_SHARES_NONPOSITIVE")
    return _ok(equity / diluted, unit="usd_per_share")


# --- 7. FCFE ---------------------------------------------------------------------


@register_formula(
    id="VAL-FCFE-017", version=_VERSION, unit="usd",
    inputs=["net_income", "dna", "capex", "dnwc", "net_borrowing"],
)
def fcfe(net_income: float, dna: float, capex: float, dnwc: float, net_borrowing: float) -> Value:
    """FCFE (Cerebro 7, VAL-FCFE-017): `NetIncome + D&A - Capex -
    ChangeNWC + NetBorrowing`."""
    return _ok(net_income + dna - capex - dnwc + net_borrowing, unit="usd")


@register_formula(
    id="VAL-FCFEV-018", version=_VERSION, unit="usd", inputs=["fcfes", "cost_equity_value", "terminal_growth"],
)
def fcfe_value(fcfes: Sequence[float], cost_equity_value: float, terminal_growth: float) -> Value:
    """FCFE equity value (Cerebro 7, VAL-FCFEV-018):
    `sum(FCFE_t/(1+Ke)^t) + TerminalFCFE/(Ke-g)/(1+Ke)^N`. Refuses
    (`NOT_MEANINGFUL`) when `terminal_growth >= cost_equity_value`."""
    if terminal_growth >= cost_equity_value:
        return _null(NullState.NOT_MEANINGFUL, "usd", "TERMINAL_GROWTH_GE_COST_OF_EQUITY")
    n = len(fcfes)
    pv_explicit = sum(f / (1 + cost_equity_value) ** (t + 1) for t, f in enumerate(fcfes))
    terminal_fcfe = fcfes[-1] * (1 + terminal_growth)
    pv_terminal = (terminal_fcfe / (cost_equity_value - terminal_growth)) / (1 + cost_equity_value) ** n
    return _ok(pv_explicit + pv_terminal, unit="usd")


# --- 9. Economic-profit valuation -------------------------------------------------


@register_formula(id="VAL-EVAEV-021", version=_VERSION, unit="usd", inputs=["ic0", "eps", "wacc_value"])
def economic_profit_value(ic0: float, eps: Sequence[float], wacc_value: float) -> Value:
    """Economic-profit enterprise value (Cerebro 9, VAL-EVAEV-021):
    `InvestedCapital_0 + PV(future economic profits)`. Should reconcile to
    FCFF DCF under consistent assumptions (see `reconciles`)."""
    pv_eps = sum(ep / (1 + wacc_value) ** (t + 1) for t, ep in enumerate(eps))
    return _ok(ic0 + pv_eps, unit="usd")


def reconciles(a: Value, b: Value, tol: float = 0.01) -> bool:
    """True when two valid `Value`s agree within `tol` relative tolerance
    (Cerebro 9: "A mismatch exposes a modeling error"). `False` if either
    is null."""
    if a.is_null or b.is_null:
        return False
    denom = max(abs(a.value), abs(b.value), 1e-12)
    return abs(a.value - b.value) / denom <= tol


# --- 10. Residual income ----------------------------------------------------------


@register_formula(
    id="VAL-RI-022", version=_VERSION, unit="usd",
    inputs=["net_income", "cost_equity_value", "beginning_book_equity"],
)
def residual_income(net_income: float, cost_equity_value: float, beginning_book_equity: float) -> Value:
    """Residual income (Cerebro 10, VAL-RI-022): `NetIncome -
    CostEquity*BeginningBookEquity`."""
    return _ok(net_income - cost_equity_value * beginning_book_equity, unit="usd")


@register_formula(
    id="VAL-RIV-023", version=_VERSION, unit="usd", inputs=["book_equity0", "ris", "cost_equity_value"],
)
def residual_income_value(book_equity0: float, ris: Sequence[float], cost_equity_value: float) -> Value:
    """Residual-income equity value (Cerebro 10, VAL-RIV-023): `BookEquity_0
    + PV(future residual income)`."""
    pv_ri = sum(ri / (1 + cost_equity_value) ** (t + 1) for t, ri in enumerate(ris))
    return _ok(book_equity0 + pv_ri, unit="usd")


# --- 14. Fundamental multiples -----------------------------------------------------


@register_formula(id="VAL-JPE-032", version=_VERSION, unit="", inputs=["g", "roe", "ke"])
def justified_pe(g: float, roe: float, ke: float) -> Value:
    """Justified P/E (Cerebro 14.1, VAL-JPE-032): `(1 - g/ROE) / (Ke - g)`.
    Requires positive sustainable ROE and `g < Ke`; refuses
    (`NOT_MEANINGFUL`) otherwise."""
    if roe <= 0:
        return _null(NullState.NOT_MEANINGFUL, "", "ROE_NONPOSITIVE")
    if g >= ke:
        return _null(NullState.NOT_MEANINGFUL, "", "GROWTH_GE_COST_OF_EQUITY")
    return _ok((1 - g / roe) / (ke - g), unit="")


@register_formula(
    id="VAL-JEVS-033", version=_VERSION, unit="", inputs=["margin", "tax_rate", "g", "roic_value", "wacc_value"],
)
def justified_ev_sales(margin: float, tax_rate: float, g: float, roic_value: float, wacc_value: float) -> Value:
    """Justified EV/Sales (Cerebro 14.2, VAL-JEVS-033): `AfterTaxOperating
    Margin * (1 - g/ROIC) / (WACC - g)`, `AfterTaxOperatingMargin =
    margin*(1-tax)`. Refuses (`NOT_MEANINGFUL`) when `g >= WACC` or `ROIC ==
    0`."""
    if wacc_value <= g:
        return _null(NullState.NOT_MEANINGFUL, "", "GROWTH_GE_WACC")
    if roic_value == 0:
        return _null(NullState.NOT_MEANINGFUL, "", "ROIC_ZERO")
    after_tax_margin = margin * (1 - tax_rate)
    return _ok(after_tax_margin * (1 - g / roic_value) / (wacc_value - g), unit="")


# --- 15. Historical valuation with robust statistics -------------------------------


@register_formula(id="VAL-ZHIST-035", version=_VERSION, unit="", inputs=["current", "history"])
def hist_zscore(current: float, history: Sequence[float]) -> Value:
    """Robust historical z-score (Cerebro 15, VAL-ZHIST-035): `(Current -
    Historical median) / (1.4826*MAD)`."""
    if not history:
        return _null(NullState.MISSING, "", "HISTORY_EMPTY")
    median = statistics.median(history)
    mad = statistics.median(abs(x - median) for x in history)
    scale = 1.4826 * mad
    if scale == 0:
        return _null(NullState.NOT_MEANINGFUL, "", "ROBUST_SCALE_ZERO")
    return _ok((current - median) / scale, unit="")


# --- 40. Margin of safety -----------------------------------------------------------


@register_formula(id="VAL-MOS-040", version=_VERSION, unit="pct", inputs=["value", "price"])
def margin_of_safety(value: float, price: float) -> Value:
    """Margin of safety (Cerebro 18/VAL-MOS-040): `(Value - Price) / Value`."""
    if value == 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "VALUE_ZERO")
    return _ok((value - price) / value, unit="pct")


# --- Constant-growth pricing model: shared by reverse_dcf/scenarios/monte_carlo ----
#
# Cerebro 13/16 describe reverse DCF and scenario/Monte Carlo analysis as
# operating on top of "the DCF model" without pinning down its explicit
# forecast structure. To have a single well-defined, invertible model to
# solve/simulate over, this module forecasts revenue at a constant growth
# rate for `years`, applies a constant operating margin, and reinvests at
# `reinvestment_rate = g/ROIC` (Cerebro 4.6/VAL-REINV-043) so growth is never
# "free" — the same discipline `dcf_value`'s explicit-FCFF-list interface
# leaves to the caller. This is distinct from (and does not replace)
# `dcf_value`, which takes an already-built FCFF path.


def _constant_growth_per_share(
    growth: float, margin: float, wacc_value: float, tv_growth: float,
    revenue0: float, tax_rate: float, roic_value: float, years: int, shares: float, net_debt: float,
) -> float:
    # Single source of truth for the g>=WACC refusal, shared with the
    # Value-returning `gordon_terminal_value`: this float core raises so any
    # caller that forgets to guard fails loudly rather than silently pricing
    # a meaningless negative terminal value; `_constant_growth_value` and the
    # per-scenario/reverse-DCF guards below convert that condition into a
    # graceful `NOT_MEANINGFUL` Value at the public boundary.
    if tv_growth >= wacc_value:
        raise ValueError("terminal growth >= wacc: not meaningful")
    revenue = revenue0
    fcffs: list[float] = []
    nopat_n = 0.0
    for _ in range(years):
        revenue *= 1 + growth
        nopat_n = revenue * margin * (1 - tax_rate)
        fcffs.append(nopat_n * (1 - _terminal_reinvestment_rate(growth, roic_value)))
    # Terminal FCFF is built from year-N *NOPAT* grown one more year at
    # tv_growth and reinvested at tv_growth's own rate — not from fcffs[-1],
    # which is already net of year-N's reinvestment (using fcffs[-1] here
    # would apply the reinvestment haircut twice).
    nopat_terminal = nopat_n * (1 + tv_growth)
    fcff_terminal = nopat_terminal * (1 - _terminal_reinvestment_rate(tv_growth, roic_value))
    tv = fcff_terminal / (wacc_value - tv_growth)
    pv_explicit = sum(f / (1 + wacc_value) ** (t + 1) for t, f in enumerate(fcffs))
    pv_terminal = tv / (1 + wacc_value) ** years
    equity = pv_explicit + pv_terminal - net_debt
    return equity / shares


def _constant_growth_value(
    growth: float, margin: float, wacc_value: float, tv_growth: float,
    revenue0: float, tax_rate: float, roic_value: float, years: int, shares: float, net_debt: float,
) -> Value:
    """Value-returning boundary around `_constant_growth_per_share`: converts
    the `g >= WACC` refusal into a graceful `NOT_MEANINGFUL` Value (with a
    `TERMINAL_GROWTH_GE_WACC` warning) instead of letting the float core's
    `ValueError` escape. Used by `scenarios` (per-branch) so one bad scenario
    refuses only its own branch rather than crashing all three."""
    if tv_growth >= wacc_value:
        return _null(NullState.NOT_MEANINGFUL, "usd_per_share", "TERMINAL_GROWTH_GE_WACC")
    v = _constant_growth_per_share(
        growth, margin, wacc_value, tv_growth,
        revenue0=revenue0, tax_rate=tax_rate, roic_value=roic_value,
        years=years, shares=shares, net_debt=net_debt,
    )
    return _ok(v, "usd_per_share")


@register_formula(id="VAL-RDCF-027", version=_VERSION, unit="pct", inputs=["price", "shares", "base_inputs"])
def reverse_dcf(price: float, shares: float, base_inputs: ReverseDCFInputs) -> ReverseDCFResult:
    """Reverse DCF (Cerebro 13, VAL-RDCF-027): solve for the implied revenue
    CAGR that reconciles the constant-growth model to `price` (holding
    margin at `base_inputs.margin`), then the implied terminal operating
    margin (holding growth at `base_inputs.consensus_growth`, defaulting to
    the just-solved implied growth)."""
    # Economically-invalid inputs (terminal growth >= WACC) are diagnosed up
    # front, BEFORE brentq — otherwise the float pricing core raises the same
    # ValueError brentq raises when it can't bracket a root, and the two get
    # conflated into a misleading NO_SIGN_CHANGE_IN_GROWTH_BOUNDS diagnosis.
    if base_inputs.tv_growth >= base_inputs.wacc:
        null = _null(NullState.NOT_MEANINGFUL, "pct", "INVALID_INPUTS_G_GE_WACC")
        return ReverseDCFResult(
            implied_growth=null,
            implied_margin=null,
            consensus_growth_used=base_inputs.consensus_growth or 0.0,
            converged=False,
            warnings=["reverse_dcf: INVALID_INPUTS_G_GE_WACC (terminal growth >= WACC)"],
        )

    common = dict(
        revenue0=base_inputs.revenue0, tax_rate=base_inputs.tax_rate, roic_value=base_inputs.roic,
        years=base_inputs.years, shares=shares, net_debt=base_inputs.net_debt,
    )

    def f_growth(g: float) -> float:
        return _constant_growth_per_share(
            g, base_inputs.margin, base_inputs.wacc, base_inputs.tv_growth, **common,
        ) - price

    lo, hi = base_inputs.growth_bounds
    try:
        implied_growth = brentq(f_growth, lo, hi, xtol=1e-12, rtol=1e-12)
        converged = True
        warnings: list[str] = []
    except ValueError:
        return ReverseDCFResult(
            implied_growth=_null(NullState.NOT_SCORABLE, "pct", "NO_SIGN_CHANGE_IN_GROWTH_BOUNDS"),
            implied_margin=_null(NullState.NOT_SCORABLE, "pct", "NO_SIGN_CHANGE_IN_GROWTH_BOUNDS"),
            consensus_growth_used=base_inputs.consensus_growth or 0.0,
            converged=False,
            warnings=["reverse_dcf growth solve did not bracket a root"],
        )

    consensus_growth = base_inputs.consensus_growth if base_inputs.consensus_growth is not None else implied_growth

    def f_margin(m: float) -> float:
        return _constant_growth_per_share(
            consensus_growth, m, base_inputs.wacc, base_inputs.tv_growth, **common,
        ) - price

    mlo, mhi = base_inputs.margin_bounds
    try:
        implied_margin = brentq(f_margin, mlo, mhi, xtol=1e-12, rtol=1e-12)
    except ValueError:
        return ReverseDCFResult(
            implied_growth=_ok(implied_growth, "pct"),
            implied_margin=_null(NullState.NOT_SCORABLE, "pct", "NO_SIGN_CHANGE_IN_MARGIN_BOUNDS"),
            consensus_growth_used=consensus_growth,
            converged=False,
            warnings=["reverse_dcf margin solve did not bracket a root"],
        )

    return ReverseDCFResult(
        implied_growth=_ok(implied_growth, "pct"),
        implied_margin=_ok(implied_margin, "pct"),
        consensus_growth_used=consensus_growth,
        converged=converged,
        warnings=warnings,
    )


@register_formula(id="VAL-SCEN-036", version=_VERSION, unit="usd_per_share", inputs=["bear", "base", "bull", "common"])
def scenarios(bear: ScenarioInput, base: ScenarioInput, bull: ScenarioInput, common: DCFCommonInputs) -> ScenarioResult:
    """Bear/Base/Bull scenario valuation (Cerebro 16.1, VAL-SCEN-036):
    `weighted = sum(probability_i * value_i)`. Probabilities must sum to
    1.0; raises `ValueError` otherwise (Cerebro: "Probabilities sum to 1 and
    remain disclosed")."""
    total_p = bear.probability + base.probability + bull.probability
    if abs(total_p - 1.0) > 1e-9:
        raise ValueError(f"scenario probabilities must sum to 1.0, got {total_p}")

    def value_of(s: ScenarioInput) -> Value:
        # Per-branch Value boundary: an offending scenario (tv_growth >= wacc)
        # refuses only ITS OWN branch (NOT_MEANINGFUL) rather than raising and
        # tumbling the other two — reverse_dcf/monte_carlo already guard this
        # condition; scenarios now does too, via the shared wrapper.
        return _constant_growth_value(
            s.growth, s.margin, s.wacc, s.tv_growth,
            revenue0=common.revenue0, tax_rate=common.tax_rate, roic_value=common.roic,
            years=common.years, shares=common.shares, net_debt=common.net_debt,
        )

    bear_v, base_v, bull_v = value_of(bear), value_of(base), value_of(bull)

    # A probability-weighted mean can't be formed while any branch is refused
    # (its value is genuinely unknown, not zero) — refuse the aggregate too.
    if bear_v.is_null or base_v.is_null or bull_v.is_null:
        weighted = _null(
            NullState.NOT_MEANINGFUL, "usd_per_share",
            "WEIGHTED_VALUE_UNDEFINED_REFUSED_SCENARIO",
        )
        warnings = ["scenarios: at least one branch refused (g >= WACC); weighted value not computable"]
    else:
        weighted = _ok(
            bear.probability * bear_v.value + base.probability * base_v.value + bull.probability * bull_v.value,
            "usd_per_share",
        )
        warnings = []

    return ScenarioResult(
        bear_value=bear_v,
        base_value=base_v,
        bull_value=bull_v,
        weighted_value=weighted,
        probabilities_sum=total_p,
        warnings=warnings,
    )


@register_formula(
    id="VAL-MC-037", version=_VERSION, unit="usd_per_share",
    inputs=["base_inputs", "n", "seed"],
)
def monte_carlo(base_inputs: MonteCarloInputs, n: int = 2000, *, seed: int) -> MonteCarloResult:
    """Monte Carlo valuation (Cerebro 16.2, VAL-MC-037): seeded triangular
    draws on growth/margin/WACC, `rng = numpy.random.default_rng(seed)` —
    the same seed always produces identical percentiles (no global
    `np.random`, no time-based seeding)."""
    rng = np.random.default_rng(seed)
    g = rng.triangular(base_inputs.growth_range.low, base_inputs.growth_range.mode, base_inputs.growth_range.high, size=n)
    m = rng.triangular(base_inputs.margin_range.low, base_inputs.margin_range.mode, base_inputs.margin_range.high, size=n)
    w = rng.triangular(base_inputs.wacc_range.low, base_inputs.wacc_range.mode, base_inputs.wacc_range.high, size=n)
    # Clamp WACC trials to stay above terminal growth so every trial is
    # meaningful (Cerebro 16.2: "Use bounded distributions").
    w = np.maximum(w, base_inputs.tv_growth + 1e-4)

    values = np.empty(n)
    for i in range(n):
        values[i] = _constant_growth_per_share(
            float(g[i]), float(m[i]), float(w[i]), base_inputs.tv_growth,
            revenue0=base_inputs.revenue0, tax_rate=base_inputs.tax_rate, roic_value=base_inputs.roic,
            years=base_inputs.years, shares=base_inputs.shares, net_debt=base_inputs.net_debt,
        )

    p10, p25, p50, p75, p90 = np.percentile(values, [10, 25, 50, 75, 90])
    return MonteCarloResult(
        p10=_ok(float(p10), "usd_per_share"),
        p25=_ok(float(p25), "usd_per_share"),
        median=_ok(float(p50), "usd_per_share"),
        p75=_ok(float(p75), "usd_per_share"),
        p90=_ok(float(p90), "usd_per_share"),
        seed=seed,
        trials=n,
    )


@register_formula(id="VAL-ENSEMBLE-044", version=_VERSION, unit="usd_per_share", inputs=["models"])
def ensemble(models: Sequence[EnsembleModelInput]) -> EnsembleResult:
    """Reliability-weighted model ensemble (Cerebro 19, VAL-ENSEMBLE-044):
    `sum(ModelValue_i * ReliabilityWeight_i) / sum(ReliabilityWeights)`.
    Null-valued models are excluded from the weighted average (and noted)
    rather than silently zeroed. Dispersion is the sample standard deviation
    of the included values."""
    valid = [(m.value.value, m.weight) for m in models if m.value.is_valid and m.weight > 0]
    if not valid:
        null = _null(NullState.NOT_SCORABLE, "usd_per_share", "NO_VALID_MODEL_VALUES")
        return EnsembleResult(value=null, dispersion=null, models=list(models))

    total_w = sum(w for _, w in valid)
    weighted = sum(v * w for v, w in valid) / total_w
    dispersion = statistics.pstdev(v for v, _ in valid) if len(valid) > 1 else 0.0
    return EnsembleResult(
        value=_ok(weighted, "usd_per_share"),
        dispersion=_ok(dispersion, "usd_per_share"),
        models=list(models),
    )
