"""Port de `compute.ts` — las fórmulas de la cadena, sin I/O.

Funciones puras: convierten el contrato crudo del Option Chain Snapshot de
Massive en la fila que consumen el sub-agente 4 (Estructura) y la tabla.

Vive aparte de `massive.py` por la misma razón que en el original: el cliente
HTTP se ocupa de traer páginas, y las fórmulas se pueden probar sin red.

**Traducción literal, incluidas sus cinco lagunas.** Llegué a añadir guardas
para datos malformados y están todas quitadas: la instrucción es que el motor
sea exactamente el suyo. Lo que cada laguna cuesta está medido en
`engine/scripts/upstream-tito-compute.patch`, que es el parche listo para
mandárselo — pero el port no se adelanta a su archivo.

La coacción de tipos sí es literal hasta el detalle (`_js_number` = `Number()`
de JS, con sus rarezas: `Number("inf")` es NaN aunque `float("inf")` parsee,
`Number("1_000")` es NaN aunque Python acepte el guión bajo).

Lo ÚNICO que no se puede replicar es el tipo de tres campos: su `openInterest`
conserva el crudo (`"500"` como string) y aquí lleva el número, porque el resto
del motor suma esa columna y en Python un string no se suma. El valor calculado
es el mismo.

Verificado con `engine/scripts/diff_compute.sh`, que ejecuta su `compute.ts`
real en Node y compara la salida campo a campo.
"""

from __future__ import annotations

import math

from .jsmath import es_nulo, js_number as _js_number
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

#: Lo que `Number()` acepta como infinito, con SU capitalización exacta:
#: `Number("Infinity")` es infinito pero `Number("infinity")` y `Number("inf")`
#: son NaN — mientras que `float()` de Python parsea las tres sin distinguir.
#: Sin esta tabla el port convertía en infinito un texto que para él es basura.
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
    """`raw.x ?? fallback` seguido de la aritmética de JS. Literal.

    La parte literal: el `??` solo cambia `null`/`undefined`; todo lo demás
    sobrevive y lo coacciona la multiplicación, así que un `"500"` sale 500.
    Portar aquí la regla estricta del precio era una divergencia cara — si
    Massive pasa a mandar los números como texto, Víctor sigue calculando y el
    port se llenaba de ceros **sin un solo error**.

    El valor que se guarda en el campo es el número, no el crudo. En su JSON
    `openInterest` sale como el string `"500"`; aquí sale `500.0`, porque el
    resto del motor suma ese campo y en Python un string no se suma. El valor
    calculado es el mismo.

    Sin guarda de cantidad, como él: un open interest negativo entra negativo y
    un `"abc"` entra como `NaN`. Llegué a mandarlos al fallback —una sola fila
    con OI -900.000 invierte el nocional de la cadena entera y la declara
    ilíquida— y está QUITADO: el arreglo propuesto está en
    `engine/scripts/upstream-tito-compute.patch`, pero el port reproduce su
    archivo.
    """
    return fallback if es_nulo(v) else _js_number(v)


