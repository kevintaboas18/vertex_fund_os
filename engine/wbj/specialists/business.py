"""Business specialist (Task 15): BUS-001..030 (`FORMULAS.md`'s
BUS-MIX-001..BUS-SBC-030) and the five weighted business dimensions
(20 pts).

Sources of truth (`Cerebro/01_business_analysis/`):
- `FORMULAS.md`: the 30 registered formulas.
- `DECISION_RULES.md`: the wide-moat gate, business verdict bands, and
  mandatory flags (`VALUE_DESTRUCTION`, `CONCENTRATION_RED_FLAG`,
  `DILUTION_RED_FLAG`).
- `SCORING.md`: the five weighted dimensions and their per-dimension
  gate/cap column.
- `OUTPUT_SCHEMA.md`: `BusinessOutput`'s extension fields.
- `Cerebro/shared/OUTPUT_CONTRACT.md` / `HANDOFF_CONTRACT.md`: the common
  envelope, implemented in `wbj.specialists.common`.

## ROIC/spread/EVA/incremental-ROIC reuse

Per the task-15 brief ("ROIC/spread/EVA reuse `wbj.engines.valuation_engine`
functions where they exist"), `nopat`/`roic`/`spread`/`eva`/
`incremental_roic`/`fundamental_growth` below are thin re-exports of the
Task 13 valuation engine's own functions (module docstring precedent:
FIN-EF-023..027 in `financial.py` re-implements ROE/ROIC/ROA rather than
reusing the engine because `financial_analysis` predates
`valuation_engine`'s existence; `business_analysis` does not have that
excuse, so it imports directly). Each is still assigned its own `BUS-*`
metric id on the `MetricRow` it produces -- the *formula*, not the id, is
shared.

## Dataset coverage vs. `Packet`

`DATASET.md` marks most of this specialist's inputs "conditional":
`recurring_revenue_5y`, `customer_revenue_shares`, `market_share_...`,
`retention_churn_cohorts`, `customer_economics`, and
`management_guidance_history` are not part of `wbj.schemas.packet.Packet`
(no segment/customer/cohort/guidance block exists there), and
`customer_concentration` / `market_share` are additionally on
`wbj.core.nullstates.PROHIBITED_IMPUTATION` -- this module never infers
them from other reported numbers. `run(packet, overlay)` accepts them via
`overlay` (mirroring `financial.py`'s `overlay["wacc"]` precedent) so the
formula functions are directly testable and `run()` degrades honestly
(`MISSING`, not a guess) when the overlay omits them. Supported overlay
keys: `wacc` (float, required for every ROIC/spread/EVA-based metric),
`largest_customer_share` (0-1 decimal), `customer_shares` (list of 0-1
decimals, for the customer HHI), `segment_shares` (list of 0-1 decimals),
`recurring_revenue` (float), `guidance_history` (list of
`{"actual": float, "guidance_midpoint": float}`), `peer_roic` (list of
floats, >=8 for `wbj.core.scoring.peer_score` to engage),
`retention` (dict with `begin`/`expansion`/`contraction`/`churn` for
NRR/GRR), `customer_economics` (dict with `arpu`, `gross_margin`,
`customer_life_years`, `cac_spend`, `new_customers`, `monthly_arpu`).

## Moat classification, "2 quantitative moat effects", thesis killers

`DECISION_RULES.md`'s wide-moat gate condition 3 is "At least two
independent moat effects are quantitatively visible". This module computes
the mechanical conditions around it — spread persistence, margin range,
concentration — but cannot enumerate which qualitative moat *category*
(retention/switching costs, cost advantage, network scale,
regulated/intangible protection, efficient scale) a piece of evidence
belongs to. That classification, and `DECISION_RULES.md`'s mandatory
"three business-model or execution risks", are judgment requests.

Neither scores a dimension. DATA_POLICY.md's `Q` class is "qualitative
evidence that is not scored unless a conversion rule exists", and Cerebro
defines no conversion from `Wide|Narrow|None` to a 0-10 score. The
classification still fills OUTPUT_SCHEMA.md's `moat` block and settles the
gate's condition 3; the risks are checked by
`THESIS_KILLERS_NOT_LISTED`.

## Dimension-level caps vs. mandatory-flag caps

`SCORING.md`'s dimension table has its own "Gate / cap" column (e.g. "Score
capped at 6 without positive ROIC-WACC spread") -- unlike `financial.py`'s
`capped_verdict` (which caps only the *label*, per
`SpecialistOutput.verdict`'s docstring), these are baked directly into the
computed dimension score itself via `common.apply_dimension_cap`: they are
part of the same deterministic point math `Category(dimensions)` reproduces
from, not a post-hoc override on the outward-facing verdict. The
*business-verdict* table's own "Additional condition" for the Excellent
band (moat gate, ROIC>=20%, FCF conversion>=0.9x) and the
`VALUE_DESTRUCTION` flag (ROIC<WACC) DO cap the verdict label only, mirroring
`financial.py`'s override discipline -- see `capped_verdict`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date, timedelta as _timedelta
from typing import Any, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from wbj.core import adapters as _adapters
from wbj.core import confidence_inputs as _confidence_inputs
from wbj.core import periods as _periods
from wbj.core import taxes as _taxes
from wbj.core.confidence import confidence as _confidence_formula
from wbj.core.formulas import cagr as _cagr
from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import (
    COVERAGE_COMPLETE,
    COVERAGE_USABLE,
    Category,
    Dimension,
    anchor_score,
)
from wbj.engines import valuation_engine as ve
from wbj.schemas.packet import Packet
from wbj.specialists.common import (
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
    "DIM_MOAT",
    "DIM_COMPETITIVE",
    "DIM_MANAGEMENT",
    "DIM_DURABILITY",
    "DIM_CUSTOMER",
    "DIMENSION_NAMES",
    "BUS_30_IDS",
    "MoatSummary",
    "BusinessOutput",
    "capped_verdict",
    "UNREGISTERED_METHODOLOGY_DISCLOSURES",
    "MoatGateInputs",
    "verdict",
    "run",
    "value_destruction_triggered",
    "is_concentration_red_flag",
    "is_dilution_red_flag",
    "margin_range_is_stable",
    "wide_moat_margin_range_ok",
    # formula functions
    "segment_revenue_share",
    "recurring_revenue_pct",
    "largest_customer_concentration",
    "customer_hhi",
    "segment_hhi",
    "revenue_cagr",
    "gross_margin",
    "operating_margin",
    "margin_stability",
    "margin_range",
    "nopat",
    "average_invested_capital",
    "roic",
    "spread",
    "eva",
    "incremental_roic",
    "cumulative_fcf_conversion",
    "reinvestment_rate",
    "fundamental_growth",
    "net_revenue_retention",
    "gross_revenue_retention",
    "logo_churn",
    "customer_ltv",
    "customer_acquisition_cost",
    "ltv_to_cac",
    "cac_payback_months",
    "guidance_accuracy",
    "diluted_share_cagr",
    "capital_allocation_spread",
    "sbc_burden",
]

_VERSION = "2.0.0"
AGENT_ID = "business_analysis"
MAX_POINTS = 20.0

DIM_MOAT = "moat_and_pricing_power"
DIM_COMPETITIVE = "competitive_position"
DIM_MANAGEMENT = "management_and_capital_allocation"
DIM_DURABILITY = "business_durability"
DIM_CUSTOMER = "customer_economics"

DIMENSION_NAMES = (DIM_MOAT, DIM_COMPETITIVE, DIM_MANAGEMENT, DIM_DURABILITY, DIM_CUSTOMER)

DIMENSION_MAX_POINTS: dict[str, float] = {
    DIM_MOAT: 5.0,
    DIM_COMPETITIVE: 4.0,
    DIM_MANAGEMENT: 4.0,
    DIM_DURABILITY: 4.0,
    DIM_CUSTOMER: 3.0,
}
assert sum(DIMENSION_MAX_POINTS.values()) == MAX_POINTS

# The 30 registered formulas, FORMULAS.md order.
BUS_30_IDS: tuple[str, ...] = (
    "BUS-MIX-001", "BUS-REC-002", "BUS-CONC-003", "BUS-HHI-004", "BUS-HHI-005",
    "BUS-CAGR-006", "BUS-GM-007", "BUS-OM-008", "BUS-STAB-009", "BUS-RANGE-010",
    "BUS-NOPAT-011", "BUS-IC-012", "BUS-ROIC-013", "BUS-SPREAD-014", "BUS-EVA-015",
    "BUS-IROIC-016", "BUS-FCFC-017", "BUS-REINV-018", "BUS-SG-019", "BUS-NRR-020",
    "BUS-GRR-021", "BUS-CHURN-022", "BUS-LTV-023", "BUS-CAC-024", "BUS-LTVCAC-025",
    "BUS-PAYBACK-026", "BUS-GUIDE-027", "BUS-DIL-028", "BUS-ALLOC-029", "BUS-SBC-030",
)
assert len(BUS_30_IDS) == 30


# INDUSTRY_ADAPTERS.md lists NRR, GRR, churn, CAC payback and LTV/CAC
# under "SaaS / subscriptions" as metrics to *add* for that adapter — they
# are not part of the default non-financial set. SCORING.md's customer-
# economics row says as much: "If not applicable, use adapter metrics; do
# not impute." Reporting them MISSING on a chip maker and scoring the
# dimension zero is imputing exactly what that forbids.
_SUBSCRIPTION_ADAPTERS = _adapters.SUBSCRIPTION_ADAPTERS


def _slot(row: Any) -> Value:
    """One metric's contribution to a dimension, keeping *why* it is null.

    Collapsing every null into NOT_SCORABLE erases the distinction
    MISSING_DATA_POLICY.md's decision tree opens with. A subscription
    metric on a chip maker did not fail to source — it does not apply,
    and `Dimension.applicable_weight` can only drop it from the
    denominator if the state survives to here.
    """
    if row.score10 is not None:
        return Value.of(row.score10, unit="score")
    if getattr(row.value, "state", None) is NullState.NOT_APPLICABLE:
        return Value.null(NullState.NOT_APPLICABLE, unit="score",
                          warnings=list(getattr(row.value, "warnings", []) or []))
    return Value.null(NullState.NOT_SCORABLE, unit="score")


# DECISION_RULES.md's TAM source-quality tiers and the confidence each
# earns. Kept as a literal of the documented table rather than imported
# from market.py: this reads a shared *constant*, not another
# specialist's output, and the independence rule is about scores.
_TAM_TIER_CONFIDENCE: dict[int, float] = {1: 100.0, 2: 85.0, 3: 70.0, 4: 45.0, 5: 0.0}
#: CONFIDENCE_ENGINE.md's label table: 85-100 High, 70-84 Medium,
#: 50-69 Low, <50 Insufficient. SCORING.md caps competitive position
#: "if market definition is low confidence", and Victor's own table puts
#: the top of Low at 69 — so the cap lifts at 70, where Medium begins.
#: This was 60, a number that appears nowhere in his documents and
#: contradicts them: a source scoring 60-69 is Low by his table and
#: would have lifted a cap he says to keep. No current tier lands in that
#: range, so the reading was latent rather than wrong in practice — the
#: kind of defect that waits for a table to change.
_LOW_CONFIDENCE_CEILING = 69.0
_CONFIDENCE_FLOOR = _LOW_CONFIDENCE_CEILING + 1.0


def _market_definition_is_confident(overlay: dict) -> bool:
    """True when the market this company is measured against is defined by
    a source good enough to rank it on.

    SCORING.md caps competitive position at 8 when market definition is
    low confidence. Without a sourced TAM there is no market definition
    at all, which is the lowest confidence there is, so the cap holds.
    """
    tier = (overlay or {}).get("tam_source_tier")
    if not isinstance(tier, int):
        return False
    return _TAM_TIER_CONFIDENCE.get(tier, 0.0) >= _CONFIDENCE_FLOOR


# BUS-T008 grants relief to a "non-subscription *industrial*", and
# SCORING.md's customer-economics row accepts "NRR, GRR, churn, LTV/CAC,
# payback or repeat-purchase proxies" — so the dimension is in scope
# wherever customers come back, which is most of the market.
#
# Keying relief off the adapter was wrong: the default non-financial
# adapter covers a chip maker and Netflix alike, so Netflix, Spotify and
# Costco were all excused from a question central to their business.
# Relief is now affirmative — granted only where the model genuinely has
# no repeat-customer economics to measure — and everything else reports
# NOT_SCORABLE, which keeps costing until the figures arrive.
#: Industries whose revenue is contractual or subscription-based, so
#: BUS-NRR-020..BUS-PAYBACK-026 measure something that exists.
#:
#: FORMULAS.md gates these six on the business model, not on effort:
#: BUS-NRR-020 is "Subscription/business-model adapter only",
#: BUS-PAYBACK-026 is "subscription adapter", BUS-LTVCAC-025 is
#: "Model-specific". Scope was decided the other way round — in scope by
#: default, excused only for a short list of industrials — so every
#: consumer-goods, retail and healthcare company was charged for not
#: disclosing net revenue retention and CAC payback. Coca-Cola sells
#: concentrate to bottlers: it has no logo churn to report, and six
#: MISSING rows sat in its denominator for metrics its business model
#: does not produce, holding coverage at 0.657 and below
#: MISSING_DATA_POLICY.md's 0.70 gate floor.
#:
#: Membership is affirmative: a model earns these metrics by running on
#: recurring contracts, not by failing to match an exclusion list.
# La lista vive en `core/adapters.py` desde el 2026-08-06. `market.py` hacía la
# MISMA pregunta -- ¿este negocio produce este dato? -- y no la contestaba en
# absoluto: ni una sola línea de NOT_APPLICABLE en todo el módulo, así que le
# cobraba ARPU a Coca-Cola. Se movió al módulo compartido, que existe
# literalmente para que dos especialistas no puedan discrepar en silencio.
_SUBSCRIPTION_INDUSTRIES = _adapters.SUBSCRIPTION_INDUSTRIES

#: "healthcare plans" salió de la lista el 2026-08-06, y no por conveniencia:
#: `INDUSTRY_ADAPTERS.md` le asigna a las aseguradoras un juego de métricas
#: COMPLETAMENTE distinto -- "ROE, combined ratio, reserve development,
#: solvency capital, book-value growth" -- y no nombra NRR, GRR ni churn en
#: ninguna parte. Un plan de salud cobra primas recurrentes, sí, pero no
#: reporta un puente de ingresos por cohorte ni un CAC payback: reporta
#: afiliados y ratio de siniestralidad.
#:
#: Medido: era la razón por la que UNH salía en 0,583 de cobertura de business
#: mientras NVDA, con el MISMO adaptador `default_nonfinancial`, salía en
#: 0,913. Siete métricas que su industria no publica en esa forma, sentadas en
#: su denominador. Es exactamente el caso que el comentario de arriba dice
#: haber arreglado para Coca-Cola, con otra etiqueta.

#: Adapters that name subscription economics directly.
_SUBSCRIPTION_ADAPTERS = ("saas", "subscription")


def _sin_cliente_sobre_el_umbral(overlay: dict) -> bool:
    """Consta que NINGUN cliente llega al umbral de divulgacion.

    Se declara en `Entradas/<TICKER>.json`, que es por donde entran tanto lo
    que escribe un analista como lo que el juez responde tras leer el 10-K
    -- `merge_overlay` deja su respuesta en el overlay como una clave mas, no
    en un contenedor aparte, asi que aqui se lee igual que cualquier entrada.
    Dos nombres porque el resto del archivo mezcla ingles y espanol.
    """
    if not isinstance(overlay, dict):
        return False
    for clave in ("no_customer_above_threshold", "sin_cliente_sobre_umbral"):
        if overlay.get(clave) is True:
            return True
    return False


def _subscription_business(packet: Packet, overlay: dict) -> bool:
    """True when a security's customer economics are subscription-shaped,
    so BUS-NRR-020..BUS-PAYBACK-026 are applicable rather than absent.

    The distinction decides whether an unavailable metric leaves the
    coverage denominator (NOT_APPLICABLE) or costs the dimension
    (MISSING). MISSING_DATA_POLICY.md's decision tree puts applicability
    first: "Is the metric applicable? If no, use NOT_APPLICABLE and
    invoke the industry adapter."
    """
    # Data in hand settles it: an analyst who supplied a retention bridge
    # has established the metric exists for this company, whatever its
    # sector label says.
    if any(isinstance(overlay.get(k), dict) and overlay.get(k)
           for k in ("retention", "customer_economics", "churn")):
        return True

    adapter = (getattr(getattr(packet, "analysis", None), "industry_adapter", "") or "").lower()
    if any(tag in adapter for tag in _SUBSCRIPTION_ADAPTERS):
        return True

    security = getattr(packet, "security", None)
    industry = (getattr(security, "industry", "") or "").lower()
    return any(tag in industry for tag in _SUBSCRIPTION_INDUSTRIES)


def _ok(x: float, unit: str, **lineage: object) -> Value:
    return Value.of(x, unit=unit, evidence_class=lineage.pop("evidence_class", EvidenceClass.C), **lineage)


def _null(state: NullState, unit: str, *warnings: str) -> Value:
    return Value.null(state, unit=unit, warnings=list(warnings))


def shares_are_complete(shares: Sequence[float]) -> bool:
    """Whether a share set is a full breakdown, per BUS-MIX-001's rule
    that shares "must sum to approximately 100% after eliminations".

    Customer-concentration notes disclose only the customers above a
    reporting threshold, so a customer share set is almost always a
    subset. Knowing which it is decides whether an HHI built from it is
    the real figure or FORMULAS.md's "lower bound".
    """
    if not shares:
        return False
    return abs(sum(shares) - 1.0) <= SHARE_SUM_TOLERANCE


def _ols_slope(y: Sequence[float]) -> float:
    n = len(y)
    x = np.arange(n, dtype=float)
    slope, _intercept = np.polyfit(x, np.asarray(y, dtype=float), 1)
    return float(slope)


# ============================================================================
# BUS-MIX-001..BUS-HHI-005: mix and concentration
# ============================================================================


def segment_revenue_share(segment_revenue: float, total_revenue: float) -> Value:
    """Segment revenue share (BUS-MIX-001): `Revenue_i / Total revenue`."""
    if total_revenue == 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "SEGMENT_SHARE_ZERO_TOTAL_REVENUE")
    return _ok(segment_revenue / total_revenue, unit="pct")


def recurring_revenue_pct(recurring_revenue: float, total_revenue: float) -> Value:
    """Recurring revenue percentage (BUS-REC-002): `Recurring revenue /
    Total revenue`."""
    if total_revenue == 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "RECURRING_REVENUE_ZERO_TOTAL_REVENUE")
    return _ok(recurring_revenue / total_revenue, unit="pct")


def largest_customer_concentration(customer_revenue: float, total_revenue: float) -> Value:
    """Largest-customer concentration (BUS-CONC-003): `Revenue from largest
    customer / Total revenue`. `customer_concentration` is on
    `PROHIBITED_IMPUTATION` -- callers must pass disclosed figures only
    (see module docstring)."""
    if total_revenue == 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "CONCENTRATION_ZERO_TOTAL_REVENUE")
    return _ok(customer_revenue / total_revenue, unit="pct")


def is_concentration_red_flag(share: float) -> bool:
    """`DECISION_RULES.md`: "CONCENTRATION_RED_FLAG when one customer/
    product exceeds 30% of revenue" (>30%, strictly)."""
    return share > 0.30


def customer_hhi(shares: Sequence[float]) -> Value:
    """Customer concentration HHI (BUS-HHI-004): `sum(share_i^2)`, shares as
    decimals. FORMULAS.md: "partial HHI must be labeled lower bound" when
    `shares` is a disclosed subset rather than the full customer base --
    callers are responsible for passing the full disclosed set; this
    function does not know whether its input is complete."""
    if not shares:
        return _null(NullState.MISSING, "ratio", "CUSTOMER_HHI_EMPTY_SHARES")
    return _ok(sum(s * s for s in shares), unit="ratio")


def segment_hhi(shares: Sequence[float]) -> Value:
    """Product/segment HHI (BUS-HHI-005): `sum(segment_share_i^2)`."""
    if not shares:
        return _null(NullState.MISSING, "ratio", "SEGMENT_HHI_EMPTY_SHARES")
    return _ok(sum(s * s for s in shares), unit="ratio")


# ============================================================================
# BUS-CAGR-006..BUS-RANGE-010: growth and margins
# ============================================================================


def revenue_cagr(end: float, begin: float, years: float) -> Value:
    """Revenue CAGR (BUS-CAGR-006): `(Revenue_end/Revenue_begin)^(1/n)-1`.
    Reuses `wbj.core.formulas.cagr`, which already refuses (`NOT_MEANINGFUL`)
    a non-positive beginning value or a sign change, per
    CALCULATION_CONVENTIONS.md."""
    return _cagr(end, begin, years)


def gross_margin(gross_profit: float, revenue: float) -> Value:
    """Gross margin (BUS-GM-007): `Gross profit / Revenue`."""
    if revenue == 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "GROSS_MARGIN_ZERO_REVENUE")
    return _ok(gross_profit / revenue, unit="pct")


def operating_margin(ebit: float, revenue: float) -> Value:
    """Operating margin (BUS-OM-008): `Normalized EBIT / Revenue`."""
    if revenue == 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "OPERATING_MARGIN_ZERO_REVENUE")
    return _ok(ebit / revenue, unit="pct")


def margin_stability(margins: Sequence[float]) -> Value:
    """Margin stability (BUS-STAB-009): population stdev of >=5 annual
    operating margins. Lower is better (FORMULAS.md)."""
    if len(margins) < 5:
        return _null(NullState.MISSING, "pct", "MARGIN_STABILITY_NEEDS_5_POINTS")
    return _ok(float(np.std(np.asarray(margins, dtype=float))), unit="pct")


def recession_margin_drawdown(
    margins_by_year: Sequence[tuple[int, float]], recession_years: Sequence[int]
) -> Value:
    """Worst operating-margin decline into an NBER recession year
    (BUS-STAB-009: "also report range and recession-year drawdown").

    Measured as the drop from the prior year's margin into the recession
    year, in percentage points, taking the deepest one in the window. The
    calendar comes from FRED's USREC, so which years count is the NBER's
    call rather than this module's.

    The two absences MISSING_DATA_POLICY.md's decision tree separates:

    - A missing input — no margin history, or no FRED recession calendar
      supplied — is `MISSING` (step 2: a source expected to report it but
      absent), because the drawdown could not be computed for lack of data.
    - Both inputs present, but the reported history simply does not span a
      recession year with a predecessor to compare against, is
      `NOT_APPLICABLE` (step 1): nothing is absent from the filings, and
      scoring it as missing evidence would penalise a company for the
      business cycle's timing.
    """
    if not margins_by_year or not recession_years:
        return Value.null(NullState.MISSING, unit="pct",
                          warnings=["RECESSION_DRAWDOWN_INPUTS_UNAVAILABLE"])
    by_year = dict(margins_by_year)
    recessions = set(recession_years)
    drops = [
        by_year[y - 1] - by_year[y]
        for y in sorted(by_year)
        if y in recessions and (y - 1) in by_year
    ]
    if not drops:
        return Value.null(NullState.NOT_APPLICABLE, unit="pct",
                          warnings=["NO_RECESSION_YEAR_IN_MARGIN_WINDOW"])
    return _ok(max(drops), unit="pct")


def sbc_to_fcf(sbc: float, fcf: float) -> Value:
    """Stock-based compensation as a share of free cash flow
    (BUS-SBC-030: "Also report SBC/FCF and diluted-share trend").

    SBC/revenue alone understates the burden on owners for a company
    whose cash conversion is weak, which is exactly when it matters.
    """
    if fcf <= 0:
        return _null(NullState.NOT_MEANINGFUL, "ratio", "SBC_TO_FCF_NONPOSITIVE_FCF")
    return _ok(sbc / fcf, unit="ratio")


def margin_range(margins: Sequence[float]) -> Value:
    """Margin range (BUS-RANGE-010): `max(margin_5y) - min(margin_5y)`."""
    if len(margins) < 5:
        return _null(NullState.MISSING, "pct", "MARGIN_RANGE_NEEDS_5_POINTS")
    return _ok(max(margins) - min(margins), unit="pct")


def margin_range_is_stable(range_pct: float) -> bool:
    """FORMULAS.md, verbatim: "A range <=3 percentage points is a positive
    moat signal" (BUS-RANGE-010). Distinct from the wide-moat *gate*'s own,
    looser 5pp threshold -- see `wide_moat_margin_range_ok`."""
    return range_pct <= 0.03


