"""Las semánticas de JavaScript que el port tiene que reproducir.

Dos primitivas viven aquí porque las usa más de un módulo y porque en las dos
Python "casi" coincide con JS — que es la clase de diferencia que no se ve hasta
que alguien la mide: `Math.round` y `Date.parse`.

──────────────────────────── `Math.round` ────────────────────────────

Existe por un hallazgo medido: `round()` de Python y `Math.round()` de JS **no
son la misma función**. Python usa redondeo bancario (mitad al par) y JS redondea
la mitad hacia arriba:

    round(10.5) = 10        Math.round(10.5) = 11
    round(2.5)  = 2         Math.round(2.5)  = 3
    round(11.5) = 12        Math.round(11.5) = 12      ← aquí sí coinciden

No es un caso raro. Los sub-agentes puntúan promediando enteros
(`round((iv.points + rank.points) / 2)`), y ahí el `.5` sale constantemente: de
las 121 combinaciones de dos puntuaciones de 0 a 10, **30 caen en un `.5` donde
las dos funciones dan resultados distintos**. En todas ellas el port devolvía un
punto MENOS que él, y ese punto entra directo al scorecard de 0-100.

Lo descubrió el diferencial de `levels.ts`: una fuerza de nivel salía 10 donde su
archivo daba 11.

──────────────────────────── `Date.parse` ────────────────────────────

Vivía dentro de `stores.py` mientras solo lo usaba el orden de los trades. Subió
aquí al encontrar que `levels.recencyFactor` también cuenta el tiempo **con la
aritmética de JS** —milisegundos desde epoch, no días de calendario— y el port lo
hacía a su manera. Medido contra su archivo en Node: 4 de 28 combinaciones de
(último toque, hora de consulta) daban un factor de frescura distinto, siempre
más ALTO en el port, o sea niveles más fuertes de lo que él calcula. Ver
`js_days_since` y `engine/scripts/diff_frescura.sh`.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = ["js_round", "js_number", "js_gt", "js_add", "js_string",
           "js_date_parse", "js_time", "js_days_since", "js_locale_string",
           "MS_POR_DIA"]

#: `const DAY = 86_400_000` de su `levels.ts`.
MS_POR_DIA = 86_400_000


def js_round(x: float) -> float:
    """`Math.round(x)` de JS: la mitad SIEMPRE hacia arriba, no al par.

    ECMA-262 lo define como `floor(x + 0.5)`, incluidos los negativos —por eso
    `Math.round(-10.5)` es `-10` y no `-11`—, así que se implementa igual.

    Los no finitos pasan TAL CUAL, que es lo que dice la especificación:
    `Math.round(NaN)` es `NaN` y `Math.round(Infinity)` es `Infinity`. El port
    devolvía 0 para el `NaN`, y eso convertía un dato ilegible en un número
    perfectamente creíble —un DTE de 0, o sea "vence hoy"— justo donde él
    propaga el `NaN` y deja que se vea. Desde que `compute` es literal esto
    importa de verdad: un open interest `"abc"` llega como `NaN` y recorre el
    motor entero.

    Devuelve `float` y no `int` por lo mismo: en JS `Math.round` siempre da un
    Number, y un `NaN` no cabe en un `int` de Python.
    """
    if not math.isfinite(x):
        return x
    return float(math.floor(x + 0.5))


#: El *Date Time String Format* de ECMA-262, que es lo único que `Date.parse`
#: tiene definido: año solo, año-mes o fecha completa, con hora opcional. El año
#: extendido (`+002026-…`) también es del estándar.
#:
#: Lo que queda FUERA a propósito es el parseo *legacy* (`"Jul 30 2026"`,
#: `"$5"`, `"500"`). La propia especificación lo declara **implementation-
#: defined**: V8 lo resuelve con heurísticas propias —`Date.parse("500")` es el
#: año 500 y `Date.parse("$5")` es mayo de 2001— que otro motor no tiene por qué
#: compartir. Replicar eso sería copiar una peculiaridad de V8, no la lógica de
#: Víctor; aquí dan `NaN`, que es lo que ya devuelve cualquier otra cosa
#: ilegible. La fuente manda ISO.
_ISO_JS = re.compile(
    r"^([+-]\d{6}|\d{4})(?:-(\d{2})(?:-(\d{2}))?)?"
    r"(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3})\d*)?)?"
    r"(Z|z|[+-]\d{2}:?\d{2})?)?$"
)


def js_date_parse(v: Any) -> float:
    """`Date.parse(v)` en milisegundos, o `NaN` si no se puede.

    Reproduce su parseo salvo en un punto. La regla de ES2015+ no es uniforme:
    una fecha **sola** (`"2026-07-30"`) se lee en UTC, y una fecha-hora **sin
    offset** (`"2026-07-30T15:00:00"`) se lee en la zona LOCAL de la máquina.

    Se replica tal cual, incluida esa dependencia de la TZ del servidor: el
    mismo archivo se recorta distinto en UTC que en `America/New_York`, porque
    ese orden decide qué trade se cae por el tope de `MAX_PER_TICKER`. En Render
    y en el contenedor la TZ es UTC, así que en la práctica coinciden.
    """
    if not isinstance(v, str):
        return math.nan          # `Date.parse(undefined)`, `Date.parse(null)` → NaN
    m = _ISO_JS.match(v.strip())
    if not m:
        return math.nan
    y, mo, d, hh, mm, ss, ms, off = m.groups()
    # `"2026"` y `"2026-07"` son formatos válidos del estándar: el mes y el día
    # que falten valen 1. Sin esto, un timestamp truncado se ordenaba como
    # ilegible en vez de por su fecha.
    try:
        base = datetime(int(y), int(mo or 1), int(d or 1),
                        int(hh or 0), int(mm or 0),
                        int(ss or 0), int((ms or "0").ljust(3, "0")) * 1000)
    except ValueError:
        # Dos casos caen aquí:
        #  · `"2026-13-45"`, `"2026-02-30"` — la especificación pide una fecha
        #    válida en el formato ISO, así que `NaN`. (V8 cae a su parseo legacy
        #    y las desborda al mes siguiente; eso es cosa suya, no del estándar.)
        #  · el año extendido fuera del rango de `datetime`, que empieza en el
        #    año 1 y llega al 9999. `Date.parse("-000001-01-01T00:00:00Z")` sí
        #    da un número. Es un muro del lenguaje, no una decisión: un
        #    timestamp del año -1 no existe en este dominio.
        return math.nan
    if off is None and hh is not None:
        dt = base                                # fecha-hora sin offset → LOCAL
    elif off is None or off in ("Z", "z"):
        dt = base.replace(tzinfo=timezone.utc)
    else:
        o = off.replace(":", "")
        delta = timedelta(hours=int(o[1:3]), minutes=int(o[3:5]))
        dt = base.replace(tzinfo=timezone(-delta if o[0] == "-" else delta))
    try:
        return dt.timestamp() * 1000.0
    except (OSError, OverflowError, ValueError):   # fechas fuera del rango del SO
        return math.nan


def js_time(now: datetime) -> float:
    """`date.getTime()` de JS: milisegundos desde epoch.

    Un `datetime` naive se lee como UTC, que es el mismo criterio que usa
    `occ.market_date`. Sin eso el corte dependería de la zona del servidor y el
    mismo dato daría números distintos en Render y en local.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.timestamp() * 1000.0


