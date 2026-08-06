"""Motor GEX — Gamma Exposure de dealer a partir de la cadena de opciones.

Port de `web/lib/gex.ts`.

Fusiona el **posicionamiento** (gamma × Open Interest de toda la cadena) con la
**actividad real** (premium de los trades que están ocurriendo) para dibujar
"nodos de concentración" y derivar un precio imán.

Cuando la fuente de cadena no entrega gamma ni IV:

- la IV se estima de la volatilidad realizada del subyacente (barras diarias),
- la gamma se calcula con Black-Scholes por contrato,
- y donde hay gamma real del tape (MarketSnack), se **ancla** la estimada contra
  la real promediando ambas. Anclar y no sustituir es deliberado: la gamma del
  tape solo cubre los strikes que operaron, y saltar entre dos escalas distintas
  produciría nodos falsos en los bordes.

Términos neutros a propósito: esto describe posicionamiento de dealers, no
recomienda operar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Sequence

from .jsmath import (js_round, js_gt, js_number, js_add, js_string, js_max,
                     js_min, js_log, js_sqrt, js_clave, js_orden, es_nulo)
from .black_scholes import bs_gamma
from .occ import days_to_expiration
from .structure import ChainRow

__all__ = [
    "FALLBACK_IV",
    "NEAR_SPOT_PCT",
    "GEX_WEIGHT",
    "TRADE_WEIGHT",
    "TradeLite",
    "GexNode",
    "GexAnalysis",
    "estimate_iv",
    "gex_analysis",
]

#: IV de respaldo cuando no hay suficientes barras para estimar.
FALLBACK_IV = 0.4

#: Solo se consideran strikes dentro de ±este % del spot (los LEAPs lejanos no pintan).
NEAR_SPOT_PCT = 0.2

#: Peso del GEX vs. premium de trades reales al medir "concentración de dinero".
GEX_WEIGHT = 0.6
TRADE_WEIGHT = 0.4


@dataclass(frozen=True)
class TradeLite:
    """Subconjunto estructural de un FlowRow: lo mínimo para anclar y sumar."""

    strike: float | None
    type: Literal["call", "put", "unknown"]
    premium: float
    gamma: float


@dataclass(frozen=True)
class GexNode:
    strike: float
    net_gex: float  # call_gex − put_gex (signo = lado dominante)
    call_gex: float  # magnitud de gamma de calls (>=0)
    put_gex: float  # magnitud de gamma de puts (>=0)
    trade_premium: float  # $ de trades reales en ese strike
    trade_count: int
    concentration: float  # 0-1 (dinero concentrado: GEX + actividad)
    side: Literal["call", "put"]  # signo del GEX neto


@dataclass(frozen=True)
class GexAnalysis:
    spot: float
    iv: float
    nodes: list[GexNode]  # cerca del spot, ordenados por concentración desc
    king_strike: float | None  # nodo principal = precio imán / objetivo
    flip_strike: float | None  # zona de inversión gamma
    regime: Literal["positive", "negative"]  # gamma neta total
    total_net_gex: float
    direction: Literal["up", "down", "flat"] | None
    confidence: int  # 0-100
    low_liquidity: bool
    n: int  # strikes considerados cerca del spot


def estimate_iv(closes: Sequence[float]) -> float:
    """IV estimada de la volatilidad realizada anualizada de los cierres diarios.

    `closes` va del más viejo al más reciente. Ventana de hasta 21 sesiones.
    """
    # `closes.filter(v => v > 0).slice(-22)`: el filtro compara con coacción
    # pero NO convierte — los valores siguen crudos y es la división de abajo la
    # que los coacciona. `Math.log` de un negativo da `NaN` donde `math.log`
    # lanza, y `Math.min`/`Math.max` propagan el `NaN` donde los de Python lo
    # esconden (`min(1.0, nan)` devuelve 1.0).
    c = [v for v in closes if js_gt(v)][-22:]
    if len(c) < 3:
        return FALLBACK_IV
    rets = [js_log(js_number(b) / js_number(a)) for a, b in zip(c, c[1:])]
    mean = sum(rets) / len(rets)
    variance = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    iv = js_sqrt(variance) * math.sqrt(252)
    return js_min(3, js_max(0.05, iv))


def _empty(spot: float, iv: float, low_liquidity: bool) -> GexAnalysis:
    return GexAnalysis(
        spot=spot, iv=iv, nodes=[], king_strike=None, flip_strike=None,
        regime="positive", total_net_gex=0.0, direction=None, confidence=0,
        low_liquidity=low_liquidity, n=0,
    )


def gex_analysis(
    rows: Sequence[ChainRow],
    closes: Sequence[float],
    spot: float,
    now: datetime,
    trades: Sequence[TradeLite] = (),
    conviction_score: float | None = None,
    structure_score: float | None = None,
    low_liquidity: bool = False,
) -> GexAnalysis:
    """Analiza la cadena y deriva nodo imán, zona de flip, régimen y confianza.

    ``GEX por strike = gamma × OI × 100 × spot² × 0.01``, con signo +call/−put.
    """
    iv = estimate_iv(closes)
    # `if (spot <= 0 || rows.length === 0)`, literal. Con `spot` ilegible la
    # comparación es FALSA (`NaN <= 0` no se cumple) y su archivo sigue
    # adelante; el port usaba `not (spot > 0)`, que es la negación contraria y
    # devolvía el análisis vacío. Lo destapó el corpus malformado.
    if js_number(spot) <= 0 or not rows:
        return _empty(spot, iv, low_liquidity)

    # ── Gamma real por strike+lado (promedio) desde los trades, para anclar ──
    #
    # Sus dos índices son `Map`, no objetos: la clave es el strike CRUDO, sin
    # coaccionar, así que `"500"` y `500` son dos entradas distintas (ver
    # `jsmath.js_clave`). El port indexaba por `js_number` y las fundía, lo que
    # cambiaba el conteo de strikes `n` que sale en el reporte.
    real_gamma: dict = {}
    trade_prem: dict = {}
    for t in trades:
        if es_nulo(t.strike) or t.type == "unknown":
            continue
        if js_gt(t.gamma):
            # `${t.strike}|${t.type}` — plantilla, o sea `String()`: un `null`
            # escribe "null" y un `true` escribe "true".
            k = f"{js_string(t.strike)}|{js_string(t.type)}"
            real_gamma.setdefault(k, []).append(abs(js_number(t.gamma)))
        p = trade_prem.setdefault(js_clave(t.strike), {"premium": 0.0, "count": 0})
        p["premium"] = js_add(p["premium"], t.premium)
        p["count"] += 1

    # ── GEX por strike sobre toda la cadena (solo contratos vigentes) ──
    spot_n = js_number(spot)          # `spot` se DEVUELVE crudo; solo la aritmética coacciona
    lo, hi = spot_n * (1 - NEAR_SPOT_PCT), spot_n * (1 + NEAR_SPOT_PCT)
    by_strike: dict = {}

    for r in rows:
        strike = js_number(r.strike)
        if strike < lo or strike > hi:
            continue
        # `if (r.openInterest <= 0) continue;`, literal: con un OI ilegible la
        # comparación es falsa y la fila ENTRA, envenenando su GEX con un `NaN`
        # a la vista. El port la descartaba en silencio.
        if js_number(r.open_interest) <= 0:
            continue
        dte = days_to_expiration(r.expiration, now)
        # `if (dte <= 0) continue;`, literal. Un `NaN` NO se salta: `NaN <= 0`
        # es falso, así que la fila entra y su gamma sale `NaN`, igual que en su
        # archivo. El port descartaba la fila, que es una decisión distinta —y
        # más silenciosa: un vencimiento ilegible desaparecía del GEX sin dejar
        # rastro en vez de envenenar la suma a la vista.
        if dte <= 0:
            continue
        T = dte / 365

        gamma = bs_gamma(spot_n, strike, T, iv)
        anchor = real_gamma.get(f"{js_string(r.strike)}|{js_string(r.contract_type)}")
        if anchor:
            gamma = (gamma + sum(anchor) / len(anchor)) / 2

        gex = gamma * js_number(r.open_interest) * 100 * spot_n * spot_n * 0.01
        s = by_strike.setdefault(js_clave(r.strike),
                                 {"strike": r.strike, "call_gex": 0.0, "put_gex": 0.0})
        if r.contract_type == "call":
            s["call_gex"] += gex
        else:
            s["put_gex"] += gex

    if not by_strike:
        return _empty(spot, iv, low_liquidity)

    # ── Nodos + concentración de dinero (GEX + actividad real) ──
    raw = []
    for clave, g in by_strike.items():
        net_gex = g["call_gex"] - g["put_gex"]
        tp = trade_prem.get(clave)
        raw.append(
            {
                "strike": g["strike"],          # el crudo, que es lo que él propaga
                "net_gex": net_gex,
                "call_gex": g["call_gex"],
                "put_gex": g["put_gex"],
                "trade_premium": tp["premium"] if tp else 0.0,
                "trade_count": tp["count"] if tp else 0,
                "gex_mag": abs(net_gex),
            }
        )

    # `Math.max(...lista, 0)` — el 0 es un argumento más (suelo), y un solo
    # `NaN` en la lista se lleva el máximo entero. `max(..., default=0)` de
    # Python no hace ninguna de las dos cosas.
    max_gex_mag = js_max(*[r["gex_mag"] for r in raw], 0)
    max_trade_prem = js_max(*[r["trade_premium"] for r in raw], 0)
    has_trades = max_trade_prem > 0

    nodes = sorted(
        (
            GexNode(
                strike=r["strike"],
                net_gex=r["net_gex"],
                call_gex=r["call_gex"],
                put_gex=r["put_gex"],
                trade_premium=r["trade_premium"],
                trade_count=r["trade_count"],
                concentration=(
                    GEX_WEIGHT * (r["gex_mag"] / max_gex_mag if max_gex_mag > 0 else 0)
                    + TRADE_WEIGHT * (r["trade_premium"] / max_trade_prem)
                    if has_trades
                    else (r["gex_mag"] / max_gex_mag if max_gex_mag > 0 else 0)
                ),
                side="call" if r["net_gex"] >= 0 else "put",
            )
            for r in raw
        ),
        # `.sort((a, b) => b.concentration - a.concentration)`: un `NaN` en la
        # resta vale "iguales", no reordena — ver `jsmath.js_orden`.
        key=js_orden(lambda a, b: b.concentration - a.concentration),
    )

    king_strike = nodes[0].strike if nodes else None

    # ── Zona de inversión gamma: entre strikes contiguos donde el GEX neto
    # cambia de signo (put-dominante abajo → call-dominante arriba). Se elige el
    # cruce MÁS CERCANO al spot e interpola el punto medio ponderado: un flip a
    # 15% de distancia no gobierna el comportamiento de hoy.
    asc = sorted(raw, key=js_orden(
        lambda a, b: js_number(a["strike"]) - js_number(b["strike"])))
    flip_strike: float | None = None
    best_dist = math.inf
    for a, b in zip(asc, asc[1:]):
        if (a["net_gex"] < 0 <= b["net_gex"]) or (a["net_gex"] > 0 >= b["net_gex"]):
            span = abs(a["net_gex"]) + abs(b["net_gex"])
            sa, sb = js_number(a["strike"]), js_number(b["strike"])
            cross = (sa + (sb - sa) * (abs(a["net_gex"]) / span)
                     if span > 0 else (sa + sb) / 2)
            dist = abs(cross - spot_n)
            if dist < best_dist:
                best_dist, flip_strike = dist, cross

    total_net_gex = sum(r["net_gex"] for r in raw)
    regime: Literal["positive", "negative"] = "positive" if total_net_gex >= 0 else "negative"

    if king_strike is None:
        direction = None
    elif js_number(king_strike) > spot_n * 1.002:
        direction = "up"
    elif js_number(king_strike) < spot_n * 0.998:
        direction = "down"
    else:
        direction = "flat"

    # ── Confianza: nitidez del nodo principal + scores de sub-agentes ──
    # Sin sub-scores se asume 0.5 (neutro), no 1: la ausencia de confirmación
    # no puede subir la confianza.
    sum_gex_mag = sum(r["gex_mag"] for r in raw)
    sharpness = (max_gex_mag / sum_gex_mag) if sum_gex_mag > 0 else 0.0
    sub_scores = [v for v in (conviction_score, structure_score) if not es_nulo(v)]
    sub_avg = (sum(js_number(v) for v in sub_scores) / len(sub_scores) / 10
               if sub_scores else 0.5)
    # `Math.round(100 * Math.min(1, …))`: `Math.min` propaga el `NaN` y
    # `Math.round` lo deja pasar. El `min()` de Python devolvía el 1.0 y la
    # confianza salía 100 justo donde su archivo dice "no sé".
    confidence = js_round(100 * js_min(1, 0.6 * sharpness + 0.4 * sub_avg))

    return GexAnalysis(
        spot=spot, iv=iv, nodes=nodes, king_strike=king_strike,
        flip_strike=flip_strike, regime=regime, total_net_gex=total_net_gex,
        direction=direction, confidence=confidence,
        low_liquidity=low_liquidity, n=len(by_strike),
    )
