"""Valuation specialist (Task 19): VAL-NORM-001..VAL-ENSEMBLE-044
(`FORMULAS.md`) and the five weighted valuation dimensions (10 pts).

Sources of truth (`Cerebro/06_valuation_analysis/`):
- `FORMULAS.md`: the 44 registered formulas.
- `DECISION_RULES.md`: the model-selection matrix, DCF/WACC consistency
  rules, the scenario framework, reverse DCF, and the valuation verdict
  bands.
- `SCORING.md`: the five weighted dimensions and their per-dimension
  gate/cap column.
- `OUTPUT_SCHEMA.md`: `ValuationOutput`'s extension fields.

## Engine reuse (per the task-19 brief: do NOT reimplement)

Every DCF/WACC/reverse-DCF/scenario/Monte-Carlo/ensemble formula is a
direct call into `wbj.engines.valuation_engine` (Task 13) --
`nopat`/`invested_capital`/`roic`/`fundamental_growth`,
`cost_of_equity`/`synthetic_kd`/`wacc`, `equity_bridge`/`per_share`,
`economic_profit_value`/`reconciles`, `justified_pe`/`justified_ev_sales`/
`hist_zscore`, `reverse_dcf`, `scenarios`, `monte_carlo`, `ensemble`,
`margin_of_safety`. This module's own code is the *orchestration* layer:
deriving each engine call's inputs from `Packet` (+ `overlay`), assembling
the five weighted dimensions, and shaping `OUTPUT_SCHEMA.md`'s extension
fields -- not new valuation math.

The FCFF DCF itself is run through the engine's `scenarios` (whose
per-branch `_constant_growth_value` is the engine's own constant-growth
FCFF DCF) and `monte_carlo`, not the engine's list-based `dcf_value`
helper -- `dcf_value` takes an already-built explicit FCFF path, whereas
this module forecasts the path from a growth/margin/ROIC driver set, which
is exactly what `scenarios`/`monte_carlo`/`reverse_dcf` are built to
consume. `model_cross_checks.dispersion` (and its reliability-weighted
value) comes from the engine's `ensemble` (VAL-ENSEMBLE-044), not an
unweighted stdlib stdev.

## Model-selection matrix (brief's simplification of DECISION_RULES.md)

This module implements the "general non-financial" row of Cerebro's
8-row model-selection matrix (FCFF DCF + an economic-profit cross-check),
plus the bank/insurer and REIT rows through `_financial_adapter_output`
and `_reit_adapter_output`.

Any adapter left over -- one the matrix names but registers no model for,
AND any name none of `wbj.core.adapters`' three sets classifies -- returns
an `ADAPTER_MODELS_NOT_REGISTERED` mandatory flag with every dimension
`NOT_APPLICABLE`, rather than silently running FCFF/EV-EBITDA math the
matrix says to avoid for that company type (`DECISION_RULES.md`: "Do not
use a valuation model that conflicts with the company type").

The unrecognised-name half matters as much as the documented one: an
adapter spelled `bank_adapter` instead of `banks` used to fall straight
through to the conventional DCF and price a BANK by FCFF.

## Dataset coverage vs. `Packet`

`packet.facts_table` (`price`, `cash`, `diluted_shares`, `revenue`,
`total_debt`) and `packet.capital_structure` (`beta`, `market_cap`) supply
the WACC/DCF inputs a real ticker actually has; `packet.fundamentals
.annual`'s latest row supplies EBIT/tax/margin/ROIC building blocks (the
same NOPAT/invested-capital derivation `business.py`/`market.py` use
locally). `estimates.risk_free_rate` and `estimates.fmp_analyst_estimates`
(for a consensus growth read) come from `Packet.estimates`. What `Packet`
does NOT carry -- interest expense (for a real cost of debt), a peer
multiple panel (>=8 peers; `estimates.peers` is a bare 3-ticker list, not
a multiples panel), a historical own-multiple series, and any explicit
scenario/growth override -- is `overlay`-only, mirroring `financial.py`'s
`overlay["wacc"]` precedent; each gap degrades to a documented default
assumption (flagged) or `NOT_SCORABLE`, never a silent guess.

## Scenario defaults

Per the brief ("scenario growth/margin defaults derived... flagged as
assumptions, agent-overridable"): base growth defaults to
`fundamental_growth(reinvestment_rate, ROIC)` (`VAL-REINV-043`, historical
capex-implied reinvestment rate against the latest ROIC); bear/bull growth
default to half/1.5x the base rate; base margin defaults to the latest
reported operating margin, bear/bull to +/-2 points; discount rates widen
+/-100bp bear/bull. `overlay["scenarios"]` (a `{"bear": {...}, "base":
{...}, "bull": {...}}` dict of raw `ScenarioInput` fields) overrides any
or all of these per the brief's "agent-overridable" instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from wbj.core import adapters as _adapters
from wbj.core import taxes as _taxes
from wbj.core.confidence import confidence as _confidence_formula
from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension, anchor_score
from wbj.engines import valuation_engine as ve
from wbj.schemas.packet import Packet
from wbj.schemas.valuation import (
    DCFCommonInputs,
    EnsembleModelInput,
    MonteCarloInputs,
    MonteCarloRange,
    ReverseDCFInputs,
    ScenarioInput,
)
from wbj.specialists.common import (
    overlay_float,
    CategoryStats,
    apply_dimension_cap,
    JudgmentRequest,
    MetricRow,
    SecurityRef,
    SpecialistOutput,
    ValidationTestsSummary,
    status_from_coverage,
)

__all__ = [
    "AGENT_ID",
    "MAX_POINTS",
    "DIM_MULTIPLES",
    "DIM_HIST_PEER",
    "DIM_CF_YIELD",
    "DIM_FAIR_VALUE_SCENARIOS",
    "DIM_MOS",
    "DIMENSION_NAMES",
    "SUPPORTED_ADAPTER",
    "ValuationOutput",
    "verdict",
    "run",
]

_VERSION = "2.0.0"
AGENT_ID = "valuation_analysis"
MAX_POINTS = 10.0

DIM_MULTIPLES = "growth_adjusted_multiples"
DIM_HIST_PEER = "historical_and_peer_comparison"
DIM_CF_YIELD = "cash_flow_and_earnings_yield"
DIM_FAIR_VALUE_SCENARIOS = "fair_value_by_scenarios"
DIM_MOS = "margin_of_safety"

DIMENSION_NAMES = (DIM_MULTIPLES, DIM_HIST_PEER, DIM_CF_YIELD, DIM_FAIR_VALUE_SCENARIOS, DIM_MOS)

DIMENSION_MAX_POINTS: dict[str, float] = {
    DIM_MULTIPLES: 3.0, DIM_HIST_PEER: 2.0, DIM_CF_YIELD: 2.0, DIM_FAIR_VALUE_SCENARIOS: 2.0, DIM_MOS: 1.0,
}
assert sum(DIMENSION_MAX_POINTS.values()) == MAX_POINTS

SUPPORTED_ADAPTER = "default_nonfinancial"

# INDUSTRY_ADAPTERS.md draws a line this module has to respect: some
# adapters *replace* the valuation model, others only *add* to it.
#
#   Banks     "Prefer excess-return or dividend-discount valuation"
#   Insurers  "...and excess-return valuation"
#   REITs     "...NAV, and cap-rate valuation"
#   Biotech   "Use risk-adjusted NPV by program"
#
# Those four name a different primary model, and running an FCFF DCF on
# them anyway would produce a number the methodology forbids. The other
# two adapters say no such thing: SaaS "Add ARR growth, NRR, ..." and
# commodities "Normalize price/deck and margins through a cycle" both
# keep the discounted-cash-flow model and qualify its inputs. Gating on
# `!= SUPPORTED_ADAPTER` swept those two in as well, so a software
# company and an oil major were refused a valuation the document asks
# for.
MODEL_REPLACING_ADAPTERS = _adapters.MODEL_REPLACING

# "Peak earnings cannot be the base case without a cycle-normalization
# test." The DCF still runs; it carries the caveat.
CYCLE_NORMALIZED_ADAPTERS = _adapters.MODEL_NORMALIZING

MIN_PEERS = 8


# ============================================================================
# Anchor provenance: every scored metric's 0-10 interpolation scale.
# ============================================================================
#
# The valuation FORMULAS.md registers NO numeric band, so every scored 0-10
# scale is this module's calibration. The scoring *direction* for each comes
# from SCORING.md's dimension column (a discount is cheaper -> higher score,
# an extreme premium is dearer -> lower), and the interpolation magnitude is
# the engine's. AGENT.md's no-speculation rule permits a scored non-reported
# number only as an "explicitly disclosed assumption", so every scored metric
# is registered here (all MIXED) and `_anchor_disclosures()` surfaces them on
# every run -- the discipline already in business.py, market.py, technical.py
# and risk.py. The DCF/WACC/scenario/reverse-DCF outputs themselves are
# reported values, not anchor-scored, and carry no scale to disclose.
ANCHOR_PROVENANCE: dict[str, tuple[str, str]] = {
    "VAL-PEG-028": ("MIXED", 'SCORING.md Multiples dim: "Price embeds conservative growth" '
                    '(7-10) vs "growth/returns far above evidenced capacity" (0-3) -- a lower '
                    "PEG is cheaper."),
    "VAL-ZHIST-035": ("MIXED", 'SCORING.md Hist/peer dim: "Discount" (7-10) vs "Extreme premium" '
                      "(0-3) -- a lower valuation z-score against own history is cheaper."),
    "VAL-EY-029": ("MIXED", 'SCORING.md CF-yield dim: "Attractive normalized yields" (7-10) vs '
                   '"Negative/very low yields" (0-3) -- a higher earnings yield is cheaper.'),
    "VAL-FCFY-030": ("MIXED", 'SCORING.md CF-yield dim: "Attractive normalized yields" (7-10) '
                     "-- a higher FCF yield is cheaper; magnitude the engine's."),
    "VAL-MOS-040": ("MIXED", 'SCORING.md MOS dim: ">15% base MOS" (7-10), "0-15%" (4-6) -- the '
                    "15% margin-of-safety boundary is Victor's, the surrounding scale the engine's."),
}

#: Scored inputs that carry no VAL- metric row (a justified-P/E comparison, a
#: reverse-DCF plausibility read, and the price-vs-scenario fair-value score).
_SCORED_WITHOUT_ROW: dict[str, tuple[str, str]] = {
    "justified_pe_comparison (Multiples dim)": (
        "MIXED", 'SCORING.md Multiples dim: trading below the fundamental justified P/E '
        "(VAL-JPE-032) is cheaper; magnitude the engine's."),
    "reverse_dcf_plausibility (Multiples dim)": (
        "MIXED", 'DECISION_RULES.md reverse DCF: the price-implied growth below a consensus/'
        "fundamental reference is more plausible/cheaper; magnitude the engine's."),
    "fair_value_by_scenarios (Fair-value dim)": (
        "MIXED", 'SCORING.md Fair-value dim: "Price below conservative base and near bear '
        'value" (7-10) vs "Price above bull/base values" (0-3) -- the price-vs-scenario '
        "thresholds are Victor's, the discrete 1/5/8/10 mapping the engine's."),
}


def _anchor_disclosures() -> list[str]:
    """One grouped line naming every metric whose 0-10 scale this module
    calibrated (AGENT.md: a scored non-reported number must be an explicitly
    disclosed assumption). All valuation anchors are MIXED."""
    mixed = sorted(m for m, (s, _) in ANCHOR_PROVENANCE.items() if s == "MIXED")
    mixed += [k for k, (s, _) in _SCORED_WITHOUT_ROW.items() if s == "MIXED"]
    if not mixed:
        return []
    return [
        "Scoring anchors (partly derived): " + ", ".join(sorted(mixed)) + ". The valuation "
        "FORMULAS.md registers no numeric band, so each metric's scoring direction is taken "
        "from SCORING.md's dimension description (a discount scores higher, an extreme "
        "premium lower) and the 0-10 interpolation points between are this module's dated "
        "2.0.0 calibration, not a value from Cerebro."
    ]


def _ok(x: float, unit: str, **lineage: object) -> Value:
    return Value.of(x, unit=unit, evidence_class=lineage.pop("evidence_class", EvidenceClass.C), **lineage)


def _null(state: NullState, unit: str, *warnings: str) -> Value:
    return Value.null(state, unit=unit, warnings=list(warnings))


def _score_from_anchor(v: Value, anchors: list[tuple[float, float]]) -> float | None:
    if v.is_null:
        return None
    return anchor_score(v.value, anchors)


def verdict(score10: float) -> str:
    """Valuation verdict bands (`DECISION_RULES.md`, verbatim)."""
    if score10 >= 8.0:
        return "Attractive across scenarios with a defensible margin of safety"
    if score10 >= 6.0:
        return "Fair to moderately attractive"
    if score10 >= 4.0:
        return "Full valuation; depends on optimistic execution"
    return "Expensive or not supported by cash-flow/return assumptions"


# ============================================================================
# Envelope assembly
# ============================================================================


class ModelSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    primary: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)


class WaccSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: float | None = None
    components: dict[str, float | None] = Field(default_factory=dict)
    sensitivity: list[dict[str, float | None]] = Field(default_factory=list)


class ScenarioSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    probability: float | None = None
    assumptions: dict[str, float | None] = Field(default_factory=dict)
    enterprise_value: float | None = None
    equity_value: float | None = None
    per_share_value: float | None = None


class ReverseDCFSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_price: float | None = None
    implied_revenue_cagr: float | None = None
    implied_margin: float | None = None
    implied_high_growth_years: int | None = None


class ModelCrossChecks(BaseModel):
    model_config = ConfigDict(frozen=True)

    fcff: float | None = None
    economic_profit: float | None = None
    residual_income: float | None = None
    relative: float | None = None
    dispersion: float | None = None


class FairValueDistribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    p10: float | None = None
    p25: float | None = None
    median: float | None = None
    p75: float | None = None
    p90: float | None = None


class ReferenceBands(BaseModel):
    model_config = ConfigDict(frozen=True)

    bear: float | None = None
    base: float | None = None
    bull: float | None = None
    margin_of_safety_15pct: float | None = None
    margin_of_safety_25pct: float | None = None


class ValuationOutput(SpecialistOutput):
    """`valuation_analysis`'s `run()` result: the shared envelope plus
    OUTPUT_SCHEMA.md's valuation-specific extension fields."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    model_selection: ModelSelection = Field(default_factory=ModelSelection)
    normalization_reconciliation: list[str] = Field(default_factory=list)
    wacc: WaccSummary = Field(default_factory=WaccSummary)
    scenarios: list[ScenarioSummary] = Field(default_factory=list)
    reverse_dcf: ReverseDCFSummary = Field(default_factory=ReverseDCFSummary)
    model_cross_checks: ModelCrossChecks = Field(default_factory=ModelCrossChecks)
    fair_value_distribution: FairValueDistribution = Field(default_factory=FairValueDistribution)
    reference_bands: ReferenceBands = Field(default_factory=ReferenceBands)
    #: `INSTITUTIONAL_VALUATION_ENGINE.md`'s terminal-value checklist:
    #: "terminal-value share and implied exit multiple are shown". Both were
    #: reachable and neither was surfaced -- the share was never computed here
    #: at all (`ve.terminal_share` existed with no caller), and the multiple
    #: lived only inside an assumption sentence, so no consumer could read
    #: either. Section 6.6 is what makes the share load-bearing: a terminal
    #: share above 75% is the high-sensitivity warning, and a reader cannot
    #: check that against a number they were never shown.
    terminal_value_share: float | None = None
    implied_exit_multiple: float | None = None


@dataclass(frozen=True)
class _Row:
    metric_id: str
    value: Value
    score10: float | None
    confidence: float = 0.0


def _confidence_for(v: Value) -> float:
    if v.is_null:
        return 0.0
    base_by_class = {
        EvidenceClass.R: 95.0, EvidenceClass.C: 85.0, EvidenceClass.E: 60.0,
        EvidenceClass.A: 50.0, EvidenceClass.Q: 30.0,
    }
    base = base_by_class.get(v.evidence_class, 70.0) if v.evidence_class else 70.0
    base -= 15.0 * len(v.warnings)
    return max(0.0, min(100.0, base))


def _est_field(row: dict, *keys: str):
    """Primer valor no nulo entre `keys`.

    FMP renombro los campos al retirar `/api/v3/` en favor de `/stable/`:
    `estimatedRevenueAvg` -> `revenueAvg`, `estimatedEpsAvg` -> `epsAvg`. El
    codigo seguia pidiendo los viejos, asi que **todo estimado forward salia
    None en silencio** y la valuacion caia al P/E trailing sin decirlo.
    `quick.py` ya toleraba ambos; esto lo alinea.
    """
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def _next_year_estimate(estimates, as_of: str):
    """Fila de consenso del PROXIMO periodo, no la primera de la lista.

    FMP devuelve los estimados en orden DESCENDENTE (el mas lejano primero:
    para NVDA, 2031 antes que 2027). `fmp_est[0]` no era "el proximo año" sino
    el mas lejano disponible, asi que el reverse-DCF y el P/E forward
    comparaban contra un consenso a 5 años vista.
    """
    futuras = [e for e in (estimates or []) if str(e.get("date", "")) > str(as_of)]
    return min(futuras, key=lambda e: str(e["date"])) if futuras else None


