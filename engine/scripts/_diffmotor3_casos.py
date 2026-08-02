"""Corpus del tercer diferencial: `gexHeatmap.ts` y las funciones puras de `news.ts`.

Son los dos únicos módulos de su `web/lib` que ningún otro diferencial toca.
`occ`, `conditions`, `expectedMove` y `blackScholes` sí se ejercitan —y con
corpus malformado— desde `diff_motor.sh` y `diff_motor2.sh`, porque el motor los
llama en cada caso; estos dos cuelgan de rutas propias y quedaban sin medir.

Mismo criterio que los otros dos: primero la mezcla bien formada, y después
basura campo a campo. El `AUSENTE` borra la clave, que NO es lo mismo que
ponerla a `null` (ver `jsmath.UNDEFINED`).
"""
import json
import random
from datetime import date, timedelta

BASURA_NUM = [None, "500", "abc", "", "   ", [], {}, True, False,
              "NaN", "Infinity", "-Infinity", "1_000", "0x1A",
              1e308, -1e308, 1e-308, -0.0, 0.1 + 0.2]
BASURA_TXT = [None, "", "  ", "CALL", "Put", "PUT", 0, 5, True, [], {}]
BASURA_FECHA = [None, "", "2026-09-18", "2026-09-18T00:00:00Z", "2026-09",
                "2026", "2026-13-45", "20260918", "basura", 20260918]


class _Ausente:
    pass


AUSENTE = _Ausente()
AHORA = "2026-07-31T18:00:00Z"


def mezcla(base, campo, valor):
    d = dict(base)
    if valor is AUSENTE:
        d.pop(campo, None)
    else:
        d[campo] = valor
    return d


def _fila_cadena(strike=100.0, exp="2026-09-18", oi=9000, tipo="call"):
    return {"contractType": tipo, "expiration": exp, "strike": strike,
            "openInterest": oi, "volume": 4000, "notionalValue": 9e7}


def _trade_calor(strike=100.0, exp="2026-09-18", gamma=0.03):
    return {"strike": strike, "expiration": exp, "gamma": gamma,
            "premium": 250000.0}


# ───────────────────────────── gexHeatmap ─────────────────────────────────


def casos_heatmap():
    out = []
    r = random.Random(11)
    vencimientos = ["2026-08-21", "2026-09-18", "2026-10-16", "2027-01-15"]

    # Bien formados: cadenas de varios vencimientos y radios distintos.
    for _ in range(30):
        filas = [_fila_cadena(float(s), r.choice(vencimientos),
                              r.choice([0, 100, 9000, 250000]),
                              r.choice(["call", "put"]))
                 for s in range(70, 135, 5)]
        trades = [_trade_calor(float(r.choice([90, 95, 100, 105])),
                               r.choice(vencimientos),
                               round(r.uniform(0.001, 0.2), 5))
                  for _ in range(r.choice([0, 1, 6]))]
        out.append({"rows": filas, "spot": r.choice([0, 100.0, 850.0]),
                    "iv": r.choice([0.05, 0.45, 3.0]), "trades": trades,
                    "now": AHORA, "strikeRadius": r.choice([1, 3, 18]),
                    "maxExpirations": r.choice([1, 2, 8])})

    # MALFORMADOS, campo a campo de la cadena.
    base = _fila_cadena()
    for campo, valores in (("strike", BASURA_NUM), ("openInterest", BASURA_NUM),
                           ("expiration", BASURA_FECHA), ("contractType", BASURA_TXT)):
        for v in valores + [AUSENTE]:
            out.append({"rows": [mezcla(base, campo, v),
                                 _fila_cadena(105.0), _fila_cadena(95.0, tipo="put")],
                        "spot": 100.0, "iv": 0.45, "trades": [], "now": AHORA})

    # …y de los trades que anclan la gamma.
    t = _trade_calor()
    for campo, valores in (("strike", BASURA_NUM), ("gamma", BASURA_NUM),
                           ("premium", BASURA_NUM), ("expiration", BASURA_FECHA)):
        for v in valores + [AUSENTE]:
            out.append({"rows": [_fila_cadena(), _fila_cadena(105.0)],
                        "spot": 100.0, "iv": 0.45,
                        "trades": [mezcla(t, campo, v)], "now": AHORA})

    # Parámetros sueltos, filas que no son objetos y listas vacías.
    caso = {"rows": [_fila_cadena(), _fila_cadena(105.0)], "spot": 100.0,
            "iv": 0.45, "trades": [], "now": AHORA}
    for campo in ("spot", "iv", "strikeRadius", "maxExpirations"):
        for v in BASURA_NUM + [AUSENTE]:
            out.append(mezcla(caso, campo, v))
    out.append({"rows": [], "spot": 100.0, "iv": 0.45, "trades": [], "now": AHORA})
    out.append({"rows": [None, "basura", 42, [], _fila_cadena()], "spot": 100.0,
                "iv": 0.45, "trades": [], "now": AHORA})
    out.append({"rows": [_fila_cadena()], "spot": 100.0, "iv": 0.45,
                "trades": [None], "now": AHORA})
    return out


