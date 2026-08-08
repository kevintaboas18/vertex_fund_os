"""Persistencia del motor — las series que se acumulan hacia adelante.

Port de `chainStore.ts`, `ivStore.ts`, `predictionStore.ts` y el guardado de
trades de Víctor.

**Por qué existe esto y no es opcional.** Tres piezas del motor no funcionan con
una sola foto del mercado; necesitan una serie que solo se construye guardando
una foto por día:

- **Sub-agente 5 (Contexto IV)** — el IV Rank real necesita 52 semanas de IV
  histórica y ninguna fuente la vende. Sin `IvStore` el rank se queda **para
  siempre** en el proxy de volatilidad realizada.
- **Sub-agente 6 (Confirmación de Precio)** — sin `TradesStore` no hay flows
  pasados que evaluar, así que la categoría sale `None` **aunque haya tape**.
- **Auto-calibración** — sin `PredictionStore` nunca hay 5 predicciones
  vencidas, y el lazo de control jamás se enciende.

Víctor lo documenta así: *"se acumula hacia adelante porque Massive no expone OI
histórico"*. La consecuencia práctica es que el agente **mejora con el uso**, y
que un despliegue sin disco persistente lo mantiene en su peor versión para
siempre.

Todo es JSON en disco, deduplicado **por día de mercado (ET)**: llamar dos veces
el mismo día no duplica ni pisa. `WBJ_TITO_DATA` cambia el directorio (en Render
apúntalo al disco montado, p.ej. `/var/data/tito`).
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from functools import cmp_to_key
from pathlib import Path
from typing import Any, Literal, Sequence

from .jsmath import (RangeError, UNDEFINED, js_abs, js_date_parse, js_gt, js_le, js_max, js_min,
                     js_number, js_orden, js_string)
from .occ import market_date_str

__all__ = [
    "data_dir",
    "HISTORY_DAYS",
    "IV_HISTORY_DAYS",
    "CHAIN_DAYS",
    "IV_DAYS",
    "JOURNAL_DAYS",
    "MAX_PER_TICKER",
    "save_chain_snapshot",
    "load_chain_history",
    "save_iv_snapshot",
    "load_iv_history",
    "StoredTrades",
    "SaveResult",
    "save_trades",
    "load_trades",
    "PredictionSnapshot",
    "save_prediction",
    "load_journal",
    "review_predictions",
    "calibration_from_review",
]

#: Ventanas que guarda cada serie, en días. Son las de Víctor.
#: Los trades NO llevan ventana: `store.ts` recorta por cantidad (MAX_PER_TICKER).
# Los nombres son los SUYOS (`HISTORY_DAYS` de chainStore.ts, `IV_HISTORY_DAYS`
# de ivStore.ts). Los alias cortos se quedan porque este módulo fusiona cuatro
# stores suyos y "HISTORY_DAYS" a secas no dice de cuál historia habla.
HISTORY_DAYS = 45     # chainStore: el documento pide 45 días de cadena
IV_HISTORY_DAYS = 365  # ivStore: ventana de 52 semanas para el rank
CHAIN_DAYS = HISTORY_DAYS
IV_DAYS = IV_HISTORY_DAYS
JOURNAL_DAYS = 120   # predictionStore


def data_dir() -> Path:
    """Directorio de datos. `WBJ_TITO_DATA` lo redirige (disco de Render, etc.)."""
    d = Path(os.environ.get("WBJ_TITO_DATA", "") or (Path.cwd() / "data" / "tito"))
    return d


def _path(kind: str, ticker: str) -> Path:
    """No crea el directorio: leer no debe dejar carpetas escritas. `_write` lo
    crea cuando de verdad hay algo que guardar."""
    return data_dir() / kind / f"{ticker.upper()}.json"


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


#: `fcntl` es solo POSIX. En Windows —donde se corre el preflight local— no
#: existe, y un import directo tumbaría el módulo entero. Sin él queda el
#: cerrojo de hilos, que cubre el caso de un solo proceso (que es el de Windows).
try:
    import fcntl
except ImportError:   # pragma: no cover — Windows
    fcntl = None      # type: ignore[assignment]

#: Un cerrojo por archivo dentro del proceso. Se acompaña de `flock` para los
#: workers hermanos; sin el de hilos, dos peticiones del mismo worker ya se
#: pisan entre ellas.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

#: Rutas que este hilo ya tiene tomadas. Sin esto, entrar dos veces al mismo
#: cerrojo desde el mismo hilo cuelga el proceso ENTERO y para siempre: el
#: `threading.Lock` no es reentrante, y `flock` tampoco lo es entre dos
#: descriptores del mismo proceso, así que ni cambiándolo por un `RLock` se
#: arregla. Hoy ningún camino anida, pero es una trampa cara —un worker colgado
#: no da error ni timeout, simplemente deja de responder— y sale barata de
#: cerrar aquí.
_HELD = threading.local()


@contextmanager
def _exclusive(path: Path):
    """Serializa el ciclo leer-fusionar-escribir sobre `path`.

    DIVERGENCIA declarada: Víctor no tiene cerrojo. Su `saveTrades` hace
    `await loadTrades()` y luego `await fs.writeFile()`, así que dos peticiones
    al mismo ticker se intercalan en los `await` y la segunda escribe encima de
    lo que leyó la primera. Medido aquí: 8 escrituras concurrentes de 50 trades
    dejaban **100 de 400** — se perdía el 75% de la memoria que este archivo
    existe para acumular. Y es un caso normal, no raro: el panel se auto-refresca
    mientras el usuario consulta, y Render corre varios workers.

    El cerrojo va sobre un `.lock` aparte y no sobre el JSON, porque la escritura
    atómica reemplaza el inodo del JSON en cada guardado y el `flock` se quedaría
    colgado del inodo viejo.
    """
    key = str(path)
    tomados = getattr(_HELD, "paths", None)
    if tomados is None:
        tomados = _HELD.paths = set()
    if key in tomados:
        yield          # este hilo ya lo tiene: la exclusividad la da el de fuera
        return

    with _LOCKS_GUARD:
        lk = _LOCKS.setdefault(key, threading.Lock())
    with lk:
        lock_path = path.with_suffix(path.suffix + ".lock")
        fh = None
        if fcntl is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                fh = open(lock_path, "a+")
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except OSError:
                if fh is not None:
                    fh.close()
                fh = None   # sin flock (Windows, FS raro, disco RO) queda el de hilos
        tomados.add(key)
        try:
            yield
        finally:
            tomados.discard(key)
            if fh is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                finally:
                    fh.close()


def _finite(obj: Any) -> Any:
    """`NaN`/`Infinity` → `null`, que es lo que hace `JSON.stringify`.

    Sin esto el archivo deja de ser JSON **válido**: Python lo relee porque su
    `json.loads` acepta esas constantes por defecto, pero cualquier otra cosa
    que lo abra —`jq`, un backup, un script de migración, otro lenguaje— lo
    rechaza. Y una IV que llega rota no debe convertir el historial entero en
    ilegible para el resto del mundo.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def _write(path: Path, payload: Any) -> None:
    """Escritura atómica: un proceso muerto a media escritura no debe dejar un
    JSON truncado que luego se lea como 'sin historial'."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            try:
                # allow_nan=False para ENTERARSE de que hay un no-finito; el
                # camino normal no paga nada por el intento.
                json.dump(payload, fh, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
            except ValueError:
                fh.seek(0)
                fh.truncate()
                json.dump(_finite(payload), fh, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prune(rows: list[dict], days: int, key: str = "date") -> list[dict]:
    """Recorta a la ventana y ordena. Sin esto el fichero crece sin fin.

    Las filas que no son objetos se IGNORAN en vez de reventar. No es
    defensa preventiva: con un archivo cuyo contenido no es una lista de
    diccionarios —el formato de SU app, o un archivo a medio escribir— esto
    lanzaba `AttributeError: 'str' object has no attribute 'get'`, mientras
    que su `loadIvHistory` devuelve `null` y sigue. El motor degradaba a
    «sin historial», que es correcto, pero por el camino equivocado: una
    excepción atrapada arriba en vez de un archivo descartado aquí.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    limpias = [r for r in rows if isinstance(r, dict)]
    return sorted((r for r in limpias if str(r.get(key, "")) >= cutoff),
                  key=lambda r: str(r.get(key, "")))