def _mc_range(pessimistic: float, base: float, optimistic: float) -> MonteCarloRange:
    """A triangular range that is always ordered `low <= mode <= high`.

    The bear/bull defaults are derived from the base case (`base*0.5` and
    `base*1.5`), and `_scenario_input` applies overrides PER SCENARIO. So an
    analyst who sets only `scenarios.base.growth` — the override the module
    documents — leaves bear and bull bracketing the OLD base. Setting a base
    of 12% under a 36% consensus gave low=18%, mode=12%: numpy's
    `triangular` raises `left > mode`, the exception escaped `run()`, and
    the whole valuation category came back ERROR.

    The endpoints are taken as the extremes of the three scenarios rather
    than by position, which keeps an explicit three-way override intact
    while making the inversion unrepresentable.
    """
    return MonteCarloRange(low=min(pessimistic, base, optimistic), mode=base,
                           high=max(pessimistic, base, optimistic))


def _last_reported_date(packet) -> str:
    """The period end of the newest REPORTED fiscal year.

    The cutoff that separates consensus for periods already filed from
    consensus for periods still ahead. Callers passed
    `getattr(packet.analysis, "as_of", "")` — and `AnalysisMeta` has no
    `as_of` field, so that was always `""`, which made every estimate row
    count as forward-looking. `_next_year_estimate` then returned the
    OLDEST row: for NVDA the "next year" estimate was FY2022's $26.7bn
    against $215.9bn reported, five years stale, feeding the reverse-DCF's
    consensus comparison and the forward P/E.
    """
    rows = (packet.fundamentals or {}).get("annual") or []
    if not rows:
        return ""
    newest = rows[0]
    date = str(newest.get("date") or "")
    if date:
        return date
    # No period end reported: fall back to the fiscal year label, which
    # sorts correctly against the estimates' `YYYY-MM-DD` dates.
    year = str(newest.get("fiscalYear") or newest.get("calendarYear") or "")
    return f"{year}-12-31" if year else ""


def _consensus_revenue_cagr(estimates, revenue0: float | None, as_of: str,
                            horizon_years: int) -> dict | None:
    """Consensus revenue CAGR over the explicit forecast period.

    DATASET.md makes `forecast_drivers` — "revenue, margins, reinvestment,
    ROIC ... explicit model assumptions" — a REQUIRED input, and
    DECISION_RULES.md's reverse-DCF section names consensus as one of the
    four references a forecast is measured against. This is the packet's
    only automatic source for that input.

    Returns the CAGR to the furthest consensus year inside `horizon_years`,
    with the evidence supporting it (year, analyst count), or None when the
    packet carries no forward estimate.
    """
    if not revenue0 or revenue0 <= 0 or horizon_years <= 0:
        return None
    forward = []
    for row in (estimates or []):
        date = str(row.get("date", ""))
        if date <= str(as_of):
            continue
        revenue = _est_field(row, "revenueAvg", "estimatedRevenueAvg")
        if revenue is not None and revenue > 0:
            forward.append((date, revenue, row))
    if not forward:
        return None
    forward.sort()
    # One year per consensus period, capped at the explicit horizon: a CAGR
    # taken to a year beyond the period the DCF actually forecasts would
    # price growth the model never applies.
    idx = min(len(forward), horizon_years) - 1
    date, revenue, row = forward[idx]
    years = idx + 1

    def _cagr(target: float | None) -> float | None:
        if target is None or target <= 0:
            return None
        return (target / revenue0) ** (1.0 / years) - 1.0

    # The analysts' own low/high, which is a SOURCED dispersion. Bear and
    # bull used to be `base*0.5` and `base*1.5`: multipliers calibrated for
    # a small trailing-ratio growth, and nonsense on a large one — PLTR's
    # bull came out at 109%, or 40x revenue in five years, an assumption
    # nobody made. DECISION_RULES.md's scenario framework asks each
    # scenario to "separately define" its revenue growth path; this is the
    # packet's only sourced way to do that.
    return {
        "cagr": _cagr(revenue),
        "cagr_low": _cagr(_est_field(row, "revenueLow", "estimatedRevenueLow")),
        "cagr_high": _cagr(_est_field(row, "revenueHigh", "estimatedRevenueHigh")),
        "years": years,
        "to_date": date,
        "to_revenue": revenue,
        "analysts": _est_field(row, "numAnalystsRevenue", "numberAnalystEstimatedRevenue"),
    }


def _num(row: dict, key: str) -> float | None:
    x = row.get(key)
    return float(x) if isinstance(x, (int, float)) else None


def _annual_rows(packet: Packet) -> list[dict]:
    rows = packet.fundamentals.get("annual") or []
    return list(reversed(rows))  # ascending, per DATASET.md


def _fact(packet: Packet, key: str) -> float | None:
    v = packet.facts_table.get(key)
    return v.value if v is not None and v.is_valid else None


#: DECISION_RULES.md's model-selection matrix, quoted per adapter: what it
#: assigns, what it names as the secondary check, and what it bars. Every model
#: on the primary side is a name the matrix uses and that FORMULAS.md never
#: registers -- no id, no formula text, no inputs, no validation test -- which
#: is why they are named here rather than implemented.
_MATRIX_MODELS: dict[str, dict[str, list[str]]] = {
    "reits": {
        "primary": ["NAV", "AFFO_DCF", "CAP_RATES"],
        "secondary": ["P_AFFO", "DIVIDEND_MODEL"],
        "avoid": ["STANDARD_EPS_FCFF_WITHOUT_ADAPTER"],
    },
    "biotech": {
        "primary": ["RISK_ADJUSTED_NPV", "DECISION_TREE"],
        "secondary": ["CASH_RUNWAY", "REAL_OPTIONS"],
        "avoid": ["PE", "ORDINARY_TERMINAL_VALUE_DCF"],
    },
}


def _reit_adapter_output(packet: Packet, overlay: dict[str, Any]) -> ValuationOutput:
    """A REIT, on the two secondary checks its matrix row names.

    `DECISION_RULES.md`: "REIT | NAV, AFFO DCF, cap rates | **P/AFFO, dividend
    model** | Standard EPS/FCFF without adapter". The primary three stay
    unregistered and unbuilt; the secondary two are reachable, and reaching
    them beats reporting nothing:

    - **dividend model** -- VAL-DDM-024/VAL-HDDM-025, registered formulas over
      dividends REITs tag in XBRL, so it runs on tier-1 data with no analyst
      input at all;
    - **P/AFFO** -- the ratio is unambiguous once AFFO is given. What is
      ambiguous is AFFO itself, so it is not computed here. The issuer already
      published one under a definition it disclosed; the analyst transcribes
      that with its source, which makes it evidence class R (reported) rather
      than a definition this module picked.

    Why the transcription matters: FFO for Realty Income lands anywhere from
    3,405M to 3,897M depending on whether real-estate impairments are added
    back and which depreciation line is treated as real-estate depreciation --
    a 14% spread across three defensible readings, on the same filing. That
    spread would flow straight into P/AFFO and into the category score, which
    makes choosing among them a scoring decision, not an implementation one.

    A figure without `ffo_source` is dropped for the same reason a CIK override
    without its filing is: DATA_POLICY.md requires a stable locator, and a
    non-GAAP number with no stated definition is not reported evidence.

    The category is still INCOMPLETE -- the matrix's *primary* models remain
    unregistered, and secondary checks do not substitute for them.
    """
    annual = _annual_rows(packet)
    latest = annual[-1] if annual else {}
    assumptions: list[str] = []
    rows: list[_Row] = []

    def add(metric_id: str, v: Value, score10: float | None) -> None:
        rows.append(_Row(metric_id=metric_id, value=v, score10=score10,
                         confidence=_confidence_for(v)))

    price = _fact(packet, "price")
    shares = _fact(packet, "diluted_shares") or _num(latest, "diluted_shares")
    rf = overlay.get("risk_free_rate", (packet.estimates or {}).get("risk_free_rate"))
    beta = overlay.get("beta", packet.capital_structure.get("beta"))
    erp = overlay_float(overlay, "erp", 0.045)
    ke_v = (ve.cost_of_equity(float(rf), float(beta), erp)
            if rf is not None and beta is not None
            else _null(NullState.MISSING, "pct", "COST_OF_EQUITY_INPUTS_UNAVAILABLE"))

    # --- secondary check 1: the dividend model (registered, tier-1 data) ---
    d0 = overlay.get("dividend_per_share")
    dps_history = overlay.get("dividends_per_share_history") or []
    div_growth, stability_note = _dividend_growth(dps_history)
    ddm_v = _null(NullState.MISSING, "usd_per_share", "DDM_INPUTS_UNAVAILABLE")
    hddm_v = _null(NullState.MISSING, "usd_per_share", "HDDM_INPUTS_UNAVAILABLE")
    if isinstance(d0, (int, float)) and ke_v.is_valid:
        g_short = div_growth if div_growth is not None else 0.0
        tv_growth = overlay_float(overlay, "tv_growth", 0.025)
        years = int(overlay.get("forecast_years", 5))
        # Gordon's `g` is perpetual, so DECISION_RULES.md rule 3 governs it:
        # terminal growth must sit below the cost of equity and match long-run
        # nominal economic growth. `g_short` is the dividend series' own CAGR
        # -- a near-term measurement. VAL-HDDM-025 below is the model that
        # carries it, fading to the same long-run rate.
        ddm_v = ve.gordon_dividend_value(float(d0), tv_growth, ke_v.value)
        hddm_v = ve.h_model_dividend_value(float(d0), g_short, tv_growth,
                                           years / 2.0, ke_v.value)
        assumptions.append(
            f"Dividend model (the matrix's secondary check for a REIT): D0={float(d0):,.2f}/share "
            f"({overlay.get('dividend_tag', 'XBRL')}), growth {g_short:.2%}, cost of equity "
            f"{ke_v.value:.2%}. Tier-1 data, no analyst input. " + stability_note
        )
    add("VAL-DDM-024", ddm_v, None)
    add("VAL-HDDM-025", hddm_v, None)

    # --- secondary check 2: P/AFFO, off a transcribed reported figure ---
    affo = overlay.get("affo_reported")
    ffo = overlay.get("ffo_reported")
    source = overlay.get("ffo_source")
    p_affo = None
    if (affo or ffo) and not source:
        assumptions.append(
            "ffo_reported/affo_reported supplied without `ffo_source` and therefore ignored. "
            "DATA_POLICY.md requires a stable locator, and a non-GAAP figure with no stated "
            "definition is not reported evidence -- FFO alone spans ~14% across defensible "
            "readings of the same filing."
        )
    elif affo and source and price and shares:
        per_share = float(affo) / shares
        p_affo = price / per_share if per_share else None
        if p_affo:
            assumptions.append(
                f"P/AFFO = {p_affo:,.1f}x on AFFO {float(affo):,.0f} ({per_share:,.2f}/share) "
                f"against a {price:,.2f} price. AFFO is the issuer's own reported figure, "
                f"transcribed from: {source}. Evidence class R -- this module does not compute "
                "FFO or AFFO, because no FORMULAS.md registers either and the definition is "
                "the analyst's to state, not this code's to choose."
            )
    if ffo and source and shares:
        assumptions.append(
            f"FFO reported {float(ffo):,.0f} ({float(ffo) / shares:,.2f}/share), from: {source}.")

    # --- NAV inputs: loaded, cited and tiered, but not combined ---
    # DECISION_RULES.md makes NAV a *primary* model for a REIT and no
    # FORMULAS.md registers it, so the pieces are carried and the arithmetic
    # is not performed. The tier rule is Victor's own, lifted from the TAM:
    # "a `tam` without both `tam_source` and a 1-4 `tam_source_tier` is
    # REJECTED", tier 5 being "unattributed ... 0; not scorable".
    cap_rate = overlay.get("cap_rate")
    cap_tier = overlay.get("cap_rate_source_tier")
    noi = overlay.get("noi_reported")
    if cap_rate is not None:
        if not overlay.get("cap_rate_source") or cap_tier not in (1, 2, 3, 4):
            assumptions.append(
                "cap_rate supplied without `cap_rate_source` and a 1-4 `cap_rate_source_tier`, "
                "and therefore rejected. DECISION_RULES.md's source-quality tiers rate an "
                "unattributed figure 0 and not scorable; the same rule the TAM is held to."
            )
            cap_rate = None
        else:
            assumptions.append(
                f"Cap rate {float(cap_rate):.2%} (tier {cap_tier}), from: "
                f"{overlay['cap_rate_source']}. NOTE: an issuer's published cap rate is on "
                "what it TRANSACTED -- Realty Income's Q1 2026 acquisitions closed at 6.7% and "
                "its dispositions at 7.2%, the latter skewed by 78 vacant properties among 97 "
                "sold. Applying either to the whole portfolio is an analyst judgement about "
                "comparability, not a reported fact about the portfolio."
            )
    if noi and overlay.get("noi_source"):
        assumptions.append(f"NOI reported {float(noi):,.0f}, from: {overlay['noi_source']}.")
    if cap_rate and noi:
        assumptions.append(
            "NAV inputs are complete (NOI, cap rate with tier, net debt from the packet) but "
            "NAV is NOT computed: no FORMULAS.md registers it, and the arithmetic hides three "
            "unmade choices -- which NOI (the issuer's own is 'Annualized Pro Forma Cash NOI', "
            "already a variant), which cap rate applies to a portfolio when the published one "
            "covers a quarter's transactions, and whether debt enters at book or market. Each "
            "moves the answer materially, which makes them scoring decisions."
        )
    sup = overlay.get("reit_supplement")
    if sup:
        docs = ", ".join(f"{c['name']} [{c['match']}]" for c in sup.get("candidates", [])[:3])
        assumptions.append(
            f"Quarterly supplement located: 8-K {sup['accession']} filed {sup['filing_date']} "
            f"-- {docs}. That is where the issuer states FFO, AFFO, NOI and its cap rates. "
            "SOURCE_HIERARCHY.md places it at tier 2 ('official issuer filing exhibit') or "
            "tier 4 ('official earnings release and investor presentation'), not tier 1. "
            "Figures are transcribed from it, not parsed: the document is a laid-out rendering "
            "whose tables carry no column structure."
        )

    # --- dimensions ---
    mos_v = _null(NullState.MISSING, "pct", "MOS_INPUTS_UNAVAILABLE")
    candidates = [v for v in (ddm_v.value if ddm_v.is_valid else None,
                              hddm_v.value if hddm_v.is_valid else None) if v is not None]
    if candidates and price is not None:
        mos_v = ve.margin_of_safety(sum(candidates) / len(candidates), price)
    add("VAL-MOS-040", mos_v, None)
    mos_score = _score_from_anchor(mos_v, [(-0.25, 0), (0.0, 4), (0.15, 7), (0.30, 10)])
    scored = [(1.0, Value.of(mos_score, unit="score"))] if mos_score is not None else []

    # El modelo de dividendos es un chequeo que la matriz nombra para REITs, y
    # corre sobre el tag de dividendos en XBRL sin pedirle nada a nadie. Si
    # produjo un valor justo -- y lo produjo, porque de ahi sale el margen de
    # seguridad -- entonces `fair_value_by_scenarios` NO es inaplicable: es la
    # dimension que ese valor llena. Marcarla inaplicable teniendo el numero
    # calculado escondia dos puntos de la categoria detras de una etiqueta.
    na = Value.null(NullState.NOT_APPLICABLE, unit="score",
                    warnings=["NOT_APPLICABLE_UNDER_REITS"])
    # P/AFFO es EL multiplo de un REIT: INDUSTRY_ADAPTERS.md dice "replace EPS
    # with FFO/AFFO", asi que el hueco no es que el multiplo no aplique, es que
    # falta AFFO. Y falta por una razon escrita arriba: su definicion varia un
    # 14% entre lecturas defendibles del mismo filing, asi que la transcribe un
    # analista con su fuente. Eso es NOT_SCORABLE -- cuenta en contra y se ve --
    # y no NOT_APPLICABLE, que lo sacaria del denominador y lo escondería.
    sin_affo = Value.null(NullState.NOT_SCORABLE, unit="score",
                          warnings=["P_AFFO_NEEDS_ISSUER_AFFO_IN_ENTRADAS"])
    por_dimension = {
        DIM_MOS: scored,
        DIM_FAIR_VALUE_SCENARIOS: ([(1.0, Value.of(mos_score, unit="score"))]
                                   if mos_score is not None else [(1.0, sin_affo)]),
        # Multiplos e historico: el de EPS no aplica (queda reemplazado), el de
        # AFFO falta. Los dos estados, cada uno donde corresponde.
        DIM_MULTIPLES: [(0.5, na), (0.5, sin_affo)],
        DIM_HIST_PEER: [(1.0, sin_affo)],
        DIM_CF_YIELD: [(0.5, na), (0.5, sin_affo)],
    }
    dims = [Dimension(name=n, max_points=DIMENSION_MAX_POINTS[n],
                      metric_scores=por_dimension[n])
            for n in DIMENSION_NAMES]
    awarded = Category(name="valuation", max_points=MAX_POINTS, dimensions=dims).points()
    # `MISSING_DATA_POLICY.md`: peso VALIDO / peso APLICABLE. Antes se dividia
    # entre `MAX_POINTS`, los 10 puntos completos, asi que O salia en 0,100.
    _apl = sum(d.max_points * d.applicable_weight() for d in dims)
    coverage = (sum(d.max_points * d.valid_weight() for d in dims) / _apl) if _apl else 0.0

    assumptions.append(
        "industry_adapter='reits': DECISION_RULES.md assigns NAV, AFFO DCF and cap rates as "
        "the PRIMARY models, and none is registered in any FORMULAS.md -- no id, no formula, "
        "no inputs, no validation test. What runs here are the row's two secondary checks "
        "(P/AFFO and the dividend model). A secondary check does not substitute for a primary "
        f"model, so valid dimension weight is {coverage:.0%} and SCORING_ENGINE.md's INCOMPLETE "
        "applies: displayable, but it cannot pass a profile gate. NAV and cap rates stay "
        "unreachable for a different reason than FFO -- a cap rate is in no filing at all."
    )

    return ValuationOutput(
        agent_id=AGENT_ID, status="INCOMPLETE",
        security=SecurityRef(ticker=packet.security.ticker, exchange=packet.security.exchange,
                             currency=packet.security.reporting_currency),
        knowledge_timestamp=packet.analysis.knowledge_timestamp,
        category=CategoryStats(max_points=MAX_POINTS, awarded_points=awarded,
                               score_10=10.0 * awarded / MAX_POINTS,
                               confidence=_category_confidence(coverage, packet)),
        verdict=verdict(10.0 * awarded / MAX_POINTS), coverage=coverage, dimensions=dims,
        metrics=[MetricRow.from_value(r.metric_id, r.value, formula_id=r.metric_id,
                                      formula_version=_VERSION,
                                      score=(r.score10 if r.score10 is not None else "NOT_SCORABLE"),
                                      confidence=r.confidence) for r in rows],
        mandatory_flags=["ADAPTER_REIT_SECONDARY_CHECKS_ONLY",
                         "ADAPTER_PRIMARY_MODELS_NOT_REGISTERED",
                         "CATEGORY_INCOMPLETE_GATE_INELIGIBLE"],
        assumptions=assumptions, judgment_requests=[],
        source_lineage=["packet.fundamentals.annual", "packet.facts_table",
                        "overlay.dividends_per_share_history", "Entradas/<TICKER>.json"],
        validation_tests=ValidationTestsSummary(passed=0, failed=0, warnings=1),
        model_selection=ModelSelection(
            primary=[], rejected=["FCFF_DCF", "STANDARD_EPS", "EV_EBITDA"]),
        wacc=WaccSummary(value=None, components={
            "risk_free_rate": float(rf) if rf is not None else None,
            "beta": float(beta) if beta is not None else None,
            "erp": erp,
            "cost_of_equity": ke_v.value if ke_v.is_valid else None,
        }),
    )