def wide_moat_margin_range_ok(range_pct: float) -> bool:
    """DECISION_RULES.md wide-moat gate condition 2, verbatim: the five-year
    operating-margin range is "no more than 5 percentage points" (<=0.05).

    This is deliberately looser than `margin_range_is_stable`'s <=0.03
    BUS-RANGE-010 "positive moat signal": the two thresholds serve different
    purposes and a 3-5pp company legitimately clears the gate while not
    earning the stronger BUS-RANGE-010 moat signal. Confusing the two
    wrongly fails such a company's wide-moat gate."""
    return range_pct <= 0.05


# ============================================================================
# BUS-NOPAT-011..BUS-SG-019: ROIC / WACC / reinvestment
# (nopat/invested_capital/roic/spread/eva/incremental_roic/fundamental_growth
# are thin re-exports of `wbj.engines.valuation_engine` -- see module
# docstring "ROIC/spread/EVA/incremental-ROIC reuse".)
# ============================================================================

nopat = ve.nopat
roic = ve.roic
spread = ve.spread
eva = ve.eva
incremental_roic = ve.incremental_roic
fundamental_growth = ve.fundamental_growth


def non_cash_working_capital(row: dict) -> float | None:
    """Non-cash working capital for BUS-REINV-018's second term, or None when
    the filing lacks the current-account totals.

    Delegates to `VAL-NWC-006` in the valuation engine, which is where
    FORMULAS.md registers this formula. It was implemented here first, as an
    unregistered local helper, because BUS-REINV-018's balance-sheet fallback
    needed it and no VAL row existed yet; keeping a second copy would be
    keeping a second sign convention.

    Used when a packet carries no `changeInWorkingCapital` of its own. That
    term used to fall to zero whenever the key was absent, leaving the
    numerator measuring net capex alone. MISSING_DATA_POLICY.md's third step
    is exactly this case: "Can it be calculated from validated components? If
    yes, use `C` evidence."
    """
    return ve.non_cash_working_capital_from_row(row)


def operating_view_inputs(row: dict) -> tuple[float, float] | None:
    """Operating assets and operating liabilities for BUS-IC-012's
    reconciliation, from reported balance-sheet lines.

    Operating assets are total assets less the cash treated as excess;
    operating liabilities are total liabilities less interest-bearing
    debt, leaving the trade and accrual claims that fund operations.

    Both must strip *the same* cash figure the financing view subtracts,
    or the two views disagree by construction and the reconciliation
    reports a divergence that is an artefact of the definitions rather
    than a fact about the company. A first attempt here stripped cash
    plus short- and long-term investments from assets while the financing
    view subtracted only cash, and the >5% check then fired on every
    company tested — 10% to 61% — which is noise, not a control.

    Aligned, the identity `TA = TL + TE` makes the two views equal, so
    what survives is the real gap: minority interest and preferred
    claims, which sit in equity but not in the operating base. KO and XOM
    diverge by exactly their minority interest (3.1% and 2.5%), inside
    the 5% tolerance; a larger gap means restated or inconsistent inputs,
    which is what the check is for.

    Returns `None` when the filing does not carry the lines, rather than
    substituting zeros -- a missing balance is not a zero balance.
    """
    total_assets = _num(row, "total_assets")
    total_liabilities = _num(row, "total_liabilities")
    total_debt = _num(row, "total_debt")
    if total_assets is None or total_liabilities is None or total_debt is None:
        return None
    cash = _num(row, "cash") or 0.0
    return total_assets - cash, total_liabilities - total_debt


def average_invested_capital(
    debt_begin: float | None, equity_begin: float | None, cash_begin: float,
    debt_end: float, equity_end: float, cash_end: float,
    *, operating_begin: tuple[float, float] | None = None,
    operating_end: tuple[float, float] | None = None,
) -> Value:
    """Average invested capital (BUS-IC-012): `average(financing-view IC at
    the beginning and end of the period)`, reusing
    `valuation_engine.invested_capital`'s financing view for each endpoint.

    FORMULAS.md's caveat on this row is "Reconcile to operating assets
    minus operating liabilities", and the reconciliation was never run:
    both endpoints were computed without `operating_assets`/
    `operating_liabilities`, so the engine returned `operating_view=None`
    and the >5% divergence check never fired. That check is the only
    thing standing behind the `excess_cash` argument, which this module
    fills with *total* cash because no filing discloses how much of a
    cash balance operations require. Passing the operating view makes the
    substitution visible when it distorts the number.
    """
    if debt_begin is None or equity_begin is None:
        # Ending balance only: compute it, label it, and let
        # `_confidence_for`'s per-warning deduction carry the "reduce
        # confidence" half of the convention.
        ic_end_only = ve.invested_capital(
            debt_end, equity_end, cash_end,
            operating_assets=operating_end[0] if operating_end else None,
            operating_liabilities=operating_end[1] if operating_end else None)
        if ic_end_only.financing_view.is_null:
            return _null(NullState.MISSING, "usd", "AVERAGE_INVESTED_CAPITAL_INPUTS_UNAVAILABLE")
        return _ok(ic_end_only.financing_view.value, unit="usd",
                   warnings=sorted({WARN_END_BALANCE_PROXY, *ic_end_only.warnings}))

    ic_begin_r = ve.invested_capital(debt_begin, equity_begin, cash_begin,
                                     operating_assets=operating_begin[0] if operating_begin else None,
                                     operating_liabilities=operating_begin[1] if operating_begin else None)
    ic_end_r = ve.invested_capital(debt_end, equity_end, cash_end,
                                   operating_assets=operating_end[0] if operating_end else None,
                                   operating_liabilities=operating_end[1] if operating_end else None)
    ic_begin, ic_end = ic_begin_r.financing_view, ic_end_r.financing_view
    if ic_begin.is_null or ic_end.is_null:
        return _null(NullState.MISSING, "usd", "AVERAGE_INVESTED_CAPITAL_INPUTS_UNAVAILABLE")
    warnings = sorted({w for r in (ic_begin_r, ic_end_r) for w in r.warnings})
    return _ok((ic_begin.value + ic_end.value) / 2, unit="usd", warnings=warnings)


def cumulative_fcf_conversion(fcf_sum_5y: float, ni_sum_5y: float) -> Value:
    """Cumulative FCF conversion (BUS-FCFC-017): `sum(FCF_5y) /
    sum(NetIncome_5y)`. FORMULAS.md: "sign changes require review" --
    refuses when the net-income sum is non-positive."""
    if ni_sum_5y <= 0:
        return _null(NullState.NOT_MEANINGFUL, "ratio", "FCF_CONVERSION_NI_SUM_NONPOSITIVE")
    return _ok(fcf_sum_5y / ni_sum_5y, unit="ratio")


def reinvestment_rate(net_capex: float, dnwc: float, rd_adjustment: float, nopat_value: float) -> Value:
    """Reinvestment rate (BUS-REINV-018): `(Net capex + change in
    non-cash working capital + capitalized-R&D adjustment) / NOPAT`.
    FORMULAS.md: "unstable when NOPAT is near zero" -- refuses at exactly
    zero rather than dividing."""
    if nopat_value == 0:
        return _null(NullState.NOT_MEANINGFUL, "ratio", "REINVESTMENT_RATE_ZERO_NOPAT")
    return _ok((net_capex + dnwc + rd_adjustment) / nopat_value, unit="ratio")


# ============================================================================
# BUS-NRR-020..BUS-PAYBACK-026: customer economics (subscription adapter)
# ============================================================================


def net_revenue_retention(begin: float, expansion: float, contraction: float, churn: float) -> Value:
    """NRR (BUS-NRR-020): `(Begin + expansion - contraction - churn) /
    Begin`."""
    if begin <= 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "NRR_NONPOSITIVE_BEGIN_COHORT")
    return _ok((begin + expansion - contraction - churn) / begin, unit="pct")


def gross_revenue_retention(begin: float, contraction: float, churn: float) -> Value:
    """GRR (BUS-GRR-021): `(Begin - contraction - churn) / Begin`; excludes
    expansion."""
    if begin <= 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "GRR_NONPOSITIVE_BEGIN_COHORT")
    return _ok((begin - contraction - churn) / begin, unit="pct")


def logo_churn(lost_customers: float, begin_customers: float) -> Value:
    """Logo churn (BUS-CHURN-022): `Customers lost / Customers at period
    start`."""
    if begin_customers <= 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "LOGO_CHURN_NONPOSITIVE_BEGIN_CUSTOMERS")
    return _ok(lost_customers / begin_customers, unit="pct")


def customer_ltv(arpu: float, gross_margin_pct: float, customer_life_years: float) -> Value:
    """Customer LTV (BUS-LTV-023): `ARPU * Gross margin * Expected customer
    life`. `customer_life_years` here is the 1/churn approximation (state
    the survival-model or approximation used, per FORMULAS.md)."""
    return _ok(arpu * gross_margin_pct * customer_life_years, unit="usd")


def customer_acquisition_cost(spend: float, new_customers: float) -> Value:
    """CAC (BUS-CAC-024): `Acquisition spend / New customers added`."""
    if new_customers <= 0:
        return _null(NullState.NOT_MEANINGFUL, "usd", "CAC_NONPOSITIVE_NEW_CUSTOMERS")
    return _ok(spend / new_customers, unit="usd")


def ltv_to_cac(ltv: float, cac: float) -> Value:
    """LTV/CAC (BUS-LTVCAC-025)."""
    if cac <= 0:
        return _null(NullState.NOT_MEANINGFUL, "ratio", "LTV_CAC_NONPOSITIVE_CAC")
    return _ok(ltv / cac, unit="ratio")


def cac_payback_months(cac: float, monthly_arpu: float, gross_margin_pct: float) -> Value:
    """CAC payback months (BUS-PAYBACK-026): `CAC / (Monthly ARPU * Gross
    margin)`."""
    denom = monthly_arpu * gross_margin_pct
    if denom <= 0:
        return _null(NullState.NOT_MEANINGFUL, "months", "CAC_PAYBACK_NONPOSITIVE_DENOMINATOR")
    return _ok(cac / denom, unit="months")


# ============================================================================
# BUS-GUIDE-027..BUS-SBC-030: management and dilution
# ============================================================================


def guidance_accuracy(actual: float, guidance_midpoint: float, floor: float | None = None) -> Value:
    """Guidance accuracy (BUS-GUIDE-027): `1 - |actual - guidance_midpoint|
    / max(|guidance_midpoint|, materiality_floor)`, clipped to [0, 1].

    `materiality_floor` defaults to `GUIDANCE_MATERIALITY_FLOOR` — Victor
    names the term but states no number, so it is declared engine
    calibration (CALIBRATION_REGISTRY), not a value read from a filing.
    Referenced in the body rather than the default argument because the
    constant is defined after this function (evaluation-order)."""
    if floor is None:
        floor = GUIDANCE_MATERIALITY_FLOOR
    denom = max(abs(guidance_midpoint), floor)
    raw = 1.0 - abs(actual - guidance_midpoint) / denom
    return _ok(min(1.0, max(0.0, raw)), unit="ratio")


def diluted_share_cagr(shares_end: float, shares_begin: float, years: float) -> Value:
    """Diluted-share CAGR (BUS-DIL-028): positive is dilution. Reuses
    `wbj.core.formulas.cagr` (same as `financial.py`'s FIN-DX-033)."""
    return _cagr(shares_end, shares_begin, years)


def is_dilution_red_flag(cagr_pct: float) -> bool:
    """`DECISION_RULES.md`: "DILUTION_RED_FLAG when diluted shares grow >5%
    CAGR for three years without commensurate per-share value creation."
    This module reads the ">5% CAGR" threshold as the mechanical trigger
    (the brief's own summary: "diluted share CAGR >5% -> DILUTION_RED_FLAG");
    "commensurate per-share value creation" is a judgment call this
    function does not attempt (a red flag is raised, not a final verdict)."""
    return cagr_pct > 0.05


def capital_allocation_spread(incremental_roic_value: float, wacc_value: float) -> Value:
    """Capital-allocation value spread (BUS-ALLOC-029): `Incremental ROIC -
    WACC`."""
    return _ok(incremental_roic_value - wacc_value, unit="pct")


def sbc_burden(sbc: float, revenue: float) -> Value:
    """SBC burden (BUS-SBC-030): `SBC / Revenue`."""
    if revenue == 0:
        return _null(NullState.NOT_MEANINGFUL, "pct", "SBC_BURDEN_ZERO_REVENUE")
    return _ok(sbc / revenue, unit="pct")


# ============================================================================
# Mandatory flags
# ============================================================================


def value_destruction_triggered(roic_value: float | None, wacc_value: float | None) -> bool:
    """`DECISION_RULES.md`: "VALUE_DESTRUCTION when normalized ROIC < WACC."
    `None` inputs never trigger."""
    return roic_value is not None and wacc_value is not None and roic_value < wacc_value


# ============================================================================
# Verdict
# ============================================================================


def verdict(score10: float) -> str:
    """Business verdict bands (`DECISION_RULES.md`, verbatim score column):
    8.0-10.0 Excellent, 6.0-7.99 Good, 4.0-5.99 Mediocre/mixed, <4.0 Weak."""
    if score10 >= 8.0:
        return "Excellent business"
    if score10 >= 6.0:
        return "Good business"
    if score10 >= 4.0:
        return "Mediocre / mixed business"
    return "Weak business"


def capped_verdict(score10: float, *, value_destruction: bool, excellent_gate_passes: bool) -> str:
    """The verdict label after applying `DECISION_RULES.md`'s conditions to
    the label only (never to `category.awarded_points` -- see
    `SpecialistOutput.verdict`'s docstring and the module docstring's
    "Dimension-level caps vs. mandatory-flag caps").

    `value_destruction` (ROIC<WACC): `DECISION_RULES.md`'s Weak-business row
    names "ROIC below WACC" directly as sufficient for the weakest band, so
    this caps the effective score at the Weak ceiling -- the same full-cap
    discipline as `financial.py`'s Override 1.

    `excellent_gate_passes`: the business-verdict table's own "Additional
    condition" for Excellent (ROIC>=20% or top-decile adapter return,
    positive spread, FCF conversion>=0.9x, wide-moat gate passes) -- when
    `False`, a would-be-Excellent score is capped at the Good ceiling.
    """
    effective = score10
    if value_destruction:
        effective = min(effective, 3.99)
    elif not excellent_gate_passes:
        effective = min(effective, 7.99)
    return verdict(effective)


# ============================================================================
# Envelope assembly
# ============================================================================


class MoatSummary(BaseModel):
    """`envelope.moat` (OUTPUT_SCHEMA.md)."""

    model_config = ConfigDict(frozen=True)

    classification: str = "NotScorable"
    quantitative_evidence: list[str] = Field(default_factory=list)


class MoatGateInputs(BaseModel):
    """The verdict's gate conditions, carried on the output so the label
    can be recomputed once a judgment moves the score.

    `merge_overlay` rescored the category but left `verdict` alone, so a
    `moat_classification` answer that pushed the score from 7.75 to 8.31
    still reported "Good business" beside an Excellent number. Recomputing
    needs the gate state, and the gate state was a local variable inside
    `run()`.
    """

    model_config = ConfigDict(frozen=True)

    mechanical_conditions_pass: bool = False
    roic_at_least_20pct: bool = False
    positive_spread: bool = False
    fcf_conversion_at_least_0_9: bool = False

    def excellent_gate_passes(self, moat_effects_count: int) -> bool:
        """DECISION_RULES.md's Excellent row: "ROIC >=20% or top-decile
        adapter return, positive spread, FCF conversion >=0.9x, moat gate
        passes". The moat gate is the full four-condition one.

        `mechanical_conditions_pass` carries conditions 1, 2 and 4;
        `moat_effects_count` carries condition 3, "At least two
        independent moat effects are quantitatively visible". It is the
        length of `moat.quantitative_evidence`, so an unjudged moat — an
        empty evidence list, count 0 — fails the gate rather than passing
        by default, as it did through the `Wide` classification before.
        """
        return (
            self.roic_at_least_20pct
            and self.positive_spread
            and self.fcf_conversion_at_least_0_9
            and self.mechanical_conditions_pass
            and moat_effects_count >= WIDE_MOAT_MIN_EFFECTS
        )


class BusinessOutput(SpecialistOutput):
    """`business_analysis`'s `run()` result: the shared envelope plus
    OUTPUT_SCHEMA.md's business-specific extension fields."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    business_in_one_sentence: str | None = None
    moat: MoatSummary = Field(default_factory=MoatSummary)
    #: Gate state for `recompute_verdict`, which runs after a judgment
    #: moves the score. Not part of OUTPUT_SCHEMA.md — internal to the
    #: verdict recomputation.
    moat_gate_inputs: MoatGateInputs = Field(default_factory=MoatGateInputs)
    roic_history: list[float] = Field(default_factory=list)
    roic_wacc_spread_history: list[float] = Field(default_factory=list)
    margin_stability: dict[str, Any] = Field(default_factory=dict)
    customer_economics: dict[str, Any] = Field(default_factory=dict)
    capital_allocation: dict[str, Any] = Field(default_factory=dict)
    competitive_position: dict[str, Any] = Field(default_factory=dict)
    three_thesis_killers: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _Row:
    metric_id: str
    value: Value
    score10: float | None  # None -> NOT_SCORABLE
    confidence: float = 0.0


def _confidence_for(v: Value) -> float:
    """Per-row confidence (0-100); same convention as `financial.py`'s
    `_confidence_for`: null rows score 0, valid rows start from their
    evidence class and lose 15 points per warning."""
    if v.is_null:
        return 0.0
    base_by_class = {
        EvidenceClass.R: 95.0, EvidenceClass.C: 85.0, EvidenceClass.E: 60.0,
        EvidenceClass.A: 50.0, EvidenceClass.Q: 30.0,
    }
    base = base_by_class.get(v.evidence_class, 70.0) if v.evidence_class else 70.0
    base -= 15.0 * len(v.warnings)
    return max(0.0, min(100.0, base))


def _num(row: dict, key: str) -> float | None:
    v = row.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _annual_rows(packet: Packet) -> list[dict]:
    rows = packet.fundamentals.get("annual") or []
    return list(reversed(rows))  # ascending, per DATASET.md


def _reported_tax_rate(row: dict) -> float | None:
    """This year's normalized cash tax rate, or None. See
    `wbj.core.taxes` for why an out-of-range effective rate counts as
    unavailable rather than being clamped into range."""
    return _taxes.reported_tax_rate(_num(row, "income_before_tax"),
                                    _num(row, "income_tax_expense"))


def _has_reported_tax_rate(row: dict) -> bool:
    """Whether this year yields a usable normalized cash tax rate."""
    return _reported_tax_rate(row) is not None


def _tax_rate(row: dict, fallback: float) -> float:
    rate = _reported_tax_rate(row)
    return fallback if rate is None else rate


def _roic_and_spread_history(
    annual: list[dict], wacc_value: float | None
) -> tuple[list[float], list[float], int, int, int]:
    """Per-year ROIC (and, if `wacc_value` given, spread) for every year
    that has both its own and its prior year's balance-sheet inputs --
    mirrors `financial.py`'s FIN-EF-027 `roic_hist` construction, reused
    here for `envelope.roic_history` / `roic_wacc_spread_history` and the
    moat gate's "spread >=5pp in >=4/5 years" persistence check."""
    roic_hist: list[float] = []
    spread_hist: list[float] = []
    # Years whose ROIC rests on the 21% statutory rate rather than the
    # company's own reported taxes. `run()` declared the substitution for
    # the headline NOPAT but not for this history — which is what the
    # wide-moat gate's "spread >=5pp in >=4/5 years" persistence check
    # reads. A loss-making biotech falls back in four years out of six,
    # and nothing said so.
    substituted_tax_years = 0
    # CONFIDENCE_ENGINE.md's `consistency` is "agreement across periods
    # and reconciled sources". This counts the first half directly: each
    # period whose financing and operating views of invested capital tie
    # out is a period whose balance sheet agrees with itself.
    ic_checks_run = ic_checks_passed = 0
    for i in range(1, len(annual)):
        ebit_i = _num(annual[i], "ebit")
        debt_i, equity_i = _num(annual[i], "total_debt"), _num(annual[i], "total_equity")
        debt_im1, equity_im1 = _num(annual[i - 1], "total_debt"), _num(annual[i - 1], "total_equity")
        cash_i, cash_im1 = _num(annual[i], "cash") or 0.0, _num(annual[i - 1], "cash") or 0.0
        if None in (ebit_i, debt_i, equity_i, debt_im1, equity_im1):
            continue
        tax_rate = _tax_rate(annual[i], 0.21)
        if not _has_reported_tax_rate(annual[i]):
            substituted_tax_years += 1
        nopat_i = nopat(ebit_i, tax_rate)
        avg_ic_v = average_invested_capital(
            debt_im1, equity_im1, cash_im1, debt_i, equity_i, cash_i,
            operating_begin=operating_view_inputs(annual[i - 1]),
            operating_end=operating_view_inputs(annual[i]),
        )
        if avg_ic_v.is_null:
            continue
        if operating_view_inputs(annual[i]) is not None:
            ic_checks_run += 1
            if WARN_IC_VIEWS_DIFFER not in (avg_ic_v.warnings or []):
                ic_checks_passed += 1
        roic_v = roic(nopat_i.value, avg_ic_v.value)
        if roic_v.is_null:
            continue
        roic_hist.append(roic_v.value)
        if wacc_value is not None:
            spread_hist.append(spread(roic_v.value, wacc_value).value)
    return (roic_hist, spread_hist, substituted_tax_years,
            ic_checks_passed, ic_checks_run)


