"""Primitivas de Black-Scholes, puras y compartidas.

Port de `web/lib/blackScholes.ts`.

Viven aquí y no dentro de `gex.py` porque más de un consumidor necesita delta e
IV implícita de la misma familia de fórmulas. `norm_cdf` NO se redefine: ya está
en `expected_move` y está testeada allí.
"""

from __future__ import annotations

import math
from typing import Literal

from .expected_move import norm_cdf

__all__ = ["RISK_FREE", "OptionType", "bs_price", "bs_delta", "bs_gamma", "implied_vol"]

#: Tasa libre de riesgo. Constante a propósito: a 7-45 días su efecto sobre el
#: delta es de segundo orden, y una llamada extra por una curva de tasas no se
#: paga sola.
RISK_FREE = 0.04

OptionType = Literal["call", "put"]

_IV_LO = 0.01
_IV_HI = 5.0
_IV_TOL = 1e-6
_IV_ITERS = 60


def _phi(x: float) -> float:
    """Densidad normal estándar φ(x)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1(spot: float, strike: float, T: float, iv: float, r: float) -> float:
    return (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))


def _invalid(spot: float, strike: float, T: float, iv: float) -> bool:
    return not (spot > 0) or not (strike > 0) or not (T > 0) or not (iv > 0)


def bs_price(
    spot: float,
    strike: float,
    T: float,
    iv: float,
    type: OptionType,
    r: float = RISK_FREE,
) -> float:
    """Precio teórico de una europea. Devuelve 0 si los insumos no son válidos."""
    if _invalid(spot, strike, T, iv):
        return 0.0
    d1 = _d1(spot, strike, T, iv, r)
    d2 = d1 - iv * math.sqrt(T)
    disc = strike * math.exp(-r * T)
    if type == "call":
        return spot * norm_cdf(d1) - disc * norm_cdf(d2)
    return disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


def bs_delta(
    spot: float,
    strike: float,
    T: float,
    iv: float,
    type: OptionType,
    r: float = RISK_FREE,
) -> float:
    """Delta. Call ∈ (0,1), put ∈ (−1,0). Devuelve 0 si los insumos no son válidos."""
    if _invalid(spot, strike, T, iv):
        return 0.0
    nd1 = norm_cdf(_d1(spot, strike, T, iv, r))
    return nd1 if type == "call" else nd1 - 1.0


def bs_gamma(spot: float, strike: float, T: float, iv: float) -> float:
    """Gamma de Black-Scholes (r = 0).

    Se mantiene con r = 0 —igual que el original— para no alterar el GEX ya
    calibrado: cambiar la tasa aquí movería todos los nodos históricos.
    """
    if _invalid(spot, strike, T, iv):
        return 0.0
    sqrt_t = math.sqrt(T)
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * T) / (iv * sqrt_t)
    return _phi(d1) / (spot * iv * sqrt_t)


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    T: float,
    type: OptionType,
    r: float = RISK_FREE,
) -> float | None:
    """IV implícita por bisección sobre σ.

    Devuelve ``None`` si el precio viola los límites de no-arbitraje (pasa con
    quotes anchas o cruzadas) — el que llama debe caer a una IV estimada y
    **marcar la fila**, no inventar un número.
    """
    if not (price > 0) or not (spot > 0) or not (strike > 0) or not (T > 0):
        return None

    disc = strike * math.exp(-r * T)
    intrinsic = max(0.0, disc - spot) if type == "put" else max(0.0, spot - disc)
    upper = disc if type == "put" else spot
    if price <= intrinsic or price >= upper:
        return None

    lo, hi = _IV_LO, _IV_HI
    if bs_price(spot, strike, T, lo, type, r) > price:
        return None
    if bs_price(spot, strike, T, hi, type, r) < price:
        return None

    for _ in range(_IV_ITERS):
        mid = (lo + hi) / 2
        p = bs_price(spot, strike, T, mid, type, r)
        if abs(p - price) < _IV_TOL:
            return mid
        if p < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