def js_locale_string(x: float) -> str:
    """`Number.prototype.toLocaleString("en-US")`, con sus reglas por defecto.

    Agrupa de tres en tres con coma y usa **hasta** 3 decimales, sin rellenar:
    un entero sale sin parte decimal. El `f"{x:,}"` de Python no sirve para eso
    —sobre un `float` imprime `73,886.0`— y ese texto va al campo `why` de cada
    nivel, que es lo que lee el reporte.

    Los no finitos siguen su formato, que no es el de Python: `NaN` y `∞`.
    """
    if x != x:
        return "NaN"
    if math.isinf(x):
        return "∞" if x > 0 else "-∞"
    s = f"{x:,.3f}".rstrip("0").rstrip(".")
    return s or "0"


def js_days_since(fecha: Any, now: datetime, hora: str = "T21:00:00Z") -> float:
    """`(now.getTime() - Date.parse(`${fecha}${hora}`)) / DAY`, literal.

    Es la cuenta de días de su `levels.recencyFactor`, y lo que importa es que
    son días **fraccionarios**, no días de calendario. Su ancla es `T21:00:00Z`
    —el cierre de la sesión que produjo esa barra— así que dos consultas del
    mismo día caen a lados distintos del umbral según la hora.

    Medido contra su archivo en Node: contar días de calendario en vez de esto
    daba un factor de frescura más ALTO en 4 de 28 casos —siempre a partir de
    las 22:00 UTC, o sea desde las 6 de la tarde en Nueva York—, y un factor más
    alto es un nivel más fuerte del que él calcula.

    Devuelve `NaN` cuando la fecha no se parsea, que es lo que hace su
    `Date.parse`; el llamador decide (él responde 1).
    """
    return (js_time(now) - js_date_parse(f"{fecha}{hora}")) / MS_POR_DIA


_INFINITOS_JS = {"Infinity": math.inf, "+Infinity": math.inf, "-Infinity": -math.inf}

