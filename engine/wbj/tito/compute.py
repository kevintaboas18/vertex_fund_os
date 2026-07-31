"""Port de `compute.ts` — las fórmulas de la cadena, sin I/O.

Funciones puras: convierten el contrato crudo del Option Chain Snapshot de
Massive en la fila que consumen el sub-agente 4 (Estructura) y la tabla.

Vive aparte de `massive.py` por la misma razón que en el original: el cliente
HTTP se ocupa de traer páginas, y las fórmulas se pueden probar sin red. Antes
la conversión estaba metida dentro de `fetch_option_chain`, y ahí se coló el
único bug real de este módulo: el nocional daba por hecho 100 acciones por
contrato.
"""

from __future__ import annotations

from typing import Any, Sequence

from .structure import ChainRow, PriceSource

__all__ = [
    "DEFAULT_SHARES_PER_CONTRACT",
    "contract_price",
    "open_premium",
    "notional_value",
    "to_row",
    "sort_by_open_interest_desc",
    "count_expirations",
]

#: Acciones por contrato de un contrato estándar. Los ajustados traen otro
#: número en `details.shares_per_contract` y hay que respetarlo.
DEFAULT_SHARES_PER_CONTRACT = 100


def _num(v: Any) -> float | None:
    """`typeof x === "number"` de Víctor: un string numérico NO cuenta."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def contract_price(raw: dict) -> tuple[float | None, PriceSource]:
    """`contractPrice`: precio del contrato para el Open Premium.

    La fórmula del agente pide **BID**, pero el plan actual de Massive no
    devuelve quotes, así que cae a `last_trade` → `day.close` → `day.vwap`.
    Cuando haya bid, se cambia aquí y solo aquí.

    Devuelve también de dónde salió, porque un Open Premium calculado sobre el
    VWAP del día no vale lo mismo que uno sobre el último trade, y quien lo lea
    tiene derecho a saberlo.
    """
    lt = _num((raw.get("last_trade") or {}).get("price"))
    if lt is not None and lt > 0:
        return lt, "last_trade"
    day = raw.get("day") or {}
    close = _num(day.get("close"))
    if close is not None and close > 0:
        return close, "day_close"
    vwap = _num(day.get("vwap"))
    if vwap is not None and vwap > 0:
        return vwap, "day_vwap"
    return None, "none"


def open_premium(open_interest: float, price: float | None) -> float | None:
    """Open Premium = Open Interest × precio del contrato. `None` si no hay precio.

    `None` y `0` son cosas distintas: el primero es "no se pudo calcular", el
    segundo "vale cero". No se colapsan.
    """
    if price is None:
        return None
    return open_interest * price


def notional_value(
    open_interest: float,
    strike: float,
    shares_per_contract: float = DEFAULT_SHARES_PER_CONTRACT,
) -> float:
    """Valor Nocional = OI × acciones por contrato × strike.

    Es el dinero que cambiaría de manos si todo ese open interest expirara ITM,
    y es la entrada principal del sub-agente 4. `shares_per_contract` viene del
    contrato: darlo por hecho en 100 infla el nocional de los ajustados por el
    factor del ajuste.
    """
    return open_interest * shares_per_contract * strike


def _normalize_type(t: Any) -> str:
    """`normalizeType`: cualquier cosa que no sea "put" es un call."""
    return "put" if t == "put" else "call"


def to_row(raw: dict) -> ChainRow:
    """`toRow`: contrato crudo de Massive → fila lista.

    Los campos ausentes caen a 0 / cadena vacía, como en el original: un
    contrato incompleto no debe tumbar la cadena entera.
    """
    details = raw.get("details") or {}
    day = raw.get("day") or {}
    oi = _num(raw.get("open_interest")) or 0
    strike = _num(details.get("strike_price")) or 0
    shares = _num(details.get("shares_per_contract"))
    if shares is None:
        shares = DEFAULT_SHARES_PER_CONTRACT
    price, source = contract_price(raw)
    return ChainRow(
        option_ticker=str(details.get("ticker") or ""),
        contract_type=_normalize_type(details.get("contract_type")),  # type: ignore[arg-type]
        expiration=str(details.get("expiration_date") or ""),
        strike=strike,
        open_interest=int(oi),
        volume=int(_num(day.get("volume")) or 0),
        price=price,
        price_source=source,
        open_premium=open_premium(oi, price),
        notional_value=notional_value(oi, strike, shares),
    )


def sort_by_open_interest_desc(rows: Sequence[ChainRow]) -> list[ChainRow]:
    """Ordena por Open Interest de mayor a menor. Devuelve una lista nueva.

    No muta la de entrada — el original lo deja explícito con `[...rows]` y hay
    llamadores que siguen usando el orden original después.
    """
    return sorted(rows, key=lambda r: r.open_interest, reverse=True)


def count_expirations(rows: Sequence[ChainRow]) -> int:
    """Vencimientos distintos presentes. Las cadenas vacías no cuentan."""
    return len({r.expiration for r in rows if r.expiration})