def _compute_all(
    packet: Packet, overlay: dict[str, Any], resolution: dict[str, str] | None = None,
) -> tuple[list[_Row], list[str], list[JudgmentRequest], dict[str, Any]]:
    annual = _annual_rows(packet)
    rows: list[_Row] = []
    assumptions: list[str] = []
    input_warnings: list[str] = []
    judgment_requests: list[JudgmentRequest] = []
    ctx: dict[str, Any] = {}

    # OUTPUT_CONTRACT.md requires `period` ("Explicit") and `source`
    # ("Stable source locator") on every metric row, and HANDOFF_CONTRACT.md
    # has the main agent reject a packet whose rows are incomplete. Both
    # arrived empty on all thirty rows: `MetricRow.from_value` reads them
    # off the `Value`, and nothing ever set them. They are attached here,
    # once, so a new metric cannot be added without them.
    filings_source = _filings_source(packet, annual)
    analyst_source = f"Entradas/{packet.security.ticker.upper()}.json"
    # The period the analyst states for their quarterly snapshot inputs;
    # None falls back to the filing FY, as before.
    analyst_period = _stated_analyst_period(overlay)

    # SOURCE_HIERARCHY.md: prefer the reconciled fact (tier-1 EDGAR where
    # the builder could get it) over the raw provider row, and refuse to
    # score on one whose sources materially disagree.
    conflicted_inputs = {
        key for key in _RECONCILED_INPUTS if _reconciled_fact(packet, key)[1]
    }
    conflicted_metrics = {
        metric
        for key in conflicted_inputs
        for metric in _RECONCILED_INPUTS[key]
    }
    resolution = resolution or {}
    ctx["conflicted_inputs"] = sorted(conflicted_inputs)
    if conflicted_inputs:
        for key in sorted(conflicted_inputs):
            fact = (getattr(packet, "facts_table", None) or {}).get(key)
            detail = (getattr(fact, "warnings", None) or [""])[0]
            assumptions.append(
                f"SOURCE_HIERARCHY.md conflict on {key!r}: {detail} Metrics depending on it "
                f"({', '.join(sorted(_RECONCILED_INPUTS[key]))}) carry "
                f"{WARN_INPUT_CONFLICTED}; model-fit confidence reduced by "
                f"{MODEL_FIT_PENALTY_PER_PROXY:.0f} points."
            )

    def add(metric_id: str, v: Value, score10: float | None, *,
            period: str | None = None, source: str | None = None) -> None:
        score10 = _authorised_score(metric_id, score10)
        v = _with_provenance(v, period=period or _period_label(annual),
                             source=source or filings_source)
        if metric_id in conflicted_metrics and not v.is_null:
            # SOURCE_HIERARCHY.md step 5 names the contested *input*
            # (total_debt/cash) as "the metric ... do not score it" — the
            # packet already carries it CONFLICTED, and business does not score
            # it as a metric. A metric *derived* from it stays computable on
            # the tier-1 (EDGAR) value the default order prefers, and carries
            # the contest into the warnings, where `_confidence_for` prices it.
            # run() discloses whether the choice is material. Withholding the
            # derived scores was tried and is wrong: it lost six of business's
            # core metrics over a difference that is usually definitional
            # (leases summed into EDGAR debt) and moves no score.
            v = v.model_copy(update={
                "warnings": sorted({WARN_INPUT_CONFLICTED, *(v.warnings or [])})})
        rows.append(_Row(metric_id=metric_id, value=v, score10=score10,
                         confidence=_confidence_for(v)))

    revenues = [_num(r, "revenue") for r in annual]
    gp_hist = [_num(r, "gross_profit") for r in annual]
    ebit_hist = [_num(r, "ebit") for r in annual]
    ni_hist = [_num(r, "net_income") for r in annual]
    ocf_hist = [_num(r, "operating_cash_flow") for r in annual]
    capex_hist = [abs(_num(r, "capex") or 0.0) if _num(r, "capex") is not None else None for r in annual]
    debt_hist = [_num(r, "total_debt") for r in annual]
    # The latest period of both reconciled IC inputs takes the higher-tier
    # (EDGAR) value the builder picked, honouring SOURCE_HIERARCHY.md's default
    # order, and follows run()'s conflict resolution when the sources disagree.
    # Wiring only debt once left BUS-IC-012 combining a tier-1 total_debt with a
    # raw provider cash in `debt + equity - excess cash`.
    if debt_hist:
        debt_hist[-1] = _resolved_latest_input(packet, "total_debt", resolution, debt_hist[-1])
    _reconciled_cash = _resolved_latest_input(
        packet, "cash", resolution, _num(annual[-1], "cash") if annual else None)
    equity_hist = [_num(r, "total_equity") for r in annual]
    diluted_hist = [_num(r, "diluted_shares") for r in annual]
    sbc_hist = [_num(r, "stock_based_compensation") for r in annual]

    wacc_value = _overlay_number(overlay, "wacc", input_warnings)
    wacc_value = float(wacc_value) if wacc_value is not None else None
    ctx["wacc_value"] = wacc_value
    if wacc_value is None:
        assumptions.append(
            "No overlay['wacc'] supplied: every ROIC/spread/EVA-based metric (BUS-ROIC-013, "
            "BUS-SPREAD-014, BUS-EVA-015, BUS-ALLOC-029) and the wide-moat gate/VALUE_DESTRUCTION "
            "check are skipped (MISSING, not NOT_SCORABLE -- an ordinary missing external input)."
        )

    # No `rev_first`/`ni_latest`: BUS-CAGR-006 derives its own begin/end from
    # `_cagr_over_window` (a bounded 5y/3y window, not "whatever row came
    # first") and BUS-FCFC-017 sums five years rather than reading the latest.
    # Both locals were left behind by those better implementations.
    rev_latest = revenues[-1] if revenues else None
    gp_latest = gp_hist[-1] if gp_hist else None
    ebit_latest = ebit_hist[-1] if ebit_hist else None
    debt_latest = debt_hist[-1] if debt_hist else None
    equity_latest = equity_hist[-1] if equity_hist else None
    n_years = len(annual)

    # ---- BUS-MIX-001: segment revenue share (overlay only) ----
    # The overlay value was read and then discarded: `v` was overwritten
    # with MISSING unconditionally, so this reported "unavailable" even
    # with the company's own reported segmentation in hand.
    segment_shares_overlay = _overlay_numbers(overlay, "segment_shares", input_warnings)
    segments_complete = shares_are_complete(segment_shares_overlay or [])
    if segment_shares_overlay:
        # The largest share only means "the biggest slice of the whole"
        # when the slices are the whole. Nothing checked that, so a
        # partial breakdown reported the same way as a complete one.
        v = _ok(max(segment_shares_overlay), unit="pct",
                warnings=[] if segments_complete else [WARN_SHARES_INCOMPLETE])
        if not segments_complete:
            assumptions.append(
                f"BUS-MIX-001: segment shares sum to {sum(segment_shares_overlay):.1%}, outside "
                f"FORMULAS.md's approximately-100% requirement (tolerance "
                f"{SHARE_SUM_TOLERANCE:.0%}). The breakdown is partial, so the largest share is a "
                "lower bound on true segment concentration."
            )
    else:
        v = _null(NullState.MISSING, "pct", "SEGMENT_REVENUE_UNAVAILABLE")
    add("BUS-MIX-001", v, None, source=analyst_source)

    # ---- BUS-REC-002: recurring revenue % (overlay only) ----
    # Two shapes reach this row and they are not interchangeable. A filing
    # states the *share* ("subscription revenue was 45% of total"), while an
    # analyst capturing the segment writes the *amount*. Dividing a share by
    # revenue a second time is DATA_POLICY.md's forbidden silent change of
    # denominator, and it turned a 45%-recurring company into a 0% one.
    recurring_share = _overlay_number(overlay, "recurring_revenue_share", input_warnings)
    recurring = _overlay_number(overlay, "recurring_revenue", input_warnings)
    if recurring_share is not None:
        v = (_ok(recurring_share, unit="pct") if 0.0 <= recurring_share <= 1.0
             else _null(NullState.NOT_MEANINGFUL, "pct",
                        f"RECURRING_REVENUE_SHARE_OUT_OF_RANGE: {recurring_share}"))
    elif recurring is not None and rev_latest not in (None, 0):
        v = recurring_revenue_pct(recurring, rev_latest)
    else:
        # `DATASET.md` tipa `recurring_revenue_5y` como **conditional** y lo
        # define como "contractual or subscription revenue with recurring
        # character". Un negocio que no corre sobre contratos recurrentes no
        # tiene esa cifra AUSENTE: no la tiene. Es el paso 1 del arbol de
        # `MISSING_DATA_POLICY.md` -- "la metrica aplica? Si no,
        # NOT_APPLICABLE" -- la misma puerta que ya cruzan BUS-NRR-020..026,
        # de la que esta fila se habia quedado fuera.
        #
        # Ojo con el otro lado: un SaaS que SI corre sobre suscripciones y no
        # publica el porcentaje tiene un hueco real, y sigue siendo MISSING.
        # NVDA es el caso limite y por eso importa: tiene ingreso recurrente
        # (AI Enterprise) y no lo reporta, asi que le cuenta en contra.
        # Y la industria es solo un PROXY de la pregunta real, que es de
        # empresa: NVDA es "Semiconductors" y aun asi tiene ingreso recurrente
        # (NVIDIA AI Enterprise) que no reporta. Su propio archivo lo dice con
        # todas las letras -- "no es que no aplique, es que no la reporta" --
        # asi que un analista que lo haya comprobado pisa al proxy con
        # `recurring_revenue_applies`. Sin esa declaracion se marcaria como no
        # aplicable algo que un humano ya verifico que si aplica, y NVDA
        # saltaba a una cobertura perfecta de 1,000 por un hueco real.
        declarado = overlay.get("recurring_revenue_applies")
        aplica = (bool(declarado) if declarado is not None
                  else _subscription_business(packet, overlay))
        v = _null(NullState.MISSING if aplica else NullState.NOT_APPLICABLE,
                  "pct", "RECURRING_REVENUE_UNAVAILABLE")
    add("BUS-REC-002", v, _score_from_anchor(v, [(0.0, 0), (0.30, 4), (0.70, 7), (1.0, 10)]), source=analyst_source)

    # ---- BUS-CONC-003: largest customer concentration (overlay only; PROHIBITED_IMPUTATION) ----
    largest_customer_share = _overlay_number(overlay, "largest_customer_share", input_warnings)
    if largest_customer_share is not None:
        v = largest_customer_concentration(largest_customer_share * 100.0, 100.0)
    elif _sin_cliente_sobre_el_umbral(overlay):
        # El filing declara que NINGUN cliente llega al umbral. Eso no es un
        # dato que falte: es un hallazgo, y el mas favorable de esta metrica
        # -- no hay concentracion que medir porque no la hay.
        #
        # `DATASET.md` tipa `customer_revenue_shares` como "required WHEN
        # DISCLOSED". Si no se divulga porque no hay nada que divulgar, la
        # condicion no se cumple, y cobrarlo como MISSING castiga a la empresa
        # por NO tener el problema.
        #
        # Medido, y es la razon por la que Kevin pregunto por que difieren las
        # coberturas: Coca-Cola declara un embotellador sobre el 10% y saca
        # 0,93 en business; Microsoft no tiene ninguno y saca 0,58.
        #
        # El motor solo no puede deducirlo -- por eso el comentario de abajo
        # decia que MISSING era el estado final correcto -- pero el juez SI
        # lee el filing, y se verifico que distingue la frase afirmativa de KO
        # ("one bottler accounted for 10% of our net operating revenues") de
        # la negativa ("No bottlers or customers represented 10% or more"),
        # que es donde fallaba un patron de texto.
        v = _null(NullState.NOT_APPLICABLE, "pct",
                  "NO_CUSTOMER_AT_OR_ABOVE_DISCLOSURE_THRESHOLD: el filing "
                  "declara que ninguno llega al umbral, asi que no hay "
                  "concentracion que reportar")
    else:
        # A bare token told the reader the number was absent but not what to
        # do about it, and not that "absent" has two very different causes:
        # an issuer that discloses a >=10%-of-revenue customer and simply
        # hasn't been read yet (supply it), versus one that discloses none
        # because none reaches the threshold (nothing to supply, ever).
        # Naming the remedy is what turns this from a dead end into a task.
        v = _null(NullState.MISSING, "pct",
                  "CUSTOMER_CONCENTRATION_UNAVAILABLE_PROHIBITED_IMPUTATION: set "
                  "`largest_customer_share` (0-1, revenue-based) in "
                  "Entradas/<TICKER>.json from the 10-K. If the filing discloses "
                  "no customer at or above the reporting threshold, there is no "
                  "figure to supply and MISSING is the correct final state -- "
                  "MISSING_DATA_POLICY.md bars inferring one either way")
    add("BUS-CONC-003", v, _score_from_anchor(v, [(0.0, 10), (0.30, 6), (0.50, 2), (1.0, 0)]), source=analyst_source)
    ctx["largest_customer_share"] = v.value if v.is_valid else None

    # ---- BUS-HHI-004: customer HHI (overlay only) ----
    customer_shares_overlay = _overlay_numbers(overlay, "customer_shares", input_warnings)
    if customer_shares_overlay:
        v = customer_hhi(customer_shares_overlay)
        # FORMULAS.md: "partial HHI must be labeled lower bound." The
        # label was never applied, and a customer-concentration note
        # discloses only the customers above a reporting threshold — so
        # this figure is a lower bound essentially every time, and read
        # as the real one.
        if v.is_valid and not shares_are_complete(customer_shares_overlay):
            v = _ok(v.value, unit="ratio", warnings=[WARN_HHI_LOWER_BOUND])
            assumptions.append(
                f"BUS-HHI-004: the {len(customer_shares_overlay)} disclosed customer share(s) sum "
                f"to {sum(customer_shares_overlay):.1%}; undisclosed customers carry the rest. "
                "The HHI is therefore a lower bound on true customer concentration, per "
                "FORMULAS.md."
            )
    else:
        v = _null(NullState.MISSING, "ratio", "CUSTOMER_HHI_UNAVAILABLE")
    add("BUS-HHI-004", v, None, source=analyst_source)

    # ---- BUS-HHI-005: segment HHI (overlay only) ----
    if segment_shares_overlay:
        v = segment_hhi(segment_shares_overlay)
        if v.is_valid and not segments_complete:
            # Same rule as BUS-HHI-004: an HHI over part of the base
            # understates concentration and must say so.
            v = _ok(v.value, unit="ratio", warnings=[WARN_HHI_LOWER_BOUND])
    else:
        v = _null(NullState.MISSING, "ratio", "SEGMENT_HHI_UNAVAILABLE")
    add("BUS-HHI-005", v, None, source=analyst_source)

    # ---- BUS-CAGR-006: revenue CAGR over FORMULAS.md's registered window ----
    cagr_window = _cagr_over_window(annual, revenues, CAGR_WINDOW_YEARS)
    if cagr_window is not None:
        begin, end, years, cagr_period = cagr_window
        v = revenue_cagr(end, begin, years)
    else:
        v, cagr_period = _null(NullState.MISSING, "pct", "REVENUE_CAGR_INSUFFICIENT_HISTORY"), None
    add("BUS-CAGR-006", v, _score_from_anchor(v, [(-0.05, 0), (0.0, 3), (0.10, 7), (0.20, 10)]), period=cagr_period)
    ctx["revenue_cagr"] = v.value if v.is_valid else None
    # FORMULAS.md registers BUS-CAGR-006 at "3y and 5y" — two windows. The
    # 5y feeds the score above; the 3y is computed and reported alongside
    # so the shorter window the registry names is not dropped.
    cagr_window_3y = _cagr_over_window(annual, revenues, 3)
    if cagr_window_3y is not None:
        _cb3, _ce3, _cy3, _ = cagr_window_3y
        _rv3 = revenue_cagr(_ce3, _cb3, _cy3)
        ctx["revenue_cagr_3y"] = _rv3.value if _rv3.is_valid else None
    else:
        ctx["revenue_cagr_3y"] = None

    # ---- BUS-GM-007 / BUS-OM-008: latest margins ----
    if gp_latest is not None and rev_latest not in (None, 0):
        v = gross_margin(gp_latest, rev_latest)
    else:
        v = _null(NullState.MISSING, "pct", "GROSS_MARGIN_INPUTS_UNAVAILABLE")
    add("BUS-GM-007", v, None)

    if ebit_latest is not None and rev_latest not in (None, 0):
        v = operating_margin(ebit_latest, rev_latest)
    else:
        v = _null(NullState.MISSING, "pct", "OPERATING_MARGIN_INPUTS_UNAVAILABLE")
    add("BUS-OM-008", v, None)
    om_latest = v.value if v.is_valid else None

    # ---- BUS-STAB-009 / BUS-RANGE-010: 5y operating margin stability/range ----
    op_margins = [
        e / r for e, r in zip(ebit_hist, revenues) if e is not None and r not in (None, 0)
    ]
    window = op_margins[-5:]
    if len(window) >= 5:
        v_stab = margin_stability(window)
        v_range = margin_range(window)
    else:
        v_stab = _null(NullState.MISSING, "pct", "MARGIN_STABILITY_NEEDS_5_POINTS")
        v_range = _null(NullState.MISSING, "pct", "MARGIN_RANGE_NEEDS_5_POINTS")
    add("BUS-STAB-009", v_stab, _score_from_anchor(v_stab, [(0.0, 10), (0.03, 8), (0.08, 4), (0.20, 0)]), period=_period_label(annual, 5))
    add("BUS-RANGE-010", v_range, _score_from_anchor(v_range, [(0.0, 10), (0.03, 8), (0.05, 6), (0.10, 2), (0.20, 0)]), period=_period_label(annual, 5))
    ctx["margin_range"] = v_range.value if v_range.is_valid else None
    ctx["op_margins_window"] = window

    # BUS-STAB-009's caveat names three outputs: the standard deviation,
    # the range, and the recession-year drawdown. Only the first two
    # existed. The third is what separates a margin that is merely steady
    # in good years from one that holds through a cycle — the distinction
    # SCORING.md's moat band is built on ("stable margins" for 7-10,
    # "margins compress" for 0-3).
    margins_by_year = [
        (int(_fiscal_year(r)), e / rev)
        for r, e, rev in zip(annual, ebit_hist, revenues)
        if e is not None and rev not in (None, 0) and _fiscal_year(r) is not None
    ]
    v_drawdown = recession_margin_drawdown(margins_by_year, overlay.get("recession_years") or [])
    ctx["recession_margin_drawdown"] = v_drawdown.value if v_drawdown.is_valid else None

    # BUS-OM-008: "Show reported and normalized." No normalization
    # adjustments are applied — NORMALIZATION_AND_RESTATEMENTS.md's list
    # (restructuring, litigation, discontinued operations, unusual items)
    # needs line-item detail the packet does not carry — so the reported
    # and normalized figures are the same number. That has to be said
    # rather than left for a reader to assume a normalization happened.
    if om_latest is not None:
        assumptions.append(
            "BUS-OM-008: operating margin is computed from reported EBIT. No normalization "
            "adjustments (restructuring, litigation, discontinued operations, unusual items) "
            "are applied, so the reported and normalized figures are identical here. "
            "NORMALIZATION_AND_RESTATEMENTS.md's rule against aggressive normalization means "
            "this is the conservative reading, not an omission of a computed adjustment."
        )

    # ---- BUS-NOPAT-011 / BUS-IC-012 / BUS-ROIC-013 (reused from valuation_engine) ----
    tax_rate = _tax_rate(annual[-1], 0.21) if annual else 0.21
    # `_tax_rate` substitutes the statutory rate whenever the filing yields
    # no usable normalized cash rate — income_tax_expense missing *or* an
    # effective rate outside [0, 1]. The out-of-range case is exactly the
    # single items NORMALIZATION_AND_RESTATEMENTS.md strips: a valuation-
    # allowance release drives the rate negative, a one-time charge above
    # 100% (Salesforce read 173.8% in FY2016). The disclosure must fire on
    # the same condition the substitution does, or CALCULATION_CONVENTIONS.md's
    # "disclose the substitution" is silently skipped whenever the rate is
    # distorted rather than merely absent — checking `income_tax_expense is
    # None` caught only the second. `_has_reported_tax_rate` is the same test
    # `_tax_rate` and the ROIC-history substitution count already use.
    if not annual or not _has_reported_tax_rate(annual[-1]):
        assumptions.append("BUS-NOPAT-011: normalized cash tax rate unavailable; substituted the 21% statutory rate.")

    if ebit_latest is not None:
        v_nopat = nopat(ebit_latest, tax_rate)
    else:
        v_nopat = _null(NullState.MISSING, "usd", "NOPAT_INPUTS_UNAVAILABLE")
    add("BUS-NOPAT-011", v_nopat, None)
    nopat_latest = v_nopat.value if v_nopat.is_valid else None

    if debt_latest is not None and equity_latest is not None:
        cash_latest = (_reconciled_cash if _reconciled_cash is not None
                       else _num(annual[-1], "cash") or 0.0)
        # CALCULATION_CONVENTIONS.md: "Use average balance-sheet values for
        # return ratios when both beginning and ending values exist. If only
        # ending values exist, label the result END_BALANCE_PROXY and reduce
        # confidence." The call site gated on two years of balance sheet, so a
        # recent listing with only the latest year lost invested capital, ROIC
        # and everything derived rather than getting the ending-balance figure
        # the convention says to compute and flag. `average_invested_capital`
        # already falls back when `begin` is None; only the latest period is
        # required here, and the begin values are passed as-is (None when the
        # prior year is absent) so it can.
        has_begin = len(annual) >= 2 and debt_hist[-2] is not None and equity_hist[-2] is not None
        cash_begin = (_num(annual[-2], "cash") or 0.0) if len(annual) >= 2 else 0.0
        v_ic = average_invested_capital(
            debt_hist[-2] if has_begin else None,
            equity_hist[-2] if has_begin else None,
            cash_begin, debt_latest, equity_latest, cash_latest,
            operating_begin=operating_view_inputs(annual[-2]) if len(annual) >= 2 else None,
            operating_end=operating_view_inputs(annual[-1]) if annual else None,
        )
    else:
        v_ic = _null(NullState.MISSING, "usd", "AVERAGE_INVESTED_CAPITAL_INPUTS_UNAVAILABLE")
    add("BUS-IC-012", v_ic, None)

    if nopat_latest is not None and v_ic.is_valid:
        v_roic = roic(nopat_latest, v_ic.value)
        # CALCULATION_CONVENTIONS.md labels "the result" — the return ratio —
        # END_BALANCE_PROXY, so ROIC inherits it (and its per-warning confidence
        # cut) from an ending-balance invested capital, not only IC-012.
        if WARN_END_BALANCE_PROXY in (v_ic.warnings or []) and v_roic.is_valid:
            v_roic = v_roic.model_copy(update={
                "warnings": sorted({WARN_END_BALANCE_PROXY, *(v_roic.warnings or [])})})
    else:
        v_roic = _null(NullState.MISSING, "pct", "ROIC_INPUTS_UNAVAILABLE")
    add("BUS-ROIC-013", v_roic, _score_from_anchor(v_roic, [(0.0, 0), (0.08, 3), (0.15, 7), (0.25, 10)]))
    roic_latest = v_roic.value if v_roic.is_valid else None
    ctx["roic_latest"] = roic_latest

    # ---- BUS-SPREAD-014 / BUS-EVA-015 ----
    if roic_latest is not None and wacc_value is not None:
        v_spread = spread(roic_latest, wacc_value)
        # Both are return-ratio results, so an ending-balance ROIC carries
        # END_BALANCE_PROXY through to the spread (CALCULATION_CONVENTIONS.md).
        if WARN_END_BALANCE_PROXY in (v_roic.warnings or []) and v_spread.is_valid:
            v_spread = v_spread.model_copy(update={
                "warnings": sorted({WARN_END_BALANCE_PROXY, *(v_spread.warnings or [])})})
    else:
        v_spread = _null(NullState.MISSING, "pct", "SPREAD_INPUTS_UNAVAILABLE")
    add("BUS-SPREAD-014", v_spread, _score_from_anchor(v_spread, [(-0.05, 0), (0.0, 3), (0.05, 7), (0.10, 10)]))
    spread_latest = v_spread.value if v_spread.is_valid else None
    ctx["spread_latest"] = spread_latest

    # BUS-EVA-015 registers "(ROIC - WACC) * Average invested capital". The
    # reused `eva(nopat, wacc, ic) = nopat - wacc*ic` gives exactly that when
    # `ic` is the *average* invested capital, since roic = nopat / v_ic — so it
    # takes `v_ic`, not a freshly-computed *beginning* IC. The old form used the
    # valuation engine's VAL-EVA-020 base (IC_(t-1)), disagreeing with ROIC's
    # capital base, and gated on two years so it vanished for a single-year
    # company whose IC is now an ending-balance proxy.
    if nopat_latest is not None and wacc_value is not None and v_ic.is_valid:
        v_eva = eva(nopat_latest, wacc_value, v_ic.value)
        if WARN_END_BALANCE_PROXY in (v_ic.warnings or []) and v_eva.is_valid:
            v_eva = v_eva.model_copy(update={
                "warnings": sorted({WARN_END_BALANCE_PROXY, *(v_eva.warnings or [])})})
    else:
        v_eva = _null(NullState.MISSING, "usd", "EVA_INPUTS_UNAVAILABLE")
    add("BUS-EVA-015", v_eva, None)

    # ---- ROIC/spread history (feeds BUS-IROIC-016, moat persistence, extension fields) ----
    (roic_hist, spread_hist, substituted_tax_years,
     ic_passed, ic_run) = _roic_and_spread_history(annual, wacc_value)
    ctx["substituted_tax_years"] = substituted_tax_years
    ctx["ic_checks_passed"] = ic_passed
    ctx["ic_checks_run"] = ic_run
    ctx["roic_hist"] = roic_hist
    ctx["spread_hist"] = spread_hist

    # ---- BUS-IROIC-016 / BUS-ALLOC-029: incremental ROIC over a 3y window ----
    # The change is measured from the row INCREMENTAL_ROIC_WINDOW_YEARS
    # back, not from the oldest row available. `annual` is ascending, so
    # the latest is index -1 and three years back is `len - 1 - 3`; a
    # shorter history clamps the start to 0, which is the best available
    # change rather than a mislabelled longer one.
    first_idx = max(0, len(annual) - 1 - INCREMENTAL_ROIC_WINDOW_YEARS)
    if nopat_latest is not None and len(annual) >= 2:
        ebit_first = ebit_hist[first_idx]
        if ebit_first is not None:
            nopat_first = nopat(ebit_first, _tax_rate(annual[first_idx], tax_rate)).value
            delta_nopat = nopat_latest - nopat_first
        else:
            delta_nopat = None
    else:
        delta_nopat = None
    # Proper delta-IC: financing-view IC at the window's first vs. latest row.
    if (
        delta_nopat is not None
        and len(debt_hist) >= 2
        and debt_hist[first_idx] is not None
        and equity_hist[first_idx] is not None
        and debt_latest is not None
        and equity_latest is not None
    ):
        ic_first = ve.invested_capital(debt_hist[first_idx], equity_hist[first_idx], _num(annual[first_idx], "cash") or 0.0).financing_view
        # The latest period takes the resolved tier-1 cash, exactly as it takes
        # the resolved tier-1 `debt_latest`. Reading the raw provider row here
        # left this endpoint combining a tier-1 debt with a provider cash — the
        # same source mix SOURCE_HIERARCHY.md's default order rules out, and the
        # one already corrected for BUS-IC-012.
        ic_last = ve.invested_capital(
            debt_latest, equity_latest,
            (_reconciled_cash if _reconciled_cash is not None
             else _num(annual[-1], "cash") or 0.0),
        ).financing_view
        if ic_first.is_valid and ic_last.is_valid:
            delta_ic = ic_last.value - ic_first.value
        else:
            delta_ic = None
    else:
        delta_ic = None

    if delta_nopat is not None and delta_ic is not None:
        v_iroic = incremental_roic(delta_nopat, delta_ic)
    else:
        v_iroic = _null(NullState.MISSING, "pct", "INCREMENTAL_ROIC_INPUTS_UNAVAILABLE")
    add("BUS-IROIC-016", v_iroic, None)
    iroic_value = v_iroic.value if v_iroic.is_valid else None

    if iroic_value is not None and wacc_value is not None:
        v_alloc = capital_allocation_spread(iroic_value, wacc_value)
    else:
        v_alloc = _null(NullState.MISSING, "pct", "CAPITAL_ALLOCATION_SPREAD_INPUTS_UNAVAILABLE")
    add("BUS-ALLOC-029", v_alloc, _score_from_anchor(v_alloc, [(-0.05, 0), (0.0, 3), (0.05, 7), (0.10, 10)]))

    # ---- BUS-FCFC-017: cumulative FCF conversion (5y or available window) ----
    fcf_hist = [
        (ocf_hist[i] - capex_hist[i]) if ocf_hist[i] is not None and capex_hist[i] is not None else None
        for i in range(n_years)
    ]
    # FORMULAS.md registers BUS-FCFC-017 at "5y rolling" — a single,
    # five-year window — and DATASET.md requires five years of its inputs
    # ("Minimum history is a requirement for a score, not a suggestion").
    # BUS-T005 states the sum over five years. So the sum needs five annual
    # points, the same floor STAB-009/RANGE-010 already enforce; fewer is
    # MISSING, not a shorter sum scored under a period label that claims
    # five years (period=_period_label(annual, 5) below).
    fcf_window = [x for x in fcf_hist[-5:] if x is not None]
    ni_window = [x for x in ni_hist[-5:] if x is not None]
    if len(fcf_window) >= 5 and len(ni_window) >= 5:
        v = cumulative_fcf_conversion(sum(fcf_window), sum(ni_window))
    else:
        v = _null(NullState.MISSING, "ratio", "FCF_CONVERSION_INSUFFICIENT_HISTORY")
    add("BUS-FCFC-017", v, _score_from_anchor(v, [(0.0, 0), (0.5, 4), (0.9, 7), (1.2, 10)]), period=_period_label(annual, 5))
    fcf_conversion = v.value if v.is_valid else None
    ctx["fcf_conversion"] = fcf_conversion

    # ---- BUS-REINV-018 / BUS-SG-019 ----
    # FORMULAS.md's numerator is "Net capex + change in non-cash working
    # capital + capitalized R&D adjustment". Two of the three terms were
    # passed as literal 0.0 with nothing said, so the rate measured capex
    # alone. Working capital is reported and was simply not wired: NVIDIA's
    # grew by about $16B against $6B of capex, so the metric was reading
    # roughly a quarter of the company's actual reinvestment, and
    # BUS-SG-019 (reinvestment x ROIC) inherited the same understatement.
    #
    # The cash-flow statement presents `changeInWorkingCapital` as an
    # adjustment to operating cash flow, so a build in working capital
    # arrives negative. The reinvestment numerator wants it as a use of
    # cash, hence the sign flip.
    # The cash-flow statement's own `changeInWorkingCapital` is an adjustment
    # to operating cash flow, so a build arrives negative and is flipped. FMP
    # reports it and the builder passes unmapped statement keys through under
    # their raw names, so a real packet carries it; where a packet does not
    # (a trimmed fixture, or a filer that omits the line), the change is
    # derived from the two balance sheets instead, where a build is already
    # positive — a use of cash, which is what the reinvestment numerator wants.
    dnwc_reported = _num(annual[-1], "changeInWorkingCapital") if annual else None
    if dnwc_reported is not None:
        dnwc, dnwc_basis = -dnwc_reported, "cash-flow statement"
    elif _adapters.replaces_model(packet.analysis.industry_adapter):
        # INDUSTRY_ADAPTERS.md, banks: "do not use ... conventional FCFF",
        # and VAL-NWC-006 is one of its inputs. A bank's current accounts are
        # deposits and loans, not working capital, so the balance-sheet
        # derivation is arithmetically fine and economically meaningless --
        # JPM computes to -2.7 TRILLION. A reported cash-flow line stays
        # usable (the filer decided what belongs in it); deriving one here
        # does not.
        dnwc, dnwc_basis = 0.0, "adapter"
    else:
        nwc_end = non_cash_working_capital(annual[-1]) if annual else None
        nwc_begin = non_cash_working_capital(annual[-2]) if len(annual) >= 2 else None
        if nwc_end is not None and nwc_begin is not None:
            dnwc, dnwc_basis = nwc_end - nwc_begin, "balance sheet"
        else:
            dnwc, dnwc_basis = 0.0, None
    # Capitalizing R&D requires an amortization life no filing states; it
    # is an analyst adjustment, so it comes from the overlay or stays out
    # and says so, rather than being silently zero.
    rd_adjustment = _overlay_number(overlay, "capitalized_rd_adjustment", input_warnings)
    rd_adjustment = rd_adjustment if rd_adjustment is not None else 0.0

    if len(capex_hist) >= 1 and capex_hist[-1] is not None and nopat_latest not in (None, 0):
        v_reinv = reinvestment_rate(capex_hist[-1], dnwc, rd_adjustment, nopat_latest)
        if dnwc_basis == "balance sheet":
            assumptions.append(
                "BUS-REINV-018: the change in non-cash working capital is calculated from the "
                "two reported balance sheets — (current assets - cash) - (current liabilities - "
                "short-term debt) — because no statement line carries it directly. "
                "MISSING_DATA_POLICY.md step 3: calculable from validated components, evidence "
                "class C."
            )
        elif dnwc_basis == "adapter":
            assumptions.append(
                f"BUS-REINV-018: industry_adapter={packet.analysis.industry_adapter!r} replaces "
                "the model, and INDUSTRY_ADAPTERS.md bars conventional FCFF for it. No reported "
                "working-capital change is available and the balance-sheet derivation is "
                "meaningless here (current accounts are deposits/loans, not working capital), so "
                "the term is omitted rather than derived; the numerator covers net capex only."
            )
        elif dnwc_basis is None:
            assumptions.append(
                "BUS-REINV-018: neither a reported working-capital change nor the two balance "
                "sheets needed to derive one; the reinvestment numerator covers net capex only "
                "and understates reinvestment."
            )
        if rd_adjustment == 0.0:
            assumptions.append(
                "BUS-REINV-018: no capitalized-R&D adjustment supplied. R&D is expensed as "
                "reported; capitalizing it needs an amortization life no filing states, so the "
                "term is omitted rather than assumed."
            )
        # FORMULAS.md: "unstable when NOPAT is near zero". Refusing only at
        # exactly zero let a NOPAT a rounding error away from zero produce
        # an arbitrarily large rate that BUS-SG-019 then multiplied.
        nopat_scale = [abs(x) for x in
                       [nopat(e, _tax_rate(r, 0.21)).value
                        for e, r in zip(ebit_hist, annual) if e is not None]
                       if x is not None]
        if nopat_scale:
            typical = sum(nopat_scale) / len(nopat_scale)
            if typical > 0 and abs(nopat_latest) < NEAR_ZERO_NOPAT_FRACTION * typical:
                v_reinv = _ok(v_reinv.value, unit="ratio",
                              warnings=[WARN_NOPAT_NEAR_ZERO]) if v_reinv.is_valid else v_reinv
                assumptions.append(
                    f"BUS-REINV-018: latest NOPAT is under "
                    f"{NEAR_ZERO_NOPAT_FRACTION:.0%} of its own "
                    f"{len(nopat_scale)}-year average magnitude, so the rate is unstable in "
                    "FORMULAS.md's sense. The threshold is measured against the company's own "
                    "history because FORMULAS.md sets no magnitude."
                )
    else:
        v_reinv = _null(NullState.MISSING, "ratio", "REINVESTMENT_RATE_INPUTS_UNAVAILABLE")
    add("BUS-REINV-018", v_reinv, None)

    if v_reinv.is_valid and roic_latest is not None:
        v_sg = fundamental_growth(v_reinv.value, roic_latest)
    else:
        v_sg = _null(NullState.MISSING, "pct", "SUSTAINABLE_GROWTH_INPUTS_UNAVAILABLE")
    add("BUS-SG-019", v_sg, None)

    # ---- BUS-NRR-020 / BUS-GRR-021 / BUS-CHURN-022: customer economics (overlay only) ----
    subscription = _subscription_business(packet, overlay)
    ctx["customer_economics_applicable"] = subscription
    # `DATASET.md` tipa `retention_churn_cohorts` -- NRR, GRR, churn, cohortes
    # -- como **conditional**, y su fuente como "issuer KPI / validated
    # dataset". Es decir: solo forma parte del paquete esperado cuando el
    # emisor PUBLICA ese KPI. Microsoft no publica NRR ni churn ni CAC; nadie
    # los tiene por no haberlos buscado.
    #
    # Cobrarlos como MISSING hacia que el modelo de negocio decidiera la
    # cobertura en vez de los datos. Medido: las siete salian NOT_APPLICABLE
    # para Coca-Cola y un banco -- fuera del denominador, gratis -- y MISSING
    # para Microsoft y Palantir. Ser una empresa de suscripcion costaba los 3
    # puntos enteros de esta dimension que un refresquero no paga, y por eso
    # KO sacaba 0,929 en business contra 0,583 de MSFT.
    #
    # Es el mismo defecto que ya se corrigio en la concentracion de clientes:
    # no tener el dato porque la condicion no se cumple no es lo mismo que
    # tenerlo ausente.
    #
    # El aviso SIGUE nombrando la clave que hay que escribir, asi que un
    # analista que quiera puntuarlas ve exactamente que suministrar -- lo que
    # cambia es que su ausencia deja de restar.
    _absent = NullState.NOT_APPLICABLE
    _why = ("_ISSUER_DOES_NOT_PUBLISH_THIS_KPI" if subscription
            else "_NOT_A_SUBSCRIPTION_BUSINESS")

    retention = _overlay_mapping(overlay, "retention", input_warnings)
    if {"begin", "expansion", "contraction", "churn"} <= retention.keys():
        v_nrr = net_revenue_retention(retention["begin"], retention["expansion"], retention["contraction"], retention["churn"])
        v_grr = gross_revenue_retention(retention["begin"], retention["contraction"], retention["churn"])
    else:
        v_nrr = _null(_absent, "pct", "NRR_UNAVAILABLE" + _why)
        v_grr = _null(_absent, "pct", "GRR_UNAVAILABLE" + _why)
    add("BUS-NRR-020", v_nrr, _score_from_anchor(v_nrr, [(0.85, 0), (1.0, 6), (1.1, 8), (1.2, 10)]), source=analyst_source, period=analyst_period)
    add("BUS-GRR-021", v_grr, _score_from_anchor(v_grr, [(0.70, 0), (0.85, 5), (0.95, 8), (1.0, 10)]), source=analyst_source, period=analyst_period)

    churn_inputs = _overlay_mapping(overlay, "churn", input_warnings)
    if churn_inputs and {"lost", "begin_customers"} <= churn_inputs.keys():
        v_churn = logo_churn(churn_inputs["lost"], churn_inputs["begin_customers"])
    else:
        v_churn = _null(_absent, "pct", "LOGO_CHURN_UNAVAILABLE" + _why)
    add("BUS-CHURN-022", v_churn, _score_from_anchor(v_churn, [(0.20, 0), (0.10, 5), (0.05, 8), (0.0, 10)]), source=analyst_source, period=analyst_period)

    # ---- BUS-LTV-023 / BUS-CAC-024 / BUS-LTVCAC-025 / BUS-PAYBACK-026 (overlay only) ----
    ce = _overlay_mapping(overlay, "customer_economics", input_warnings)
    if {"arpu", "gross_margin", "customer_life_years"} <= ce.keys():
        v_ltv = customer_ltv(ce["arpu"], ce["gross_margin"], ce["customer_life_years"])
    else:
        v_ltv = _null(_absent, "usd", "LTV_INPUTS_UNAVAILABLE" + _why)
    add("BUS-LTV-023", v_ltv, None, source=analyst_source, period=analyst_period)

    if {"cac_spend", "new_customers"} <= ce.keys():
        v_cac = customer_acquisition_cost(ce["cac_spend"], ce["new_customers"])
    else:
        v_cac = _null(_absent, "usd", "CAC_INPUTS_UNAVAILABLE" + _why)
    add("BUS-CAC-024", v_cac, None, source=analyst_source, period=analyst_period)

    if v_ltv.is_valid and v_cac.is_valid:
        v_ltvcac = ltv_to_cac(v_ltv.value, v_cac.value)
    else:
        v_ltvcac = _null(_absent, "ratio", "LTV_CAC_RATIO_INPUTS_UNAVAILABLE" + _why)
    add("BUS-LTVCAC-025", v_ltvcac, _score_from_anchor(v_ltvcac, [(1.0, 0), (2.0, 4), (3.0, 7), (5.0, 10)]), source=analyst_source, period=analyst_period)

    if v_cac.is_valid and {"monthly_arpu", "gross_margin"} <= ce.keys():
        v_payback = cac_payback_months(v_cac.value, ce["monthly_arpu"], ce["gross_margin"])
    else:
        v_payback = _null(_absent, "months", "CAC_PAYBACK_INPUTS_UNAVAILABLE" + _why)
    add("BUS-PAYBACK-026", v_payback, _score_from_anchor(v_payback, [(24.0, 0), (18.0, 5), (12.0, 8), (6.0, 10)], higher_is_better=False), source=analyst_source, period=analyst_period)

    # ---- BUS-GUIDE-027: guidance accuracy (overlay only) ----
    guidance_history = overlay.get("guidance_history") or []
    if guidance_history:
        # `guidance_history` is hand-written by an analyst, so a row can
        # carry the keys with null or non-numeric values. Testing only for
        # key presence let `abs(None)` raise and take the whole specialist
        # down; a bad row is skipped instead.
        accuracies = []
        for g in guidance_history:
            if not isinstance(g, dict):
                continue
            actual, mid = g.get("actual"), g.get("guidance_midpoint")
            try:
                accuracies.append(guidance_accuracy(float(actual), float(mid)).value)
            except (TypeError, ValueError):
                assumptions.append(
                    f"BUS-GUIDE-027: guidance row skipped, actual/guidance_midpoint "
                    f"not numeric ({actual!r}, {mid!r})."
                )
        v_guide = _ok(sum(accuracies) / len(accuracies), unit="ratio") if accuracies else _null(
            NullState.MISSING, "ratio", "GUIDANCE_ACCURACY_NO_VALID_ROWS"
        )
    elif overlay.get("_sin_comunicado_de_resultados"):
        # El emisor no presenta comunicado de resultados ante la SEC, que es
        # la fuente que `DATASET.md` declara para este campo. No es que no se
        # haya leido: no existe por esa via.
        #
        # Verificado sobre seis emisores: KO, WMT y PLTR si lo presentan --
        # ahi el dato esta y su ausencia SIGUE contando como hueco real, con
        # la clave nombrada en el aviso. NVDA, LLY y XOM no, y publican en su
        # propia sala de prensa.
        v_guide = _null(NullState.NOT_APPLICABLE, "ratio",
                        "NO_EARNINGS_RELEASE_FILED: el emisor no presenta "
                        "comunicado de resultados ante la SEC, que es la "
                        "fuente que DATASET.md declara para el guidance. "
                        "Suministra `guidance_history` en "
                        "Entradas/<TICKER>.json si lo publica por otra via")
    else:
        v_guide = _null(NullState.MISSING, "ratio", "GUIDANCE_ACCURACY_UNAVAILABLE")
    add("BUS-GUIDE-027", v_guide, _score_from_anchor(v_guide, [(0.0, 0), (0.7, 4), (0.9, 7), (1.0, 10)]), source=analyst_source, period=_guidance_period(guidance_history))

    # ---- BUS-DIL-028: diluted share CAGR over the registered window ----
    dil_window = _cagr_over_window(annual, diluted_hist, CAGR_WINDOW_YEARS)
    if dil_window is not None:
        dil_begin, dil_end, dil_years, dil_period = dil_window
        v_dil = diluted_share_cagr(dil_end, dil_begin, dil_years)
    else:
        v_dil, dil_period = _null(NullState.MISSING, "pct", "DILUTED_SHARE_CAGR_INSUFFICIENT_HISTORY"), None
    add("BUS-DIL-028", v_dil, _score_from_anchor(v_dil, [(-0.02, 10), (0.0, 7), (0.02, 4), (0.05, 0)]), period=dil_period)
    ctx["diluted_cagr"] = v_dil.value if v_dil.is_valid else None
    # DECISION_RULES.md's DILUTION_RED_FLAG names a three-year window
    # specifically — "diluted shares grow >5% CAGR *for three years*" —
    # distinct from the metric's registered 3y/5y, so the flag reads its
    # own 3-year CAGR.
    dil_flag_window = _cagr_over_window(annual, diluted_hist, 3)
    if dil_flag_window is not None:
        _fb, _fe, _fy, _ = dil_flag_window
        _v3 = diluted_share_cagr(_fe, _fb, _fy)
        ctx["diluted_cagr_3y"] = _v3.value if _v3.is_valid else None
        # The span the window actually reached. "for three years" is a
        # duration condition, so the flag below fires only on a genuine
        # three-year span; a shorter history reports the CAGR but does not
        # trip the flag.
        ctx["diluted_cagr_3y_years"] = _fy
    else:
        ctx["diluted_cagr_3y"] = None
        ctx["diluted_cagr_3y_years"] = None

    # ---- BUS-SBC-030: SBC burden ----
    sbc_latest = sbc_hist[-1] if sbc_hist else None
    if sbc_latest is not None and rev_latest not in (None, 0):
        v_sbc = sbc_burden(sbc_latest, rev_latest)
    else:
        v_sbc = _null(NullState.MISSING, "pct", "SBC_BURDEN_INPUTS_UNAVAILABLE")
    add("BUS-SBC-030", v_sbc, _score_from_anchor(v_sbc, [(0.0, 10), (0.02, 8), (0.10, 3), (0.20, 0)]))
    fcf_latest = fcf_hist[-1] if fcf_hist else None
    v_sbc_fcf = (
        sbc_to_fcf(sbc_latest, fcf_latest)
        if sbc_latest is not None and fcf_latest is not None
        else _null(NullState.MISSING, "ratio", "SBC_TO_FCF_INPUTS_UNAVAILABLE")
    )
    ctx["sbc_to_fcf"] = v_sbc_fcf.value if v_sbc_fcf.is_valid else None

    # ---- Management capital-return proxy (buybacks/dividends funded from surplus) ----
    if annual:
        buybacks = abs(_num(annual[-1], "common_stock_repurchased") or 0.0)
        dividends = abs(_num(annual[-1], "dividends_paid") or 0.0)
        ctx["returns_cash"] = (buybacks + dividends) > 0
    else:
        ctx["returns_cash"] = None

    # ---- Competitive-position proxy: revenue CAGR (documented proxy, see run()) ----
    ctx["om_latest"] = om_latest

    # Collected after every overlay read, not before: an earlier
    # placement extended an empty list and the warnings never reached the
    # report, which is the only place the analyst would see that a block
    # of their file was ignored.
    assumptions.extend(input_warnings)
    assumptions.extend(_anchor_disclosures())
    assumptions.extend(calibration_disclosures())
    return rows, assumptions, judgment_requests, ctx


