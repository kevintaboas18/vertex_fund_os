"""Casos para los CUATRO módulos que faltaban por comparar contra su archivo.

    ivcontext.ts   sub-agente 5 — IV Rank, régimen, front skew      10 pts
    gex.ts         nodo imán, zona de flip, régimen gamma
    prediction.ts  Prediction Pro — 3 escenarios + calibración
    risk.ts        techo de contratos = min(prima, quema de theta)

`diff_motor.sh` ya cubre los otros cuatro (flow, structure, validation, levels)
y su corpus malformado destapó 104 casos donde el port lanzaba y su archivo no.
Estos cuatro estaban solo probados con datos BIEN FORMADOS, que es exactamente
el hueco que allí resultó caro.

Mismo criterio que allí: primero una tanda determinista bien formada (semilla
fija), y luego el corpus MALFORMADO — número en texto, campo ausente, enum en
mayúsculas, fila que no es objeto, 1e308, `NaN`, fecha con hora. Son las formas
que manda una fuente cuando cambia su esquema, no invenciones.
"""
import json
import random
from datetime import date, datetime, timedelta, timezone

# Las mismas listas de basura que el otro corpus, para que un hallazgo en un
# módulo se busque igual en los demás.
BASURA_NUM = [None, "500", "abc", "", "   ", [], {}, True, False,
              "NaN", "Infinity", "-Infinity", "1_000", "0x1A",
              1e308, -1e308, 1e-308, -0.0, 0.1 + 0.2]
BASURA_TXT = [None, "", "  ", "CALL", "Put", "PUT", 0, 5, True, [], {}]
BASURA_FECHA = [None, "", "2026-09-18", "2026-09-18T00:00:00Z", "2026-09",
                "2026", "2026-13-45", "20260918", "basura", 20260918]


class _Ausente:
    """Quita la clave. `undefined` no es `null` para `typeof`."""


AUSENTE = _Ausente()


def mezcla(base, campo, valor):
    d = dict(base)
    if valor is AUSENTE:
        d.pop(campo, None)
    else:
        d[campo] = valor
    return d


def _fila_flow(i=1, **kw):
    """Un `FlowRow` ya clasificado, que es lo que consumen ivcontext y risk."""
    d = {
        "id": i, "symbol": "DEMO270115C00100000", "underlying": "DEMO",
        "type": "call", "strike": 100.0, "expiration": "2027-01-15", "dte": 200,
        "price": 9.2, "size": 800, "side": "AT_ASK", "aggression": "ask",
        "assetPrice": 100.0, "bid": 9.14, "ask": 9.26, "premium": 736_000.0,
        "delta": 0.62, "gamma": 0.03, "theta": -0.04, "vega": 0.3,
        "thetaPctDaily": 0.43, "iv": 0.44, "openInterest": 4000, "volume": 5200,
        "score": 8, "sentiment": "bullish",
        "timestamp": "2026-07-31T15:30:00Z",
        "conditionCode": None, "conditionName": None,
        "flags": {"big": False, "convDelta": True, "aboveAsk": True,
                  "belowBid": False, "mid": False, "leap": True, "repeated": False,
                  "multileg": False, "simultaneous": False, "exceededOI": True},
        "scores": {"volume": 10, "timing": 10, "repetition": 0, "total": 20},
        "unusual": True, "interesting": True, "expiryStatus": "vigente",
    }
    d.update(kw)
    return d


def _cierres(n=260, base=100.0, vol=0.02, semilla=11):
    r = random.Random(semilla)
    out, c = [], base
    for _ in range(n):
        c = max(0.5, c * (1 + r.gauss(0, vol)))
        out.append(round(c, 4))
    return out


def _fila_cadena(**kw):
    d = {"contractType": "call", "expiration": "2026-09-18", "strike": 100.0,
         "openInterest": 9000, "volume": 4000, "notionalValue": 9e7}
    d.update(kw)
    return d


