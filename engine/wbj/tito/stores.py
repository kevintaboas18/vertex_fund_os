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

from .jsmath import (RangeError, UNDEFINED, es_nulo, js_abs, js_date_parse, js_gt, js_le,
                     js_max, js_min, js_number, js_orden, js_string)
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
    "migra_series",
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


# ── El SOBRE de sus tres series diarias ─────────────────────────────────────
#
# Los tres —cadena, IV y predicciones— comparten forma en su repo:
#
#     { ticker, updatedAt, snapshots: [ … ] }   // más reciente primero
#
# Y comparten reglas: una foto por día de mercado (la del día se REEMPLAZA en
# cada corrida), orden descendente por fecha, y recorte **por cantidad** con
# `.slice(0, N)` — no por ventana de fechas.
#
# La diferencia entre las dos formas de recortar importa: si un ticker se deja
# de mirar seis meses, su recorte conserva las 365 fotos que hay y el de fecha
# las tira todas. El IV Rank se quedaría sin historia justo en el ticker que más
# tiempo lleva acumulándola.
#
# Todo esto se escribe en **camelCase**, que es como lo escribe él. Los objetos
# de Python siguen en snake_case, como el resto del port: la traducción ocurre
# aquí, en el borde del disco, igual que `massive.py` traduce su API. El archivo
# que queda es intercambiable con el de su app — se comprueba en
# `diff_series.sh`, que lo lee con SU TypeScript.


def _sobre(ticker: str, snapshots: list[dict], now: datetime) -> dict:
    """`{ ticker, updatedAt, snapshots }` — su envoltorio, literal."""
    return {"ticker": ticker.strip().upper(),
            "updatedAt": _iso(now), "snapshots": snapshots}


def _iso(d: datetime) -> str:
    """`Date.toISOString()`: UTC, milisegundos y `Z`."""
    u = d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


def _lee_sobre(path) -> dict | None:
    """Su `load*`: el objeto si trae `snapshots`, `None` si no.

    `Array.isArray(parsed.snapshots) ? parsed : null` — un archivo con otra
    forma NO es un historial vacío, es un archivo que no reconocemos, y
    devolver `None` es lo que deja al llamador distinguir «aún no hay nada» de
    «hay algo que no sé leer».
    """
    crudo = _read(path)
    if not isinstance(crudo, dict) or not isinstance(crudo.get("snapshots"), list):
        return None
    return crudo


def _fusiona(previas: list[dict], nueva: dict, tope: int) -> list[dict]:
    """Dedupe por fecha + orden descendente + recorte por CANTIDAD.

    Es su bloque de `new Map()` → `sort(b.date.localeCompare(a.date))` →
    `.slice(0, N)`, en ese orden. El `Map` hace que la foto de hoy pise a la de
    hoy: en cada corrida se actualiza, no se acumulan varias del mismo día.
    """
    por_fecha: dict[str, dict] = {}
    for s in previas:
        if isinstance(s, dict) and s.get("date"):
            por_fecha[s["date"]] = s
    por_fecha[nueva["date"]] = nueva
    ordenadas = sorted(por_fecha.values(), key=lambda s: str(s.get("date", "")),
                       reverse=True)
    return ordenadas[:tope]


# ─────────────────────────────── cadena (sub-agente 4) ──────────────────────