def _score_from_anchor(
    v: Value, anchors: list[tuple[float, float]], *, higher_is_better: bool = True
) -> float | None:
    """0-10 continuous score via `wbj.core.scoring.anchor_score`, or `None`
    (NOT_SCORABLE) when `v` is null. `higher_is_better=False` mirrors the
    anchors already being given in "worse->better" x-order (e.g. CAC
    payback months) rather than inverting the score after the fact."""
    if v.is_null:
        return None
    return anchor_score(v.value, anchors)


#: Every overlay key this module reads, paired with the DATASET.md field
#: it satisfies. Used to build `source_lineage`.
_OVERLAY_LINEAGE: dict[str, str] = {
    "wacc": "wacc_inputs",
    "segment_shares": "revenue_by_segment_5y",
    "recurring_revenue": "recurring_revenue_5y",
    # Declara que la empresa SI tiene ingreso recurrente aunque no publique el
    # porcentaje. Sale del mismo campo del dataset porque responde a la misma
    # pregunta: si ese campo aplica a esta empresa.
    "recurring_revenue_applies": "recurring_revenue_5y",
    "largest_customer_share": "customer_revenue_shares",
    # No es un dato: dice si la FUENTE que DATASET.md declara para el guidance
    # existe para este emisor. Sin el, no haber presentado comunicado y no
    # haberlo leido se leian igual.
    "_sin_comunicado_de_resultados": "management_guidance_history",
    # El otro lado de la misma fila de DATASET.md. `customer_revenue_shares`
    # es "required WHEN DISCLOSED": estas dos claves dicen que NO se divulga
    # porque no hay nada que divulgar, y ese hallazgo tiene el mismo origen
    # -- la nota de concentracion del filing -- que la cifra cuando la hay.
    "no_customer_above_threshold": "customer_revenue_shares",
    "sin_cliente_sobre_umbral": "customer_revenue_shares",
    "customer_shares": "customer_revenue_shares",
    "retention": "retention_churn_cohorts",
    "churn": "retention_churn_cohorts",
    "customer_economics": "customer_economics",
    "guidance_history": "management_guidance_history",
    "peer_roic": "competitive_evidence",
    "peer_operating_margin": "competitive_evidence",
    "peer_recession_drawdown": "competitive_evidence",
    "cost_of_equity": "wacc_inputs",
    "capitalized_rd_adjustment": "capital_allocation_10y",
    # The analyst-supplied TAM's source tier. It decides whether SCORING.md's
    # market-definition cap on competitive position holds or lifts — 8 against
    # 10 on a four-point dimension — and was read without ever being declared,
    # so a score that moved on it could not be traced to it.
    #
    # It is filed under the TAM, not under market share. Victor defines the
    # tiers in `03_market_analysis/DECISION_RULES.md` under the heading
    # "Source-quality tiers for TAM", and its disclosure rule reads "1. exact
    # TAM definition and source tier". The field whose preferred source those
    # tiers grade is `tam_sam_som_sources` ("government / industry primary
    # source"). This row previously named `market_share_company_industry_3y`,
    # which is what the tier *affects* here — not where the figure comes from,
    # and a lineage table records provenance.
    "tam_source_tier": "tam_sam_som_sources",
    # SCORING.md's durability concentration-cap exception ("unless contract
    # protection is quantified"). Contractual protection around a
    # concentrated customer is the quantified form of `competitive_evidence`
    # ("switching costs", "licenses"), which has no dedicated numeric field.
    "contract_protection": "competitive_evidence",
    # Not a DATASET.md company field: the NBER recession calendar, which
    # BUS-STAB-009's drawdown needs, comes from FRED's USREC series.
    "recession_years": "FRED USREC (NBER recession indicator)",
}


def _source_lineage(packet: Packet, overlay: dict[str, Any]) -> list[str]:
    """What this run actually read.

    The field was a one-element literal, `["packet.fundamentals.annual"]`,
    which understated the provenance every time: the conditional inputs
    DATASET.md lists — WACC, segment shares, customer concentration,
    retention, guidance — all arrive through the overlay, and none of
    them appeared. AGENT.md's operating sequence ends with "Return the
    required schema and complete audit trail", and an audit trail that
    omits the analyst-supplied half is not complete.
    """
    lineage = ["packet.fundamentals.annual", "packet.analysis", "packet.security"]
    for key, dataset_field in sorted(_OVERLAY_LINEAGE.items()):
        if overlay.get(key):
            lineage.append(f"overlay.{key} (DATASET.md: {dataset_field})")
    return lineage


