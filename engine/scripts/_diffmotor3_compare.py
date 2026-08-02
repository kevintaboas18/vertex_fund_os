"""Compara gexHeatmap / news contra SUS archivos en Node.

Mismo contrato que los otros dos comparadores: se indulta 1 ULP de coma flotante
y nada más. `MOTOR3_DEBUG=1` imprime dónde lanza el port; `MOTOR3_LISTA=1`
vuelca caso por caso.
"""
import collections
import json
import math
import os
import pathlib
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diffmotor3_casos import casos as generar                     # noqa: E402

from wbj.tito.gex_heatmap import HeatTrade, gex_heatmap            # noqa: E402
from wbj.tito.jsmath import UNDEFINED                              # noqa: E402
from wbj.tito.news import (NewsBias, NewsItem, company_aliases,    # noqa: E402
                           contradiction_flag, decode_entities, flow_bias,
                           mentions_company, news_bias, parse_feed_date,
                           parse_rss, recency_weight)
from wbj.tito.structure import ChainRow                            # noqa: E402

C = generar()
V = json.load(open(os.environ["MOTOR3_OUT"]))
_DONDE = collections.Counter()


def r6(x):
    """`Math.round(x * 1e6) / 1e6` del lado JS, con sus mismas salvedades."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return x
    if not math.isfinite(x):
        return "NaN" if x != x else ("Inf" if x > 0 else "-Inf")
    y = x * 1e6
    if not math.isfinite(y):
        return "Inf" if y > 0 else "-Inf"
    return math.floor(y + 0.5) / 1e6


def d6(x):
    """`r6` en profundidad, el espejo del `d6` del runner."""
    if isinstance(x, list):
        return [d6(v) for v in x]
    if isinstance(x, dict):
        return {k: d6(v) for k, v in x.items()}
    return r6(x)


def g(d, k):
    """`obj.k` de JS: la clave ausente es `undefined`, que NO es `null`."""
    if not isinstance(d, dict):
        return UNDEFINED
    return d.get(k, UNDEFINED)


class _Nulo:
    """El `null` de JS como receptor: leer una propiedad lanza TypeError."""

    __slots__ = ()

    def __getattr__(self, k):
        raise TypeError(f"Cannot read properties of null (reading '{k}')")


class _Primitivo:
    """`"basura"`, `42`, `[]`: leer una propiedad da `undefined`."""

    __slots__ = ()

    def __getattr__(self, k):
        return UNDEFINED


def _receptor(d):
    if d is None:
        return _Nulo()
    if not isinstance(d, dict):
        return _Primitivo()
    return None


def _ulp(a, b):
    return (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and not isinstance(a, bool) and not isinstance(b, bool)
            and a != 0 and abs(a - b) / abs(a) < 1e-12)


def dif(a, b, ruta=""):
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [(ruta + ".len", len(a), len(b))]
        for i, (x, y) in enumerate(zip(a, b)):
            d = dif(x, y, f"{ruta}[{i}]")
            if d:
                return d
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            d = dif(a[k], b.get(k), f"{ruta}.{k}")
            if d:
                return d
        return []
    return [] if (a == b or _ulp(a, b)) else [(ruta, a, b)]


def protegido(fn):
    try:
        return d6(fn())
    except Exception as e:                       # noqa: BLE001 — se compara la clase
        if os.environ.get("MOTOR3_DEBUG"):
            marcos = [m for m in traceback.extract_tb(sys.exc_info()[2])
                      if "/wbj/tito/" in m.filename]
            u = marcos[-1] if marcos else traceback.extract_tb(sys.exc_info()[2])[-1]
            _DONDE[f"{u.filename.split('/tito/')[-1]}:{u.lineno} "
                   f"{type(e).__name__} | {(u.line or '')[:58]}"] += 1
        return {"ERROR": type(e).__name__}


fallos, n = [], 0
lanza_port, lanza_el = [], []


def compara(bloque, i, mio, suyo_full):
    global n
    n += 1
    if isinstance(mio, dict) and "ERROR" in mio and not (
            isinstance(suyo_full, dict) and "ERROR" in suyo_full):
        lanza_port.append((bloque, mio["ERROR"]))
    elif isinstance(suyo_full, dict) and "ERROR" in suyo_full and not (
            isinstance(mio, dict) and "ERROR" in mio):
        lanza_el.append((bloque, suyo_full["ERROR"]))
    suyo = suyo_full
    if (isinstance(mio, dict) and isinstance(suyo_full, dict)
            and "ERROR" not in mio and "ERROR" not in suyo_full):
        suyo = {k: suyo_full.get(k) for k in mio}
    fallos.extend((f"{bloque}#{i}",) + d for d in dif(suyo, mio, ""))


AHORA = datetime.fromisoformat(C["now"].replace("Z", "+00:00"))

# ─────────────────────────────── gexHeatmap ───────────────────────────────
for i, (c, v) in enumerate(zip(C["heatmap"], V["heatmap"])):
    def _caso(c=c):
        r = gex_heatmap(
            [_receptor(x) or ChainRow(g(x, "contractType"), g(x, "expiration"),
                                      g(x, "strike"), g(x, "openInterest"),
                                      g(x, "volume"), g(x, "notionalValue"))
             for x in c["rows"]],
            g(c, "spot"), g(c, "iv"),
            datetime.fromisoformat(c["now"].replace("Z", "+00:00")),
            trades=[_receptor(t) or HeatTrade(g(t, "strike"), g(t, "expiration"),
                                              g(t, "gamma"), g(t, "premium"))
                    for t in (c.get("trades") if c.get("trades") is not None else [])],
            strike_radius=(c["strikeRadius"] if "strikeRadius" in c else 18),
            max_expirations=(c["maxExpirations"] if "maxExpirations" in c else 8))

        def cel(x):
            return None if x is None else [x.strike, x.expiration, x.net_gex,
                                           x.call_gex, x.put_gex,
                                           x.open_interest, x.intensity]
        return {
            "spot": r.spot, "iv": r.iv, "totalNetGex": r.total_net_gex,
            "maxAbsCell": r.max_abs_cell,
            "nExp": len(r.expirations), "nStrikes": len(r.strikes),
            "nCells": len(r.cells),
            "exps": [[e.expiration, e.dte, e.net_gex, e.open_interest]
                     for e in r.expirations[:4]],
            "strikes": [[s.strike, s.net_gex, s.call_gex, s.put_gex,
                         s.open_interest, s.distance_pct] for s in r.strikes[:4]],
            "cells": [cel(x) for x in r.cells[:6]],
            "calientePos": cel(r.hottest_positive),
            "calienteNeg": cel(r.hottest_negative),
        }
    compara("heatmap", i, protegido(_caso), v)

# ─────────────────────────────────── news ─────────────────────────────────
N, VN = C["news"], V["news"]


def _sesgo(d):
    return NewsBias(bias=g(d, "bias"), score=g(d, "score"),
                    positive=g(d, "positive"), negative=g(d, "negative"),
                    neutral=g(d, "neutral"))


def _item(d):
    return _receptor(d) or NewsItem(
        id=g(d, "id"), title=g(d, "title"), url=g(d, "url"),
        publisher=g(d, "publisher"), published_utc=g(d, "publishedUtc"),
        description=g(d, "description"), sentiment=g(d, "sentiment"),
        reasoning=g(d, "reasoning"), layer=g(d, "layer"),
        matched_by=g(d, "matchedBy"))


for i, (s, v) in enumerate(zip(N["entidades"], VN["entidades"])):
    compara("entidades", i, protegido(lambda s=s: decode_entities(s)), v)
for i, (s, v) in enumerate(zip(N["fechas"], VN["fechas"])):
    compara("fechas", i, protegido(lambda s=s: parse_feed_date(s)), v)
for i, (x, v) in enumerate(zip(N["rss"], VN["rss"])):
    compara("rss", i, protegido(lambda x=x: [
        [it.id, it.title, it.url, it.publisher, it.published_utc,
         it.description, it.sentiment, it.reasoning, it.layer]
        for it in parse_rss(x, "pub")]), v)
for i, ((t, nm), v) in enumerate(zip(N["alias"], VN["alias"])):
    compara("alias", i, protegido(lambda t=t, nm=nm: company_aliases(t, nm)), v)
for i, ((t, a), v) in enumerate(zip(N["menciones"], VN["menciones"])):
    compara("menciones", i, protegido(lambda t=t, a=a: mentions_company(t, a)), v)
for i, ((p, ahora), v) in enumerate(zip(N["frescura"], VN["frescura"])):
    compara("frescura", i, protegido(
        lambda p=p, ahora=ahora: recency_weight(
            p, datetime.fromisoformat(ahora.replace("Z", "+00:00")))), v)
for i, (lote, v) in enumerate(zip(N["lotes"], VN["lotes"])):
    def _caso(lote=lote):
        b = news_bias([_item(x) for x in lote], AHORA)
        return [b.bias, b.score, b.positive, b.negative, b.neutral]
    compara("lotes", i, protegido(_caso), v)
for i, (p, v) in enumerate(zip(N["flowPct"], VN["flowPct"])):
    compara("flowPct", i, protegido(lambda p=p: flow_bias(p)), v)
for i, ((f, s), v) in enumerate(zip(N["contradiccion"], VN["contradiccion"])):
    def _caso(f=f, s=s):
        c = contradiction_flag(f, _sesgo(s))
        return [c.kind, c.title, c.detail]
    compara("contradiccion", i, protegido(_caso), v)


# ── Reporte ────────────────────────────────────────────────────────────────
print(f"  {n} casos · gexHeatmap + news")
casos_malos = {f[0] for f in fallos}
revienta = collections.Counter(f"{b} → {e}" for b, e in lanza_port)
solo_el = collections.Counter(f"{b} → {e}" for b, e in lanza_el)
campos = collections.Counter(
    f"{b.split('#')[0]}{ruta}" for b, ruta, s, m in fallos
    if not (isinstance(m, dict) and "ERROR" in m) and ruta != ".ERROR")

if revienta:
    print("\n  ✗ EL PORT LANZA Y ÉL NO — tumba la petición, no una fila")
    for k, c in revienta.most_common(10):
        print(f"      {k:<50} {c:>4} caso(s)")
if solo_el:
    print("\n  · él lanza y el port no")
    for k, c in solo_el.most_common(10):
        print(f"      {k:<50} {c:>4} caso(s)")
if campos:
    print("\n  ✗ CAMPOS QUE DIFIEREN")
    for k, c in campos.most_common(14):
        ej = next(f for f in fallos if f"{f[0].split('#')[0]}{f[1]}" == k)
        print(f"      {k:<38} {c:>4}   ej {ej[0]}:"
              f" víctor={str(ej[2])[:40]} port={str(ej[3])[:40]}")
if os.environ.get("MOTOR3_LISTA"):
    filtro = os.environ["MOTOR3_LISTA"]
    print("\n  · DETALLE")
    for b, ruta, s, m in fallos:
        if filtro in ("1", b.split("#")[0]):
            print(f"      {b}{ruta:<22} víctor={str(s)[:66]!r} port={str(m)[:66]!r}")
if _DONDE:
    print("\n  · DÓNDE LANZA EL PORT (MOTOR3_DEBUG)")
    for k, c in _DONDE.most_common(12):
        print(f"      {c:>4}  {k}")

#: Por qué está declarado cada caso. Sin esto, la línea base es una lista de
#: números que nadie puede auditar seis meses después.
_RAZONES = {
    "fechas#9": (
        "`new Date(\"0\")` cae al parseo LEGACY de V8, que la propia "
        "especificación declara *implementation-defined* (da el año 2000). "
        "`jsmath.js_date_parse` no lo replica a propósito — copiarlo sería "
        "copiar una peculiaridad de V8, no la lógica de Víctor. Mismo criterio "
        "y mismas 15 exenciones que `diff_primitivas.sh`. La fuente manda ISO."
    ),
}

BASE = pathlib.Path(__file__).with_name("_diffmotor3_base.json")
conocidos = set(json.loads(BASE.read_text())) if BASE.exists() else set()
if os.environ.get("MOTOR3_CONGELA"):
    BASE.write_text(json.dumps(sorted(casos_malos), indent=0))
    print(f"  · línea base actualizada: {len(casos_malos)} casos declarados")
    conocidos = casos_malos

for c in sorted(casos_malos):
    print(f"\n  · DECLARADO {c}\n      {_RAZONES.get(c, 'SIN RAZÓN ESCRITA')}")
sin_razon = sorted(casos_malos - set(_RAZONES))

nuevos = sorted(casos_malos - conocidos)
curados = sorted(conocidos - casos_malos)
if nuevos:
    print(f"\n  ✗ {len(nuevos)} DIVERGENCIA(S) NUEVA(S) sin declarar: {nuevos[:8]}")
if curados:
    print(f"\n  ! {len(curados)} declarado(s) que ya NO divergen — actualiza la "
          f"línea base (MOTOR3_CONGELA=1): {curados[:8]}")

print(f"\n  {n - len(casos_malos)}/{n} casos idénticos"
      + (f" · {len(casos_malos)} declarados" if casos_malos else " · sin diferencias"))
if sin_razon:
    print(f"\n  ✗ {len(sin_razon)} declarado(s) SIN razón escrita en `_RAZONES`: "
          f"{sin_razon[:8]}")
sys.exit(1 if (nuevos or curados or sin_razon) else 0)
