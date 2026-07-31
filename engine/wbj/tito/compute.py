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

import math
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
    if v is None:
        return fallback
    # Los booleanos SÍ se convierten aquí (`true * 5 === 5`), al revés que en la
    # regla estricta, donde `typeof true === "boolean"` los rechaza. Son las dos
    # reglas de Víctor haciendo cosas distintas con el mismo valor, a propósito.
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        try:
            n = float(str(v).strip())
        except (TypeError, ValueError):
            return fallback
    # `float("NaN")` y `float("inf")` PARSEAN. Sin este filtro un
    # `open_interest: "NaN"` llegaba entero hasta `int(oi)`, que lanza
    # ValueError y tumba la cadena completa desde dentro del cliente HTTP.
    if not math.isfinite(n):
        return fallback
    # DIVERGENCIA declarada: los negativos caen al fallback. Estos cuatro campos
    # son cantidades —contratos abiertos, contratos negociados, un precio de
    # ejercicio, acciones por contrato— y ninguna puede ser negativa. Víctor los
    # arrastra porque JS no distingue, y el resultado es que **una sola fila
    # corrupta tira la cadena entera**: con un OI de -900.000 en una cadena por
    # lo demás sana, el nocional pasaba de +$900M a -$8.100M, Estructura de 3/15
    # a 1/15, y se encendía la salvaguarda de baja liquidez sobre una cadena
    # perfectamente líquida. Un nocional que RESTA es peor que uno que falta.
    return n if n >= 0 else fallback


#: Marca "el campo no venía", distinta de "vino y no se entiende".
_AUSENTE = object()


def _shares(v: Any) -> float:
    """Acciones por contrato, distinguiendo AUSENTE de ILEGIBLE.

    `?? 100` de Víctor solo rellena cuando el campo falta. Si viene y no se
    puede leer (`"abc"`, `[]`, `""`), él acaba con un nocional `NaN`.

    Aquí no se puede caer al 100: sería **inventar** el multiplicador estándar
    justo cuando no hay evidencia de cuál es — que es exactamente el bug que
    motivó portar este módulo, reintroducido por la puerta de atrás. Medido:
    con `shares_per_contract: "abc"` el fallback a 100 daba un nocional de
    $900.000 sobre un contrato del que no sabemos nada.

    Sin multiplicador fiable el nocional es 0, que además enciende la
    salvaguarda de baja liquidez del sub-agente 4 en vez de fabricar un número.
    """
    if v is None or v is _AUSENTE:
        return DEFAULT_SHARES_PER_CONTRACT
    return _coerce(v, 0)


def _obj(v: Any) -> dict:
    """El `?.` de Víctor: si no es un objeto, leerle un campo da `undefined`.

    `raw.get("details") or {}` solo cubre `None` y el dict vacío. Si Massive
    manda `details` como texto o número —la misma clase de cambio de esquema que
    ya apareció dos veces— el `.get` lanza `AttributeError`, y como esto corre
    dentro del bucle de `fetch_option_chain`, **una sola fila malformada tumba
    la página entera** y la cadena sale vacía.
    """
    return v if isinstance(v, dict) else {}


def contract_price(raw: dict) -> tuple[float | None, PriceSource]:
    """`contractPrice`: precio del contrato para el Open Premium.

    La fórmula del agente pide **BID**, pero el plan actual de Massive no
    devuelve quotes, así que cae a `last_trade` → `day.close` → `day.vwap`.
    Cuando haya bid, se cambia aquí y solo aquí.

    Devuelve también de dónde salió, porque un Open Premium calculado sobre el
    VWAP del día no vale lo mismo que uno sobre el último trade, y quien lo lea
    tiene derecho a saberlo.
    """
    raw = _obj(raw)

    def usable(v: float | None) -> bool:
        # DIVERGENCIA declarada: un precio infinito NO se acepta. En JS
        # `Infinity > 0` es `true`, así que Víctor lo devuelve como precio y el
        # Open Premium sale `Infinity` — que su `JSON.stringify` convierte a
        # `null`, así que su archivo sigue siendo válido. `json.dumps` escribiría
        # `Infinity`, que NO es JSON, y estos dos campos existen precisamente
        # para servir la tabla de la cadena. Es el mismo fallo que apareció en
        # `store.ts`. Un precio infinito es basura: cae al siguiente de la
        # cascada, que es lo que ya se hace con el 0 y los negativos.
        return v is not None and v > 0 and math.isfinite(v)

    lt = _num(_obj(raw.get("last_trade")).get("price"))
    if usable(lt):
        return lt, "last_trade"
    day = _obj(raw.get("day"))
    close = _num(day.get("close"))
    if usable(close):
        return close, "day_close"
    vwap = _num(day.get("vwap"))
    if usable(vwap):
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
    """`normalizeType`: cualquier cosa que no sea "put" es un call.

    DIVERGENCIA declarada: se compara en minúsculas. Víctor hace `t === "put"`
    exacto, así que un `"PUT"` se convierte en **call** — y ese fallo no avisa,
    miente. Medido con una cadena entera en mayúsculas:

        "put"  →  GEX total  -13,614,827  ·  régimen negative
        "PUT"  →  GEX total  +27,229,653  ·  régimen positive

    Todos los puts pasan a contarse como calls: el GEX neto cambia de signo, el
    put wall desaparece y el régimen se invierte. Es la señal central del motor
    entero decidida por el `case` de un string de la fuente.
    """
    return "put" if str(t or "").strip().lower() == "put" else "call"


def _finito(v: float | None, fallback: float | None) -> float | None:
    """Recorta un resultado no finito al salir de las fórmulas.

    Las dos multiplicaciones pueden **desbordar aunque las entradas sean
    finitas**: `1e200 * 100 * 1e200` es `inf`. Filtrar solo lo que ENTRA dejaba
    el agujero abierto por el otro lado, y un `inf` acaba en `json.dumps` como
    `Infinity`, que no es JSON. Es el mismo fallo que el `NaN` de `store.ts`, y
    el arreglo del precio infinito estaba a medias sin esto.

    Se recorta aquí y no dentro de `notional_value` / `open_premium` para que
    esas dos sigan siendo el port literal de sus fórmulas.
    """
    if v is None or not math.isfinite(v):
        return fallback
    return v


def to_row(raw: dict) -> ChainRow:
    """`toRow`: contrato crudo de Massive → fila lista.

    Los campos ausentes caen a 0 / cadena vacía, como en el original: un
    contrato incompleto no debe tumbar la cadena entera.
    """
    raw = _obj(raw)
    details = _obj(raw.get("details"))
    day = _obj(raw.get("day"))
    oi = _coerce(raw.get("open_interest"), 0)
    strike = _coerce(details.get("strike_price"), 0)
    shares = _shares(details.get("shares_per_contract", _AUSENTE))
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
        # `?? ""`, no `or ""`: solo el ausente cae a vacío. Con `or`, un ticker
        # `0` o `False` —basura, pero basura que la fuente mandó— se borraba y
        # quedaba indistinguible de "no vino".
        option_ticker="" if details.get("ticker") is None else str(details["ticker"]),
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
        # Recortados: el producto desborda a `inf` con entradas finitas.
        open_premium=_finito(open_premium(oi_int, price), None),
        notional_value=_finito(notional_value(oi_int, strike, shares), 0.0),
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