def _reconciled_fact(packet: Packet, key: str) -> tuple[float | None, bool]:
    """A reconciled fact and whether its sources conflict.

    `packet.facts_table` holds the builder's FMP-vs-EDGAR verdict, with
    tier-1 EDGAR preferred per SOURCE_HIERARCHY.md's default order.
    Returns `(value, conflicted)`; a conflicted fact carries no value,
    which is the document's "do not score it".
    """
    fact = (getattr(packet, "facts_table", None) or {}).get(key)
    if fact is None:
        return None, False
    state = str(getattr(fact, "state", "") or "")
    conflicted = state.endswith("CONFLICTED")
    value = getattr(fact, "value", None)
    return (None if conflicted else value), conflicted


def _conflict_values(packet: Packet, key: str) -> tuple[float | None, float | None]:
    """The `(fmp, edgar)` source values behind a conflicted reconciled input.

    The builder stores them under `<key>:fmp` / `<key>:edgar` whenever
    `reconcile` marks the input CONFLICTED, so a specialist can score the
    category under each and measure SOURCE_HIERARCHY.md's materiality.
    """
    ft = getattr(packet, "facts_table", None) or {}

    def _v(name: str) -> float | None:
        f = ft.get(name)
        return getattr(f, "value", None) if f is not None else None

    return _v(f"{key}:fmp"), _v(f"{key}:edgar")


def _resolved_latest_input(packet: Packet, key: str,
                           resolution: dict[str, str] | None, raw: float | None) -> float | None:
    """The latest-period value for a reconciled input, honouring
    SOURCE_HIERARCHY.md's tier-1 preference and `run()`'s conflict resolution.

    Non-conflicted: the builder's reconciled (tier-1) value where present,
    else the raw provider row. Conflicted: the EDGAR (tier-1) value by
    default, the FMP value only while `run()` measures the alternative. The
    raw row is the last resort. (Under a `withhold` resolution the derived
    metrics are nulled in `add()`, so the value chosen here is immaterial.)
    """
    reconciled, conflicted = _reconciled_fact(packet, key)
    if not conflicted:
        return reconciled if reconciled is not None else raw
    fmp_v, edgar_v = _conflict_values(packet, key)
    if (resolution or {}).get(key) == "fmp":
        return fmp_v if fmp_v is not None else raw
    return edgar_v if edgar_v is not None else raw


def _conflict_is_material(a: "BusinessOutput", b: "BusinessOutput") -> bool:
    """SOURCE_HIERARCHY.md's materiality for business: a source conflict is
    material when swapping the two source values changes the category score by
    at least 0.5/10, or flips a mandatory gate or override. The gates/overrides
    a debt/cash conflict can move surface as `mandatory_flags` (VALUE_DESTRUCTION)
    and the verdict band (the Excellent gate, the moat cap)."""
    score_a = a.category.score_10 or 0.0
    score_b = b.category.score_10 or 0.0
    if abs(score_a - score_b) >= 0.5:
        return True
    return set(a.mandatory_flags) != set(b.mandatory_flags) or a.verdict != b.verdict


# --- calibration this engine chose ----------------------------------------
#
# Cerebro states a direction for each of these and no magnitude. There is
# no derivation: "approximately", "near zero" and "reduce" fix a sign,
# not a number, and unlike the evidence classes or the adapter treatments
# there is no ordered set of levels to space evenly across a stated
# range. A metric cannot be computed without them, so they are chosen
# here and declared — AGENT.md permits a number that is not a reported
# value only as an "explicitly disclosed assumption", and this is the
# disclosure.
#
# Registered together so no reader has to hunt for which figures are
# Victor's and which are not. Everything else that scores traces to a
# stated threshold; see ANCHOR_PROVENANCE.
CALIBRATION_REGISTRY: dict[str, tuple[str, str]] = {
    # constant: (the Cerebro text that fixes the direction, why no number follows)
    "SHARE_SUM_TOLERANCE": (
        'BUS-MIX-001: "Shares must sum to approximately 100% after eliminations."',
        "'approximately' bounds nothing; eliminations and rounding move a real "
        "breakdown off 1.0 by an amount no document states."),
    "NEAR_ZERO_NOPAT_FRACTION": (
        'BUS-REINV-018: "unstable when NOPAT is near zero." '
        'CALCULATION_CONVENTIONS.md: "Never divide by a denominator whose '
        'absolute value is immaterial without marking the ratio NOT_MEANINGFUL."',
        "neither 'near zero' nor 'immaterial' is quantified for a denominator; "
        "SOURCE_HIERARCHY.md's materiality defaults cover source conflicts, not "
        "divisors. Measured against the company's own trailing NOPAT rather than "
        "an external figure, so at least the comparison is its own."),
    "MODEL_FIT_PENALTY_PER_PROXY": (
        'FORMULAS.md execution rules: "Record any proxy in `warnings` and '
        'reduce model-fit confidence."',
        "'reduce' gives a direction and no size. Sits between the documented "
        "levels rather than stepping between them: a substituted input degrades "
        "the fit, but not by as much as the methodology naming a different model "
        "altogether."),
    "GUIDANCE_MATERIALITY_FLOOR": (
        'BUS-GUIDE-027: "1 - abs(Actual - Guidance midpoint) / '
        'max(abs(Guidance midpoint), materiality_floor)".',
        "the formula names `materiality_floor` as the denominator floor but "
        "gives no number. It binds only when a guidance midpoint is itself near "
        "zero, where an unfloored denominator would divide accuracy toward minus "
        "infinity; chosen small so it changes nothing for an ordinary midpoint."),
}


def calibration_disclosures() -> list[str]:
    """One line per constant this engine chose, for the audit trail."""
    return [
        f"Engine calibration {name} = {globals()[name]!r}. Cerebro fixes the "
        f"direction ({basis}) and states no magnitude: {why}"
        for name, (basis, why) in sorted(CALIBRATION_REGISTRY.items())
    ]


#: SCORING.md names inputs and a weighting scheme that Cerebro never
#: registers a formula, threshold, or number for. AGENT.md's no-speculation
#: rule forbids scoring them, and its "complete audit trail" requirement
#: means the gap must be disclosed in the output rather than left silent in
#: code comments. These are surfaced on every run so a reader — Victor —
#: sees exactly what is his to register before it can change.
UNREGISTERED_METHODOLOGY_DISCLOSURES: tuple[str, ...] = (
    "SCORING.md names `rank` (competitive position) and `cyclicality` "
    "(business durability) as primary inputs, but FORMULAS.md registers no "
    "formula and states no threshold for either, so neither carries a scored "
    "slot (AGENT.md no-speculation rule). Scoring them requires Victor to "
    "register a formula in FORMULAS.md.",
    "SCORING.md registers no per-dimension input weights. Per SCORING_ENGINE.md "
    "(\"Weights must be registered in that metric's scoring file\"), each "
    "dimension's registered inputs are weighted equally until Victor registers "
    "weights in SCORING.md.",
)


# --- anchor provenance -----------------------------------------------------
#
# SCORING_ENGINE.md: "When an absolute band is defined, use piecewise-linear
# interpolation between registered anchors." Registered, not chosen at the
# call site and left there.
#
# Some anchor points are Victor's own numbers and can be pointed at: the
# 30% concentration flag, the 5% dilution CAGR, the 3- and 5-point margin
# ranges, ROIC >=20% and FCF conversion >=0.9x from the Excellent row,
# the 5pp spread from the wide-moat gate. Others are engine calibration —
# he states a direction ("Lower is better") without a scale, and a metric
# cannot score without one.
#
# This table says which is which, so a reader can tell a number Victor
# wrote from a number this module chose. AGENT.md permits the latter only
# as an "explicitly disclosed assumption"; `_anchor_disclosures()` is the
# disclosure.
ANCHOR_PROVENANCE: dict[str, tuple[str, str]] = {
    # metric_id: (source, note)
    #
    # VICTOR — the threshold that decides this metric's score is a number he
    # states, in his own words, for this metric's own quantity.
    #
    # Not "every anchor point": he never states a full 0-10 scale, so the
    # endpoints and the interior are interpolation in every case here — 0.30
    # is his for BUS-CONC-003, 0.50 and 1.0 are not. What the label claims is
    # narrower and checkable: the number the band turns on is his, not
    # derived from a band description (MIXED), not absent altogether (ENGINE),
    # and not borrowed from a rule about a different quantity (BUS-ALLOC-029,
    # below). `test_the_victor_anchors_are_the_ones_he_actually_states` pins
    # exactly this list.
    "BUS-CONC-003":    ("VICTOR",  "DECISION_RULES.md: flag >30%; SCORING.md caps at 6 there"),
    "BUS-RANGE-010":   ("VICTOR",  "FORMULAS.md <=3pp signal; DECISION_RULES.md <=5pp wide-moat gate"),
    "BUS-ROIC-013":    ("VICTOR",  "DECISION_RULES.md verdict bands: >=20% excellent, 15-20 good, 8-15 mediocre"),
    "BUS-SPREAD-014":  ("VICTOR",  "DECISION_RULES.md wide-moat gate: >=5 percentage points"),
    "BUS-DIL-028":     ("VICTOR",  "DECISION_RULES.md: dilution red flag above 5% CAGR"),
    # Not VICTOR: the group above means "every anchor point is a threshold he
    # states", and he states none for the *incremental* spread. FORMULAS.md's
    # BUS-ALLOC-029 row gives only "Primary quantitative test of reinvestment
    # quality", and SCORING.md's management row says only 'Incremental ROIC >
    # WACC' — a dimension-level band listing all four of that row's inputs, not
    # a scale for this metric. The 5pp is his magnitude for a different
    # quantity (the wide-moat ROIC-WACC spread), borrowed here, so the label is
    # MIXED and the borrowing is disclosed in the output instead of being
    # passed off as his. The guard test's own VICTOR list already excluded it.
    "BUS-ALLOC-029":   ("MIXED",
                        "DECISION_RULES.md's wide-moat row fixes 5pp for the ROIC-WACC spread; "
                        "SCORING.md's management row states only 'Incremental ROIC > WACC' for "
                        "this one, so the magnitude is borrowed and the scale is this engine's"),

    # MIXED — the boundary that decides a band is his, stated in
    # SCORING.md's 0-3 / 4-6 / 7-10 column or in FORMULAS.md; the
    # interior points are interpolation this engine chose. Reading these
    # as wholly invented understates what his text pins down: a first
    # pass here marked seven of them ENGINE, which was too harsh.
    "BUS-REC-002":     ("MIXED",   "SCORING.md durability 0-3 'One-off revenue' fixes 0%->0 and full recurrence->10; the 30/70 breakpoints are scale"),
    # Its caveat is "Lower is better; also report range and recession-year
    # drawdown" — a ranking direction and no threshold. The <=3pp signal
    # this entry used to cite belongs to BUS-RANGE-010, one row below;
    # attributing it here credited BUS-STAB-009 with a threshold Cerebro
    # never gives it.
    "BUS-STAB-009":    ("ENGINE",  "FORMULAS.md gives only 'Lower is better' for this row and states no threshold; the margin-range signal this entry used to cite belongs to BUS-RANGE-010"),
    "BUS-FCFC-017":    ("MIXED",   "DECISION_RULES.md gives >=0.9x for the Excellent row; the rest is scale"),
    "BUS-NRR-020":     ("MIXED",   "SCORING.md customer-economics 4-6 'Stable retention' fixes NRR=1.0 at the band top; 7-10 'compounding' fixes the direction above it"),
    "BUS-GRR-021":     ("MIXED",   "GRR=1.0 is no contraction or churn, the top of SCORING.md's 'Stable retention'; the band below is scale"),
    "BUS-CHURN-022":   ("MIXED",   "SCORING.md 0-3 'Deteriorating retention' fixes the direction and zero churn->10; the magnitudes are scale"),
    "BUS-LTVCAC-025":  ("MIXED",   "SCORING.md 0-3 'uneconomic acquisition' fixes LTV/CAC=1.0 at 0 — below it, acquisition costs more than it returns"),
    "BUS-GUIDE-027":   ("MIXED",   "FORMULAS.md clips to [0,1], fixing both endpoints; the band inside is scale"),

    # ENGINE — he states a direction and no number anywhere, so the whole
    # scale is this engine's. A metric cannot score without one.
    # PROXY — MISSING_DATA_POLICY.md step 4: "Is a proxy explicitly
    # registered? If yes, use it with a proxy flag and lower model-fit
    # confidence." That authorises the score; the scale it is scored on
    # is still not stated, so it stays declared.
    "BUS-CAGR-006":    ("PROXY",   "MISSING_DATA_POLICY.md step 4 authorises a flagged proxy for the market-share trend"),
    "BUS-PAYBACK-026": ("ENGINE",  "SCORING.md names payback in the 7-10 band but states no month count"),
    "BUS-SBC-030":     ("ENGINE",  "FORMULAS.md gives no SBC-burden threshold and no ranking direction for this row"),
}


def _authorised_score(metric_id: str, score10: float | None) -> float | None:
    """The score a metric is allowed to publish.

    SCORING_ENGINE.md opens two scoring paths: an absolute band
    interpolated "between registered anchors", and a peer percentile
    needing "a minimum of 8 valid peers". MISSING_DATA_POLICY.md closes
    the list — "Otherwise, use `NOT_SCORABLE`".

    A metric whose provenance is ENGINE has neither: Cerebro states no
    threshold for it, and no peer set exists for that quantity. It keeps
    its computed value, which is real, and publishes no score.

    A PROXY is different and is allowed through: step 4 of the same
    decision tree — "Is a proxy explicitly registered? If yes, use it
    with a proxy flag and lower model-fit confidence" — authorises the
    score, and the flag and the confidence reduction are applied.
    """
    if ANCHOR_PROVENANCE.get(metric_id, ("", ""))[0] == "ENGINE":
        return None
    return score10


def _anchor_disclosures() -> list[str]:
    """One line per metric whose scoring scale this module calibrated.

    AGENT.md's no-speculation rule allows a number that is not Victor's
    only as an "explicitly disclosed assumption". Without this the output
    presented his thresholds and the engine's interchangeably.
    """
    engine = sorted(m for m, (s, _) in ANCHOR_PROVENANCE.items() if s == "ENGINE")
    mixed = sorted(m for m, (s, _) in ANCHOR_PROVENANCE.items() if s == "MIXED")
    proxy = sorted(m for m, (s, _) in ANCHOR_PROVENANCE.items() if s == "PROXY")
    out: list[str] = []
    if mixed:
        out.append(
            "Scoring anchors (partly derived): " + ", ".join(mixed) + ". The band "
            "boundary that decides each score is stated in Cerebro — SCORING.md's "
            "0-3/4-6/7-10 column or FORMULAS.md — and the interpolation points "
            "between boundaries are this engine's calibration.")
    if proxy:
        out.append(
            "Scoring anchors (registered proxy): " + ", ".join(proxy) + ". "
            "MISSING_DATA_POLICY.md step 4 authorises a flagged proxy where the "
            "real input is unavailable; the substitution is recorded in the "
            "metric warnings and reduces model-fit confidence. The scale itself "
            "is not stated in Cerebro.")
    if engine:
        out.append(
            "Not scored: " + ", ".join(engine) + ". Cerebro states no threshold "
            "for these and no peer set exists, so neither of SCORING_ENGINE.md's "
            "scoring paths is open and MISSING_DATA_POLICY.md's step 5 applies — "
            "\"Otherwise, use NOT_SCORABLE\". Their computed values are reported; "
            "their scores are withheld.")
    return out


# --- formula versions ------------------------------------------------------
#
# FORMULA_REGISTRY.md requires every formula to carry "a formula version",
# and its change-control rule is explicit: "A threshold or formula change
# is a model change, not a copy edit. It requires: a version increment; a
# reason; before/after test cases; an impact sample."
#
# There was no per-formula version. All thirty rows reported `_VERSION`,
# the *agent* version, so a reader could not tell a formula that had
# never changed from one whose behaviour had been rewritten — and eight
# were rewritten while still declaring 2.0.0.
#
# Baseline is the release these formulas shipped in. A formula whose
# behaviour changed carries a dated version and an entry in
# `FORMULA_CHANGELOG` naming the reason and the test that covers it.
#: FORMULAS.md's "Required inputs" column, verbatim, for each registered
#: formula. Its execution rules say "Show calculation inputs in the output
#: packet", and DATA_POLICY.md's lineage block carries `calculation_inputs`;
#: neither was implemented, so a reader could see a metric's value, formula
#: and version but not which quantities went into it. Taken from his column
#: rather than restated, so the packet shows what the registry asks for.
FORMULA_INPUTS: dict[str, str] = {
    "BUS-MIX-001": "segment revenue, total revenue",
    "BUS-REC-002": "recurring revenue, revenue",
    "BUS-CONC-003": "customer revenue, total revenue",
    "BUS-HHI-004": "all customer shares or disclosed approximation",
    "BUS-HHI-005": "segment revenue shares",
    "BUS-CAGR-006": "revenue start/end, years",
    "BUS-GM-007": "gross profit, revenue",
    "BUS-OM-008": "normalized EBIT, revenue",
    "BUS-STAB-009": "5 annual operating margins",
    "BUS-RANGE-010": "5 annual margins",
    "BUS-NOPAT-011": "EBIT, tax rate",
    "BUS-IC-012": "debt, equity, excess cash",
    "BUS-ROIC-013": "NOPAT, invested capital",
    "BUS-SPREAD-014": "ROIC, WACC",
    "BUS-EVA-015": "spread, invested capital",
    "BUS-IROIC-016": "multi-year NOPAT and invested capital",
    "BUS-FCFC-017": "FCF and net income",
    "BUS-REINV-018": "capex, D&A, NWC, R&D, NOPAT",
    "BUS-SG-019": "reinvestment rate, ROIC",
    "BUS-NRR-020": "cohort revenue bridge",
    "BUS-GRR-021": "cohort bridge",
    "BUS-CHURN-022": "lost and beginning customers",
    "BUS-LTV-023": "ARPU, gross margin, retention/churn",
    "BUS-CAC-024": "acquisition spend, new customers",
    "BUS-LTVCAC-025": "LTV, CAC",
    "BUS-PAYBACK-026": "CAC, ARPU, margin",
    "BUS-GUIDE-027": "actual, guidance midpoint",
    "BUS-DIL-028": "diluted shares",
    "BUS-ALLOC-029": "incremental ROIC, WACC",
    "BUS-SBC-030": "SBC, revenue",
}


def formula_inputs(metric_id: str) -> list[str]:
    """The registered inputs of one formula, as DATA_POLICY.md's lineage
    field wants them: a list, split on the registry's own commas."""
    stated = FORMULA_INPUTS.get(metric_id)
    return [part.strip() for part in stated.split(",")] if stated else []


#: The registry is versioned as a whole, and VERSION.md line 5 states the
#: number: `Formula-registry version: 2026.1`. FORMULA_REGISTRY.md's own
#: formula-result object carries the same string, so this is what a
#: registered formula publishes.
#:
#: Nothing here may move it. FORMULA_REGISTRY.md's change control opens
#: with "A threshold or formula change is a model change, not a copy edit"
#: -- the subject of the increment is the *formula*: its text, sign
#: convention, inputs, or thresholds. Correcting engine code that computed
#: something other than the registered formula is none of those; it makes
#: the engine finally compute 2026.1, and publishing a higher number would
#: tell a reader Victor's model changed when its text is byte-identical.
#: The increment, and the backtest note VERSION.md pairs with it, belong to
#: whoever edits FORMULAS.md.
FORMULA_REGISTRY_VERSION = "2026.1"

#: What an id outside the registry publishes. `core.formulas` already answers
#: an unknown formula_id with "unknown"; an unregistered metric must not
#: borrow the registry's version to look registered.
UNREGISTERED_FORMULA_VERSION = "unknown"

#: Where the engine's reading of a formula was corrected, why, and the test
#: that pins it. These are *implementation* corrections against registry
#: 2026.1 -- the registry did not move -- but change control's reason and
#: before/after cases are worth carrying either way, so a reader who sees a
#: number change between two runs can find out what happened to it.
FORMULA_CHANGELOG: dict[str, tuple[str, str]] = {
    "BUS-MIX-001": (
        "Shares are now checked against the registry's approximately-100% "
        "rule; a partial breakdown is labelled instead of reported as complete.",
        "test_business_formula_caveats.py::test_a_partial_breakdown_is_recognised"),
    "BUS-HHI-004": (
        "Partial disclosure is labelled a lower bound, per the registry caveat.",
        "test_business_formula_caveats.py::test_hhi_over_a_partial_base_understates_concentration"),
    "BUS-HHI-005": (
        "Same lower-bound labelling as BUS-HHI-004 when segments are partial.",
        "test_business_formula_caveats.py::test_a_partial_breakdown_is_recognised"),
    "BUS-STAB-009": (
        "Adds the recession-year drawdown the registry asks for alongside "
        "the standard deviation and range, dated from FRED's NBER series.",
        "test_adapter_matrix.py"),
    "BUS-IC-012": (
        "Runs the registry's mandated reconciliation to the operating view, "
        "and computes an ending-balance figure (labelled) where only one "
        "period exists.",
        "test_business_roic_inputs.py::test_the_two_invested_capital_views_agree_on_consistent_inputs"),
    "BUS-ROIC-013": (
        "An effective tax rate outside [0,1] is reported unavailable rather "
        "than silently clamped, per CALCULATION_CONVENTIONS.md.",
        "test_business_roic_inputs.py::test_an_effective_tax_rate_outside_the_unit_interval_is_not_normalized"),
    "BUS-IROIC-016": (
        "A negative denominator change is NOT_MEANINGFUL, per the registry "
        "caveat; previously only the zero case was refused. 2026.2: the change "
        "is measured over the registered 3y window, and the latest endpoint "
        "takes the resolved tier-1 cash alongside the tier-1 debt instead of "
        "mixing a reconciled debt with a raw provider cash.",
        "test_business_formula_caveats.py::test_a_shrinking_capital_base_is_not_meaningful"),
    "BUS-ALLOC-029": (
        "Incremental ROIC minus WACC inherits BUS-IROIC-016's corrected window "
        "and its tier-1 cash endpoint, so the spread rests on one source.",
        "specialists/test_business.py::test_reconciled_cash_reaches_invested_capital_like_debt"),
    "BUS-REINV-018": (
        "The numerator now carries all three registered terms; working "
        "capital and the capitalized-R&D adjustment were passed as zero. "
        "2026.2: the working-capital term is derived from the two reported "
        "balance sheets, since no packet carries a `changeInWorkingCapital` "
        "line and the term was therefore still zero on every real company — "
        "NVIDIA's non-cash working capital grew 8.15bn against 3.24bn of capex.",
        "specialists/test_business.py::test_the_working_capital_term_is_derived_from_the_balance_sheets"),
    "BUS-SG-019": (
        "Unchanged in form, but inherits BUS-REINV-018's corrected input, "
        "including the 2026.2 balance-sheet working-capital term.",
        "specialists/test_business.py::test_the_working_capital_term_is_derived_from_the_balance_sheets"),
    "BUS-NRR-020": (
        "Applicability is decided affirmatively: the registry gates this on "
        "'Subscription/business-model adapter only', and scope was previously "
        "assumed unless excluded.",
        "specialists/test_business.py::test_a_transactional_model_is_not_charged_for_subscription_metrics"),
    "BUS-SBC-030": (
        "Adds the SBC/FCF read the registry asks for alongside SBC/revenue.",
        "test_adapter_matrix.py"),
    # --- 2026.2 -----------------------------------------------------------
    "BUS-CAGR-006": (
        "Measured over the registry's registered window ('3y and 5y') rather "
        "than the packet's whole history, which became a 10-year CAGR once the "
        "packet carried eleven years; the 5y scores and the 3y is reported "
        "beside it, and the period label is read from the window actually used.",
        "test_business_cagr_window.py::test_a_deeper_packet_does_not_change_the_cagr"),
    "BUS-DIL-028": (
        "Same registered window correction as BUS-CAGR-006 ('3y / 5y'); the "
        "3y read also settles DILUTION_RED_FLAG's 'for three years'.",
        "test_business_cagr_window.py::test_dilution_uses_the_same_window"),
    "BUS-IC-012": (
        "Only-ending balance sheets now yield the END_BALANCE_PROXY figure "
        "CALCULATION_CONVENTIONS.md asks for instead of MISSING, and the latest "
        "period takes the reconciled tier-1 cash as debt already did, so the "
        "two sides of `debt + equity - excess cash` share one source.",
        "specialists/test_business.py::test_a_single_year_company_gets_an_ending_balance_roic"),
    "BUS-ROIC-013": (
        "Computes from an ending-balance invested capital when only the latest "
        "year exists, and carries END_BALANCE_PROXY — the convention labels "
        "'the result', the return ratio, not only the balance-sheet aggregate.",
        "specialists/test_business.py::test_a_single_year_company_gets_an_ending_balance_roic"),
    "BUS-SPREAD-014": (
        "Inherits END_BALANCE_PROXY from an ending-balance ROIC, for the same "
        "reason: it is a return-ratio result.",
        "specialists/test_business.py::test_a_single_year_company_gets_an_ending_balance_roic"),
    "BUS-EVA-015": (
        "Computed on the average invested capital this row registers "
        "('(ROIC - WACC) * Average invested capital') rather than the valuation "
        "engine's beginning-capital base, so it agrees with ROIC; and available "
        "for a single-year history off the ending-balance IC.",
        "specialists/test_business.py::test_eva_is_computed_on_average_invested_capital"),
    "BUS-FCFC-017": (
        "Requires the registered five-year window ('5y rolling', and DATASET.md's "
        "five-year minimum); fewer than five annual points is MISSING rather than "
        "a shorter sum scored under a period label claiming five years.",
        "test_business_cagr_window.py::test_fcf_conversion_is_missing_below_five_years"),
}
for _mid in ("BUS-GRR-021", "BUS-CHURN-022", "BUS-LTV-023", "BUS-CAC-024",
             "BUS-LTVCAC-025", "BUS-PAYBACK-026"):
    FORMULA_CHANGELOG[_mid] = FORMULA_CHANGELOG["BUS-NRR-020"]