#: Bases que `Number()` entiende y `float()` no (`Number("0x1A") === 26`).
_BASES_JS = {"0x": 16, "0X": 16, "0o": 8, "0O": 8, "0b": 2, "0B": 2}


def js_number(v: Any) -> float:
    """`Number(v)` de JS: la coacción que hace su aritmética con el valor crudo.

    Es lo que convierte su `openInterest: "500"` en 500 al multiplicar, y su
    `volume: true` en 1.

    Los cuatro sitios donde `float()` de Python y `Number()` de JS **no** son lo
    mismo, todos medidos ejecutando Node sobre el mismo corpus:

        "1_000"     float() → 1000     ·  Number() → NaN   (Python permite `_`)
        "0x1A"      float() → error    ·  Number() → 26    (JS entiende bases)
        "infinity"  float() → inf      ·  Number() → NaN   (JS distingue el case)
        "inf"       float() → inf      ·  Number() → NaN
    """
    if v is None:
        return 0.0                     # `Number(null) === 0`
    if isinstance(v, bool):
        return 1.0 if v else 0.0       # `Number(true) === 1`
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()                  # `Number()` recorta el espacio en blanco
        if s == "":
            return 0.0                 # `Number("") === 0`
        if s in _INFINITOS_JS:
            return _INFINITOS_JS[s]
        if s[:2] in _BASES_JS:
            try:
                return float(int(s[2:], _BASES_JS[s[:2]]))
            except ValueError:
                return math.nan
        # `float()` acepta separadores de millar (`"1_000"`) y las constantes
        # `inf`/`nan` en cualquier capitalización; `Number()` no hace ninguna de
        # las dos. Sin este filtro un texto que para él es basura entraba como
        # número — y encima como uno grande.
        if "_" in s:
            return math.nan
        try:
            n = float(s)
        except ValueError:
            return math.nan
        return math.nan if not math.isfinite(n) else n
    if isinstance(v, (list, tuple)):
        # `Number([]) === 0`, `Number([7]) === 7`, `Number([1,2])` es NaN.
        if not v:
            return 0.0
        return js_number(v[0]) if len(v) == 1 else math.nan
    return math.nan                    # `Number({})` es NaN


def js_gt(v: Any, umbral: float = 0.0) -> bool:
    """`v > umbral` con las reglas de JS, sin lanzar.

    En JS un operador relacional coacciona con `ToNumber` antes de comparar, y
    un `NaN` hace que TODA comparación sea falsa. Por eso `undefined > 0`,
    `"abc" > 0` y `{} > 0` son los tres `false` — y `"5" > 0` es `true`.

    En Python `None > 0` es un `TypeError` que sube por la pila. Ahí está la
    diferencia que importa: donde su código descarta un flow con
    `assetPrice` ausente y sigue con los demás, el port tumbaba
    `validation_score` entero y el sub-agente 6 se quedaba sin score.
    Lo encontró el corpus malformado de `diff_motor.sh`.
    """
    return js_number(v) > umbral


def js_string(v: Any) -> str:
    """`String(v)` de JS. No es `str()` de Python en cuatro sitios.

        None   → "null"      (str() da "None")
        True   → "true"      (str() da "True")
        1e9    → "1000000000" (str() da "1000000000.0")
        [1, 2] → "1,2"       (str() da "[1, 2]")
    """
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        if v.is_integer() and abs(v) < 1e21:
            return str(int(v))
        return repr(v)
    if isinstance(v, (list, tuple)):
        return ",".join("" if x is None else js_string(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def js_add(a: Any, b: Any) -> Any:
    """El operador `+` de JS, que **no** es siempre una suma.

    ECMA-262: si alguno de los dos operandos es un string tras `ToPrimitive`,
    `+` CONCATENA; si no, convierte los dos a número y suma.

    Importa donde el motor acumula un campo que viene de fuera. Su
    `wTotal += o.premium` con un `premium` que llegue como `"500"` produce
    `"0500"`, luego `"0500500"`… y el `wTotal > 0` de después lo vuelve a
    convertir a número. Es un comportamiento raro pero es el suyo, y el port
    hacía una suma normal —o se caía con un `TypeError`—, que es otra cosa.

    Lo encontró el corpus malformado de `diff_motor.sh`. En condiciones
    normales los dos caminos coinciden: con números, esto es `a + b`.
    """
    if isinstance(a, str) or isinstance(b, str):
        return js_string(a) + js_string(b)
    if isinstance(a, (list, tuple, dict)) or isinstance(b, (list, tuple, dict)):
        # `ToPrimitive` de un array o un objeto da un string, así que concatena.
        return js_string(a) + js_string(b)
    return js_number(a) + js_number(b)
