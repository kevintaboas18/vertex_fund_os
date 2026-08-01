"""Soportes y resistencias por confluencia de precio y opciones.

Port de `web/lib/levels.ts`.

Cruza **dos fuentes independientes**, que es lo que separa un nivel real de una
coincidencia:

1. **Precio** — swing highs/lows (`find_pivots`) agrupados por tolerancia
   (`cluster_pivots`), con peso por frescura del último toque.
2. **Opciones** — según la tabla del Proceso Principal: **vender calls =
   resistencia, vender puts = soporte**. Por eso solo cuentan calls para
   resistencias y puts para soportes, y solo la ejecución al **bid** (venta)
   suma como muro. Comprar un call no construye techo; venderlo sí.

Los strikes sin rebote previo deben superar el **percentil 70 de open interest**
para entrar, o la lista se llena del ruido de cualquier strike con OI residual.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Sequence

from .jsmath import js_round

__all__ = [
    "LvlBar",
    "Pivot",
    "PivotCluster",
    "ChainLevel",
    "FlowLevel",
    "GexLevel",
    "LevelSource",
    "Level",
    "LevelsReport",
    "find_pivots",
    "cluster_pivots",
    "recency_factor",
    "find_levels",
]

LevelKind = Literal["soporte", "resistencia"]


@dataclass(frozen=True)
class LvlBar:
    time: str  # YYYY-MM-DD
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Pivot:
    price: float
    time: str
    kind: Literal["high", "low"]


@dataclass(frozen=True)
class PivotCluster:
    price: float
    touches: int
    last_touch: str
    highs: int
    lows: int


@dataclass(frozen=True)
class ChainLevel:
    strike: float
    contract_type: Literal["call", "put"]
    open_interest: int
    notional_value: float


@dataclass(frozen=True)
class FlowLevel:
    strike: float | None
    type: Literal["call", "put", "unknown"]
    aggression: str  # "ask" = comprado · "bid" = vendido
    premium: float


@dataclass(frozen=True)
class GexLevel:
    strike: float
    net_gex: float


@dataclass(frozen=True)
class LevelSource:
    touches: int
    last_touch: str | None
    open_interest: float
    notional: float
    flow_premium: float
    net_gex: float


@dataclass(frozen=True)
class Level:
    price: float
    kind: LevelKind
    strength: int  # 0-100
    distance_pct: float
    sources: LevelSource
    #: true si el precio ya lo perforó antes en el otro sentido.
    flipped: bool
    why: str  # por qué existe el nivel, en lenguaje llano


@dataclass(frozen=True)
class LevelsReport:
    spot: float
    supports: list[Level]  # más cercano primero
    resistances: list[Level]  # más cercano primero
    key_support: Level | None
    key_resistance: Level | None
    tolerance_pct: float


_EMPTY = LevelsReport(
    spot=0.0, supports=[], resistances=[],
    key_support=None, key_resistance=None, tolerance_pct=1.0,
)


def find_pivots(bars: Sequence[LvlBar], k: int = 3) -> list[Pivot]:
    """Swing highs / lows: una vela cuyo máximo (o mínimo) manda sobre las `k`
    velas de cada lado. Es la definición estándar de pivote y no requiere
    indicadores ni parámetros ajustados a mano.
    """
    out: list[Pivot] = []
    if len(bars) < k * 2 + 1:
        return out
    for i in range(k, len(bars) - k):
        is_high = is_low = True
        for j in range(i - k, i + k + 1):
            if j == i:
                continue
            if bars[j].high >= bars[i].high:
                is_high = False
            if bars[j].low <= bars[i].low:
                is_low = False
            if not is_high and not is_low:
                break
        if is_high:
            out.append(Pivot(price=bars[i].high, time=bars[i].time, kind="high"))
        if is_low:
            out.append(Pivot(price=bars[i].low, time=bars[i].time, kind="low"))
    return out


def cluster_pivots(pivots: Sequence[Pivot], tolerance_pct: float = 1) -> list[PivotCluster]:
    """Agrupa pivotes que caen casi en el mismo precio.

    Dos toques a $299 y $301 son el mismo nivel, no dos: `tolerance_pct` define
    cuánto es "casi".
    """
    if not pivots:
        return []
    ordered = sorted(pivots, key=lambda p: p.price)
    groups: list[list[Pivot]] = []
    current = [ordered[0]]

    for p in ordered[1:]:
        ref = current[0].price
        if abs(p.price - ref) / ref * 100 <= tolerance_pct:
            current.append(p)
        else:
            groups.append(current)
            current = [p]
    groups.append(current)

    return [
        PivotCluster(
            price=sum(p.price for p in g) / len(g),
            touches=len(g),
            last_touch=max(p.time for p in g),
            highs=sum(1 for p in g if p.kind == "high"),
            lows=sum(1 for p in g if p.kind == "low"),
        )
        for g in groups
    ]


def recency_factor(last_touch: str, now: datetime) -> float:
    """Peso por frescura del último toque: lo de hace un año ya no manda igual."""
    try:
        touched = date.fromisoformat(last_touch[:10])
    except (ValueError, TypeError):
        return 1.0
    days = (now.date() - touched).days
    if days < 0:
        return 1.0
    if days <= 30:
        return 1.0
    if days <= 90:
        return 0.75
    if days <= 180:
        return 0.5
    return 0.3


def find_levels(
    bars: Sequence[LvlBar],
    spot: float,
    now: datetime,
    chain: Sequence[ChainLevel] = (),
    flows: Sequence[FlowLevel] = (),
    gex: Sequence[GexLevel] = (),
    tolerance_pct: float = 1,
    rango_pct: float = 25,
) -> LevelsReport:
    """Construye la lista de soportes y resistencias con su fuerza 0-100.

    `rango_pct` acota a niveles dentro de ±% del spot: más allá no se opera.
    """
    if not (spot > 0) or not bars:
        return _EMPTY

    def near(a: float, b: float) -> bool:
        return abs(a - b) / spot * 100 <= tolerance_pct

    clusters = [
        c
        for c in cluster_pivots(find_pivots(bars), tolerance_pct)
        if abs(c.price - spot) / spot * 100 <= rango_pct
    ]

    # Strikes de opciones relevantes que aún no tienen pivote de precio: también
    # son niveles, aunque el precio nunca haya reaccionado ahí todavía.
    #
    # El ORDEN de este recorrido decide qué nivel sobrevive, y por eso es
    # `dict.fromkeys` y no un `set`. Dos strikes dentro de la tolerancia no
    # pueden coexistir —el segundo se descarta por `near`—, así que el primero
    # que se recorre gana. Su `new Set<number>()` itera en orden de INSERCIÓN,
    # o sea el orden en que los strikes vienen en la cadena.
    #
    # Aquí estaba `sorted(option_strikes)`. Es determinista, pero es OTRO orden:
    # con la cadena `[…, 170.08, …, 169.67, …]` y 2% de tolerancia, él conserva
    # 170.08 (el que viene antes) y el port conservaba 169.67 (el menor). El
    # panel dibujaba una resistencia en un precio distinto al suyo. Un `set` de
    # Python habría sido peor todavía: no conserva ningún orden.
    option_strikes = dict.fromkeys(
        c.strike for c in chain if abs(c.strike - spot) / spot * 100 <= rango_pct
    )

    candidates: list[tuple[float, PivotCluster | None]] = [(c.price, c) for c in clusters]
    for strike in option_strikes:
        if not any(near(p, strike) for p, _ in candidates):
            candidates.append((strike, None))

    max_touches = max([1, *(c.touches for c in clusters)])

    # Umbral de tamaño para los strikes SIN rebote previo.
    aligned_oi = sorted(
        oi
        for oi in (
            sum(
                c.open_interest
                for c in chain
                if near(c.strike, price)
                and (
                    c.contract_type == "call"
                    if price >= spot
                    else c.contract_type == "put"
                )
            )
            for price, _ in candidates
        )
        if oi > 0
    )
    oi_floor = aligned_oi[int(len(aligned_oi) * 0.7)] if aligned_oi else 0

    levels: list[Level] = []

    for price, cluster in candidates:
        kind: LevelKind = "soporte" if price < spot else "resistencia"

        # --- cadena de opciones en ese nivel ---
        oi = notional = 0.0
        for c in chain:
            if not near(c.strike, price):
                continue
            # Vender calls sostiene una resistencia; vender puts sostiene un soporte.
            aligned = (
                c.contract_type == "call" if kind == "resistencia" else c.contract_type == "put"
            )
            if not aligned:
                continue
            oi += c.open_interest
            notional += c.notional_value

        # --- flujo real ejecutado que apunta a ese nivel ---
        flow_premium = 0.0
        for f in flows:
            if f.strike is None or not near(f.strike, price):
                continue
            if f.aggression != "bid":
                continue  # solo la VENTA construye muro
            if kind == "resistencia" and f.type == "call":
                flow_premium += f.premium
            if kind == "soporte" and f.type == "put":
                flow_premium += f.premium

        # --- gamma del dealer ---
        net_gex = sum(g.net_gex for g in gex if near(g.strike, price))

        touches = cluster.touches if cluster else 0

        # Un strike donde el precio nunca reaccionó solo cuenta si el dinero es grande.
        if touches == 0 and flow_premium == 0 and oi < oi_floor:
            continue

        last_touch = cluster.last_touch if cluster else None
        recency = recency_factor(last_touch, now) if last_touch else 0.0

        # --- puntuación 0-100 ---
        p_touch = min(1.0, touches / max(2, max_touches)) * 35 * (0.4 + 0.6 * recency)
        p_oi = min(1.0, math.log10(1 + oi) / 5) * 25 if oi > 0 else 0.0
        p_flow = min(1.0, math.log10(1 + flow_premium) / 8) * 20 if flow_premium > 0 else 0.0
        p_gex = min(1.0, abs(net_gex) / 5e8) * 10
        # Confluencia: precio Y opciones a la vez. Es el bonus que separa un
        # nivel real de una coincidencia.
        confluence = 10 if touches > 0 and (oi > 0 or flow_premium > 0) else 0

        # `Math.round`, no el `round` de Python: el suyo redondea la mitad
        # hacia arriba y el de Python al par. Aquí salía una fuerza de 10
        # donde su archivo daba 11 — el hallazgo que destapó `jsmath`.
        strength = js_round(min(100.0, p_touch + p_oi + p_flow + p_gex + confluence))
        if strength < 8:
            continue  # ruido

        # Nivel "flipeado": tocado antes como techo y ahora actúa de suelo (o al revés).
        flipped = bool(
            cluster
            and (
                (kind == "soporte" and cluster.highs > cluster.lows)
                or (kind == "resistencia" and cluster.lows > cluster.highs)
            )
        )

        parts: list[str] = []
        if touches > 0:
            parts.append(f"el precio reaccionó {touches} {'vez' if touches == 1 else 'veces'} aquí")
        if oi > 0:
            lado = "calls" if kind == "resistencia" else "puts"
            parts.append(f"{js_round(oi):,} contratos abiertos de {lado}")
        if flow_premium > 0:
            lado = "calls" if kind == "resistencia" else "puts"
            parts.append(f"venta de {lado} por dinero real")
        if confluence > 0:
            parts.append("confluencia precio + opciones")
        if not parts:
            parts.append("posicionamiento de la cadena")

        levels.append(
            Level(
                price=price,
                kind=kind,
                strength=strength,
                distance_pct=(price - spot) / spot * 100,
                sources=LevelSource(
                    touches=touches, last_touch=last_touch, open_interest=oi,
                    notional=notional, flow_premium=flow_premium, net_gex=net_gex,
                ),
                flipped=flipped,
                why=" · ".join(parts),
            )
        )

    supports = sorted(
        (l for l in levels if l.kind == "soporte"), key=lambda l: l.price, reverse=True
    )
    resistances = sorted((l for l in levels if l.kind == "resistencia"), key=lambda l: l.price)

    def strongest(xs: list[Level]) -> Level | None:
        return max(xs, key=lambda l: l.strength) if xs else None

    return LevelsReport(
        spot=spot,
        supports=supports[:6],
        resistances=resistances[:6],
        key_support=strongest(supports),
        key_resistance=strongest(resistances),
        tolerance_pct=tolerance_pct,
    )
