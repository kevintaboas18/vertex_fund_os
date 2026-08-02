"""El borde de Vertex — las guardas que en su repo viven FUERA de las librerías.

Este módulo existe por una decisión explícita: **sus archivos son literales**.
`store.ts`, `compute.ts` y `barsStore.ts` se portan tal cual, sin añadirles
comprobaciones que él no escribió, porque cada añadido es una divergencia que el
diferencial tiene que declarar y una razón menos para poder decir "esto es su
motor".

Pero las guardas que se quitaron de ahí no protegían de nada imaginario: cada
una tapaba un fallo medido. La salida no es borrarlas, es **ponerlas donde su
propio pipeline ya las tiene**. En su proyecto de Next.js las librerías nunca
reciben datos crudos del mundo:

- el ticker llega a sus rutas por `searchParams`, se normaliza y se rechaza el
  vacío ANTES de tocar un store;
- las filas de trades pasan por el filtro de `/api/validation`
  (`t.assetPrice > 0 && t.timestamp`), que en TypeScript tira sola cualquier
  fila que no sea un objeto, porque `"basura".assetPrice` es `undefined`;
- las barras las produce `fetchDailyBars`, que ya devuelve `DailyBar[]`
  construidos por él, no JSON ajeno.

Vertex no es Next.js: aquí el borde es FastAPI y el disco. Así que el borde se
escribe, y se escribe una sola vez, aquí.

**Regla de oro de este archivo:** nada de lo que hay aquí puede cambiar un
número. Solo decide qué entra al motor y qué se queda fuera. Si algo de aquí
empieza a calcular, va en el módulo que le toca.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .levels import LvlBar
from .massive import DailyBar
from .stores import _sanea_ticker

__all__ = [
    "MAX_TICKER_LEN",
    "TickerInvalido",
    "ticker_valido",
    "trades_utiles",
    "trades_sin_id",
    "barras_utiles",
]

#: Tope de longitud del ticker. El símbolo más largo de EE. UU. tiene 5 letras;
#: con sufijos de clase y de warrant (`BRK.B`, `XYZ.WS`) no pasa de 10. 24 deja
#: margen de sobra y corta el nombre de archivo absurdo antes de que llegue al
#: sistema de ficheros.
MAX_TICKER_LEN = 24


class TickerInvalido(ValueError):
    """El ticker no sirve para nombrar un archivo ni para pedir datos."""


def ticker_valido(ticker: Any) -> str:
    """Ticker normalizado y utilizable, o `TickerInvalido`.

    Su `fileFor` hace `ticker.trim().toUpperCase().replace(/[^A-Z0-9._-]/g, "")`
    y nada más. Ese regex ya cierra la travesía de rutas —borra las barras, así
    que `"../../etc/x"` se queda en `"....ETCX"`—, pero **no** cierra el cubo
    compartido: `"!!!"`, `"@@@"`, `"ñ"` y `""` sanean todos a la cadena vacía y
    acaban escribiendo en el mismo `.json`. La memoria de un ticker se mezclaría
    con la de otro sin un solo error por medio.

    En su repo eso no pasa porque sus rutas rechazan el ticker vacío antes de
    llamar a ningún store. Aquí hace lo mismo esta función, y por eso el store
    puede quedarse literal.

    Devuelve el ticker YA saneado: el llamador debe pasar este valor hacia
    abajo, no el original, para que lo que se pide a la red y lo que nombra el
    archivo sean la misma cadena.
    """
    if not isinstance(ticker, str):
        raise TickerInvalido("El ticker tiene que ser texto.")
    safe = _sanea_ticker(ticker)
    # Se exige al menos una letra o un dígito, no solo "que quede algo": su
    # regex conserva el punto y el guion, así que `"..."` y `"../.."` (que se
    # queda en `"...."`) sobreviven al saneado y estrenarían un `.json` propio
    # lleno de basura. Un ticker sin un solo carácter alfanumérico no existe.
    if not re.search(r"[A-Z0-9]", safe):
        raise TickerInvalido(
            f"Ticker inválido: {ticker.strip()!r} no deja ninguna letra ni dígito "
            "(solo se conservan A-Z, 0-9, punto, guion y guion bajo)."
        )
    if len(safe) > MAX_TICKER_LEN:
        raise TickerInvalido(
            f"Ticker demasiado largo ({len(safe)} caracteres, máximo {MAX_TICKER_LEN})."
        )
    return safe


def trades_utiles(trades: Iterable[Any]) -> list[dict]:
    """Las filas de un historial en disco que son objetos, y solo esas.

    `load_trades` devuelve el array tal como está en el archivo, como su
    `loadTrades` (que solo mira `Array.isArray(parsed.trades)`; el contenido no
    lo toca). Un archivo a medio escribir, editado a mano o venido de una
    versión anterior puede traer un `null` o un string entre los trades.

    En TypeScript esa fila se cae sola en el primer filtro que la mire
    —`"basura".assetPrice` es `undefined`, y `undefined > 0` es `false`—, así
    que su `/api/validation` nunca la ve. En Python `"basura".get(...)` es un
    `AttributeError` que tumba la petición entera. Este filtro es esa misma
    caída, escrita.
    """
    return [t for t in trades if isinstance(t, dict)]


def trades_sin_id(trades: Iterable[Any]) -> int:
    """Cuántas filas guardadas no tienen `id`. Es un contador, no un filtro.

    La clave de dedupe de su `saveTrades` es `t.id` a secas. Si el tape deja de
    traer ese campo, `flow._base_row` mete 0 en todos y el `Map` conserva **un
    solo trade** de la corrida entera — sin error, y `added` tampoco avisa
    porque a partir del segundo la clave "ya existe". Es su comportamiento y se
    porta tal cual (ver `engine/scripts/upstream-tito-store.patch`).

    Lo único que no se puede dejar es que pase MUDO: la memoria del sub-agente 6
    se acumula hacia adelante y lo que no se guardó no se recupera. Este número
    sube a `stats` y a `/api/tito-health` para que el fallo se vea el mismo día.
    """
    return sum(1 for t in trades if not isinstance(t, dict) or t.get("id") is None)


def barras_utiles(cache: Any) -> list[LvlBar] | None:
    """Las barras de un `BarsFile` leído de disco, o `None` si no son barras.

    `loadBars` hace `JSON.parse(raw) as BarsFile`. Ese `as` es una afirmación
    para el compilador, no una comprobación: lo que haya en el archivo entra tal
    cual. De ahí salen sus dos bugs, los dos portados literalmente:

        {"ticker":"A","date":hoy}                → `cached.bars.length` lanza
        {"ticker":"B","date":hoy,"bars":"texto"} → devuelve el string "texto"

    El segundo es el feo: `"texto".length > 0` es cierto, la guarda pasa, y el
    llamador recibe un string donde espera barras. Sin excepción: el análisis
    sigue, sobre basura y en silencio.

    Mientras el módulo no lo llamaba nadie los dos eran inertes. Desde que el
    panel de Proyecciones usa el cache, un archivo a medio escribir —un proceso
    muerto, un disco lleno— tumbaría la petición o la dejaría calculando sobre
    texto. Un cache ilegible es un cache que no está: devuelve `None`, se pide a
    la red y el archivo se reescribe solo.

    Se exige que TODAS las barras sean barras de verdad (`DailyBar` del cliente
    de Massive o el `LvlBar` que usa `levels`). Un cache medio bueno no es un
    ahorro: el histórico se usa entero para máximos, mínimos y toques, y una
    serie con huecos silenciosos es peor que una petición de más.
    """
    if cache is None:
        return None
    bars = getattr(cache, "bars", None)
    if not isinstance(bars, list) or not bars:
        return None
    if not all(isinstance(b, (LvlBar, DailyBar)) for b in bars):
        return None
    return bars