# ───────────────────────────── ivcontext ──────────────────────────────────


def casos_ivcontext():
    out = []
    r = random.Random(5)
    # Bien formados: la mezcla que produce el tape real.
    for _ in range(40):
        n = r.choice([0, 1, 3, 12, 60])
        filas = [_fila_flow(i + 1, iv=round(r.uniform(0.05, 3.0), 4),
                            dte=r.choice([3, 20, 45, 200, 400]),
                            premium=round(r.uniform(1e4, 5e6), 2))
                 for i in range(n)]
        hist = [{"date": (date(2026, 1, 5) + timedelta(days=d)).isoformat(),
                 "avgIv": round(r.uniform(10, 180), 3)}
                for d in range(r.choice([0, 5, 59, 60, 120, 365]))]
        out.append({"rows": filas, "closes": _cierres(r.choice([0, 5, 31, 260]),
                                                      semilla=r.randint(1, 99)),
                    "ivHistory": hist})

    # MALFORMADOS, campo a campo.
    base = _fila_flow()
    for campo, valores in (("iv", BASURA_NUM), ("dte", BASURA_NUM),
                           ("premium", BASURA_NUM), ("type", BASURA_TXT)):
        for v in valores + [AUSENTE]:
            out.append({"rows": [mezcla(base, campo, v), _fila_flow(2)],
                        "closes": _cierres(260), "ivHistory": []})
    # Filas que no son objetos, y cierres rotos.
    out.append({"rows": [None, "basura", 42, [], _fila_flow()],
                "closes": _cierres(260), "ivHistory": []})
    for cierres in ([], [None, 1, 2], ["100", 101, 102], [1e308] * 40,
                    [0] * 40, [-100] * 40, [float("nan")] * 40 if False else ["NaN"] * 40):
        out.append({"rows": [_fila_flow()], "closes": cierres, "ivHistory": []})
    # Historial de IV roto: es lo que decide si el rank es real o proxy.
    for hist in ([], [{"date": "x", "avgIv": None}] * 70,
                 [{"avgIv": 50}] * 70, [{"date": "2026-01-01"}] * 70,
                 [None] * 70, ["basura"] * 70,
                 [{"date": "2026-01-01", "avgIv": "50"}] * 70):
        out.append({"rows": [_fila_flow()], "closes": _cierres(260), "ivHistory": hist})
    return out


# ─────────────────────────────── gex ──────────────────────────────────────


def casos_gex():
    out = []
    r = random.Random(13)
    NOW = "2026-07-31T18:00:00Z"
    for _ in range(40):
        filas = [_fila_cadena(contractType=ct, strike=float(s),
                              expiration=r.choice(["2026-08-21", "2026-09-18", "2027-01-15"]),
                              openInterest=r.randint(0, 90_000),
                              volume=r.randint(0, 300_000),
                              notionalValue=r.uniform(0, 9e8))
                 for s in range(80, 125, 5) for ct in ("call", "put")]
        out.append({"rows": filas if r.random() > .1 else [],
                    "closes": _cierres(r.choice([0, 5, 31, 260]), semilla=r.randint(1, 99)),
                    "spot": r.choice([0, 100.0, 5.0, 850.0]),
                    "trades": [{"strike": 100.0, "type": "call",
                                "gamma": 0.03, "premium": 5e5}] if r.random() > .5 else [],
                    "convictionScore": r.choice([None, 0, 5, 10]),
                    "structureScore": r.choice([None, 0, 5, 10]),
                    "lowLiquidity": r.choice([True, False]),
                    "now": NOW})

    base_row = _fila_cadena()
    sano = {"rows": [_fila_cadena(strike=float(s)) for s in range(90, 115, 5)],
            "closes": _cierres(260), "spot": 100.0, "trades": [],
            "convictionScore": 5, "structureScore": 5, "lowLiquidity": False,
            "now": NOW}
    for campo, valores in (("openInterest", BASURA_NUM), ("strike", BASURA_NUM),
                           ("notionalValue", BASURA_NUM), ("volume", BASURA_NUM),
                           ("contractType", BASURA_TXT), ("expiration", BASURA_FECHA)):
        for v in valores + [AUSENTE]:
            out.append(dict(sano, rows=[mezcla(base_row, campo, v)]
                            + [_fila_cadena(strike=float(s)) for s in (95, 100, 105)]))
    for v in BASURA_NUM + [AUSENTE]:
        out.append(dict(sano, spot=v if v is not AUSENTE else None))
    for v in (None, [], [None], ["basura"], [{}], [{"strike": "abc"}],
              [{"strike": 100.0, "gamma": "abc", "premium": None,
                "type": "call"}]):
        out.append(dict(sano, trades=v))
    for v in ([], [None] * 40, ["100"] * 40, [1e308] * 40, [0] * 40):
        out.append(dict(sano, closes=v))
    out.append(dict(sano, rows=[None, "basura", 42, _fila_cadena()]))
    return out


