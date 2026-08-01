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

from .jsmath import js_round
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
    c = [v for v in closes if v > 0][-22:]
    if len(c) < 3:
        return FALLBACK_IV
    rets = [math.log(b / a) for a, b in zip(c, c[1:])]
    mean = sum(rets) / len(rets)
    variance = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    iv = math.sqrt(variance) * math.sqrt(252)
    return min(3.0, max(0.05, iv))


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
    if spot <= 0 or not rows:
        return _empty(spot, iv, low_liquidity)

    # ── Gamma real por strike+lado (promedio) desde los trades, para anclar ──
    real_gamma: dict[str, list[float]] = {}
    trade_prem: dict[float, dict] = {}
    for t in trades:
        if t.strike is None or t.type == "unknown":
            continue
        if t.gamma > 0:
            real_gamma.setdefault(f"{t.strike}|{t.type}", []).append(abs(t.gamma))
        p = trade_prem.setdefault(t.strike, {"premium": 0.0, "count": 0})
        p["premium"] += t.premium
        p["count"] += 1

    # ── GEX por strike sobre toda la cadena (solo contratos vigentes) ──
    lo, hi = spot * (1 - NEAR_SPOT_PCT), spot * (1 + NEAR_SPOT_PCT)
    by_strike: dict[float, dict] = {}

    for r in rows:
        if r.strike < lo or r.strike > hi:
            continue
        if r.open_interest <= 0:
            continue
        dte = days_to_expiration(r.expiration, now)
        if dte is None or dte <= 0:
            continue
        T = dte / 365

        gamma = bs_gamma(spot, r.strike, T, iv)
        anchor = real_gamma.get(f"{r.strike}|{r.contract_type}")
        if anchor:
            gamma = (gamma + sum(anchor) / len(anchor)) / 2

        gex = gamma * r.open_interest * 100 * spot * spot * 0.01
        s = by_strike.setdefault(r.strike, {"call_gex": 0.0, "put_gex": 0.0})
        if r.contract_type == "call":
            s["call_gex"] += gex
        else:
            s["put_gex"] += gex

    if not by_strike:
        return _empty(spot, iv, low_liquidity)

    # ── Nodos + concentración de dinero (GEX + actividad real) ──
    raw = []
    for strike, g in by_strike.items():
        net_gex = g["call_gex"] - g["put_gex"]
        tp = trade_prem.get(strike)
        raw.append(
            {
                "strike": strike,
                "net_gex": net_gex,
                "call_gex": g["call_gex"],
                "put_gex": g["put_gex"],
                "trade_premium": tp["premium"] if tp else 0.0,
                "trade_count": tp["count"] if tp else 0,
                "gex_mag": abs(net_gex),
            }
        )

    max_gex_mag = max((r["gex_mag"] for r in raw), default=0.0)
    max_trade_prem = max((r["trade_premium"] for r in raw), default=0.0)
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
        key=lambda n: n.concentration,
        reverse=True,
    )

    king_strike = nodes[0].strike if nodes else None

    # ── Zona de inversión gamma: entre strikes contiguos donde el GEX neto
    # cambia de signo (put-dominante abajo → call-dominante arriba). Se elige el
    # cruce MÁS CERCANO al spot e interpola el punto medio ponderado: un flip a
    # 15% de distancia no gobierna el comportamiento de hoy.
    asc = sorted(raw, key=lambda r: r["strike"])
    flip_strike: float | None = None
    best_dist = math.inf
    for a, b in zip(asc, asc[1:]):
        if (a["net_gex"] < 0 <= b["net_gex"]) or (a["net_gex"] > 0 >= b["net_gex"]):
            span = abs(a["net_gex"]) + abs(b["net_gex"])
            cross = (
                a["strike"] + (b["strike"] - a["strike"]) * (abs(a["net_gex"]) / span)
                if span > 0
                else (a["strike"] + b["strike"]) / 2
            )
            dist = abs(cross - spot)
            if dist < best_dist:
                best_dist, flip_strike = dist, cross

    total_net_gex = sum(r["net_gex"] for r in raw)
    regime: Literal["positive", "negative"] = "positive" if total_net_gex >= 0 else "negative"

    if king_strike is None:
        direction = None
    elif king_strike > spot * 1.002:
        direction = "up"
    elif king_strike < spot * 0.998:
        direction = "down"
    else:
        direction = "flat"

    # ── Confianza: nitidez del nodo principal + scores de sub-agentes ──
    # Sin sub-scores se asume 0.5 (neutro), no 1: la ausencia de confirmación
    # no puede subir la confianza.
    sum_gex_mag = sum(r["gex_mag"] for r in raw)
    sharpness = (max_gex_mag / sum_gex_mag) if sum_gex_mag > 0 else 0.0
    sub_scores = [v for v in (conviction_score, structure_score) if v is not None]
    sub_avg = (sum(sub_scores) / len(sub_scores) / 10) if sub_scores else 0.5
    # `Math.round`, mitad hacia arriba — ver `jsmath.js_round`.
    confidence = js_round(100 * min(1.0, 0.6 * sharpness + 0.4 * sub_avg))

    return GexAnalysis(
        spot=spot, iv=iv, nodes=nodes, king_strike=king_strike,
        flip_strike=flip_strike, regime=regime, total_net_gex=total_net_gex,
        direction=direction, confidence=confidence,
        low_liquidity=low_liquidity, n=len(by_strike),
    )