def _upsert(rows: list[dict], row: dict, key: str = "date") -> list[dict]:
    """Dedupe por día: el último del día gana, no se acumulan duplicados."""
    out = [r for r in rows if r.get(key) != row.get(key)]
    out.append(row)
    return out


# ─────────────────────────────── cadena (sub-agente 4) ──────────────────────


def save_chain_snapshot(ticker: str, rows: Sequence[Any], now: datetime) -> int:
    """Guarda una foto diaria de la cadena. Devuelve los días acumulados.

    Solo se guarda el agregado por strike, no la cadena entera: lo que el
    sub-agente 4 mira es cómo se mueve el open interest, y guardar miles de
    contratos por día haría el fichero inmanejable sin aportar nada.
    """
    by_strike: dict[float, dict] = {}
    for r in rows:
        s = by_strike.setdefault(r.strike, {"strike": r.strike, "call_oi": 0, "put_oi": 0, "volume": 0})
        if r.contract_type == "call":
            s["call_oi"] += r.open_interest
        else:
            s["put_oi"] += r.open_interest
        s["volume"] += r.volume

    path = _path("chain", ticker)
    with _exclusive(path):   # mismo motivo que en save_trades: leer-fusionar-escribir
        hist = _read(path) or []
        hist = _upsert(hist, {"date": market_date_str(now), "strikes": list(by_strike.values())})
        hist = _prune(hist, CHAIN_DAYS)
        _write(path, hist)
    return len(hist)


