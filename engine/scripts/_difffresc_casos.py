"""Casos del diferencial de `recencyFactor` + el lado del port.

Barre la rejilla donde su cuenta de días y una cuenta de días de CALENDARIO
dejan de coincidir: el desfase entre las dos es `(hora_UTC - 21) / 24`, así que
solo se separan cuando la diferencia cae **justo** en un umbral (30, 90 o 180
días) y la consulta va después de las 21:00Z. Por eso las horas de la rejilla se
concentran ahí y los toques se eligen para caer exactamente en los tres bordes.

Uso: `FR_CASOS=… FR_PY_OUT=… python3 _difffresc_casos.py`
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from wbj.tito.levels import recency_factor  # noqa: E402

AHORA = date(2026, 7, 31)
#: Los tres umbrales y sus vecinos inmediatos, más un par de casos lejanos.
DELTAS = [0, 1, 29, 30, 31, 89, 90, 91, 179, 180, 181, 365, -1, -10]
HORAS = [0, 6, 12, 18, 20, 21, 22, 23]

casos = []
for delta in DELTAS:
    toque = (AHORA - timedelta(days=delta)).isoformat()
    for h in HORAS:
        for minuto in (0, 30, 59):
            casos.append({
                "last_touch": toque,
                "now": datetime(2026, 7, 31, h, minuto, tzinfo=timezone.utc)
                       .isoformat().replace("+00:00", "Z"),
            })
# Entradas que su `Date.parse` no entiende: concatenar `T21:00:00Z` a algo que ya
# trae hora da una cadena inválida, y él responde 1 por el `!Number.isFinite`.
for raro in ("", "no soy fecha", "2026-07-31T00:00:00Z", "2026-13-45", "2026", "2026-07"):
    casos.append({"last_touch": raro, "now": "2026-07-31T22:00:00Z"})

with open(os.environ["FR_CASOS"], "w", encoding="utf-8") as fh:
    json.dump(casos, fh)

mio = [recency_factor(c["last_touch"],
                      datetime.fromisoformat(c["now"].replace("Z", "+00:00")))
       for c in casos]
with open(os.environ["FR_PY_OUT"], "w", encoding="utf-8") as fh:
    json.dump(mio, fh)
print(f"  port evaluó {len(mio)} casos")
