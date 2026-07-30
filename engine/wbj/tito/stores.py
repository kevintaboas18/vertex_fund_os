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
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Sequence

from .occ import market_date_str

__all__ = [
    "data_dir",
    "CHAIN_DAYS",
    "IV_DAYS",
    "JOURNAL_DAYS",
    "TRADES_DAYS",
    "save_chain_snapshot",
    "load_chain_history",
    "save_iv_snapshot",
    "load_iv_history",
    "save_trades",
    "load_trades",
    "PredictionSnapshot",
    "save_prediction",
    "load_journal",
    "review_predictions",
    "calibration_from_review",
]

#: Ventanas que guarda cada serie, en días. Son las de Víctor.
CHAIN_DAYS = 45      # chainStore: el documento pide 45 días de cadena
IV_DAYS = 365        # ivStore: ventana de 52 semanas para el rank
JOURNAL_DAYS = 120   # predictionStore
TRADES_DAYS = 120    # trades por ticker para el backtest del sub-agente 6


def data_dir() -> Path:
    """Directorio de datos. `WBJ_TITO_DATA` lo redirige (disco de Render, etc.)."""
    d = Path(os.environ.get("WBJ_TITO_DATA", "") or (Path.cwd() / "data" / "tito"))
    return d


def _path(kind: str, ticker: str) -> Path:
    p = data_dir() / kind
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{ticker.upper()}.json"


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, payload: Any) -> None:
    """Escritura atómica: un proceso muerto a media escritura no debe dejar un
    JSON truncado que luego se lea como 'sin historial'."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
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
    hist = _read(path) or []
    hist = _upsert(hist, {"date": market_date_str(now), "avg_iv": round(float(avg_iv), 4)})
    hist = _prune(hist, IV_DAYS)
    _write(path, hist)
    return len(hist)


def load_iv_history(ticker: str) -> list[dict]:
    """Formato que consume `iv_context_score`: ``[{date, avg_iv}, …]``."""
    return _prune(_read(_path("iv", ticker)) or [], IV_DAYS)


# ─────────────────────────── trades (sub-agente 6) ──────────────────────────


def save_trades(ticker: str, rows: Sequence[Any], now: datetime) -> int:
    """Acumula los trades notables. Es lo que hace posible el sub-agente 6.

    Dedupe por `id` del trade, no por día: en una misma sesión llegan trades
    nuevos y re-guardar no debe perder los anteriores ni duplicarlos.
    """
    path = _path("trades", ticker)
    prev = _read(path) or []
    seen = {r.get("id") for r in prev if isinstance(r, dict)}
    for r in rows:
        if r.id in seen:
            continue
        seen.add(r.id)
        prev.append({
            "id": r.id, "timestamp": r.timestamp, "type": r.type, "strike": r.strike,
            "expiration": r.expiration, "asset_price": r.asset_price,
            "premium": r.premium, "aggression": r.aggression,
        })
    cutoff = (date.today() - timedelta(days=TRADES_DAYS)).isoformat()
    prev = sorted(
        (r for r in prev if str(r.get("timestamp", ""))[:10] >= cutoff),
        key=lambda r: r["timestamp"],
    )
    _write(path, prev)
    return len(prev)


def load_trades(ticker: str) -> list[dict]:
    return _read(_path("trades", ticker)) or []


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
