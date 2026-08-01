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
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .levels import LvlBar
from .massive import MassiveError
from .occ import MARKET_TZ, market_date_str
from .stores import MAX_TICKER_LEN, _exclusive, _read, _sanea_ticker, _write, data_dir

__all__ = [
    "BarsFile",
    "MARKET_CLOSE_HOUR",
    "load_bars",
    "save_bars",
    "cached_daily_bars",
]

#: Cierre de la sesión regular, hora del Este. La barra del día solo es
#: definitiva a partir de aquí.
MARKET_CLOSE_HOUR = 16


@dataclass(frozen=True)
class BarsFile:
    """`BarsFile` de Víctor: las barras y el día de mercado en que se guardaron."""

    ticker: str
    date: str          # día de mercado (ET) en que se guardó
    bars: list[LvlBar]


def _file_for(ticker: str) -> Path:
    """Mismo saneado que el resto de stores, con sus mismas guardas."""
    return data_dir() / "bars" / f"{_sanea_ticker(ticker)}.json"


def _sesion_cerrada(now: datetime) -> bool:
    """¿La sesión del día de mercado de `now` ya cerró?

    DIVERGENCIA declarada, y la única de este módulo. El cache de Víctor vale
    para todo el día de mercado, sin mirar la hora. Eso le sirve porque su
    consumidor es el escáner de Wheel, donde una última barra algo vieja no
    cambia qué contrato vender.

    Aquí el consumidor es la gráfica del panel, que se auto-refresca para
    enseñar el movimiento del día. La barra de hoy es **parcial** mientras la
    sesión está abierta: guardarla a las 11:00 ET congelaría el precio hasta el
    cierre y el usuario vería una gráfica que no se mueve mientras el mercado sí.

    Así que el cache solo se da por bueno cuando la sesión que lo produjo ya
    terminó. Fuera de horario —que es cuando de verdad se repiten las
    consultas— ahorra igual.
    """
    et = now.astimezone(MARKET_TZ)
    return et.hour >= MARKET_CLOSE_HOUR


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
    return BarsFile(ticker=str(parsed.get("ticker", "")),
                    date=str(parsed.get("date", "")), bars=bars)


def save_bars(ticker: str, bars: Sequence[LvlBar], now: datetime | None = None) -> None:
    """`saveBars`: guarda las barras con el día de mercado en que se pidieron."""
    now = now or datetime.now(timezone.utc)
    path = _file_for(ticker)
    with _exclusive(path):
        _write(path, {
            "ticker": ticker.strip().upper(),
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

    Si la red falla devuelve `[]`, como el original — el llamador ya sabe
    distinguir "sin barras" y cortar con su motivo.

    `fetch` existe para poder probar esto sin red; por defecto es
    `massive.fetch_daily_bars`.
    """
    now = now or datetime.now(timezone.utc)
    hoy = market_date_str(now)

    cache = load_bars(ticker)
    if cache and cache.date == hoy and cache.bars and _sesion_cerrada(now):
        return cache.bars

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
            save_bars(ticker, bars, now)
        except OSError:
            pass  # sin disco el cache no funciona, pero la consulta sí
    return bars
