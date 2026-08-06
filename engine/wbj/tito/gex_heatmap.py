"""Heatmap de GEX por strike × vencimiento.

Port de `web/lib/gexHeatmap.ts`.

El mapa de nodos resume el GEX en **una** dimensión (el strike). Este módulo lo
abre en **dos**: cada celda es el GEX neto de un strike en un vencimiento
concreto, que es donde se ve qué expiración está sosteniendo cada muro. Dos
muros del mismo tamaño pesan distinto si uno vence el viernes y el otro en seis
meses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .jsmath import (MS_POR_DIA, es_nulo, js_abs, js_add, js_clave,
                     js_date_parse, js_gt, js_is_finite, js_max, js_min,
                     js_number, js_orden, js_round, js_string, js_time,
                     js_truthy)
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


def _dte_of(expiration: str, now: datetime) -> float:
    """DTE contra el cierre del día de vencimiento (21:00 UTC ≈ 17:00 ET).

    Literal:

        const ms = new Date(`${expiration}T21:00:00Z`).getTime() - now.getTime();
        return Math.max(0, Math.round(ms / 86_400_000));

    Sin recortar el vencimiento a 10 caracteres y sin caer a 0 cuando no se
    parsea: `Math.max(0, NaN)` en JS es `NaN`, no 0. El port devolvía 0 —que se
    lee como "vence hoy" y manda la celda a la banda más caliente del heatmap—
    donde él propaga un `NaN` que no entra en ninguna banda.

    Importa desde que `compute` es literal: el vencimiento llega tal como lo
    manda Massive, y si trae hora la concatenación con `T21:00:00Z` es inválida.
    Medido en `diff_reloj.sh`.
    """
    ms = js_date_parse(f"{expiration}T21:00:00Z") - js_time(now)
    return max(0.0, js_round(ms / MS_POR_DIA)) if ms == ms else math.nan


def _entero_js(v, negado: bool = False) -> int:
    """`ToIntegerOrInfinity(v)` — lo que `Array.prototype.slice` hace con su
    argumento: `NaN` y `undefined` valen 0, y el resto se trunca hacia cero.

    `negado` devuelve `-n`, que es como su archivo pide "los últimos n"
    (`.slice(-strikeRadius)`).
    """
    n = js_number(v)
    if n != n:
        n = 0.0
    if math.isinf(n):
        return -(10 ** 9) if (n > 0) == negado else 10 ** 9
    n = int(n)
    return -n if negado else n


#: "argumento ausente" — que NO es lo mismo que pasar `null`. `.slice(0)` da la
#: lista entera y `.slice(0, null)` da una vacía, porque `ToIntegerOrInfinity`
#: convierte el `null` en 0.
_SIN = object()


def _rodaja(xs: list, inicio, fin=_SIN) -> list:
    """`xs.slice(inicio, fin)` de JS, con sus reglas de índice.

    Dos trampas que el corte de Python no tiene: `.slice(-0)` es `.slice(0)`
    —la lista ENTERA, no una vacía— y un `fin` de `null` sí cuenta como 0.
    """
    i = inicio if isinstance(inicio, int) else _entero_js(inicio)
    if i < 0:
        i = max(0, len(xs) + i)
    if fin is _SIN:
        return xs[i:]
    j = _entero_js(fin)
    if j < 0:
        j = max(0, len(xs) + j)
    return xs[i:j]


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
    # `if (!(spot > 0) || rows.length === 0)`: la comparación coacciona sola y
    # un `spot` ilegible la hace falsa, así que sigue. Todo el resto del cuerpo
    # trabaja con el crudo y coacciona en cada uso, igual que su archivo — el
    # port usaba operadores de Python y lanzaba con la mitad del corpus
    # malformado. Lo destapó `diff_motor3.sh`.
    if not js_gt(spot) or not rows:
        return _EMPTY
    spot_n = js_number(spot)

    # Gamma real del tape por (strike, vencimiento) para anclar la estimación.
    real_gamma: dict[str, list[float]] = {}
    for t in trades:
        if es_nulo(t.strike) or not js_truthy(t.expiration) or not js_gt(t.gamma):
            continue
        # `${t.strike}|${t.expiration}` — plantilla, o sea `String()`.
        clave = f"{js_string(t.strike)}|{js_string(t.expiration)}"
        # `e.sum += t.gamma` — el `+` de JS: con una gamma `"Infinity"` el
        # acumulador es la CADENA "0Infinity", que al dividir da `NaN`. El port
        # coaccionaba antes de acumular y salía un infinito.
        real_gamma.setdefault(clave, []).append(t.gamma)

    # Vencimientos más cercanos con contratos vivos.
    exp_set: dict = {}
    for r in rows:
        # `!r.expiration` es la veracidad de JS; `r.openInterest <= 0` NO se
        # cumple con un OI ilegible, así que la fila entra.
        if not js_truthy(r.expiration) or js_number(r.open_interest) <= 0:
            continue
        k = js_clave(r.expiration)
        exp_set[k] = js_add(exp_set.get(k, 0), r.open_interest)
    # `[...expSet.keys()].sort()` sin comparador: ordena por TEXTO, no numérico.
    expirations = [e for e in sorted((r.expiration for r in rows
                                      if js_clave(r.expiration) in exp_set),
                                     key=js_string)
                   if js_number(_dte_of(e, now)) >= 0]
    vistos, unicas = set(), []
    for e in expirations:
        if js_clave(e) not in vistos:
            vistos.add(js_clave(e))
            unicas.append(e)
    expirations = _rodaja(unicas, 0, max_expirations)
    if not expirations:
        return _EMPTY
    exp_selected = {js_clave(e) for e in expirations}

    # Strikes alrededor del spot: los `strike_radius` más cercanos por cada lado.
    todos: dict = {}
    for r in rows:
        if js_clave(r.expiration) in exp_selected and js_gt(r.strike):
            todos.setdefault(js_clave(r.strike), r.strike)
    all_strikes = sorted(todos.values(),
                         key=js_orden(lambda a, b: js_number(a) - js_number(b)))
    arriba = [s for s in all_strikes if js_number(s) >= spot_n]
    abajo = [s for s in all_strikes if js_number(s) < spot_n]
    above = _rodaja(arriba, 0, strike_radius)
    below = _rodaja(abajo, _entero_js(strike_radius, negado=True))
    strike_set = {js_clave(s) for s in below} | {js_clave(s) for s in above}
    if not strike_set:
        return _EMPTY

    # Acumulación por celda.
    cell_map: dict[str, HeatCell] = {}
    for r in rows:
        if (js_clave(r.expiration) not in exp_selected
                or js_clave(r.strike) not in strike_set):
            continue
        if js_number(r.open_interest) <= 0:
            continue

        dte = _dte_of(r.expiration, now)
        # Piso de 1 día: T=0 haría explotar la gamma de Black-Scholes.
        T = js_max(dte, 1) / 365
        gamma = bs_gamma(spot_n, js_number(r.strike), T, js_number(iv))
        anchor = real_gamma.get(f"{js_string(r.strike)}|{js_string(r.expiration)}")
        if anchor:
            suma: object = 0
            for x in anchor:
                suma = js_add(suma, x)
            gamma = (gamma + js_number(suma) / len(anchor)) / 2

        # GEX = Γ · OI · 100 · S² · 0.01 — dólares por 1% de movimiento.
        gex = gamma * js_number(r.open_interest) * 100 * spot_n * spot_n * 0.01
        key = f"{js_string(r.strike)}|{js_string(r.expiration)}"
        cell = cell_map.setdefault(key, HeatCell(strike=r.strike, expiration=r.expiration))
        if r.contract_type == "call":
            cell.call_gex += gex
            cell.net_gex += gex
        else:
            cell.put_gex += gex
            cell.net_gex -= gex
        cell.open_interest = js_add(cell.open_interest, r.open_interest)

    cells = list(cell_map.values())
    if not cells:
        return _EMPTY

    # `Math.max(...lista, 1)` — el 1 es un argumento más y un `NaN` se lleva el
    # máximo entero, que es lo que hace que la intensidad salga `NaN` y no 1.
    max_abs_cell = js_max(*[js_abs(c.net_gex) for c in cells], 1)
    for c in cells:
        c.intensity = js_min(1, js_abs(c.net_gex) / max_abs_cell)

    # Totales por strike (fila) y por vencimiento (columna).
    by_strike: dict = {}
    for c in cells:
        s = by_strike.setdefault(
            js_clave(c.strike),
            HeatStrike(strike=c.strike,
                       distance_pct=(js_number(c.strike) - spot_n) / spot_n * 100),
        )
        s.net_gex += c.net_gex
        s.call_gex += c.call_gex
        s.put_gex += c.put_gex
        s.open_interest = js_add(s.open_interest, c.open_interest)

    by_exp: dict = {}
    for c in cells:
        e = by_exp.setdefault(
            js_clave(c.expiration), HeatExpiration(expiration=c.expiration, dte=_dte_of(c.expiration, now))
        )
        e.net_gex += c.net_gex
        e.open_interest = js_add(e.open_interest, c.open_interest)

    # `.sort((a, b) => b.netGex - a.netGex)` — resta numérica, y un `NaN` deja
    # el par como estaba (ver `jsmath.js_orden`).
    sorted_cells = sorted(cells, key=js_orden(lambda a, b: b.net_gex - a.net_gex))

    return GexHeatmap(
        spot=spot,
        iv=iv,
        # Precio alto arriba, como en cualquier tabla de opciones.
        strikes=sorted(by_strike.values(),
                       key=js_orden(lambda a, b: js_number(b.strike) - js_number(a.strike))),
        # `.sort((a, b) => a.expiration.localeCompare(b.expiration))` — texto.
        expirations=sorted(by_exp.values(), key=lambda e: js_string(e.expiration)),
        cells=cells,
        max_abs_cell=max_abs_cell,
        hottest_positive=sorted_cells[0] if js_gt(sorted_cells[0].net_gex) else None,
        hottest_negative=(sorted_cells[-1]
                          if js_number(sorted_cells[-1].net_gex) < 0 else None),
        total_net_gex=sum(c.net_gex for c in cells),
    )
