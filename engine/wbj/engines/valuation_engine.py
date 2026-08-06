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
from typing import Any, Sequence

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
    "adjusted_present_value",
    "real_option_value",
    "sum_of_the_parts",
    "gordon_dividend_value",
    "h_model_dividend_value",
    "justified_pb",
    "justified_pe",
    "justified_ev_sales",
    "hist_zscore",
    "implied_investor_irr",
    "terminal_year_metrics",
    "exit_multiple_terminal_value",
    "implied_exit_multiple",
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
    capital`, over a 3-5 year window per Cerebro's guidance.

    `BUS-IROIC-016`'s caveat is "not meaningful for negative denominator
    change", and only the zero case was refused. A shrinking capital base
    inverts the ratio's meaning: a company whose NOPAT fell by 50 while
    its invested capital fell by 10 scored +500% incremental ROIC, and
    one that shrank on both sides scored +30%. That number feeds
    `BUS-ALLOC-029` (incremental ROIC - WACC), which carries the heaviest
    single weight in the business agent's management dimension, so a
    contracting business read as an outstanding capital allocator.
    """
    if delta_ic <= 0:
        return _null(NullState.NOT_MEANINGFUL, "pct",
                     "DELTA_IC_ZERO" if delta_ic == 0 else "DELTA_IC_NEGATIVE")
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


@register_formula(
    id="VAL-NWC-006", version=_VERSION, unit="usd",
    inputs=["current_assets", "cash", "short_term_investments",
            "current_liabilities", "short_term_debt"],
)
def non_cash_working_capital(current_assets: float | None, cash: float | None,
                             short_term_investments: float | None,
                             current_liabilities: float | None,
                             short_term_debt: float | None) -> Value:
    """Non-cash working capital (VAL-NWC-006): `Operating current assets
    excluding cash - Operating current liabilities excluding debt`.

    FORMULAS.md adds "exclude financing items", and three lines qualify:

    - cash, named in the formula itself;
    - short-term investments, because `DATA_DICTIONARY.md` reconciles
      invested capital to "debt plus equity minus **excess cash**", and
      marketable securities are excess cash parked in a current account
      rather than capital tied up in operations (AAPL FY2025: 18.8B, which
      left in would overstate the level by that much);
    - short-term debt, a financing claim on the liability side.

    What stays is operating: receivables, inventory, payables, deferred
    revenue (a customer prepayment, not borrowing).

    Sign convention: a positive level means cash tied up in operations. Its
    *change* is what `VAL-FCFF-005` subtracts and what `BUS-REINV-018` counts
    as a use of cash, so a build is positive on this basis.

    The two current-account totals are required; the three exclusions default
    to 0.0 when a filer omits the line (an absent line is a zero balance, not
    an unknown one), but a missing total is `MISSING` rather than an assumed
    zero -- a working-capital level of "zero" and one that could not be
    computed are not the same claim.
    """
    if current_assets is None or current_liabilities is None:
        return _null(NullState.MISSING, "usd", "NWC_REQUIRES_BOTH_CURRENT_TOTALS")
    operating_assets = current_assets - (cash or 0.0) - (short_term_investments or 0.0)
    operating_liabilities = current_liabilities - (short_term_debt or 0.0)
    return _ok(operating_assets - operating_liabilities, unit="usd")


def non_cash_working_capital_from_row(row: dict) -> float | None:
    """`VAL-NWC-006` off one canonical statement row, as a plain float.

    Kept so `BUS-REINV-018`'s balance-sheet fallback and this registry entry
    cannot drift apart: two implementations of one formula is two sign
    conventions waiting to disagree.
    """
    v = non_cash_working_capital(
        row.get("total_current_assets"), row.get("cash"),
        row.get("short_term_investments"),
        row.get("total_current_liabilities"), row.get("short_term_debt"),
    )
    return v.value if v.is_valid else None


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


def economic_profit_value(ic0: float, eps: Sequence[float], wacc_value: float) -> Value:
    """`InvestedCapital_0 + PV(economic profits)` for an EP series the caller
    already holds — the arithmetic of Cerebro 9, without the forecast.

    Deliberately NOT registered: `economic_profit_ev` owns VAL-EVAEV-021,
    because that id has to name the model the report publishes and the
    reconciliation tests, and that model builds its own EP series from the
    same forecast the FCFF DCF prices. Two functions claiming one id let the
    registry silently keep whichever was decorated last.

    `eps` must be ECONOMIC PROFITS (`NOPAT_t - WACC * IC_{t-1}`). Passing free
    cash flow here is what broke the cross-check: adding `ic0` to a present
    value of cash flows double-counts the capital base.
    """
    pv_eps = sum(ep / (1 + wacc_value) ** (t + 1) for t, ep in enumerate(eps))
    return _ok(ic0 + pv_eps, unit="usd")


@register_formula(
    id="VAL-EVAEV-021", version=_VERSION, unit="usd",
    inputs=["ic0", "growth", "margin", "wacc_value", "tv_growth", "revenue0",
            "tax_rate", "roic_value", "years"],
)
def economic_profit_ev(ic0: float, growth: float, margin: float, wacc_value: float,
                       tv_growth: float, revenue0: float, tax_rate: float,
                       roic_value: float, years: int) -> Value:
    """Economic-profit enterprise value, built on the SAME forecast the FCFF
    DCF prices (Cerebro 9, VAL-EVAEV-021).

    `EV = IC0 + PV(economic profits) + PV(terminal EP)`, where an economic
    profit is `NOPAT_t - WACC * IC_{t-1}` and invested capital rolls forward
    by the reinvestment the FCFF path already implies
    (`reinvestment_t = NOPAT_t - FCFF_t`).

    Cerebro 9 asks the two models to agree because "a mismatch exposes a
    modeling error". They could not agree here, and the mismatch exposed one
    in this file rather than in the assumptions: the caller was passing
    `NOPAT * (1 - reinvestment_rate) * (1+g)^t` — which is FREE CASH FLOW —
    into a function whose second argument must be economic profit, and the
    old formula then added `IC0` on top of a present value of cash flows.
    That double-counts the capital base: on NVDA-shaped inputs it read 31.6%
    above the FCFF DCF, so `FCFF_ECONOMIC_PROFIT_RECONCILIATION_FAILED` fired
    on every company and `VAL-EVAEV-021` published a number that was neither
    model.

    The terminal term is `TV_fcff - IC_N`: the FCFF terminal value prices
    everything from N+1 onward, and the economic-profit form states that same
    continuing value as capital already on the books plus the profits above
    the cost of that capital. Subtracting `IC_N` is what stops it being
    counted twice — the same mistake, one horizon later.
    """
    if tv_growth >= wacc_value:
        return _null(NullState.NOT_MEANINGFUL, "usd", "TERMINAL_GROWTH_GE_WACC")
    terminal = terminal_year_metrics(
        growth, margin, wacc_value, tv_growth, revenue0, tax_rate, roic_value, years,
    )
    fcffs = terminal["explicit_fcffs"]

    revenue, ic = revenue0, ic0
    pv_ep = 0.0
    for t, fcff in enumerate(fcffs, start=1):
        revenue *= 1 + growth
        nopat = revenue * margin * (1 - tax_rate)
        # Economic profit is measured on the capital in place at the START of
        # the year, so charge WACC before this year's reinvestment lands.
        pv_ep += (nopat - wacc_value * ic) / (1 + wacc_value) ** t
        ic += nopat - fcff
    pv_terminal = (terminal["terminal_value"] - ic) / (1 + wacc_value) ** years
    return _ok(ic0 + pv_ep + pv_terminal, unit="usd")


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


@register_formula(
    id="VAL-APV-019", version=_VERSION, unit="usd",
    inputs=["unlevered_fcf", "unlevered_cost", "debt_schedule", "tax_rate",
            "pretax_kd", "distress_probability", "distress_cost",
            "financing_side_effects"],
)
def adjusted_present_value(
    unlevered_fcf: Sequence[float], unlevered_cost: float,
    debt_schedule: Sequence[float], tax_rate: float, pretax_kd: float,
    distress_probability: float = 0.0, distress_cost: float = 0.0,
    financing_side_effects: float = 0.0, terminal_growth: float | None = None,
) -> dict[str, Any]:
    """Adjusted present value (VAL-APV-019): `Value of unlevered operations +
    PV(Tax shields) - PV(Expected distress costs) + Financing side effects`.

    DECISION_RULES.md makes it primary for "changing leverage / LBO-like" and
    names what it replaces there: "static WACC without debt path". That is the
    point of separating the pieces -- a WACC folds the tax shield into one
    discount rate and so assumes a capital structure that holds still, which
    is exactly the assumption an LBO breaks. Here the shield is valued year by
    year off the debt schedule, so a deleveraging path prices correctly.

    The shield is discounted at `pretax_kd`, not at `unlevered_cost`: its risk
    is the risk of the debt that generates it. That choice is a modelling
    assumption Cerebro does not settle, and the caller is told so rather than
    it passing silently.

    Distress is `probability * cost`, both caller-supplied: FORMULAS.md calls
    it "PV(Expected distress costs)" and registers no estimator for either
    term, so neither is invented here. Both default to zero, which is the
    honest reading of "not supplied" -- and is disclosed as such, because a
    zero distress term is an assumption, not an absence.

    Returns the components alongside the total so a reader can see which piece
    carries the value.
    """
    out: dict[str, Any] = {}
    if unlevered_cost <= 0:
        out["value"] = _null(NullState.NOT_MEANINGFUL, "usd", "APV_NONPOSITIVE_UNLEVERED_COST")
        return out
    if not unlevered_fcf:
        out["value"] = _null(NullState.MISSING, "usd", "APV_REQUIRES_UNLEVERED_FCF")
        return out
    if not 0.0 <= distress_probability <= 1.0:
        out["value"] = _null(NullState.NOT_MEANINGFUL, "usd", "DISTRESS_PROBABILITY_OUT_OF_RANGE")
        return out

    n = len(unlevered_fcf)
    pv_ops = sum(f / (1 + unlevered_cost) ** (t + 1) for t, f in enumerate(unlevered_fcf))
    if terminal_growth is not None:
        if terminal_growth >= unlevered_cost:
            out["value"] = _null(NullState.NOT_MEANINGFUL, "usd", "TERMINAL_GROWTH_GE_UNLEVERED_COST")
            return out
        tv = unlevered_fcf[-1] * (1 + terminal_growth) / (unlevered_cost - terminal_growth)
        pv_ops += tv / (1 + unlevered_cost) ** n

    # Year t's shield is that year's debt balance * tax rate * pre-tax cost of
    # debt -- the interest deduction the balance actually generates.
    pv_shield = sum(
        (d * tax_rate * pretax_kd) / (1 + pretax_kd) ** (t + 1)
        for t, d in enumerate(debt_schedule or [])
    )
    pv_distress = distress_probability * distress_cost

    out["pv_unlevered_operations"] = pv_ops
    out["pv_tax_shields"] = pv_shield
    out["pv_distress"] = pv_distress
    out["financing_side_effects"] = financing_side_effects
    out["value"] = _ok(pv_ops + pv_shield - pv_distress + financing_side_effects, unit="usd")
    return out


@register_formula(
    id="VAL-ROPT-038", version=_VERSION, unit="usd",
    inputs=["project_pv", "investment", "volatility", "years", "risk_free"],
)
def real_option_value(project_pv: float, investment: float, volatility: float,
                      years: float, risk_free: float) -> Value:
    """Real-option value (VAL-ROPT-038): the option-pricing value of a staged,
    discretionary project, "after avoiding DCF double count".

    FORMULAS.md offers two methods -- "option-pricing **or** decision-tree" --
    and this implements the first, as Black-Scholes. That is a choice between
    alternatives Cerebro leaves open, not a reading of a single stated
    formula, and it is not neutral: closed-form pricing assumes a single
    decision point and lognormal project value, where a decision tree can
    carry staged, path-dependent outcomes (a phase-II readout gating a
    phase-III spend) that this cannot. A caller whose project is genuinely
    multi-stage is better served by the decision-tree branch, which is not
    implemented here.

    Black-Scholes on the project: `S` is the PV of the project's cash flows,
    `K` the staged investment, `T` the window in which the decision can be
    taken. What it captures that a DCF cannot is the *right without the
    obligation* -- a project abandoned when it turns out badly loses only the
    option premium, and a plain NPV cannot express that asymmetry, which is
    why an out-of-the-money project still carries value here.

    FORMULAS.md scopes it hard: "only for material, separable, discretionary
    projects", and "after avoiding DCF double count". Both are the caller's to
    enforce -- a project already inside the DCF's cash flows must not be added
    again, and this function cannot see the DCF.

    Refuses on non-positive PV, investment, time or volatility. Zero
    volatility is not a degenerate option to price at intrinsic value here: a
    project with no uncertainty has no option premium, and reporting one
    would be inventing optionality the inputs deny.
    """
    import math

    if project_pv <= 0 or investment <= 0:
        return _null(NullState.NOT_MEANINGFUL, "usd", "REAL_OPTION_REQUIRES_POSITIVE_PV_AND_COST")
    if years <= 0:
        return _null(NullState.NOT_MEANINGFUL, "usd", "REAL_OPTION_REQUIRES_POSITIVE_HORIZON")
    if volatility <= 0:
        return _null(NullState.NOT_APPLICABLE, "usd", "REAL_OPTION_REQUIRES_UNCERTAINTY")

    sigma_t = volatility * math.sqrt(years)
    d1 = (math.log(project_pv / investment) + (risk_free + volatility ** 2 / 2) * years) / sigma_t
    d2 = d1 - sigma_t
    ncdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    value = project_pv * ncdf(d1) - investment * math.exp(-risk_free * years) * ncdf(d2)
    return _ok(max(value, 0.0), unit="usd")


@register_formula(
    id="VAL-CONV-039", version=_VERSION, unit="usd_per_share",
    inputs=["equity_value", "shares", "price", "convertibles", "options"],
)
def convertible_dilution(equity_value: float, shares: float, price: float,
                         convertibles: Sequence[dict] | None = None,
                         options: Sequence[dict] | None = None) -> dict[str, Any]:
    """Convertible and option dilution (VAL-CONV-039): "if-converted shares
    and debt/interest adjustment under scenario; use treasury method for
    options", "apply scenario-consistent dilution".

    Two mechanisms, and they are not interchangeable:

    - **If-converted**, for convertible debt. When conversion is in the money
      the instrument becomes equity: its shares join the count *and* its face
      value stops being a claim, so equity value rises by the face. Applying
      only the share half is the common error -- it dilutes without crediting
      the debt that disappeared.
    - **Treasury method**, for options. Exercise proceeds buy shares back at
      the market price, so net new shares are `count * (1 - strike/price)`.
      Equity value is unchanged: the proceeds are already netted out.

    "Scenario-consistent" is the reason `price` is a parameter rather than a
    fixed spot: an option out of the money in the bear case is in the money in
    the bull one, and using one price across all three would dilute scenarios
    that never triggered the conversion.

    Returns the diluted per-share value with the pieces that produced it, so
    a caller can disclose which instruments actually converted.
    """
    out: dict[str, Any] = {
        "shares_base": shares, "shares_added": 0.0,
        "equity_value_added": 0.0, "converted": [], "exercised": [],
    }
    if shares <= 0 or price <= 0:
        out["per_share"] = _null(NullState.NOT_MEANINGFUL, "usd_per_share",
                                 "DILUTION_REQUIRES_POSITIVE_SHARES_AND_PRICE")
        return out

    for c in (convertibles or []):
        conv_price = float(c.get("conversion_price") or 0.0)
        conv_shares = float(c.get("conversion_shares") or 0.0)
        face = float(c.get("face") or 0.0)
        if conv_price <= 0 or conv_shares <= 0:
            continue
        if price > conv_price:  # in the money: it converts
            out["shares_added"] += conv_shares
            out["equity_value_added"] += face
            out["converted"].append(c.get("name") or f"conv@{conv_price:g}")

    for o in (options or []):
        strike = float(o.get("strike") or 0.0)
        count = float(o.get("count") or 0.0)
        if strike <= 0 or count <= 0 or price <= strike:
            continue  # out of the money adds nothing under the treasury method
        out["shares_added"] += count * (1 - strike / price)
        out["exercised"].append(o.get("name") or f"opt@{strike:g}")

    diluted_shares = shares + out["shares_added"]
    out["shares_diluted"] = diluted_shares
    out["per_share"] = _ok((equity_value + out["equity_value_added"]) / diluted_shares,
                           unit="usd_per_share")
    return out


@register_formula(
    id="VAL-SOTP-026", version=_VERSION, unit="usd",
    inputs=["segment_values", "corporate_assets", "corporate_claims", "holding_discount"],
)
def sum_of_the_parts(segment_values: Sequence[float], corporate_assets: float,
                     corporate_claims: float, holding_discount: float = 0.0) -> Value:
    """Sum-of-the-parts equity value (VAL-SOTP-026): `sum(Segment
    enterprise/equity values) + corporate assets - corporate claims -
    holding discount_if_justified`.

    DECISION_RULES.md's matrix makes it the primary model for a multi-segment
    conglomerate, and names the failure mode it replaces: "one blended
    multiple without segment logic". FORMULAS.md states the same caution as
    "avoid applying a multiple to consolidated metrics twice" -- which is why
    the caller must value each segment separately and cannot reach this
    function with a single company-level multiple.

    `holding_discount` is a fraction of the summed value, applied only when
    the caller can justify it (Victor's own "_if_justified"); it defaults to
    zero, because an unjustified conglomerate discount is a thumb on the
    scale rather than a valuation.

    Refuses on fewer than two segments -- a one-segment SOTP is a consolidated
    valuation wearing a different name, which is the double-count the caution
    is about -- and on a discount outside 0-1.
    """
    values = [v for v in (segment_values or []) if v is not None]
    if len(values) < 2:
        return _null(NullState.NOT_APPLICABLE, "usd", "SOTP_REQUIRES_AT_LEAST_TWO_SEGMENTS")
    if not 0.0 <= holding_discount < 1.0:
        return _null(NullState.NOT_MEANINGFUL, "usd", "HOLDING_DISCOUNT_OUT_OF_RANGE")
    gross = sum(values)
    return _ok(gross * (1 - holding_discount) + corporate_assets - corporate_claims,
               unit="usd")


@register_formula(id="VAL-DDM-024", version=_VERSION, unit="usd_per_share",
                  inputs=["d0", "g", "ke"])
def gordon_dividend_value(d0: float, g: float, ke: float) -> Value:
    """Gordon dividend value (VAL-DDM-024): `Dividend_1 / (CostEquity - g)`,
    with `Dividend_1 = D0 * (1 + g)`.

    FORMULAS.md conditions it on "stable payout and growth", and
    DECISION_RULES.md's matrix makes it a *primary* model for banks and
    insurers -- the company types whose FCFF the same matrix forbids.

    Refuses on `g >= Ke` (the denominator turns zero or negative, and a
    negative per-share value is arithmetic rather than a valuation) and on a
    non-positive dividend, where the model has nothing to discount: a
    non-payer is not worth zero, it is outside this model's scope, so the
    caller must reach for another rather than read a floor here.
    """
    if d0 <= 0:
        return _null(NullState.NOT_APPLICABLE, "usd_per_share", "DDM_REQUIRES_A_DIVIDEND")
    if ke <= g:
        return _null(NullState.NOT_MEANINGFUL, "usd_per_share", "GROWTH_GE_COST_OF_EQUITY")
    return _ok(d0 * (1 + g) / (ke - g), unit="usd_per_share")


@register_formula(id="VAL-HDDM-025", version=_VERSION, unit="usd_per_share",
                  inputs=["d0", "g_short", "g_long", "half_life", "ke"])
def h_model_dividend_value(d0: float, g_short: float, g_long: float,
                           half_life: float, ke: float) -> Value:
    """H-model dividend value (VAL-HDDM-025):
    `D0*(1+gL)/(Ke-gL) + D0*H*(gS-gL)/(Ke-gL)`.

    The first term is the Gordon value at the long-run rate; the second is
    the extra value of fading from `g_short` to `g_long`, where `H` is half
    the length of the transition. FORMULAS.md scopes it to "mature transition
    companies only" -- which is the case the plain Gordon model misprices, by
    assuming today's growth continues unchanged forever.

    Refuses on `g_long >= Ke` for the same reason as VAL-DDM-024 (both terms
    share that denominator), on a non-positive dividend, and on a negative
    half-life. A `g_short` below `g_long` is allowed: the second term simply
    turns negative, which is the correct treatment of a company fading *up*
    to its long-run rate.
    """
    if d0 <= 0:
        return _null(NullState.NOT_APPLICABLE, "usd_per_share", "HDDM_REQUIRES_A_DIVIDEND")
    if ke <= g_long:
        return _null(NullState.NOT_MEANINGFUL, "usd_per_share", "LONG_GROWTH_GE_COST_OF_EQUITY")
    if half_life < 0:
        return _null(NullState.NOT_MEANINGFUL, "usd_per_share", "NEGATIVE_HALF_LIFE")
    steady = d0 * (1 + g_long) / (ke - g_long)
    transition = d0 * half_life * (g_short - g_long) / (ke - g_long)
    return _ok(steady + transition, unit="usd_per_share")


@register_formula(id="VAL-JPB-031", version=_VERSION, unit="x", inputs=["roe", "g", "ke"])
def justified_pb(roe: float, g: float, ke: float) -> Value:
    """Justified price-to-book (VAL-JPB-031): `(ROE - g) / (CostEquity - g)`.

    Reads as: a business earning exactly its cost of equity is worth book
    (ROE = Ke gives 1.0x); the premium or discount to book is the spread
    between what it earns on equity and what that equity costs.

    FORMULAS.md conditions it on "stable ROE, payout, and g < Ke". `g >= Ke`
    refuses -- the denominator turns zero or negative, and a negative
    "multiple" is arithmetic, not a valuation. A negative ROE also refuses:
    the formula would return a negative multiple that reads like a cheap one.

    This is the bank-side counterpart of `VAL-JPE-032`. DECISION_RULES.md's
    model-selection matrix lists "P/B vs ROE" as the secondary check for
    banks and insurers, which is the same statement in words.
    """
    if ke <= g:
        return _null(NullState.NOT_MEANINGFUL, "x", "GROWTH_GE_COST_OF_EQUITY")
    if roe < 0:
        return _null(NullState.NOT_MEANINGFUL, "x", "ROE_NEGATIVE")
    return _ok((roe - g) / (ke - g), unit="x")


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


def terminal_year_metrics(
    growth: float, margin: float, wacc_value: float, tv_growth: float,
    revenue0: float, tax_rate: float, roic_value: float, years: int,
) -> dict[str, Any]:
    """The terminal year of the constant-growth path, and the Gordon terminal
    value built on it.

    Extracted from `_constant_growth_per_share`, which now calls it, so
    `VAL-TVE-013`'s implied exit multiple divides the same terminal value by
    the same terminal metric the DCF actually priced. Recomputing either one
    alongside would be a second model quietly disagreeing with the first.

    Returns terminal-year revenue and EBIT (the two metrics FORMULAS.md names
    for an exit multiple, "terminal EBITDA/revenue/earnings" -- EBITDA is not
    among them because no D&A path is forecast), the explicit-period FCFFs,
    and the undiscounted terminal value.

    Raises on `tv_growth >= wacc_value`, the same refusal
    `_constant_growth_per_share` makes. The guard belongs here, with the
    division it protects: extracting the arithmetic without it left the
    public entry point dividing by zero on an input its private caller had
    already rejected.
    """
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
    return {
        "revenue": revenue,
        "ebit": revenue * margin,
        "explicit_fcffs": fcffs,
        "terminal_value": fcff_terminal / (wacc_value - tv_growth),
    }


@register_formula(
    id="VAL-TVE-013", version=_VERSION, unit="usd",
    inputs=["terminal_metric", "exit_multiple"],
)
def exit_multiple_terminal_value(terminal_metric: float, exit_multiple: float) -> Value:
    """Exit-multiple terminal value (VAL-TVE-013): `Terminal metric *
    Selected normalized exit multiple`.

    FORMULAS.md and section 6.5 both bound this to one role: "**use only as a
    cross-check**", and "the multiple must match terminal growth, margins,
    ROIC, and risk -- a current-cycle multiple cannot be copied blindly into
    perpetuity". It therefore never replaces `VAL-TVG-012` in the DCF; the
    caller prices with Gordon and reads this beside it.

    A non-positive terminal metric refuses: a multiple applied to a negative
    EBIT returns a negative "value" that is arithmetic, not a valuation.
    """
    if terminal_metric <= 0:
        return _null(NullState.NOT_MEANINGFUL, "usd", "EXIT_MULTIPLE_NEEDS_POSITIVE_METRIC")
    if exit_multiple <= 0:
        return _null(NullState.NOT_MEANINGFUL, "usd", "EXIT_MULTIPLE_NONPOSITIVE")
    return _ok(terminal_metric * exit_multiple, unit="usd")


def implied_exit_multiple(terminal_value: float, terminal_metric: float) -> Value:
    """The exit multiple the DCF's own terminal value implies.

    The valuation checklist requires it outright: "terminal-value share and
    **implied exit multiple** are shown". This is the multiple that *is*
    justified by terminal fundamentals, because it falls out of the Gordon
    value that terminal growth, margin, ROIC and WACC already produced -- so
    it is the reference any supplied multiple has to be defended against.
    """
    if terminal_metric <= 0:
        return _null(NullState.NOT_MEANINGFUL, "x", "IMPLIED_MULTIPLE_NEEDS_POSITIVE_METRIC")
    return _ok(terminal_value / terminal_metric, unit="x")


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
    terminal = terminal_year_metrics(
        growth, margin, wacc_value, tv_growth, revenue0, tax_rate, roic_value, years,
    )
    fcffs, tv = terminal["explicit_fcffs"], terminal["terminal_value"]
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


@register_formula(
    id="VAL-IRR-041", version=_VERSION, unit="pct",
    inputs=["price", "terminal_per_share", "years", "distributions"],
)
def implied_investor_irr(price: float, terminal_per_share: float, years: int,
                         distributions: Sequence[float] | None = None) -> Value:
    """Implied investor IRR (VAL-IRR-041): the discount rate at which buying
    one share at `price` today, receiving `distributions` over the holding
    period, and selling at `terminal_per_share` breaks even.

    FORMULAS.md: "IRR of purchase price, forecast distributions/buybacks *if
    modeled*, and terminal per-share value". The "if modeled" is load-bearing:
    with no dividend forecast the series is the two endpoints alone, and the
    result is a price-appreciation-only IRR that understates total return for
    any payer. The caller must disclose that -- see the note this method
    carries: "terminal value and holding period disclosed".

    Sign convention: `price` is the t=0 outflow, distributions arrive at the
    end of years 1..n, and the terminal value lands with the final one.

    Refuses rather than returning a misleading number when the arithmetic has
    no economic meaning: a non-positive price or holding period, and a total
    wipeout (terminal value and distributions both zero), whose IRR is -100%
    exactly and is better read as a null than as a rate.
    """
    if price <= 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "IRR_REQUIRES_POSITIVE_PRICE")
    if years <= 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "IRR_REQUIRES_POSITIVE_HOLDING_PERIOD")
    if terminal_per_share < 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "IRR_NEGATIVE_TERMINAL_VALUE")

    flows = list(distributions or [0.0] * years)
    if len(flows) != years:
        return _null(NullState.NOT_MEANINGFUL, "pct", "IRR_DISTRIBUTIONS_LENGTH_NE_YEARS")
    if terminal_per_share == 0 and not any(flows):
        return _null(NullState.NOT_MEANINGFUL, "pct", "IRR_TOTAL_LOSS")

    flows = list(flows)
    flows[-1] += terminal_per_share

    def npv(rate: float) -> float:
        return -price + sum(cf / (1 + rate) ** (t + 1) for t, cf in enumerate(flows))

    # Bracket just above -100% (where the discount factor blows up) to a rate
    # no equity holding period sustains.
    lo, hi = -0.9999, 10.0
    try:
        return _ok(brentq(npv, lo, hi, xtol=1e-12, rtol=1e-12), unit="pct")
    except ValueError:
        return _null(NullState.NOT_MEANINGFUL, "pct", "IRR_NO_SIGN_CHANGE_IN_BOUNDS")


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

    # `brentq` solo mira los EXTREMOS, y esta funcion no es monotona: mas
    # crecimiento exige mas reinversion, asi que el valor sube, hace pico y
    # cae. Con f(lo) y f(hi) los dos negativos y un pico positivo en medio hay
    # DOS raices y el acotado por extremos no encuentra ninguna. Se escanea el
    # rango y se resuelve en el primer tramo donde el signo cambia.
    _muestras = 64
    _xs = [lo + (hi - lo) * i / _muestras for i in range(_muestras + 1)]
    _tramo = None
    _previo = None
    for x in _xs:
        try:
            y = f_growth(x)
        except Exception:  # noqa: BLE001 -- un tramo refutado no invalida el resto
            _previo = None
            continue
        if _previo is not None and (_previo[1] < 0) != (y < 0):
            _tramo = (_previo[0], x)
            break
        _previo = (x, y)

    try:
        if _tramo is None:
            raise ValueError("sin cambio de signo en el rango")
        implied_growth = brentq(f_growth, _tramo[0], _tramo[1], xtol=1e-12, rtol=1e-12)
        converged = True
        warnings: list[str] = []
    except ValueError:
        # No hay raiz, y la razon importa: el valor modelado NO es monotono en
        # el crecimiento. Mas crecimiento exige mas reinversion, asi que pasado
        # cierto punto el flujo libre cae y con el el valor. Medido en AMD: el
        # maximo que el modelo alcanza son ~11,46 por accion y el precio son
        # 482,05. Ningun crecimiento justifica ese precio.
        #
        # Decir solo "no encontro raiz" tiraba esa conclusion a la basura. Es
        # justo la banda 0-3 que `SCORING.md` describe para esta dimension --
        # "price implies growth/returns far above evidenced capacity" -- asi
        # que se distingue de que direccion viene el fallo.
        try:
            en_lo, en_hi = f_growth(lo), f_growth(hi)
            techo = max(f_growth(g) for g in
                        (lo, 0.0, min(hi, 0.25), min(hi, 0.5), min(hi, 1.0), hi))
        except Exception:  # noqa: BLE001
            en_lo = en_hi = techo = None

        if techo is not None and techo < 0:
            motivo = ("PRICE_ABOVE_EVERY_MODELLED_VALUE: ningun crecimiento en "
                      f"[{lo:.0%}, {hi:.0%}] alcanza el precio; el maximo que el "
                      "modelo produce queda por debajo. SCORING.md: 'price implies "
                      "growth/returns far above evidenced capacity'")
        elif en_lo is not None and en_lo > 0 and en_hi is not None and en_hi > 0:
            motivo = ("PRICE_BELOW_EVERY_MODELLED_VALUE: el precio queda por "
                      "debajo del valor en todo el rango de crecimiento")
        else:
            motivo = "NO_SIGN_CHANGE_IN_GROWTH_BOUNDS"
        return ReverseDCFResult(
            # El ESTADO sigue siendo NOT_SCORABLE: no hay crecimiento
            # implicito que reportar, y cambiarlo alteraba un contrato que
            # nada necesitaba. Lo que cambia es el AVISO, que ahora dice de
            # que lado falla -- y con eso el especialista puede puntuar.
            implied_growth=_null(NullState.NOT_SCORABLE, "pct", motivo),
            implied_margin=_null(NullState.NOT_SCORABLE, "pct", motivo),
            consensus_growth_used=base_inputs.consensus_growth or 0.0,
            converged=False,
            warnings=[f"reverse_dcf: {motivo.split(':')[0]}"],
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


# ---------------------------------------------------------------------------
# Valor justo de un adaptador que reemplaza el modelo (bancos, aseguradoras,
# REITs), en UN SOLO SITIO.
#
# Se calculaba dos veces: en `specialists/valuation.py` y otra vez en
# `overlay/from_packet.py`, que deriva el margen de seguridad que consume el
# agente de riesgo. Dos calculos del mismo numero divergen, y divergieron:
# reconstruyendo el segundo a mano, Realty Income salia con -81,2% de margen
# contra el -2,2% del agente. Cuarenta veces. Con eso, riesgo habria dicho que
# la empresa esta carisima mientras valuacion decia que esta en precio, y una
# contradiccion entre dos partes del sistema no se ve como se ve un hueco.
#
# Ahora hay una funcion y dos llamadores, asi que no pueden discrepar.
# ---------------------------------------------------------------------------

def valor_justo_por_adaptador(
    *,
    adapter: str | None,
    price: float | None,
    shares: float | None,
    net_income: float | None,
    equity_now: float | None,
    equity_begin: float | None,
    cost_of_equity_value: float | None,
    dividend_per_share: float | None,
    dividend_growth: float | None,
    terminal_growth: float = 0.025,
    forecast_years: int = 5,
) -> tuple[float | None, list[str]]:
    """El valor justo por accion y los modelos que lo produjeron.

    `DECISION_RULES.md` asigna a bancos y aseguradoras renta residual / exceso
    de retorno / DDM, y a los REITs el modelo de dividendos como chequeo
    secundario; a todos les prohibe el DCF de empresa. Aqui corren esos
    modelos y se promedian los que dieron numero, que es exactamente lo que
    hace la ruta de adaptador del especialista.

    Devuelve `(None, [])` en cuanto no haya ningun modelo con datos. Nunca una
    estimacion.
    """
    candidatos: list[float] = []
    usados: list[str] = []
    ad = (adapter or "").lower()

    # Renta residual: primaria para bancos y aseguradoras. Un REIT no la usa
    # -- su matriz nombra NAV, AFFO y cap rates, y de los tres ninguno esta
    # registrado, asi que le queda el modelo de dividendos.
    if "reit" not in ad and None not in (net_income, equity_now, equity_begin,
                                         cost_of_equity_value, shares) and shares:
        ri = residual_income(net_income, cost_of_equity_value, equity_begin)
        if ri.is_valid:
            # `forecast_years` periodos, no uno: con un solo periodo el valor
            # se trunca al libro y el margen sale absurdo (JPM -158,9%).
            riv = residual_income_value(equity_now, [ri.value] * max(1, forecast_years),
                                        cost_of_equity_value)
            if riv.is_valid and riv.value > 0:
                candidatos.append(riv.value / shares)
                usados.append("residual income")

    # Modelo de dividendos: primario para REITs, secundario para bancos.
    if (isinstance(dividend_per_share, (int, float)) and dividend_per_share > 0
            and cost_of_equity_value):
        g_corto = dividend_growth if dividend_growth is not None else 0.0
        ddm = gordon_dividend_value(float(dividend_per_share), terminal_growth,
                                    cost_of_equity_value)
        if ddm.is_valid and ddm.value > 0:
            candidatos.append(ddm.value)
            usados.append("Gordon DDM")
        hddm = h_model_dividend_value(float(dividend_per_share), g_corto,
                                      terminal_growth, max(1, forecast_years) / 2.0,
                                      cost_of_equity_value)
        if hddm.is_valid and hddm.value > 0:
            candidatos.append(hddm.value)
            usados.append("H-model")

    if not candidatos:
        return None, []
    return sum(candidatos) / len(candidatos), usados


# ---------------------------------------------------------------------------
# Crecimiento base del escenario, en UN SOLO SITIO.
#
# `specialists/valuation.py` elegia el consenso de analistas cuando existe y
# caia al crecimiento fundamental si no; `overlay/from_packet.py`, que deriva
# el margen de seguridad para el agente de riesgo, usaba SIEMPRE el
# fundamental. Mismo modelo, distinto insumo -- y por eso NVDA salia con
# -437,6% de margen en un sitio y -96,0% en el otro, un 355,9% de desvio para
# la misma empresa.
# ---------------------------------------------------------------------------

def crecimiento_base(consensus_cagr: dict | None,
                     fundamental_growth: float | None) -> float | None:
    """El crecimiento del escenario base, elegido igual en todo el sistema.

    El consenso manda cuando existe: `DECISION_RULES.md` lo nombra como una de
    las cuatro referencias contra las que se mide un pronostico. El
    fundamental (`reinvestment * ROIC`) es el respaldo cuando no hay
    estimaciones publicadas.
    """
    if isinstance(consensus_cagr, dict):
        c = consensus_cagr.get("cagr")
        if isinstance(c, (int, float)):
            return float(c)
    return float(fundamental_growth) if isinstance(fundamental_growth, (int, float)) else None