def formula_version(metric_id: str) -> str:
    """The registry version a formula publishes.

    The registry is versioned as a whole (VERSION.md line 5), so every id
    in it publishes that one string. An id the registry does not list is
    not a registered formula and says so, the way `core.formulas` answers an
    unknown id -- it must not borrow 2026.1 to look registered.
    """
    return (FORMULA_REGISTRY_VERSION if metric_id in FORMULA_INPUTS
            else UNREGISTERED_FORMULA_VERSION)


# --- reading analyst input safely ------------------------------------------
#
# `Entradas/<TICKER>.json` is written by hand, so any block can arrive the
# wrong shape: a string where a number belongs, a list of nulls, a bare
# value where a mapping is expected. Every one of those raised out of
# `run()` and took the whole specialist with it — nine of ten malformed
# shapes tested returned no output at all, rather than the one metric
# degrading to MISSING. AGENT.md's contract is that an unusable input is
# NOT_SCORABLE, not an exception.
#
# `guidance_history` was hardened once already, for exactly this reason.
# These read the rest the same way.

def _overlay_number(overlay: dict, key: str, warnings: list[str]) -> float | None:
    """A numeric overlay value, or None when it is not one."""
    raw = overlay.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        warnings.append(f"analyst input {key!r} ignored: expected a number, got {raw!r}.")
        return None


def _overlay_mapping(overlay: dict, key: str, warnings: list[str]) -> dict:
    """A mapping overlay block, or an empty one when it is not a mapping."""
    raw = overlay.get(key)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        warnings.append(f"analyst input {key!r} ignored: expected an object, got {type(raw).__name__}.")
        return {}
    return raw


def _overlay_numbers(overlay: dict, key: str, warnings: list[str]) -> list[float]:
    """The numeric members of a list-shaped overlay block.

    Non-numeric members are dropped and named, rather than poisoning a
    sum or a max further down.
    """
    raw = overlay.get(key)
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        warnings.append(f"analyst input {key!r} ignored: expected a list, got {type(raw).__name__}.")
        return []
    out: list[float] = []
    dropped = 0
    for item in raw:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            dropped += 1
    if dropped:
        warnings.append(f"analyst input {key!r}: {dropped} non-numeric entr(ies) dropped.")
    return out



#: `wbj.core.periods.fiscal_year` — one reading, shared with the overlay
#: builder so the company and its peers are placed on the same years. Kept as
#: a module-level name because the call sites and tests use it.
_fiscal_year = _periods.fiscal_year


#: DATA_POLICY.md's example gives one `source_type`, "filing", for a figure
#: read off a statement. Business reads two kinds: the filings the packet
#: carries, and the conditional DATASET.md inputs the analyst transcribes into
#: `Entradas/<TICKER>.json`. The locator already distinguishes them.
_ANALYST_SOURCE_PREFIX = "Entradas/"


def _row_lineage(packet: Packet, annual: list[dict], v: Value) -> dict[str, Any]:
    """The lineage fields a metric row can only get from the run itself.

    `restated` stays None: NORMALIZATION_AND_RESTATEMENTS.md asks for the
    latest restated series, but no packet carries a per-figure restatement
    flag, and writing `false` would assert an original where the truth is
    unknown.
    """
    # The *locator* is what names the analyst file; `source_name` is now the
    # human name of the source, so reading it here mislabelled every
    # analyst-supplied row as a filing.
    locator = v.source_locator or v.source_name or ""
    start, end = _period_dates(annual, v.period)
    return {
        "period_start": start,
        "period_end": end,
        "knowledge_timestamp": packet.analysis.knowledge_timestamp,
        "source_type": ("analyst input" if locator.startswith(_ANALYST_SOURCE_PREFIX)
                        else "filing"),
        "restated": None,
        "currency": packet.security.reporting_currency,
    }


def _period_dates(annual: list[dict], label: str | None) -> tuple[str | None, str | None]:
    """`period_start` / `period_end` for a metric's stated period label.

    DATA_POLICY.md's lineage block wants the period as explicit dates, where
    OUTPUT_CONTRACT.md's row publishes it as a label ("FY2025",
    "FY2021-FY2025"). Both describe the same span, so the dates are read from
    the statement rows the label already names.

    A statement row's `date` is the period *end*, so a label's end is its
    last named year's date and its start is the day after the row preceding
    its first named year. Where the packet carries no earlier row the start
    is left unknown rather than guessed.
    """
    if not label or not annual:
        return None, None
    years = re.findall(r"FY(\d{4})", label)
    if not years:
        return None, None
    by_year = {_fiscal_year(r): r for r in annual}
    first, last = by_year.get(years[0]), by_year.get(years[-1])
    end = str(last.get("date") or "") if last else ""
    end = end if len(end) == 10 else None
    if first is None:
        return None, end
    order = [r for r in annual if _fiscal_year(r) is not None]
    try:
        prior = order[order.index(first) - 1] if order.index(first) > 0 else None
    except ValueError:
        prior = None
    if prior is None:
        return None, end
    prior_end = str(prior.get("date") or "")
    if len(prior_end) != 10:
        return None, end
    try:
        return (_date.fromisoformat(prior_end) + _timedelta(days=1)).isoformat(), end
    except ValueError:
        return None, end


def _fiscal_years(annual: list[dict]) -> list[str]:
    return [y for y in (_fiscal_year(r) for r in annual) if y is not None]


def _period_label(annual: list[dict], window: int = 1) -> str:
    """The fiscal period a metric covers, as OUTPUT_CONTRACT.md's
    `period` field. A point metric carries the latest fiscal year; a
    window metric carries its span, so a reader can tell a one-year
    margin from a five-year range without consulting the formula."""
    years = _fiscal_years(annual)
    if not years:
        return "UNKNOWN"
    if window <= 1:
        return f"FY{years[-1]}"
    span = years[-window:]
    return f"FY{span[0]}-FY{span[-1]}" if len(span) > 1 else f"FY{span[-1]}"


def _filings_source(packet: Packet, annual: list[dict]) -> str:
    """A stable locator for the filings behind a computed metric.

    Names the statement set and pins the latest filing by fiscal year and
    acceptance date, so the row can be traced to the document it came
    from rather than to "the packet".
    """
    ticker = packet.security.ticker.upper()
    if not annual:
        return f"fmp:annual-statements/{ticker}"
    latest = annual[-1]
    year = _fiscal_year(latest) or "?"
    filed = latest.get("filingDate") or latest.get("acceptedDate") or "?"
    return (f"fmp:annual-statements/{ticker}"
            f" (income,balance,cash-flow; latest FY{year} filed {filed})")


def adapter_excess_return_persistence(annual: list[dict], cost_of_equity: float | None
                                      ) -> float | None:
    """Share of the last five years in which ROE beat the cost of equity
    by at least the wide-moat threshold, or None when it cannot be read.

    DECISION_RULES.md's wide-moat condition 1 admits an alternative: "or
    an approved financial-sector adapter shows equivalent excess
    returns". INDUSTRY_ADAPTERS.md says what the adapter substitutes —
    for banks, "Replace ROIC with ROE, ROTCE"; for insurers, "Use ROE
    ... and excess-return valuation" — so the equivalent of the
    ROIC-WACC spread is ROE against the cost of equity.

    "Equivalent" is read as the same standard, not a softer one: the same
    five-percentage-point margin over the same five-year window that the
    first branch requires.
    """
    if cost_of_equity is None:
        return None
    excess = []
    for row in annual:
        net_income, equity = _num(row, "net_income"), _num(row, "total_equity")
        if net_income is None or not equity or equity <= 0:
            continue
        excess.append(net_income / equity - cost_of_equity)
    window = excess[-WIDE_MOAT_PERSISTENCE_WINDOW:]
    if not window:
        return None
    return sum(1 for e in window if e >= WIDE_MOAT_MIN_SPREAD) / len(window)


def peer_resilience_is_top_quartile(own_drawdown: float | None,
                                    peer_drawdowns: list[float]) -> bool:
    """Whether this security's margin held up better through the cycle
    than three peers in four.

    The other alternative in condition 2: "or peer-relative resilience is
    in the top quartile through a cycle". Resilience is measured as the
    fall in operating margin into an NBER recession year — the same
    quantity BUS-STAB-009's caveat asks be reported alongside the
    standard deviation — so a smaller drop ranks better.

    SCORING_ENGINE.md's percentile needs "a minimum of 8 valid peers";
    with fewer, this branch simply is not available.
    """
    if own_drawdown is None or len(peer_drawdowns) < _MIN_PEERS_FOR_PERCENTILE:
        return False
    from wbj.core.scoring import peer_score

    ranked = peer_score(own_drawdown, peer_drawdowns, higher_is_better=False)
    return bool(ranked.is_valid and ranked.value >= _TOP_QUARTILE_SCORE)


def coverage_band_caveat(coverage: float) -> str | None:
    """MISSING_DATA_POLICY.md's coverage band, in words, or None when the
    category is complete.

    That document names three bands — ">=0.85 complete", "0.70 <=
    coverage < 0.85 usable with caveat", "<0.70 incomplete and
    gate-ineligible" — but the handoff `status` literal has only
    COMPLETE / INCOMPLETE / ERROR, so a category at 0.80 read the same as
    one at 0.50: both INCOMPLETE. The middle band's own name is "usable
    *with caveat*", and the caveat is what was missing. Emitting it names
    the band a reader of `status` alone could not see, without touching
    the shared status literal HANDOFF_CONTRACT.md fixes.

    The band boundaries are Victor's: COVERAGE_COMPLETE (0.85) and
    COVERAGE_USABLE (0.70).
    """
    if coverage >= COVERAGE_COMPLETE:
        return None
    if coverage >= COVERAGE_USABLE:
        return (
            f"Coverage {coverage:.2f} is in MISSING_DATA_POLICY.md's "
            f"'usable with caveat' band [{COVERAGE_USABLE:.2f}, {COVERAGE_COMPLETE:.2f}): "
            "the category is usable but not complete. `status` reads INCOMPLETE for "
            "this band as it does for the one below it; this names which."
        )
    if coverage > 0:
        return (
            f"Coverage {coverage:.2f} is below MISSING_DATA_POLICY.md's "
            f"{COVERAGE_USABLE:.2f} floor: 'incomplete and gate-ineligible'. "
            "SCORING_ENGINE.md: an incomplete category may be displayed but cannot "
            "pass a profile gate."
        )
    return None


def _cagr_over_window(annual: list[dict], values: list,
                      window_years: int) -> tuple[float, float, float, str] | None:
    """`(begin, end, years, period)` for a CAGR over the last
    `window_years` fiscal years, or None below two valid points.

    Uses only rows whose value is present, and reads the period from the
    window it actually spans — the same rows, so the declared period
    cannot claim a shorter span than the computation used, which was the
    other half of this defect. A shorter history clamps to what exists,
    as `_annual_rows`-driven windows do elsewhere.
    """
    pairs = [(r, v) for r, v in zip(annual, values) if v is not None]
    if len(pairs) < 2:
        return None
    span = pairs[-(window_years + 1):]      # a k-year change spans k+1 points
    begin_row, begin = span[0]
    end_row, end = span[-1]

    def _fy(row: dict) -> str:
        year = _fiscal_year(row)
        return f"FY{year}" if year else "FY?"

    period = f"{_fy(begin_row)}-{_fy(end_row)}"
    return begin, end, float(len(span) - 1), period


def _stated_analyst_period(overlay: dict) -> str | None:
    """The period the analyst states their snapshot inputs cover, from a
    top-level `"period"` in `Entradas/<TICKER>.json`, or None.

    OUTPUT_CONTRACT.md requires `period` to be explicit, and DATASET.md
    asks for "8 quarters" of retention and cohort history. Those metrics
    are analyst-supplied, so their period belongs to the analyst, not to
    the annual filing whose FY they used to inherit. When the file states
    none, the metric falls back to that FY as before — no worse than the
    prior behaviour, and the period the analyst can now supply overrides
    it.
    """
    period = overlay.get("period")
    return period.strip() if isinstance(period, str) and period.strip() else None


def _guidance_period(guidance_history: list) -> str | None:
    """The period BUS-GUIDE-027 covers, from the periods its own entries
    carry. The metric averages accuracy across the quarters the analyst
    supplied — each already dated in `guidance_history` — so its period is
    their span, needing nothing the file does not already hold.
    """
    periods = sorted(
        g["period"].strip()
        for g in guidance_history
        if isinstance(g, dict) and isinstance(g.get("period"), str) and g["period"].strip()
    )
    if not periods:
        return None
    return periods[0] if len(periods) == 1 else f"{periods[0]}-{periods[-1]}"


def _reported(by_id: dict[str, Any], metric_id: str) -> float | None:
    """One metric's value for an OUTPUT_SCHEMA.md block, or None.

    The blocks report numbers; a row's state and warnings already travel
    in `metrics`, so a null here is simply an absent figure.
    """
    row = by_id.get(metric_id)
    if row is None:
        return None
    return row.value.value if row.value.is_valid else None


#: DATA_POLICY.md's lineage separates what the source *is* (`source_name`,
#: his example "SEC 10-K") from which document it is (`source_locator`, his
#: "filing accession or stable identifier"). Both were collapsed into
#: `source_name`, leaving `source_locator` empty on every row.
_FILINGS_SOURCE_NAME = "FMP annual statements (income, balance, cash-flow)"
_ANALYST_SOURCE_NAME = "Analyst input file"


def _with_provenance(v: Value, *, period: str, source: str) -> Value:
    """Attach period and source to a `Value` without overwriting lineage
    a formula already recorded."""
    updates = {}
    if not getattr(v, "period", None):
        updates["period"] = period
    if not getattr(v, "source_name", None) and not getattr(v, "source_locator", None):
        updates["source_locator"] = source
        updates["source_name"] = (
            _ANALYST_SOURCE_NAME if source.startswith(_ANALYST_SOURCE_PREFIX)
            else _FILINGS_SOURCE_NAME
        )
    return v.model_copy(update=updates) if updates else v



def run(packet: Packet, overlay: dict[str, Any] | None = None) -> BusinessOutput:
    """Run the business specialist against `packet`, producing a
    `BusinessOutput` (max 20 pts): 30 BUS-* metric rows, five weighted
    dimensions, mandatory flags, and OUTPUT_SCHEMA.md's extension fields.

    See the module docstring for `overlay`'s supported keys (`wacc` is the
    load-bearing one: without it, every ROIC/spread/EVA-derived metric and
    the wide-moat gate degrade to MISSING).

    When a reconciled valuation input (total_debt, cash) conflicts between its
    two sources (>5% gap, reconcile's proxy), this orchestrates
    SOURCE_HIERARCHY.md's conflict resolution. Step 5's "mark the metric
    CONFLICTED and do not score it" names the contested *input* — total_debt or
    cash — which the packet already carries as CONFLICTED and business does not
    score as a metric. The *derived* BUS-* metrics stay computable, and the
    default order (Section 1) says which value to compute them on: the tier-1
    (EDGAR) source. So the derived metrics always score, on the EDGAR value,
    with both values kept in the audit trail (step 3) and the selected value
    and reason recorded (step 4). The FMP alternative is scored only to measure
    and disclose the materiality (Section 3): whether the source choice would
    move the category score >=0.5/10 or flip a gate/override. No conflict means
    a single ordinary pass.
    """
    overlay = overlay or {}
    conflicts = {k for k in _RECONCILED_INPUTS if _reconciled_fact(packet, k)[1]}
    if not conflicts:
        return _run_once(packet, overlay)

    # Score on the tier-1 (EDGAR) value; run FMP only to size the materiality.
    edgar = _run_once(packet, overlay, resolution={k: "edgar" for k in conflicts})
    fmp = _run_once(packet, overlay, resolution={k: "fmp" for k in conflicts})
    keys = ", ".join(sorted(conflicts))
    delta = abs((edgar.category.score_10 or 0.0) - (fmp.category.score_10 or 0.0))
    if _conflict_is_material(edgar, fmp):
        note = (
            f"SOURCE_HIERARCHY.md conflict on {keys} is MATERIAL: the FMP value would "
            f"move the category score {delta:.2f}/10 or flip a gate/override. Scored on "
            f"the tier-1 (EDGAR) value per the default order, with both values kept in "
            f"the audit trail; the contest is priced into confidence, and any mandatory "
            f"red flag the tier-1 value raises (e.g. VALUE_DESTRUCTION) stands."
        )
    else:
        note = (
            f"SOURCE_HIERARCHY.md conflict on {keys} is immaterial: the FMP value would "
            f"move the category score only {delta:.2f}/10 (< 0.5) and flip no "
            f"gate/override. Scored on the tier-1 (EDGAR) value, both values in the "
            f"audit trail."
        )
    return edgar.model_copy(update={"assumptions": [*edgar.assumptions, note]})


