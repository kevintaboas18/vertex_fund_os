"""Sub-agente 5 — Contexto IV ("¿IV limpia o inflada?", 10% del scorecard).

Port de `web/lib/ivcontext.ts`. Fuente: `SCOREDCARD/Contexto-IV.md`.

El documento pide dos cosas:

1. El **promedio de IV por fecha de vencimiento** y los contratos de mayor IV
   → sirve para ver si un movimiento está a punto de pasar.
2. El **IV Rank** → dónde está la IV de hoy dentro de su propio rango histórico,
   es decir si el movimiento se está comprimiendo o expandiendo.

Nota de escala: MarketSnack entrega la IV en **decimal** (0.477 = 47.7%). Aquí
se trabaja SIEMPRE en **porcentaje**, que es la unidad de las tablas del
documento. Mezclar las dos unidades es el error más fácil de cometer y el más
difícil de ver: un 0.47 leído como 0.47% cae en la banda "muy baja" en vez de
"la zona buena".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

from .jsmath import js_round
from .flow import FlowRow

__all__ = [
    "TRADING_DAYS",
    "MIN_IV_HISTORY_DAYS",
    "IvBand",
    "iv_points",
    "iv_rank_points",
    "realized_vol_series",
    "rank_within",
    "IvExpirationStat",
    "IvContract",
    "IvContextScore",
    "iv_context_score",
]

TRADING_DAYS = 252

#: Días de historia propia a partir de los cuales el IV Rank real desplaza al proxy.
MIN_IV_HISTORY_DAYS = 60

IvRankSource = Literal["iv-history", "realized-proxy", "none"]
IvRegime = Literal["dormida", "compresion", "expansion", "normal", "inflada", "desconocido"]


@dataclass(frozen=True)
class IvBand:
    points: int
    band: str
    #: El documento marca +100% como "categoría especial".
    special: bool = False


def iv_points(iv_pct: float) -> IvBand:
    """Parámetro 1 — Implied Volatility actual.

    El pico está en la IV **moderada (40-60%)**: hay movimiento esperado y la
    prima todavía es razonable. Hacia arriba baja porque comprar volatilidad
    cara castiga aunque el precio acompañe (riesgo de IV crush), y hacia abajo
    baja porque no se espera movimiento ninguno.
    """
    if not math.isfinite(iv_pct) or iv_pct < 0:
        return IvBand(0, "sin datos")
    if iv_pct >= 100:
        return IvBand(6, "≥100% — prima muy inflada", special=True)
    if iv_pct >= 90:
        return IvBand(5, "90-99% — cara")
    if iv_pct >= 61:
        return IvBand(8, "61-89% — elevada")
    if iv_pct >= 40:
        return IvBand(10, "40-60% — moderada, la zona buena")
    if iv_pct >= 30:
        return IvBand(5, "30-39% — baja")
    return IvBand(2, "<30% — muy baja, sin movimiento esperado")


def iv_rank_points(rank: float) -> IvBand:
    """Parámetro 2 — IV Rank (0-100): dónde cae la IV de hoy en su rango histórico.

    El pico está en **16-30%**: IV comprimida pero despertando. Por debajo de 15
    la acción está dormida, y por encima de 70 la volatilidad ya está estirada,
    con poco recorrido y riesgo de desplome de prima.
    """
    if not math.isfinite(rank) or rank < 0:
        return IvBand(0, "sin datos")
    if rank >= 100:
        return IvBand(0, "100% — techo histórico")
    if rank >= 71:
        return IvBand(1, "71-99% — IV estirada")
    if rank >= 51:
        return IvBand(5, "51-70% — por encima de su media")
    if rank >= 31:
        return IvBand(8, "31-50% — en su media")
    if rank >= 16:
        return IvBand(10, "16-30% — comprimida, lista para expandir")
    return IvBand(2, "0-15% — dormida, sin movimiento")


# ---------- volatilidad realizada (proxy del IV Rank) ----------


def realized_vol_series(closes: Sequence[float], window: int = 30) -> list[float]:
    """Serie de volatilidad realizada anualizada (%) en ventana móvil.

    Es el proxy del IV histórico: nadie nos vende una serie de IV de 52 semanas,
    pero la volatilidad que la acción REALMENTE tuvo sí la podemos calcular.
    """
    if len(closes) < window + 2:
        return []
    rets: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    out: list[float] = []
    for i in range(window, len(rets) + 1):
        window_rets = rets[i - window : i]
        mean = sum(window_rets) / len(window_rets)
        var = sum((r - mean) ** 2 for r in window_rets) / (len(window_rets) - 1)
        out.append(math.sqrt(var * TRADING_DAYS) * 100)
    return out


def rank_within(series: Sequence[float], current: float) -> float | None:
    """Posición de `current` dentro de [min, max] de la serie, en 0-100."""
    if len(series) < 2:
        return None
    low, high = min(series), max(series)
    if not (high > low):
        return None
    return max(0.0, min(100.0, (current - low) / (high - low) * 100))


# ---------- reporte ----------


@dataclass(frozen=True)
class IvExpirationStat:
    expiration: str
    dte: int | None
    trades: int
    avg_iv: float
    max_iv: float
    premium: float


@dataclass(frozen=True)
class IvContract:
    id: int
    symbol: str
    strike: float | None
    type: str
    expiration: str | None
    dte: int | None
    iv: float
    premium: float
    size: int


@dataclass
class IvContextScore:
    score: int
    iv: dict
    rank: dict
    by_expiration: list[IvExpirationStat]
    top_contracts: list[IvContract]
    front_skew: float | None
    regime: IvRegime
    note: str


_EMPTY = IvContextScore(
    score=0,
    iv={
        "current": None, "simple_avg": None, "min": None, "max": None,
        "contracts": 0, "points": 0, "band": "sin datos", "special": False,
    },
    rank={
        "value": None, "source": "none", "days": 0, "low": None, "high": None,
        "reference": None, "points": 0, "band": "sin datos",
    },
    by_expiration=[],
    top_contracts=[],
    front_skew=None,
    regime="desconocido",
    note="Sin trades con volatilidad implícita en la ventana.",
)

_NOTE: dict[str, str] = {
    "dormida": (
        "IV pegada al piso de su propio rango: el mercado no espera movimiento. Las opciones "
        "están baratas, pero baratas porque nadie cree que pase nada."
    ),
    "compresion": (
        "IV comprimida contra su propio rango: las opciones están baratas y el movimiento lleva "
        "rato guardado. Es el terreno donde una apuesta direccional paga mejor si acierta."
    ),
    "expansion": (
        "IV estirada en la parte alta de su rango: el mercado ya está pagando caro el movimiento. "
        "Comprar aquí carga con el riesgo de que la prima se desinfle aunque el precio acompañe."
    ),
    "inflada": (
        "IV por encima de 100%: prima extremadamente inflada. Normalmente hay un evento encima y "
        "suele estar descontado — el riesgo de IV crush después del anuncio es alto."
    ),
    "normal": "IV en la zona media de su rango histórico: ni regalada ni estirada.",
    "desconocido": "No hay suficiente historia para situar la IV en su rango.",
}


def iv_context_score(
    rows: Sequence[FlowRow],
    closes: Sequence[float],
    iv_history: Sequence[dict] | None = None,
) -> IvContextScore:
    """Contexto IV (0-10) = promedio de los puntos de IV actual y de IV Rank.

    `iv_history` es la historia propia acumulada día a día (``{date, avg_iv}``);
    cuando alcanza `MIN_IV_HISTORY_DAYS` desplaza sola al proxy de volatilidad
    realizada, sin que nadie tenga que cambiar nada.
    """
    iv_history = iv_history or []

    with_iv = [r for r in rows if math.isfinite(r.iv) and r.iv > 0]
    if not with_iv:
        return _EMPTY

    def iv_pct(r: FlowRow) -> float:
        return r.iv * 100

    # IV representativa = ponderada por premium. El dinero grande define el
    # contexto; un promedio simple lo dominarían los cientos de tickets
    # pequeños de 0DTE.
    w_sum = w_iv = plain = 0.0
    lo, hi = math.inf, -math.inf
    for r in with_iv:
        v = iv_pct(r)
        w = max(r.premium, 1.0)
        w_sum += w
        w_iv += v * w
        plain += v
        lo = min(lo, v)
        hi = max(hi, v)
    current = w_iv / w_sum
    iv_band = iv_points(current)

    # --- promedio de IV por vencimiento (lo que pide el documento) ---
    by_exp_map: dict[str, dict] = {}
    for r in with_iv:
        if not r.expiration:
            continue
        e = by_exp_map.setdefault(r.expiration, {"dte": r.dte, "ivs": [], "premium": 0.0})
        e["ivs"].append(iv_pct(r))
        e["premium"] += r.premium
        if e["dte"] is None:
            e["dte"] = r.dte

    by_expiration = sorted(
        (
            IvExpirationStat(
                expiration=exp,
                dte=e["dte"],
                trades=len(e["ivs"]),
                avg_iv=sum(e["ivs"]) / len(e["ivs"]),
                max_iv=max(e["ivs"]),
                premium=e["premium"],
            )
            for exp, e in by_exp_map.items()
        ),
        key=lambda e: e.dte if e.dte is not None else 10**9,
    )

    # Skew del frente: si el vencimiento más cercano cotiza muy por encima del
    # resto, el mercado está pagando por un evento inminente.
    front_skew: float | None = None
    if len(by_expiration) >= 2:
        rest = by_expiration[1:]
        rest_avg = sum(e.avg_iv for e in rest) / len(rest)
        front_skew = by_expiration[0].avg_iv - rest_avg

    top_contracts = [
        IvContract(
            id=r.id, symbol=r.symbol, strike=r.strike, type=r.type,
            expiration=r.expiration, dte=r.dte, iv=iv_pct(r),
            premium=r.premium, size=r.size,
        )
        for r in sorted(with_iv, key=lambda r: r.iv, reverse=True)[:8]
    ]

    # --- IV Rank: historia propia si alcanza, si no el proxy de vol realizada ---
    rank_value: float | None = None
    source: IvRankSource = "none"
    days = 0
    low = high = reference = None

    if len(iv_history) >= MIN_IV_HISTORY_DAYS:
        series = [
            h["avg_iv"] for h in iv_history
            if isinstance(h.get("avg_iv"), (int, float))
            and math.isfinite(h["avg_iv"]) and h["avg_iv"] > 0
        ]
        rank_value = rank_within(series, current)
        if rank_value is not None:
            source, days = "iv-history", len(series)
            low, high, reference = min(series), max(series), current

    if rank_value is None:
        rv = realized_vol_series(closes)
        if len(rv) >= 2:
            today = rv[-1]
            rank_value = rank_within(rv, today)
            if rank_value is not None:
                source, days = "realized-proxy", len(rv)
                low, high, reference = min(rv), max(rv), today

    rank_band = iv_rank_points(rank_value) if rank_value is not None else IvBand(0, "sin datos")

    # Régimen: cruza el NIVEL de IV con su POSICIÓN histórica. Ninguno de los
    # dos solo alcanza — una IV de 45% puede ser cara o barata según su rango.
    regime: IvRegime = "desconocido"
    if rank_value is not None:
        if iv_band.special:
            regime = "inflada"
        elif rank_value < 16:
            regime = "dormida"
        elif rank_value <= 30:
            regime = "compresion"
        elif rank_value >= 71:
            regime = "expansion"
        else:
            regime = "normal"

    return IvContextScore(
        score=js_round((iv_band.points + rank_band.points) / 2),
        iv={
            "current": current,
            "simple_avg": plain / len(with_iv),
            "min": None if lo == math.inf else lo,
            "max": None if hi == -math.inf else hi,
            "contracts": len(with_iv),
            "points": iv_band.points,
            "band": iv_band.band,
            "special": bool(iv_band.special),
        },
        rank={
            "value": rank_value, "source": source, "days": days,
            "low": low, "high": high, "reference": reference,
            "points": rank_band.points, "band": rank_band.band,
        },
        by_expiration=by_expiration,
        top_contracts=top_contracts,
        front_skew=front_skew,
        regime=regime,
        note=_NOTE[regime],
    )
