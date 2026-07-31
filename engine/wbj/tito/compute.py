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
    """`typeof x === "number"`: un string numérico NO cuenta.

    Es la regla ESTRICTA, y Víctor solo la usa para el **precio**. Tiene sentido
    que sea estricta ahí: un precio que llega en un tipo raro produciría un Open
    Premium silenciosamente equivocado, y es mejor caer al siguiente de la
    cascada. `typeof NaN === "number"` es `true` en JS, pero luego `NaN > 0` es
    `false`, así que el NaN también cae al siguiente — igual que aquí.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _coerce(v: Any, fallback: float) -> float:
    """La regla LAXA: `raw.x ?? fallback` seguido de aritmética de JS.

    Para OI, strike, volumen y acciones por contrato Víctor NO usa `typeof`:
    usa `??`, que solo cambia `null`/`undefined`. Un `"500"` sobrevive y luego
    `"500" * 100 * strike` lo convierte a número, así que su nocional sale bien.

    Portar la regla estricta a estos cuatro campos era una divergencia cara: si
    Massive pasa a mandar los números como texto, Víctor sigue calculando y este
    port se llenaba de ceros **sin un solo error** — la cadena entera con OI 0,
    Estructura y GEX a cero. Es el mismo fallo silencioso que apareció en
    `store.ts` cuando cambia el esquema de la fuente.

    Diferencia con JS que sí se mantiene: la basura no numérica (`"abc"`) da
    `NaN` en JS y envenena el nocional en silencio; aquí cae al `fallback`, que
    además enciende la salvaguarda de baja liquidez del sub-agente 4.
    """
    if isinstance(v, bool) or v is None:
        return fallback
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return fallback


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
    oi = _coerce(raw.get("open_interest"), 0)
    strike = _coerce(details.get("strike_price"), 0)
    shares = _coerce(details.get("shares_per_contract"), DEFAULT_SHARES_PER_CONTRACT)
    price, source = contract_price(raw)
    # El MISMO `oi` alimenta el campo y las dos fórmulas. Redondear para el campo
    # y calcular con el crudo dejaba filas que se contradicen a sí mismas: OI 60
    # con un nocional calculado sobre 60.5.
    #
    # DIVERGENCIA declarada: el open interest se trunca a entero. Es un CONTEO de
    # contratos —no existe medio contrato abierto— y Víctor lo arrastra tal cual
    # solo porque JS no distingue. Con datos reales no cambia nada: ninguna
    # fuente manda un OI fraccionario.
    oi_int = int(oi)
    return ChainRow(
        option_ticker=str(details.get("ticker") or ""),
        contract_type=_normalize_type(details.get("contract_type")),  # type: ignore[arg-type]
        # DIVERGENCIA declarada: se recorta a YYYY-MM-DD. Víctor no lo hace
        # porque su destino es una tabla; aquí la cadena de vencimiento es la
        # CLAVE con la que agrupan el sub-agente 4 y el heatmap, y también la
        # etiqueta del eje. Sin canonizar, "2026-09-18" y "2026-09-18T00:00:00"
        # serían dos vencimientos distintos.
        expiration=str(details.get("expiration_date") or "")[:10],
        strike=strike,
        open_interest=oi_int,
        volume=int(_coerce(day.get("volume"), 0)),
        price=price,
        price_source=source,
        open_premium=open_premium(oi_int, price),
        notional_value=notional_value(oi_int, strike, shares),
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
