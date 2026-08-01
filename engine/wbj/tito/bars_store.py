"""Port de `barsStore.ts` — cache en disco de barras diarias, por día de mercado.

Traducción literal del original. Su cabecera dice, palabra por palabra:

    Las barras diarias solo cambian una vez al día, pero el escaneo de Wheel
    las pide para 40 tickers en cada pasada. Sin cache serían 40 llamadas
    repetidas por escaneo. Solo servidor.

    `fetchDailyBars` sigue sin cache para el resto de rutas: este store es
    nuevo y en v1 solo lo usa Wheel.

Esa segunda frase es la que manda: **el store existe, pero no se enchufa a las
demás rutas.** Aquí se respeta igual — `_tito_chain_and_bars` sigue llamando a
`fetch_daily_bars` directo, como hacen las rutas de Víctor que no son Wheel.

Lo único que no es traducción literal es el manejo de fichero: se reutilizan
`_exclusive`, `_read` y `_write` de `stores.py` en vez de un `fs.writeFile`
suelto. No cambia el comportamiento con datos correctos; evita que una escritura
a medias o dos peticiones simultáneas dejen el archivo roto — los mismos
problemas que costaron once hallazgos en la auditoría de `store.ts`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .levels import LvlBar
from .occ import market_date_str
from .stores import _exclusive, _read, _write, data_dir

__all__ = [
    "BarsFile",
    "load_bars",
    "save_bars",
    "cached_daily_bars",
]


@dataclass(frozen=True)
class BarsFile:
    """`BarsFile`: el ticker, el día de mercado (ET) en que se guardó y las barras."""

    ticker: str
    date: str
    bars: list[LvlBar]


def _file_for(ticker: str) -> Path:
    """`fileFor`, literal:

        const safe = ticker.trim().toUpperCase().replace(/[^A-Z0-9._-]/g, "");

    No usa el `_sanea_ticker` de `stores.py` a propósito: aquel rechaza el
    ticker que se queda en nada (`"!!!"`, `""`) y el demasiado largo, y esas dos
    guardas no están aquí. En `store.ts` valían la pena —un `.json` compartido
    contamina la MEMORIA acumulada del sub-agente 6—; en un cache de barras el
    daño se limita a que dos tickers basura compartan un archivo que se
    reescribe cada día.

    La travesía de rutas sigue cerrada sin guarda extra: su propio regex borra
    las barras, así que `../..` se queda en `....` y no sale del directorio.
    """
    safe = re.sub(r"[^A-Z0-9._-]", "", ticker.strip().upper())
    return data_dir() / "bars" / f"{safe}.json"


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
    """`loadBars`: el cache guardado, o `None` si no hay o no se puede leer.

    DIVERGENCIA declarada, y la única que queda: aquí se **valida** lo leído.
    Víctor hace `JSON.parse(raw) as BarsFile` a secas, y ese `as` es una promesa
    del compilador que el disco no tiene por qué cumplir. Medido contra su
    archivo, con un cache del día corrupto:

        sin campo `bars`      → `cached.bars.length` lanza TypeError
                                y **tumba la petición entera**
        `bars` como texto     → `"texto".length > 0` es cierto, así que
                                **devuelve el string como si fueran barras**
        `bars` como objeto    → devuelve el objeto igual

    Basta una escritura interrumpida o un disco lleno para dejar ese archivo, y
    entonces el escaneo revienta o corre sobre basura sin avisar. El parche
    propuesto para el original está en
    `engine/scripts/upstream-tito-barsstore.patch`.
    """
    parsed = _read(_file_for(ticker))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("bars"), list):
        return None
    return BarsFile(
        ticker=str(parsed.get("ticker", "")),
        date=str(parsed.get("date", "")),
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
    if cached and cached.date == today and cached.bars:
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