def load_chain_history(ticker: str) -> list[dict]:
    return _prune(_read(_path("chain", ticker)) or [], CHAIN_DAYS)


# ─────────────────────────────── IV (sub-agente 5) ──────────────────────────


def save_iv_snapshot(
    ticker: str,
    avg_iv: float,
    now: datetime,
    min_iv: float | None = None,
    max_iv: float | None = None,
    contracts: int | None = None,
    front_skew: float | None = None,
) -> int:
    """Guarda la foto de IV del día (en %). Alimenta el IV Rank real.

    A partir de `MIN_IV_HISTORY_DAYS` (60) muestras, `iv_context_score` deja de
    usar el proxy de volatilidad realizada y pasa al rank de verdad, solo.

    `avg_iv` tiene que ser la IV **ponderada por premium** (`iv.current` de
    `iv_context_score`), que es lo que guarda su `saveIvSnapshot`. Un promedio
    simple lo dominan los cientos de tickets pequeños de 0DTE, y ese número
    queda escrito para siempre: el IV Rank de dentro de seis meses se calcula
    sobre lo que se guarde hoy.

    Los cuatro campos opcionales son los que su snapshot también persiste
    (`minIv`, `maxIv`, `contracts`, `frontSkew`). El rank NO los usa —solo lee
    `avgIv`—, pero sin ellos no se puede auditar hacia atrás por qué la IV de
    un día concreto salió como salió.
    """
    if not (avg_iv and avg_iv > 0):
        return len(load_iv_history(ticker))
    path = _path("iv", ticker)
    fila = {"date": market_date_str(now), "avg_iv": round(float(avg_iv), 4)}
    for k, v in (("min_iv", min_iv), ("max_iv", max_iv),
                 ("contracts", contracts), ("front_skew", front_skew)):
        if v is not None:
            fila[k] = round(float(v), 4) if k != "contracts" else int(v)
    with _exclusive(path):
        hist = _read(path) or []
        hist = _upsert(hist, fila)
        hist = _prune(hist, IV_DAYS)
        _write(path, hist)
    return len(hist)


