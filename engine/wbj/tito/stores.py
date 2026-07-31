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
from pathlib import Path
from typing import Any, Literal, Sequence

from .occ import market_date_str

__all__ = [
    "data_dir",
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
CHAIN_DAYS = 45      # chainStore: el documento pide 45 días de cadena
IV_DAYS = 365        # ivStore: ventana de 52 semanas para el rank
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
    """Recorta a la ventana y ordena. Sin esto el fichero crece sin fin."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return sorted((r for r in rows if str(r.get(key, "")) >= cutoff), key=lambda r: r[key])


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


def save_iv_snapshot(ticker: str, avg_iv: float, now: datetime) -> int:
    """Guarda la IV media del día (en %). Alimenta el IV Rank real.

    A partir de `MIN_IV_HISTORY_DAYS` (60) muestras, `iv_context_score` deja de
    usar el proxy de volatilidad realizada y pasa al rank de verdad, solo.
    """
    if not (avg_iv and avg_iv > 0):
        return len(load_iv_history(ticker))
    path = _path("iv", ticker)
    with _exclusive(path):
        hist = _read(path) or []
        hist = _upsert(hist, {"date": market_date_str(now), "avg_iv": round(float(avg_iv), 4)})
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


#: Tope por ticker para que el archivo no crezca sin control (`MAX_PER_TICKER`).
MAX_PER_TICKER = 5000

#: Un ticker más largo que esto no es un ticker. Sin el tope, uno de 300
#: caracteres llega al sistema de archivos y revienta con ENAMETOOLONG —un error
#: que depende del FS y del sistema— en vez del mismo `ValueError` determinista
#: que dan los demás tickers inservibles. El más largo de verdad no pasa de 6.
MAX_TICKER_LEN = 64


@dataclass(frozen=True)
class StoredTrades:
    """`StoredTrades` de Víctor: el archivo no es una lista pelada.

    El envoltorio guarda de quién es el historial y cuándo se tocó por última
    vez, que es lo que permite responder "¿esta memoria está viva?" sin abrir
    los 5000 trades.
    """

    ticker: str
    updated_at: str
    trades: list[dict]


@dataclass(frozen=True)
class SaveResult:
    """`SaveResult` de Víctor — lo que su UI muestra tras guardar."""

    total: int          # cuántas quedan guardadas
    added: int          # cuántas eran nuevas en esta corrida
    first_seen: str | None  # fecha del trade más antiguo guardado


def _file_for(ticker: str) -> Path:
    """`fileFor`: el ticker es entrada de usuario y aquí acaba siendo una ruta.

    El saneado no es cosmético — sin él un `../` en el ticker escribe fuera del
    directorio de datos.

    Dos cosas que Víctor no hace:

    - **No crea el directorio.** Era un efecto secundario en la ruta de LECTURA:
      preguntar "¿hay historial?" dejaba carpetas escritas. El que escribe ya
      las crea.
    - **Rechaza el ticker que se queda en nada.** `"!!!"`, `"@@@"` y `""` sanean
      todos a la cadena vacía, así que `fileFor` los mandaba al MISMO
      `.json` — un cubo compartido donde la memoria de una consulta basura
      contamina la siguiente. Mejor un error que un archivo que no es de nadie.
    """
    safe = re.sub(r"[^A-Z0-9._-]", "", ticker.strip().upper())
    if not safe.strip("._-") or len(safe) > MAX_TICKER_LEN:
        raise ValueError(f"ticker inservible como nombre de archivo: {ticker!r}")
    return data_dir() / "trades" / f"{safe}.json"


def _ts_key(row: dict) -> float:
    """`Date.parse(timestamp)`, con los no-parseables al final (como NaN en JS).

    DIVERGENCIA deliberada: un timestamp SIN zona se lee como UTC, no como hora
    local. `Date.parse("2026-07-30T15:00:00")` (y `datetime.timestamp()`) lo
    interpretan en la zona del servidor, así que el mismo archivo se ordenaba
    distinto en UTC que en America/New_York — y el orden decide qué se cae por
    el tope de MAX_PER_TICKER. La persistencia no puede depender de la TZ de la
    máquina. Es además el criterio que ya usa `flow._epoch`, que directamente
    rechaza los naive.
    """
    try:
        dt = datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return float("-inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _dedupe_key(row: dict) -> Any:
    """La clave del `Map` de Víctor es `t.id`, y para datos válidos esto lo es.

    La guarda es para el id AUSENTE. `flow._base_row` hace
    `int(_num(raw.get("id")))`, así que un tape sin ese campo devuelve **0 para
    todos** los trades — y un `Map` con la misma clave 5000 veces conserva uno.
    No es teórico: basta con que MarketSnack renombre el campo para que la
    memoria del sub-agente 6 se vacíe en silencio, sin un solo error. Con id
    real el comportamiento es idéntico al suyo; sin él, cada trade conserva su
    identidad en vez de fundirse con los demás.
    """
    rid = row.get("id")
    if rid:
        return rid
    return ("sin-id", row.get("timestamp"), row.get("symbol"),
            row.get("strike"), row.get("premium"))


def load_trades(ticker: str) -> StoredTrades | None:
    """`loadTrades`. Devuelve `None` si aún no hay historial para este ticker.

    `None` y "historial vacío" son cosas distintas y el llamador las distingue:
    la primera es "nunca se ha guardado nada", la segunda "se guardó y no quedó
    nada". Por eso no colapsa a lista vacía.

    Un ticker que no da nombre de archivo tampoco tiene historial: leer nunca
    lanza. Guardarlo sí, porque ahí sí hay algo que se perdería en silencio.
    """
    try:
        path = _file_for(ticker)
    except ValueError:
        return None
    parsed = _read(path)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("trades"), list):
        return None
    return StoredTrades(
        ticker=str(parsed.get("ticker", "")),
        updated_at=str(parsed.get("updated_at", "")),
        # Simétrico con `save_trades`, que ya descarta lo que no es dict. En TS
        # una fila corrupta se lee como `undefined` y el pipeline sigue; en
        # Python cada `.get()` sobre ella revienta, y el `except` del llamador
        # convierte eso en "no hay memoria" — apagando el IV Rank real, el
        # sub-agente 6 y la calibración de golpe y sin un solo mensaje.
        trades=[t for t in parsed["trades"] if isinstance(t, dict)],
    )


def save_trades(ticker: str, rows: Sequence[Any]) -> SaveResult:
    """`saveTrades`: fusiona con lo guardado (dedupe por id) y persiste.

    Es lo que hace posible el sub-agente 6: un flow de hoy no tiene recorrido
    que juzgar, así que la Confirmación de Precio solo existe sobre lo que este
    archivo fue acumulando.

    Leer-fusionar-escribir va bajo cerrojo: sin él, dos peticiones al mismo
    ticker leen el mismo archivo y la segunda escribe encima de lo que acumuló
    la primera.
    """
    clean = ticker.strip().upper()
    path = _file_for(clean)   # antes del cerrojo: un ticker inservible falla ya

    with _exclusive(path):
        existing = load_trades(clean)
        by_id: dict[Any, dict] = {}

        for t in (existing.trades if existing else []):
            if isinstance(t, dict):
                by_id[_dedupe_key(t)] = t
        added = 0
        for r in rows:
            row = r if isinstance(r, dict) else asdict(r)
            key = _dedupe_key(row)
            if key not in by_id:
                added += 1
            by_id[key] = row   # el análisis más reciente gana

        merged = sorted(by_id.values(), key=_ts_key, reverse=True)[:MAX_PER_TICKER]

        _write(path, {
            "ticker": clean,
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "trades": merged,
        })

    oldest = merged[-1].get("timestamp") if merged else None
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


def save_prediction(ticker: str, snap: PredictionSnapshot) -> int:
    """Guarda la foto del día. Dedupe por (fecha, horizonte)."""
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


def review_predictions(
    snapshots: Sequence[dict],
    bars: Sequence[Any],
    now: datetime,
) -> dict:
    """Compara cada predicción contra lo que hizo el precio.

    Devuelve el **sesgo** (error medio *firmado* del target base) que alimenta
    la auto-calibración: si el agente apunta sistemáticamente alto, el sesgo
    sale positivo y `calibration_shift_pct` baja el próximo target.

    Solo cuentan las **vencidas**: juzgar una predicción a mitad de su horizonte
    mediría ruido, no acierto.
    """
    today = market_date_str(now)
    evals: list[dict] = []

    for s in snapshots:
        try:
            end = (date.fromisoformat(s["date"]) + timedelta(days=int(s["horizon_days"]))).isoformat()
        except (ValueError, KeyError, TypeError):
            continue
        matured = today >= end
        window = [b for b in bars if s["date"] < b.time <= end]
        if not window:
            evals.append({**s, "sessions": 0, "matured": matured, "actual_close": None,
                          "base_error_pct": None, "base_touched": False,
                          "direction_hit": None, "best": None})
            continue

        actual_close = window[-1].close
        actual_high = max(b.high for b in window)
        actual_low = min(b.low for b in window)
        spot = float(s["spot"])
        base_error_pct = ((actual_close - float(s["base"])) / spot * 100) if spot > 0 else None

        best = min(
            (("bear", s["bear"]), ("base", s["base"]), ("bull", s["bull"])),
            key=lambda t: abs(float(t[1]) - actual_close),
        )[0]

        moved = actual_close - spot
        flat_band = spot * 0.01
        if not matured:
            direction_hit = None
        elif s["direction"] == "up":
            direction_hit = moved > 0
        elif s["direction"] == "down":
            direction_hit = moved < 0
        else:
            direction_hit = abs(moved) <= flat_band

        evals.append({
            **s, "sessions": len(window), "matured": matured,
            "actual_close": actual_close,
            "base_error_pct": base_error_pct,
            "base_touched": _touched(float(s["base"]), spot, actual_high, actual_low),
            "direction_hit": direction_hit, "best": best,
        })

    evals.sort(key=lambda e: e["date"], reverse=True)
    mat = [e for e in evals if e["matured"] and e["actual_close"] is not None]

    def mean(xs: list[float]) -> float | None:
        return (sum(xs) / len(xs)) if xs else None

    errs = [e["base_error_pct"] for e in mat if e["base_error_pct"] is not None]
    best_counts = {"bear": 0, "base": 0, "bull": 0}
    for e in mat:
        if e["best"]:
            best_counts[e["best"]] += 1

    return {
        "evals": evals,
        "matured_count": len(mat),
        "mean_abs_error_pct": mean([abs(x) for x in errs]),
        "bias_pct": mean(errs),
        "base_touch_rate": (sum(1 for e in mat if e["base_touched"]) / len(mat) * 100) if mat else None,
        "direction_hit_rate": (sum(1 for e in mat if e["direction_hit"]) / len(mat) * 100) if mat else None,
        "best_counts": best_counts,
    }


def calibration_from_review(review: dict) -> dict:
    """Empaqueta el review en lo que espera `predict_pro(calibration=…)`."""
    return {"bias_pct": review.get("bias_pct"), "samples": review.get("matured_count", 0)}
