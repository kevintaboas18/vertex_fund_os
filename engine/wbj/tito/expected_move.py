"""Movimiento esperado por desviación estándar + probabilidades por nivel.

Port de `web/lib/expectedMove.ts`.

Reemplaza a las "burbujas" por dos cosas medibles:

1. Un **cono de proyección**: hasta dónde puede llegar el precio a 1σ y 2σ.
2. Una **probabilidad por nivel** de precio, para pesar el heatmap.

Modelo: movimiento browniano geométrico **sin deriva** (r = 0), que es la
convención del mercado de opciones para el "expected move". Todo aquí es puro y
testeable.

Divergencia deliberada vs. el original TypeScript: `norm_cdf` usa `math.erf`
(exacto a doble precisión) en lugar de la aproximación de Abramowitz & Stegun
7.1.26 (|ε| < 7.5e-8) que usa el TS por no tener `erf` en el runtime. El
resultado es el mismo dentro de 1e-7 y aquí es estrictamente más preciso.
"""

from __future__ import annotations

import math

from .jsmath import js_gt, js_number
from dataclasses import dataclass
from typing import Literal, Sequence

__all__ = [
    "DAYS_PER_YEAR",
    "norm_cdf",
    "ExpectedMove",
    "expected_move",
    "ConePoint",
    "cone_points",
    "prob_above",
    "prob_in_band",
    "prob_touch",
    "LevelInput",
    "LevelProb",
    "level_probabilities",
    "PredictionPath",
    "prediction_path",
]

#: El mercado anualiza la IV sobre días naturales, no hábiles.
DAYS_PER_YEAR = 365

OptionSide = Literal["call", "put"]


def norm_cdf(x: float) -> float:
    """Normal acumulada estándar N(x)."""
    if not math.isfinite(x):
        return 1.0 if x > 0 else 0.0
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class ExpectedMove:
    """Bandas de movimiento esperado a un horizonte dado."""

    spot: float
    iv: float  # decimal (0.55 = 55%)
    days: float
    sigma: float  # desviación estándar del movimiento, en $
    sigma_pct: float  # y en % del spot
    upper1: float
    lower1: float  # ~68% de probabilidad
    upper2: float
    lower2: float  # ~95%


def _exp(x: float) -> float:
    """`Math.exp(x)` de JS: desborda a `Infinity`, no lanza `OverflowError`.

    Con una IV de 1e308 el exponente se va de rango. Su motor devuelve
    `Infinity` y sigue; el port se caía y con él los tres escenarios.
    """
    try:
        return math.exp(x)
    except OverflowError:
        return math.inf


def expected_move(spot: float, iv: float, days: float) -> ExpectedMove:
    """Movimiento esperado a `days` días.

    ``σ = S · IV · √(T/365)`` — la fórmula que usan los brokers para el
    "expected move". Las bandas usan lognormal (``exp``) para que el suelo nunca
    cruce cero.
    """
    # `js_number` / `js_gt`: en TS estas comparaciones y productos coaccionan
    # solos y un valor ilegible los hace falsos o `NaN`; en Python lanzan y se
    # llevan el sub-agente entero. Lo destapó el corpus malformado de
    # `diff_motor2.sh`. Con datos bien formados no cambia nada.
    spot, iv, days = js_number(spot), js_number(iv), js_number(days)
    T = max(days, 0.0) / DAYS_PER_YEAR
    safe_iv = max(iv, 0.01)
    sd = safe_iv * math.sqrt(T)  # desviación en log-espacio
    sigma = spot * sd
    return ExpectedMove(
        spot=spot,
        iv=safe_iv,
        days=days,
        sigma=sigma,
        sigma_pct=(sigma / spot * 100.0) if spot > 0 else 0.0,
        upper1=spot * _exp(sd),
        lower1=spot * _exp(-sd),
        upper2=spot * _exp(2 * sd),
        lower2=spot * _exp(-2 * sd),
    )


@dataclass(frozen=True)
class ConePoint:
    """Un punto del cono de proyección."""

    t: float  # sesiones/días desde hoy
    mid: float
    upper1: float
    lower1: float
    upper2: float
    lower2: float


def cone_points(spot: float, iv: float, days: float, steps: int = 20) -> list[ConePoint]:
    """Cono de proyección: la banda se abre con √t, no en línea recta."""
    n = max(1, int(steps))
    out: list[ConePoint] = []
    for i in range(n + 1):
        t = days * i / n
        em = expected_move(spot, iv, t)
        out.append(
            ConePoint(
                t=t,
                mid=spot,
                upper1=em.upper1,
                lower1=em.lower1,
                upper2=em.upper2,
                lower2=em.lower2,
            )
        )
    return out


def prob_above(spot: float, strike: float, iv: float, days: float) -> float:
    """P(S_T > K) bajo lognormal sin deriva."""
    spot, strike = js_number(spot), js_number(strike)
    if not (spot > 0) or not (strike > 0):
        return 0.0
    T = max(js_number(days), 0.0) / DAYS_PER_YEAR
    sd = max(js_number(iv), 0.01) * math.sqrt(T)
    if sd <= 0:
        return 1.0 if spot > strike else 0.0
    # `Math.log(0)` en JS es -Infinity; en Python es un `ValueError`.
    d2 = ((-math.inf if spot / strike == 0 else math.log(spot / strike))
          - 0.5 * sd * sd) / sd
    return norm_cdf(d2)


