"""Persistencia del buzón de salida hacia el broker — port de `lib/outboxStore.ts`.

Solo servidor. La lógica pura vive en `watchlist.py`.

Ojo con lo que NO guarda: los griegos, tu sizing y tu saldo se quedan en el
navegador. Aquí solo cae la identidad del contrato (ticker y, si el broker los
acepta, tipo/strike/vencimiento), que es lo mínimo para resolverlo en el broker.

Su archivo es `web/data/outbox.json`; aquí cuelga del mismo directorio de datos
que el resto de stores portados (`stores.data_dir()`), que es el que Render
monta en disco. Escribe con el renombre atómico de `stores._write`: un proceso
muerto a media escritura no deja un JSON truncado que luego se lea como «cola
vacía» y pierda lo encolado.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .stores import _read, _write, data_dir
from .watchlist import OutboxItem

__all__ = ["load_outbox", "save_outbox", "ARCHIVO"]

ARCHIVO = "outbox.json"

_VACIO: dict[str, Any] = {"updatedAt": "", "items": []}


def _ruta():
    return data_dir() / ARCHIVO


def _a_item(d: dict[str, Any]) -> OutboxItem:
    """Una fila del disco a `OutboxItem`, tolerando la cola vieja de
    solo-tickers (sin `symbol`/`type`/`strike`/`expiration`)."""
    strike = d.get("strike")
    return OutboxItem(
        ticker=str(d.get("ticker") or ""),
        broker=str(d.get("broker") or ""),
        addedAt=str(d.get("addedAt") or ""),
        syncedAt=d.get("syncedAt") or None,
        symbol=d.get("symbol") or None,
        type=d.get("type") or None,
        strike=strike if isinstance(strike, (int, float)) else None,
        expiration=d.get("expiration") or None,
        failedAt=d.get("failedAt") or None,
        failReason=d.get("failReason") or None,
    )


def _a_json(i: OutboxItem) -> dict[str, Any]:
    """Al disco con la MISMA forma que su JSON.

    Los campos de contrato solo aparecen cuando la fila los tiene. No es
    cosmética: `remove_from_outbox` distingue la fila legado de la nueva por la
    AUSENCIA de `symbol`, así que volcar el dataclass entero —con `symbol:
    null`— no cambiaría nada hoy pero sí en cuanto alguien lea ese archivo con
    su código, donde `"symbol" in item` sería cierto para una fila que no lo
    tiene.
    """
    d: dict[str, Any] = {
        "ticker": i.ticker,
        "broker": i.broker,
        "addedAt": i.addedAt,
        "syncedAt": i.syncedAt,
    }
    if i.symbol:
        d["symbol"] = i.symbol
        d["type"] = i.type
        d["strike"] = i.strike
        d["expiration"] = i.expiration
    if i.failedAt:
        d["failedAt"] = i.failedAt
        d["failReason"] = i.failReason
    return d


def load_outbox() -> dict[str, Any]:
    crudo = _read(_ruta())
    if not isinstance(crudo, dict) or not isinstance(crudo.get("items"), list):
        return dict(_VACIO)          # aún no hay nada encolado
    filas = [_a_item(x) for x in crudo["items"] if isinstance(x, dict)]
    return {"updatedAt": str(crudo.get("updatedAt") or ""), "items": filas}


def save_outbox(items: list[OutboxItem]) -> dict[str, Any]:
    ahora = datetime.now(timezone.utc)
    marca = ahora.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ahora.microsecond // 1000:03d}Z"
    _write(_ruta(), {"updatedAt": marca, "items": [_a_json(i) for i in items]})
    return {"updatedAt": marca, "items": list(items)}
