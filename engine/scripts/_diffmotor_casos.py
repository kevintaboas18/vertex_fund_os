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

    # ── Corpus MALFORMADO ──────────────────────────────────────────────────
    #
    # Todo lo de arriba está bien formado: cada campo con su tipo. Eso mide la
    # lógica, no las semánticas del lenguaje — y las semánticas son justo donde
    # el port se separa de él sin avisar. En `compute.ts` el mismo salto destapó
    # cinco divergencias que ningún test bien formado tocaba.
    #
    # Estas formas no son inventadas: son las que MarketSnack y Massive mandan
    # de verdad cuando cambia su esquema (número en texto, campo ausente, enum
    # en mayúsculas) más las que produce un JSON a medio escribir en disco.
    #
    # `NaN` e `Infinity` viajan como texto porque JSON no los tiene: `Number()`
    # los interpreta, que es exactamente lo que hace su código al recibirlos.
    est += _estructura_basura()
    val += _validation_basura()
    lev += _levels_basura()
    lotes += _flow_basura(base)

    return {"validation": val, "levels": lev, "structure": est,
            "flow": {"now": NOW, "lotes": lotes}}


#: Valores que rompen un campo numérico. `""` y `[]` son 0 en JS, `"500"` es
#: 500, `"abc"` y `{}` son NaN, `true` es 1 — cinco reglas distintas.
_BASURA_NUM = [None, "500", "abc", "", "   ", [], {}, True, False,
               "NaN", "Infinity", "-Infinity", "1_000", "0x1A",
               1e308, -1e308, 1e-308, -0.0, 0.1 + 0.2]

#: Formas que rompen un campo de texto o un enum.
_BASURA_TXT = [None, "", "  ", "CALL", "Put", "PUT", 0, 5, True, [], {}]

#: Fechas: la que trae hora es la que de verdad manda Massive, y desde que
#: `compute` es literal ya no se recorta.
_BASURA_FECHA = [None, "", "2026-09-18", "2026-09-18T00:00:00Z",
                 "2026-09-18T21:00:00Z", "2026-09", "2026", "2026-13-45",
                 "20260918", "basura", 20260918]


def _mezcla(base_dict, campo, valor):
    """Copia el dict cambiando UN campo. Aísla la causa de cada diferencia."""
    d = dict(base_dict)
    if valor is _AUSENTE:
        d.pop(campo, None)
    else:
        d[campo] = valor
    return d


class _Ausente:
    """Marca 'quita la clave', que no es lo mismo que ponerla a `null`.

    En TS `x.campo` ausente es `undefined` y `x.campo = null` es `null`. El `??`
    trata a los dos igual pero `typeof` no, y `compute.ts` usa las dos reglas.
    """


_AUSENTE = _Ausente()


def _estructura_basura():
    """`structureScore` con filas de cadena rotas, campo a campo."""
    sana = {"contractType": "call", "expiration": "2026-09-18", "strike": 100.0,
            "openInterest": 9000, "volume": 4000, "notionalValue": 9e7}
    out = []
    for campo, valores in (("openInterest", _BASURA_NUM), ("volume", _BASURA_NUM),
                           ("strike", _BASURA_NUM), ("notionalValue", _BASURA_NUM),
                           ("contractType", _BASURA_TXT), ("expiration", _BASURA_FECHA)):
        for v in valores + [_AUSENTE]:
            # Una fila rota entre cuatro sanas: así se ve si contamina la suma
            # o si solo se cae ella, que es la diferencia que importa.
            out.append([_mezcla(sana, campo, v)]
                       + [dict(sana, strike=100.0 + i * 5) for i in range(1, 5)])
    # Filas que ni siquiera son objetos, y la cadena entera degenerada.
    out.append([None, "basura", 42, [], dict(sana)])
    out.append([])
    out.append([dict(sana, openInterest=0, volume=0, notionalValue=0)])
    out.append([dict(sana, strike=-100.0), dict(sana, openInterest=-9000)])
    return out


def _validation_basura():
    """`validationScore` con flows y barras rotos."""
    barras = _barras(80, 100.0, 0.02)
    flow = {"id": 1, "timestamp": "2026-02-02T15:30:00Z", "type": "call",
            "strike": 100.0, "expiration": "2026-09-18", "assetPrice": 100.0,
            "premium": 5e5, "aggression": "ask"}
    out = []
    for campo, valores in (("assetPrice", _BASURA_NUM), ("premium", _BASURA_NUM),
                           ("strike", _BASURA_NUM), ("type", _BASURA_TXT),
                           ("aggression", _BASURA_TXT), ("timestamp", _BASURA_FECHA),
                           ("expiration", _BASURA_FECHA)):
        for v in valores + [_AUSENTE]:
            out.append({"flows": [_mezcla(flow, campo, v), dict(flow, id=2)],
                        "bars": barras, "now": "2026-06-01T21:00:00Z",
                        "horizon": 20, "thresholdPct": None})
    # Barras rotas: son la otra mitad de la entrada.
    for mala in (None, "basura", 42, {}, {"time": "2026-02-03"},
                 {"time": "2026-02-03", "high": "abc", "low": None, "close": []},
                 {"time": None, "high": 1, "low": 1, "close": 1}):
        out.append({"flows": [dict(flow)], "bars": [mala] + barras[:30],
                    "now": "2026-06-01T21:00:00Z", "horizon": 20, "thresholdPct": None})
    # El umbral y el horizonte, que el llamador controla.
    for thr in (0, -1, "3", None, 1e308):
        out.append({"flows": [dict(flow)], "bars": barras,
                    "now": "2026-06-01T21:00:00Z", "horizon": 20, "thresholdPct": thr})
    for hz in (0, -5, 1, 10_000):
        out.append({"flows": [dict(flow)], "bars": barras,
                    "now": "2026-06-01T21:00:00Z", "horizon": hz, "thresholdPct": None})
    return out


