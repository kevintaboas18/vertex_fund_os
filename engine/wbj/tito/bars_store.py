"""Port de `barsStore.ts` — cache en disco de barras diarias, por día de mercado.

Las barras diarias solo cambian una vez al día, pero cada consulta del panel de
Proyecciones las vuelve a pedir. Sin cache son 365 días de historial bajando por
la red en cada refresco, para un dato que ya no se mueve.

En el original este store lo usa **solo** el escáner de Wheel (40 tickers por
pasada, fuera del alcance de este port). Aquí el consumidor es
`_tito_chain_and_bars`, que corre en cada petición de Proyecciones y con el
auto-refresco del panel encima — o sea que el cache rinde más aquí que allí.

Reutiliza a propósito los ayudantes endurecidos de `stores.py` (`_exclusive`,
`_read`, `_write`, el saneado del ticker). Escribir su propio manejo de archivos
habría reintroducido, uno por uno, los once hallazgos de la auditoría de
`store.ts`: la carrera de escritura, la travesía de rutas, el `NaN` que rompe el
JSON y el resto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from .levels import LvlBar
from .massive import MassiveError
from .occ import MARKET_TZ, market_date, market_date_str
from .stores import MAX_TICKER_LEN, _exclusive, _read, _sanea_ticker, _write, data_dir

__all__ = [
    "BarsFile",
    "MARKET_CLOSE_HOUR",
    "mercado_abierto",
    "load_bars",
    "save_bars",
    "cached_daily_bars",
]

#: Sesión regular del mercado de EE.UU., hora del Este.
MARKET_OPEN_MIN = 9 * 60 + 30    # 09:30
MARKET_CLOSE_MIN = 16 * 60       # 16:00
MARKET_CLOSE_HOUR = MARKET_CLOSE_MIN // 60   # se mantiene por compatibilidad


@dataclass(frozen=True)
class BarsFile:
    """`BarsFile` de Víctor: las barras y el día de mercado en que se guardaron."""

    ticker: str
    date: str          # día de mercado (ET) en que se guardó
    bars: list[LvlBar]
    #: Ventana, en días de CALENDARIO, con la que se pidieron. Víctor no la
    #: guarda porque su único llamador usa siempre el default. Sin ella, el
    #: primero que pidiera 30 días dejaba el cache corto y el siguiente que
    #: pidiera 365 recibía un histórico truncado **en silencio** — con el
    #: análisis corriendo sobre 21 barras en vez de 261.
    days: int = 0


def _file_for(ticker: str) -> Path:
    """Mismo saneado que el resto de stores, con sus mismas guardas."""
    return data_dir() / "bars" / f"{_sanea_ticker(ticker)}.json"


def mercado_abierto(now: datetime) -> bool:
    """¿Está la sesión regular en curso? Lunes a viernes, 09:30–16:00 ET.

    No conoce los festivos: en uno, esto dice "abierto" y el cache se refresca
    todo el día. Se desperdicia un puñado de llamadas ~9 días al año; nunca se
    sirve un dato viejo. Se intentó cubrirlos mirando la propia barra y salió
    peor — ver `_datos_congelados`.
    """
    et = now.astimezone(MARKET_TZ)
    if et.weekday() >= 5:            # sábado o domingo
        return False
    minutos = et.hour * 60 + et.minute
    return MARKET_OPEN_MIN <= minutos < MARKET_CLOSE_MIN


def _datos_congelados(now: datetime) -> bool:
    """¿Puede el mercado cambiar todavía lo que hay en este cache?

    DIVERGENCIA declarada, y la única de peso en este módulo. El cache de Víctor
    vale para todo el día de mercado, **sin mirar la hora**. Eso le sirve porque
    su consumidor es el escáner de Wheel, donde una última barra algo vieja no
    cambia qué contrato vender.

    Aquí el consumidor es la gráfica del panel, que se auto-refresca para
    enseñar el movimiento del día. La barra de hoy es **parcial** mientras la
    sesión está abierta: guardarla a las 11:00 ET congelaría el precio hasta el
    cierre y el usuario vería una gráfica quieta con el mercado moviéndose.

    La regla es una sola: **con la sesión en curso no se cachea, punto.** Fin de
    semana, pre-market y después del cierre sí.

    Dos versiones anteriores de esto estaban mal, y las dos las encontró la
    auditoría:

    - Mirar solo `hora >= 16` dejaba **el fin de semana entero sin cache**,
      justo cuando más se repiten las consultas y con cero posibilidad de que el
      dato cambie.
    - Añadir "…o si la última barra guardada es anterior a hoy" pretendía cubrir
      los festivos sin calendario, pero confunde **festivo** con **el dato aún
      no ha llegado**. Massive puede no haber publicado el agregado de hoy a las
      9:31; se cacheaba sin la barra del día y la sesión entera se servía de ahí
      — la gráfica sin el día en curso hasta el cierre.

    En un festivo se refresca todo el día y se desperdicia un puñado de llamadas.
    Sale más barato que congelar la gráfica en una sesión viva.
    """
    return not mercado_abierto(now)


def _recorta(bars: Sequence[LvlBar], days: int, now: datetime) -> list[LvlBar]:
    """Recorta a la ventana pedida por FECHA, no por número de barras.

    `days` son días de **calendario** —así lo entiende `fetch_daily_bars`, que
    hace `end - timedelta(days=days)`— y en 30 días de calendario caben unas 21
    barras, no 30. Una primera versión de esto hacía `bars[-days:]` y devolvía
    30 barras, o sea 41 días de calendario: más histórico del pedido.
    """
    if days <= 0:
        return list(bars)
    corte = (market_date(now) - timedelta(days=days)).isoformat()
    return [b for b in bars if b.time >= corte]


def _a_bar(d: object) -> LvlBar | None:
    if not isinstance(d, dict):
        return None
    try:
        return LvlBar(time=str(d["time"]), high=float(d["high"]),
                      low=float(d["low"]), close=float(d["close"]))
    except (KeyError, TypeError, ValueError):
        return None


def load_bars(ticker: str) -> BarsFile | None:
    """`loadBars`. `None` si no hay cache para este ticker.

    Las filas que no reconstruyan una barra se descartan, igual que en
    `load_trades`: una entrada corrupta no puede tumbar el cache entero.
    """
    try:
        path = _file_for(ticker)
    except ValueError:
        return None
    parsed = _read(path)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("bars"), list):
        return None
    bars = [b for b in (_a_bar(x) for x in parsed["bars"]) if b is not None]
    try:
        dias = int(parsed.get("days") or 0)
    except (TypeError, ValueError):
        dias = 0
    return BarsFile(ticker=str(parsed.get("ticker", "")),
                    date=str(parsed.get("date", "")), bars=bars, days=dias)


def save_bars(ticker: str, bars: Sequence[LvlBar], now: datetime | None = None,
              days: int = 0) -> None:
    """`saveBars`: guarda las barras con el día de mercado en que se pidieron.

    `days` deja constancia de la ventana pedida, para que un cache corto no se
    sirva a quien necesita uno largo."""
    now = now or datetime.now(timezone.utc)
    path = _file_for(ticker)
    with _exclusive(path):
        _write(path, {
            "ticker": ticker.strip().upper(),
            "date": market_date_str(now),
            "bars": [{"time": b.time, "high": b.high, "low": b.low, "close": b.close}
                     for b in bars],
            "days": int(days),
        })


def cached_daily_bars(
    ticker: str,
    days: int = 365,
    now: datetime | None = None,
    fetch=None,
) -> list[LvlBar]:
    """`cachedDailyBars`: barras diarias con cache de un día de mercado.

    Si la red falla devuelve `[]`, como el original — el llamador ya sabe
    distinguir "sin barras" y cortar con su motivo.

    `fetch` existe para poder probar esto sin red; por defecto es
    `massive.fetch_daily_bars`.
    """
    now = now or datetime.now(timezone.utc)
    hoy = market_date_str(now)

    cache = load_bars(ticker)
    # El cache solo sirve si CUBRE la ventana pedida. Al revés —servir uno corto
    # a quien pide largo— trunca el histórico en silencio, y el análisis correría
    # sobre 21 barras creyendo tener 261.
    if (cache and cache.date == hoy and cache.bars
            and cache.days >= days and _datos_congelados(now)):
        return _recorta(cache.bars, days, now)

    if fetch is None:
        from .massive import fetch_daily_bars as fetch  # import tardío: evita el ciclo

    try:
        bars = list(fetch(ticker, days=days))
    except (MassiveError, OSError, ValueError):
        # SOLO lo que puede fallar por la red o por el disco. Un `except
        # Exception` a secas se tragaba también los errores de programación —una
        # firma cambiada, un typo— y los hacía pasar por "Massive está caído",
        # que es el peor diagnóstico posible: manda a mirar la red cuando el
        # problema está en el código.
        #
        # DIVERGENCIA declarada: si hay cache, aunque sea de otro día, se
        # devuelve. El original devuelve `[]` y el motor corta con "sin barras".
        # Unas barras de ayer dan un análisis viejo pero honesto; ninguna barra
        # no da nada.
        return cache.bars if cache and cache.bars else []

    if bars:
        try:
            save_bars(ticker, bars, now, days=days)
        except OSError:
            pass  # sin disco el cache no funciona, pero la consulta sí
    return bars
