"""Port de `barsStore.ts` — cache en disco de barras diarias, por día de mercado.

Su cabecera dice, palabra por palabra:

    Las barras diarias solo cambian una vez al día, pero el escaneo de Wheel
    las pide para 40 tickers en cada pasada. Sin cache serían 40 llamadas
    repetidas por escaneo. Solo servidor.

    `fetchDailyBars` sigue sin cache para el resto de rutas: este store es
    nuevo y en v1 solo lo usa Wheel.

El módulo tiene DOS capas, y la separación es lo que hace que se pueda verificar:

- **Sus tres funciones** (`load_bars`, `save_bars`, `cached_daily_bars`) son el
  port de su archivo, comprobado ejecutándolo en Node (`diff_bars.sh`).
- **`daily_bars_for_panel`** es política de Vertex, no suya. Es lo que enchufa
  el cache al panel de Proyecciones, que es justo lo que su segunda frase dice
  que en v1 no se hacía. Va aparte, con su propia regla y sus propios tests.

Por qué se enchufa aquí y él no lo hizo: Proyecciones pide barras diarias en
CADA consulta y el panel se auto-refresca. Las barras diarias cambian una vez al
día. Es exactamente el motivo que él escribió para Wheel, aplicado a un panel
que consulta un ticker en vez de cuarenta.

Lo único que no es traducción literal de sus tres funciones es el manejo de
fichero: se reutilizan `_exclusive`, `_read` y `_write` de `stores.py`, que
añaden cerrojo y escritura atómica sin cambiar nada de lo observable con una
petición a la vez.

Sus DOS bugs de `loadBars` (`as BarsFile` no comprueba nada) están portados tal
cual. La guarda que los tapaba se movió al borde de Vertex
(`borde.barras_utiles`), que es donde su pipeline la tiene: sus barras salen de
`fetchDailyBars`, que devuelve `DailyBar[]` construidos por él, no JSON ajeno.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .borde import barras_utiles
from .levels import LvlBar
from .occ import market_date_str
from .stores import _exclusive, _prop, _read, _sanea_ticker, _write, data_dir

__all__ = [
    "BarsFile",
    "load_bars",
    "save_bars",
    "cached_daily_bars",
    "daily_bars_for_panel",
]


@dataclass(frozen=True)
class BarsFile:
    """`BarsFile`: el ticker, el día de mercado (ET) en que se guardó y las barras.

    `bars` va sin tipar a propósito. En TS, `JSON.parse(raw) as BarsFile` no
    comprueba nada: lo que haya en el disco entra tal cual, y si el archivo
    trae `bars: "texto"` el campo acaba siendo un string. Tiparlo aquí como
    `list[LvlBar]` sería mentir sobre lo que puede contener.
    """

    ticker: str
    date: str
    bars: Any


def _file_for(ticker: str) -> Path:
    """`fileFor`, literal:

        const safe = ticker.trim().toUpperCase().replace(/[^A-Z0-9._-]/g, "");

    Es exactamente el mismo saneado que su `store.ts`, así que se comparte
    `_sanea_ticker` en vez de copiar el regex: dos saneados distintos para la
    misma entrada, en el mismo repo, es una trampa por sí sola.

    Sin guardas. Su regex ya cierra la travesía de rutas —borra las barras, así
    que `"../../etc/x"` se queda en `"....ETCX"`—; lo que NO cierra es el cubo
    compartido (`"!!!"`, `""` y `"ñ"` van todos al mismo `.json`). Esa guarda
    vive en el borde de Vertex (`borde.ticker_valido`), igual que en su repo
    vive en las rutas de Next.
    """
    return data_dir() / "bars" / f"{_sanea_ticker(ticker)}.json"


def _a_bar(d: object) -> Any:
    """`DailyBar` crudo → `LvlBar`, o el valor TAL CUAL si no lo es.

    En TS no hay conversión: los objetos del JSON ya sirven como `DailyBar` por
    su forma, y uno malformado se queda en la lista con sus campos en
    `undefined`. En Python hace falta un `LvlBar` para que `levels`,
    `validation` y compañía puedan leer `b.time`.

    Lo que no se hace es **descartar**: una barra ilegible se devuelve sin
    tocar. Filtrarla cambiaría `bars.length`, que es justo lo único que mira su
    `cachedDailyBars` — una lista de 250 barras rotas pasaría a valer 0 y el
    cache se recargaría donde el suyo no lo hace. Quién decide sobre una serie
    ilegible es `borde.barras_utiles`, no esta función.
    """
    if not isinstance(d, dict):
        return d
    try:
        return LvlBar(time=str(d["time"]), high=float(d["high"]),
                      low=float(d["low"]), close=float(d["close"]))
    except (KeyError, TypeError, ValueError):
        return d


def load_bars(ticker: str) -> BarsFile | None:
    """`loadBars`, literal:

        const raw = await fs.readFile(fileFor(ticker), "utf8");
        return JSON.parse(raw) as BarsFile;
        // catch → null

    Ese `as` es una afirmación para el compilador, no una comprobación: lo que
    haya en el archivo entra tal cual. **No se valida nada**, como él. De ahí
    salen sus dos bugs, portados a propósito:

        {"ticker":"A","date":hoy}                → `cached.bars.length` lanza
        {"ticker":"B","date":hoy,"bars":"texto"} → devuelve el string "texto"

    Ver `engine/scripts/upstream-tito-barsstore.patch` para el arreglo propuesto
    aguas arriba. Quien no quiera comerse ninguno de los dos pasa el resultado
    por `borde.barras_utiles`, que es lo que hace `daily_bars_for_panel`.

    El `None` sale solo donde sale el suyo: su `catch` cubre el archivo que no
    existe, el JSON roto y `JSON.parse("null")`. Los campos se leen con las
    reglas de JS (`_prop`), así que un archivo que en disco sea un array o un
    número da un `BarsFile` con los tres campos en `None` — igual que su
    `cached.date` sería `undefined` y el cache se descartaría por no coincidir.
    """
    parsed = _read(_file_for(ticker))
    if parsed is None:
        return None
    bars = _prop(parsed, "bars")
    return BarsFile(
        ticker=_prop(parsed, "ticker"),
        date=_prop(parsed, "date"),
        bars=[_a_bar(x) for x in bars] if isinstance(bars, list) else bars,
    )


def save_bars(ticker: str, bars: Sequence[LvlBar], now: datetime | None = None) -> None:
    """`saveBars`: guarda con el día de mercado en que se pidieron."""
    now = now or datetime.now(timezone.utc)
    path = _file_for(ticker)
    with _exclusive(path):
        _write(path, {
            "ticker": ticker.upper(),   # `ticker.toUpperCase()`, sin trim (él tampoco)
            "date": market_date_str(now),
            "bars": [{"time": b.time, "high": b.high, "low": b.low, "close": b.close}
                     for b in bars],
        })


def cached_daily_bars(
    ticker: str,
    days: int = 365,
    now: datetime | None = None,
    fetch=None,
) -> list[LvlBar]:
    """`cachedDailyBars`: barras diarias con cache de un día de mercado.

    Si falla la red devuelve `[]`, como el original.

    `fetch` solo existe para poder probar esto sin red; por defecto es
    `massive.fetch_daily_bars`. No forma parte del contrato de Víctor.
    """
    now = now or datetime.now(timezone.utc)
    today = market_date_str(now)

    cached = load_bars(ticker)
    # `if (cached && cached.date === today && cached.bars.length > 0)` — con
    # `len()`, no con la veracidad del objeto: es lo que hace que un `bars`
    # ausente lance (BUG 1, `len(None)` = TypeError) y que un `bars` de texto
    # pase la guarda y salga por el `return` (BUG 2). Los dos suyos, los dos
    # portados. `daily_bars_for_panel` es la que no se los come.
    if cached is not None and cached.date == today and len(cached.bars) > 0:
        return cached.bars

    if fetch is None:
        from .massive import fetch_daily_bars as fetch   # import tardío: evita el ciclo

    try:
        bars = list(fetch(ticker, days))
    except Exception:
        bars = []          # `.catch(() => [])`

    if bars:
        save_bars(ticker, bars, now)
    return bars


# ─────────────────────────────────────────────────────────────────────────
# Política de Vertex — NO es de Víctor
# ─────────────────────────────────────────────────────────────────────────
#
# `cachedDailyBars` cachea por día de mercado (ET) y punto. Eso le sirve al
# escaneo de Wheel, que corre una vez y pide 40 tickers de golpe. Para un panel
# que se consulta en vivo esa regla tiene tres agujeros, y los tres se midieron
# la primera vez que intenté enchufarlo:
#
#   1. A media sesión, la barra de HOY es parcial. Cachearla congela la última
#      vela y los niveles calculados sobre ella durante el resto del día.
#   2. Si Massive publica tarde, después del cierre el archivo se sella con la
#      fecha de hoy pero SIN la barra de hoy — y ya no se vuelve a pedir.
#   3. El fin de semana `market_date` devuelve sábado, que nunca coincide con la
#      fecha del cache: se pierde el cache justo los dos días en que las barras
#      no pueden cambiar.
#
# La política vive aquí y no dentro de `cached_daily_bars` para que aquella siga
# siendo el port literal de su función (27/27 en `diff_bars.sh`). Lo que sigue
# es decisión de Vertex y se prueba aparte.

#: Hora ET a partir de la cual la sesión del día se considera cerrada y las
#: barras diarias, definitivas. El cierre es a las 16:00; la hora extra es
#: margen para que el proveedor consolide el agregado del día.
_CIERRE_ET = 17


def _ultima_sesion_cerrada(now: datetime) -> str:
    """Último día laborable cuya sesión ya cerró, en `YYYY-MM-DD` (ET).

    Sin calendario de festivos a propósito: un festivo hace que este cálculo
    apunte a un día sin barras, el cache se considera viejo y se pide de nuevo.
    O sea que el error posible es **una petición de más**, nunca un dato viejo.
    Meter un calendario de festivos sería una dependencia nueva para ahorrar una
    llamada cuatro veces al año.
    """
    from .occ import MARKET_TZ

    if now.tzinfo is None:
        # Mismo criterio que `occ.market_date`: un naive se lee como UTC y no en
        # la zona de la máquina. Si no, el corte —y con él la validez del
        # cache— dependería de la TZ del servidor.
        now = now.replace(tzinfo=timezone.utc)
    et = now.astimezone(MARKET_TZ)
    d = et.date()
    if et.hour < _CIERRE_ET:
        d -= timedelta(days=1)      # la sesión de hoy aún no está consolidada
    while d.weekday() >= 5:         # sábado (5) y domingo (6)
        d -= timedelta(days=1)
    return d.isoformat()


def daily_bars_for_panel(
    ticker: str,
    days: int = 365,
    now: datetime | None = None,
    fetch=None,
) -> list[LvlBar]:
    """Barras diarias para el panel de Proyecciones, con cache seguro.

    La regla es una sola y se ancla en **el dato, no en el reloj**: una serie
    vale mientras su última barra sea la de la última sesión cerrada. Eso
    resuelve los tres agujeros de golpe:

    - **fin de semana / festivo** — la serie del viernes sigue siendo la última
      sesión cerrada el sábado y el domingo, así que el cache SÍ sirve;
    - **Massive publica tarde** — la serie sin la barra de hoy no llega a la
      última sesión cerrada, así que no se cachea y se vuelve a pedir;
    - **a media sesión** — antes del cierre, la última sesión cerrada es la de
      ayer, y la serie de ayer sirve: la barra de hoy está a medio hacer y no
      tiene por qué entrar. El spot en vivo lo pone la cadena, no las barras.

    Y una cuarta guarda: nunca se reemplaza una serie por otra **más corta**.
    Una página truncada o un rate limit devuelven menos barras, y sellarlas en
    el cache recortaba el histórico en silencio — con el año de barras que
    necesitan `levels` y el sub-agente 6 dependiendo de ello.

    Y una quinta: el archivo se lee por `borde.barras_utiles`, así que los dos
    bugs del `as BarsFile` de su `loadBars` —que aquí están portados tal cual—
    no llegan al panel. Un cache ilegible se trata como un cache que no está.

    `fetch` solo existe para poder probar esto sin red.
    """
    now = now or datetime.now(timezone.utc)
    corte = _ultima_sesion_cerrada(now)

    cache = barras_utiles(load_bars(ticker))
    if cache is not None and cache[-1].time >= corte:
        return cache

    if fetch is None:
        from .massive import fetch_daily_bars as fetch   # import tardío: evita el ciclo
    bars = list(fetch(ticker, days))

    if bars and bars[-1].time >= corte:
        _guarda_si_no_acorta(ticker, bars, now)
    return bars


def _guarda_si_no_acorta(ticker: str, bars: Sequence[LvlBar], now: datetime) -> bool:
    """Escribe el cache salvo que acorte lo que ya hay. Decide DENTRO del cerrojo.

    La comparación tiene que ir bajo el mismo cerrojo que la escritura, no sobre
    el `load_bars` que hizo el llamador. Con dos peticiones simultáneas del
    mismo ticker —el panel se auto-refresca y Render corre varios workers—, las
    dos leen el cache vacío, las dos deciden que pueden escribir, y **la última
    en llegar gana**: una respuesta truncada de 5 barras pisaba una serie buena
    de 250. Medido, y es justo el fallo que esta guarda existe para evitar.

    Es el mismo patrón leer-decidir-escribir sin cerrojo que costó ocho
    hallazgos en `store.ts`.
    """
    with _exclusive(_file_for(ticker)):
        # Relectura bajo cerrojo: el estado de antes puede ser de hace dos
        # llamadas de red. `_exclusive` es reentrante por hilo, así que el
        # `save_bars` de dentro no se bloquea a sí mismo.
        actual = barras_utiles(load_bars(ticker))
        if actual is not None and len(bars) < len(actual):
            return False
        save_bars(ticker, bars, now)
        return True
