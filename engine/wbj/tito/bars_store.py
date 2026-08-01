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

Dos cosas no son traducción literal, las dos declaradas en su sitio: el manejo
de fichero (se reutilizan `_exclusive`, `_read` y `_write` de `stores.py`) y la
validación de `load_bars`, que tapa los dos bugs de su archivo — inertes
mientras no lo llamaba nadie, reales desde que el panel lo usa.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .levels import LvlBar
from .occ import market_date_str
from .stores import _exclusive, _read, _sanea_ticker, _write, data_dir

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

    GUARDA (divergencia deliberada): usa el MISMO `_sanea_ticker` que
    `stores.py`, que además rechaza el ticker que se queda en nada (`"!!!"`,
    `""`, `"ñ"`) y el demasiado largo. Su regex los sanea todos a la cadena
    vacía y los manda al mismo `.json`.

    Mientras el módulo no lo llamaba nadie esto daba igual y se dejó literal. Al
    cablearlo dejó de dar igual: `fetch_option_chain` **no lanza** con una cadena
    vacía —devuelve `rows=[]`, que el motor sabe manejar—, así que la llamada a
    barras se alcanza con cualquier ticker que el usuario escriba.

    Se comparte la función en vez de copiarla: dos saneados distintos para la
    misma entrada, en el mismo repo, es una trampa por sí sola.

    La travesía de rutas ya la cerraba su regex: borra las barras, así que
    `../..` se queda en `....` y no sale del directorio.
    """
    return data_dir() / "bars" / f"{_sanea_ticker(ticker)}.json"


def _a_bar(d: object) -> LvlBar | None:
    """`DailyBar` crudo → `LvlBar`. En TS esto es el `as BarsFile` del `JSON.parse`."""
    if not isinstance(d, dict):
        return None
    try:
        return LvlBar(time=str(d["time"]), high=float(d["high"]),
                      low=float(d["low"]), close=float(d["close"]))
    except (KeyError, TypeError, ValueError):
        return None


def load_bars(ticker: str) -> BarsFile | None:
    """`loadBars`:

        const raw = await fs.readFile(fileFor(ticker), "utf8");
        return JSON.parse(raw) as BarsFile;

    GUARDA (divergencia deliberada): **se valida lo que hay en disco**. Ese `as`
    es una afirmación para el compilador, no una comprobación, así que en el
    original lo que haya en el archivo pasa tal cual y `cachedDailyBars` lo usa
    sin mirar. De ahí salen sus dos bugs:

        {"ticker":"A","date":hoy}                → `cached.bars.length` lanza
        {"ticker":"B","date":hoy,"bars":"texto"} → devuelve el string "texto"

    El segundo es el feo: `"texto".length > 0` es cierto, así que la guarda pasa
    y el llamador recibe un string donde espera barras. Sin excepción: el
    análisis sigue, sobre basura y en silencio.

    Mientras el módulo no lo llamaba nadie los dos estaban replicados. Ahora que
    el panel de Proyecciones sí lo usa, un archivo a medio escribir —un proceso
    muerto, un disco lleno— tumbaría la petición entera o, peor, la dejaría
    calculando sobre texto. Un cache ilegible es un cache que no está: se
    devuelve `None`, se pide a la red y el archivo se reescribe solo.

    Ver `engine/scripts/upstream-tito-barsstore.patch`.

    Lo que sí se conserva del original: la lista de barras se reconstruye cuando
    de verdad es una lista. En TS los objetos del JSON ya sirven como `DailyBar`
    por su forma; en Python hacen falta `LvlBar` para que `levels`, `validation`
    y compañía puedan leer `b.time`.
    """
    try:
        path = _file_for(ticker)
    except ValueError:
        return None          # un ticker sin nombre de archivo tampoco tiene cache
    parsed = _read(path)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("bars"), list):
        return None
    return BarsFile(
        ticker=parsed.get("ticker"),
        date=parsed.get("date"),
        bars=[b for b in (_a_bar(x) for x in parsed["bars"]) if b is not None],
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
    # ausente lance (BUG 1) y que un `bars` de texto pase la guarda (BUG 2).
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

    `fetch` solo existe para poder probar esto sin red.
    """
    now = now or datetime.now(timezone.utc)
    corte = _ultima_sesion_cerrada(now)

    cache = load_bars(ticker)
    if cache is not None and cache.bars and cache.bars[-1].time >= corte:
        return cache.bars

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
        actual = load_bars(ticker)
        if actual is not None and actual.bars and len(bars) < len(actual.bars):
            return False
        save_bars(ticker, bars, now)
        return True
