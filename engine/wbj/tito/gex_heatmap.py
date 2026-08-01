"""Heatmap de GEX por strike × vencimiento.

Port de `web/lib/gexHeatmap.ts`.

El mapa de nodos resume el GEX en **una** dimensión (el strike). Este módulo lo
abre en **dos**: cada celda es el GEX neto de un strike en un vencimiento
concreto, que es donde se ve qué expiración está sosteniendo cada muro. Dos
muros del mismo tamaño pesan distinto si uno vence el viernes y el otro en seis
meses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .jsmath import js_round
from .black_scholes import bs_gamma
from .structure import ChainRow

__all__ = [
    "HeatTrade",
    "HeatCell",
    "HeatExpiration",
    "HeatStrike",
    "GexHeatmap",
    "gex_heatmap",
]


@dataclass(frozen=True)
class HeatTrade:
    strike: float | None
    expiration: str | None
    gamma: float
    premium: float


@dataclass
class HeatCell:
    strike: float
    expiration: str
    #: GEX neto en $ por 1% de movimiento (+ calls / − puts).
    net_gex: float = 0.0
    call_gex: float = 0.0
    put_gex: float = 0.0
    open_interest: int = 0
    #: Intensidad 0-1 dentro de la malla, para el color.
    intensity: float = 0.0


@dataclass
class HeatExpiration:
    expiration: str
    dte: int
    net_gex: float = 0.0
    open_interest: int = 0


@dataclass
class HeatStrike:
    strike: float
    net_gex: float = 0.0
    call_gex: float = 0.0
    put_gex: float = 0.0
    open_interest: int = 0
    distance_pct: float = 0.0


@dataclass
class GexHeatmap:
    spot: float
    iv: float
    strikes: list[HeatStrike]  # de mayor a menor precio (como se pinta)
    expirations: list[HeatExpiration]  # de más cercano a más lejano
    cells: list[HeatCell]
    max_abs_cell: float  # |GEX| máximo de una celda — referencia del color
    hottest_positive: HeatCell | None
    hottest_negative: HeatCell | None
    total_net_gex: float


_EMPTY = GexHeatmap(
    spot=0.0, iv=0.0, strikes=[], expirations=[], cells=[],
    max_abs_cell=0.0, hottest_positive=None, hottest_negative=None, total_net_gex=0.0,
)


def _dte_of(expiration: str, now: datetime) -> int:
    """DTE contra el cierre del día de vencimiento (21:00 UTC ≈ 17:00 ET)."""
    try:
        exp = datetime.fromisoformat(f"{expiration[:10]}T21:00:00+00:00")
    except (ValueError, TypeError):
        return 0
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, js_round((exp - now).total_seconds() / 86_400))


def gex_heatmap(
    rows: Sequence[ChainRow],
    spot: float,
    iv: float,
    now: datetime,
    trades: Sequence[HeatTrade] = (),
    strike_radius: int = 18,
    max_expirations: int = 8,
) -> GexHeatmap:
    """Construye la malla strike × vencimiento con su GEX neto por celda."""
    if not (spot > 0) or not rows:
        return _EMPTY

    # Gamma real del tape por (strike, vencimiento) para anclar la estimación.
    real_gamma: dict[str, list[float]] = {}
    for t in trades:
        if t.strike is None or not t.expiration or not (t.gamma > 0):
            continue
        real_gamma.setdefault(f"{t.strike}|{t.expiration}", []).append(t.gamma)

    # Vencimientos más cercanos con contratos vivos.
    exp_set: dict[str, int] = {}
    for r in rows:
        if not r.expiration or r.open_interest <= 0:
            continue
        exp_set[r.expiration] = exp_set.get(r.expiration, 0) + r.open_interest
    expirations = [e for e in sorted(exp_set) if _dte_of(e, now) >= 0][:max_expirations]
    if not expirations:
        return _EMPTY
    exp_selected = set(expirations)

    # Strikes alrededor del spot: los `strike_radius` más cercanos por cada lado.
    all_strikes = sorted(
        {r.strike for r in rows if r.expiration in exp_selected and r.strike > 0}
    )
    above = [s for s in all_strikes if s >= spot][:strike_radius]
    below = [s for s in all_strikes if s < spot][-strike_radius:]
    strike_set = set(below) | set(above)
    if not strike_set:
        return _EMPTY

    # Acumulación por celda.
    cell_map: dict[str, HeatCell] = {}
    for r in rows:
        if r.expiration not in exp_selected or r.strike not in strike_set:
            continue
        if r.open_interest <= 0:
            continue

        dte = _dte_of(r.expiration, now)
        # Piso de 1 día: T=0 haría explotar la gamma de Black-Scholes.
        T = max(dte, 1) / 365
        gamma = bs_gamma(spot, r.strike, T, iv)
        anchor = real_gamma.get(f"{r.strike}|{r.expiration}")
        if anchor:
            gamma = (gamma + sum(anchor) / len(anchor)) / 2

        # GEX = Γ · OI · 100 · S² · 0.01 — dólares por 1% de movimiento.
        gex = gamma * r.open_interest * 100 * spot * spot * 0.01
        key = f"{r.strike}|{r.expiration}"
        cell = cell_map.setdefault(key, HeatCell(strike=r.strike, expiration=r.expiration))
        if r.contract_type == "call":
            cell.call_gex += gex
            cell.net_gex += gex
        else:
            cell.put_gex += gex
            cell.net_gex -= gex
        cell.open_interest += r.open_interest

    cells = list(cell_map.values())
    if not cells:
        return _EMPTY

    max_abs_cell = max([1.0, *(abs(c.net_gex) for c in cells)])
    for c in cells:
        c.intensity = min(1.0, abs(c.net_gex) / max_abs_cell)

    # Totales por strike (fila) y por vencimiento (columna).
    by_strike: dict[float, HeatStrike] = {}
    for c in cells:
        s = by_strike.setdefault(
            c.strike,
            HeatStrike(strike=c.strike, distance_pct=(c.strike - spot) / spot * 100),
        )
        s.net_gex += c.net_gex
        s.call_gex += c.call_gex
        s.put_gex += c.put_gex
        s.open_interest += c.open_interest

    by_exp: dict[str, HeatExpiration] = {}
    for c in cells:
        e = by_exp.setdefault(
            c.expiration, HeatExpiration(expiration=c.expiration, dte=_dte_of(c.expiration, now))
        )
        e.net_gex += c.net_gex
        e.open_interest += c.open_interest

    sorted_cells = sorted(cells, key=lambda c: c.net_gex, reverse=True)

    return GexHeatmap(
        spot=spot,
        iv=iv,
        # Precio alto arriba, como en cualquier tabla de opciones.
        strikes=sorted(by_strike.values(), key=lambda s: s.strike, reverse=True),
        expirations=sorted(by_exp.values(), key=lambda e: e.expiration),
        cells=cells,
        max_abs_cell=max_abs_cell,
        hottest_positive=sorted_cells[0] if sorted_cells[0].net_gex > 0 else None,
        hottest_negative=sorted_cells[-1] if sorted_cells[-1].net_gex < 0 else None,
        total_net_gex=sum(c.net_gex for c in cells),
    )