def _levels_basura():
    """`findLevels` con cadena, flujo, GEX y barras rotos."""
    barras = _barras(120, 100.0, 0.02)
    ch = {"strike": 105.0, "contractType": "call", "openInterest": 40000,
          "notionalValue": 4e8}
    fl = {"strike": 95.0, "type": "put", "aggression": "bid", "premium": 2e6}
    gx = {"strike": 110.0, "netGex": -5e8}
    base = {"bars": barras, "spot": 100.0, "now": "2026-06-01T21:00:00Z",
            "chain": [dict(ch)], "flows": [dict(fl)], "gex": [dict(gx)],
            "tolerancePct": 1, "rangePct": 25}
    out = []
    for v in _BASURA_NUM + [_AUSENTE]:
        out.append(dict(base, chain=[_mezcla(ch, "openInterest", v), dict(ch, strike=115.0)]))
        out.append(dict(base, flows=[_mezcla(fl, "premium", v), dict(fl, strike=90.0)]))
        out.append(dict(base, gex=[_mezcla(gx, "netGex", v), dict(gx, strike=120.0)]))
    for v in _BASURA_TXT + [_AUSENTE]:
        out.append(dict(base, chain=[_mezcla(ch, "contractType", v)]))
        out.append(dict(base, flows=[_mezcla(fl, "aggression", v)]))
    # El spot manda: sin él no hay niveles, y un spot roto no puede inventarlos.
    for spot in (0, -100.0, "100", None, 1e308, 0.0001):
        out.append(dict(base, spot=spot))
    # Barras rotas y `now` roto — `recencyFactor` cuelga de las dos.
    for mala in (None, "basura", 42, {"time": "2026-02-03"},
                 {"time": "2026-02-03", "high": "abc", "low": None, "close": []}):
        out.append(dict(base, bars=[mala] + barras[:60]))
    for ahora in ("2026-06-01T21:00:00Z", "2026-06-01T22:00:00Z",
                  "2026-06-01T23:59:00Z", "2026-06-01T00:00:00Z"):
        # Las horas del borde: es donde `recencyFactor` cambia de banda.
        out.append(dict(base, now=ahora))
    return out


def _flow_basura(base_dt):
    """`classifyFlow` + los 3 sub-agentes con trades rotos."""
    sano = {"id": 1, "symbol": "DEMO270115C00100000", "price": 2.0, "size": 800,
            "side": "AT_ASK", "bid_price": 1.9, "ask_price": 2.1,
            "premium": 160_000.0, "delta": 0.62, "gamma": 0.03, "theta": -0.04,
            "vega": 0.3, "implied_volatility": 0.44, "open_interest": 4000,
            "volume": 5200, "score": 8, "sentiment": "bullish",
            "timestamp": "2026-07-31T17:30:00Z", "asset_price": 100.0,
            "trade_condition_id": 231}
    out = []
    for campo, valores in (
        ("price", _BASURA_NUM), ("size", _BASURA_NUM), ("premium", _BASURA_NUM),
        ("bid_price", _BASURA_NUM), ("ask_price", _BASURA_NUM),
        ("delta", _BASURA_NUM), ("gamma", _BASURA_NUM), ("theta", _BASURA_NUM),
        ("vega", _BASURA_NUM), ("implied_volatility", _BASURA_NUM),
        ("open_interest", _BASURA_NUM), ("volume", _BASURA_NUM),
        ("asset_price", _BASURA_NUM), ("score", _BASURA_NUM),
        ("id", _BASURA_NUM), ("trade_condition_id", _BASURA_NUM),
        ("side", _BASURA_TXT), ("sentiment", _BASURA_TXT),
        ("symbol", _BASURA_TXT + ["DEMO", "DEMO270115X00100000",
                                  "DEMO27011XC00100000", "270115C00100000"]),
        ("timestamp", _BASURA_FECHA + ["2026-07-31T17:30:00",
                                       "2026-07-31T17:30:00.250Z",
                                       "2026-07-31T12:30:00-05:00"]),
    ):
        for v in valores + [_AUSENTE]:
            # Tres filas: la rota y dos sanas del MISMO contrato dentro de la
            # ventana de 5 min, para que `markRepeated` y `detectClusters`
            # tengan grupo que formar.
            out.append([_mezcla(sano, campo, v),
                        dict(sano, id=2, timestamp="2026-07-31T17:31:00Z"),
                        dict(sano, id=3, timestamp="2026-07-31T17:32:00Z")])
    # Lotes que no son listas de objetos.
    out.append([None, "basura", 42, [], dict(sano)])
    out.append([dict(sano), dict(sano)])                    # ids duplicados
    out.append([dict(sano, id=0), dict(sano, id=0), dict(sano, id=0)])
    # Todos con el MISMO timestamp: dispara `markSimultaneous`.
    out.append([dict(sano, id=i, symbol=f"DEMO270115C0010{i}000") for i in range(1, 6)])
    # Todos con timestamp ILEGIBLE: él no los filtra en `markRepeated`.
    out.append([dict(sano, id=i, timestamp="ayer") for i in range(1, 5)])
    return out


if __name__ == "__main__":
    print(json.dumps(casos()))