def load_iv_history(ticker: str) -> list[dict]:
    """Formato que consume `iv_context_score`: ``[{date, avg_iv}, …]``."""
    return _prune(_read(_path("iv", ticker)) or [], IV_DAYS)


# ─────────────────────────── trades (sub-agente 6) ──────────────────────────
#
# Port de `store.ts`. Es el único store de Víctor con forma propia: los otros
# tres son series por día (una fila diaria, dedupe por fecha) y este es un
# registro por TRADE (dedupe por id, tope por cantidad). Las diferencias no son
# de estilo, cada una responde a algo:
#
# - **Guarda el análisis completo, no 8 campos.** Víctor persiste el `FlowRow`
#   entero —scores, flags, greeks, condition_code—. El comentario de su
#   `saveTrades` lo dice: *"los trades vienen ya clasificados/puntuados, así que
#   se guarda el análisis completo"*. Un archivo recortado obliga a reclasificar
#   para responder cualquier pregunta nueva sobre el pasado.
# - **El análisis más reciente gana.** Al re-ver un id ya guardado lo
#   sobrescribe en vez de saltárselo, porque hay campos que dependen de HOY
#   (`expiry_status` pasa de vigente → expirado) y se recalculan cada corrida.
# - **Tope por cantidad, no por días.** 5000 trades por ticker. Un ticker con
#   poco flujo conserva meses de historia; uno muy líquido no revienta el disco.
#   Recortar por ventana temporal le quitaría al sub-agente 6 justo la historia
#   que necesita en los tickers tranquilos.
# - **Orden descendente** (lo más nuevo primero), que es también lo que hace que
#   el recorte a 5000 tire lo más viejo.
#
# Su lógica, LITERAL — incluidos sus tres agujeros con datos malformados:
# ticker que se queda en nada, id ausente que funde el historial, fila corrupta
# que tumba el guardado. 47/47 casos del diferencial, cero divergencias
# declaradas. Los tres están propuestos para el upstream en
# `engine/scripts/upstream-tito-store.patch` y fijados —con su coste medido—
# por `TestLasTresQueSeQuedanComoEl`.
#
# Las guardas no desaparecieron: se movieron al BORDE de Vertex (`borde.py`),
# que es donde su pipeline de TypeScript ya las tiene de forma implícita. Aquí
# pesan más que en su repo —esto es la memoria ACUMULADA del sub-agente 6 y lo
# que se pierde no se recupera—, así que el borde no es opcional; lo que no
# puede es entrar dentro de su archivo.
#
# Aparte van el cerrojo y la escritura atómica, que no cambian nada de lo
# observable con una petición a la vez pero evitan perder el 75% de las
# escrituras concurrentes.


#: Tope por ticker para que el archivo no crezca sin control (`MAX_PER_TICKER`).
MAX_PER_TICKER = 5000


@dataclass(frozen=True)
class StoredTrades:
    """`StoredTrades` de Víctor: el archivo no es una lista pelada.

    El envoltorio guarda de quién es el historial y cuándo se tocó por última
    vez, que es lo que permite responder "¿esta memoria está viva?" sin abrir
    los 5000 trades.

    Los tres campos van sin tipar de verdad porque `loadTrades` hace
    `JSON.parse(raw)` y solo comprueba `Array.isArray(parsed.trades)`: el resto
    del archivo entra tal cual. Un `ticker` que en disco sea un número llega como
    número, y un `updatedAt` ausente llega como `None` (su `undefined`).
    """

    ticker: Any
    updated_at: Any
    trades: list


@dataclass(frozen=True)
class SaveResult:
    """`SaveResult` de Víctor — lo que su UI muestra tras guardar."""

    total: int          # cuántas quedan guardadas
    added: int          # cuántas eran nuevas en esta corrida
    first_seen: str | None  # fecha del trade más antiguo guardado