# ─────────────────────────────── news ─────────────────────────────────────

_XML_OK = """<rss><channel>
<item><title>Apple beats on earnings</title><link>https://x/1</link>
<pubDate>Fri, 24 Jul 2026 03:15:46 GMT</pubDate>
<description>Strong quarter &amp; record revenue</description></item>
<item><title><![CDATA[NVIDIA cuts guidance]]></title><link>https://x/2</link>
<pubDate>2026-07-24 02:54:27</pubDate></item>
<item><title>sin enlace</title><pubDate>basura</pubDate></item>
</channel></rss>"""


def casos_news():
    entidades = ["", "sin nada", "AT&amp;T", "&lt;b&gt;", "&#65;&#66;", "&#x41;",
                 "&nosoyuna;", "&#999999999;", "&#;", "&amp;amp;", "&AMP;",
                 "a&#48;b", "&#-1;", "&#NaN;"]
    fechas = [None, "", "basura", "Fri, 24 Jul 2026 03:15:46 GMT",
              "2026-07-24 02:54:27", "2026-07-24T02:54:27Z", "2026-13-45",
              "2026-07-24 02:54", "  2026-07-24 02:54:27  ", "0"]
    xmls = [_XML_OK, "", "<rss></rss>", "<item></item>",
            "<item><title>a</title><link>b</link></item>",
            "<ITEM><TITLE>MAY</TITLE><LINK>l</LINK></ITEM>",
            "<item><title>   </title><link>l</link></item>"]
    alias = [("AAPL", "Apple Inc."), ("A", "Agilent Technologies, Inc."),
             ("IT", None), ("TSLA", "Tesla, Inc"), ("XYZ", ""),
             ("  aapl  ", "The Coca-Cola Company"), ("NVDA", "NVIDIA Corp.")]
    textos = ["Apple beats on earnings", "apple pie recipe", "AAPL rallies",
              "aapl rallies", "no menciona nada", "AAPLE", "x AAPL y", "AAPL",
              "Agilent Technologies wins", ""]
    pesos = [("2026-07-31T12:00:00Z", AHORA), ("2026-07-29T12:00:00Z", AHORA),
             ("2026-07-26T12:00:00Z", AHORA), ("2026-06-01T12:00:00Z", AHORA),
             ("2026-08-30T12:00:00Z", AHORA), ("basura", AHORA), ("", AHORA)]

    def item(sent, pub="2026-07-31T12:00:00Z"):
        return {"id": "i", "title": "t", "url": "u", "publisher": "p",
                "publishedUtc": pub, "description": None, "sentiment": sent,
                "reasoning": None, "layer": "macro"}

    lotes = [
        [], [item("positive")], [item("negative")], [item(None)],
        [item("neutral"), item("neutral")],
        [item("positive"), item("negative")],
        [item("positive"), item("positive"), item("negative")],
        [item("positive", "2026-06-01T12:00:00Z"), item("negative")],
        [item("positive", "basura")],
        [item("positive"), item("positive"), item("positive"), item("negative")],
    ]
    sesgos = [{"bias": b, "score": s, "positive": 1, "negative": 1, "neutral": 0}
              for b in ("bullish", "bearish", "neutral", "mixed")
              for s in (0.5, -0.5, 0.0)]
    return {
        "entidades": entidades,
        "fechas": fechas,
        "rss": xmls,
        "alias": alias,
        "menciones": [[t, ["AAPL", "Apple Inc"]] for t in textos]
                     + [["x", []], ["AAPL", ["A"]], ["", ["AAPL"]]],
        "frescura": pesos,
        "lotes": lotes,
        "flowPct": [0, 39.9, 40, 40.1, 50, 59.9, 60, 100, -5, 1e308],
        "contradiccion": [[f, s] for f in ("bullish", "bearish", "neutral", "mixed")
                          for s in sesgos[:6]],
    }


def casos():
    return {"heatmap": casos_heatmap(), "news": casos_news(), "now": AHORA}


if __name__ == "__main__":
    print(json.dumps(casos()))
