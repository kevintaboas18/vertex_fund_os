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
from typing import Any, Sequence

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
    """`loadBars`, literal:

        const raw = await fs.readFile(fileFor(ticker), "utf8");
        return JSON.parse(raw) as BarsFile;

    **No valida nada**, igual que el original: ese `as` es una afirmación para
    el compilador, no una comprobación, así que lo que haya en el disco pasa tal
    cual. Un `bars` que sea texto llega como texto y uno ausente llega como
    `None`.

    Eso deja vivos los dos bugs de su archivo —ver
    `engine/scripts/upstream-tito-barsstore.patch`— y es a propósito: se replican
    para que el port sea idéntico. Los tests `TestBugsDeVictorReplicados` los
    fijan con su comportamiento exacto, incluido el `TypeError`.

    Lo único que sí se reconstruye es la lista de barras cuando de verdad es una
    lista: en TS los objetos del JSON ya sirven como `DailyBar` por su forma, y
    en Python hacen falta `LvlBar` para que `levels`, `validation` y compañía
    puedan leer `b.time`. Con cualquier otra cosa se pasa el valor crudo.
    """
    parsed = _read(_file_for(ticker))
    if not isinstance(parsed, dict):
        # `JSON.parse` de un array o de un escalar: en TS el `as` lo deja pasar
        # y los tres campos salen `undefined` — no cadena vacía. `None` solo
        # cuando de verdad no hay archivo o no se pudo leer, que es el `catch`.
        return None if parsed is None else BarsFile(ticker=None, date=None, bars=None)
    crudo = parsed.get("bars")
    bars: Any = ([b for b in (_a_bar(x) for x in crudo) if b is not None]
                 if isinstance(crudo, list) else crudo)
    return BarsFile(ticker=parsed.get("ticker"), date=parsed.get("date"), bars=bars)


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