# ──────────────────────────── prediction ──────────────────────────────────


def casos_prediction():
    out = []
    r = random.Random(29)
    for _ in range(60):
        nodos = [{"strike": round(100 * r.uniform(.7, 1.3), 2),
                  "concentration": round(r.uniform(0, 1), 4),
                  "side": r.choice(["call", "put"]),
                  "netGex": r.uniform(-9e8, 9e8)}
                 for _ in range(r.choice([0, 1, 3, 12]))]
        out.append({
            "spot": r.choice([0, 5.0, 100.0, 850.0]),
            "iv": r.choice([0, 0.001, 0.01, 0.45, 2.0]),
            "horizonDays": r.choice([0, 1, 10, 20, 30, 120]),
            "nodes": nodos,
            "scores": {k: r.choice([None, 0, 5, 10]) for k in
                       ("aggression", "conviction", "unusuality", "structure",
                        "ivContext", "validation")},
            "regime": r.choice(["positive", "negative"]),
            "callPct": r.choice([None, 0, 50, 100]),
            "hitRate": r.choice([None, 0, 45, 60, 100]),
            "lowLiquidity": r.choice([True, False]),
            "calibration": r.choice([None, {"biasPct": None, "samples": 0},
                                     {"biasPct": 12.5, "samples": 9},
                                     {"biasPct": -30.0, "samples": 5}]),
        })

    sano = {"spot": 100.0, "iv": 0.45, "horizonDays": 20,
            "nodes": [{"strike": 105.0, "concentration": 0.4, "side": "call",
                       "netGex": -3e8}],
            "scores": {"aggression": 8, "conviction": 7, "unusuality": 6,
                       "structure": 5, "ivContext": 5, "validation": 6},
            "regime": "negative", "callPct": 70, "hitRate": 55,
            "lowLiquidity": False, "calibration": None}
    for campo in ("spot", "iv", "horizonDays", "callPct", "hitRate"):
        for v in BASURA_NUM + [AUSENTE]:
            out.append(mezcla(sano, campo, v))
    for campo, valores in (("regime", BASURA_TXT), ("lowLiquidity", BASURA_TXT)):
        for v in valores + [AUSENTE]:
            out.append(mezcla(sano, campo, v))
    nodo = sano["nodes"][0]
    for campo, valores in (("strike", BASURA_NUM), ("concentration", BASURA_NUM),
                           ("netGex", BASURA_NUM), ("side", BASURA_TXT)):
        for v in valores + [AUSENTE]:
            out.append(dict(sano, nodes=[mezcla(nodo, campo, v), dict(nodo, strike=95.0)]))
    for v in (None, [], [None], ["basura"], [42]):
        out.append(dict(sano, nodes=v))
    for v in ({}, {"aggression": "8"}, {"aggression": None}, None,
              {k: "abc" for k in sano["scores"]}, {k: 1e308 for k in sano["scores"]}):
        out.append(dict(sano, scores=v))
    for v in ({"biasPct": "12", "samples": 9}, {"biasPct": 12}, {"samples": 9},
              {"biasPct": 1e308, "samples": 1e308}, {}, None, "basura"):
        out.append(dict(sano, calibration=v))

    # ── El EMPATE al elegir bull y bear ────────────────────────────────────
    #
    # `predictPro` elige el objetivo alcista con
    # `others.filter(l => l.strike > spot).sort((a,b) => b.magnet - a.magnet)[0]`
    # — ordenar y quedarse con el primero. El port usa `max(..., key=magnet)`.
    # Con imanes DISTINTOS las dos dan lo mismo; con imanes IGUALES cada una
    # depende de una garantía de su lenguaje (el orden estable de `sort` en JS,
    # el «primer máximo» de `max` en Python), y eso no es algo que se deba dar
    # por bueno razonándolo: se mide.
    #
    # El empate no es rebuscado, es la puerta de al lado. `levelProbabilities`
    # normaliza con `total > 0 ? l.magnet / total : 0`, así que en cuanto la
    # probabilidad de toque es cero para todos —iv 0, horizonte 0, spot lejos—
    # **todos los imanes valen exactamente 0** y el desempate decide solo el
    # objetivo. Las filas de arriba lo rozan por azar; estas lo buscan.
    empate = dict(sano, calibration=None)
    for iv, dias in ((0, 20), (0.45, 0), (0, 0), (1e-9, 1)):
        out.append(dict(empate, iv=iv, horizonDays=dias, nodes=[
            {"strike": 104.0, "concentration": 0.4, "side": "call", "netGex": 1e8},
            {"strike": 108.0, "concentration": 0.4, "side": "put", "netGex": -1e8},
            {"strike": 96.0, "concentration": 0.4, "side": "put", "netGex": -2e8},
            {"strike": 92.0, "concentration": 0.4, "side": "call", "netGex": 3e8},
        ]))
    # Mismo strike repetido: el filtro `l.strike !== rawBase` los quita a los
    # DOS, y con ellos puede desaparecer el único candidato de ese lado.
    out.append(dict(empate, nodes=[
        {"strike": 105.0, "concentration": 0.9, "side": "call", "netGex": 5e8},
        {"strike": 105.0, "concentration": 0.9, "side": "put", "netGex": -5e8},
        {"strike": 95.0, "concentration": 0.3, "side": "put", "netGex": -1e8},
    ]))
    # Un nodo EXACTAMENTE en el spot: no es `> spot` ni `< spot`, cae fuera de
    # los dos lados aunque sea el imán.
    out.append(dict(empate, nodes=[
        {"strike": 100.0, "concentration": 1.0, "side": "call", "netGex": 9e8},
        {"strike": 106.0, "concentration": 0.2, "side": "call", "netGex": 1e8},
    ]))
    # `NaN` COMPITIENDO. El corpus de arriba ya mete `NaN` en un nodo, pero
    # siendo el único de su lado: nadie compara nada. Aquí hay tres arriba y el
    # comparador tiene que ordenar contra un `NaN`, que en JS devuelve `NaN`
    # (tratado como 0, orden indefinido) y en Python hace que `max` dependa del
    # orden de llegada. Es el caso donde los dos lenguajes se pueden separar.
    for basura in ("NaN", "Infinity", "-Infinity", None, "abc"):
        out.append(dict(empate, nodes=[
            {"strike": 103.0, "concentration": basura, "side": "call", "netGex": 1e8},
            {"strike": 107.0, "concentration": 0.5, "side": "call", "netGex": 2e8},
            {"strike": 111.0, "concentration": 0.5, "side": "put", "netGex": -1e8},
            {"strike": 94.0, "concentration": basura, "side": "put", "netGex": -3e8},
            {"strike": 90.0, "concentration": 0.5, "side": "put", "netGex": -2e8},
        ]))
    return out


