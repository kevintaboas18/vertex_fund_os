"""Corpus del diferencial de `reviewPredictions` — el motor de calibración.

Es la función que decide cuánto se corrige el target base por el sesgo
histórico del agente: si sale mal, el motor se auto-corrige hacia el lado
equivocado y NADA en el reporte lo delata. Estaba portada y sin diferencial.

Mismo criterio que los otros corpus: primero bien formado, después basura.
"""
import json
import random
from datetime import date, timedelta

BASURA_NUM = [None, "500", "abc", "", [], {}, True, False, "NaN", "Infinity",
              1e308, -1e308, 0, -0.0]
BASURA_FECHA = [None, "", "2026-09-18", "2026-13-45", "20260918", "basura",
                20260918, "2026-09-18T00:00:00Z"]


def _barras(desde: str, n: int, base=100.0, semilla=1):
    r = random.Random(semilla)
    d0, out, p = date.fromisoformat(desde), [], base
    for i in range(n):
        c = round(p * (1 + r.uniform(-0.03, 0.03)), 4)
        out.append({"time": (d0 + timedelta(days=i)).isoformat(),
                    "high": round(max(p, c) * 1.01, 4),
                    "low": round(min(p, c) * 0.99, 4), "close": c})
        p = c
    return out


def _foto(fecha, h, spot=100.0, bear=94.0, base=101.0, bull=108.0, direction="up"):
    return {"date": fecha, "savedAt": f"{fecha}T21:00:00.000Z", "spot": spot,
            "horizonDays": h, "bear": bear, "base": base, "bull": bull,
            "direction": direction, "confidence": 70}


def casos():
    r = random.Random(3)
    out = []

    # Bien formados: diarios de varios tamaños y horizontes, con y sin madurar.
    for _ in range(45):
        n = r.choice([0, 1, 3, 12, 40])
        fotos = [_foto((date(2026, 4, 1) + timedelta(days=i * 3)).isoformat(),
                       r.choice([10, 20, 30]),
                       spot=round(r.uniform(50, 400), 2),
                       bear=round(r.uniform(40, 90), 2),
                       base=round(r.uniform(90, 120), 2),
                       bull=round(r.uniform(120, 200), 2),
                       direction=r.choice(["up", "down", "flat"]))
                 for i in range(n)]
        out.append({"snapshots": fotos,
                    "bars": _barras("2026-04-01", r.choice([0, 5, 60, 200]),
                                    semilla=r.randint(1, 99)),
                    "now": r.choice(["2026-04-15T18:00:00Z", "2026-07-31T18:00:00Z",
                                     "2027-01-15T18:00:00Z"])})

    # MALFORMADOS, campo a campo de la foto.
    base_f = _foto("2026-04-01", 20)
    bars = _barras("2026-04-01", 60)
    for campo, valores in (("spot", BASURA_NUM), ("bear", BASURA_NUM),
                           ("base", BASURA_NUM), ("bull", BASURA_NUM),
                           ("horizonDays", BASURA_NUM), ("date", BASURA_FECHA),
                           ("direction", [None, "", "UP", "arriba", 0, True])):
        for v in valores:
            f = dict(base_f)
            f[campo] = v
            out.append({"snapshots": [f], "bars": bars, "now": "2026-07-31T18:00:00Z"})
    # …y de las barras.
    for campo, valores in (("time", BASURA_FECHA), ("high", BASURA_NUM),
                           ("low", BASURA_NUM), ("close", BASURA_NUM)):
        for v in valores:
            b = dict(bars[10])
            b[campo] = v
            out.append({"snapshots": [base_f], "bars": [b] + bars[11:],
                        "now": "2026-07-31T18:00:00Z"})
    # Listas que no son listas de objetos.
    out.append({"snapshots": [], "bars": bars, "now": "2026-07-31T18:00:00Z"})
    out.append({"snapshots": [base_f], "bars": [], "now": "2026-07-31T18:00:00Z"})
    # Dos fotos del mismo día con horizontes distintos: el caso que Vertex
    # produce y su clave de dedupe (solo la fecha) no distingue.
    out.append({"snapshots": [_foto("2026-04-01", 10), _foto("2026-04-01", 30)],
                "bars": bars, "now": "2026-07-31T18:00:00Z"})
    return out


if __name__ == "__main__":
    print(json.dumps(casos()))