def save_chain_snapshot(ticker: str, s: Any, now: datetime) -> int:
    """Guarda la foto del día de la cadena. Devuelve los días acumulados.

    Port de su `saveChainSnapshot`. Recibe el **StructureScore ya calculado**,
    no la cadena cruda: lo que él persiste es el resultado del sub-agente 4 —
    su score, sus puntos por cada parte y los 5 strikes de más nocional—, no
    los miles de contratos que lo produjeron.

    Antes aquí se guardaba `{date, strikes:[{strike, call_oi, put_oi, volume}]}`,
    que no era su formato **ni sus datos**: sin `score` ni `points` no se puede
    reconstruir por qué el sub-agente 4 puntuó lo que puntuó un día concreto,
    que es justo para lo que existe este historial.
    """
    fecha = market_date_str(now)
    n, k, v = s.notional, s.strikes, s.vol_oi
    foto = {
        "date": fecha,
        "savedAt": _iso(now),
        "score": s.score,
        "avgNotionalPerStrike": n["avg_per_strike"],
        "totalNotional": n["total"],
        "strikeCount": n["strike_count"],
        "notionalPoints": n["points"],
        "dominantCount": k["dominant_count"],
        "strikePoints": k["points"],
        "volOIPct": v["pct"],
        "volOIPoints": v["points"],
        "callPct": k["call_pct"],
        "putPct": k["put_pct"],
        "dominantSide": k["dominant_side"],
        "lowLiquidity": n["low_liquidity"],
        # `.slice(0, 5)` suyo: los cinco de más nocional, con tres campos.
        "topStrikes": [{"strike": x.strike, "notional": x.notional, "side": x.side}
                       for x in k["top"][:5]],
    }
    path = _path("chain", ticker)
    with _exclusive(path):   # mismo motivo que en save_trades: leer-fusionar-escribir
        previo = _lee_sobre(path)
        fotos = _fusiona((previo or {}).get("snapshots", []), foto, CHAIN_DAYS)
        _write(path, _sobre(ticker, fotos, now))
    return len(fotos)


def load_chain_history(ticker: str) -> list[dict]:
    """Las fotos guardadas, más reciente primero. Lista vacía si no hay nada.

    Devuelve el ARRAY y no el sobre porque es lo que consume el motor; sus
    rutas hacen lo mismo (`hist?.snapshots ?? []`). El sobre vive en el
    archivo, que es donde tiene que estar para que sea el suyo.
    """
    return (_lee_sobre(_path("chain", ticker)) or {}).get("snapshots", [])


# ─────────────────────────────── IV (sub-agente 5) ──────────────────────────


def save_iv_snapshot(ticker: str, s: Any, now: datetime) -> int:
    """Guarda la foto de IV del día. Alimenta el IV Rank real.

    Port de su `saveIvSnapshot`. Recibe el **IvContextScore** entero, como él:
    de ahí salen `avgIv` (la IV ponderada por premium — el dinero grande define
    el contexto, no los cientos de tickets de 0DTE), y los cuatro campos que
    permiten auditar después por qué la IV de un día salió como salió.

    Su guarda es `if (s.iv.current == null)`: solo el nulo. Un `current` de 0 SÍ
    se guarda, y se filtra al leer (`Number.isFinite(v) && v > 0`). Aquí se hace
    igual — rechazarlo al escribir cambiaría qué días existen en el historial.

    A partir de `MIN_IV_HISTORY_DAYS` (60) muestras, `iv_context_score` deja de
    usar el proxy de volatilidad realizada y pasa al rank de verdad, solo.
    """
    actual = s.iv.get("current") if isinstance(s.iv, dict) else None
    path = _path("iv", ticker)
    if es_nulo(actual):
        # Su `return existing ?? {…, snapshots: []}`: no se escribe nada.
        return len(load_iv_history(ticker))
    foto = {
        "date": market_date_str(now),
        "savedAt": _iso(now),
        "avgIv": actual,
        "minIv": s.iv.get("min"),
        "maxIv": s.iv.get("max"),
        "contracts": s.iv.get("contracts"),
        "frontSkew": s.front_skew,
    }
    with _exclusive(path):
        previo = _lee_sobre(path)
        fotos = _fusiona((previo or {}).get("snapshots", []), foto, IV_DAYS)
        _write(path, _sobre(ticker, fotos, now))
    return len(fotos)


