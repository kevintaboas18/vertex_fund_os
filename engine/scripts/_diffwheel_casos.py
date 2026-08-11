"""Casos del diferencial de la WHEEL: wheel.ts, wheelAfford.ts, earnings.ts.

Los cuatro módulos de la Wheel estaban portados y **ningún diferencial los
comparaba contra su TypeScript**. Es una estrategia entera del tab —la que dice
qué put vender, con cuánto colateral y con qué probabilidad de expirar sin
valor— midiéndose solo contra sus propios tests.

Lo que cuelga de aquí no es cosmético: `wheelMetrics` decide el crédito y el
colateral (o sea si la operación te CABE en la cuenta), `scoreCandidate` reparte
los 100 puntos que ordenan la tabla, `liquidityBlock` decide qué se esconde por
ilíquido, y `earningsFlag` decide si el vencimiento se come un reporte.

Deterministas (semilla fija). El corpus incluye entradas malformadas a
propósito: la cadena viene de Massive y un campo que cambie de tipo no puede
puntuar distinto en un lado que en el otro.
"""
import json
import random


def _presets():
    """Sus TRES presets, por id. El diferencial los recorre todos porque cada
    uno mueve el rango de delta y de DTE, que es lo que elige los strikes."""
    return ["conservador", "balanceado", "agresivo"]


def _quotes(rng, n, spot):
    """Filas de cadena como las normaliza su `ChainQuote`."""
    out = []
    for _ in range(n):
        strike = round(spot * rng.uniform(0.5, 1.25), 2)
        bid = rng.choice([None, 0, round(rng.uniform(0.01, 12), 2)])
        ask = None if bid is None else (
            None if rng.random() < 0.1 else round((bid or 0.05) * rng.uniform(1.0, 2.2), 2))
        out.append({
            "strike": strike,
            "expiration": rng.choice(["2026-08-21", "2026-09-18", "2026-10-16", "2026-12-18"]),
            "dte": rng.choice([0, 1, 5, 14, 21, 30, 45, 60, 120]),
            "bid": bid,
            "ask": ask,
            "lastTrade": rng.choice([None, round(rng.uniform(0.01, 12), 2)]),
            "openInterest": rng.choice([0, 1, 50, 99, 100, 101, 5000, 40000]),
        })
    return out


def _supports(rng, spot, n):
    """`Level[]` de su `levels.ts`: sólo `price` y `strength` importan aquí."""
    return [{"price": round(spot * rng.uniform(0.6, 1.05), 2),
             "kind": "support",
             "strength": round(rng.uniform(0, 100), 2),
             "distancePct": round(rng.uniform(-30, 5), 4),
             "flipped": rng.random() < 0.2,
             "why": "",
             "sources": {"touches": rng.randrange(0, 9), "lastTouch": None,
                         "openInterest": rng.randrange(0, 9000),
                         "notional": rng.uniform(0, 9e6),
                         "flowPremium": rng.uniform(0, 9e6),
                         "netGex": rng.uniform(-9e6, 9e6)}}
            for _ in range(n)]


