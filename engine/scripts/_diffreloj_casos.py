"""Casos + lado del port del diferencial del RELOJ.

Cinco funciones del motor cuentan tiempo, y las cinco lo hacían con
`datetime.fromisoformat` de Python donde él usa `Date.parse` de JS. No son la
misma función: `fromisoformat` acepta cosas que `Date.parse` rechaza y al revés,
y su aritmética es en milisegundos desde epoch, no en días de calendario.

    occ.days_to_expiration       Math.round((exp - marketToday) / DAY)
    gex_heatmap._dte_of          Math.max(0, Math.round(ms / DAY))
    validation._days_between     Math.floor(ms / DAY)
    news.recency_weight          (now - pub) / HOUR
    flow._epoch                  Math.floor(Date.parse(ts) / 1000)

El corpus mezcla lo que Massive y MarketSnack mandan de verdad con las formas
que los rompen: vencimiento CON hora (alcanzable desde que `compute` es literal
y no recorta a `YYYY-MM-DD`), formatos cortos del estándar (`"2026"`,
`"2026-07"`), timestamp sin zona y basura.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from datetime import datetime, timezone  # noqa: E402

from wbj.tito.flow import _epoch  # noqa: E402
from wbj.tito.gex_heatmap import _dte_of  # noqa: E402
from wbj.tito.news import recency_weight  # noqa: E402
from wbj.tito.occ import days_to_expiration  # noqa: E402
from wbj.tito.validation import _days_between  # noqa: E402

AHORA = "2026-07-31T18:30:00Z"

FECHAS = [
    "2026-09-18", "2026-07-31", "2026-07-30", "2026-01-01", "2025-12-31",
    # Lo que rompe: vencimiento con hora (Massive lo manda así a veces y
    # `compute` ya no lo recorta), formatos cortos, fecha imposible y basura.
    "2026-09-18T00:00:00Z", "2026-09-18T00:00:00", "2026-09-18T21:00:00Z",
    "2026", "2026-09", "2026-13-45", "20260918", "basura", "",
]

STAMPS = [
    "2026-07-31T15:00:00Z", "2026-07-31T18:29:00Z", "2026-07-31T19:00:00Z",
    "2026-07-30T15:00:00-05:00", "2026-07-25T15:00:00Z", "2026-06-01T00:00:00Z",
    "2026-08-01T00:00:00Z", "2026-07-31T15:00:00.250Z",
    # Sin zona: ES2015+ lo lee en la zona LOCAL. El port lo descartaba.
    "2026-07-31T15:00:00", "2026-07-31", "2026", "ayer", "",
]

PARES = [(a, b) for a in STAMPS for b in STAMPS]


def _n(x):
    """Los no finitos viajan como texto: JSON no tiene NaN."""
    if x is None:
        return None
    if isinstance(x, float):
        if math.isnan(x):
            return "NaN"
        if math.isinf(x):
            return "Inf" if x > 0 else "-Inf"
    return x


def main() -> None:
    now = datetime.fromisoformat(AHORA.replace("Z", "+00:00")).astimezone(timezone.utc)
    with open(os.environ["RJ_CASOS"], "w", encoding="utf-8") as fh:
        json.dump({"now": AHORA, "fechas": FECHAS, "stamps": STAMPS, "pares": PARES}, fh)

    mio = {
        "dte":  [_n(days_to_expiration(f, now)) for f in FECHAS],
        "heat": [_n(_dte_of(f, now)) for f in FECHAS],
        "betw": [_n(_days_between(a, b)) for a, b in PARES],
        "news": [_n(recency_weight(t, now)) for t in STAMPS],
        "clus": [_n(_epoch(t)) for t in STAMPS],
    }
    with open(os.environ["RJ_PY_OUT"], "w", encoding="utf-8") as fh:
        json.dump(mio, fh)
    print(f"  port evaluó {sum(len(v) for v in mio.values())} valores")


if __name__ == "__main__":
    main()