def _unsupported_adapter_output(packet: Packet) -> ValuationOutput:
    """A company type whose primary models Cerebro names but never registers.

    `MISSING_DATA_POLICY.md` step 1 is the rule this follows: "is the metric
    applicable? If no, use `NOT_APPLICABLE` and invoke the industry adapter."
    Each dimension therefore carries an explicit `NOT_APPLICABLE` rather than
    an empty score list -- an absent dimension and one that does not apply
    read the same from outside, and only the second is true here.

    The status is `INCOMPLETE`, which is `SCORING_ENGINE.md`'s own word for
    valid dimension weight below 70% ("may be displayed but cannot pass a
    profile gate"), not `ERROR`. Nothing failed: FCFF and EV/EBITDA are barred
    for this company type, and the models that replace them
    (`_MATRIX_MODELS`) exist in the matrix as names only. Reporting ERROR made
    a documented methodology gap look like a broken run, and the two call for
    completely different actions -- one is a question for Victor, the other a
    bug report.

    `awarded_points` stays 0.0 because nothing was scored, and `is_unscored()`
    already renders that as N/S rather than as a zero earned on merit.
    """
    adapter = packet.analysis.industry_adapter or ""
    models = _MATRIX_MODELS.get(adapter, {"primary": [], "secondary": [], "avoid": []})
    na = Value.null(NullState.NOT_APPLICABLE, unit="score",
                    warnings=[f"NOT_APPLICABLE_UNDER_{adapter.upper() or 'ADAPTER'}"])
    dims = [Dimension(name=n, max_points=DIMENSION_MAX_POINTS[n],
                      metric_scores=[(1.0, na)])
            for n in DIMENSION_NAMES]
    return ValuationOutput(
        agent_id=AGENT_ID, status="INCOMPLETE",
        security=SecurityRef(ticker=packet.security.ticker, exchange=packet.security.exchange, currency=packet.security.reporting_currency),
        knowledge_timestamp=packet.analysis.knowledge_timestamp,
        category=CategoryStats(max_points=MAX_POINTS, awarded_points=0.0, score_10=0.0, confidence=_category_confidence(0.0, packet)),
        verdict=verdict(0.0), coverage=0.0, dimensions=dims, metrics=[],
        mandatory_flags=["ADAPTER_MODELS_NOT_REGISTERED",
                         "CATEGORY_INCOMPLETE_GATE_INELIGIBLE"],
        assumptions=[
            f"industry_adapter={adapter!r}: DECISION_RULES.md's model-selection matrix "
            f"assigns {', '.join(models['primary']) or 'no implemented model'} as primary and "
            f"{', '.join(models['secondary']) or 'none'} as the secondary check, and bars "
            f"{', '.join(models['avoid']) or 'the default models'}. Every primary model there "
            "is a NAME in the matrix and in INDUSTRY_ADAPTERS.md, with no entry in any "
            "FORMULAS.md: no id, no formula text, no required inputs, no validation test. "
            "There is nothing to run in place of the barred DCF, and writing one would be "
            "choosing a definition Cerebro has not made (FFO alone differs between the NAREIT "
            "definition and an adjusted one). This is a methodology gap to close upstream, not "
            "a data gap an analyst can fill in Entradas/<TICKER>.json.",
            "SCORING_ENGINE.md: every dimension is NOT_APPLICABLE, so valid dimension weight "
            "is 0% and the category is INCOMPLETE -- displayable, but it cannot pass a profile "
            "gate. The 0.0 points are 'nothing was scored', which `is_unscored()` renders as "
            "N/S, not a zero earned on merit.",
        ],
        judgment_requests=[], source_lineage=["packet.fundamentals.annual", "packet.facts_table"],
        validation_tests=ValidationTestsSummary(passed=0, failed=0, warnings=1),
        model_selection=ModelSelection(primary=[], rejected=["FCFF_DCF", "ECONOMIC_PROFIT"]),
    )


#: Adapters whose primary models this module now implements. INDUSTRY_ADAPTERS.md
#: for banks: "prefer excess-return or dividend-discount valuation"; for insurers:
#: "excess-return valuation". Both are in hand (VAL-RIV-023 and VAL-DDM-024/025),
#: so refusing these two outright is no longer the honest answer.
#:
#: REITs and biotech stay refused: their primary models (NAV, AFFO DCF, cap
#: rates; risk-adjusted NPV by program) are not implemented and FORMULAS.md does
#: not register them, so there is nothing to run in place of the barred DCF.
FINANCIAL_ADAPTERS = frozenset({"banks", "insurers"})


def _dividend_growth(history: list[float]) -> tuple[float | None, str]:
    """CAGR of a dividend series, plus what it hides.

    FORMULAS.md admits VAL-DDM-024/VAL-HDDM-025 "only for stable payout and
    growth", so measuring the rate is not enough -- whether the series is
    stable is part of the input. JPM's reported history runs 0.20 -> 5.80,
    which is a 23.42% CAGR, but the second step is 0.20 -> 1.00: a recovery
    from the post-2009 cut, not a growth rate anything can compound at. Fed
    into Gordon it exceeds any plausible cost of equity and the model refuses
    -- correct, but for a reason the reader cannot see from "23.42%" alone.

    The year-over-year spread is reported as fact rather than judged against a
    threshold: Cerebro registers no stability cutoff, so the number is shown
    and the condition named, not silently applied.
    """
    if len(history) < 2 or history[0] <= 0:
        return None, ""
    n = len(history) - 1
    cagr = (history[-1] / history[0]) ** (1.0 / n) - 1.0
    yoy = [history[i] / history[i - 1] - 1.0
           for i in range(1, len(history)) if history[i - 1] > 0]
    if not yoy:
        return cagr, ""
    note = (f"Year-over-year dividend growth ranges {min(yoy):.1%} to {max(yoy):.1%} "
            f"across the {n} reported steps. FORMULAS.md admits these models only for "
            "stable payout and growth; a CAGR measured across a recovery compounds a "
            "one-off step as if it were a rate.")
    return cagr, note


