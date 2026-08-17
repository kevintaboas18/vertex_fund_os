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
from .jsmath import (js_gt, js_number, js_is_finite, js_le, js_max, js_min,
                     js_abs, js_floor, js_to_fixed, js_truthy, es_nulo)
from dataclasses import dataclass
from typing import Literal

from .flow import FlowRow, unusual_trade_score

__all__ = [
    "MAX_THETA_PCT_DAILY",
    "THETA_BUDGET_PCT",
    "MIN_DTE",
    "IDEA_UNUSUAL_THRESHOLD",
    "MONEYNESS_CAP",
    "RiskProfile",
    "Budgets",
    "Sizing",
    "QualityResult",
    "budgets_of",
    "passes_quality_filter",
    "is_tradeable_idea",
    "within_moneyness",
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

#: Días mínimos al vencimiento para que valga la pena mirarlo. Bajo a propósito
#: (contratos "más cercanos"): permite semanales/near-term, pero sigue tumbando
#: los 0DTE / "expira_hoy" —que van por `expiry_status`— por ser pura lotería.
MIN_DTE = 2

#: Umbral de inusualidad SOLO para el screener de ideas (`is_tradeable_idea`).
#:
#: Más laxo que el institucional (`UNUSUAL_TRADE_THRESHOLD = 7` en `flow.py`, que
#: define el scorecard): con un piso de premium más bajo, el puntaje de tamaño ya
#: no basta para llegar a 7, así que aquí basta con un 5 para dejar pasar flujo
#: direccional de tamaño mediano. No toca la definición institucional del dashboard.
IDEA_UNUSUAL_THRESHOLD = 5

#: Cercanía máxima del strike al precio del subyacente, |strike − spot| / spot.
#:
#: Contratos "más cercanos": descarta lo muy OTM (lotería barata) y lo muy ITM
#: (caro y sin apalancamiento). 0.25 = dentro del ±25% del precio actual.
MONEYNESS_CAP = 0.25

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

    # ── Los dos campos del MODELO DE KEVIN ───────────────────────────────────
    #
    # Van con `None` por defecto **a propósito**: sin ellos `budgets_of` se
    # comporta exactamente igual que el `budgetsOf` de su `risk.ts`, así que la
    # paridad con Víctor no se toca y `diff_motor.sh` sigue en cero. La
    # divergencia es opcional y declarada, no una reescritura.
    #
    # El modelo de Víctor y el de Kevin son los dos coherentes, pero distintos:
    #
    #   Víctor: «puedo perder el X% de la CUENTA» → despliega como mucho ese X%,
    #           porque una opción larga puede irse a cero. No supone que un stop
    #           llegue a ejecutarse.
    #   Kevin:  «despliego entre el 5% y el 80% de la cuenta y corto en −30% de
    #           lo que puse». El % es de LA POSICIÓN, no de la cuenta.
    #
    #: Techo de despliegue, en % de la cuenta (el extremo alto de la banda
    #: «cuánto capital puede ocupar UNA sola posición»).
    max_position_pct: float | None = None
    #: Pérdida que se acepta, en % de LO QUE CUESTA LA POSICIÓN.
    loss_pct_of_position: float | None = None


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


def _safe(n) -> float:
    """`Number.isFinite(n) && n > 0 ? n : 0` — número finito y positivo, o 0.

    `Number.isFinite` NO coacciona: un `true` y un `"500"` valen 0 aquí, aunque
    `Number()` sepa leerlos. El port usaba `isinstance` + `isfinite`, y como en
    Python `bool` es subclase de `int`, un `tolerancePct: true` se convertía en
    un 1% de tolerancia real. Medido en `diff_motor2.sh`.
    """
    return float(n) if js_is_finite(n) and n > 0 else 0.0


def budgets_of(profile: RiskProfile) -> Budgets:
    """Los dos presupuestos con los que `size_flow` pone el techo.

    Dos modelos, y el del perfil manda:

    - **Sin `max_position_pct`** (el de Víctor, y el de cualquier `RiskProfile`
      construido con dos campos): prima = X% de la CUENTA, theta = 5% de la
      cuenta. Literal a su `budgetsOf`. No se ha tocado.

    - **Con `max_position_pct`** (el de Kevin): el techo de despliegue es su
      banda de posición, y el de theta es la pérdida que acepta sobre ese
      despliegue. Los dos salen de lo que contestó, en vez de que su 5-80% se
      quedara sin llegar nunca a la matemática.

    El presupuesto de theta sigue yendo APARTE del de prima y por el motivo de
    siempre: si fueran el mismo, `presupuesto/quema >= presupuesto/costo` para
    toda opción larga —la quema se topa en el costo— y el `min` elegiría la
    prima el 100% de las veces. Aquí no pasa: el de theta es una FRACCIÓN del
    de prima, así que muerde en cuanto la quema del horizonte se come más de
    ese % del contrato.
    """
    account = _safe(getattr(profile, "account_size", 0))
    tolerance = _safe(getattr(profile, "tolerance_pct", 0))
    pos_pct = getattr(profile, "max_position_pct", None)
    if pos_pct is None or _safe(pos_pct) == 0:
        return Budgets(
            premium=account * tolerance / 100,
            theta=account * THETA_BUDGET_PCT / 100,
        )
    premium = account * _safe(pos_pct) / 100
    # La pérdida aceptada sobre lo que se despliega. Sin ese dato se cae al
    # techo de theta de siempre: inventarse un porcentaje sería peor que usar
    # el que ya está justificado en el documento de Inusualidad.
    perdida = getattr(profile, "loss_pct_of_position", None)
    theta = (premium * _safe(perdida) / 100 if perdida is not None
             and _safe(perdida) > 0 else account * THETA_BUDGET_PCT / 100)
    return Budgets(premium=premium, theta=theta)


def passes_quality_filter(row: FlowRow) -> QualityResult:
    """Capa 1 — ¿el contrato es operable? Filtra antes de que el sizing lo toque.

    **No estima theta**: si el feed no lo trajo, el flow no se dimensiona. Un
    theta inventado produciría un techo inventado.
    """
    if es_nulo(row.theta_pct_daily):
        return QualityResult(False, "sin_theta", "El feed no trajo theta para este contrato.")
    if row.expiry_status in ("expirado", "expira_hoy"):
        return QualityResult(False, "vencido", "El contrato ya venció o vence hoy.")
    if es_nulo(row.dte) or js_number(row.dte) < MIN_DTE:
        return QualityResult(
            False,
            "vencido",
            f"Vence en menos de {MIN_DTE} días: no da tiempo a que el movimiento se desarrolle.",
        )
    if js_number(row.theta_pct_daily) > MAX_THETA_PCT_DAILY:
        return QualityResult(
            False,
            "theta_alto",
            # `row.thetaPctDaily.toFixed(1)` es una llamada a MÉTODO: sobre un
            # valor que no sea número lanza, igual que su archivo.
            f"Pierde {js_to_fixed(row.theta_pct_daily, 1)}% de su valor al día "
            f"(máximo {MAX_THETA_PCT_DAILY:.0f}%): es lotería, no posición.",
        )
    return QualityResult(True)


def is_tradeable_idea(row: FlowRow) -> bool:
    """¿El flow merece salir en el screener? Capa 1 + el umbral del SCREENER."""
    return (
        passes_quality_filter(row).ok
        and unusual_trade_score(row).total >= IDEA_UNUSUAL_THRESHOLD
    )


def within_moneyness(row: FlowRow, cap: float = MONEYNESS_CAP) -> bool:
    """¿El strike está cerca del precio actual? Filtro de "contratos más cercanos".

    Ante datos faltantes (sin strike o sin precio del subyacente) **no filtra**:
    la cercanía es una preferencia, no una salvaguarda, así que no tira filas por
    falta de datos.

    `Math.abs(row.strike - spot)` es una RESTA de JS: el `-` coacciona, así que
    un `strike: "220"` que llegue como texto del feed vale 220 y no revienta.
    """
    spot = _safe(row.asset_price)
    if spot == 0 or es_nulo(row.strike):
        return True
    return js_le(js_abs(js_number(row.strike) - spot) / spot, cap)


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
    # `if (ctx.lowLiquidity)` con la veracidad de JS: `[]` bloquea.
    if js_truthy(low_liquidity):
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
    #
    # `Math.max(0, Math.min(row.dte ?? 0, safe(horizonDays)))`, literal y en
    # tres puntos que el port tenía distintos: NO es un entero (un horizonte de
    # 0.3 días se reporta como 0.3), `?? 0` solo sustituye `null`/`undefined`
    # —un `dte: 0` sigue siendo 0— y `Math.min` propaga el `NaN` en vez de
    # esconderlo, que es lo que hacía el `min()` de Python.
    dte = row.dte if not es_nulo(row.dte) else 0
    burn_days = js_max(0, js_min(dte, _safe(horizon_days)))
    raw_burn = _safe(js_abs(row.theta)) * _MULTIPLIER * burn_days
    # Tope: una opción larga no puede perder más que su prima.
    theta_burn_per_contract = js_min(raw_burn, cost_per_contract)
    fully_decays = raw_burn >= cost_per_contract and raw_burn > 0

    by_premium = js_floor(budgets.premium / cost_per_contract)
    by_theta = (
        js_floor(budgets.theta / theta_burn_per_contract)
        if theta_burn_per_contract > 0
        else by_premium  # sin quema medible, la prima decide
    )

    max_contracts = js_max(0, js_min(by_premium, by_theta))
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
