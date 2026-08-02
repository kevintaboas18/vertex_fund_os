"""Lado Python del diferencial del cono: la geometría que sirve el endpoint.

Llama a `cone_points` y `prediction_path` —las dos funciones de su
`expectedMove.ts` que `/api/projection-targets` usa para `chart_geometry`— sobre
la misma rejilla de casos que la primera pasada, y vuelca el resultado para que
Node lo compare contra SU archivo.

Sin esto el diferencial mediría solo la fórmula de respaldo del HTML, que desde
que existe `chart_geometry` ya no es la que dibuja el panel.
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from wbj.tito.expected_move import cone_points, prediction_path  # noqa: E402

STEPS_CONO, STEPS_RUTA = 24, 30
casos = []
for iv in (0.001, 0.005, 0.0099, 0.01, 0.05, 0.2, 0.45, 0.8, 1.2, 2.0, 3.0):
    for days in (10, 20, 30):
        for spot in (5.0, 100.0, 850.0):
            # Targets a los dos lados y fuera del cono, para ejercitar el recorte.
            for mult in (0.85, 0.99, 1.0, 1.02, 1.30):
                target = spot * mult
                ruta = prediction_path(spot, target, iv, float(days), STEPS_RUTA)
                casos.append({
                    "spot": spot, "iv": iv, "days": float(days), "target": target,
                    "cone": [{"t": c.t, "upper1": c.upper1, "lower1": c.lower1,
                              "upper2": c.upper2, "lower2": c.lower2}
                             for c in cone_points(spot, iv, float(days), STEPS_CONO)],
                    "path": [{"t": t, "price": p} for t, p in ruta.points],
                })

with open(os.environ["CONO_PY_OUT"], "w", encoding="utf-8") as fh:
    json.dump(casos, fh)
print(f"  port evaluó {len(casos)} conos + rutas")