def _sanea_ticker(ticker: str) -> str:
    """`fileFor`, literal:

        const safe = ticker.trim().toUpperCase().replace(/[^A-Z0-9._-]/g, "");

    Sin guardas. La travesía de rutas ya la cierra su propio regex, que borra
    las barras: `"../../etc/x"` se queda en `"....ETCX"` y no sale del
    directorio. Lo que su regex NO cierra es el cubo compartido: `"!!!"`,
    `"@@@"` y `""` sanean todos a la cadena vacía y acaban en el mismo `.json`.

    Esa guarda estaba aquí y se movió al BORDE de Vertex (`borde.py`), que es
    donde su pipeline de TypeScript ya la tiene: sus rutas reciben el ticker de
    `searchParams`, lo normalizan y rechazan el vacío antes de que llegue a
    ningún store. Así el port es su archivo y el dato sigue sin mezclarse.
    """
    return re.sub(r"[^A-Z0-9._-]", "", ticker.strip().upper())


def _file_for(ticker: str) -> Path:
    """`fileFor`. No crea el directorio: preguntar "¿hay historial?" no puede
    dejar carpetas escritas. El que escribe ya las crea."""
    return data_dir() / "trades" / f"{_sanea_ticker(ticker)}.json"


def _prop(obj: Any, name: str) -> Any:
    """`obj.name` con las reglas de JS, que es lo que corre en su `saveTrades`.

    - Sobre `null`/`undefined` **lanza** (`Cannot read properties of null`).
    - Sobre cualquier otra cosa que no sea objeto —un string, un número— da
      `undefined` sin quejarse. Por eso una fila `"basura"` no revienta su
      bucle y una fila `null` sí.
    """
    if obj is None:
        raise TypeError(f"Cannot read properties of null (reading '{name}')")
    if isinstance(obj, dict):
        return obj.get(name)
    return None


#: `Date.parse` de JS. Vivía aquí; subió a `jsmath` al descubrir que
#: `levels.recency_factor` también cuenta el tiempo con la aritmética de JS. El
#: alias conserva el nombre privado que usan los tests y el diferencial.
_date_parse = js_date_parse


def _cmp_reciente_primero(a: Any, b: Any) -> int:
    """`(a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp)`.

    El detalle que importa es el `NaN`: si un timestamp no se parsea, la resta
    da `NaN`, y ECMA-262 manda tratar un comparador que devuelve `NaN` como
    **0**, o sea "iguales". Con un `sort` estable eso deja las filas ilegibles
    donde estaban en vez de mandarlas al final. Aquí se replica exactamente:
    `cmp_to_key` sobre este comparador y el Timsort de Python, que también es
    estable.
    """
    d = _date_parse(_prop(b, "timestamp")) - _date_parse(_prop(a, "timestamp"))
    if d != d:          # NaN → SortCompare devuelve +0
        return 0
    return -1 if d < 0 else (1 if d > 0 else 0)


def _dedupe_key(row: Any) -> Any:
    """La clave del `Map` de Víctor: `t.id`, tal cual.

    Sin guarda para el id AUSENTE. `flow._base_row` hace
    `int(_num(raw.get("id")))`, así que un tape sin ese campo devuelve 0 para
    TODOS los trades y el `Map` conserva uno solo — sin un error, y `added`
    tampoco avisa porque a partir del segundo la clave "ya existe". Es su
    comportamiento; el arreglo propuesto está en
    `engine/scripts/upstream-tito-store.patch`.

    Es la única de las tres que NO se puede mover al borde: vive dentro de su
    `saveTrades`. `borde.trades_con_id` la deja al menos visible — cuenta
    cuántos trades llegan sin id para que el panel lo pueda decir.
    """
    return _prop(row, "id")


