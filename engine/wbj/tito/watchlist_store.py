"""LEGADO — solo lectura, y solo para migrar una vez. Port de `lib/watchlistStore.ts`.

El watchlist vivía aquí, en un archivo único del servidor. Eso significaba un
solo watchlist para todos en un despliegue compartido, y el saldo de cada
persona aterrizando en el servidor. Ahora vive en el navegador (el bloque
`wlLocal*` del panel, port de su `watchlistLocal.ts`).

Este módulo se queda para que quien ya tuviera un `watchlist.json` no pierda lo
marcado: la página lo importa una vez y no vuelve a mirarlo. Ya nadie escribe
aquí — no hay `save`, y esa ausencia es el punto.
"""

from __future__ import annotations

from typing import Any

from .stores import _read, data_dir
from .watchlist import WatchlistEntry

__all__ = ["load_watchlist", "ARCHIVO"]

ARCHIVO = "watchlist.json"

_VACIO: dict[str, Any] = {"updatedAt": "", "broker": "none", "entries": []}


def _a_entry(d: dict[str, Any]) -> WatchlistEntry:
    def _f(k: str, por_defecto: float = 0.0) -> float:
        v = d.get(k)
        return float(v) if isinstance(v, (int, float)) else por_defecto

    def _on(k: str):
        v = d.get(k)
        return v if isinstance(v, (int, float)) else None

    return WatchlistEntry(
        symbol=str(d.get("symbol") or ""),
        ticker=str(d.get("ticker") or ""),
        type=str(d.get("type") or "call"),
        strike=_on("strike"),
        expiration=d.get("expiration") or None,
        addedAt=str(d.get("addedAt") or ""),
        entrySpot=_f("entrySpot"),
        entryPrice=_f("entryPrice"),
        entryDte=_on("entryDte"),
        entryPremium=_f("entryPremium"),
        entryThetaPctDaily=_on("entryThetaPctDaily"),
        maxContracts=int(_f("maxContracts")),
        binding=d.get("binding") or None,
        accountSizeAtEntry=_f("accountSizeAtEntry"),
        tolerancePctAtEntry=_f("tolerancePctAtEntry"),
        brokerSync=None,
    )


def load_watchlist() -> dict[str, Any]:
    crudo = _read(data_dir() / ARCHIVO)
    if not isinstance(crudo, dict) or not isinstance(crudo.get("entries"), list):
        return dict(_VACIO)          # nunca hubo watchlist en servidor
    return {
        "updatedAt": str(crudo.get("updatedAt") or ""),
        "broker": str(crudo.get("broker") or "none"),
        "entries": [_a_entry(x) for x in crudo["entries"] if isinstance(x, dict)],
    }