def _financial_adapter_output(packet: Packet, overlay: dict[str, Any]) -> ValuationOutput:
    """Valuation for a bank or insurer, on the models the matrix assigns it.

    DECISION_RULES.md's model-selection matrix, "Bank / insurer" row: primary
    residual income / excess return / DDM, secondary P/B vs ROE, avoid
    enterprise DCF and EV/EBITDA. INDUSTRY_ADAPTERS.md says the same in prose
    and adds "do not use ... conventional FCFF".

    Every FCFF-derived dimension is therefore `NOT_APPLICABLE` rather than
    zero -- SCORING_ENGINE.md is explicit that missing evidence is not a 5/10,
    and a barred model is not a failed one. What follows from that is Victor's
    own rule, not a scheme invented here: "a category is INCOMPLETE when valid
    dimension weight is below 70%", and "an incomplete category may be
    displayed but cannot pass a profile gate". A bank now gets a real equity
    value and an honest, gate-ineligible category, instead of a blank refusal.
    """
    annual = _annual_rows(packet)
    latest = annual[-1] if annual else {}
    prior = annual[-2] if len(annual) >= 2 else {}
    assumptions: list[str] = []
    rows: list[_Row] = []

    def add(metric_id: str, v: Value, score10: float | None) -> None:
        rows.append(_Row(metric_id=metric_id, value=v, score10=score10,
                         confidence=_confidence_for(v)))

    price = _fact(packet, "price")
    shares = _fact(packet, "diluted_shares") or _num(latest, "diluted_shares")
    net_income = _num(latest, "net_income")
    equity_now = _num(latest, "total_equity")
    equity_begin = _num(prior, "total_equity") or equity_now

    # Cost of equity: CAPM, the same inputs the default path uses. No WACC --
    # a bank's liabilities are its raw material, not its financing.
    rf = overlay.get("risk_free_rate", (packet.estimates or {}).get("risk_free_rate"))
    beta = overlay.get("beta", packet.capital_structure.get("beta"))
    erp = overlay_float(overlay, "erp", 0.045)
    ke_v = (ve.cost_of_equity(float(rf), float(beta), erp)
            if rf is not None and beta is not None
            else _null(NullState.MISSING, "pct", "COST_OF_EQUITY_INPUTS_UNAVAILABLE"))

    roe = (net_income / equity_begin
           if net_income is not None and equity_begin not in (None, 0) else None)

    # ---- VAL-RI-022 / VAL-RIV-023: residual income (excess return) ----
    ri_v = _null(NullState.MISSING, "usd", "RESIDUAL_INCOME_INPUTS_UNAVAILABLE")
    riv_v = _null(NullState.MISSING, "usd", "RESIDUAL_INCOME_VALUE_INPUTS_UNAVAILABLE")
    riv_per_share = None
    if net_income is not None and equity_begin not in (None, 0) and ke_v.is_valid:
        ri_v = ve.residual_income(net_income, ke_v.value, equity_begin)
        years = int(overlay.get("forecast_years", 5))
        g = overlay_float(overlay, "residual_income_growth", 0.0)
        if ri_v.is_valid and equity_now is not None:
            ris = [ri_v.value * (1 + g) ** (t + 1) for t in range(years)]
            riv_v = ve.residual_income_value(equity_now, ris, ke_v.value)
            if riv_v.is_valid and shares:
                riv_per_share = riv_v.value / shares
            assumptions.append(
                f"VAL-RIV-023: {years} years of residual income held flat at the latest "
                f"observation ({ri_v.value:,.0f}) grown {g:.1%} a year, on book equity "
                f"{equity_now:,.0f} and a {ke_v.value:.2%} cost of equity. FORMULAS.md warns "
                "that clean-surplus violations require adjustment; none is applied here, so "
                "the value assumes book equity moves only through earnings and dividends."
            )
    add("VAL-RI-022", ri_v, None)
    add("VAL-RIV-023", riv_v, None)

    # ---- VAL-DDM-024 / VAL-HDDM-025 ----
    # The long-run rate is read once, outside the dividend branch: VAL-JPB-031
    # below is also a stable-growth model and needs it whether or not this
    # company pays a dividend.
    tv_growth = overlay_float(overlay, "tv_growth", 0.025)
    d0 = overlay.get("dividend_per_share")
    dps_history = overlay.get("dividends_per_share_history") or []
    div_growth, stability_note = _dividend_growth(dps_history)
    ddm_v = _null(NullState.MISSING, "usd_per_share", "DDM_INPUTS_UNAVAILABLE")
    hddm_v = _null(NullState.MISSING, "usd_per_share", "HDDM_INPUTS_UNAVAILABLE")
    if isinstance(d0, (int, float)) and ke_v.is_valid:
        g_short = div_growth if div_growth is not None else 0.0
        years = int(overlay.get("forecast_years", 5))
        # Gordon's `g` is perpetual, so DECISION_RULES.md rule 3 governs it:
        # terminal growth must sit below the cost of equity and match long-run
        # nominal economic growth. `g_short` is the dividend series' own CAGR
        # -- a near-term measurement. VAL-HDDM-025 below is the model that
        # carries it, fading to the same long-run rate.
        ddm_v = ve.gordon_dividend_value(float(d0), tv_growth, ke_v.value)
        hddm_v = ve.h_model_dividend_value(
            float(d0), g_short, tv_growth, years / 2.0, ke_v.value)
        assumptions.append(
            f"VAL-DDM-024/VAL-HDDM-025: D0={float(d0):,.2f}/share "
            f"({overlay.get('dividend_tag', 'XBRL')}), growth {g_short:.2%} measured over "
            f"{max(len(dps_history) - 1, 0)} reported years, cost of equity {ke_v.value:.2%}. "
            "INDUSTRY_ADAPTERS.md names dividend-discount valuation a preferred model here. "
            + stability_note
        )
    add("VAL-DDM-024", ddm_v, None)
    add("VAL-HDDM-025", hddm_v, None)

    # ---- VAL-JPB-031: the matrix's secondary check, "P/B vs ROE" ----
    # Stable-growth `g` (INSTITUTIONAL_VALUATION_ENGINE.md:387). An analyst-
    # supplied `book_growth` still wins; absent one the long-run rate is the
    # right default, not a second hardcoded number that can drift from it.
    jpb_v = (ve.justified_pb(roe, overlay_float(overlay, "book_growth", tv_growth), ke_v.value)
             if roe is not None and ke_v.is_valid
             else _null(NullState.MISSING, "x", "JUSTIFIED_PB_INPUTS_UNAVAILABLE"))
    add("VAL-JPB-031", jpb_v, None)

    # ---- VAL-JPE-032: P/E justificado, y el múltiplo contra él ----
    # `SCORING.md` da 3 puntos a la dimensión de múltiplos y nombra cuatro
    # entradas: VAL-PEG-028, JPE-032, JEVS-033 y el DCF inverso. El adaptador
    # prohíbe las que van sobre ENTERPRISE VALUE —EV/Sales y el DCF inverso,
    # que descuenta flujos de la empresa— porque en un banco la deuda es
    # materia prima y el enterprise value no significa nada. Pero el P/E
    # justificado sale de ROE, crecimiento y costo de capital: los tres
    # existen aquí y ninguno está prohibido.
    #
    # La dimensión se estaba dejando vacía entera. Vacía no es lo mismo que
    # inaplicable: una dimensión ausente y una que no aplica se leen igual
    # desde fuera, y sólo la segunda era verdad para dos de las cuatro
    # entradas.
    jpe_v = (ve.justified_pe(tv_growth, roe, ke_v.value)
             if roe is not None and ke_v.is_valid
             else _null(NullState.MISSING, "", "JUSTIFIED_PE_INPUTS_UNAVAILABLE"))
    add("VAL-JPE-032", jpe_v, None)

    eps_banco = (net_income / shares
                 if net_income is not None and shares not in (None, 0) else None)
    pe_actual = (_ok(price / eps_banco, unit="ratio")
                 if price and eps_banco and eps_banco > 0
                 else _null(NullState.MISSING, "ratio", "TRAILING_PE_UNAVAILABLE"))
    pe_vs_jpe = _null(NullState.MISSING, "pct", "JPE_COMPARISON_UNAVAILABLE")
    if jpe_v.is_valid and pe_actual.is_valid and jpe_v.value != 0:
        pe_vs_jpe = _ok((pe_actual.value - jpe_v.value) / jpe_v.value, unit="pct")
    # Mismas anclas que la ruta por defecto: cotizar por debajo del P/E que
    # justifican sus propios fundamentales puntúa alto.
    multiplos_score = _score_from_anchor(
        pe_vs_jpe, [(0.50, 0), (0.15, 4), (0.0, 7), (-0.15, 10)])
    if pe_vs_jpe.is_valid:
        assumptions.append(
            f"VAL-JPE-032: el P/E justificado por ROE {roe:.1%}, crecimiento "
            f"{tv_growth:.1%} y costo de capital {ke_v.value:.1%} es "
            f"{jpe_v.value:,.1f}x; el mercado paga {pe_actual.value:,.1f}x, un "
            f"{pe_vs_jpe.value:+.1%}. VAL-JEVS-033 y el DCF inverso quedan "
            "NOT_APPLICABLE: los dos se apoyan en enterprise value, que en un "
            "banco no significa nada porque su deuda es materia prima."
        )

    # ---- VAL-EY-029: rendimiento de utilidades ----
    # `SCORING.md` da 2 puntos a la dimensión de rendimientos y nombra dos
    # entradas: VAL-EY-029 (utilidades/precio) y VAL-FCFY-030 (flujo libre/
    # capitalización). El adaptador de bancos prohíbe la SEGUNDA —"do not use
    # ... conventional FCFF"— pero no la primera: las utilidades de un banco
    # son perfectamente reales y su precio también.
    #
    # La dimensión entera se estaba dejando vacía, así que un banco perdía sus
    # 2 puntos por una prohibición que sólo alcanzaba a la mitad. Ahora se
    # puntúa lo permitido y lo prohibido se marca NOT_APPLICABLE, que es lo que
    # `MISSING_DATA_POLICY.md` manda hacer con lo que no aplica —y, a
    # diferencia de MISSING, sale del denominador de la cobertura.
    ey_v = (_ok(eps_banco / price, unit="pct")
            if eps_banco is not None and eps_banco > 0 and price
            else _null(NullState.NOT_MEANINGFUL, "pct",
                       "EARNINGS_YIELD_NONPOSITIVE_EPS_OR_PRICE"))
    ey_score = _score_from_anchor(ey_v, [(0.0, 0), (0.03, 4), (0.06, 7), (0.10, 10)])
    add("VAL-EY-029", ey_v, ey_score)

    fcfy_v = _null(NullState.NOT_APPLICABLE, "pct",
                   f"NOT_APPLICABLE_UNDER_{(packet.analysis.industry_adapter or '').upper()}")
    add("VAL-FCFY-030", fcfy_v, None)
    if ey_v.is_valid:
        assumptions.append(
            f"VAL-EY-029: rendimiento de utilidades {ey_v.value:.2%} "
            f"({eps_banco:,.2f} por accion sobre un precio de {price:,.2f}). "
            "VAL-FCFY-030 queda NOT_APPLICABLE, no ausente: INDUSTRY_ADAPTERS.md "
            "prohibe el flujo de caja libre convencional para este adaptador "
            "-- los pasivos de un banco son su materia prima, no su "
            "financiamiento -- pero no prohibe sus utilidades."
        )

    # ---- Fair value and margin of safety, off the models the matrix allows ----
    # Por la funcion COMPARTIDA del motor, no con aritmetica local. El mismo
    # valor justo lo necesita `overlay/from_packet.py` para derivar el margen
    # de seguridad que consume el agente de riesgo, y calcularlo dos veces es
    # lo que hacia que divergieran: reconstruido a mano, Realty Income salia
    # con -81,2% de margen contra el -2,2% de aqui.
    fair_value, _modelos = ve.valor_justo_por_adaptador(
        adapter=packet.analysis.industry_adapter,
        price=price, shares=shares, net_income=net_income,
        equity_now=equity_now, equity_begin=equity_begin,
        cost_of_equity_value=ke_v.value if ke_v.is_valid else None,
        dividend_per_share=overlay.get("dividend_per_share"),
        dividend_growth=div_growth,
        terminal_growth=overlay_float(overlay, "tv_growth", 0.025),
        forecast_years=int(overlay.get("forecast_years", 5)))
    candidates = _modelos
    mos_v = (ve.margin_of_safety(fair_value, price)
             if fair_value is not None and price is not None
             else _null(NullState.MISSING, "pct", "MOS_INPUTS_UNAVAILABLE"))
    add("VAL-MOS-040", mos_v, None)
    if fair_value is not None and price is not None:
        assumptions.append(
            f"Fair value {fair_value:,.2f}/share is the mean of the {len(candidates)} "
            "matrix-permitted model(s) that produced a number (residual income, Gordon DDM, "
            f"H-model), against a {price:,.2f} price. No FCFF DCF, EV/EBITDA or EV/Sales "
            "figure enters it: INDUSTRY_ADAPTERS.md bars all three for this adapter."
        )

    # ---- Dimensions ----
    # Only the two the permitted models can actually fill are scored. The other
    # three name FCFF, EV/Sales and FCF-yield inputs the adapter bars, so they
    # carry no metric weight and the category falls under the 70% line by
    # construction -- which is SCORING_ENGINE.md's INCOMPLETE, reported rather
    # than dressed up as a low score.
    mos_score = _score_from_anchor(mos_v, [(-0.25, 0), (0.0, 4), (0.15, 7), (0.30, 10)])
    fv_score = mos_score  # same price-vs-value read the default path scores
    _s = (lambda x: [(1.0, Value.of(x, unit="score"))] if x is not None else [])
    dims = [
        # Los cuatro pesos que nombra `SCORING.md`, no una lista vacía. Dos de
        # ellos —EV/Sales y el DCF inverso— van sobre enterprise value y el
        # adaptador los prohíbe: entran como NOT_APPLICABLE y salen del
        # denominador. VAL-PEG-028 sigue siendo un hueco real (necesita el
        # crecimiento de utilidades que declara un analista), y como hueco se
        # declara: NOT_SCORABLE cuenta en contra, que es lo correcto.
        Dimension(name=DIM_MULTIPLES, max_points=DIMENSION_MAX_POINTS[DIM_MULTIPLES],
                  metric_scores=[
                      (0.35, Value.null(NullState.NOT_SCORABLE, unit="score",
                                        warnings=["PEG_NEEDS_ANALYST_GROWTH"])),
                      (0.25, Value.of(multiplos_score, unit="score")
                       if multiplos_score is not None
                       else Value.null(NullState.NOT_SCORABLE, unit="score")),
                      (0.40, Value.null(NullState.NOT_APPLICABLE, unit="score",
                                        warnings=["ENTERPRISE_VALUE_BARRED_UNDER_ADAPTER"])),
                  ]),
        # El z-score contra su propia historia (VAL-ZHIST-035) no es
        # inaplicable a un banco: su P/E tiene historia como el de cualquiera.
        # Es un hueco de datos, y por eso NOT_SCORABLE y no NOT_APPLICABLE --
        # marcarlo inaplicable lo sacaría del denominador y escondería que
        # falta.
        Dimension(name=DIM_HIST_PEER, max_points=DIMENSION_MAX_POINTS[DIM_HIST_PEER],
                  metric_scores=[
                      (1.0, Value.null(NullState.NOT_SCORABLE, unit="score",
                                       warnings=["NO_HISTORICAL_MULTIPLES_IN_ADAPTER_PATH"])),
                  ]),
        # Los dos pesos de `SCORING.md`, no una lista vacía. El de flujo de
        # caja entra como NOT_APPLICABLE y por tanto fuera del denominador; el
        # de utilidades se puntúa. Vaciar la dimensión los trataba a los dos
        # como ausentes, y sólo uno lo estaba.
        Dimension(name=DIM_CF_YIELD, max_points=DIMENSION_MAX_POINTS[DIM_CF_YIELD],
                  metric_scores=[
                      (0.5, Value.of(ey_score, unit="score") if ey_score is not None
                       else Value.null(NullState.NOT_SCORABLE, unit="score")),
                      (0.5, Value.null(NullState.NOT_APPLICABLE, unit="score",
                                       warnings=["NOT_APPLICABLE_UNDER_ADAPTER"])),
                  ]),
        Dimension(name=DIM_FAIR_VALUE_SCENARIOS,
                  max_points=DIMENSION_MAX_POINTS[DIM_FAIR_VALUE_SCENARIOS],
                  metric_scores=_s(fv_score)),
        Dimension(name=DIM_MOS, max_points=DIMENSION_MAX_POINTS[DIM_MOS],
                  metric_scores=_s(mos_score)),
    ]
    # Category.points() is SCORING_ENGINE.md's own aggregation ("dimension
    # points = max points * score / 10; category points = sum"), and it is
    # null-aware, which the per-dimension score10() is deliberately not.
    awarded = Category(name="valuation", max_points=MAX_POINTS, dimensions=dims).points()
    # `MISSING_DATA_POLICY.md`: cobertura = peso VÁLIDO / peso APLICABLE, y su
    # árbol de decisión empieza por "¿la métrica aplica? Si no, NOT_APPLICABLE".
    # Aquí se dividía entre `MAX_POINTS`, o sea entre los 10 puntos completos,
    # así que un banco pagaba en cobertura por obedecer la prohibición del
    # propio Cerebro: medido, JPM salía en 0,300 con su valuación entregada.
    # Contar lo prohibido en el denominador convierte una regla metodológica en
    # un castigo, y no distingue "no aplica" de "no lo tengo" — que es
    # exactamente la distinción que esta política existe para hacer.
    aplicable = sum(d.max_points * d.applicable_weight() for d in dims)
    valido = sum(d.max_points * d.valid_weight() for d in dims)
    coverage = (valido / aplicable) if aplicable > 0 else 0.0

    assumptions.append(
        f"industry_adapter={packet.analysis.industry_adapter!r}: DECISION_RULES.md's matrix "
        "assigns residual income / excess return / DDM as primary models and P/B vs ROE as the "
        "secondary check, and bars enterprise DCF and EV/EBITDA. The multiples, historical/peer "
        "and cash-flow-yield dimensions name barred inputs (EV/Sales, reverse DCF, FCF yield), "
        "so they carry no weight here. SCORING_ENGINE.md: valid dimension weight "
        f"{coverage:.0%} is below 70%, so this category is INCOMPLETE -- displayable, but it "
        "cannot pass a profile gate."
    )

    return ValuationOutput(
        agent_id=AGENT_ID,
        # SCORING_ENGINE.md's own word for this state: "a category is
        # INCOMPLETE when valid dimension weight is below 70%", and it "may be
        # displayed but cannot pass a profile gate". Not ERROR -- nothing
        # failed; the barred dimensions were never applicable to a bank.
        status="INCOMPLETE" if coverage < 0.70 else "COMPLETE",
        security=SecurityRef(ticker=packet.security.ticker, exchange=packet.security.exchange,
                             currency=packet.security.reporting_currency),
        knowledge_timestamp=packet.analysis.knowledge_timestamp,
        category=CategoryStats(max_points=MAX_POINTS, awarded_points=awarded,
                               score_10=10.0 * awarded / MAX_POINTS,
                               confidence=_category_confidence(coverage, packet)),
        verdict=verdict(10.0 * awarded / MAX_POINTS), coverage=coverage,
        dimensions=dims,
        metrics=[MetricRow.from_value(r.metric_id, r.value, formula_id=r.metric_id,
                                      formula_version=_VERSION,
                                      score=(r.score10 if r.score10 is not None else "NOT_SCORABLE"),
                                      confidence=r.confidence) for r in rows],
        mandatory_flags=["ADAPTER_FINANCIAL_MODELS", "CATEGORY_INCOMPLETE_GATE_INELIGIBLE"],
        assumptions=assumptions, judgment_requests=[],
        source_lineage=["packet.fundamentals.annual", "packet.facts_table",
                        "packet.capital_structure", "overlay.dividends_per_share_history"],
        validation_tests=ValidationTestsSummary(passed=0, failed=0, warnings=1),
        model_selection=ModelSelection(
            primary=["RESIDUAL_INCOME", "DDM"],
            rejected=["FCFF_DCF", "EV_EBITDA", "EV_SALES", "REVERSE_DCF"]),
        wacc=WaccSummary(value=None, components={
            "risk_free_rate": float(rf) if rf is not None else None,
            "beta": float(beta) if beta is not None else None,
            "erp": erp,
            "cost_of_equity": ke_v.value if ke_v.is_valid else None,
        }),
    )


