"""Port de `compute.ts` — las fórmulas de la cadena, sin I/O.

Funciones puras: convierten el contrato crudo del Option Chain Snapshot de
Massive en la fila que consumen el sub-agente 4 (Estructura) y la tabla.

Vive aparte de `massive.py` por la misma razón que en el original: el cliente
HTTP se ocupa de traer páginas, y las fórmulas se pueden probar sin red.

**Su lógica, con cinco guardas para datos malformados.** El criterio, medido y
no de estilo: con datos bien formados el port es **idéntico** al suyo (500/500
filas en el diferencial); las guardas solo actúan sobre entradas que su propio
código maneja mal, y ahí no hay nada que preservar. Auditado en su repo: las
guardas no están en ninguna parte —`massive.ts` mete `json.results` sin validar
y `app/api/chain/route.ts` pasa `contracts.map(toRow)` directo a
`structureScore`—, así que este es el sitio.

Cada guarda lleva el número medido de lo que cuesta no tenerla, y las cinco
están propuestas para el upstream en `engine/scripts/upstream-tito-compute.patch`.
La coacción de tipos SÍ es literal (`_js_number` = `Number()` de JS), que era el
hallazgo grande: aplicar la regla estricta a los cuatro campos laxos llenaba la
cadena de ceros en silencio si la fuente cambiaba a números en texto.

Verificado con `engine/scripts/diff_compute.sh`, que ejecuta su `compute.ts`
real en Node y compara la salida campo a campo, separando lo idéntico de las
divergencias deliberadas.
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
    """`raw.x ?? fallback` + la aritmética de JS, y una guarda de CANTIDAD.

    La parte literal: el `??` solo cambia `null`/`undefined`; todo lo demás
    sobrevive y lo coacciona la multiplicación, así que un `"500"` sale 500.
    Portar aquí la regla estricta del precio era una divergencia cara — si
    Massive pasa a mandar los números como texto, Víctor sigue calculando y el
    port se llenaba de ceros **sin un solo error**.

    El valor que se guarda en el campo es el número, no el crudo. En su JSON
    `openInterest` sale como el string `"500"`; aquí sale `500.0`, porque el
    resto del motor suma ese campo y en Python un string no se suma. El valor
    calculado es el mismo.

    GUARDA (divergencia deliberada): los cuatro campos que pasan por aquí —open
    interest, strike, volumen, acciones por contrato— son **cantidades**, y
    ninguna puede ser negativa ni no finita. Víctor las arrastra porque JS no
    distingue, y basta UNA fila para tirar la cadena entera:

        una fila con OI -900.000 en una cadena por lo demás sana
        nocional total   +$900M  →  -$8.100M       (cambia de signo)
        Estructura         3/15  →  1/15
        baja liquidez     false  →  true           (sobre una cadena líquida)

    Un nocional que RESTA no se distingue de un mercado bajista real. Y un
    `"NaN"` o un `"abc"` envenenan la suma entera del sub-agente 4, que además
    deja de poder decir "no hay datos": `NaN` no es ni un número ni una ausencia.
    Cayendo al fallback (0) la fila deja de contar y se enciende la salvaguarda
    de baja liquidez, que es la señal correcta.
    """
    if v is None:
        return fallback
    n = _js_number(v)
    if not math.isfinite(n) or n < 0:
        return fallback
    return n


#: Marca "el campo no venía", distinta de "vino y no se entiende".
_AUSENTE = object()


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
    """Acciones por contrato, distinguiendo AUSENTE de ILEGIBLE.

    El `?? 100` de Víctor solo rellena cuando el campo falta. Si viene y no se
    puede leer (`"abc"`, `{}`), él acaba con un nocional `NaN`; si viene como
    `""` o `[]`, con un nocional 0 (porque `Number("") === 0`).

    GUARDA (divergencia deliberada): aquí no se puede caer al 100, porque sería
    **inventar** el multiplicador estándar justo cuando no hay evidencia de cuál
    es — que es exactamente el bug que motivó portar este módulo, entrando por
    la puerta de atrás. Medido: con `shares_per_contract: "abc"` el fallback a
    100 daba un nocional de $900.000 sobre un contrato del que no sabemos nada.

    Sin multiplicador fiable el nocional es 0, que enciende la salvaguarda de
    baja liquidez del sub-agente 4 en vez de fabricar un número.
    """
    if v is None or v is _AUSENTE:
        return DEFAULT_SHARES_PER_CONTRACT
    return _coerce(v, 0)


def _finito(v: float | None, fallback: float | None) -> float | None:
    """Recorta un resultado no finito al salir de las fórmulas.

    Las dos multiplicaciones pueden **desbordar aunque las entradas sean
    finitas**: `1e200 * 100 * 1e200` es `inf`. Filtrar solo lo que ENTRA dejaba
    el agujero abierto por el otro lado, y un `inf` que llega a la suma del
    sub-agente 4 se lleva el total entero.

    Se recorta aquí y no dentro de `notional_value` / `open_premium` para que
    esas dos sigan siendo el port literal de sus fórmulas.
    """
    if v is None or not math.isfinite(v):
        return fallback
    return v


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
        # `typeof x === "number" && x > 0` + una GUARDA: un precio infinito NO
        # se acepta. En JS `Infinity > 0` es `true`, así que Víctor lo devuelve
        # como precio y el Open Premium sale `Infinity`. Un precio infinito es
        # basura: cae al siguiente de la cascada, que es lo que ya se hace con
        # el 0 y con los negativos. Sin esto, el `day.close` perfectamente
        # bueno que viene detrás nunca se llega a mirar.
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

    GUARDA (divergencia deliberada): se compara en minúsculas. Víctor hace
    `t === "put"` exacto, así que un `"PUT"` se convierte en **call** — y ese
    fallo no avisa, miente. Medido con una cadena entera en mayúsculas:

        "put"  →  GEX total  -13,614,827  ·  régimen negative
        "PUT"  →  GEX total  +27,229,653  ·  régimen positive

    Todos los puts pasan a contarse como calls: el GEX neto cambia de signo, el
    put wall desaparece y el régimen se invierte. Es la señal central del motor
    entero decidida por el `case` de un string de la fuente — y el `case` de un
    campo es exactamente lo que cambia en una migración de proveedor sin que
    nadie lo note. Es la guarda más barata y la más cara de no tener.
    """
    return "put" if str(t or "").strip().lower() == "put" else "call"


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
    # GUARDA: el open interest se trunca a entero. Es un CONTEO de contratos —no
    # existe medio contrato abierto— y Víctor lo arrastra tal cual solo porque
    # JS no distingue. Con datos reales no cambia nada: ninguna fuente manda un
    # OI fraccionario.
    oi_int = int(oi)
    return ChainRow(
        # `?? ""`, no `or ""`: solo el ausente cae a vacío. Con `or`, un ticker
        # `0` o `False` —basura, pero basura que la fuente mandó— se borraba y
        # quedaba indistinguible de "no vino". El `str()` es la guarda: el campo
        # se declara `str` y acaba en la tabla del panel.
        option_ticker="" if details.get("ticker") is None else _js_string(details["ticker"]),
        contract_type=_normalize_type(details.get("contract_type")),  # type: ignore[arg-type]
        # GUARDA: se recorta a YYYY-MM-DD. Víctor no lo hace porque su destino
        # es una tabla; aquí la cadena de vencimiento es la CLAVE con la que
        # agrupan el sub-agente 4 y el heatmap de GEX, y también la etiqueta del
        # eje. Sin canonizar, "2026-09-18" y "2026-09-18T00:00:00Z" son dos
        # vencimientos distintos: el mismo día parte en dos, con la mitad del
        # open interest cada uno, y `count_expirations` cuenta de más.
        expiration=str(details.get("expiration_date") or "")[:10],
        strike=strike,
        open_interest=oi_int,
        volume=int(_coerce(day.get("volume"), 0)),
        price=price,
        price_source=source,
        # Recortados: el producto desborda a `inf` con entradas finitas
        # (`1e200 * 100 * 1e200`), y un `inf` en la suma se lleva el total del
        # sub-agente 4. Filtrar solo lo que ENTRA dejaba el agujero por el otro
        # lado.
        open_premium=_finito(open_premium(oi_int, price), None),
        notional_value=_finito(notional_value(oi_int, strike, shares), 0.0),
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