def load_iv_history(ticker: str) -> list[dict]:
    """Las fotos de IV, más reciente primero.

    `iv_context_score` lee `avgIv` de cada una — la clave que hay en el archivo
    y en el suyo. Un historial viejo en snake_case NO cuenta: el rank se
    quedaría en el proxy de volatilidad realizada sin decir nada. Por eso
    `migra_series()` corre en el arranque y no cuando alguien se acuerde.
    """
    return (_lee_sobre(_path("iv", ticker)) or {}).get("snapshots", [])


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


def _prop_seam(obj: Any, name: str, suyo: str) -> Any:
    """`_prop`, pero mirando también el nombre que tiene el campo EN EL ARCHIVO.

    Esta función existe por una costura real, no por gusto. El archivo del
    diario se escribe con SU formato —`horizonDays`, camelCase— a propósito:
    es lo que lo hace intercambiable con el de su app. El port, en cambio,
    habla snake_case, y `review_predictions` leía `horizon_days`.

    Las dos decisiones son correctas por separado y juntas rompían la cadena:
    `load_journal` devolvía `horizonDays`, `_prop` no lo encontraba y daba
    `None`, el vencimiento se calculaba contra una fecha inválida y **ninguna
    predicción vencía nunca**. `matured_count` salía 0 con meses de historial,
    el sesgo se quedaba en `None` y la auto-calibración no se activaba jamás.

    Ningún test lo vio: los unitarios construyen las fotos a mano en
    snake_case, y `_diffcalib_compara.py` **traduce** `horizonDays` a
    `horizon_days` antes de llamar. O sea que la traducción existía en el banco
    de pruebas y no en producción — el modo exacto de que 182/182 casos estén
    verdes con el lazo abierto.
    """
    v = _prop(obj, name)
    return _prop(obj, suyo) if v is None else v


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
    foto = {
        "date": snap.date,
        "savedAt": snap.saved_at,
        "spot": snap.spot,
        "horizonDays": snap.horizon_days,
        "bear": snap.bear,
        "base": snap.base,
        "bull": snap.bull,
        "direction": snap.direction,
        "confidence": snap.confidence,
    }
    path = _path("predictions", ticker)
    with _exclusive(path):
        # Se llama una vez POR HORIZONTE. Sin cerrojo, dos peticiones a la vez
        # se pisan el diario y la calibración se queda con huecos.
        previo = _lee_sobre(path)
        previas = (previo or {}).get("snapshots", [])
        # La clave de dedupe es la divergencia declarada arriba: (fecha,
        # horizonte) en vez de solo la fecha. `_fusiona` deduplica por fecha, así
        # que las otras del mismo día se apartan antes y se vuelven a meter.
        del_dia = [r for r in previas
                   if isinstance(r, dict) and r.get("date") == snap.date
                   and r.get("horizonDays") != snap.horizon_days]
        resto = [r for r in previas
                 if isinstance(r, dict) and r.get("date") != snap.date]
        fotos = _fusiona(resto, foto, JOURNAL_DAYS)
        fotos = sorted(fotos + del_dia,
                       key=lambda r: (str(r.get("date", "")),
                                      -int(r.get("horizonDays") or 0)),
                       reverse=True)[:JOURNAL_DAYS]
        _write(path, _sobre(ticker, fotos, _fecha_de(snap.saved_at)))
    return len(fotos)


