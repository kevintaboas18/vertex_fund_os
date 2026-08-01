"""Casos del diferencial del MOTOR: validation.ts, levels.ts y structure.ts.

Deterministas (semilla fija), como los demás diferenciales: se comitea el
generador, no los 2,6 MB de datos.

Estos tres módulos se eligieron porque no importan a otros del repo de Víctor,
así que se pueden ejecutar tal cual con el quitado de tipos nativo de Node. Son
el sub-agente 6 (15 puntos), el sub-agente 4 (20 puntos) y los niveles que
alimentan la síntesis de precios y la gráfica.
"""
import json, random
from datetime import date, timedelta


def _barras(n, base, vol, inicio=date(2026, 1, 5)):
    d, out, c = inicio, [], base
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        c = max(0.5, c * (1 + random.gauss(0, vol)))
        r = abs(random.gauss(0, vol)) * c + 0.01
        out.append({"time": d.isoformat(), "high": round(c + r, 4),
                    "low": round(max(.01, c - r), 4), "close": round(c, 4)})
        d += timedelta(days=1)
    return out


def casos():
    random.seed(23)
    val = []
    for _ in range(120):
        n = random.choice([0, 1, 3, 20, 60, 120, 250])
        bs = _barras(n, random.uniform(3, 400), random.choice([.002, .01, .03, .08]))
        fl = []
        for i in range(random.choice([0, 1, 2, 10, 40]) if bs else 0):
            b = random.choice(bs)
            fl.append({"id": i + 1, "timestamp": b["time"] + "T15:30:00Z",
                       "type": random.choice(["call", "put", "unknown"]),
                       "strike": random.choice([None, round(b["close"] * random.uniform(.8, 1.2), 2)]),
                       "expiration": random.choice([None, "2026-09-18"]),
                       "assetPrice": b["close"], "premium": round(random.uniform(1e4, 5e6), 2),
                       "aggression": random.choice(["ask", "bid", "mid", "", "ASK"])})
        val.append({"flows": fl, "bars": bs,
                    "now": (date(2026, 1, 5) + timedelta(days=n + random.choice([0, 5, 40, 200]))).isoformat() + "T21:00:00Z",
                    "horizon": random.choice([5, 20, 40]),
                    "thresholdPct": random.choice([None, None, 1.0, 3.5])})
    val.append({"flows": [], "bars": [], "now": "2026-03-01T21:00:00Z", "horizon": 20, "thresholdPct": None})

    random.seed(41)
    lev = []
    for _ in range(100):
        n = random.choice([0, 1, 5, 40, 120, 250])
        bs = _barras(n, random.uniform(4, 600), random.choice([.003, .015, .05]))
        spot = bs[-1]["close"] if bs else random.choice([0, 100.0])
        K = lambda: round((spot or 100) * random.uniform(.7, 1.3), 2)
        lev.append({"bars": bs, "spot": spot,
            "now": (date(2026, 1, 5) + timedelta(days=n + random.choice([0, 3, 30]))).isoformat() + "T21:00:00Z",
            "chain": [{"strike": K(), "contractType": random.choice(["call", "put"]),
                       "openInterest": random.randint(0, 90000),
                       "notionalValue": random.uniform(1e4, 9e8)} for _ in range(random.choice([0, 3, 12]))],
            "flows": [{"strike": random.choice([None, K()]), "type": random.choice(["call", "put", "unknown"]),
                       "aggression": random.choice(["ask", "bid", "mid"]),
                       "premium": random.uniform(1e4, 5e6)} for _ in range(random.choice([0, 4, 15]))],
            "gex": [{"strike": K(), "netGex": random.uniform(-9e8, 9e8)} for _ in range(random.choice([0, 3, 8]))],
            "tolerancePct": random.choice([1, 0.5, 2]), "rangePct": random.choice([25, 10, 50])})

    random.seed(7)
    est = [[{"contractType": random.choice(["call", "put"]),
             "expiration": random.choice(["2026-09-18", "2026-12-18", "2027-01-15"]),
             "strike": round(random.uniform(5, 600), 2),
             "openInterest": random.randint(0, 120000), "volume": random.randint(0, 300000),
             "notionalValue": random.uniform(0, 9e9)} for _ in range(random.choice([0, 1, 8, 60, 200]))]
           for _ in range(120)]
    return {"validation": val, "levels": lev, "structure": est}


if __name__ == "__main__":
    print(json.dumps(casos()))
