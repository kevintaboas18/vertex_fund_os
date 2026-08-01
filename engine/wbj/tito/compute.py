"""Port de `compute.ts` — las fórmulas de la cadena, sin I/O.

Funciones puras: convierten el contrato crudo del Option Chain Snapshot de
Massive en la fila que consumen el sub-agente 4 (Estructura) y la tabla.

Vive aparte de `massive.py` por la misma razón que en el original: el cliente
HTTP se ocupa de traer páginas, y las fórmulas se pueden probar sin red.

**Traducción literal, incluidas sus lagunas.** Llegué a añadir aquí cinco
guardas para datos malformados y las cinco están quitadas: la instrucción es que
el motor sea exactamente el de Víctor. Auditado en su repo antes de quitarlas —
`massive.ts` mete `json.results` sin validar y `app/api/chain/route.ts` pasa las
filas directo a `structureScore`, así que `compute.ts` era el único sitio donde
podían estar, y no están. Lo que eso reabre está medido en
`engine/scripts/upstream-tito-compute.patch` y fijado por
`TestComportamientoLiteralDeVictor` en `engine/tests/tito/test_compute.py`.

Verificado con `engine/scripts/diff_compute.sh`, que ejecuta su
`compute.ts` real en Node y compara la salida campo a campo.
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

#: Acciones por contrato de un contrato estándar (`?? 100` en su `toRow`). Los
#: ajustados traen otro número en `details.shares_per_contract`.
DEFAULT_SHARES_PER_CONTRACT = 100

#: Lo que `Number()` de JS acepta como infinito y Python no (y al revés):
#: `Number("Infinity")` es infinito pero `Number("inf")` es NaN, mientras que
#: `float("inf")` parsea las dos. Sin esta tabla el port convertía en infinito
#: un texto que para él es basura.
_INFINITOS_JS = {"infinity": math.inf, "+infinity": math.inf, "-infinity": -math.inf}


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


def _js_number(v: Any) -> float:
    """`Number(v)` de JS: la coacción que hace su aritmética con el valor crudo.

    Es lo que convierte su `openInterest: "500"` en 500 al multiplicar, y su
    `volume: true` en 1. Lo único que no se reproduce es el literal hexadecimal
    (`Number("0x1A") === 26`), que ninguna fuente manda.
    """
    if v is None:
        return 0.0                     # `Number(null) === 0`
    if isinstance(v, bool):
        return 1.0 if v else 0.0       # `Number(true) === 1`
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return 0.0                 # `Number("") === 0`
        if s.lower() in _INFINITOS_JS:
            return _INFINITOS_JS[s.lower()]
        try:
            n = float(s)
        except ValueError:
            return math.nan
        # `float("inf")` y `float("nan")` parsean en Python; `Number("inf")` no.
        return math.nan if not math.isfinite(n) else n
    if isinstance(v, (list, tuple)):
        # `Number([]) === 0`, `Number([7]) === 7`, `Number([1,2])` es NaN.
        if not v:
            return 0.0
        return _js_number(v[0]) if len(v) == 1 else math.nan
    return math.nan                    # `Number({})` es NaN


def _coerce(v: Any, fallback: float) -> float:
    """`raw.x ?? fallback`, y después la aritmética de JS sobre el resultado.

    El `??` solo cambia `null`/`undefined`; todo lo demás sobrevive y lo coacciona
    la multiplicación. Un `"500"` sale 500 y un `"abc"` sale NaN, que es lo que
    hace que su nocional acabe en `NaN` (y en `null` al serializar).

    El valor que se guarda en el campo es el número, no el crudo: es la única
    concesión al lenguaje. En su JSON `openInterest` sale como el string
    `"500"`; aquí sale `500.0`, porque el resto del motor suma ese campo y en
    Python un string no se suma. **El valor calculado es el mismo.**
    """
    return fallback if v is None else _js_number(v)


def _obj(v: Any) -> dict:
    """El `?.` de Víctor: si no es un objeto, leerle un campo da `undefined`.

    `raw.get("details") or {}` solo cubre `None` y el dict vacío. Si Massive
    manda `details` como texto o número —la misma clase de cambio de esquema que
    ya apareció dos veces— el `.get` lanza `AttributeError`, y como esto corre
    dentro del bucle de `fetch_option_chain`, una sola fila malformada tumbaría
    la página entera. En TS eso es `undefined` y sigue; aquí también.
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
        # `typeof x === "number" && x > 0`, literal. Un precio infinito PASA:
        # llegué a rechazarlo y no está en el original. Su Open Premium sale
        # `Infinity` y `JSON.stringify` lo escribe como `null`; aquí lo hace
        # `_json_safe` de `vertex_api` al serializar la respuesta.
        return v is not None and v > 0

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
    """`normalizeType`: `t === "put" ? "put" : "call"`, comparación EXACTA.

    Un `"PUT"` en mayúsculas se cuenta como **call**. Llegué a comparar en
    minúsculas y está quitado: no es lo que hace su archivo. Medido con una
    cadena entera en mayúsculas, lo que cuesta es la señal central del motor:

        "put"  →  GEX total  -13,614,827  ·  régimen negative
        "PUT"  →  GEX total  +27,229,653  ·  régimen positive

    Massive manda `"call"`/`"put"` en minúsculas, así que hoy no se dispara. Si
    algún día cambia el `case`, el aviso está aquí y el arreglo en
    `engine/scripts/upstream-tito-compute.patch`.
    """
    return "put" if t == "put" else "call"


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
    shares = _coerce(details.get("shares_per_contract"), DEFAULT_SHARES_PER_CONTRACT)
    price, source = contract_price(raw)
    return ChainRow(
        # `?? ""`, no `or ""`: solo el ausente cae a vacío. Con `or`, un ticker
        # `0` o `False` —basura, pero basura que la fuente mandó— se borraba y
        # quedaba indistinguible de "no vino". El valor crudo pasa tal cual.
        option_ticker="" if details.get("ticker") is None else details["ticker"],
        contract_type=_normalize_type(details.get("contract_type")),  # type: ignore[arg-type]
        # `?? ""` y nada más. Llegué a recortarlo a YYYY-MM-DD porque esta
        # cadena es la CLAVE con la que agrupan el sub-agente 4 y el heatmap;
        # está quitado. Massive manda la fecha sola, así que hoy no cambia nada:
        # si mandara `"2026-09-18T00:00:00Z"`, ese vencimiento contaría aparte.
        expiration="" if details.get("expiration_date") is None else details["expiration_date"],
        strike=strike,
        # Sin `int()`: su `openInterest` es un `number` y puede ser fraccionario.
        # El MISMO valor alimenta el campo y las dos fórmulas, así que la fila no
        # se contradice a sí misma.
        open_interest=oi,
        volume=_coerce(day.get("volume"), 0),
        price=price,
        price_source=source,
        # Sin recortar los no finitos: el producto desborda a `inf` con entradas
        # finitas (`1e200 * 100 * 1e200`) y un `"abc"` da `NaN`. Los dos salen
        # como `null` al serializar, igual que su `JSON.stringify`.
        open_premium=open_premium(oi, price),
        notional_value=notional_value(oi, strike, shares),
    )