# ─────────────────────────────── risk ─────────────────────────────────────


def casos_risk():
    out = []
    r = random.Random(37)
    for _ in range(40):
        out.append({
            "row": _fila_flow(1, price=round(r.uniform(.05, 40), 2),
                              dte=r.choice([0, 3, 7, 20, 200]),
                              thetaPctDaily=round(r.uniform(0, 12), 3),
                              premium=round(r.uniform(1e4, 5e6), 2),
                              scores={"volume": r.randint(0, 10),
                                      "timing": r.randint(0, 10),
                                      "repetition": r.randint(0, 10),
                                      "total": r.randint(0, 30)}),
            "profile": {"accountSize": r.choice([0, 1000, 25_000]),
                        "tolerancePct": r.choice([0, 1, 5, 100])},
            "horizonDays": r.choice([0, 10, 20, 30]),
            "ctx": {"lowLiquidity": r.choice([True, False])},
        })

    sano = {"row": _fila_flow(), "profile": {"accountSize": 1000, "tolerancePct": 5},
            "horizonDays": 20, "ctx": {}}
    fila = sano["row"]
    for campo, valores in (("price", BASURA_NUM), ("dte", BASURA_NUM),
                           ("thetaPctDaily", BASURA_NUM), ("premium", BASURA_NUM),
                           ("delta", BASURA_NUM), ("gamma", BASURA_NUM),
                           ("iv", BASURA_NUM), ("type", BASURA_TXT)):
        for v in valores + [AUSENTE]:
            out.append(dict(sano, row=mezcla(fila, campo, v)))
    for campo in ("accountSize", "tolerancePct"):
        for v in BASURA_NUM + [AUSENTE]:
            out.append(dict(sano, profile=mezcla(sano["profile"], campo, v)))
    for v in BASURA_NUM + [AUSENTE]:
        out.append(mezcla(sano, "horizonDays", v))
    for v in (None, {}, {"lowLiquidity": "si"}, {"lowLiquidity": 1},
              {"lowLiquidity": None}):
        out.append(dict(sano, ctx=v))
    for v in (None, "basura", 42, {}):
        out.append(dict(sano, profile=v))
    # `scores` y `flags` rotos: `unusualTradeScore` cuelga de ellos.
    for v in (None, {}, "basura", {"total": "abc"}):
        out.append(dict(sano, row=dict(fila, scores=v)))
    for v in (None, {}, "basura", {"multileg": "si"}):
        out.append(dict(sano, row=dict(fila, flags=v)))

    # ── `withinMoneyness` (commit "screener más accesible para cuenta chica") ──
    # La cercanía es una RESTA de JS (`row.strike - spot`), así que el `-`
    # coacciona: un strike en texto vale, uno vacío vale 0 y uno con letras es
    # NaN — tres caminos distintos que aquí se miden por separado.
    for campo in ("strike", "assetPrice"):
        for v in BASURA_NUM + [AUSENTE]:
            out.append(dict(sano, row=mezcla(fila, campo, v)))
    # Los bordes exactos de la banda ±25%, por los dos lados y con el spot fijo.
    for s, a in ((100, 100), (125, 100), (125.1, 100), (126, 100),
                 (75, 100), (74.9, 100), (200, 100), (10, 100),
                 (0, 100), (-50, 100), (100, 0), (100, -100)):
        out.append(dict(sano, row=dict(fila, strike=s, assetPrice=a)))
    # Y la banda en sí: el parámetro tiene default, así que un `null` explícito
    # NO lo activa (en JS solo lo hace `undefined`) y la comparación se cae.
    for v in BASURA_NUM + [AUSENTE]:
        out.append(mezcla(sano, "cap", v))
    return out


def casos():
    return {"ivcontext": casos_ivcontext(), "gex": casos_gex(),
            "prediction": casos_prediction(), "risk": casos_risk()}


if __name__ == "__main__":
    print(json.dumps(casos()))
