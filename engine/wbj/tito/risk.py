"""Panel de riesgo — cuánto se puede poner en un flow sin volarse la cuenta.

Port de `web/lib/risk.ts`.

Dos capas en cascada:

1. **Calidad del contrato** — ¿es operable o es lotería? (`passes_quality_filter`)
2. **Sizing contra la cuenta** — ¿cuántos contratos caben? (`size_flow`)

Regla heredada: **nunca dimensionar una opción ilíquida**. Ante la duda se
bloquea y se explica el motivo. Lo que devuelve este módulo es un **TECHO**
("tu límite es N"), jamás una recomendación de compra.

Funciones puras: reciben el FlowRow ya clasificado y el perfil; no tocan red ni
disco. El saldo del usuario nunca sale de donde esté.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .flow import UNUSUAL_TRADE_THRESHOLD, FlowRow, unusual_trade_score

__all__ = [
    "MAX_THETA_PCT_DAILY",
    "THETA_BUDGET_PCT",
    "MIN_DTE",
    "RiskProfile",
    "Budgets",
    "Sizing",
    "QualityResult",
    "budgets_of",
    "passes_quality_filter",
    "is_tradeable_idea",
    "size_flow",
]

#: Techo de decaimiento diario para considerar un contrato operable, en % de su
#: propia prima. Es la banda de `theta_score` (SCOREDCARD/Inusualidad.md): por
#: encima del 5% el documento le da 0 puntos — es lotería, no posición.
MAX_THETA_PCT_DAILY = 5.0

#: Presupuesto de quema de theta, en % de la CUENTA.
#:
#: Va aparte de la tolerancia A PROPÓSITO. Si compartiera presupuesto con la
#: prima, el límite por theta jamás podría frenar: como la quema se topa en el
#: costo del contrato (una opción larga no puede perder más que su prima),
#: ``presupuesto/quema`` siempre sería >= ``presupuesto/costo`` y el ``min``
#: elegiría la prima el 100% de las veces — la capa de theta sería código
#: muerto. Con su propio presupuesto —anclado a la banda 3-5% del documento de
#: Inusualidad— el theta frena de verdad cuando la tolerancia pasa del 5%.
THETA_BUDGET_PCT = 5.0

#: Días mínimos al vencimiento para que valga la pena mirarlo.
MIN_DTE = 7

#: Un contrato son 100 acciones.
_MULTIPLIER = 100

BlockReason = Literal["iliquidez", "theta_alto", "sin_theta", "vencido"]
Binding = Literal["prima", "theta"]


@dataclass(frozen=True)
class RiskProfile:
    """Perfil del usuario. El saldo nunca se persiste del lado del servidor."""

    account_size: float
    #: % de la cuenta que acepta arriesgar por trade (el slider).
    tolerance_pct: float


@dataclass(frozen=True)
class Budgets:
    premium: float  # máximo capital a desplegar (pérdida máxima aceptada), en $
    theta: float  # máxima quema de theta en el horizonte, en $


@dataclass(frozen=True)
class Sizing:
    max_contracts: int  # el TECHO de contratos. 0 si algo lo bloquea
    binding: Binding | None  # cuál de las dos restricciones produjo el techo
    cost_per_contract: float
    total_cost: float
    cost_pct_of_account: float
    burn_days: int  # días sobre los que se calcula la quema: min(dte, horizonte)
    theta_burn_per_contract: float
    total_burn: float
    burn_pct_of_account: float
    #: La quema alcanzó el costo: el contrato se consume entero en el horizonte.
    fully_decays: bool
    blocked: dict | None


@dataclass(frozen=True)
class QualityResult:
    ok: bool
    reason: BlockReason | None = None
    detail: str | None = None


def _safe(n: float | None) -> float:
    """Número finito y positivo, o 0. Blinda contra NaN/inf que lleguen de la UI."""
    return float(n) if isinstance(n, (int, float)) and math.isfinite(n) and n > 0 else 0.0


def budgets_of(profile: RiskProfile) -> Budgets:
    account = _safe(getattr(profile, "account_size", 0))
    tolerance = _safe(getattr(profile, "tolerance_pct", 0))
    return Budgets(
        premium=account * tolerance / 100,
        theta=account * THETA_BUDGET_PCT / 100,
    )


def passes_quality_filter(row: FlowRow) -> QualityResult:
    """Capa 1 — ¿el contrato es operable? Filtra antes de que el sizing lo toque.

    **No estima theta**: si el feed no lo trajo, el flow no se dimensiona. Un
    theta inventado produciría un techo inventado.
    """
    if row.theta_pct_daily is None:
        return QualityResult(False, "sin_theta", "El feed no trajo theta para este contrato.")
    if row.expiry_status in ("expirado", "expira_hoy"):
        return QualityResult(False, "vencido", "El contrato ya venció o vence hoy.")
    if row.dte is None or row.dte < MIN_DTE:
        return QualityResult(
            False,
            "vencido",
            f"Vence en menos de {MIN_DTE} días: no da tiempo a que el movimiento se desarrolle.",
        )
    if row.theta_pct_daily > MAX_THETA_PCT_DAILY:
        return QualityResult(
            False,
            "theta_alto",
            f"Pierde {row.theta_pct_daily:.1f}% de su valor al día "
            f"(máximo {MAX_THETA_PCT_DAILY:.0f}%): es lotería, no posición.",
        )
    return QualityResult(True)


def is_tradeable_idea(row: FlowRow) -> bool:
    """¿El flow merece salir en el screener? Capa 1 + el umbral de inusualidad."""
    return (
        passes_quality_filter(row).ok
        and unusual_trade_score(row).total >= UNUSUAL_TRADE_THRESHOLD
    )


def _blocked(reason: BlockReason, detail: str) -> Sizing:
    return Sizing(
        max_contracts=0, binding=None, cost_per_contract=0.0, total_cost=0.0,
        cost_pct_of_account=0.0, burn_days=0, theta_burn_per_contract=0.0,
        total_burn=0.0, burn_pct_of_account=0.0, fully_decays=False,
        blocked={"reason": reason, "detail": detail},
    )


def size_flow(
    row: FlowRow,
    profile: RiskProfile,
    horizon_days: float,
    low_liquidity: bool = False,
) -> Sizing:
    """Capa 2 — cuántos contratos caben.

    Gana la restricción **más estricta** entre prima (pérdida máxima) y quema de
    theta, y se reporta cuál fue.
    """
    # La iliquidez manda sobre todo lo demás, por buena que sea la estructura.
    if low_liquidity:
        return _blocked(
            "iliquidez",
            "Cadena ilíquida: el agente no calcula tamaño cuando no confía en los datos.",
        )

    quality = passes_quality_filter(row)
    if not quality.ok:
        return _blocked(quality.reason, quality.detail)  # type: ignore[arg-type]

    account = _safe(getattr(profile, "account_size", 0))
    budgets = budgets_of(profile)
    cost_per_contract = _safe(row.price) * _MULTIPLIER
    if cost_per_contract == 0:
        return _blocked("sin_theta", "El contrato no tiene precio utilizable.")

    # No se quema theta más allá del vencimiento.
    burn_days = int(max(0, min(row.dte or 0, _safe(horizon_days))))
    raw_burn = _safe(abs(row.theta)) * _MULTIPLIER * burn_days
    # Tope: una opción larga no puede perder más que su prima.
    theta_burn_per_contract = min(raw_burn, cost_per_contract)
    fully_decays = raw_burn >= cost_per_contract and raw_burn > 0

    by_premium = math.floor(budgets.premium / cost_per_contract)
    by_theta = (
        math.floor(budgets.theta / theta_burn_per_contract)
        if theta_burn_per_contract > 0
        else by_premium  # sin quema medible, la prima decide
    )

    max_contracts = max(0, min(by_premium, by_theta))
    # Empate → se atribuye a la prima: es la restricción de pérdida real.
    binding: Binding | None = (
        None if max_contracts == 0 else ("theta" if by_theta < by_premium else "prima")
    )

    total_cost = max_contracts * cost_per_contract
    total_burn = max_contracts * theta_burn_per_contract

    def pct(v: float) -> float:
        return (v / account * 100) if account > 0 else 0.0

    return Sizing(
        max_contracts=max_contracts,
        binding=binding,
        cost_per_contract=cost_per_contract,
        total_cost=total_cost,
        cost_pct_of_account=pct(total_cost),
        burn_days=burn_days,
        theta_burn_per_contract=theta_burn_per_contract,
        total_burn=total_burn,
        burn_pct_of_account=pct(total_burn),
        fully_decays=fully_decays,
        blocked=None,
    )