def _fecha_de(iso: str | None) -> datetime:
    """El `savedAt` de la foto como `datetime`, para que el `updatedAt` del
    sobre sea el mismo instante y no el reloj de pared de otra línea."""
    try:
        return datetime.strptime(iso or "", "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def load_journal(ticker: str) -> list[dict]:
    """El diario, más reciente primero. Lista vacía si no hay nada.

    `review_predictions` lee `horizonDays` y `savedAt` de cada fila — las
    claves que hay en el archivo, que son las suyas. Su propio
    `reviewPredictions` lee exactamente esas, así que el mismo archivo lo
    procesan los dos sin tocar nada.
    """
    return (_lee_sobre(_path("predictions", ticker)) or {}).get("snapshots", [])


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
        end = _add_calendar_days(_prop(s, "date"),
                                 _prop_seam(s, "horizon_days", "horizonDays"))
        matured = today >= end
        fecha = _prop(s, "date")
        # `b.time > s.date && b.time <= end`: comparación de TEXTO cuando los dos
        # lo son, con la coacción de JS si alguno no lo es (`jsmath.js_le`).
        window = [b for b in bars
                  if not js_le(b.time, fecha) and js_le(b.time, end)]
        base = {
            "date": fecha,
            "horizon_days": _prop_seam(s, "horizon_days", "horizonDays"),
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


# ── Migración de los archivos que se escribieron con el formato viejo ────────
#
# Los tres stores de arriba escribían una LISTA PELADA con las claves en
# snake_case. Ahora escriben su sobre `{ticker, updatedAt, snapshots}` con las
# claves en camelCase, que es lo que hace el archivo intercambiable con el de
# su app.
#
# Esto convierte lo que ya estuviera guardado. Va aquí y no dentro de los
# `load_*` a propósito: esos son port literal de su código, y su código no sabe
# nada de un formato anterior. La conversión es política de Vertex, corre una
# vez al arrancar, y después los stores no vuelven a verla.
#
# Sin esto, un historial viejo se leería como vacío —`_lee_sobre` devuelve
# `None` ante una lista— y se perderían los días acumulados: justo el dato que
# más tarda en recuperarse, porque solo crece a una foto por día de mercado.

#: Cómo se llamaba cada campo antes y cómo se llama ahora.
_RENOMBRES = {
    "avg_iv": "avgIv", "min_iv": "minIv", "max_iv": "maxIv",
    "front_skew": "frontSkew", "saved_at": "savedAt",
    "horizon_days": "horizonDays",
}


def migra_series(ticker: str = "") -> dict[str, int]:
    """Convierte los archivos viejos al formato de Víctor. Idempotente.

    Devuelve cuántos archivos migró por carpeta. Un archivo que ya está en el
    formato nuevo no se toca, así que llamarla en cada arranque no cuesta nada
    ni reescribe historia.

    La cadena es el caso raro: el formato viejo guardaba `{date, strikes:[…]}`,
    que no son los datos de su foto —él persiste el `StructureScore`, con su
    score y sus puntos—. Esos días no se pueden convertir porque la información
    no está; se descartan y se cuentan aparte. Es una pérdida real y por eso se
    dice, en vez de dejar un archivo medio traducido que parezca completo.
    """
    hecho = {"iv": 0, "predictions": 0, "chain_descartados": 0}
    ahora = datetime.now(timezone.utc)
    for carpeta in ("iv", "predictions", "chain"):
        base = data_dir() / carpeta
        if not base.is_dir():
            continue
        for archivo in sorted(base.glob("*.json")):
            crudo = _read(archivo)
            if not isinstance(crudo, list):
                continue                          # ya está en el formato nuevo
            tk = archivo.stem
            if carpeta == "chain":
                # Sin `score` ni `points` no hay foto que reconstruir.
                _write(archivo, _sobre(tk, [], ahora))
                hecho["chain_descartados"] += 1
                continue
            fotos = []
            for fila in crudo:
                if not isinstance(fila, dict) or not fila.get("date"):
                    continue
                nueva = {_RENOMBRES.get(k, k): v for k, v in fila.items()}
                nueva.setdefault("savedAt", f"{nueva['date']}T00:00:00.000Z")
                fotos.append(nueva)
            fotos.sort(key=lambda s: str(s.get("date", "")), reverse=True)
            tope = IV_DAYS if carpeta == "iv" else JOURNAL_DAYS
            _write(archivo, _sobre(tk, fotos[:tope], ahora))
            hecho[carpeta] += 1
    return hecho
