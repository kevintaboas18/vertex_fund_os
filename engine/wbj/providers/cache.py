"""Filesystem-backed JSON response cache for wbj providers.

File layout: `<cache_dir>/<TICKER>/<key>.json` containing
`{"fetched_at": iso8601 UTC, "payload": ...}`.

This module reads the wall clock (`datetime.now(timezone.utc)`) to stamp
and age cache entries — that is infrastructure bookkeeping, not analysis
math, and is exempt from the engine's null-state/lineage discipline
(see `wbj.core.nullstates`).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

#: Reintentos ante un fallo TRANSITORIO al abrir una entrada (ver
#: `_read_record`). Acotado a propósito: la caché es una optimización, así
#: que nunca puede convertirse en una espera larga.
_REINTENTOS_LECTURA = 3
_ESPERA_LECTURA_S = 0.01


class Cache:
    """Filesystem-backed JSON cache, keyed by ticker and cache key."""

    def __init__(self, cache_dir: Path | str) -> None:
        self.cache_dir = Path(cache_dir)

    def _path(self, ticker: str, key: str) -> Path:
        return self.cache_dir / ticker / f"{key}.json"

    def _read_record(self, ticker: str, key: str) -> dict | None:
        path = self._path(ticker, key)
        if not path.exists():
            return None
        for intento in range(_REINTENTOS_LECTURA):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # Contenido roto de verdad: reintentar no lo arregla.
                return None
            except OSError:
                # En Windows, `os.replace` deja una ventana brevísima en la
                # que abrir el destino da PermissionError (sharing violation)
                # aunque el reemplazo en sí funcione — medido: 15 lecturas
                # perdidas en 1.5 s con tres escritores. En Linux (Render)
                # esto no ocurre. Sin el reintento el fallo es invisible pero
                # no gratis: el lector lo trata como "no hay caché" y vuelve
                # a pedirle a la API algo que ya estaba en disco.
                if intento == _REINTENTOS_LECTURA - 1:
                    return None
                time.sleep(_ESPERA_LECTURA_S)
        return None

    def get(self, ticker: str, key: str) -> dict | None:
        """Return the cached payload for (ticker, key), or None if absent/corrupt."""
        record = self._read_record(ticker, key)
        if record is None:
            return None
        return record.get("payload")

    def put(self, ticker: str, key: str, payload: dict) -> None:
        """Write payload to cache, stamped with the current UTC time.

        La escritura es ATÓMICA: a un temporal en el mismo directorio y
        luego `os.replace`, que es atómico en POSIX y en Windows. Un lector
        ve siempre el archivo entero — el viejo o el nuevo, nunca uno a
        medias.

        `path.write_text` no daba esa garantía: abre truncando, así que
        entre el truncado y el volcado el archivo está VACÍO en disco. Y la
        aplicación web corre cuatro hilos de fondo (el planificador, el
        backfill, el índice de FMP y la colección bajo demanda) que escriben
        esta misma caché mientras las peticiones en vivo la leen. El lector
        tolera el destrozo devolviendo `None`, así que no se veía como un
        fallo: se veía como una petición más a la API, gastando cuota para
        recuperar algo que ya estaba guardado.

        El temporal va en el MISMO directorio a propósito: `os.replace`
        sólo es atómico dentro del mismo sistema de archivos.
        """
        path = self._path(ticker, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(record), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            # Un fallo al cachear nunca puede tumbar el análisis: el dato ya
            # se obtuvo, y la caché es una optimización.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def age_days(self, ticker: str, key: str) -> float | None:
        """Return the cache entry's age in days, or None if absent/corrupt."""
        record = self._read_record(ticker, key)
        if record is None:
            return None
        try:
            fetched_at = datetime.fromisoformat(record["fetched_at"])
        except (KeyError, ValueError, TypeError):
            return None
        delta = datetime.now(timezone.utc) - fetched_at
        return delta.total_seconds() / 86400.0