def _run_once(packet: Packet, overlay: dict[str, Any],
              resolution: dict[str, str] | None = None) -> BusinessOutput:
    """One scoring pass under a fixed conflict `resolution` (see `run()`)."""
    computed, assumptions, judgment_requests, ctx = _compute_all(packet, overlay, resolution)
    by_id = {r.metric_id: r for r in computed}

    if packet.analysis.industry_adapter != "default_nonfinancial":
        assumptions.append(
            f"industry_adapter={packet.analysis.industry_adapter!r}: this module implements only "
            "conventional (non-financial-sector) formulas; ROIC/spread/EVA/moat results below should "
            "not be trusted for this security type without a sector adapter."
        )

    rows: list[MetricRow] = []
    for r in computed:
        score: float | str = r.score10 if r.score10 is not None else "NOT_SCORABLE"
        rows.append(
            MetricRow.from_value(
                r.metric_id, r.value, formula_id=r.metric_id,
                formula_version=formula_version(r.metric_id),
                score=score, confidence=r.confidence,
                calculation_inputs=formula_inputs(r.metric_id),
                # DATA_POLICY.md's remaining lineage fields. `as_of`,
                # `source_name` and `source_locator` ride on the `Value`
                # itself; these are the ones only the run knows.
                **_row_lineage(packet, _annual_rows(packet), r.value),
            )
        )

    # ---- MOAT (5 pts): spread level, spread persistence, margin range, quantitative-effects (judgment) ----
    spread_years = ctx["spread_hist"]
    # DECISION_RULES.md's wide-moat condition 1 is "at least four of the
    # last five fiscal years", so the denominator is the last five —
    # not however many years the packet happens to carry. This divided
    # over the whole history, which was harmless while the packet held
    # six years and silently wrong the moment it held eleven: a company
    # with five straight qualifying years scored 5/10 instead of 5/5.
    recent_spreads = spread_years[-WIDE_MOAT_PERSISTENCE_WINDOW:]
    persistence_frac = (
        sum(1 for s in recent_spreads if s >= WIDE_MOAT_MIN_SPREAD) / len(recent_spreads)
        if recent_spreads else None
    )
    v_persistence = (
        _ok(persistence_frac, unit="ratio") if persistence_frac is not None
        else _null(NullState.MISSING, "ratio", "SPREAD_PERSISTENCE_INSUFFICIENT_HISTORY")
    )
    # SCORING.md's moat row separates two columns this dimension had
    # merged. Its "Primary inputs" are "ROIC-WACC *persistence*, margin
    # stability, pricing/retention evidence" — three. Its "Gate / cap"
    # column is "Score capped at 6 without positive ROIC-WACC *spread*".
    # Persistence is the input; the spread level is the gate.
    #
    # This carried four slots: the spread level, the margin range,
    # persistence, and a judgment slot. So the spread level scored a
    # quarter of the dimension as an input he does not list, while also
    # driving the cap he does — counted twice — and the judgment slot
    # held another quarter for something absent from his input column.
    # Between them, half the dimension rested on things his own row
    # assigns elsewhere or not at all.
    #
    # "pricing/retention evidence" had no slot at all. DATASET.md splits
    # the evidence: `competitive_evidence` (patents, network scale,
    # switching costs) is marked "context only" and cannot score, while
    # `retention_churn_cohorts` is conditional and is measured by three
    # registered formulas. So the scorable half is retention, and its
    # absence is exactly what his 0-3 band describes: "no measurable
    # retention/pricing evidence".
    #
    # Equal weights, as everywhere else: he lists the three without rank
    # and registers no weight.
    retention_evidence = [
        _slot(by_id[mid]) for mid in ("BUS-NRR-020", "BUS-GRR-021", "BUS-CHURN-022")
    ]
    scored_retention = [v.value for v in retention_evidence if not v.is_null]
    if scored_retention:
        v_retention = _ok(sum(scored_retention) / len(scored_retention), unit="score")
    elif all(v.is_null and v.state == NullState.NOT_APPLICABLE for v in retention_evidence):
        # A business with no repeat-customer economics has no retention
        # evidence to measure. MISSING_DATA_POLICY.md step 1 takes the
        # dimension's other inputs rather than charging it for one its
        # model does not produce.
        v_retention = Value.null(NullState.NOT_APPLICABLE, unit="score",
                                 warnings=["RETENTION_EVIDENCE_NOT_A_REPEAT_CUSTOMER_MODEL"])
    else:
        v_retention = Value.null(NullState.NOT_SCORABLE, unit="score",
                                 warnings=["RETENTION_EVIDENCE_UNAVAILABLE"])

    moat_scores: list[tuple[float, Value]] = [
        # ROIC-WACC persistence
        (1 / 3, Value.of(anchor_score(persistence_frac,
                                      [(0.0, 0), (0.5, 5), (0.8, 8), (1.0, 10)]),
                         unit="score")
         if v_persistence.is_valid else Value.null(NullState.NOT_SCORABLE, unit="score")),
        # margin stability — measured by the range, which is the reading
        # his own documents tie to the moat. BUS-RANGE-010's caveat is "A
        # range <=3 percentage points is a positive moat signal in the
        # source framework", and DECISION_RULES.md's wide-moat gate
        # condition 2 is "Five-year operating-margin range is no more
        # than 5 percentage points". BUS-STAB-009 carries the input's
        # name but no threshold at all — only "Lower is better" — so
        # neither of SCORING_ENGINE.md's scoring paths is open to it and
        # it could not carry this slot even if it were meant to.
        (1 / 3, _slot(by_id["BUS-RANGE-010"])),
        # pricing/retention evidence — the retention half, which his
        # registered formulas measure; pricing stays context only.
        (1 / 3, v_retention),
    ]
    positive_spread = ctx["spread_latest"] is not None and ctx["spread_latest"] > 0
    # SCORING.md's cap for this row, in full: "Score capped at 6 without
    # positive ROIC-WACC spread *or a valid adapter*." The alternative was
    # missing, so a bank was capped on a ROIC-WACC spread that
    # INDUSTRY_ADAPTERS.md tells this engine not to use for it — "Replace
    # ROIC with ROE, ROTCE". A model-replacing adapter is what "a valid
    # adapter" means here: for SaaS or commodities the conventional
    # formulas still apply, so their spread is the right measure and the
    # cap stands.
    valid_adapter = _adapters.replaces_model(packet.analysis.industry_adapter)
    if not positive_spread and not valid_adapter:
        moat_scores = apply_dimension_cap(moat_scores, cap=6.0)
    elif not positive_spread and valid_adapter:
        assumptions.append(
            "SCORING.md's moat cap lifted through its alternative: the "
            f"{packet.analysis.industry_adapter} adapter replaces ROIC, so a negative "
            "ROIC-WACC spread is not the measure this row caps on."
        )
    moat_dim = Dimension(name=DIM_MOAT, max_points=DIMENSION_MAX_POINTS[DIM_MOAT], metric_scores=moat_scores)

    judgment_requests.append(
        JudgmentRequest(
            request_id="business_analysis:moat_classification",
            agent_id=AGENT_ID,
            metric_id="moat_classification",
            question="Classify the moat (Wide/Narrow/None) per DECISION_RULES.md's wide-moat gate: "
            "spread persistence, margin stability, and no unresolved concentration are computed "
            "mechanically above, but the final label requires qualitative review.",
            schema_hint="one of Wide|Narrow|None",
        )
    )
    # La concentracion de clientes es de las cinco que el `judge.py` de Victor
    # nombra como suyas -- "moat classification, catalyst probability, thesis
    # killers, TAM tier, customer concentration" -- pero su `business.py` no
    # emitia la peticion, asi que en su motor la metrica quedaba MISSING para
    # todos, siempre. Aqui estaba igual: `_FILING_TOPICS` ya recopilaba los
    # pasajes del filing donde se divulga, y nadie hacia la pregunta
    # (`judge.py`: `if not requests: return []`).
    #
    # Se pregunta por BANDA y no por porcentaje porque `merge_overlay` usa una
    # respuesta numerica DIRECTAMENTE como puntaje 0-10: un "40%" se habria
    # convertido en un 10, la mejor nota para la peor concentracion.
    #
    # Y se pregunta solo cuando no hay cifra: si un analista la escribio en
    # `Entradas/`, esa gana y gastar una pregunta seria tirar cuota.
    #  lo deja el bloque de BUS-CONC-003 con el
    # valor cuando puntuo y con None cuando no: es el mismo hecho, y viaja
    # entre funciones donde la variable local no llega.
    if ctx.get("largest_customer_share") is None:
        judgment_requests.append(
            JudgmentRequest(
                request_id="business_analysis:customer_concentration_band",
                agent_id=AGENT_ID,
                metric_id="BUS-CONC-003",
                question="Del 10-K adjunto, clasifica la concentracion de clientes "
                "segun las bandas de DECISION_RULES.md. Un filing que declara que "
                "NINGUN cliente alcanza el umbral de divulgacion (tipicamente 10% "
                "de los ingresos) es BELOW_10: eso es un hallazgo, no un dato "
                "ausente. Responde INSUFFICIENT solo si el filing no dice nada del "
                "asunto.",
                schema_hint="one of ABOVE_30|BETWEEN_10_AND_30|BELOW_10",
            )
        )

    judgment_requests.append(
        JudgmentRequest(
            request_id="business_analysis:moat_quantitative_effects_count",
            agent_id=AGENT_ID,
            metric_id="moat_quantitative_effects_count",
            question="Identify which of the five moat-effect categories (retention/switching costs, "
            "cost advantage, network scale, regulated/intangible protection, efficient scale) are "
            "quantitatively visible in the evidence; the wide-moat gate requires >=2.",
            schema_hint="array of 0-5 strings from the moat-effect category list",
        )
    )

    # ---- COMPETITIVE (4 pts): revenue-CAGR proxy for share trend + peer ROIC percentile ----
    # SCORING.md caps this at 8 *if* market definition is low confidence.
    # The cap used to be unconditional because, as the old comment put it,
    # no market-definition confidence was available from the packet. It is
    # now: an analyst-supplied TAM carries `tam_source_tier`, and
    # DECISION_RULES.md's tier table turns that into a confidence the same
    # way market.py does. A sourced tier 1-3 is not low confidence, so the
    # cap lifts; anything else, including no TAM at all, keeps it.
    revenue_cagr_score = _score_from_anchor(
        by_id["BUS-CAGR-006"].value, [(-0.05, 0), (0.0, 3), (0.10, 7), (0.20, 10)]
    )
    # FORMULAS.md's execution rules: "Record any proxy in `warnings` and
    # reduce model-fit confidence." Both proxies below were documented in
    # comments only, so nothing downstream — the aggregator, the report,
    # the reader — could tell a substitute stood in for the real input.
    # The rule has two halves and only the first was being honoured. The
    # declarations below went into `assumptions` and each ended with
    # "Model-fit confidence reduced accordingly" -- but nothing reduced
    # it. `_category_confidence` read the industry adapter and nothing
    # else, so a score resting on two substituted inputs reported the
    # same confidence as one resting on the real ones. The proxies are
    # now counted, carried into the metric warnings, and priced into
    # model fit.
    proxies_in_use: list[str] = []

    # A contested reconciled input is a declared uncertainty like any
    # other, so it is priced the same way. `_compute_all` detects it;
    # this is where the confidence cost is applied.
    if ctx.get("conflicted_inputs"):
        proxies_in_use.append(PROXY_CONFLICTED_SOURCE_INPUT)

    # BUS-IC-012 is "Debt + Equity - Excess cash", and no filing reports
    # how much of a cash balance operations require, so this module
    # subtracts the whole balance. On a cash-rich company that is not a
    # rounding difference: it moves NVDA's latest ROIC by roughly seven
    # percentage points. The number is kept -- inventing an operating-cash
    # threshold Victor never wrote would be worse -- but it is a proxy and
    # is now priced and disclosed as one, with the operating-view
    # reconciliation above as the check on it.
    proxies_in_use.append(PROXY_TOTAL_CASH_AS_EXCESS_CASH)
    assumptions.append(
        "BUS-IC-012: total cash is subtracted as excess cash; no filing discloses the "
        "operating cash requirement. FORMULAS.md's reconciliation to operating assets "
        "minus operating liabilities is run on both endpoints and warns above 5% "
        f"divergence. Model-fit confidence reduced by {MODEL_FIT_PENALTY_PER_PROXY:.0f} points."
    )

    substituted_tax_years = ctx.get("substituted_tax_years") or 0
    if substituted_tax_years:
        proxies_in_use.append(PROXY_STATUTORY_TAX_RATE)
        assumptions.append(
            f"BUS-ROIC-013: {substituted_tax_years} year(s) in the ROIC history use the 21% "
            "statutory rate because the filing reports no positive pretax income to derive a "
            "cash tax rate from. The wide-moat gate's spread-persistence check reads this "
            f"history. Model-fit confidence reduced by {MODEL_FIT_PENALTY_PER_PROXY:.0f} points."
        )

    if revenue_cagr_score is not None:
        proxies_in_use.append(PROXY_REVENUE_CAGR_FOR_SHARE)
        # `metrics` is the output's warnings channel, and BUS-CAGR-006 is
        # a registered formula being used as a stand-in for one that has
        # no data — so the warning belongs on its row, where a reader
        # looking at the number will see it. (The binary capital-return
        # read has no formula ID: it is a synthetic dimension slot, so
        # its disclosure stays in `assumptions`, which is what AGENT.md
        # asks of an "explicitly disclosed assumption".)
        for row in rows:
            if row.metric_id == "BUS-CAGR-006":
                row.warnings.append(PROXY_REVENUE_CAGR_FOR_SHARE)
        assumptions.append(
            "competitive_position: revenue CAGR (BUS-CAGR-006) substitutes for the "
            "market-share trend SCORING.md asks for. A company growing slower than "
            "its market is losing share, which this proxy reads as growth. Model-fit "
            f"confidence reduced by {MODEL_FIT_PENALTY_PER_PROXY:.0f} points."
        )
    if ctx.get("returns_cash") is not None:
        proxies_in_use.append(PROXY_BINARY_CAPITAL_RETURN)
        assumptions.append(
            "management_and_capital_allocation: capital return is a binary read of "
            "whether buybacks/dividends are funded from surplus, standing in for "
            "measured allocation quality. Model-fit confidence reduced by "
            f"{MODEL_FIT_PENALTY_PER_PROXY:.0f} points."
        )
    # An analyst block that was written but could not be read has to be
    # visible in the report, not just the log. Otherwise the file looks
    # filled in, the dimension scores zero, and nothing connects the two.
    assumptions.extend(overlay.get("analyst_input_warnings") or [])

    # SCORING.md's competitive-position row: "Market-share trend, peer
    # ROIC/margins, rank".
    #
    # "peer ROIC/margins" is one input carrying a slash, the same shape as
    # durability's "margin/ROIC persistence" — one comparison expressed
    # two ways. Only ROIC was compared; the margin half was never fetched,
    # though it comes from the same peer income statement the ROIC fetch
    # already reads.
    peer_roic_list = _overlay_numbers(overlay, "peer_roic", assumptions)
    peer_margin_list = _overlay_numbers(overlay, "peer_operating_margin", assumptions)

    from wbj.core.scoring import peer_score

    peer_reads: list[Value] = []
    if ctx["roic_latest"] is not None and peer_roic_list:
        v = peer_score(ctx["roic_latest"], peer_roic_list)
        if v.is_valid:
            peer_reads.append(v)
    # `by_id` holds rows, whose `.value` is a `Value`; the percentile
    # needs the number inside it.
    own_margin_row = by_id["BUS-OM-008"].value
    if own_margin_row.is_valid and peer_margin_list:
        v = peer_score(own_margin_row.value, peer_margin_list)
        if v.is_valid:
            peer_reads.append(v)

    if peer_reads:
        # Equal weight between the two halves: he names them together and
        # ranks neither.
        peer_v = _ok(sum(r.value for r in peer_reads) / len(peer_reads), unit="score")
    else:
        peer_v = Value.null(NullState.NOT_SCORABLE, unit="score",
                            warnings=["PEER_COMPARISON_UNAVAILABLE"])

    competitive_scores: list[tuple[float, Value]] = [
        # Market-share trend — the registered proxy, per
        # MISSING_DATA_POLICY.md step 4.
        (0.5, Value.of(revenue_cagr_score, unit="score",
                       warnings=[PROXY_REVENUE_CAGR_FOR_SHARE])
         if revenue_cagr_score is not None else Value.null(NullState.NOT_SCORABLE, unit="score")),
        # peer ROIC/margins
        (0.5, peer_v),
    ]
    if not _market_definition_is_confident(overlay):
        competitive_scores = apply_dimension_cap(competitive_scores, cap=8.0)
    competitive_dim = Dimension(name=DIM_COMPETITIVE, max_points=DIMENSION_MAX_POINTS[DIM_COMPETITIVE], metric_scores=competitive_scores)

    # ---- MANAGEMENT (4 pts): incremental ROIC vs WACC, dilution, capital return, guidance ----
    returns_cash = ctx.get("returns_cash")
    capital_return_value = (
        Value.of(10.0 if returns_cash else 5.0, unit="score",
                 warnings=[PROXY_BINARY_CAPITAL_RETURN])
        if returns_cash is not None
        else Value.null(NullState.NOT_SCORABLE, unit="score")
    )
    # SCORING.md's "Primary inputs" for this dimension are, in his order:
    # "Incremental ROIC, allocation history, dilution, guidance accuracy".
    # Four inputs, listed without rank and with no weight registered
    # anywhere in Cerebro — `grep -c weight SCORING.md` returns zero.
    #
    # This carried 0.35 / 0.30 / 0.20 / 0.15, numbers that appear in none
    # of his eighty-three documents. Enumerating inputs without ranking
    # them supports one reading and only one: they weigh the same. Any
    # other split is this engine deciding that guidance accuracy matters
    # less than half as much as incremental ROIC, which is a
    # methodological judgement that belongs to Victor, not here.
    #
    # If he does want them ranked, SCORING_ENGINE.md says where it goes:
    # "Weights must be registered in that metric's scoring file."
    management_scores: list[tuple[float, Value]] = [
        (0.25, _slot(by_id["BUS-ALLOC-029"])),   # Incremental ROIC
        (0.25, capital_return_value),            # allocation history
        (0.25, _slot(by_id["BUS-DIL-028"])),     # dilution
        (0.25, _slot(by_id["BUS-GUIDE-027"])),   # guidance accuracy
    ]
    # SCORING.md's gate for this row — "Qualitative reputation alone
    # cannot exceed 5" — still needs no explicit cap, and holds more
    # firmly at equal weights than it did before. `capital_return_value`
    # is the one slot not backed by a registered formula, and at 0.25 it
    # cannot reach the 70% floor by itself: alone the dimension is
    # NOT_SCORABLE, so qualitative reputation alone scores nothing at
    # all, let alone more than 5. Every scorable configuration carries at
    # least three of the four inputs.
    management_dim = Dimension(name=DIM_MANAGEMENT, max_points=DIMENSION_MAX_POINTS[DIM_MANAGEMENT], metric_scores=management_scores)

    # ---- DURABILITY (4 pts): recurring revenue, concentration, margin persistence ----
    largest_customer_share = ctx.get("largest_customer_share")
    concentration_flag = largest_customer_share is not None and is_concentration_red_flag(largest_customer_share)
    # SCORING.md's durability row: "Recurring revenue, concentration,
    # margin/ROIC persistence, cyclicality".
    #
    # "margin/ROIC persistence" carries a slash — one input measured two
    # ways, the same shape as competitive position's "peer ROIC/margins".
    # It held two slots, so that single input carried half the dimension
    # while "Recurring revenue" and "concentration" carried a quarter
    # each. The two halves now share one slot and weigh equally inside it.
    #
    # Cyclicality has no slot and stays that way. His 0-3 band names it —
    # "fragile cycle economics" — but no formula in the thirty-metric
    # registry measures it and no threshold is stated anywhere, so it
    # meets neither of SCORING_ENGINE.md's scoring paths. Adding a slot
    # that can never score would repeat the mistake the moat dimension
    # carried: weight held for something unscoreable, crowding the
    # inputs that do work.
    persistence_score = (
        Value.of(anchor_score(persistence_frac, [(0.0, 0), (0.5, 5), (0.8, 8), (1.0, 10)]),
                 unit="score")
        if v_persistence.is_valid else Value.null(NullState.NOT_SCORABLE, unit="score")
    )
    margin_persistence = _slot(by_id["BUS-RANGE-010"])
    persistence_reads = [v.value for v in (margin_persistence, persistence_score)
                         if not v.is_null]
    v_margin_roic_persistence = (
        _ok(sum(persistence_reads) / len(persistence_reads), unit="score")
        if persistence_reads
        else Value.null(NullState.NOT_SCORABLE, unit="score",
                        warnings=["MARGIN_ROIC_PERSISTENCE_UNAVAILABLE"])
    )

    durability_scores: list[tuple[float, Value]] = [
        (1 / 3, _slot(by_id["BUS-REC-002"])),        # Recurring revenue
        (1 / 3, _slot(by_id["BUS-CONC-003"])),       # concentration
        (1 / 3, v_margin_roic_persistence),          # margin/ROIC persistence
    ]
    # SCORING.md's durability cap, in full: "Largest customer >30% caps at
    # 6 *unless contract protection is quantified*." The exception had no
    # input plumbed, so the cap was unconditional. Contract protection —
    # the contractual mitigant to a concentrated customer (long-term
    # supply, minimum-volume/take-or-pay commitments, switching barriers,
    # which DATASET.md's `competitive_evidence` lists) — is a conditional
    # input with no dedicated numeric field, so it arrives through the
    # overlay like every other conditional DATASET input and is disclosed.
    # Victor conditions on *quantification*, not on a sufficiency magnitude
    # he never states, so any positive quantified protection meets the
    # exception and lifts the cap; the disclosure marks that his text gives
    # no threshold, should he want one.
    contract_protection = _overlay_number(overlay, "contract_protection", assumptions)
    contract_protection_quantified = contract_protection is not None and contract_protection > 0
    if concentration_flag and not contract_protection_quantified:
        durability_scores = apply_dimension_cap(durability_scores, cap=6.0)
    elif concentration_flag and contract_protection_quantified:
        assumptions.append(
            "SCORING.md durability concentration cap lifted through its exception: "
            f"contract protection is quantified ({contract_protection!r}, analyst-supplied). "
            "Victor states no sufficiency magnitude, so any positive quantified protection "
            "meets 'unless contract protection is quantified'."
        )
    durability_dim = Dimension(name=DIM_DURABILITY, max_points=DIMENSION_MAX_POINTS[DIM_DURABILITY], metric_scores=durability_scores)

    # ---- CUSTOMER ECONOMICS (3 pts): NRR/GRR, churn, LTV/CAC + payback ----
    # SCORING.md's "Primary inputs" for this row, in his order: "NRR,
    # GRR, churn, LTV/CAC, payback or repeat-purchase proxies". Five
    # inputs, and only three were scored — GRR and CAC payback were
    # computed, given a 0-10 score, and then dropped: neither appeared in
    # any dimension, so their scores went nowhere.
    #
    # Payback is not a minor omission. His 7-10 band reads "NRR/retention
    # and payback show compounding customer value", so the metric his top
    # band names was absent from the dimension it decides.
    #
    # Weights are equal for the same reason as management: he enumerates
    # the five without ranking them, and no weight is registered anywhere
    # in Cerebro. This carried 0.4 / 0.3 / 0.3, numbers from none of his
    # documents. If he ranks them, SCORING_ENGINE.md says where it goes:
    # "Weights must be registered in that metric's scoring file."
    customer_scores: list[tuple[float, Value]] = [
        (0.2, _slot(by_id["BUS-NRR-020"])),       # NRR
        (0.2, _slot(by_id["BUS-GRR-021"])),       # GRR
        (0.2, _slot(by_id["BUS-CHURN-022"])),     # churn
        (0.2, _slot(by_id["BUS-LTVCAC-025"])),    # LTV/CAC
        (0.2, _slot(by_id["BUS-PAYBACK-026"])),   # payback
    ]
    customer_dim = Dimension(name=DIM_CUSTOMER, max_points=DIMENSION_MAX_POINTS[DIM_CUSTOMER], metric_scores=customer_scores)

    dimensions = [moat_dim, competitive_dim, management_dim, durability_dim, customer_dim]

    # The unregistered inputs (`rank`, `cyclicality`) and the equal-weighting
    # basis were documented in code only; AGENT.md's audit trail wants them
    # in the output.
    assumptions.extend(UNREGISTERED_METHODOLOGY_DISCLOSURES)

    cat = Category(name=AGENT_ID, max_points=MAX_POINTS, dimensions=dimensions)
    awarded_points = cat.points()
    dim_score10 = cat.score10()
    coverage = cat.coverage()

    band_caveat = coverage_band_caveat(coverage)
    if band_caveat is not None:
        assumptions.append(band_caveat)

    mandatory_flags: list[str] = []

    # VALUE_DESTRUCTION is a ROIC-below-WACC verdict, and INDUSTRY_ADAPTERS.md
    # tells this engine not to measure a bank, insurer, REIT or biotech that
    # way at all ("Replace ROIC with ROE, ROTCE"; "Replace EPS with
    # FFO/AFFO"). The moat cap a few lines above already lifts on exactly this
    # condition for exactly this reason; the flag has to follow, because the
    # main agent reads it as mandatory override 2 and bars Elite/Quality on
    # the strength of it. Left unguarded, every bank and REIT was permanently
    # barred from a Quality classification by a number the methodology says
    # not to compute for them -- JPM and O both flagged it on live data.
    value_destruction_adapter = _adapters.replaces_model(packet.analysis.industry_adapter)
    value_destruction = (
        not value_destruction_adapter
        and value_destruction_triggered(ctx["roic_latest"], ctx["wacc_value"])
    )
    if value_destruction:
        mandatory_flags.append("VALUE_DESTRUCTION")
    elif value_destruction_adapter and value_destruction_triggered(
        ctx["roic_latest"], ctx["wacc_value"]
    ):
        assumptions.append(
            f"VALUE_DESTRUCTION withheld: the {packet.analysis.industry_adapter} adapter "
            "replaces ROIC (INDUSTRY_ADAPTERS.md), so a conventional ROIC-below-WACC read is "
            "not the measure this flag rests on for this security type. The sector-adapter "
            "return (ROE/ROTCE, FFO/AFFO) is not registered in FORMULAS.md, so no substitute "
            "verdict is issued."
        )

    if concentration_flag:
        mandatory_flags.append("CONCENTRATION_RED_FLAG")

    # The list is empty until the judgment layer fills it, so this is
    # raised here and cleared afterwards by `_recompute_business_flags`.
    # An output that never reaches that layer keeps the flag, which is
    # the honest reading: his rule is unmet.
    if not thesis_killers_listed(None):
        mandatory_flags.append(THESIS_KILLERS_NOT_LISTED)

    # DECISION_RULES.md: "DILUTION_RED_FLAG when diluted shares grow >5%
    # CAGR *for three years* *without commensurate per-share value
    # creation*." Three conditions. The >5% and the three-year window are
    # mechanical and settled here on the 3-year CAGR. The third —
    # "without commensurate per-share value creation" — has no registered
    # formula (FORMULAS.md's only value-creation caveat, BUS-SPREAD-014,
    # is enterprise-level, not per-share), so it is a judgment, requested
    # below and settled in `_recompute_business_flags`.
    #
    # The flag is a warning, so its unanswered posture is the opposite of
    # the wide-moat gate's: an unconfirmed dilution fires it, and an
    # analyst confirming that per-share value creation kept pace clears
    # it — the conservative reading for a red flag.
    diluted_cagr_3y = ctx.get("diluted_cagr_3y")
    diluted_cagr_3y_years = ctx.get("diluted_cagr_3y_years")
    # "for three years" (DECISION_RULES.md) is a duration condition, not
    # only a magnitude: the flag fires only when the window actually spans
    # three years. A shorter history cannot establish the sustained trend
    # Victor names, so the flag abstains rather than firing on a one- or
    # two-year dilution such as a single stock-funded acquisition.
    dilution_flag = (
        diluted_cagr_3y is not None
        and diluted_cagr_3y_years is not None
        and diluted_cagr_3y_years >= 3
        and is_dilution_red_flag(diluted_cagr_3y)
    )
    if dilution_flag:
        mandatory_flags.append("DILUTION_RED_FLAG")
        judgment_requests.append(
            JudgmentRequest(
                request_id="business_analysis:dilution_commensurate_value",
                agent_id=AGENT_ID,
                metric_id="dilution_commensurate_value",
                question="Diluted shares grew more than 5% CAGR over three years "
                f"({diluted_cagr_3y:.1%}). Did per-share value creation keep pace — "
                "is the dilution matched by commensurate per-share value creation? "
                "Answering 'yes' clears DILUTION_RED_FLAG (DECISION_RULES.md's third "
                "condition); 'no' or no answer leaves it standing.",
                schema_hint="one of yes|no",
            )
        )

    # ---- Excellent-band additional condition ----
    # DECISION_RULES.md's wide-moat gate has four conditions. Three are
    # mechanical and computed here. The fourth -- "At least two
    # independent moat effects are quantitatively visible" -- cannot be
    # derived from the packet, because it asks *which* category each
    # piece of evidence belongs to. It is the judgment layer's call, and
    # it arrives as `moat.classification`.
    #
    # Leaving it out of the gate meant treating an unanswered condition
    # as satisfied, which is precisely what AGENT.md's no-speculation
    # rule and MISSING_DATA_POLICY.md's "missing evidence is never
    # neutral" forbid: a company could be labelled Excellent without the
    # two moat effects ever having been seen. So the gate now requires a
    # judged `Wide` classification, and an unanswered one fails it.
    # DECISION_RULES.md's wide-moat gate, condition by condition. Two of
    # the four are written with an alternative, and neither alternative
    # was implemented — so a company that failed the first half of either
    # was refused a Wide-moat label his own rule would have allowed.
    #
    #   1. "ROIC exceeds WACC by at least 5 percentage points in at least
    #      four of the last five fiscal years, or an approved
    #      financial-sector adapter shows equivalent excess returns."
    #   2. "Five-year operating-margin range is no more than 5 percentage
    #      points, or peer-relative resilience is in the top quartile
    #      through a cycle."
    #   3. two independent moat effects — the judgment, settled in
    #      `MoatGateInputs.excellent_gate_passes`.
    #   4. "No unresolved customer/product concentration threat."
    spread_persistence_ok = (
        persistence_frac is not None and persistence_frac >= WIDE_MOAT_MIN_PERSISTENCE
    )
    adapter_persistence = (
        # `_annual_rows` rather than a local: the statement rows live in
        # `_compute_all`, and reaching for a name that is not in scope
        # here raised only for a financial-sector adapter, because every
        # other security short-circuits before the call.
        adapter_excess_return_persistence(_annual_rows(packet),
                                          overlay.get("cost_of_equity"))
        if _adapters.replaces_model(packet.analysis.industry_adapter) else None
    )
    adapter_excess_ok = (
        adapter_persistence is not None
        and adapter_persistence >= WIDE_MOAT_MIN_PERSISTENCE
    )
    condition_1 = spread_persistence_ok or adapter_excess_ok

    margin_range_ok = (
        ctx.get("margin_range") is not None and wide_moat_margin_range_ok(ctx["margin_range"])
    )
    peer_resilience_ok = peer_resilience_is_top_quartile(
        ctx.get("recession_margin_drawdown"),
        _overlay_numbers(overlay, "peer_recession_drawdown", assumptions),
    )
    condition_2 = margin_range_ok or peer_resilience_ok

    condition_4 = not concentration_flag

    # `positive_spread` is not one of the four. It appears twice in
    # Cerebro and neither place is this gate: DECISION_RULES.md's verdict
    # table names it in the Excellent row's additional condition — where
    # `MoatGateInputs.positive_spread` already carries it — and
    # SCORING.md's moat row uses it for the dimension cap below. Held
    # here as well, it charged the same fact a third time and denied a
    # Wide-moat label to a company meeting all four conditions whose
    # most recent year alone had turned negative. Condition 1 is about
    # persistence across five years, not the latest one.
    moat_gate_mechanical = condition_1 and condition_2 and condition_4
    if adapter_excess_ok and not spread_persistence_ok:
        assumptions.append(
            "DECISION_RULES.md wide-moat condition 1 met through its alternative: an "
            f"approved financial-sector adapter ({packet.analysis.industry_adapter}) shows "
            f"ROE above the cost of equity by at least "
            f"{WIDE_MOAT_MIN_SPREAD:.0%} in {adapter_persistence:.0%} of the last "
            f"{WIDE_MOAT_PERSISTENCE_WINDOW} years."
        )
    if peer_resilience_ok and not margin_range_ok:
        assumptions.append(
            "DECISION_RULES.md wide-moat condition 2 met through its alternative: "
            "peer-relative margin resilience through the last NBER recession ranks in "
            "the top quartile."
        )
    moat_gate_inputs = MoatGateInputs(
        mechanical_conditions_pass=moat_gate_mechanical,
        roic_at_least_20pct=ctx["roic_latest"] is not None and ctx["roic_latest"] >= 0.20,
        positive_spread=positive_spread,
        fcf_conversion_at_least_0_9=(
            ctx.get("fcf_conversion") is not None and ctx["fcf_conversion"] >= 0.9),
    )

    label = capped_verdict(
        dim_score10,
        value_destruction=value_destruction,
        # No moat effects are judged at run() time, so condition 3 is
        # unmet here; `_recompute_business_verdict` re-derives the label
        # once the judgment lands and fills `moat.quantitative_evidence`.
        excellent_gate_passes=moat_gate_inputs.excellent_gate_passes(0),
    )

    judgment_requests.append(
        JudgmentRequest(
            request_id="business_analysis:three_thesis_killers",
            agent_id=AGENT_ID,
            metric_id="three_thesis_killers",
            question="List three business-model or execution risks that could destroy the thesis "
            "(DECISION_RULES.md: mandatory, always required).",
            schema_hint="array of exactly 3 strings",
        )
    )

    # OUTPUT_SCHEMA.md requires `business_in_one_sentence`, and nothing
    # produced it — the field was hardcoded to None, so a required output
    # could never be populated. It is a qualitative read, so it belongs in
    # the judgment layer alongside the moat classification.
    judgment_requests.append(
        JudgmentRequest(
            request_id="business_analysis:business_in_one_sentence",
            agent_id=AGENT_ID,
            metric_id="business_in_one_sentence",
            question="Describe what this business sells and how it makes money, in one "
            "sentence, using only what the packet and filings state.",
            schema_hint="string",
        )
    )

    category = CategoryStats(
        max_points=MAX_POINTS, awarded_points=awarded_points, score_10=dim_score10,
        confidence=_category_confidence(
            coverage, packet, proxies_in_use,
            # _freshness counts the peer set's staleness only when the run
            # depended on peers. That is whether a peer comparison actually
            # scored (`peer_reads`), not whether peer ROIC was supplied:
            # competitive_position also scores on peer *margins*, so a run
            # resting on peer margins alone let a stale peer set escape the
            # freshness penalty — the peer-set staleness gap, closed only
            # halfway.
            uses_peers=bool(peer_reads),
            evidence_classes=[r.value.evidence_class for r in computed
                              if not r.value.is_null],
            consistency_checks=_consistency_checks(packet, ctx),
        ),
    )
    status = status_from_coverage(coverage)

    passed = 0
    failed = 0
    checks = [
        abs((category.awarded_points or 0.0) - Category(name=AGENT_ID, max_points=MAX_POINTS, dimensions=dimensions).points()) < 1e-6,
        # QA_CHECKLIST.md: "Formula IDs and versions are present" — both, so a
        # row missing either fails this, not only a missing id.
        bool(rows) and all(row.formula_id and row.formula_version for row in rows),
        bool(packet.analysis.knowledge_timestamp),
        # HANDOFF_CONTRACT.md has the main agent reject a packet when
        # "confidence and coverage are absent" — both, so both are checked
        # here rather than letting a missing coverage reach the handoff.
        category.confidence is not None,
        coverage is not None,
        # ...and when "a score lacks a formula ID or scoring rule". The id is
        # checked above; the rule is the registered anchor scale, so a published
        # numeric score must name where its scale came from.
        all(row.score == "NOT_SCORABLE" or row.metric_id in ANCHOR_PROVENANCE
            for row in rows),
    ]
    for ok in checks:
        passed += 1 if ok else 0
        failed += 0 if ok else 1
    validation_tests = ValidationTestsSummary(passed=passed, failed=failed, warnings=0)

    # ---- Judgment slots: which dimension slot each judgment-only metric feeds ----
    # None of them do. `moat_classification` holds no dimension slot: SCORING.md's moat
    # row lists three inputs and it is not one of them, and DATA_POLICY.md
    # does not score `Q` evidence absent a conversion rule Cerebro defines.
    # The answer still fills OUTPUT_SCHEMA.md's `moat.classification` and
    # settles the wide-moat gate's fourth condition.
    judgment_slots: dict[str, tuple[str, int]] = {}

    return BusinessOutput(
        agent_id=AGENT_ID,
        status=status,
        security=SecurityRef(
            ticker=packet.security.ticker, exchange=packet.security.exchange, currency=packet.security.reporting_currency
        ),
        knowledge_timestamp=packet.analysis.knowledge_timestamp,
        category=category,
        verdict=label,
        coverage=coverage,
        dimensions=dimensions,
        metrics=rows,
        mandatory_flags=mandatory_flags,
        assumptions=assumptions,
        judgment_requests=judgment_requests,
        judgment_slots=judgment_slots,
        source_lineage=_source_lineage(packet, overlay),
        validation_tests=validation_tests,
        business_in_one_sentence=None,
        moat=MoatSummary(classification="NotScorable", quantitative_evidence=[]),
        moat_gate_inputs=moat_gate_inputs,
        roic_history=ctx["roic_hist"],
        roic_wacc_spread_history=ctx["spread_hist"],
        margin_stability={
            "range": ctx.get("margin_range"),
            "window": ctx.get("op_margins_window", []),
            "recession_year_drawdown": ctx.get("recession_margin_drawdown"),
        },
        # OUTPUT_SCHEMA.md declares `customer_economics: {}` and nothing
        # ever filled it — unlike `margin_stability`, `capital_allocation`
        # and `competitive_position`, which carry their dimension's
        # figures. It reported an empty object on every security, for
        # every business model, however much retention data an analyst
        # supplied.
        #
        # The contents are the seven registered formulas of SCORING.md's
        # customer-economics row, keyed by the names FORMULAS.md gives
        # them. `applicable` comes from that row's own gate — "If not
        # applicable, use adapter metrics; do not impute" — which makes
        # applicability a fact about this dimension rather than an
        # inference: without it, a transactional model's seven nulls
        # would read identically to a subscription model that disclosed
        # nothing.
        customer_economics={
            "applicable": ctx.get("customer_economics_applicable"),
            "net_revenue_retention": _reported(by_id, "BUS-NRR-020"),
            "gross_revenue_retention": _reported(by_id, "BUS-GRR-021"),
            "logo_churn": _reported(by_id, "BUS-CHURN-022"),
            "customer_ltv": _reported(by_id, "BUS-LTV-023"),
            "customer_acquisition_cost": _reported(by_id, "BUS-CAC-024"),
            "ltv_to_cac": _reported(by_id, "BUS-LTVCAC-025"),
            "cac_payback_months": _reported(by_id, "BUS-PAYBACK-026"),
        },
        # BUS-SBC-030's caveat is "Also report SBC/FCF and diluted-share
        # trend". The share trend was here; SBC/FCF was computed, stored,
        # and dropped — the same way the customer figures were.
        capital_allocation={
            "diluted_share_cagr": ctx.get("diluted_cagr"),
            # BUS-DIL-028 is registered at "3y / 5y". The 5y above scores;
            # the 3y (also computed for DILUTION_RED_FLAG) is reported too.
            "diluted_share_cagr_3y": ctx.get("diluted_cagr_3y"),
            "returns_cash": ctx.get("returns_cash"),
            "sbc_burden": _reported(by_id, "BUS-SBC-030"),
            "sbc_to_fcf": ctx.get("sbc_to_fcf"),
            # Filled by the judgment layer when DILUTION_RED_FLAG is raised;
            # `_recompute_business_flags` reads it to clear the flag if the
            # analyst confirms per-share value creation kept pace.
            "commensurate_per_share_value_creation": None,
        },
        competitive_position={
            "revenue_cagr": ctx.get("revenue_cagr"),
            # BUS-CAGR-006 is registered at "3y and 5y"; the 5y scores, the
            # 3y is reported alongside it.
            "revenue_cagr_3y": ctx.get("revenue_cagr_3y"),
        },
        three_thesis_killers=[],
    )