def run(packet: Packet, overlay: dict[str, Any] | None = None) -> ValuationOutput:
    """Run the valuation specialist against `packet`, producing a
    `ValuationOutput` (max 10 pts): the five weighted dimensions,
    mandatory flags, and OUTPUT_SCHEMA.md's extension fields (WACC,
    scenarios, reverse DCF, cross-checks, fair-value distribution,
    reference bands), all assembled by direct calls into
    `wbj.engines.valuation_engine` (Task 13).

    See the module docstring for `overlay`'s supported keys.
    """
    overlay = overlay or {}
    adapter = packet.analysis.industry_adapter
    if adapter in FINANCIAL_ADAPTERS:
        return _financial_adapter_output(packet, overlay)
    if adapter == "reits":
        return _reit_adapter_output(packet, overlay)
    if adapter in MODEL_REPLACING_ADAPTERS:
        return _unsupported_adapter_output(packet)
    # An adapter no set classifies is one nobody has checked the
    # conventional formulas against, so running them on it is an assertion
    # without evidence. Falling through valued a company by FCFF DCF purely
    # because its adapter name was unrecognised: `industry_adapter="bank_adapter"`
    # — Victor's own spelling in VAL-T010, and this engine's is "banks" —
    # produced `primary=['FCFF_DCF', 'ECONOMIC_PROFIT']` for a BANK, the
    # exact model DECISION_RULES.md's selection matrix bars for one.
    #
    # `business.py` already treats an unclassified adapter as untrusted
    # (`_category_confidence` floors its model fit "as a model-replacing
    # adapter is: no evidence the formulas fit"). Valuation is where the
    # same unknown does the most damage, since it prices the company.
    if not _adapters.is_classified(adapter):
        return _unsupported_adapter_output(packet)

    annual = _annual_rows(packet)
    assumptions: list[str] = []
    if adapter in CYCLE_NORMALIZED_ADAPTERS:
        assumptions.append(
            f"industry_adapter={adapter!r}: INDUSTRY_ADAPTERS.md requires mid-cycle "
            "normalization -- the trailing figures driving this DCF are point-in-cycle "
            "and are not certified as a normalized base case."
        )
    judgment_requests: list[JudgmentRequest] = []
    rows: list[_Row] = []
    # Flags raised before the aggregation block builds `mandatory_flags`.
    mandatory_flags_extra: list[str] = []

    def add(metric_id: str, v: Value, score10: float | None) -> None:
        rows.append(_Row(metric_id=metric_id, value=v, score10=score10, confidence=_confidence_for(v)))

    latest = annual[-1] if annual else {}
    prior = annual[-2] if len(annual) >= 2 else {}
    revenue0 = _num(latest, "revenue") or _fact(packet, "revenue")
    ebit = _num(latest, "ebit")
    price = _fact(packet, "price")
    diluted_shares = _fact(packet, "diluted_shares") or _num(latest, "diluted_shares")
    cash = _fact(packet, "cash") or _num(latest, "cash") or 0.0
    total_debt = _fact(packet, "total_debt") or _num(latest, "total_debt") or 0.0
    net_debt = total_debt - cash

    # `tax_rate_or_statutory` substitutes 21% whenever the filing yields no
    # usable normalized rate -- which includes an effective rate outside
    # 0-100% (a negative tax benefit, a rate above 100%), not only missing
    # inputs. Gating the disclosure on `pretax is None or tax_expense is None`
    # missed the out-of-range case (the fallback made the rate non-None), the
    # same silent-substitution bug fixed in financial.py. Gate on
    # `has_reported_tax_rate` instead so an out-of-range rate is disclosed too.
    pretax, tax_expense = _num(latest, "income_before_tax"), _num(latest, "income_tax_expense")
    tax_rate = _taxes.tax_rate_or_statutory(pretax, tax_expense)
    if not _taxes.has_reported_tax_rate(pretax, tax_expense):
        assumptions.append(
            "No usable normalized cash tax rate in the latest filing (missing, non-positive "
            "pretax, or an effective rate outside 0-100%); substituted the 21% statutory rate."
        )

    # ---- ROIC / reinvestment rate / fundamental growth (VAL-NOPAT/ROIC/REINV) ----
    # Invested capital is a BOOK reconciliation -- DATA_DICTIONARY.md:
    # "Operating assets minus operating liabilities, reconciled to debt plus
    # equity minus excess cash" -- and so is ROE's denominator. This read used
    # `market_cap` for the current year while the prior year (`equity_t1`
    # below) used book equity, so the average invested capital mixed a market
    # value with a book one. On AAPL that put IC at $4.98T instead of $150B,
    # collapsing ROIC from 74.8% to 2.3% and the reinvestment-implied growth
    # from 8.5% to 0.26% -- which is what drove the DCF to a $51 base value
    # against a $334 price. The market-value equity weight WACC needs is a
    # separate quantity and already lives in `e_value` below.
    debt_t, equity_t = total_debt, _num(latest, "total_equity")
    debt_t1, equity_t1 = _num(prior, "total_debt"), _num(prior, "total_equity")
    if ebit is not None:
        nopat_v = ve.nopat(ebit, tax_rate)
    else:
        nopat_v = _null(NullState.MISSING, "usd", "NOPAT_INPUTS_UNAVAILABLE")
    add("VAL-NORM-001", _ok(ebit, unit="usd") if ebit is not None else _null(NullState.MISSING, "usd", "EBIT_UNAVAILABLE"), None)

    # ---- VAL-NWC-006: non-cash working capital ----
    # Reported as a level, unscored: SCORING.md gives it no dimension, and
    # what the models consume is its *change* (VAL-FCFF-005's `dnwc` term,
    # BUS-REINV-018's second term). Publishing the level is what lets a
    # reader check that change against the two balance sheets.
    nwc_v = ve.non_cash_working_capital(
        _num(latest, "total_current_assets"), _num(latest, "cash"),
        _num(latest, "short_term_investments"),
        _num(latest, "total_current_liabilities"), _num(latest, "short_term_debt"),
    )
    add("VAL-NWC-006", nwc_v, None)
    nwc_prior_v = ve.non_cash_working_capital(
        _num(prior, "total_current_assets"), _num(prior, "cash"),
        _num(prior, "short_term_investments"),
        _num(prior, "total_current_liabilities"), _num(prior, "short_term_debt"),
    )
    if nwc_v.is_valid and nwc_prior_v.is_valid:
        assumptions.append(
            f"VAL-NWC-006: non-cash working capital moved from "
            f"{nwc_prior_v.value:,.0f} to {nwc_v.value:,.0f} "
            f"({nwc_v.value - nwc_prior_v.value:+,.0f}); a positive change is cash "
            "tied up in operations and is subtracted in VAL-FCFF-005."
        )

    ic_result = None
    if debt_t is not None and equity_t is not None and debt_t1 is not None and equity_t1 is not None:
        ic_end = ve.invested_capital(debt_t, equity_t, cash).financing_view
        ic_begin = ve.invested_capital(debt_t1, equity_t1, _num(prior, "cash") or 0.0).financing_view
        if ic_end.is_valid and ic_begin.is_valid:
            avg_ic = (ic_end.value + ic_begin.value) / 2
            ic_result = avg_ic
    roic_v = ve.roic(nopat_v.value, ic_result) if nopat_v.is_valid and ic_result is not None else _null(NullState.MISSING, "pct", "ROIC_INPUTS_UNAVAILABLE")
    roic_value = roic_v.value if roic_v.is_valid else 0.10  # documented fallback below
    if roic_v.is_null:
        assumptions.append("ROIC unavailable from Packet; substituted a 10% fallback for the reinvestment-rate/fundamental-growth derivation.")

    # The explicit forecast horizon, read here because the base-case growth
    # below is a CAGR over exactly that period.
    years = int(overlay.get("forecast_years", 5))

    # --- Base-case revenue growth: a forecast driver, not a trailing ratio ---
    #
    # DATASET.md makes `forecast_drivers` ("revenue, margins, reinvestment,
    # ROIC ... explicit model assumptions") a REQUIRED input. This used to
    # manufacture one instead: trailing capex/NOPAT times trailing ROIC,
    # labelled VAL-REINV-043.
    #
    # That inverts the rule it cited. VAL-REINV-043 is "Terminal reinvestment
    # consistency", `rr = g / TerminalROIC` — a constraint binding the
    # TERMINAL year's reinvestment to its growth so the model cannot price
    # free growth. DECISION_RULES.md rule 2 states the same identity as a
    # consistency condition ON a forecast, not a way to produce one. The DCF
    # already enforces it: `terminal_year_metrics` derives each year's
    # reinvestment as `g / ROIC` from whatever growth it is handed.
    #
    # Measuring reinvestment as capex alone also breaks for anything
    # asset-light, where growth is bought with R&D that is expensed rather
    # than capitalised. On NVDA it produced 5.05% for a company that had just
    # grown 65.5%, valuing it near $41 against a $195 price — while the packet
    # already carried consensus revenue out to FY2031.
    #
    # So: use the forecast the packet carries, and keep fundamental growth
    # capacity beside it as the cross-reference DECISION_RULES.md's
    # reverse-DCF section asks for ("compare implied values to historical
    # performance, market capacity, consensus, and fundamental growth
    # capacity"). An analyst overrides either via
    # `overlay['scenarios']['base']['growth']`.
    capex_latest = abs(_num(latest, "capex") or 0.0)
    reinvestment_rate = capex_latest / nopat_v.value if nopat_v.is_valid and nopat_v.value != 0 else 0.30
    fundamental_growth_v = ve.fundamental_growth(reinvestment_rate, roic_value)
    fundamental_growth = fundamental_growth_v.value if fundamental_growth_v.is_valid else 0.03

    consensus_cagr = _consensus_revenue_cagr(
        (packet.estimates or {}).get("fmp_analyst_estimates") or [],
        revenue0, _last_reported_date(packet), years,
    )
    # Bear and bull default to `base * 0.5` and `base * 1.5` below. Those
    # multipliers are this module's calibration, and they were tuned when
    # the base was a small trailing ratio: on a hypergrowth consensus they
    # produce assumptions nobody made (PLTR's bull reached 109% — 40x
    # revenue in five years). The analysts' own low/high is a sourced
    # dispersion for the same horizon, and DECISION_RULES.md's scenario
    # framework asks each scenario to "separately define" its growth path.
    bear_growth = bull_growth = None
    if consensus_cagr is not None:
        base_growth = consensus_cagr["cagr"]
        bear_growth, bull_growth = consensus_cagr["cagr_low"], consensus_cagr["cagr_high"]
        spread = (
            f"Bear/bull take the analysts' own low/high for the same horizon "
            f"({bear_growth:.4f}/{bull_growth:.4f}), a sourced dispersion rather "
            "than a multiple of the base."
            if bear_growth is not None and bull_growth is not None
            else "The estimate carries no low/high, so bear/bull fall back to "
                 "0.5x/1.5x of the base — this module's calibration, not a forecast."
        )
        assumptions.append(
            f"Base-case revenue growth ({base_growth:.4f}) is the consensus revenue "
            f"CAGR over the {consensus_cagr['years']}-year explicit period (to "
            f"{consensus_cagr['to_date']}, {consensus_cagr['analysts'] or 'n/a'} "
            f"analysts) — DATASET.md's required `forecast_drivers`. {spread} "
            f"Fundamental growth capacity (capex/NOPAT * ROIC) is "
            f"{fundamental_growth:.4f} for comparison; it measures reinvestment as "
            "capex only, so it understates any business that grows on expensed "
            "R&D. Override via overlay['scenarios']['base']['growth']."
        )
    else:
        base_growth = fundamental_growth
        assumptions.append(
            f"Base-case revenue growth ({base_growth:.4f}) falls back to fundamental "
            "growth capacity (capex/NOPAT * ROIC): the packet carries no forward "
            "consensus revenue. Reinvestment is measured as capex only, so this "
            "understates any business that grows on expensed R&D. Override via "
            "overlay['scenarios']['base']['growth']."
        )

    op_margin_latest = ebit / revenue0 if ebit is not None and revenue0 not in (None, 0) else 0.20
    if ebit is None or revenue0 in (None, 0):
        assumptions.append("Latest operating margin unavailable; substituted a 20% fallback for scenario construction.")

    # ---- WACC (VAL-KE-008 / VAL-KD-011 / VAL-WACC-007) ----
    rf = overlay.get("risk_free_rate", (packet.estimates or {}).get("risk_free_rate"))
    beta = overlay.get("beta", packet.capital_structure.get("beta"))
    erp = overlay_float(overlay, "erp", 0.045)
    if rf is None or beta is None:
        ke_v = _null(NullState.MISSING, "pct", "WACC_INPUTS_UNAVAILABLE_NO_RF_OR_BETA")
        assumptions.append("Cost of equity not computed: risk-free rate or beta unavailable from Packet.estimates/capital_structure.")
    else:
        ke_v = ve.cost_of_equity(float(rf), float(beta), erp)
        # DECISION_RULES.md prefers a bottom-up beta ("from comparable
        # operating businesses where possible"). This is the security's own
        # levered beta off the packet: `ve.unlever_beta`/`ve.relever_beta`
        # (VAL-UBETA-009/VAL-LBETA-010) exist but need peer betas the packet
        # does not carry. Saying so is the difference between taking the
        # rule's fallback branch and appearing to have followed its preference.
        assumptions.append(
            f"Cost of equity uses the security's own levered beta ({float(beta):.2f}) "
            "from the packet, not a peer-derived bottom-up beta: DECISION_RULES.md "
            "prefers bottom-up 'where possible', and no peer beta panel is available."
        )
    add("VAL-KE-008", ke_v, None)

    interest_expense = overlay.get("interest_expense")
    if interest_expense is not None and ebit is not None and rf is not None:
        coverage = ebit / float(interest_expense) if interest_expense else 0.0
        kd_v = ve.synthetic_kd(float(rf), coverage)
    elif rf is not None:
        kd_v = ve.synthetic_kd(float(rf), 3.0)  # documented fallback: mid-BBB synthetic coverage
        assumptions.append("Interest expense unavailable; cost of debt used a fallback interest-coverage of 3.0x (synthetic mid-grade rating).")
    else:
        kd_v = _null(NullState.MISSING, "pct", "COST_OF_DEBT_UNAVAILABLE_NO_RF")
    add("VAL-KD-011", kd_v, None)

    e_value = packet.capital_structure.get("market_cap") or (price * diluted_shares if price and diluted_shares else None)
    if ke_v.is_valid and kd_v.is_valid and e_value is not None:
        wacc_v = ve.wacc(e_value, total_debt, ke_v.value, kd_v.value, tax_rate)
    else:
        wacc_v = _null(NullState.MISSING, "pct", "WACC_INPUTS_UNAVAILABLE")
    add("VAL-WACC-007", wacc_v, None)
    wacc_value = wacc_v.value if wacc_v.is_valid else None
    wacc_sens = ve.wacc_sensitivity(wacc_v) if wacc_v.is_valid else None

    # ---- Terminal growth (DCF consistency rule 3: must stay below WACC) ----
    tv_growth_default = overlay_float(overlay, "terminal_growth", 0.025)
    if wacc_value is not None and tv_growth_default >= wacc_value:
        tv_growth = max(0.0, wacc_value - 0.005)
        assumptions.append(
            f"Default terminal growth ({tv_growth_default:.4f}) was >= WACC ({wacc_value:.4f}); "
            f"clamped to {tv_growth:.4f} per DECISION_RULES.md's 'g < WACC' consistency rule."
        )
    else:
        tv_growth = tv_growth_default

    # ---- Scenario construction (bear/base/bull) -- brief's derived defaults ----
    # `scenario_overrides`, not `scenarios`. The two are different inputs that
    # collided on one key: MKT-SCEN-025 reads `scenarios` as a LIST of
    # [probability, outcome] pairs, while this reads a DICT of bear/base/bull
    # assumption overrides. Supplying either shape took down the other
    # specialist -- a list here raised AttributeError on `.get` and killed the
    # whole valuation run.
    #
    # A dict still wins under the old name so any file already written keeps
    # working; a list under it is market's input and is ignored here.
    scenario_overrides = overlay.get("scenario_overrides")
    if scenario_overrides is None:
        legacy = overlay.get("scenarios")
        scenario_overrides = legacy if isinstance(legacy, dict) else {}
    if not isinstance(scenario_overrides, dict):
        scenario_overrides = {}

    def _scenario_input(name: str, default_growth: float, default_margin: float, default_wacc: float | None, default_prob: float) -> ScenarioInput | None:
        ov = scenario_overrides.get(name, {})
        w = ov.get("wacc", default_wacc)
        if w is None:
            return None
        return ScenarioInput(
            probability=float(ov.get("probability", default_prob)),
            growth=float(ov.get("growth", default_growth)),
            margin=float(ov.get("margin", default_margin)),
            wacc=float(w),
            tv_growth=float(ov.get("tv_growth", tv_growth)),
        )

    bear_input = _scenario_input("bear", bear_growth if bear_growth is not None else base_growth * 0.5, op_margin_latest - 0.02, (wacc_value + 0.01) if wacc_value else None, 0.25)
    base_input = _scenario_input("base", base_growth, op_margin_latest, wacc_value, 0.50)
    bull_input = _scenario_input("bull", bull_growth if bull_growth is not None else base_growth * 1.5, op_margin_latest + 0.02, (wacc_value - 0.01) if wacc_value else None, 0.25)

    scenario_summaries: list[ScenarioSummary] = []
    # VAL-SCEN-036's number, not just its dimension score. `DECISION_RULES.md`:
    # "The main report shows each value AND the weighted value; it does not show
    # only the average." The engine computed it and the specialist dropped it on
    # the floor, so the one figure that rule names never reached a row, an
    # output or the report. Starts as the null it is when scenarios don't run.
    weighted_value_v = _null(NullState.MISSING, "usd_per_share",
                             "SCENARIO_VALUATION_NOT_COMPUTED")
    base_per_share = None
    bear_per_share = None
    bull_per_share = None
    scenario_result = None
    if bear_input and base_input and bull_input and diluted_shares and revenue0 is not None:
        common = DCFCommonInputs(revenue0=revenue0, shares=diluted_shares, tax_rate=tax_rate, roic=roic_value, years=years, net_debt=net_debt)
        try:
            scenario_result = ve.scenarios(bear_input, base_input, bull_input, common)
        except ValueError as e:
            assumptions.append(f"Scenario probabilities did not sum to 1.0: {e}")
        if scenario_result is not None:
            for name, s_input, s_value in (("Bear", bear_input, scenario_result.bear_value), ("Base", base_input, scenario_result.base_value), ("Bull", bull_input, scenario_result.bull_value)):
                scenario_summaries.append(
                    ScenarioSummary(
                        name=name, probability=s_input.probability,
                        assumptions={"growth": s_input.growth, "margin": s_input.margin, "wacc": s_input.wacc, "tv_growth": s_input.tv_growth},
                        per_share_value=s_value.value if s_value.is_valid else None,
                    )
                )
            weighted_value_v = scenario_result.weighted_value
            base_per_share = scenario_summaries[1].per_share_value
            bear_per_share = scenario_summaries[0].per_share_value
            bull_per_share = scenario_summaries[2].per_share_value
    else:
        assumptions.append("Scenario valuation not computed: WACC, diluted shares, or latest revenue unavailable.")

    # Score stays None on purpose: the Fair-value dimension is already scored by
    # the price-vs-scenario read disclosed as `fair_value_by_scenarios` in
    # `_SCORED_WITHOUT_ROW`. This row reports the weighted figure DECISION_RULES.md
    # requires; scoring it again here would double-count one dimension.
    add("VAL-SCEN-036", weighted_value_v, None)

    # ---- Reverse DCF (VAL-RDCF-027) ----
    consensus_growth = None
    fmp_est = (packet.estimates or {}).get("fmp_analyst_estimates") or []
    _next_est = _next_year_estimate(fmp_est, _last_reported_date(packet))
    if _next_est and revenue0:
        rev_next = _est_field(_next_est, "revenueAvg", "estimatedRevenueAvg")
        if rev_next is not None:
            consensus_growth = rev_next / revenue0 - 1

    reverse_dcf_summary = ReverseDCFSummary(current_price=price)
    if wacc_value is not None and diluted_shares and price and revenue0 is not None:
        rdcf_inputs = ReverseDCFInputs(
            revenue0=revenue0, shares=diluted_shares, tax_rate=tax_rate, roic=roic_value, years=years,
            net_debt=net_debt, margin=op_margin_latest, wacc=wacc_value, tv_growth=tv_growth,
            consensus_growth=consensus_growth,
        )
        rdcf_result = ve.reverse_dcf(price, diluted_shares, rdcf_inputs)
        reverse_dcf_summary = ReverseDCFSummary(
            current_price=price,
            implied_revenue_cagr=rdcf_result.implied_growth.value if rdcf_result.implied_growth.is_valid else None,
            implied_margin=rdcf_result.implied_margin.value if rdcf_result.implied_margin.is_valid else None,
            implied_high_growth_years=years,
        )
        add("VAL-RDCF-027", rdcf_result.implied_growth, None)
    else:
        add("VAL-RDCF-027", _null(NullState.MISSING, "pct", "REVERSE_DCF_INPUTS_UNAVAILABLE"), None)

    # ---- Model cross-checks: FCFF DCF vs economic-profit (DCF consistency rule 7) ----
    fcff_per_share = base_per_share  # scenarios() IS the FCFF DCF model for the base case
    econ_profit_per_share = None
    ep_ev = None            # bound outside the branch: VAL-EVA-020 emits either way
    if (wacc_value is not None and ic_result is not None and diluted_shares
            and base_input is not None and revenue0):
        # Built on the SAME base-scenario forecast the FCFF DCF prices, so
        # Cerebro 9's cross-check compares two routes through one set of
        # assumptions. It used to be fed `NOPAT*(1-rr)*(1+g)^t` — free cash
        # flow — where economic profit belongs, and `IC0` was then added on
        # top of a present value of cash flows, double-counting the capital
        # base. The check could never pass, so the flag fired on every
        # company and said nothing about the assumptions it was meant to test.
        ep_ev = ve.economic_profit_ev(
            ic0=ic_result, growth=base_input.growth, margin=base_input.margin,
            wacc_value=base_input.wacc, tv_growth=base_input.tv_growth,
            revenue0=revenue0, tax_rate=tax_rate, roic_value=roic_value,
            years=years,
        )
        if ep_ev.is_valid:
            # The same debt bridge the DCF applies (`common.net_debt`), or the
            # two per-share figures would differ by the bridge alone.
            ps_v = ve.per_share(ep_ev.value - net_debt, diluted_shares)
            econ_profit_per_share = ps_v.value if ps_v.is_valid else None
    add("VAL-EVAEV-021", _ok(econ_profit_per_share, unit="usd_per_share") if econ_profit_per_share is not None else _null(NullState.MISSING, "usd_per_share", "ECONOMIC_PROFIT_INPUTS_UNAVAILABLE"), None)

    reconciled = None
    if fcff_per_share is not None and econ_profit_per_share is not None:
        reconciled = ve.reconciles(_ok(fcff_per_share, "usd_per_share"), _ok(econ_profit_per_share, "usd_per_share"))
        if not reconciled:
            assumptions.append("FCFF DCF and economic-profit per-share values differ by >1%: inconsistent reinvestment/capital assumptions (DECISION_RULES.md rule 7).")

    # ---- Relative valuation: needs >=8 peers; Packet's estimates.peers is a bare ticker list ----
    peers_list = (packet.estimates or {}).get("peers") or []
    # FMP's /stable/stock-peers returns a flat list of peer objects; the
    # retired /v3 wrapped them in {"peersList": [...]}. Reading only the
    # old shape counted 0 peers while the packet held 9, which silently
    # dropped the relative-valuation model out of the ensemble.
    if peers_list and isinstance(peers_list[0], dict) and "peersList" in peers_list[0]:
        n_peers = len(peers_list[0].get("peersList") or [])
    else:
        n_peers = len(peers_list)
    # VAL-REL-034's peer multiples: an explicit overlay list wins, else the
    # packet's peer panel (DATASET.md `peer_multiples_and_fundamentals`).
    # `price_to_sales` is market cap / revenue -- an equity-basis multiple, so
    # applying its median to this company's revenue yields an equity value,
    # which is what the per-share comparison below wants. SCORING_ENGINE.md's
    # >=8-peer minimum still gates it.
    peer_multiples = overlay.get("peer_multiples")
    if not peer_multiples:
        peer_multiples = [
            row.get("price_to_sales")
            for row in ((packet.estimates or {}).get("peer_panel") or [])
            if isinstance(row.get("price_to_sales"), (int, float)) and row.get("price_to_sales") > 0
        ]
    relative_value = None
    if peer_multiples and len(peer_multiples) >= MIN_PEERS and revenue0:
        relative_value = float(np.median(peer_multiples)) * revenue0 / diluted_shares if diluted_shares else None
        assumptions.append(
            f"VAL-REL-034 relative value from the median price/sales of {len(peer_multiples)} "
            f"peers ({float(np.median(peer_multiples)):.2f}x) applied to this company's revenue "
            "-- an equity-basis multiple comparison, not a regression on growth/margin/ROIC/risk "
            "(FORMULAS.md's full VAL-REL-034 specification), so it enters the ensemble at the "
            "reduced reliability weight below."
        )
    # ---- Reliability-weighted ensemble (VAL-ENSEMBLE-044) -> dispersion + value ----
    # Reuse the engine's `ensemble` rather than an unweighted stdlib stdev:
    # weights reflect model reliability (FCFF DCF is the primary model;
    # economic profit a high-reliability cross-check that should reconcile;
    # the peer-relative read is weaker). `ensemble` excludes null-valued
    # models and returns both the reliability-weighted value and the
    # dispersion across the included values.
    ensemble_models: list[EnsembleModelInput] = []
    if fcff_per_share is not None:
        ensemble_models.append(EnsembleModelInput(label="FCFF_DCF", value=_ok(fcff_per_share, "usd_per_share"), weight=1.0))
    if econ_profit_per_share is not None:
        ensemble_models.append(EnsembleModelInput(label="ECONOMIC_PROFIT", value=_ok(econ_profit_per_share, "usd_per_share"), weight=0.8))
    if relative_value is not None:
        ensemble_models.append(EnsembleModelInput(label="RELATIVE", value=_ok(relative_value, "usd_per_share"), weight=0.5))
    ensemble_result = ve.ensemble(ensemble_models) if ensemble_models else None
    dispersion = (
        ensemble_result.dispersion.value
        if ensemble_result is not None and ensemble_result.dispersion.is_valid
        else None
    )

    # ---- Fair-value distribution via Monte Carlo (VAL-MC-037) ----
    fv_dist = FairValueDistribution()
    mc_result = None        # bound outside the branch: VAL-MC-037 emits either way
    if bear_input and base_input and bull_input and wacc_value is not None and diluted_shares and revenue0 is not None:
        mc_inputs = MonteCarloInputs(
            revenue0=revenue0, shares=diluted_shares, tax_rate=tax_rate, roic=roic_value, years=years, net_debt=net_debt,
            growth_range=_mc_range(bear_input.growth, base_input.growth, bull_input.growth),
            margin_range=_mc_range(bear_input.margin, base_input.margin, bull_input.margin),
            wacc_range=_mc_range(bull_input.wacc, wacc_value, bear_input.wacc),
            tv_growth=tv_growth,
        )
        mc_result = ve.monte_carlo(mc_inputs, n=2000, seed=42)
        fv_dist = FairValueDistribution(
            p10=mc_result.p10.value if mc_result.p10.is_valid else None,
            p25=mc_result.p25.value if mc_result.p25.is_valid else None,
            median=mc_result.median.value if mc_result.median.is_valid else None,
            p75=mc_result.p75.value if mc_result.p75.is_valid else None,
            p90=mc_result.p90.value if mc_result.p90.is_valid else None,
        )

    # ---- Formula-result rows for the DCF chain and the ensemble ----------
    # shared/FORMULA_REGISTRY.md: "Every formula must have: a stable formula
    # ID; formula text and sign convention; ... formula version; at least one
    # validation test", and it specifies a formula-result object carrying
    # `formula_id`, `result: {value, unit}`, `status` and `warnings`.
    #
    # Emitting is not the same as scoring. Each of these was computed and then
    # dropped: the chain that produces the per-share value (VAL-FCFF-005 ->
    # TVG-012 -> EV-014 -> EQ-015 -> PS-016, with TVS-042 the terminal share),
    # plus the economic-profit cross-check, the peer-relative read, the Monte
    # Carlo and the ensemble. `00_main_agent/AGENT.md` asks for "a traceable
    # final report, not a black-box opinion", and a figure the report never
    # carries cannot be traced or challenged.
    #
    # Every row goes in with `None` for its score. The dimensions Victor
    # defines are already fed -- `VAL-FCFF-005..027, 036..044` is his own
    # bundled range, scored through `fair_value_by_scenarios` -- so scoring
    # these again would double-count. They are evidence, not slots.
    if base_input is not None and revenue0 is not None and wacc_value is not None \
            and diluted_shares:
        try:
            tym = ve.terminal_year_metrics(
                base_input.growth, base_input.margin, wacc_value, base_input.tv_growth,
                revenue0, tax_rate, roic_value, years,
            )
            fcffs = tym["explicit_fcffs"]
            tv = tym["terminal_value"]
            pv_explicit = sum(f / (1 + wacc_value) ** (t + 1) for t, f in enumerate(fcffs))
            pv_terminal = tv / (1 + wacc_value) ** years
            ev = pv_explicit + pv_terminal
            equity = ev - net_debt
            add("VAL-FCFF-005", _ok(fcffs[0], unit="usd"), None)
            add("VAL-TVG-012", _ok(tv, unit="usd"), None)
            add("VAL-EV-014", _ok(ev, unit="usd"), None)
            add("VAL-EQ-015", _ok(equity, unit="usd"), None)
            add("VAL-PS-016", _ok(equity / diluted_shares, unit="usd_per_share"), None)
            add("VAL-TVS-042", ve.terminal_share(pv_terminal, ev), None)
        except (ValueError, ZeroDivisionError, KeyError, IndexError) as exc:
            for mid, unit in (("VAL-FCFF-005", "usd"), ("VAL-TVG-012", "usd"),
                              ("VAL-EV-014", "usd"), ("VAL-EQ-015", "usd"),
                              ("VAL-PS-016", "usd_per_share"), ("VAL-TVS-042", "pct")):
                add(mid, _null(NullState.NOT_MEANINGFUL, unit,
                               f"DCF_CHAIN_UNAVAILABLE: {type(exc).__name__}"), None)
    else:
        for mid, unit in (("VAL-FCFF-005", "usd"), ("VAL-TVG-012", "usd"),
                          ("VAL-EV-014", "usd"), ("VAL-EQ-015", "usd"),
                          ("VAL-PS-016", "usd_per_share"), ("VAL-TVS-042", "pct")):
            add(mid, _null(NullState.MISSING, unit, "DCF_CHAIN_INPUTS_UNAVAILABLE"), None)

    # VAL-REINV-043 `reinvestment / NOPAT`, the rate feeding fundamental growth.
    add("VAL-REINV-043",
        _ok(reinvestment_rate, unit="pct") if nopat_v.is_valid and nopat_v.value
        else _null(NullState.MISSING, "pct", "REINVESTMENT_RATE_INPUTS_UNAVAILABLE"), None)

    # VAL-EVA-020, the economic-profit enterprise value DCF consistency rule 7
    # reconciles against.
    add("VAL-EVA-020",
        ep_ev if ep_ev is not None
        else _null(NullState.MISSING, "usd", "ECONOMIC_PROFIT_INPUTS_UNAVAILABLE"), None)

    # VAL-RI-022 / VAL-RIV-023. DECISION_RULES.md's model-selection matrix
    # gives residual income to banks and insurers, where the financial adapter
    # computes and emits it. On this path -- a non-financial -- the model is
    # not the one Victor assigns, so MISSING_DATA_POLICY.md step 1 applies:
    # "Is the metric applicable? If no, use NOT_APPLICABLE and invoke the
    # industry adapter." Emitting the row is still required (FORMULA_REGISTRY:
    # every formula reports); what it reports is that it does not apply.
    for mid in ("VAL-RI-022", "VAL-RIV-023"):
        add(mid, _null(NullState.NOT_APPLICABLE, "usd",
                       "RESIDUAL_INCOME_NOT_THE_ASSIGNED_MODEL: DECISION_RULES.md "
                       "assigns excess-return valuation to banks and insurers; this "
                       "security uses the FCFF DCF as its primary model"), None)

    # VAL-REL-034, the peer-relative read. Already computed above and already
    # disclosed in `assumptions` as an equity-basis multiple comparison rather
    # than FORMULAS.md's full regression on growth/margin/ROIC/risk; it enters
    # the ensemble at a reduced weight for exactly that reason. The row
    # carries the figure so the reduction has something to point at.
    add("VAL-REL-034",
        _ok(relative_value, unit="usd_per_share") if relative_value is not None
        else _null(NullState.MISSING, "usd_per_share",
                   "RELATIVE_VALUE_UNAVAILABLE_NEEDS_PEER_MULTIPLES"), None)

    # VAL-UBETA-009 / VAL-LBETA-010, the bottom-up beta pair. Every input is
    # already in hand: the disclosed beta, the reported tax rate, and D/E from
    # the balance sheet. Relevering at the company's own D/E returns the
    # levered beta, which is the identity that makes the pair auditable.
    de_ratio = (float(total_debt) / float(equity_t)
                if total_debt is not None and equity_t else None)
    if beta is not None and de_ratio is not None:
        ub = ve.unlever_beta(float(beta), tax_rate, de_ratio)
        add("VAL-UBETA-009", ub, None)
        add("VAL-LBETA-010",
            ve.relever_beta(ub.value, tax_rate, de_ratio) if ub.is_valid
            else _null(NullState.NOT_MEANINGFUL, "", "UNLEVERED_BETA_UNAVAILABLE"), None)
    else:
        for mid in ("VAL-UBETA-009", "VAL-LBETA-010"):
            add(mid, _null(NullState.MISSING, "", "BETA_OR_DEBT_EQUITY_UNAVAILABLE"), None)

    # VAL-FCFE-017 / VAL-FCFEV-018, the equity-side cash-flow pair.
    # `net_borrowing` is the change in total debt across the two latest
    # reported balance sheets -- a reported movement, not an assumption.
    prior = annual[1] if len(annual) > 1 else {}
    net_borrowing = (float(total_debt) - float(prior.get("total_debt")))\
        if total_debt is not None and prior.get("total_debt") is not None else None
    ni = _num(latest, "net_income")
    dna = _num(latest, "depreciation_and_amortization")
    if None not in (ni, dna, capex_latest, net_borrowing):
        # Change in non-cash working capital: VAL-NWC-006 computed at both
        # period ends a few sections above, so the delta is reported, not
        # assumed. Absent either end, zero -- and the row says so.
        dnwc = (float(nwc_v.value) - float(nwc_prior_v.value)
                if nwc_v.is_valid and nwc_prior_v.is_valid else 0.0)
        fcfe_v = ve.fcfe(float(ni), float(dna), abs(float(capex_latest)), dnwc,
                         float(net_borrowing))
        add("VAL-FCFE-017", fcfe_v, None)
        if fcfe_v.is_valid and ke_v.is_valid:
            fcfe_path = [fcfe_v.value * (1 + tv_growth) ** t for t in range(1, years + 1)]
            add("VAL-FCFEV-018",
                ve.fcfe_value(fcfe_path, ke_v.value, tv_growth), None)
        else:
            add("VAL-FCFEV-018", _null(NullState.MISSING, "usd",
                                       "FCFE_VALUE_INPUTS_UNAVAILABLE"), None)
    else:
        for mid, unit in (("VAL-FCFE-017", "usd"), ("VAL-FCFEV-018", "usd")):
            add(mid, _null(NullState.MISSING, unit, "FCFE_INPUTS_UNAVAILABLE"), None)

    # VAL-RD-002 / VAL-RDA-003. The R&D history is reported; the useful life
    # is not -- FORMULAS.md says plainly "Useful life is an industry
    # assumption", so the engine must not pick one. Absent it, both stay
    # MISSING with the remedy named (MISSING_DATA_POLICY.md).
    rd_life = overlay.get("rd_useful_life")
    rd_history = [float(r["rnd_expense"]) for r in reversed(annual)
                  if r.get("rnd_expense") is not None]
    if rd_life and rd_history:
        add("VAL-RD-002", ve.rd_asset(rd_history, int(rd_life)), None)
        add("VAL-RDA-003", ve.rd_amortization(rd_history, int(rd_life)), None)
    else:
        why = ("RD_USEFUL_LIFE_NOT_SUPPLIED: set `rd_useful_life` (years) in "
               "Entradas/<TICKER>.json. FORMULAS.md: \"Useful life is an industry "
               "assumption\", so no default is chosen here"
               if rd_history else "RD_HISTORY_UNAVAILABLE")
        add("VAL-RD-002", _null(NullState.MISSING, "usd", why), None)
        add("VAL-RDA-003", _null(NullState.MISSING, "usd", why), None)

    # VAL-LEASE-004, PV of operating-lease commitments at the pre-tax debt
    # rate. Needs the maturity schedule, which is Victor's own
    # `lease_schedule` dataset field and has no structured free source.
    lease_commitments = overlay.get("lease_commitments")
    if lease_commitments and kd_v.is_valid:
        add("VAL-LEASE-004",
            ve.lease_debt([float(x) for x in lease_commitments], kd_v.value), None)
    else:
        add("VAL-LEASE-004", _null(
            NullState.MISSING, "usd",
            "LEASE_SCHEDULE_NOT_SUPPLIED: set `lease_commitments` (payment per "
            "future year) in Entradas/<TICKER>.json -- DATASET.md's "
            "`lease_schedule`"), None)

    add("VAL-MC-037",
        mc_result.median if mc_result is not None
        else _null(NullState.MISSING, "usd_per_share", "MONTE_CARLO_INPUTS_UNAVAILABLE"), None)
    add("VAL-ENSEMBLE-044",
        ensemble_result.value if ensemble_result is not None
        else _null(NullState.MISSING, "usd_per_share", "ENSEMBLE_INPUTS_UNAVAILABLE"), None)

    # ---- Margin of safety (VAL-MOS-040) against the base scenario ----
    mos_v = ve.margin_of_safety(base_per_share, price) if base_per_share is not None and price is not None else _null(NullState.MISSING, "pct", "MOS_INPUTS_UNAVAILABLE")
    add("VAL-MOS-040", mos_v, None)

    # ---- Exit-multiple terminal value (VAL-TVE-013), cross-check only ----
    # Section 6.5: "use only as a cross-check ... a current-cycle multiple
    # cannot be copied blindly into perpetuity". So this never feeds the DCF;
    # it sits beside it. The valuation checklist also requires the *implied*
    # exit multiple be shown -- that is the one terminal growth, margin, ROIC
    # and WACC already justify, and therefore the reference any supplied
    # multiple has to be defended against.
    tve_v = _null(NullState.MISSING, "usd", "EXIT_MULTIPLE_TERMINAL_VALUE_INPUTS_UNAVAILABLE")
    terminal_value_share_v: float | None = None
    implied_exit_multiple_v: float | None = None
    if base_input is not None and revenue0 is not None and wacc_value is not None:
        try:
            tm = ve.terminal_year_metrics(
                base_input.growth, base_input.margin, base_input.wacc,
                base_input.tv_growth, revenue0, tax_rate, roic_value, years,
            )
        except ValueError:
            tm = None
        if tm is not None:
            implied_v = ve.implied_exit_multiple(tm["terminal_value"], tm["ebit"])
            # VAL-TVS-042 off the same priced path: PV(TV) / EV, where EV is
            # the discounted explicit FCFFs plus the discounted terminal value.
            _w = base_input.wacc
            _pv_term = tm["terminal_value"] / (1 + _w) ** years
            _pv_expl = sum(f / (1 + _w) ** (t + 1) for t, f in enumerate(tm["explicit_fcffs"]))
            _tvs = ve.terminal_share(_pv_term, _pv_expl + _pv_term)
            if _tvs.is_valid:
                terminal_value_share_v = _tvs.value
            if implied_v.is_valid:
                implied_exit_multiple_v = implied_v.value
            supplied = overlay.get("exit_multiple")
            if isinstance(supplied, (int, float)):
                tve_v = ve.exit_multiple_terminal_value(tm["ebit"], float(supplied))
            elif implied_v.is_valid:
                # No analyst multiple: state the implied one rather than
                # inventing a peer median FORMULAS.md does not ask for.
                tve_v = _null(
                    NullState.MISSING, "usd",
                    "EXIT_MULTIPLE_NOT_SUPPLIED: set `exit_multiple` (EV/EBIT on the "
                    "terminal year) in Entradas/<TICKER>.json. FORMULAS.md requires it be "
                    "justified by terminal fundamentals, so no peer median is assumed here")
            if implied_v.is_valid:
                assumptions.append(
                    f"VAL-TVE-013: the DCF's own terminal value implies an exit multiple of "
                    f"{implied_v.value:,.1f}x terminal EBIT ({tm['ebit']:,.0f} in year {years}); "
                    "that is the multiple terminal growth, margin, ROIC and WACC justify. "
                    "Cross-check only -- section 6.5 bars a current-cycle multiple from being "
                    "copied into perpetuity, and this never replaces VAL-TVG-012 in the DCF."
                )
    add("VAL-TVE-013", tve_v, None)

    # ---- Implied investor IRR (VAL-IRR-041) against the base scenario ----
    # FORMULAS.md frequency is "scenario", so all three are computed and
    # disclosed; the row carries the base case, matching VAL-MOS-040 above.
    # The registry's note -- "terminal value and holding period disclosed" --
    # is why the assumption states both, and why it says outright that no
    # distribution forecast is modelled: with none, this is a
    # price-appreciation-only IRR that understates total return for a payer,
    # and a reader who assumed dividends were in it would overstate the case.
    irr_v = (ve.implied_investor_irr(price, base_per_share, years)
             if base_per_share is not None and price is not None
             else _null(NullState.MISSING, "pct", "IRR_INPUTS_UNAVAILABLE"))
    add("VAL-IRR-041", irr_v, None)
    if irr_v.is_valid:
        by_scenario = []
        for name, per_share in (("bear", bear_per_share), ("base", base_per_share),
                                ("bull", bull_per_share)):
            if per_share is None:
                continue
            v = ve.implied_investor_irr(price, per_share, years)
            if v.is_valid:
                by_scenario.append(f"{name} {v.value:.1%}")
        assumptions.append(
            f"VAL-IRR-041: implied IRR over a {years}-year holding period from "
            f"{price:,.2f} to the scenario terminal value ({', '.join(by_scenario)}). "
            "No distribution or buyback forecast is modelled, so this is a "
            "price-appreciation-only IRR and understates total return for a "
            "company that pays out."
        )

    # ---- Growth-adjusted multiples (VAL-PEG-028, VAL-JPE-032, VAL-JEVS-033) ----
    # VAL-PEG-028 is "Forward P/E / Expected EPS growth percent". Prefer the
    # forward P/E from next-year consensus EPS (estimates.fmp_analyst_estimates);
    # only fall back to trailing EPS -- a documented proxy, FORMULAS.md's own
    # "definitions vary" caveat -- when no forward estimate is present.
    eps = _num(latest, "eps")
    _ny = _next_year_estimate(fmp_est, _last_reported_date(packet))
    fwd_eps = _est_field(_ny, "epsAvg", "estimatedEpsAvg") if _ny else None
    pe_eps = fwd_eps if (fwd_eps is not None and fwd_eps > 0) else eps
    used_trailing_pe = not (fwd_eps is not None and fwd_eps > 0)
    consensus_eps_growth_pct = overlay.get("eps_growth_pct")
    if pe_eps is not None and price and pe_eps > 0 and consensus_eps_growth_pct:
        pe = price / pe_eps
        v_peg = _ok(pe / (consensus_eps_growth_pct * 100), unit="ratio") if consensus_eps_growth_pct != 0 else _null(NullState.NOT_MEANINGFUL, "ratio", "PEG_ZERO_GROWTH")
        if used_trailing_pe and v_peg.is_valid:
            assumptions.append(
                "VAL-PEG-028: no forward EPS estimate available; used trailing P/E as a "
                "documented proxy for the registered forward P/E (FORMULAS.md: 'definitions vary')."
            )
    else:
        v_peg = _null(NullState.MISSING, "ratio", "PEG_INPUTS_UNAVAILABLE_OR_NEGATIVE_EARNINGS")
    add("VAL-PEG-028", v_peg, _score_from_anchor(v_peg, [(0.5, 10), (1.0, 7), (2.0, 3), (3.5, 0)]))

    roe = _num(latest, "net_income") / equity_t if _num(latest, "net_income") is not None and equity_t not in (None, 0) else None
    # `tv_growth`, not `base_growth`. INSTITUTIONAL_VALUATION_ENGINE.md 14.1
    # calls this "a stable-growth relationship" and FORMULAS.md types the row
    # "stable model" -- the `g` in `(1 - g/ROE)/(Ke - g)` is the perpetual
    # rate, not the near-term `reinvestment_rate * ROIC`. Feeding the
    # near-term figure sent MSFT and XOM past their own cost of equity, and
    # the formula correctly refused a metric that should have scored.
    v_jpe = ve.justified_pe(tv_growth, roe, ke_v.value) if roe is not None and ke_v.is_valid else _null(NullState.MISSING, "", "JUSTIFIED_PE_INPUTS_UNAVAILABLE")
    add("VAL-JPE-032", v_jpe, None)

    # ---- VAL-APV-019: adjusted present value ----
    # DECISION_RULES.md makes APV primary for "changing leverage / LBO-like"
    # and names what it replaces: "static WACC without debt path". A debt
    # *schedule* is therefore the whole input -- a single balance is the
    # static assumption APV exists to escape -- and no schedule can be
    # inferred from one balance sheet, so absence is MISSING with the remedy.
    debt_path = overlay.get("debt_schedule")
    ufcf = overlay.get("unlevered_fcf")
    apv_v = _null(NullState.MISSING, "usd",
                  "APV_INPUTS_NOT_SUPPLIED: set `debt_schedule` (balance per forecast year) "
                  "and `unlevered_fcf` in Entradas/<TICKER>.json. DECISION_RULES.md scopes APV "
                  "to changing leverage; a single reported balance is the static-WACC "
                  "assumption it exists to replace, so no path is inferred from one")
    if isinstance(debt_path, list) and debt_path and isinstance(ufcf, list) and ufcf:
        ku = overlay.get("unlevered_cost", ke_v.value if ke_v.is_valid else None)
        if ku is not None:
            apv = ve.adjusted_present_value(
                [float(x) for x in ufcf], float(ku), [float(x) for x in debt_path],
                tax_rate, overlay_float(overlay, "pretax_kd", kd_v.value if kd_v.is_valid else 0.05),
                overlay_float(overlay, "distress_probability", 0.0),
                overlay_float(overlay, "distress_cost", 0.0),
                overlay_float(overlay, "financing_side_effects", 0.0),
                terminal_growth=overlay.get("tv_growth"),
            )
            apv_v = apv["value"]
            if apv_v.is_valid:
                assumptions.append(
                    f"VAL-APV-019: unlevered operations {apv['pv_unlevered_operations']:,.0f} "
                    f"+ tax shields {apv['pv_tax_shields']:,.0f} "
                    f"- expected distress {apv['pv_distress']:,.0f} "
                    f"+ financing side effects {apv['financing_side_effects']:,.0f}. "
                    "Shields are discounted at the pre-tax cost of debt (their risk is the "
                    "risk of the debt that generates them), a modelling choice Cerebro does "
                    "not settle. "
                    + ("Distress is zero because neither probability nor cost was supplied -- "
                       "an assumption, not an absence."
                       if apv["pv_distress"] == 0 else "")
                )
    add("VAL-APV-019", apv_v, None)

    # ---- VAL-ROPT-038: real-option value ----
    # FORMULAS.md scopes it to "material, separable, discretionary projects"
    # and "after avoiding DCF double count". Both are judgements about a
    # specific project, so every input is analyst-supplied: nothing in a
    # filing says which projects are separable, and adding one already inside
    # the DCF's cash flows would count it twice.
    proj = overlay.get("real_option_project")
    ropt_v = _null(NullState.MISSING, "usd",
                   "REAL_OPTION_NOT_SUPPLIED: set `real_option_project` "
                   "{project_pv, investment, volatility, years} in Entradas/<TICKER>.json. "
                   "FORMULAS.md admits this only for material, separable, discretionary "
                   "projects and only after excluding them from the DCF, both of which are "
                   "judgements no filing states")
    if isinstance(proj, dict) and proj:
        rf_ropt = proj.get("risk_free", rf)
        if rf_ropt is not None:
            ropt_v = ve.real_option_value(
                float(proj.get("project_pv", 0.0)), float(proj.get("investment", 0.0)),
                float(proj.get("volatility", 0.0)), float(proj.get("years", 0.0)),
                float(rf_ropt))
            if ropt_v.is_valid:
                assumptions.append(
                    f"VAL-ROPT-038: {ropt_v.value:,.0f} on a project PV of "
                    f"{float(proj.get('project_pv', 0)):,.0f} against "
                    f"{float(proj.get('investment', 0)):,.0f} of staged investment, "
                    f"{float(proj.get('volatility', 0)):.0%} volatility over "
                    f"{float(proj.get('years', 0)):g} years. This is additive ONLY if the "
                    "project is excluded from the DCF cash flows above -- FORMULAS.md's "
                    "'after avoiding DCF double count' is the caller's to enforce."
                )
    add("VAL-ROPT-038", ropt_v, None)

    # ---- VAL-CONV-039: convertible and option dilution ----
    # VAL-T008's expected result is "per-share value incomplete" when the
    # schedule is missing -- a statement the output has to make, not just a
    # calculation it declines to do. The instruments live in the 10-K's
    # equity and debt notes and no endpoint carries them, so absence is the
    # normal case and has to read as incomplete rather than as undiluted.
    convertibles = overlay.get("convertibles")
    options_sched = overlay.get("options_outstanding")
    conv_v = _null(NullState.MISSING, "usd_per_share",
                   "CONVERTIBLE_SCHEDULE_NOT_SUPPLIED: set `convertibles` "
                   "[{name, face, conversion_price, conversion_shares}] and/or "
                   "`options_outstanding` [{name, count, strike}] in Entradas/<TICKER>.json "
                   "from the 10-K equity and debt notes. VAL-T008: without them the "
                   "per-share value is INCOMPLETE, not undiluted")
    if (convertibles or options_sched) and base_per_share is not None and diluted_shares:
        # Scenario-consistent: each scenario's own per-share value is the price
        # that decides whether an instrument is in the money. The row carries
        # the base case, matching VAL-MOS-040 and VAL-IRR-041.
        equity_base = base_per_share * diluted_shares
        d = ve.convertible_dilution(equity_base, diluted_shares, base_per_share,
                                    convertibles, options_sched)
        conv_v = d["per_share"]
        if conv_v.is_valid:
            assumptions.append(
                f"VAL-CONV-039: base-case dilution moves {diluted_shares:,.0f} shares to "
                f"{d['shares_diluted']:,.0f} and per-share value from {base_per_share:,.2f} "
                f"to {conv_v.value:,.2f}. Converted: "
                f"{', '.join(d['converted']) or 'none'}; exercised: "
                f"{', '.join(d['exercised']) or 'none'}. If-converted credits the extinguished "
                f"face ({d['equity_value_added']:,.0f}) to equity, not only the shares; options "
                "use the treasury method. Dilution is scenario-consistent, so an instrument out "
                "of the money in the bear case does not dilute it."
            )
    elif base_per_share is not None:
        mandatory_flags_extra.append("PER_SHARE_VALUE_INCOMPLETE_NO_DILUTION_SCHEDULE")
    add("VAL-CONV-039", conv_v, None)

    # ---- VAL-SOTP-026: sum of the parts ----
    # DECISION_RULES.md makes this the primary model for a multi-segment
    # conglomerate and names what it replaces: "one blended multiple without
    # segment logic". FORMULAS.md's caution is the same failure -- "avoid
    # applying a multiple to consolidated metrics twice" -- which is exactly
    # why no company-level multiple is substituted when per-segment ones are
    # absent. The segments and their revenue are reported (FMP segmentation);
    # what each is worth is not, and inventing it would be the double-count.
    seg_rev = overlay.get("segment_revenue")
    seg_multiples = overlay.get("segment_multiples")
    sotp_v = _null(NullState.MISSING, "usd", "SOTP_SEGMENT_REVENUE_UNAVAILABLE")
    if isinstance(seg_rev, dict) and len(seg_rev) >= 2:
        if isinstance(seg_multiples, dict) and seg_multiples:
            missing = [k for k in seg_rev if k not in seg_multiples]
            if missing:
                sotp_v = _null(
                    NullState.MISSING, "usd",
                    "SOTP_SEGMENT_MULTIPLES_INCOMPLETE: no multiple for "
                    f"{', '.join(sorted(missing))}. Applying another segment's multiple "
                    "to them is the blended-multiple failure DECISION_RULES.md names")
            else:
                sotp_v = ve.sum_of_the_parts(
                    [seg_rev[k] * float(seg_multiples[k]) for k in seg_rev],
                    corporate_assets=cash or 0.0,
                    corporate_claims=total_debt or 0.0,
                    holding_discount=overlay_float(overlay, "holding_discount", 0.0),
                )
        else:
            sotp_v = _null(
                NullState.MISSING, "usd",
                "SOTP_SEGMENT_MULTIPLES_NOT_SUPPLIED: set `segment_multiples` "
                "(EV/Sales per segment name) in Entradas/<TICKER>.json. FORMULAS.md bars "
                "applying one consolidated multiple across segments, so none is assumed")
        assumptions.append(
            "VAL-SOTP-026: reported segments and revenue -- "
            + "; ".join(f"{k} {v:,.0f}" for k, v in sorted(
                seg_rev.items(), key=lambda kv: -kv[1]))
            + ". Each needs its own peer multiple (`segment_multiples` in "
            "Entradas/<TICKER>.json); the matrix's rejected model for this company type is "
            "'one blended multiple without segment logic', so no company-level multiple is "
            "substituted for a missing one."
        )
    add("VAL-SOTP-026", sotp_v, None)

    # ---- VAL-DDM-024 / VAL-HDDM-025: dividend-discount models ----
    # DECISION_RULES.md's matrix makes these *primary* for banks and insurers
    # -- the company types whose FCFF it forbids -- and a secondary check for
    # mature non-financials. D0 comes from EDGAR XBRL (tier 1, free); the
    # growth rate is measured off the same reported series rather than
    # borrowed from the FCFF path, because FORMULAS.md conditions both models
    # on payout stability, which is a property of the dividend history.
    d0 = overlay.get("dividend_per_share")
    dps_history = overlay.get("dividends_per_share_history") or []
    div_growth, stability_note = _dividend_growth(dps_history)

    if not isinstance(d0, (int, float)) or not ke_v.is_valid:
        v_ddm = _null(NullState.MISSING, "usd_per_share", "DDM_INPUTS_UNAVAILABLE")
        v_hddm = _null(NullState.MISSING, "usd_per_share", "HDDM_INPUTS_UNAVAILABLE")
    else:
        g_short = div_growth if div_growth is not None else base_growth
        # Gordon's `g` is a PERPETUAL rate, so DECISION_RULES.md rule 3
        # governs it directly: "Terminal growth must be below WACC/cost of
        # equity and consistent with long-run nominal economic growth in the
        # valuation currency." `g_short` is neither -- it is the CAGR of the
        # reported dividend series, which for MSFT, XOM and JNJ exceeds their
        # own cost of equity and made the model refuse.
        #
        # Clamping it to just under Ke would be worse than refusing: the
        # denominator collapses and the value explodes. The rate rule 3
        # actually names is the long-run one, which is `tv_growth` -- already
        # clamped under WACC a few sections above. That also makes the pair
        # coherent rather than redundant: VAL-DDM-024 is the stable-growth
        # model and VAL-HDDM-025 the fading one, both landing on one long-run
        # rate. `g_short` stays reported in the assumptions below, since it is
        # what qualifies the payout as stable in the first place.
        v_ddm = ve.gordon_dividend_value(float(d0), tv_growth, ke_v.value)
        # H is half the transition length; the transition is the same forecast
        # horizon the DCF uses, and gL is the same terminal growth, so the two
        # models fade to one long-run rate rather than to two.
        v_hddm = ve.h_model_dividend_value(
            float(d0), g_short, tv_growth, years / 2.0, ke_v.value)
    add("VAL-DDM-024", v_ddm, None)
    add("VAL-HDDM-025", v_hddm, None)
    if v_ddm.is_valid or v_hddm.is_valid:
        assumptions.append(
            f"VAL-DDM-024/VAL-HDDM-025: D0={float(d0):,.2f}/share "
            f"({overlay.get('dividend_tag', 'XBRL')}, period ending "
            f"{overlay.get('dividend_period_end', 'n/a')}), dividend growth "
            f"{(div_growth if div_growth is not None else base_growth):.2%} measured over "
            f"{max(len(dps_history) - 1, 0)} reported years, cost of equity {ke_v.value:.2%}. "
            f"The H-model fades that to {tv_growth:.2%} over {years} years (H={years / 2.0:g}). "
            "FORMULAS.md conditions both on stable payout; the matrix makes them primary "
            "for banks and insurers and a secondary check elsewhere. "
            f"Gordon denominator (Ke - g) = "
            f"{ke_v.value - (div_growth if div_growth is not None else base_growth):.2%}: the "
            "closer that spread runs to zero the more VAL-DDM-024 amplifies small changes in "
            "either input, which is what the H-model's faded rate is there to cross-check. "
            + stability_note
        )

    # ---- VAL-JPB-031: justified price-to-book ----
    # `(ROE - g)/(Ke - g)`. DECISION_RULES.md's model-selection matrix names
    # "P/B vs ROE" as the secondary check for banks and insurers, which is
    # this formula in words. Reported, not scored: SCORING.md's dimensions
    # name VAL-PEG-028/JPE-032/JEVS-033 and VAL-REL-034/ZHIST-035, and 031
    # falls in the gap between those two lists.
    # Stable-growth `g` for the same reason as VAL-JPE-032:
    # INSTITUTIONAL_VALUATION_ENGINE.md:387 introduces this as "a
    # stable-growth justified price-to-book relationship".
    v_jpb = (ve.justified_pb(roe, tv_growth, ke_v.value)
             if roe is not None and ke_v.is_valid
             else _null(NullState.MISSING, "x", "JUSTIFIED_PB_INPUTS_UNAVAILABLE"))
    add("VAL-JPB-031", v_jpb, None)
    if v_jpb.is_valid and price and diluted_shares and equity_t not in (None, 0):
        actual_pb = price * diluted_shares / equity_t
        note = (
            f"VAL-JPB-031: justified P/B is {v_jpb.value:,.2f}x on ROE {roe:.1%} "
            f"({_num(latest, 'net_income'):,.0f} on {equity_t:,.0f} of book equity) against a "
            f"{ke_v.value:.1%} cost of equity; the market trades it at {actual_pb:,.2f}x book. "
            "A business earning exactly its cost of equity is worth book (1.0x), so the "
            "premium is the spread between what equity earns and what it costs. "
            "FORMULAS.md conditions this on stable ROE and payout."
        )
        # DECISION_RULES.md's model-selection matrix names "P/B vs ROE" as the
        # secondary check for banks and insurers only; other company types get
        # DDM/relative multiples there. Saying so matters most where book
        # equity has been compressed by buybacks -- AAPL's 151.9% ROE sits on
        # equity its repurchases shrank, so the multiple is arithmetically
        # right and economically weightless, and a reader shown 134.92x with
        # no scope note would take it for a valuation signal.
        if packet.analysis.industry_adapter not in ("banks", "insurers"):
            note += (
                f" Scope: the model-selection matrix assigns P/B vs ROE to banks and insurers; "
                f"for industry_adapter={packet.analysis.industry_adapter!r} this is context, "
                "not a valuation check -- book equity is not the base its models run on."
            )
        assumptions.append(note)
    trailing_pe_v = _ok(price / eps, unit="ratio") if price and eps and eps > 0 else _null(NullState.MISSING, "ratio", "TRAILING_PE_UNAVAILABLE")
    peg_style_v = _null(NullState.MISSING, "ratio", "JPE_COMPARISON_UNAVAILABLE")
    if v_jpe.is_valid and trailing_pe_v.is_valid and v_jpe.value != 0:
        peg_style_v = _ok((trailing_pe_v.value - v_jpe.value) / v_jpe.value, unit="pct")
    peg_style_score = _score_from_anchor(peg_style_v, [(0.50, 0), (0.15, 4), (0.0, 7), (-0.15, 10)])

    v_jevs = (
        # Stable-growth `g` (FORMULAS.md types VAL-JEVS-033 "stable model");
        # the guard here is `g < WACC` rather than `g < Ke`.
        ve.justified_ev_sales(op_margin_latest, tax_rate, tv_growth, roic_value, wacc_value)
        if wacc_value is not None else _null(NullState.MISSING, "", "JUSTIFIED_EV_SALES_INPUTS_UNAVAILABLE")
    )
    add("VAL-JEVS-033", v_jevs, None)

    rdcf_plausibility_score = None
    if reverse_dcf_summary.implied_revenue_cagr is not None:
        reference_growth = consensus_growth if consensus_growth is not None else base_growth
        implied_vs_reference = reverse_dcf_summary.implied_revenue_cagr - reference_growth
        rdcf_plausibility_score = anchor_score(implied_vs_reference, [(0.10, 0), (0.02, 5), (-0.02, 8), (-0.10, 10)])

    # ---- Historical/peer comparison (VAL-REL-034, VAL-ZHIST-035) ----
    hist_multiples = overlay.get("historical_multiples")
    v_zhist = ve.hist_zscore(trailing_pe_v.value, hist_multiples) if hist_multiples and trailing_pe_v.is_valid else _null(NullState.MISSING, "", "ZHIST_UNAVAILABLE")
    add("VAL-ZHIST-035", v_zhist, _score_from_anchor(v_zhist, [(2.0, 0), (0.5, 5), (0.0, 7), (-1.0, 10)]))
    if n_peers < MIN_PEERS and not hist_multiples:
        assumptions.append(f"Only {n_peers} peers available from Packet.estimates.peers (need >={MIN_PEERS}) and no overlay['historical_multiples'] supplied; historical/peer dimension falls back to history-only or NOT_SCORABLE.")

    # ---- Cash-flow and earnings yield (VAL-EY-029, VAL-FCFY-030) ----
    v_ey = _ok(eps / price, unit="pct") if eps is not None and eps > 0 and price else _null(NullState.NOT_MEANINGFUL, "pct", "EARNINGS_YIELD_NONPOSITIVE_EPS_OR_PRICE")
    add("VAL-EY-029", v_ey, _score_from_anchor(v_ey, [(0.0, 0), (0.03, 4), (0.06, 7), (0.10, 10)]))
    fcf_latest = _num(latest, "fcf")
    market_cap = e_value
    v_fcfy = _ok(fcf_latest / market_cap, unit="pct") if fcf_latest is not None and market_cap else _null(NullState.MISSING, "pct", "FCF_YIELD_INPUTS_UNAVAILABLE")
    add("VAL-FCFY-030", v_fcfy, _score_from_anchor(v_fcfy, [(0.0, 0), (0.02, 4), (0.05, 7), (0.08, 10)]))

    by_id = {r.metric_id: r for r in rows}

    metric_rows: list[MetricRow] = []
    for r in rows:
        score: float | str = r.score10 if r.score10 is not None else "NOT_SCORABLE"
        metric_rows.append(MetricRow.from_value(r.metric_id, r.value, formula_id=r.metric_id, formula_version=_VERSION, score=score, confidence=r.confidence))

    # ---- DIM_MULTIPLES (3 pts) ----
    multiples_scores: list[tuple[float, Value]] = [
        (0.35, Value.of(by_id["VAL-PEG-028"].score10, unit="score") if by_id["VAL-PEG-028"].score10 is not None else Value.null(NullState.NOT_SCORABLE, unit="score")),
        (0.25, Value.of(peg_style_score, unit="score") if peg_style_score is not None else Value.null(NullState.NOT_SCORABLE, unit="score")),
        (0.40, Value.of(rdcf_plausibility_score, unit="score") if rdcf_plausibility_score is not None else Value.null(NullState.NOT_SCORABLE, unit="score")),
    ]
    multiples_dim = Dimension(name=DIM_MULTIPLES, max_points=DIMENSION_MAX_POINTS[DIM_MULTIPLES], metric_scores=multiples_scores)

    # ---- DIM_HIST_PEER (2 pts): min 8 peers or history-only ----
    hist_peer_score = by_id["VAL-ZHIST-035"].score10
    hist_peer_scores: list[tuple[float, Value]] = [
        (1.0, Value.of(hist_peer_score, unit="score") if hist_peer_score is not None else Value.null(NullState.NOT_SCORABLE, unit="score")),
    ]
    hist_peer_dim = Dimension(name=DIM_HIST_PEER, max_points=DIMENSION_MAX_POINTS[DIM_HIST_PEER], metric_scores=hist_peer_scores)

    # ---- DIM_CF_YIELD (2 pts) ----
    cf_yield_scores: list[tuple[float, Value]] = [
        (0.5, Value.of(by_id["VAL-EY-029"].score10, unit="score") if by_id["VAL-EY-029"].score10 is not None else Value.null(NullState.NOT_SCORABLE, unit="score")),
        (0.5, Value.of(by_id["VAL-FCFY-030"].score10, unit="score") if by_id["VAL-FCFY-030"].score10 is not None else Value.null(NullState.NOT_SCORABLE, unit="score")),
    ]
    cf_yield_dim = Dimension(name=DIM_CF_YIELD, max_points=DIMENSION_MAX_POINTS[DIM_CF_YIELD], metric_scores=cf_yield_scores)

    # ---- DIM_FAIR_VALUE_SCENARIOS (2 pts): WACC>g, diluted shares, scenario audit ----
    scenario_gate_passes = (
        wacc_value is not None and wacc_value > tv_growth and diluted_shares is not None
        and scenario_result is not None and abs(scenario_result.probabilities_sum - 1.0) < 1e-6
    )
    if scenario_gate_passes and price is not None and bear_per_share is not None and base_per_share is not None and bull_per_share is not None:
        if price > bull_per_share:
            fv_score = 1.0
        elif price > base_per_share:
            fv_score = 5.0
        elif price >= bear_per_share:
            fv_score = 8.0
        else:
            fv_score = 10.0
    else:
        fv_score = None
    fair_value_scores: list[tuple[float, Value]] = [
        (1.0, Value.of(fv_score, unit="score") if fv_score is not None else Value.null(NullState.NOT_SCORABLE, unit="score")),
    ]
    if not scenario_gate_passes:
        assumptions.append("Fair-value-by-scenarios gate not satisfied (needs WACC>terminal growth, diluted shares, and probabilities summing to 1) -- dimension NOT_SCORABLE.")
    fair_value_dim = Dimension(name=DIM_FAIR_VALUE_SCENARIOS, max_points=DIMENSION_MAX_POINTS[DIM_FAIR_VALUE_SCENARIOS], metric_scores=fair_value_scores)

    # ---- DIM_MOS (1 pt): 0-15% -> 4-6; >15% -> 7-10; low confidence caps 5 ----
    mos_score = _score_from_anchor(mos_v, [(-0.20, 0), (0.0, 3), (0.15, 6), (0.30, 10)])
    mos_scores: list[tuple[float, Value]] = [
        (1.0, Value.of(mos_score, unit="score") if mos_score is not None else Value.null(NullState.NOT_SCORABLE, unit="score")),
    ]
    # SCORING.md MOS gate: "Low-confidence valuation caps dimension at 5."
    # Cerebro does not quantify "low confidence", so this module reads it as a
    # cross-model dispersion above 30% of the base-case value -- an engine
    # calibration, disclosed when it bites.
    low_confidence = dispersion is not None and base_per_share and dispersion / abs(base_per_share) > 0.30
    if low_confidence:
        mos_scores = apply_dimension_cap(mos_scores, cap=5.0)
        assumptions.append(
            f"MOS dimension capped at 5 (SCORING.md low-confidence gate): cross-model "
            f"dispersion {dispersion:.2f} exceeds 30% of the base-case value "
            f"{base_per_share:.2f} -- the 30% 'low confidence' threshold is this module's "
            "dated 2.0.0 calibration, Cerebro states the cap but not the number."
        )
    mos_dim = Dimension(name=DIM_MOS, max_points=DIMENSION_MAX_POINTS[DIM_MOS], metric_scores=mos_scores)

    dimensions = [multiples_dim, hist_peer_dim, cf_yield_dim, fair_value_dim, mos_dim]

    # AGENT.md's no-speculation rule: every scored metric's 0-10 scale is the
    # module's calibration (the valuation FORMULAS.md states no bands), so it
    # rides in the output as a disclosed assumption.
    assumptions.extend(_anchor_disclosures())

    cat = Category(name=AGENT_ID, max_points=MAX_POINTS, dimensions=dimensions)
    awarded_points = cat.points()
    dim_score10 = cat.score10()
    coverage = cat.coverage()

    mandatory_flags: list[str] = list(mandatory_flags_extra)
    if scenario_result is not None:
        for name, v in (("bear", scenario_result.bear_value), ("base", scenario_result.base_value), ("bull", scenario_result.bull_value)):
            if "TERMINAL_VALUE_SHARE_ABOVE_75PCT" in v.warnings:
                mandatory_flags.append("HIGH_TERMINAL_SENSITIVITY")
    if reconciled is False:
        mandatory_flags.append("FCFF_ECONOMIC_PROFIT_RECONCILIATION_FAILED")

    label = verdict(dim_score10)

    category = CategoryStats(max_points=MAX_POINTS, awarded_points=awarded_points, score_10=dim_score10, confidence=_category_confidence(coverage, packet))
    status = status_from_coverage(coverage)

    passed = 0
    failed = 0
    checks = [
        abs((category.awarded_points or 0.0) - Category(name=AGENT_ID, max_points=MAX_POINTS, dimensions=dimensions).points()) < 1e-6,
        all(row.formula_id for row in metric_rows),
        bool(packet.analysis.knowledge_timestamp),
        category.confidence is not None,
    ]
    for ok in checks:
        passed += 1 if ok else 0
        failed += 0 if ok else 1
    validation_tests = ValidationTestsSummary(passed=passed, failed=failed, warnings=0)

    return ValuationOutput(
        agent_id=AGENT_ID,
        status=status,
        security=SecurityRef(ticker=packet.security.ticker, exchange=packet.security.exchange, currency=packet.security.reporting_currency),
        knowledge_timestamp=packet.analysis.knowledge_timestamp,
        category=category,
        verdict=label,
        coverage=coverage,
        dimensions=dimensions,
        metrics=metric_rows,
        mandatory_flags=mandatory_flags,
        assumptions=assumptions,
        judgment_requests=judgment_requests,
        source_lineage=["packet.fundamentals.annual", "packet.facts_table", "packet.capital_structure", "packet.estimates"],
        validation_tests=validation_tests,
        model_selection=ModelSelection(primary=["FCFF_DCF", "ECONOMIC_PROFIT"], rejected=["DDM", "RELATIVE_MULTIPLES"]),
        normalization_reconciliation=[],
        wacc=WaccSummary(
            value=wacc_value,
            # DECISION_RULES.md's WACC rules are stated on the *inputs*:
            # "Risk-free rate, ERP, cash flows, and terminal growth use the
            # same currency" and "use bottom-up beta from comparable operating
            # businesses where possible". Publishing only the two outputs left
            # a reader unable to check either rule -- the CAPM inputs behind
            # `cost_of_equity` appeared nowhere in the output at all.
            components={
                "risk_free_rate": float(rf) if rf is not None else None,
                "beta": float(beta) if beta is not None else None,
                "erp": erp,
                "cost_of_equity": ke_v.value if ke_v.is_valid else None,
                "cost_of_debt": kd_v.value if kd_v.is_valid else None,
                "tax_rate": tax_rate,
            },
            sensitivity=(
                [{"minus_bp": wacc_sens.minus_bp.value, "plus_bp": wacc_sens.plus_bp.value}] if wacc_sens is not None and wacc_sens.minus_bp.is_valid else []
            ),
        ),
        scenarios=scenario_summaries,
        reverse_dcf=reverse_dcf_summary,
        model_cross_checks=ModelCrossChecks(fcff=fcff_per_share, economic_profit=econ_profit_per_share, residual_income=None, relative=relative_value, dispersion=dispersion),
        fair_value_distribution=fv_dist,
        terminal_value_share=terminal_value_share_v,
        implied_exit_multiple=implied_exit_multiple_v,
        reference_bands=ReferenceBands(
            bear=bear_per_share, base=base_per_share, bull=bull_per_share,
            margin_of_safety_15pct=base_per_share * 0.85 if base_per_share is not None else None,
            margin_of_safety_25pct=base_per_share * 0.75 if base_per_share is not None else None,
        ),
    )


def _category_confidence(coverage: float, packet: Packet) -> float:
    coverage_component = max(0.0, min(1.0, coverage)) * 100.0
    source_quality = 85.0
    freshness = 100.0 if packet.staleness.get("consensus", "FRESH") == "FRESH" else 50.0
    consistency = 75.0
    # Model fit is how well a discounted-cash-flow valuation suits this
    # company, so it tracks the same three-way split the gate above uses:
    # a full fit where the default formulas apply, a reduced one where
    # the model runs on inputs the adapter says must be normalized first,
    # and a poor one where the methodology asks for a different model
    # entirely. Collapsing that to 90/40 told a software company its
    # valuation barely fit, on an adapter that only adds metrics.
    model_fit = _adapters.model_fit(packet.analysis.industry_adapter)
    return _confidence_formula(
        coverage=coverage_component, source_quality=source_quality, freshness=freshness,
        consistency=consistency, model_fit=model_fit,
    )