def _js_string(v: Any) -> str:
    """`String(v)` de JS, no el `str()` de Python.

    Solo lo usa `option_ticker`, que es el único campo de TEXTO al que la fuente
    puede mandarle un número. Los dos lenguajes no lo formatean igual:

        String(1e9)   → "1000000000"        str(1e9)   → "1000000000.0"
        String(true)  → "true"              str(True)  → "True"

    Sin esto, el `str()` que garantiza el tipo del campo introducía una
    diferencia con el original donde no hacía ninguna falta.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return str(int(v))
    return str(v)


def _shares(v: Any) -> float:
    """`raw.details?.shares_per_contract ?? 100`, literal.

    No distingue AUSENTE de ILEGIBLE: con `"abc"` el nocional sale `NaN` y con
    `""` sale 0, porque `Number("")` es 0. Llegué a separarlos —caer al 100 con
    un valor ilegible es inventar el multiplicador justo donde no hay evidencia
    de cuál es— y está quitado. Ver `upstream-tito-compute.patch`.
    """
    return DEFAULT_SHARES_PER_CONTRACT if es_nulo(v) else _js_number(v)


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
        # `Infinity > 0` es `true` en JS. El Open Premium sale `Infinity` y el
        # `_json_safe` del endpoint lo escribe como `null`, igual que su
        # `JSON.stringify`.
        return not es_nulo(v) and v > 0

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
    if es_nulo(price):
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

    Comparación EXACTA, la suya: un `"PUT"` en mayúsculas se convierte en
    **call**. Llegué a comparar en minúsculas y está quitado. Lo que cuesta,
    medido con una cadena entera en mayúsculas:

        "put"  →  GEX total  -13,614,827  ·  régimen negative
        "PUT"  →  GEX total  +27,229,653  ·  régimen positive

    Todos los puts pasan a contarse como calls: el GEX neto cambia de signo, el
    put wall desaparece y el régimen se invierte. Es la señal central del motor
    entero decidida por el `case` de un string de la fuente — y el `case` de un
    campo es exactamente lo que cambia en una migración de proveedor sin que
    nadie lo note. Hoy Massive manda minúsculas, así que no se dispara; el
    arreglo propuesto está en `upstream-tito-compute.patch`.
    """
    return "put" if t == "put" else "call"


def to_row(raw: dict) -> ChainRow:
    """`toRow`: contrato crudo de Massive → fila lista.

    Traducción literal. Los campos ausentes caen a 0 / cadena vacía, como en el
    original: un contrato incompleto no debe tumbar la cadena entera.

    Cinco guardas que llegué a poner aquí están QUITADAS —el case del tipo de
    contrato, los negativos, `shares` ilegible, el recorte del producto
    desbordado y la canonización del vencimiento—. Lo que cuesta cada una está
    medido en `engine/scripts/upstream-tito-compute.patch`; el port reproduce su
    archivo, no lo mejora.

    Lo único que no es traducción literal es el TIPO de tres campos numéricos:
    su `openInterest` conserva el crudo (`"500"` como string) y aquí lleva el
    número, porque el resto del motor suma esa columna y en Python un string no
    se suma. El valor calculado es el mismo — 3004/3004 filas en el diferencial.
    """
    raw = _obj(raw)
    details = _obj(raw.get("details"))
    day = _obj(raw.get("day"))
    open_interest = _coerce(raw.get("open_interest"), 0)
    strike = _coerce(details.get("strike_price"), 0)
    shares = _shares(details.get("shares_per_contract"))
    price, source = contract_price(raw)
    return ChainRow(
        # `?? ""`, no `or ""`: solo el ausente cae a vacío. Con `or`, un ticker
        # `0` o `False` —basura, pero basura que la fuente mandó— se borraría y
        # quedaría indistinguible de "no vino". El `String()` de JS y no el
        # `str()` de Python: `String(1e9)` es "1000000000", no "1000000000.0".
        option_ticker="" if es_nulo(details.get("ticker")) else _js_string(details["ticker"]),
        contract_type=_normalize_type(details.get("contract_type")),  # type: ignore[arg-type]
        expiration=("" if es_nulo(details.get("expiration_date"))
                    else details["expiration_date"]),
        strike=strike,
        open_interest=open_interest,
        volume=_coerce(day.get("volume"), 0),
        price=price,
        price_source=source,
        open_premium=open_premium(open_interest, price),
        notional_value=notional_value(open_interest, strike, shares),
    )


def sort_by_open_interest_desc(rows: Sequence[ChainRow]) -> list[ChainRow]:
    """Ordena por Open Interest de mayor a menor. Devuelve una lista nueva.

    No muta la de entrada — el original lo deja explícito con `[...rows]` y hay
    llamadores que siguen usando el orden original después.

    El comparador reproduce el suyo, incluida la regla del `NaN`: si un OI no se
    puede restar, ECMA-262 manda tratar el resultado como 0 ("iguales") y el
    sort estable deja la fila donde estaba. Por `to_row` ya no puede llegar un
    `NaN` —lo para `_coerce`—, pero esta función es pública y también la usan
    filas construidas a mano.
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
