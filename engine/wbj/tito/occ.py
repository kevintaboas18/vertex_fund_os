"""Parseo de símbolos de opción en formato OCC.

Port de `web/lib/occ.ts`.

Estructura del símbolo: ``ROOT + YYMMDD + (C|P) + strike*1000`` (8 dígitos),
p. ej. ``TSLA261120P00305000`` → TSLA, 2026-11-20, put, strike 305.

El manejo de fechas es deliberadamente en **hora del mercado (ET)**, no UTC:
después de las ~8 PM ET, UTC ya pasó al día siguiente y los días a vencimiento
se reportarían con un día de más.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from .jsmath import (MS_POR_DIA, UNDEFINED, js_date_parse, js_number,
                     js_round, js_truthy)

__all__ = ["OccInfo", "parse_occ", "market_date", "market_date_str", "days_to_expiration"]

MARKET_TZ = ZoneInfo("America/New_York")

_STRIKE_RE = re.compile(r"^\d{8}$")
_TYPE_RE = re.compile(r"^[CP]$")
_DATE_RE = re.compile(r"^\d{6}$")

OptionType = Literal["call", "put"]


@dataclass(frozen=True)
class OccInfo:
    """Las cuatro piezas que codifica un símbolo OCC."""

    underlying: str
    expiration: str  # YYYY-MM-DD
    type: OptionType
    strike: float


def parse_occ(symbol) -> OccInfo | None:
    """Descompone un símbolo OCC. Devuelve ``None`` si no encaja con el formato.

    Nunca adivina: un símbolo malformado devuelve ``None`` en vez de un parseo
    parcial, porque un strike equivocado contamina todo el scorecard.

    `if (!symbol || symbol.length < 16) return null;` es literal, y con un
    símbolo que NO es texto se comporta de tres maneras distintas — las tres
    medidas ejecutando su archivo en Node:

        symbol: []    `[]` es truthy pero `[].length` es 0  → null, sin ruido
        symbol: 5     `(5).length` es `undefined`, y `undefined < 16` es FALSO,
        symbol: {}    así que sigue y `symbol.slice` LANZA un TypeError

    O sea que un símbolo numérico tumba la petición en su archivo. El port
    llamaba con `symbol if isinstance(symbol, str) else ""`, que lo convertía en
    un `null` silencioso — otra decisión. El filtro de entrada vive en
    `borde.py`, no aquí.
    """
    if not js_truthy(symbol):
        return None
    largo = len(symbol) if isinstance(symbol, (str, list, tuple)) else UNDEFINED
    if js_number(largo) < 16:          # `undefined < 16` es falso: sigue y lanza
        return None
    if not isinstance(symbol, str):
        raise TypeError("symbol.slice is not a function")
    strike_raw = symbol[-8:]
    type_raw = symbol[-9:-8]
    date_raw = symbol[-15:-9]
    underlying = symbol[:-15]
    if (
        not _STRIKE_RE.match(strike_raw)
        or not _TYPE_RE.match(type_raw)
        or not _DATE_RE.match(date_raw)
        or not underlying
    ):
        return None
    expiration = f"20{date_raw[0:2]}-{date_raw[2:4]}-{date_raw[4:6]}"
    return OccInfo(
        underlying=underlying,
        expiration=expiration,
        type="call" if type_raw == "C" else "put",
        strike=int(strike_raw) / 1000,
    )


def market_date(now: datetime) -> date:
    """Fecha del mercado (ET) para `now`."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("UTC"))
    return now.astimezone(MARKET_TZ).date()


def market_date_str(now: datetime) -> str:
    """Fecha del mercado (ET) como ``YYYY-MM-DD``."""
    return market_date(now).isoformat()


def _market_today(now: datetime) -> float:
    """`marketToday`: el día de mercado (ET) como epoch de medianoche UTC."""
    return js_date_parse(f"{market_date_str(now)}T00:00:00Z")


def days_to_expiration(expiration: str, now: datetime) -> float:
    """Días hasta el vencimiento respecto al día de mercado (ET) de `now`.

    Literal:

        const exp = Date.parse(`${expiration}T00:00:00Z`);
        return Math.round((exp - marketToday(now)) / 86_400_000);

    Devuelve `NaN` cuando la fecha no se parsea, que es lo que hace su
    `Date.parse` — no `None`. La diferencia importa desde que `compute` es
    literal: él **no** recorta el vencimiento a `YYYY-MM-DD`, así que si Massive
    manda `"2026-09-18T00:00:00Z"` la concatenación con `T00:00:00Z` da una
    cadena inválida y su motor propaga `NaN`. El port lo recortaba y devolvía 49
    donde él no devuelve ningún número. Medido en `diff_reloj.sh`.

    Un `NaN` no es un 0 silencioso: `NaN > x` y `NaN <= x` son los dos falsos,
    así que cae por todas las bandas de puntuación en vez de colarse como "vence
    hoy". Es exactamente lo que le pasa a él.
    """
    exp = js_date_parse(f"{expiration}T00:00:00Z")
    return js_round((exp - _market_today(now)) / MS_POR_DIA)


def add_days(d: date, n: int) -> date:
    """Azúcar para tests de calendario."""
    return d + timedelta(days=n)
