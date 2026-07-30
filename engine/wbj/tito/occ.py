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


def parse_occ(symbol: str) -> OccInfo | None:
    """Descompone un símbolo OCC. Devuelve ``None`` si no encaja con el formato.

    Nunca adivina: un símbolo malformado devuelve ``None`` en vez de un parseo
    parcial, porque un strike equivocado contamina todo el scorecard.
    """
    if not symbol or len(symbol) < 16:
        return None
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


def days_to_expiration(expiration: str, now: datetime) -> int | None:
    """Días hasta el vencimiento respecto al día de mercado (ET) de `now`.

    Devuelve ``None`` si la fecha no es parseable — nunca un 0 silencioso, que
    el scorecard leería como "vence hoy".
    """
    try:
        exp = date.fromisoformat(expiration[:10])
    except (ValueError, TypeError):
        return None
    return (exp - market_date(now)).days


def add_days(d: date, n: int) -> date:
    """Azúcar para tests de calendario."""
    return d + timedelta(days=n)