def prob_in_band(spot: float, low: float, high: float, iv: float, days: float) -> float:
    """P(el precio termine dentro de [low, high]) al vencimiento del horizonte."""
    a = prob_above(spot, min(low, high), iv, days)
    b = prob_above(spot, max(low, high), iv, days)
    return max(0.0, a - b)


def prob_touch(spot: float, strike: float, iv: float, days: float) -> float:
    """P(el precio TOQUE el nivel en algún momento antes del horizonte).

    Principio de reflexión: para una barrera, la probabilidad de tocarla es ~2×
    la de terminar más allá. Por eso un muro cercano se toca mucho más de lo que
    sugiere la probabilidad de cierre.
    """
    spot, strike = js_number(spot), js_number(strike)
    if not (spot > 0) or not (strike > 0):
        return 0.0
    if abs(strike - spot) < 1e-9:
        return 1.0
    beyond = (
        prob_above(spot, strike, iv, days)
        if strike > spot
        else 1.0 - prob_above(spot, strike, iv, days)
    )
    return min(1.0, 2.0 * beyond)


@dataclass(frozen=True)
class LevelInput:
    """Nivel candidato: strike + cuánto dinero de GEX concentra."""

    strike: float
    concentration: float
    side: OptionSide
    net_gex: float


@dataclass(frozen=True)
class LevelProb:
    """Nivel con sus probabilidades y su peso normalizado en el heatmap."""

    strike: float
    touch: float  # probabilidad estadística de tocar el nivel (0-1)
    band: float  # probabilidad de cerrar en la banda de ese strike (0-1)
    concentration: float  # concentración de dinero del GEX (0-1)
    magnet: float  # mezcla normalizada estadística × posicionamiento (0-1)
    side: OptionSide
    net_gex: float


def level_probabilities(
    spot: float,
    iv: float,
    days: float,
    levels: Sequence[LevelInput],
    band_width: float | None = None,
) -> list[LevelProb]:
    """Probabilidad por nivel para el heatmap.

    No basta la estadística pura: un strike lejano con un muro enorme importa
    más que uno cercano vacío. Se mezcla la probabilidad de toque con la
    concentración de dinero (el "imán" del GEX) y se normaliza para que los
    niveles sumen 100%.
    """
    if not levels or not js_gt(spot):
        return []

    # `sort((a, b) => a.strike - b.strike)` — resta numérica.
    spot = js_number(spot)
    ordered = sorted(levels, key=lambda l: js_number(l.strike))
    # Ancho de banda = separación TÍPICA (mediana) entre strikes, no la media:
    # un solo strike raro no debe ensanchar la banda de todos los demás.
    gaps = sorted(
        g for g in (js_number(b.strike) - js_number(a.strike)
                    for a, b in zip(ordered, ordered[1:])) if g > 0
    )
    width = band_width if band_width is not None else (gaps[len(gaps) // 2] if gaps else spot * 0.01)

    raw: list[LevelProb] = []
    for l in ordered:
        touch = prob_touch(spot, js_number(l.strike), iv, days)
        _k = js_number(l.strike)
        band = prob_in_band(spot, _k - width / 2, _k + width / 2, iv, days)
        raw.append(
            LevelProb(
                strike=l.strike,
                touch=touch,
                band=band,
                concentration=l.concentration,
                # El piso de 0.01 evita que un nivel sin GEX quede con imán 0 y
                # desaparezca del reparto: sigue siendo alcanzable, solo pesa poco.
                magnet=touch * max(js_number(l.concentration), 0.01),
                side=l.side,
                net_gex=l.net_gex,
            )
        )

    total = sum(l.magnet for l in raw)
    scaled = [
        LevelProb(
            strike=l.strike,
            touch=l.touch,
            band=l.band,
            concentration=l.concentration,
            magnet=(l.magnet / total) if total > 0 else 0.0,
            side=l.side,
            net_gex=l.net_gex,
        )
        for l in raw
    ]
    return sorted(scaled, key=lambda l: l.magnet, reverse=True)


@dataclass(frozen=True)
class PredictionPath:
    """Ruta esperada hacia el objetivo, ya recortada al cono."""

    target: float  # precio objetivo (nodo imán del GEX, acotado por el cono de 2σ)
    points: list[tuple[float, float]]  # (t, precio) — camino suavizado desde el spot
    clamped: bool


def prediction_path(
    spot: float,
    target: float,
    iv: float,
    days: float,
    steps: int = 12,
) -> PredictionPath:
    """Ruta esperada hacia el objetivo.

    Avanza rápido al principio y se aplana al final (raíz cuadrada del tiempo),
    igual que la difusión real de un precio. Si el objetivo cae fuera del cono de
    2σ se recorta: **el precio no puede llegar a donde la volatilidad no da.**
    """
    em = expected_move(spot, iv, days)
    clamped_target = min(max(target, em.lower2), em.upper2)
    clamped = abs(clamped_target - target) > 1e-9
    n = max(1, int(steps))
    points = [
        (days * (i / n), spot + (clamped_target - spot) * math.sqrt(i / n))
        for i in range(n + 1)
    ]
    return PredictionPath(target=clamped_target, points=points, clamped=clamped)
