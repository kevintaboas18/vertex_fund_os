"""Casos del diferencial del MOTOR: validation.ts, levels.ts y structure.ts.

Deterministas (semilla fija), como los demás diferenciales: se comitea el
generador, no los 2,6 MB de datos.

Cubre los seis sub-agentes que producen el score:

    flow.ts        agresividad + convicción + inusualidad   55 pts
    structure.ts   estructura de la cadena                  20 pts
    validation.ts  confirmación de precio                   15 pts
    levels.ts      niveles por confluencia (gráfica y síntesis de precios)

`flow.ts` importa `conditions` y `occ`, que también se bajan; los demás no
importan nada, así que se ejecutan tal cual.
"""
import json, random
from datetime import date, datetime, timedelta, timezone


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
    random.seed(97)
    NOW = "2026-07-31T18:00:00Z"
    SIDES = ["AT_ASK", "ABOVE_ASK", "ASKSIDE", "AT_BID", "BELOW_BID", "BIDSIDE",
             "MID", "NO_SIDE", "", "at_ask"]
    COND = [None, 0, 100, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209, 210,
            211, 212, 213, 214, 215, 219, 227, 231, 232, 233]
    base = datetime(2026, 7, 31, 18, 0, tzinfo=timezone.utc)

    def _trade(i, minutos, strike=None, cp=None):
        cp = cp or random.choice("CP")
        strike = strike if strike is not None else random.choice([90, 95, 100, 105, 110, 150, 205])
        exp = random.choice(["270115", "260918", "260807", "261218"])
        bid = round(random.uniform(.05, 40), 2)
        ask = round(bid + random.uniform(0, 6), 2)
        px = round(random.choice([bid, ask, (bid + ask) / 2, ask + .5,
                                  max(.01, bid - .3), random.uniform(bid, ask)]), 2)
        size = random.choice([1, 5, 50, 300, 800, 5000])
        return {"id": i, "symbol": f"DEMO{exp}{cp}{int(strike * 1000):08d}",
                "price": px, "size": size, "side": random.choice(SIDES),
                "bid_price": bid, "ask_price": ask, "premium": round(px * size * 100, 2),
                "delta": round(random.uniform(-1, 1), 4),
                "gamma": round(random.uniform(0, .2), 5),
                "theta": round(random.uniform(-2, 0), 4),
                "vega": round(random.uniform(0, 2), 4),
                "implied_volatility": round(random.uniform(.05, 3), 4),
                "open_interest": random.randint(0, 90000),
                "volume": random.randint(0, 300000), "score": random.randint(0, 10),
                "sentiment": random.choice(["bullish", "bearish", "neutral", ""]),
                "timestamp": (base - timedelta(minutes=minutos)).isoformat().replace("+00:00", "Z"),
                "asset_price": round(random.uniform(50, 300), 2),
                "trade_condition_id": random.choice(COND)}

    lotes = []
    for _ in range(90):
        lote = []
        for i in range(random.choice([0, 1, 2, 5, 20, 60, 150])):
            # 35% de racimo: mismo strike y tipo, dentro de la ventana de 5 min.
            if random.random() < .35 and lote:
                b = lote[0]
                lote.append(_trade(i + 1, random.randint(0, 4),
                                   strike=int(b["symbol"][-8:]) / 1000, cp=b["symbol"][-9]))
            else:
                lote.append(_trade(i + 1, random.randint(0, 60 * 24 * 10)))
        lotes.append(lote)

    return {"validation": val, "levels": lev, "structure": est,
            "flow": {"now": NOW, "lotes": lotes}}


if __name__ == "__main__":
    print(json.dumps(casos()))