def load_trades(ticker: str) -> StoredTrades | None:
    """`loadTrades`, literal:

        const parsed = JSON.parse(raw);
        return Array.isArray(parsed.trades) ? parsed : null;

    Él solo comprueba que `trades` sea un array; el contenido no lo mira.

    `None` y "historial vacío" son cosas distintas y el llamador las distingue:
    la primera es "nunca se ha guardado nada", la segunda "se guardó y no quedó
    nada". Por eso no colapsa a lista vacía.

    El contenido del array NO se mira, como él. Una fila corrupta (un `null`, un
    string) se devuelve tal cual, y su `saveTrades` la vuelve a escribir o se
    cae con ella. El filtro que había aquí se movió al borde de Vertex
    (`borde.trades_utiles`), que es donde su pipeline lo hace de forma
    implícita: en TS `t.assetPrice` sobre un string es `undefined` y la fila se
    cae sola por el filtro de `/api/validation`.
    """
    parsed = _read(_file_for(ticker))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("trades"), list):
        # Su `catch` cubre los tres casos que aquí llegan como no-dict: archivo
        # que no existe, JSON roto y `JSON.parse("null")` (que en TS revienta al
        # leerle `.trades`). Un array pelado o un escalar no tienen `.trades`,
        # así que caen por el `Array.isArray` — mismo `null` de salida.
        return None
    return StoredTrades(
        ticker=parsed.get("ticker"),
        updated_at=parsed.get("updatedAt"),
        trades=parsed["trades"],
    )


def save_trades(ticker: str, rows: Sequence[Any]) -> SaveResult:
    """`saveTrades`: fusiona con lo guardado (dedupe por id) y persiste.

    Es lo que hace posible el sub-agente 6: un flow de hoy no tiene recorrido
    que juzgar, así que la Confirmación de Precio solo existe sobre lo que este
    archivo fue acumulando.

    Traducción literal salvo el fichero: leer-fusionar-escribir va bajo cerrojo
    y la escritura es atómica. Sin el cerrojo, dos peticiones al mismo ticker
    leen el mismo archivo y la segunda escribe encima de lo que acumuló la
    primera — medido aquí: 8 escrituras concurrentes de 50 trades dejaban 100 de
    400. No cambia nada de lo observable con una sola petición a la vez, que es
    el caso que mide el diferencial contra su archivo.
    """
    clean = ticker.strip().upper()
    path = _file_for(clean)

    with _exclusive(path):
        existing = load_trades(clean)
        by_id: dict[Any, Any] = {}

        # `for (const t of existing?.trades ?? []) byId.set(t.id, t);` — sin
        # filtrar: una fila `null` en disco lanza aquí, igual que en el suyo.
        for t in (existing.trades if existing else []):
            by_id[_dedupe_key(t)] = t
        added = 0
        for r in rows:
            row = r if isinstance(r, dict) else asdict(r)
            key = _dedupe_key(row)
            if key not in by_id:
                added += 1
            by_id[key] = row   # el análisis más reciente gana

        merged = sorted(by_id.values(), key=cmp_to_key(_cmp_reciente_primero))[:MAX_PER_TICKER]

        _write(path, {
            "ticker": clean,
            # `new Date().toISOString()` — con milisegundos, que es lo que
            # produce el original.
            "updatedAt": datetime.now(timezone.utc)
                                 .isoformat(timespec="milliseconds")
                                 .replace("+00:00", "Z"),
            "trades": merged,
        })

    # `merged[merged.length - 1]?.timestamp ?? null`: el `?.` no lanza con una
    # fila corrupta y el `??` solo convierte el ausente, no la cadena vacía.
    ultimo = merged[-1] if merged else None
    oldest = ultimo.get("timestamp") if isinstance(ultimo, dict) else None
    return SaveResult(total=len(merged), added=added, first_seen=oldest)


# ──────────────────── predicciones + calibración (memoria) ──────────────────


@dataclass(frozen=True)
class PredictionSnapshot:
    date: str  # día de mercado (ET)
    horizon_days: int
    spot: float
    bear: float
    base: float
    bull: float
    direction: Literal["up", "down", "flat"]
    #: `confidence` y `savedAt` de su `PredictionSnapshot`. `reviewPredictions`
    #: no los lee —el sesgo sale de los targets—, pero su diario los guarda y
    #: sin ellos no se puede auditar hacia atrás con qué confianza se dijo lo
    #: que se dijo, que es justo lo que hace falta al revisar un fallo.
    confidence: int = 0
    saved_at: str | None = None