def casos():
    salida = {"metrics": [], "premium": [], "spread": [], "liquidity": [],
              "score": [], "atmIv": [], "candidates": [], "afford": [],
              "earnings": [], "earningsFlag": []}

    # ── wheelMetrics: crédito, colateral, anualizado, breakeven, cojín, prob ──
    # Las fronteras son las que rompen: dte 0 (su `Math.max(dte,1)`), colateral
    # 0 (división), spot 0 (el cojín) e IV 0 (la probabilidad).
    rng = random.Random(11)
    fijos = [
        {"strike": 20, "price": 0.5, "spot": 21, "dte": 30, "iv": 0.6},
        {"strike": 20, "price": 0.5, "spot": 21, "dte": 0, "iv": 0.6},
        {"strike": 0, "price": 0.5, "spot": 21, "dte": 30, "iv": 0.6},
        {"strike": 20, "price": 0, "spot": 0, "dte": 30, "iv": 0.6},
        {"strike": 20, "price": 0.5, "spot": 21, "dte": 30, "iv": 0},
        {"strike": 20, "price": 0.5, "spot": 21, "dte": 1, "iv": 3.5},
        {"strike": 500, "price": 12.5, "spot": 480, "dte": 45, "iv": 0.28},
    ]
    for f in fijos:
        salida["metrics"].append(f)
    for _ in range(160):
        spot = round(rng.uniform(3, 600), 2)
        salida["metrics"].append({
            "strike": round(spot * rng.uniform(0.5, 1.2), 2),
            "price": round(rng.uniform(0, 25), 4),
            "spot": spot,
            "dte": rng.choice([0, 1, 3, 7, 21, 45, 120, 365]),
            "iv": round(rng.uniform(0, 4), 4),
        })

    # ── pickPremium + HAIRCUT: de qué fuente sale la prima y cuánto se recorta ──
    rng = random.Random(12)
    salida["premium"] = [
        {"bid": None, "ask": None, "lastTrade": None, "model": None},
        {"bid": 0, "ask": 1, "lastTrade": 0.5, "model": 0.4},
        {"bid": 1.0, "ask": 1.2, "lastTrade": None, "model": None},
        {"bid": None, "ask": 1.2, "lastTrade": 0.9, "model": None},
        {"bid": None, "ask": None, "lastTrade": None, "model": 0.77},
        {"bid": -1, "ask": 1.2, "lastTrade": 0.9, "model": 0.5},
    ] + [{"bid": rng.choice([None, 0, round(rng.uniform(0.01, 9), 2)]),
          "ask": rng.choice([None, 0, round(rng.uniform(0.01, 11), 2)]),
          "lastTrade": rng.choice([None, 0, round(rng.uniform(0.01, 9), 2)]),
          "model": rng.choice([None, 0, round(rng.uniform(0.01, 9), 2)])}
         for _ in range(120)]

    # ── spreadPctOf + liquidityBlock: qué se esconde por ilíquido ──
    # MAX_SPREAD_PCT=25 y MIN_OI=100 son sus dos umbrales; el corpus los cruza
    # por los dos lados, incluido el empate exacto.
    rng = random.Random(13)
    salida["spread"] = [
        {"bid": None, "ask": None}, {"bid": 0, "ask": 0}, {"bid": 1, "ask": 1},
        {"bid": 1, "ask": 1.25}, {"bid": 1, "ask": 1.5}, {"bid": 0, "ask": 2},
        {"bid": 4, "ask": 4.4}, {"bid": -1, "ask": 1},
    ] + [{"bid": rng.choice([None, 0, round(rng.uniform(0.01, 9), 2)]),
          "ask": rng.choice([None, 0, round(rng.uniform(0.01, 11), 2)])}
         for _ in range(90)]
    salida["liquidity"] = [
        {"bid": 1.0, "ask": 1.1, "openInterest": 100},
        {"bid": 1.0, "ask": 1.1, "openInterest": 99},
        {"bid": 1.0, "ask": 1.5, "openInterest": 5000},   # spread exacto 40%
        {"bid": 1.0, "ask": 1.25, "openInterest": 5000},  # spread exacto 22,2%
        {"bid": None, "ask": 1.1, "openInterest": 5000},
        {"bid": 0, "ask": 1.1, "openInterest": 5000},
    ] + [{"bid": rng.choice([None, 0, round(rng.uniform(0.01, 9), 2)]),
          "ask": rng.choice([None, 0, round(rng.uniform(0.01, 11), 2)]),
          "openInterest": rng.choice([0, 1, 99, 100, 101, 5000, 40000])}
         for _ in range(110)]

    # ── scoreCandidate: los 100 puntos que ordenan la tabla ──
    rng = random.Random(14)
    for _ in range(200):
        spot = round(rng.uniform(5, 400), 2)
        salida["score"].append({
            "annualizedPct": round(rng.uniform(-20, 300), 3),
            "ivRank": rng.choice([None, 0, 15, 30, 49.99, 50, 70, 100]),
            "spot": spot,
            "strike": round(spot * rng.uniform(0.5, 1.2), 2),
            "breakeven": round(spot * rng.uniform(0.4, 1.1), 2),
            "cushionPct": round(rng.uniform(-30, 60), 3),
            "supports": _supports(rng, spot, rng.choice([0, 1, 3, 8])),
            "openInterest": rng.choice([0, 99, 100, 500, 9000]),
            "spreadPct": rng.choice([None, 0, 5, 24.99, 25, 40]),
            "earnings": rng.choice(["fuera", "dentro", "dentro_confirmado", "no_aplica"]),
        })

    # ── atmIv ──
    rng = random.Random(15)
    salida["atmIv"] = [{"rows": [], "spot": 20.0}] + [
        {"rows": [{"strike": round(rng.uniform(1, 200), 2), "iv": round(rng.uniform(0, 3), 4)}
                  for _ in range(rng.choice([1, 2, 9, 40]))],
         "spot": round(rng.uniform(1, 200), 2)} for _ in range(60)]

    # ── wheelCandidates: el ensamblado entero, preset a preset ──
    rng = random.Random(16)
    for i in range(70):
        spot = round(rng.uniform(4, 300), 2)
        salida["candidates"].append({
            "ticker": f"W{i:03d}",
            "spot": rng.choice([0, -1, spot, spot, spot]),
            "quotes": _quotes(rng, rng.choice([0, 1, 8, 40, 120]), spot or 20),
            "preset": rng.choice(_presets()),
            "ivRank": rng.choice([None, 0, 35, 60, 100]),
            "supports": _supports(rng, spot or 20, rng.choice([0, 2, 6])),
            "earnings": rng.choice(["fuera", "dentro", "dentro_confirmado", "no_aplica"]),
            "fallbackIv": round(rng.uniform(0.05, 2.5), 4),
        })

    # ── affordOf + sortByAffordThenScore: si te CABE con tu saldo ──
    rng = random.Random(17)
    for _ in range(90):
        n = rng.choice([0, 1, 5, 20])
        filas = [{"strike": round(rng.uniform(2, 300), 2),
                  "score": rng.choice([None, {"total": rng.randrange(0, 101)}]),
                  "blocked": rng.random() < 0.25}
                 for _ in range(n)]
        salida["afford"].append({"rows": filas,
                                 "cash": rng.choice([0, 100, 1000, 2500, 50_000])})

    # ── estimateNextEarnings + earningsFlag ──
    rng = random.Random(18)
    salida["earnings"] = [
        {"filings": [], "now": "2026-08-07T15:00:00Z"},
        {"filings": ["2026-05-01"], "now": "2026-08-07T15:00:00Z"},
        {"filings": ["2025-08-01", "2025-11-01", "2026-02-01", "2026-05-01"],
         "now": "2026-08-07T15:00:00Z"},
        {"filings": ["no-es-fecha", "2026-05-01"], "now": "2026-08-07T15:00:00Z"},
    ]
    for _ in range(70):
        base = rng.randrange(1, 13)
        fs = [f"2025-{((base + k * 3 - 1) % 12) + 1:02d}-{rng.randrange(1, 28):02d}"
              for k in range(rng.choice([0, 1, 2, 4, 8]))]
        salida["earnings"].append({"filings": fs, "now": "2026-08-07T15:00:00Z"})
    # Su tercer parámetro es el SKEW DEL FRENTE en puntos, no un booleano: el
    # mercado "confirma" el reporte pagando de más por el vencimiento cercano.
    # El umbral es `> 10`, y el corpus lo cruza por los dos lados y en el empate.
    salida["earningsFlag"] = [
        {"next": None, "expiration": "2026-09-18", "frontSkew": None},
        {"next": "2026-09-01", "expiration": "2026-09-18", "frontSkew": None},
        {"next": "2026-09-01", "expiration": "2026-09-18", "frontSkew": 10},
        {"next": "2026-09-01", "expiration": "2026-09-18", "frontSkew": 10.0001},
        {"next": "2026-09-18", "expiration": "2026-09-18", "frontSkew": 0},
        {"next": "2026-09-19", "expiration": "2026-09-18", "frontSkew": 40},
        {"next": "2026-08-01", "expiration": "2026-09-18", "frontSkew": -5},
        # Fechas ilegibles: su `getTime()` da NaN y toda comparación con NaN es
        # falsa, así que cae en "dentro". Aquí se mide, no se supone.
        {"next": "no-es-fecha", "expiration": "2026-09-18", "frontSkew": 0},
        {"next": "2026-09-01", "expiration": "tampoco", "frontSkew": 0},
    ]
    for _ in range(60):
        salida["earningsFlag"].append({
            "next": rng.choice([None, f"2026-{rng.randrange(1,13):02d}-{rng.randrange(1,29):02d}"]),
            "expiration": f"2026-{rng.randrange(1,13):02d}-{rng.randrange(1,29):02d}",
            "frontSkew": rng.choice([None, -20, 0, 9.99, 10, 10.01, 35])})

    return salida


if __name__ == "__main__":
    print(json.dumps(casos()))