def sort_by_open_interest_desc(rows: Sequence[ChainRow]) -> list[ChainRow]:
    """Ordena por Open Interest de mayor a menor. Devuelve una lista nueva.

    No muta la de entrada — el original lo deja explícito con `[...rows]` y hay
    llamadores que siguen usando el orden original después.

    Con un OI `NaN` su comparador devuelve `NaN`, que ECMA-262 trata como 0
    ("iguales"), y el sort estable deja la fila donde estaba. `_clave` reproduce
    eso: los no finitos comparan como iguales entre sí y con todo lo demás.
    """
    from functools import cmp_to_key

    def cmp(a: ChainRow, b: ChainRow) -> int:
        d = _js_number(b.open_interest) - _js_number(a.open_interest)
        if d != d:          # NaN → SortCompare devuelve +0
            return 0
        return -1 if d < 0 else (1 if d > 0 else 0)

    return sorted(rows, key=cmp_to_key(cmp))


def count_expirations(rows: Sequence[ChainRow]) -> int:
    """Vencimientos distintos presentes. Las cadenas vacías no cuentan.

    `new Set(rows.map(r => r.expiration).filter(Boolean)).size` — el `filter`
    es por veracidad, así que `""` y `0` no cuentan pero `"0"` sí.
    """
    return len({r.expiration for r in rows if r.expiration})