# INDUSTRY_ADAPTERS.md splits into adapters that *replace* this module's
# formulas and adapters that only *add* to them, and model-fit confidence
# should follow that split rather than a flat "is it the default".
#
# Banks "Replace ROIC with ROE, ROTCE"; insurers and REITs likewise
# substitute their core measures, so the ROIC/margin machinery here does
# not fit. SaaS, by contrast, says "*Add* ARR growth, NRR, GRR, churn" —
# ROIC, margins and capital allocation still apply exactly as written, so
# a subscription business should not be told its business analysis is a
# poor fit. Commodities ask for cycle normalisation, which degrades fit
# without invalidating it.
# Kept as a name because tests and callers import it; the values live in
# `wbj.core.adapters` so every specialist reads one table.
#: FORMULAS.md: "Record any proxy in `warnings` and reduce model-fit
#: confidence." The document sets no size, so the penalty is chosen to be
#: material without swamping the adapter signal: two active proxies move a
#: full-fit 90 to 70, which still reads as a better-fitting model than one
#: whose methodology has been replaced outright (40).
MODEL_FIT_PENALTY_PER_PROXY = 10.0

#: A model cannot fit worse than one the methodology says not to use at
#: all, which INDUSTRY_ADAPTERS.md places at the bottom of the scale.
#:
#: This was 40, chosen when a replaced-model adapter also scored 40. Once
#: `confidence_inputs` derived that bottom from Cerebro it became 0, and
#: a floor of 40 then did the opposite of its job: it *raised* every
#: bank, insurer, REIT and biotech from the 0 the documents imply to 40.
MODEL_FIT_PROXY_FLOOR = _confidence_inputs.MODEL_FIT_REPLACED

PROXY_REVENUE_CAGR_FOR_SHARE = "PROXY_REVENUE_CAGR_FOR_MARKET_SHARE_TREND"
PROXY_TOTAL_CASH_AS_EXCESS_CASH = "PROXY_TOTAL_CASH_AS_EXCESS_CASH"

#: BUS-MIX-001: "Shares must sum to approximately 100% after
#: eliminations." Eliminations and rounding move the total off 1.0 by a
#: little; a set that misses by more than this is not a full breakdown,
#: and treating it as one understates every concentration measure built
#: on it.
SHARE_SUM_TOLERANCE = 0.05

#: BUS-REINV-018: "unstable when NOPAT is near zero". FORMULAS.md sets no
#: magnitude, so "near zero" is measured against the company's own
#: trailing NOPAT rather than an external figure, and the threshold is
#: declared in `assumptions` as AGENT.md permits for a disclosed
#: assumption.
NEAR_ZERO_NOPAT_FRACTION = 0.10

#: BUS-GUIDE-027's denominator is `max(|guidance_midpoint|,
#: materiality_floor)`. Victor names `materiality_floor` as a term but
#: states no number; it only bites when a guidance midpoint is itself near
#: zero, where an unfloored denominator would send accuracy toward minus
#: infinity. See CALIBRATION_REGISTRY for the declaration.
GUIDANCE_MATERIALITY_FLOOR = 0.01

#: DECISION_RULES.md wide-moat condition 1: "ROIC exceeds WACC by at
#: least 5 percentage points in at least four of the last five fiscal
#: years." Both halves are named here so widening the packet's history
#: cannot quietly change what the gate measures.
WIDE_MOAT_PERSISTENCE_WINDOW = 5
WIDE_MOAT_MIN_SPREAD = 0.05
WIDE_MOAT_MIN_PERSISTENCE = 0.8   # four of five

#: DECISION_RULES.md wide-moat condition 3: "At least two independent moat
#: effects are quantitatively visible." The count comes from the
#: `moat_quantitative_effects_count` judgment, stored in
#: `moat.quantitative_evidence`.
WIDE_MOAT_MIN_EFFECTS = 2

#: FORMULAS.md registers BUS-IROIC-016 and BUS-ALLOC-029 at a frequency of
#: "3y rolling". The incremental-ROIC change is measured over this window,
#: fixed rather than derived from the packet's history depth — the same
#: latency WIDE_MOAT_PERSISTENCE_WINDOW guards against. Reading the whole
#: available history instead turned a 3-year change into a 10-year one
#: once the packet carried eleven years.
INCREMENTAL_ROIC_WINDOW_YEARS = 3

#: FORMULAS.md registers BUS-CAGR-006 and BUS-DIL-028 at "3y and 5y" —
#: not the full packet history. Reading `revenues[0]` and
#: `valid_diluted[0]` measured the whole span, which became a 10-year
#: CAGR once the packet carried eleven years. Five is the longer of his
#: two windows and matches DATASET.md's 5-year minimum. A `5y` CAGR
#: compares a year to the one five years before it, so it spans six
#: annual points.
CAGR_WINDOW_YEARS = 5

#: SCORING_ENGINE.md: "Use a minimum of 8 valid peers."
_MIN_PEERS_FOR_PERCENTILE = 8

#: "top quartile" on `peer_score`'s 0-10 percentile scale: better than
#: three peers in four.
_TOP_QUARTILE_SCORE = 7.5

WARN_SHARES_INCOMPLETE = "SEGMENT_SHARES_DO_NOT_SUM_TO_100PCT"
WARN_HHI_LOWER_BOUND = "HHI_IS_A_LOWER_BOUND_PARTIAL_DISCLOSURE"
WARN_NOPAT_NEAR_ZERO = "REINVESTMENT_RATE_UNSTABLE_NOPAT_NEAR_ZERO"

#: CALCULATION_CONVENTIONS.md: "Use average balance-sheet values for
#: return ratios when both beginning and ending values exist. If only
#: ending values exist, label the result END_BALANCE_PROXY and reduce
#: confidence." The second sentence had no implementation — a company
#: with a single year of balance-sheet history reported no ROIC at all
#: rather than an ending-balance one, so a recent listing lost the
#: metric the convention says to compute and flag.
WARN_END_BALANCE_PROXY = "END_BALANCE_PROXY"

#: SOURCE_HIERARCHY.md's conflict rule: "If the difference remains
#: material, mark the metric `CONFLICTED` and do not score it." The
#: packet builder already reconciles FMP against SEC EDGAR and records
#: the verdict in `packet.facts_table` — NVIDIA's total debt differs by
#: 34.8% between them ($11.41B vs $8.47B) and is marked CONFLICTED with
#: no value. Business never read that table, so it took FMP's figure and
#: scored invested capital, ROIC, spread and EVA on an input the packet
#: had already declared unusable.
#: DECISION_RULES.md's "Mandatory flags" section carries four bullets.
#: Three name a flag and the condition that raises it; the fourth is
#: "Always list three business-model or execution risks that could
#: destroy the thesis." It sits in the same list, so it is a requirement
#: of the same standing — but it is the only one with no name of its own,
#: and nothing raised anything when it went unmet. An output could ship
#: with the list empty and read as fully compliant.
#:
#: The name is this engine's, since he gives none; the requirement and
#: its place among the mandatory flags are his.
THESIS_KILLERS_NOT_LISTED = "THESIS_KILLERS_NOT_LISTED"

#: "list three" — the count he asks for. The failure the rule guards is
#: their absence, so more than three satisfies it and fewer does not.
REQUIRED_THESIS_KILLERS = 3


def thesis_killers_listed(risks: Any) -> bool:
    """Whether an output meets DECISION_RULES.md's fourth mandatory rule.

    Three entries of empty string would satisfy a length check and list
    no risk at all, so each has to carry text.
    """
    if not isinstance(risks, (list, tuple)):
        return False
    stated = [r for r in risks if isinstance(r, str) and r.strip()]
    return len(stated) >= REQUIRED_THESIS_KILLERS


WARN_INPUT_CONFLICTED = "INPUT_CONFLICTED_ACROSS_SOURCES"

#: Raised by `valuation_engine.invested_capital` when the financing
#: and operating views of one period differ by more than 5%.
WARN_IC_VIEWS_DIFFER = "INVESTED_CAPITAL_VIEWS_DIFFER_GT_5PCT"

#: The reconciled facts business depends on, and the metrics each feeds.
_RECONCILED_INPUTS = {
    "total_debt": ("BUS-IC-012", "BUS-ROIC-013", "BUS-SPREAD-014",
                   "BUS-EVA-015", "BUS-IROIC-016", "BUS-ALLOC-029"),
    "cash": ("BUS-IC-012", "BUS-ROIC-013", "BUS-SPREAD-014",
             "BUS-EVA-015", "BUS-IROIC-016", "BUS-ALLOC-029"),
}
PROXY_STATUTORY_TAX_RATE = "PROXY_STATUTORY_TAX_RATE_IN_ROIC_HISTORY"
PROXY_CONFLICTED_SOURCE_INPUT = "PROXY_CONFLICTED_SOURCE_INPUT"
PROXY_BINARY_CAPITAL_RETURN = "PROXY_BINARY_CAPITAL_RETURN_FOR_ALLOCATION_QUALITY"

#: The staleness keys whose data this specialist actually consumes.
#: DATA_POLICY.md gives every data type its own staleness default and says
#: plainly that "Staleness affects confidence"; CONFIDENCE_ENGINE.md has
#: `freshness` "decays after the staleness default". Business reads annual
#: fundamentals always, and the peer set whenever the overlay supplies
#: `peer_roic` -- which carries half of competitive_position. It does not
#: read daily market data or consensus, so those must not drag its
#: freshness either way.
_FRESHNESS_ALWAYS = ("quarterly_fundamentals",)
_FRESHNESS_IF_PEERS_USED = "peer_set"


def _freshness(packet: Packet, uses_peers: bool) -> float:
    """Freshness (0-100) across every staleness signal this run depended on.

    Reading only `quarterly_fundamentals` meant a peer set past
    DATA_POLICY.md's 90-day rebuild threshold cost nothing, while its ROIC
    percentile went on carrying half of competitive_position. Each stale
    input decays the component the same way the single-key version did
    (100 fresh, 50 stale); the result is their mean, so one stale input
    out of two lands at 75 rather than flipping the whole component.
    """
    keys = list(_FRESHNESS_ALWAYS)
    if uses_peers:
        keys.append(_FRESHNESS_IF_PEERS_USED)
    scores = [100.0 if packet.staleness.get(k, "FRESH") == "FRESH" else 50.0 for k in keys]
    return sum(scores) / len(scores)


def _consistency_checks(packet: Packet, ctx: dict[str, Any]) -> tuple[int, int]:
    """Both halves of CONFIDENCE_ENGINE.md's "agreement across periods and
    reconciled sources", as one passed/run pair."""
    src_passed, src_run = _source_reconciliation_checks(packet)
    return (src_passed + (ctx.get("ic_checks_passed") or 0),
            src_run + (ctx.get("ic_checks_run") or 0))


def _source_reconciliation_checks(packet: Packet) -> tuple[int, int]:
    """How many reconciled facts agreed, out of those with two sources.

    The packet builder already compares FMP against SEC EDGAR and records
    the verdict. A fact only one source reports is not a disagreement —
    there is nothing to compare — so it is counted neither way.
    """
    passed = run = 0
    for key, fact in (getattr(packet, "facts_table", None) or {}).items():
        # `<field>:fmp` / `<field>:edgar` are the two source values kept for a
        # conflicted input, not independent facts — the conflict they came
        # from is already counted once under `<field>`.
        if ":" in key:
            continue
        warnings = " ".join(getattr(fact, "warnings", None) or [])
        if "unavailable" in warnings:      # single-source: nothing to compare
            continue
        run += 1
        if not str(getattr(fact, "state", "") or "").endswith("CONFLICTED"):
            passed += 1
    return passed, run


def _category_confidence(coverage: float, packet: Packet,
                         proxies_in_use: list[str] | None = None,
                         *, uses_peers: bool = False,
                         evidence_classes: list[Any] | None = None,
                         consistency_checks: tuple[int, int] = (0, 0)) -> float:
    """Category confidence (0-100) via `wbj.core.confidence.confidence()`.

    Every component is now measured. Three used to be flat constants —
    `source_quality = 85.0`, `consistency = 80.0`, and a per-adapter
    `model_fit` of 90/65/50/40 — figures that appear in none of Cerebro's
    documents and, for the first two, measured nothing: the same numbers
    came back whether a metric rested on an audited filing or an
    analyst's estimate, and whether the two data sources agreed or
    contradicted each other. `wbj.core.confidence_inputs` derives all
    three from the signals CONFIDENCE_ENGINE.md's own one-line
    descriptions name.

    `proxies_in_use` implements FORMULAS.md's "Record any proxy in
    `warnings` and reduce model-fit confidence". That is a direction
    without a magnitude, so the size of the reduction remains this
    engine's calibration and is declared in `assumptions` as such.
    """
    coverage_component = max(0.0, min(1.0, coverage)) * 100.0

    measured_quality = _confidence_inputs.source_quality(evidence_classes or [])
    # Nothing scored, so there is no evidence to judge. `coverage`
    # already carries that fact at twice this component's weight, and
    # scoring absent evidence at either extreme would double-count it, so
    # this contributes neutrally.
    source_quality = 50.0 if measured_quality is None else measured_quality

    freshness = _freshness(packet, uses_peers)

    measured_consistency = _confidence_inputs.consistency(*consistency_checks)
    consistency = 50.0 if measured_consistency is None else measured_consistency

    adapter = packet.analysis.industry_adapter
    adapter_is_classified = (
        _adapters.replaces_model(adapter)
        or _adapters.normalizes_inputs(adapter)
        or (adapter or "") in _adapters.MODEL_ADDITIVE
    )
    if adapter_is_classified:
        model_fit = _confidence_inputs.model_fit(
            replaces_model=_adapters.replaces_model(adapter),
            normalizes_inputs=_adapters.normalizes_inputs(adapter),
        )
    else:
        # An adapter no set classifies is one nobody has checked the
        # conventional formulas against. adapters.py's own model_fit floors
        # such a name — "claiming a good fit for it would be an assertion
        # without evidence" — but confidence_inputs.model_fit cannot see the
        # difference (a known-additive adapter and an unknown one both
        # arrive as neither-replacing-nor-normalizing), so an unclassified
        # adapter would otherwise score a perfect 100. Floor it, as a
        # model-replacing adapter is: no evidence the formulas fit.
        model_fit = _confidence_inputs.MODEL_FIT_REPLACED
    penalty = MODEL_FIT_PENALTY_PER_PROXY * len(proxies_in_use or [])
    model_fit = max(MODEL_FIT_PROXY_FLOOR, model_fit - penalty)

    return _confidence_formula(
        coverage=coverage_component, source_quality=source_quality, freshness=freshness,
        consistency=consistency, model_fit=model_fit,
    )