def save_prediction(ticker: str, snap: PredictionSnapshot) -> int:
    """Guarda la foto del día. Dedupe por **(fecha, horizonte)**.

    DIVERGENCIA declarada respecto a su `savePrediction`, que deduplica **solo
    por fecha** (`byDate.set(s.date, s)`).

    Su UI muestra un horizonte a la vez y hace un POST por el que esté
    seleccionado, así que su diario tiene una fila por día y la clave le basta.
    Vertex sirve los tres horizontes en la MISMA respuesta y guarda los tres:
    con su clave, dos de cada tres se perderían en silencio y la calibración se
    quedaría con un tercio de las muestras.

    Su propio `reviewPredictions` lee `horizonDays` de cada fila para decidir si
    ya venció, así que un diario con tres horizontes por día lo procesa sin
    tocar nada — la clave es lo único que cambia.
    """
    path = _path("predictions", ticker)
    with _exclusive(path):
        # Se llama una vez POR HORIZONTE. Sin cerrojo, dos peticiones a la vez
        # se pisan el diario y la calibración se queda con huecos.
        rows = _read(path) or []
        rows = [
            r for r in rows
            if not (r.get("date") == snap.date and r.get("horizon_days") == snap.horizon_days)
        ]
        rows.append(asdict(snap))
        rows = _prune(rows, JOURNAL_DAYS)
        _write(path, rows)
    return len(rows)


def load_journal(ticker: str) -> list[dict]:
    return _prune(_read(_path("predictions", ticker)) or [], JOURNAL_DAYS)


def _touched(target: float, spot: float, high: float, low: float) -> bool:
    return high >= target if target >= spot else low <= target


def _add_calendar_days(fecha, dias) -> str:
    """`addCalendarDays` de su archivo: `new Date(`${d}T00:00:00Z`)` + días.

    Es aritmética de **calendario en UTC**, no de sesiones: `setUTCDate` cuenta
    días naturales. Con una fecha ilegible su `Date` sale `Invalid Date` y
    `toISOString()` LANZA un `RangeError` — que se propaga, así que ese diario
    entero no se revisa. El port lo capturaba y saltaba SOLO esa foto, o sea
    calibraba con una muestra que él nunca llega a tener.
    """
    ms = js_date_parse(f"{js_string(fecha)}T00:00:00Z")
    d = js_number(dias)
    if ms != ms or d != d or math.isinf(d):
        raise RangeError("Invalid time value")
    try:
        return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                + timedelta(days=int(d))).date().isoformat()
    except (OverflowError, OSError, ValueError):
        raise RangeError("Invalid time value") from None


def _touched(target, spot, high, low) -> bool:
    """`target >= spot ? high >= target : low <= target`, con la coacción de JS."""
    t, sp = js_number(target), js_number(spot)
    return js_number(high) >= t if t >= sp else js_number(low) <= t


