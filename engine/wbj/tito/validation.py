"""Sub-agente 6 — Validación de Flows / Confirmación de Precio (15% del scorecard).

Port de `web/lib/validation.ts`. Fuente: `SCOREDCARD/Validacion-Flows.md`.

La pregunta: después de que un flow aparece, ¿el precio lo **valida** o lo
**absorbe**? Para cada flow guardado se mide cuánto tiempo pasó y cuánto se
movió el precio a favor y en contra del contrato (excursión máxima favorable /
adversa), que es exactamente lo que pide el documento: *"cuánto tiempo ha tardado
en desarrollarse ese movimiento, tanto al alza del contrato como a la baja"*.

AVISO IMPORTANTE — este PDF, a diferencia de los otros cinco, **NO trae tabla de
puntos**. Las bandas de `validation_points` / `speed_points` son una PROPUESTA
construida con el mismo estilo que las demás categorías. Están aisladas en esas
dos funciones para cambiarlas de un solo sitio cuando el documento las defina.
Bajo la regla innegociable de Vertex ("sin fórmula, no hay conclusión") esta es
la única parte del scorecard cuya escala no tiene respaldo documental, y debe
declararse como tal en cualquier reporte que la use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from .jsmath import js_add, js_date_parse, js_gt, js_number, js_round, js_string

__all__ = [
    "MOVE_THRESHOLD_PCT",
    "THRESHOLD_ATR_MULTIPLE",
    "HORIZON_SESSIONS",
    "BACKTEST_TARGET_DAYS",
    "Direction",
    "FlowLite",
    "ValBar",
    "FlowOutcome",
    "ValBand",
    "ValidationScore",
    "adaptive_threshold",
    "flow_direction",
    "evaluate_flow",
    "validation_points",
    "speed_points",
    "median",
    "validation_score",
]

Direction = Literal["alcista", "bajista", "neutral"]

#: Piso del umbral de movimiento (%) cuando la acción es muy tranquila.
MOVE_THRESHOLD_PCT = 2.0
#: Cuántos rangos diarios típicos tiene que moverse el precio para contar.
THRESHOLD_ATR_MULTIPLE = 1.5
#: Sesiones que se siguen hacia adelante tras cada flow.
HORIZON_SESSIONS = 20
#: El documento pide al menos 60 días de datos para que el backtest sea fiable.
BACKTEST_TARGET_DAYS = 60


@dataclass(frozen=True)
class FlowLite:
    id: int
    timestamp: str
    type: Literal["call", "put", "unknown"]
    strike: float | None
    expiration: str | None
    asset_price: float
    premium: float
    aggression: str  # "ask" | "bid" | "mid"


@dataclass(frozen=True)
class ValBar:
    time: str  # YYYY-MM-DD
    high: float
    low: float
    close: float


def median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def adaptive_threshold(bars: Sequence[ValBar], sessions: int = 60) -> float:
    """Umbral de movimiento adaptado a la volatilidad de la acción.

    Un 2% fijo no sirve igual para todos: TSLA recorre 4-5% en un día normal, así
    que casi cualquier flow "se validaría" en la primera sesión y la medición no
    diría nada. Se usa el rango diario típico (mediana de high-low en %) ×
    `THRESHOLD_ATR_MULTIPLE`.
    """
    recent = [b for b in bars[-sessions:] if js_gt(b.low)]
    if len(recent) < 5:
        return MOVE_THRESHOLD_PCT
    ranges = [(js_number(b.high) - js_number(b.low)) / js_number(b.low) * 100
              for b in recent]
    typical = median(ranges) or MOVE_THRESHOLD_PCT
    return max(MOVE_THRESHOLD_PCT, typical * THRESHOLD_ATR_MULTIPLE)


def flow_direction(type: str, aggression: str) -> Direction:
    """Hacia dónde apuesta un flow, según la tabla del Proceso Principal.

    Comprar call o **vender put** = alcista; comprar put o **vender call** =
    bajista. El lado de ejecución importa tanto como el tipo de contrato.
    """
    bought = aggression == "ask"
    sold = aggression == "bid"
    if type == "call":
        if bought:
            return "alcista"
        if sold:
            return "bajista"  # vender calls = resistencia / muro
    elif type == "put":
        if bought:
            return "bajista"
        if sold:
            return "alcista"  # vender puts = soporte
    return "neutral"


@dataclass
class FlowOutcome:
    id: int
    timestamp: str
    type: str
    strike: float | None
    expiration: str | None
    premium: float
    direction: Direction
    entry_price: float
    mfe_pct: float = 0.0  # excursión máxima a favor del contrato (%)
    days_to_mfe: int | None = None
    mae_pct: float = 0.0  # excursión máxima en contra (%)
    days_to_mae: int | None = None
    days_to_validate: int | None = None
    days_to_invalidate: int | None = None
    sessions_observed: int = 0
    days_elapsed: int = 0
    #: true = el precio confirmó ANTES de irse en contra.
    validated: bool = False
    #: false = demasiado reciente para juzgarlo.
    resolved: bool = False


def _days_between(a: str, b: str) -> float:
    """`Math.floor((new Date(b).getTime() - new Date(a).getTime()) / DAY)`.

    Literal, con `Date.parse` de JS y no con `fromisoformat` de Python. El port
    devolvía **0** cuando alguna de las dos no se parseaba, y un 0 aquí se lee
    como "el flow es de hoy": el backtest lo trataba como recién abierto en vez
    de dejarlo sin resolver. Él propaga `NaN`, que no pasa ninguna comparación.
    Medido en `diff_reloj.sh`.
    """
    ms = js_date_parse(b) - js_date_parse(a)
    return math.floor(ms / 86_400_000) if ms == ms else math.nan


def evaluate_flow(
    flow: FlowLite,
    bars: Sequence[ValBar],
    now: datetime,
    threshold_pct: float = MOVE_THRESHOLD_PCT,
    horizon: int = HORIZON_SESSIONS,
) -> FlowOutcome:
    """Sigue un flow hacia adelante en las barras diarias y mide qué hizo el precio.

    Solo cuentan las sesiones **posteriores** al día del flow: el mismo día ya
    está contaminado por el propio trade.
    """
    # `flow.timestamp.slice(0, 10)` — sobre `undefined` esto LANZA también en su
    # archivo, así que no se tapa. Lo que sí se hace es no lanzar por una razón
    # distinta a la suya: `js_string` deja el resto del recorrido con un texto.
    day = js_string(flow.timestamp)[:10]
    entry = flow.asset_price
    direction = flow_direction(flow.type, flow.aggression)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    days_elapsed = max(0, _days_between(flow.timestamp, now.isoformat()))

    out = FlowOutcome(
        id=flow.id, timestamp=flow.timestamp, type=flow.type, strike=flow.strike,
        expiration=flow.expiration, premium=flow.premium, direction=direction,
        entry_price=entry, days_elapsed=days_elapsed,
    )

    # `if (!(entry > 0) || direction === "neutral") return base;`
    #
    # `js_gt` y no `entry > 0`: en JS el operador relacional coacciona con
    # `ToNumber` y un `NaN` hace falsa TODA comparación, así que `undefined > 0`
    # es `false` y el flow se descarta solo. En Python `None > 0` es un
    # `TypeError` que sube por la pila — y aquí eso no tumbaba un flow, tumbaba
    # `validation_score` ENTERO: el sub-agente 6 se quedaba sin score por una
    # sola fila con `asset_price` ausente. Lo encontró el corpus malformado de
    # `diff_motor.sh`.
    if not js_gt(entry) or direction == "neutral":
        return out

    forward = [b for b in bars if js_string(b.time) > day]
    if flow.expiration:
        forward = [b for b in forward
                   if js_string(b.time) <= js_string(flow.expiration)]
    forward = forward[:horizon]
    if not forward:
        return out

    up = direction == "alcista"
    mfe = mae = 0.0
    days_to_mfe = days_to_mae = None
    days_to_validate = days_to_invalidate = None

    # En TS cada operación aritmética coacciona con `ToNumber` por su cuenta, así
    # que `(b.high - entry)` con `entry = "500"` da 500 sin que él escriba nada.
    # En Python eso es un `TypeError`. Se coacciona UNA vez aquí, que es
    # equivalente y no repite la conversión en cada vuelta del bucle.
    #
    # El campo `entry_price` de la salida conserva el valor CRUDO, como su
    # `entryPrice: entry`: lo que se coacciona es la cuenta, no lo que se reporta.
    e = js_number(entry)
    umbral = js_number(threshold_pct)

    for i, b in enumerate(forward):
        session = i + 1
        alto, bajo = js_number(b.high), js_number(b.low)
        # A favor del contrato: para un alcista el máximo; para un bajista el mínimo.
        fav = ((alto - e) / e * 100) if up else ((e - bajo) / e * 100)
        adv = ((e - bajo) / e * 100) if up else ((alto - e) / e * 100)

        if fav > mfe:
            mfe, days_to_mfe = fav, session
        if adv > mae:
            mae, days_to_mae = adv, session
        if days_to_validate is None and fav >= umbral:
            days_to_validate = session
        if days_to_invalidate is None and adv >= umbral:
            days_to_invalidate = session

    # Se juzga cuando cruzó algún umbral, o cuando ya se agotó la observación.
    hit_something = days_to_validate is not None or days_to_invalidate is not None
    exhausted = len(forward) >= horizon

    out.mfe_pct = mfe
    out.days_to_mfe = days_to_mfe
    out.mae_pct = mae
    out.days_to_mae = days_to_mae
    out.days_to_validate = days_to_validate
    out.days_to_invalidate = days_to_invalidate
    out.sessions_observed = len(forward)
    out.resolved = hit_something or exhausted
    # Validado = confirmó a favor ANTES de irse en contra.
    out.validated = days_to_validate is not None and (
        days_to_invalidate is None or days_to_validate <= days_to_invalidate
    )
    return out


# ---------- puntuación (PROPUESTA — el PDF no trae tabla) ----------


@dataclass(frozen=True)
class ValBand:
    points: int
    band: str


def validation_points(hit_rate_pct: float | None) -> ValBand:
    """Parámetro 1 — Tasa de validación: de los flows ya juzgados, cuántos confirmó."""
    if hit_rate_pct is None or not math.isfinite(hit_rate_pct):
        return ValBand(0, "sin datos")
    if hit_rate_pct >= 70:
        return ValBand(10, "≥70% — el precio valida casi siempre")
    if hit_rate_pct >= 60:
        return ValBand(8, "60-69% — valida con frecuencia")
    if hit_rate_pct >= 50:
        return ValBand(6, "50-59% — valida más de lo que absorbe")
    if hit_rate_pct >= 40:
        return ValBand(4, "40-49% — mezclado")
    if hit_rate_pct >= 30:
        return ValBand(2, "30-39% — el precio suele absorber")
    return ValBand(0, "<30% — el precio absorbe el flujo")


def speed_points(median_sessions: float | None) -> ValBand:
    """Parámetro 2 — Velocidad: mediana de sesiones hasta que el movimiento aparece."""
    if median_sessions is None or not math.isfinite(median_sessions):
        return ValBand(0, "sin datos")
    if median_sessions <= 2:
        return ValBand(10, "≤2 sesiones — reacción inmediata")
    if median_sessions <= 5:
        return ValBand(8, "3-5 sesiones — reacción rápida")
    if median_sessions <= 10:
        return ValBand(6, "6-10 sesiones — desarrollo medio")
    if median_sessions <= 15:
        return ValBand(4, "11-15 sesiones — lento")
    return ValBand(2, ">15 sesiones — muy lento")


# ---------- reporte ----------


@dataclass
class ValidationScore:
    score: int
    hit_rate: dict
    speed: dict
    weighted_hit_rate: float | None
    threshold_pct: float
    avg_mfe: float | None
    avg_mae: float | None
    by_direction: list[dict]
    outcomes: list[FlowOutcome]
    coverage: dict
    verdict: str


_EMPTY = ValidationScore(
    score=0,
    hit_rate={"value": None, "validated": 0, "resolved": 0, "points": 0, "band": "sin datos"},
    speed={"median_sessions": None, "points": 0, "band": "sin datos"},
    weighted_hit_rate=None,
    threshold_pct=MOVE_THRESHOLD_PCT,
    avg_mfe=None,
    avg_mae=None,
    by_direction=[],
    outcomes=[],
    coverage={
        "days": 0, "sessions": 0, "flows": 0, "pending": 0,
        "below_target": True, "first_flow": None, "last_flow": None,
    },
    verdict="Sin flows guardados todavía para validar.",
)


def validation_score(
    flows: Sequence[FlowLite],
    bars: Sequence[ValBar],
    now: datetime,
    threshold_pct: float | None = None,
    horizon: int = HORIZON_SESSIONS,
) -> ValidationScore:
    """Backtest del tape: ¿el precio confirmó los flows que ya vencieron?"""
    if not flows or not bars:
        return _EMPTY

    # `sort((a, b) => a.time.localeCompare(b.time))` — comparación de TEXTO. El
    # orden natural de Python lanza si se mezcla `None` con `str`; `js_string`
    # da la misma cadena que compararía él (`String(undefined)` es "undefined").
    ordered = sorted(bars, key=lambda b: js_string(b.time))
    thr = threshold_pct if threshold_pct is not None else adaptive_threshold(ordered)
    outcomes = sorted(
        (
            o
            for o in (evaluate_flow(f, ordered, now, thr, horizon) for f in flows)
            if o.direction != "neutral"
        ),
        # Ordena por texto, como su `localeCompare`: un `timestamp` ausente
        # no puede hacer que la comparación lance.
        key=lambda o: js_string(o.timestamp),
        reverse=True,
    )

    resolved = [o for o in outcomes if o.resolved]
    validated = [o for o in resolved if o.validated]
    hit_rate_value = (len(validated) / len(resolved) * 100) if resolved else None

    # Ponderada por premium: pesa más el acierto del dinero grande.
    #
    # `js_add` y no `sum()`: su acumulador es `wTotal += o.premium` y el `+` de
    # JS concatena en cuanto un operando es un string. Con premios numéricos —el
    # caso normal— esto es una suma corriente; con un `premium` que llegue en
    # texto reproduce su resultado en vez de caerse con un `TypeError`.
    w_total: Any = 0
    w_hit: Any = 0
    for o in resolved:
        w_total = js_add(w_total, o.premium)
        if o.validated:
            w_hit = js_add(w_hit, o.premium)
    weighted_hit_rate = ((js_number(w_hit) / js_number(w_total) * 100)
                         if js_gt(w_total) else None)

    median_sessions = median(
        [o.days_to_validate for o in validated if o.days_to_validate is not None]
    )

    hr = validation_points(hit_rate_value)
    sp = speed_points(median_sessions)

    by_direction = []
    for direction in ("alcista", "bajista"):
        subset = [o for o in resolved if o.direction == direction]
        ok = sum(1 for o in subset if o.validated)
        by_direction.append(
            {
                "direction": direction,
                "total": len(subset),
                "validated": ok,
                "hit_rate": (ok / len(subset) * 100) if subset else None,
            }
        )

    stamps = sorted(js_string(o.timestamp) for o in outcomes)
    first_flow = stamps[0] if stamps else None
    last_flow = stamps[-1] if stamps else None
    days = _days_between(first_flow, last_flow) if first_flow and last_flow else 0
    sessions = len({js_string(o.timestamp)[:10] for o in outcomes})

    def avg(xs: list[float]) -> float | None:
        return (sum(xs) / len(xs)) if xs else None

    if hit_rate_value is None:
        verdict = "Aún no hay flows con suficiente recorrido para juzgarlos."
    elif hit_rate_value >= 60:
        verdict = "El precio confirma el flujo: cuando entra dinero grande, el movimiento aparece."
    elif hit_rate_value >= 45:
        verdict = "Confirmación mezclada: el flujo acierta y falla en proporciones parecidas."
    else:
        verdict = "El precio absorbe el flujo: entra dinero grande pero el movimiento no se desarrolla."

    return ValidationScore(
        score=js_round((hr.points + sp.points) / 2),
        hit_rate={
            "value": hit_rate_value, "validated": len(validated),
            "resolved": len(resolved), "points": hr.points, "band": hr.band,
        },
        speed={"median_sessions": median_sessions, "points": sp.points, "band": sp.band},
        weighted_hit_rate=weighted_hit_rate,
        threshold_pct=thr,
        avg_mfe=avg([o.mfe_pct for o in resolved]),
        avg_mae=avg([o.mae_pct for o in resolved]),
        by_direction=by_direction,
        outcomes=outcomes,
        coverage={
            "days": days,
            "sessions": sessions,
            "flows": len(outcomes),
            "pending": len(outcomes) - len(resolved),
            "below_target": days < BACKTEST_TARGET_DAYS,
            "first_flow": first_flow,
            "last_flow": last_flow,
        },
        verdict=verdict,
    )
