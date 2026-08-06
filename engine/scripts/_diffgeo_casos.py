"""Corpus del diferencial de `chartGeometry.ts`.

Es la ÚLTIMA transcripción a mano del port: las tres funciones de geometría
viven en JavaScript dentro de `vertex_fund_os_platform.html` (`vcSmartDomain`,
`vcBuildScales`, `vcPackLabels`) porque las ejecuta el navegador, no el motor.
Todo lo demás se compara contra su archivo; esto no se comparaba contra nada, y
transcribir a mano es exactamente donde ya se coló un error antes (`news.ts`).

Mismo criterio que los otros corpus: primero la mezcla bien formada y después
basura campo a campo.
"""
import json
import random

BASURA_NUM = [None, "500", "abc", "", [], {}, True, False, "NaN", "Infinity",
              1e308, -1e308, 0, -0.0, 0.1 + 0.2]


def _velas(n, base=100.0, semilla=1):
    r = random.Random(semilla)
    out, p = [], base
    for i in range(n):
        o = p
        c = round(p * (1 + r.uniform(-0.03, 0.03)), 4)
        out.append({"time": 1_780_000_000 + i * 86_400, "open": o,
                    "high": round(max(o, c) * 1.01, 4),
                    "low": round(min(o, c) * 0.99, 4), "close": c})
        p = c
    return out


def casos():
    r = random.Random(7)
    dominio, escalas, etiquetas = [], [], []

    # ── smartDomain ──────────────────────────────────────────────────────
    for _ in range(40):
        spot = r.choice([0, 1e-9, 100.0, 850.0, 1e6])
        s = spot or 100.0
        dominio.append({
            "bars": _velas(r.choice([0, 1, 5, 70, 250]), s, r.randint(1, 99)),
            "spot": spot,
            "sigma1": r.choice([None, {"lower": s * 0.95, "upper": s * 1.05}]),
            "sigma2": r.choice([None, {"lower": s * 0.90, "upper": s * 1.10},
                                {"lower": s * 0.2, "upper": s * 3.0}]),
            "targets": [{"price": s * r.uniform(0.8, 1.2),
                         "weight": r.choice([0, 0.01, 0.02, 0.5, 1])}
                        for _ in range(r.choice([0, 1, 3]))],
            "levels": [{"price": s * r.uniform(0.7, 1.3)}
                       for _ in range(r.choice([0, 2, 6]))],
        })
    # Malformados: los campos que el panel rellena desde la respuesta del motor.
    base = {"bars": _velas(30), "spot": 100.0,
            "sigma1": {"lower": 95.0, "upper": 105.0},
            "sigma2": {"lower": 90.0, "upper": 110.0}, "targets": [], "levels": []}
    for campo in ("spot",):
        for v in BASURA_NUM:
            dominio.append({**base, campo: v})
    for v in BASURA_NUM:
        dominio.append({**base, "sigma1": {"lower": v, "upper": 105.0}})
        dominio.append({**base, "sigma2": {"lower": 90.0, "upper": v}})
        dominio.append({**base, "targets": [{"price": 103.0, "weight": v}]})
        dominio.append({**base, "levels": [{"price": v}]})
    dominio.append({**base, "sigma1": None, "sigma2": None})
    dominio.append({**base, "bars": []})

    # ── buildScales ──────────────────────────────────────────────────────
    pad = {"top": 18, "right": 132, "bottom": 30, "left": 10}
    for _ in range(40):
        escalas.append({
            "bars": _velas(r.choice([0, 1, 3, 70, 400]), 100.0, r.randint(1, 99)),
            "domain": {"min": r.choice([0.0, 80.0, 99.999]),
                       "max": r.choice([120.0, 100.0, 1e6])},
            "horizonDays": r.choice([0, -5, 1, 10, 20, 30, 120]),
            "width": r.choice([320, 900, 2000]),
            "height": r.choice([260, 440, 1200]),
            "padding": pad,
            "futureRatio": r.choice([0, 0.4, 0.95, 1.5, -1]),
        })
    b2 = {"bars": _velas(70), "domain": {"min": 90.0, "max": 110.0},
          "horizonDays": 20, "width": 900, "height": 440, "padding": pad,
          "futureRatio": 0.4}
    for campo in ("horizonDays", "width", "height", "futureRatio"):
        for v in BASURA_NUM:
            escalas.append({**b2, campo: v})
    escalas.append({**b2, "domain": {"min": 100.0, "max": 100.0}})
    escalas.append({**b2, "domain": {"min": 110.0, "max": 90.0}})

    # ── packLabels ───────────────────────────────────────────────────────
    for _ in range(40):
        n = r.choice([0, 1, 2, 5, 12, 40])
        etiquetas.append({
            "items": [{"y": round(r.uniform(-50, 500), 3), "id": i} for i in range(n)],
            "opts": {"top": r.choice([0, 18]), "bottom": r.choice([100, 410]),
                     "labelH": r.choice([1, 34, 200]), "gap": r.choice([0, 5, 50])},
        })
    for v in BASURA_NUM:
        etiquetas.append({"items": [{"y": v, "id": 0}, {"y": 100.0, "id": 1}],
                          "opts": {"top": 18, "bottom": 410, "labelH": 34, "gap": 5}})
    return {"dominio": dominio, "escalas": escalas, "etiquetas": etiquetas}


if __name__ == "__main__":
    print(json.dumps(casos()))
