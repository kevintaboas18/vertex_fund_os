"""Criterio de la Wheel: presets, prima, liquidez, métricas y score.

Port de `web/lib/wheel.ts`.

**PURO** — no toca red ni disco. La ruta orquesta el I/O; aquí solo se decide.

La Wheel es vender *cash-secured puts*: cobras prima por comprometerte a comprar
la acción a un strike. Un contrato son 100 acciones y el colateral es
``strike × 100`` — ese efectivo queda **inmovilizado** hasta el vencimiento.

Dos cosas de este módulo se leen al revés que el resto del agente, y no es un
error de copia:

- **La banda de IV Rank está INVERTIDA** respecto a `ivcontext.py`. Allí el pico
  está en 16-30 porque el resto del agente **compra** opciones y quiere vega
  barata; la Wheel **vende** y quiere la volatilidad cara.
- **Un rendimiento anualizado alto se CASTIGA.** Un screener que ordena por
  prima pone arriba justo las acciones que están a punto de desplomarse.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

from .black_scholes import bs_delta, bs_price, implied_vol
from .jsmath import js_round
from .expected_move import prob_above
from .levels import Level

__all__ = [
    "PresetId", "WheelPreset", "WHEEL_PRESETS",
    "PremiumSource", "HAIRCUT", "PremiumPick", "pick_premium",
    "WheelBlockReason", "MAX_SPREAD_PCT", "MIN_OI",
    "spread_pct_of", "liquidity_block",
    "WheelMetrics", "wheel_metrics",
    "EarningsFlag", "ScorePart", "WheelScore", "ScoreInput", "score_candidate",
    "ChainQuote", "IvSource", "WheelCandidate", "CandidatesInput",
    "atm_iv", "wheel_candidates",
]

_MULTIPLIER = 100

# ── Presets ──────────────────────────────────────────────────────────────

PresetId = Literal["conservador", "balanceado", "agresivo"]


@dataclass(frozen=True)
class WheelPreset:
    id: PresetId
    label: str
    #: |delta| objetivo del put a vender.
    delta_min: float
    delta_max: float
    dte_min: int
    dte_max: int
    #: % de la prima al que conviene recomprar y cerrar.
    take_profit_pct: int
    #: DTE al que conviene rolar en vez de esperar.
    roll_dte: int
    explain: str


WHEEL_PRESETS: dict[str, WheelPreset] = {
    "conservador": WheelPreset(
        id="conservador", label="Conservador",
        delta_min=0.10, delta_max=0.20, dte_min=30, dte_max=45,
        take_profit_pct=50, roll_dte=21,
        explain="Strikes lejos del precio: cobras menos, pero te asignan pocas veces."),
    "balanceado": WheelPreset(
        id="balanceado", label="Balanceado",
        delta_min=0.20, delta_max=0.30, dte_min=30, dte_max=45,
        take_profit_pct=50, roll_dte=21,
        explain="El punto medio clásico de la Wheel: prima decente y asignación ocasional."),
    "agresivo": WheelPreset(
        id="agresivo", label="Agresivo",
        delta_min=0.30, delta_max=0.40, dte_min=7, dte_max=21,
        take_profit_pct=50, roll_dte=7,
        explain="Cerca del precio y a poco plazo: cobras más y te asignan mucho más seguido."),
}

# ── Cascada de prima ─────────────────────────────────────────────────────

PremiumSource = Literal["bid", "ultimo", "modelo"]

#: Recorte por fuente. Existe porque **vendes al bid**: un mid o un último
#: precio te haría creer que cobras más de lo que realmente cobrarías.
HAIRCUT: dict[str, float] = {"bid": 0, "ultimo": 0.10, "modelo": 0.15}


@dataclass(frozen=True)
class PremiumPick:
    #: Prima por acción ya recortada — la que se usa en todos los cálculos.
    price: float
    source: PremiumSource
    #: El valor antes del recorte, para poder mostrarlo.
    raw: float


def pick_premium(bid=None, ask=None, last_trade=None, model=None) -> PremiumPick | None:
    """`bid ?? ultimo ?? modelo`, cada uno con su recorte."""
    def _pick(raw, source: str) -> PremiumPick | None:
        if raw is not None and isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
            return PremiumPick(price=raw * (1 - HAIRCUT[source]), source=source, raw=float(raw))
        return None

    return _pick(bid, "bid") or _pick(last_trade, "ultimo") or _pick(model, "modelo")


# ── Liquidez: la salvaguarda ─────────────────────────────────────────────

WheelBlockReason = Literal["sin_bid", "spread_ancho", "oi_bajo"]

MAX_SPREAD_PCT = 25
MIN_OI = 100


def _pos(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def spread_pct_of(bid, ask) -> float | None:
    """Spread relativo al mid, en %. `None` si falta un lado de la horquilla."""
    if not _pos(bid) or not _pos(ask) or ask < bid:
        return None
    mid = (bid + ask) / 2
    if not mid > 0:
        return None
    return ((ask - bid) / mid) * 100


def liquidity_block(bid, ask, open_interest) -> WheelBlockReason | None:
    """Regla crítica del proyecto: ante la duda, **no operar y avisar**.

    Un candidato bloqueado se muestra SIN número de prima y fuera de la lista
    de operables — no se enseña un número que no puedes cobrar.
    """
    if not _pos(bid):
        return "sin_bid"
    spread = spread_pct_of(bid, ask)
    if spread is None or spread > MAX_SPREAD_PCT:
        return "spread_ancho"
    if open_interest < MIN_OI:
        return "oi_bajo"
    return None


# ── Métricas del candidato ───────────────────────────────────────────────

@dataclass(frozen=True)
class WheelMetrics:
    credit: float            # prima que cobras por un contrato, en $
    collateral: float        # efectivo que queda inmovilizado, en $
    return_pct: float        # retorno sobre el colateral en el periodo, en %
    annualized_pct: float    # el mismo retorno llevado a un año, en %
    breakeven: float         # por debajo de este precio empiezas a perder
    cushion_pct: float       # distancia del spot al breakeven, en % del spot
    prob_expire_worthless: float  # P(expire sin valor), 0-100


def wheel_metrics(strike: float, price: float, spot: float, dte: float, iv: float) -> WheelMetrics:
    credit = price * _MULTIPLIER
    collateral = strike * _MULTIPLIER
    return_pct = (credit / collateral) * 100 if collateral > 0 else 0.0
    # Un DTE de 0 haría infinito el anualizado: se trata como 1 día.
    annualized_pct = return_pct * (365 / max(dte, 1))
    breakeven = strike - price
    cushion_pct = ((spot - breakeven) / spot) * 100 if spot > 0 else 0.0
    return WheelMetrics(
        credit=credit, collateral=collateral, return_pct=return_pct,
        annualized_pct=annualized_pct, breakeven=breakeven, cushion_pct=cushion_pct,
        prob_expire_worthless=prob_above(spot, strike, iv, dte) * 100)


# ── Score compuesto Wheel (0-100) ────────────────────────────────────────

EarningsFlag = Literal["fuera", "dentro", "dentro_confirmado", "no_aplica"]


@dataclass(frozen=True)
class ScorePart:
    points: int
    max: int
    band: str
    why: str   # por qué, en llano. Se muestra tal cual en la UI.


@dataclass(frozen=True)
class WheelScore:
    total: int
    annualized: ScorePart
    iv_rank: ScorePart
    cushion: ScorePart
    liquidity: ScorePart
    earnings: ScorePart


@dataclass(frozen=True)
class ScoreInput:
    annualized_pct: float
    iv_rank: float | None      # 0-100. None si no hay historia suficiente
    strike: float
    spot: float
    cushion_pct: float
    supports: Sequence[Level]  # soportes del ticker, de find_levels
    open_interest: int
    spread_pct: float | None
    earnings: EarningsFlag


#: Fuerza mínima para considerar que un soporte de verdad sostiene.
_STRONG_SUPPORT = 35


def _annualized_part(pct: float) -> ScorePart:
    # El castigo por encima del 60% es DELIBERADO: un screener que ordena por
    # prima pone arriba justo las acciones que están a punto de desplomarse.
    if pct > 60:
        return ScorePart(10, 30, ">60%",
                         "Prima sospechosamente alta: el mercado descuenta una caída fuerte.")
    if pct >= 35:
        return ScorePart(22, 30, "35-60%",
                         "Rendimiento muy alto — bien pagado, pero comprueba por qué paga tanto.")
    if pct >= 15:
        return ScorePart(30, 30, "15-35%", "Rendimiento en el rango sano para vender puts.")
    if pct >= 8:
        return ScorePart(18, 30, "8-15%", "Rendimiento modesto pero razonable.")
    return ScorePart(5, 30, "<8%",
                     "Lo que cobras no paga el riesgo de quedarte con las acciones.")


def _iv_rank_part(rank: float | None) -> ScorePart:
    # OJO: banda INVERTIDA respecto a ivcontext.py. Allí el pico está en 16-30
    # porque el resto del agente COMPRA opciones y quiere vega barata. La Wheel
    # VENDE: quiere que la volatilidad esté cara.
    if rank is None:
        return ScorePart(4, 20, "sin datos",
                         "Sin historia suficiente para saber si la volatilidad está cara o barata.")
    if rank > 70:
        return ScorePart(20, 20, ">70",
                         "La volatilidad está cara frente a su propio año: buen momento para vender prima.")
    if rank >= 50:
        return ScorePart(16, 20, "50-70", "Volatilidad por encima de su media anual.")
    if rank >= 30:
        return ScorePart(10, 20, "30-50", "Volatilidad en su zona media.")
    return ScorePart(4, 20, "<30",
                     "La volatilidad está barata: te pagan poco por asumir el riesgo.")


def _cushion_part(inp: ScoreInput) -> ScorePart:
    below = [s for s in inp.supports if s.price >= inp.strike]
    strongest: Level | None = None
    for s in below:
        if strongest is None or s.strength > strongest.strength:
            strongest = s

    if strongest is not None and strongest.strength >= _STRONG_SUPPORT:
        # `Math.round(...)` de JS, no el redondeo de Python: el suyo va medio
        # arriba y el nuestro es bancario. Una fuerza de 34.5 se leería 34 aquí
        # y 35 en su pantalla — el mismo soporte, descrito con dos números.
        return ScorePart(25, 25, "bajo soporte fuerte",
                         f"El strike queda bajo un soporte de fuerza {js_round(strongest.strength)}: "
                         f"el precio ya rebotó ahí antes.")
    if strongest is not None:
        return ScorePart(15, 25, "bajo soporte débil",
                         "El strike queda bajo un soporte, pero flojo.")
    if inp.cushion_pct > 10:
        return ScorePart(12, 25, "colchón >10%",
                         "Sin soporte identificado, pero la acción tendría que caer más de un "
                         "10% para hacerte daño.")
    return ScorePart(5, 25, "sin colchón",
                     "El strike está por encima del soporte más cercano: te pueden asignar con facilidad.")


def _liquidity_part(oi: int, spread_pct: float | None) -> ScorePart:
    # Las bandas se evalúan EN ORDEN y gana la primera que se cumple: un OI de
    # 800 con spread del 20% cae a la tercera, no cobra la primera.
    s = spread_pct if spread_pct is not None else math.inf
    if oi >= 500 and s <= 10:
        return ScorePart(15, 15, "excelente",
                         "Contrato muy negociado y con horquilla estrecha: entras y sales sin regalar dinero.")
    if oi >= 250 and s <= 15:
        return ScorePart(10, 15, "buena", "Liquidez suficiente para entrar y salir.")
    if oi >= MIN_OI and s <= MAX_SPREAD_PCT:
        return ScorePart(5, 15, "justa", "Liquidez ajustada: la horquilla te va a costar al cerrar.")
    return ScorePart(0, 15, "insuficiente", "Liquidez insuficiente.")


def _earnings_part(flag: EarningsFlag) -> ScorePart:
    if flag == "no_aplica":
        return ScorePart(10, 10, "no aplica", "No reporta resultados: no hay riesgo de reporte.")
    if flag == "fuera":
        return ScorePart(10, 10, "fuera", "El reporte estimado cae después del vencimiento.")
    if flag == "dentro":
        return ScorePart(3, 10, "dentro",
                         "El reporte estimado cae ANTES del vencimiento — es una estimación, verifícala.")
    return ScorePart(0, 10, "dentro, confirmado",
                     "El reporte cae antes del vencimiento y la volatilidad del frente lo confirma.")


def score_candidate(inp: ScoreInput) -> WheelScore:
    annualized = _annualized_part(inp.annualized_pct)
    iv_rank = _iv_rank_part(inp.iv_rank)
    cushion = _cushion_part(inp)
    liquidity = _liquidity_part(inp.open_interest, inp.spread_pct)
    earnings = _earnings_part(inp.earnings)
    return WheelScore(
        total=annualized.points + iv_rank.points + cushion.points
              + liquidity.points + earnings.points,
        annualized=annualized, iv_rank=iv_rank, cushion=cushion,
        liquidity=liquidity, earnings=earnings)


# ── Ensamblado de candidatos ─────────────────────────────────────────────

@dataclass(frozen=True)
class ChainQuote:
    """Una fila de la cadena, ya normalizada desde el snapshot de Massive."""

    strike: float
    expiration: str      # YYYY-MM-DD
    dte: float
    bid: float | None
    ask: float | None
    last_trade: float | None
    open_interest: int


IvSource = Literal["implicita", "estimada"]


@dataclass(frozen=True)
class WheelCandidate:
    ticker: str
    strike: float
    expiration: str
    dte: float
    spot: float
    delta: float          # negativo (es un put). 0 si no se pudo calcular
    iv: float             # IV decimal usada en todos los cálculos de esta fila
    iv_source: IvSource
    open_interest: int
    spread_pct: float | None
    #: `None` si el candidato está bloqueado: no se muestra prima que no puedes cobrar.
    premium: PremiumPick | None
    metrics: WheelMetrics | None
    score: WheelScore | None
    blocked: bool
    block_reason: WheelBlockReason | None


@dataclass(frozen=True)
class CandidatesInput:
    ticker: str
    spot: float
    quotes: Sequence[ChainQuote]
    preset: WheelPreset
    iv_rank: float | None
    supports: Sequence[Level]
    earnings: EarningsFlag
    #: IV de respaldo (volatilidad realizada) cuando la bisección no converge.
    fallback_iv: float
    #: **DIVERGENCIA DECLARADA.** Deja pasar el contrato sin `bid` usando la
    #: cascada de prima que él mismo escribió.
    #:
    #: Su `wheelCandidates` bloquea por `sin_bid` ANTES de llamar a
    #: `pickPremium`, así que las ramas `ultimo` y `modelo` de esa cascada
    #: —con sus recortes del 10% y 15%— son **inalcanzables** en su código. No
    #: tiene sentido escribir dos recortes que nunca se aplican, y su propio
    #: `compute.ts` explica por qué existen::
    #:
    #:     "La fórmula del agente pide BID, pero el plan actual de Massive NO
    #:      devuelve quotes, así que cae a last_trade → day.close → day.vwap."
    #:
    #: Sin esto, con una fuente sin horquilla el screener sale SIEMPRE vacío.
    #: Con esto no se pierde la salvaguarda: el `spread_pct` sigue siendo
    #: `None`, y `_liquidity_part` ya sabe tratarlo —`None` → `inf` → banda
    #: "insuficiente", **0 de 15 puntos**—. O sea que el propio score castiga
    #: no saber la liquidez, que es exactamente lo que el bloqueo protegía.
    #: `False` por defecto: su comportamiento literal es el que se prueba.
    allow_missing_quote: bool = False


def atm_iv(rows: Sequence, spot: float) -> float | None:
    """IV del strike más cercano al spot — el proxy de "la IV de esta cadena"."""
    if not rows:
        return None
    best = rows[0]
    for b in rows[1:]:
        if abs(b.strike - spot) < abs(best.strike - spot):
            best = b
    return best.iv


def wheel_candidates(inp: CandidatesInput) -> list[WheelCandidate]:
    if not inp.spot > 0:
        return []

    out: list[WheelCandidate] = []
    p = inp.preset

    for q in inp.quotes:
        if q.dte < p.dte_min or q.dte > p.dte_max:
            continue

        T = max(q.dte, 1) / 365
        mid = ((q.bid + q.ask) / 2
               if _pos(q.bid) and _pos(q.ask) else None)

        implied = implied_vol(mid, inp.spot, q.strike, T, "put") if mid is not None else None
        iv = implied if implied is not None else inp.fallback_iv
        iv_source: IvSource = "implicita" if implied is not None else "estimada"

        delta = bs_delta(inp.spot, q.strike, T, iv, "put")
        abs_delta = abs(delta)
        if abs_delta < p.delta_min or abs_delta > p.delta_max:
            continue

        spread_pct = spread_pct_of(q.bid, q.ask)
        block_reason = liquidity_block(q.bid, q.ask, q.open_interest)

        # Sin horquilla, pero con un precio real de la cascada: se deja pasar y
        # el score se encarga. Ver `allow_missing_quote`.
        if (block_reason == "sin_bid" and inp.allow_missing_quote
                and _pos(q.last_trade)):
            block_reason = None

        if block_reason:
            # Bloqueado: sin prima y sin métricas. No se enseña un número que
            # no puedes cobrar.
            out.append(WheelCandidate(
                ticker=inp.ticker, strike=q.strike, expiration=q.expiration, dte=q.dte,
                spot=inp.spot, delta=delta, iv=iv, iv_source=iv_source,
                open_interest=q.open_interest, spread_pct=spread_pct,
                premium=None, metrics=None, score=None,
                blocked=True, block_reason=block_reason))
            continue

        premium = pick_premium(bid=q.bid, ask=q.ask, last_trade=q.last_trade,
                               model=bs_price(inp.spot, q.strike, T, iv, "put"))
        if premium is None:
            continue

        metrics = wheel_metrics(strike=q.strike, price=premium.price, spot=inp.spot,
                                dte=q.dte, iv=iv)
        score = score_candidate(ScoreInput(
            annualized_pct=metrics.annualized_pct, iv_rank=inp.iv_rank,
            strike=q.strike, spot=inp.spot, cushion_pct=metrics.cushion_pct,
            supports=inp.supports, open_interest=q.open_interest,
            spread_pct=spread_pct, earnings=inp.earnings))

        out.append(WheelCandidate(
            ticker=inp.ticker, strike=q.strike, expiration=q.expiration, dte=q.dte,
            spot=inp.spot, delta=delta, iv=iv, iv_source=iv_source,
            open_interest=q.open_interest, spread_pct=spread_pct,
            premium=premium, metrics=metrics, score=score,
            blocked=False, block_reason=None))

    # Operables primero, y dentro de ellos el mejor score.
    return sorted(out, key=lambda c: (c.blocked, -(c.score.total if c.score else 0)))