def review_predictions(
    snapshots: Sequence[dict],
    bars: Sequence[Any],
    now: datetime,
) -> dict:
    """Compara cada predicción contra lo que hizo el precio. PURA.

    Devuelve el **sesgo** (error medio *firmado* del target base) que alimenta
    la auto-calibración: si el agente apunta sistemáticamente alto, el sesgo
    sale positivo y `calibration_shift_pct` baja el próximo target. Por eso esta
    función decide hacia dónde se corrige el motor, y por eso tiene diferencial
    propio (`engine/scripts/diff_calib.sh`).

    Solo cuentan las **vencidas**: juzgar una predicción a mitad de su horizonte
    mediría ruido, no acierto.
    """
    today = market_date_str(now)
    evals: list[dict] = []

    for s in snapshots:
        # `addCalendarDays` puede lanzar, y en su archivo eso se lleva la
        # revisión ENTERA (no hay try dentro del bucle). Literal.
        end = _add_calendar_days(_prop(s, "date"), _prop(s, "horizon_days"))
        matured = today >= end
        fecha = _prop(s, "date")
        # `b.time > s.date && b.time <= end`: comparación de TEXTO cuando los dos
        # lo son, con la coacción de JS si alguno no lo es (`jsmath.js_le`).
        window = [b for b in bars
                  if not js_le(b.time, fecha) and js_le(b.time, end)]
        base = {
            "date": fecha, "horizon_days": _prop(s, "horizon_days"),
            "spot": _prop(s, "spot"), "bear": _prop(s, "bear"),
            "base": _prop(s, "base"), "bull": _prop(s, "bull"),
            "direction": _prop(s, "direction"),
        }
        if not window:
            evals.append({**base, "sessions": 0, "matured": matured,
                          "actual_close": None, "actual_high": None,
                          "actual_low": None, "base_error_pct": None,
                          "base_abs_error_pct": None, "base_touched": False,
                          "bull_touched": False, "bear_touched": False,
                          "direction_hit": None, "best": None})
            continue

        actual_close = window[-1].close
        actual_high = js_max(*[b.high for b in window])
        actual_low = js_min(*[b.low for b in window])
        spot = _prop(s, "spot")
        base_error_pct = (
            (js_number(actual_close) - js_number(_prop(s, "base"))) / js_number(spot) * 100
            if js_gt(spot) else None)

        # `.sort((a,b) => Math.abs(a[1]-close) - Math.abs(b[1]-close))[0][0]`:
        # orden ESTABLE, así que con distancias iguales gana el primero (bear).
        objetivos = [("bear", _prop(s, "bear")), ("base", _prop(s, "base")),
                     ("bull", _prop(s, "bull"))]
        best = sorted(objetivos, key=js_orden(
            lambda a, b: (js_abs(js_number(a[1]) - js_number(actual_close))
                          - js_abs(js_number(b[1]) - js_number(actual_close)))))[0][0]

        moved = js_number(actual_close) - js_number(spot)
        flat_band = js_number(spot) * 0.01
        direccion = _prop(s, "direction")
        if not matured:
            direction_hit = None
        elif direccion == "up":
            direction_hit = moved > 0
        elif direccion == "down":
            direction_hit = moved < 0
        else:
            direction_hit = js_abs(moved) <= flat_band

        evals.append({
            **base, "sessions": len(window), "matured": matured,
            "actual_close": actual_close, "actual_high": actual_high,
            "actual_low": actual_low,
            "base_error_pct": base_error_pct,
            "base_abs_error_pct": (None if base_error_pct is None
                                   else js_abs(base_error_pct)),
            "base_touched": _touched(_prop(s, "base"), spot, actual_high, actual_low),
            "bull_touched": _touched(_prop(s, "bull"), spot, actual_high, actual_low),
            "bear_touched": _touched(_prop(s, "bear"), spot, actual_high, actual_low),
            "direction_hit": direction_hit, "best": best,
        })

    # `.sort((a, b) => b.date.localeCompare(a.date))` — texto, descendente.
    evals.sort(key=lambda e: js_string(e["date"]), reverse=True)
    mat = [e for e in evals if e["matured"] and e["actual_close"] is not None]

    def mean(xs: list) -> float | None:
        return (sum(xs) / len(xs)) if xs else None

    errs = [e["base_error_pct"] for e in mat if e["base_error_pct"] is not None]
    abs_errs = [e["base_abs_error_pct"] for e in mat
                if e["base_abs_error_pct"] is not None]
    best_counts = {"bear": 0, "base": 0, "bull": 0}
    for e in mat:
        if e["best"]:
            best_counts[e["best"]] += 1

    return {
        "evals": evals,
        "matured_count": len(mat),
        "mean_abs_error_pct": mean(abs_errs),
        "bias_pct": mean(errs),
        "base_touch_rate": (sum(1 for e in mat if e["base_touched"]) / len(mat) * 100)
                            if mat else None,
        "direction_hit_rate": (sum(1 for e in mat if e["direction_hit"]) / len(mat) * 100)
                               if mat else None,
        "best_counts": best_counts,
    }


def calibration_from_review(review: dict) -> dict:
    """Empaqueta el review en lo que espera `predict_pro(calibration=…)`."""
    return {"bias_pct": review.get("bias_pct"), "samples": review.get("matured_count", 0)}
