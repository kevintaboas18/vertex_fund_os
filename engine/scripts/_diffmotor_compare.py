"""Compara el motor en Python contra SUS validation/levels/structure.

Tolerancia: solo se indulta la diferencia de 1 ULP en coma flotante (< 1e-12
relativo), que viene del orden de las sumas y no de la lógica. Cualquier otra
cosa es un fallo y el script sale con código 1.
"""
import json, os, sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diffmotor_casos import casos as generar                       # noqa: E402
from wbj.tito.levels import ChainLevel, FlowLevel, GexLevel, LvlBar, find_levels  # noqa: E402
from wbj.tito.structure import ChainRow, structure_score            # noqa: E402
from wbj.tito.validation import FlowLite, ValBar, validation_score  # noqa: E402

C = generar()
V = json.load(open(os.environ["MOTOR_OUT"]))
r6 = lambda x: round(x * 1e6) / 1e6


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


fallos, n = [], 0

for i, (c, v) in enumerate(zip(C["validation"], V["validation"])):
    r = validation_score(
        [FlowLite(f["id"], f["timestamp"], f["type"], f["strike"], f["expiration"],
                  f["assetPrice"], f["premium"], f["aggression"]) for f in c["flows"]],
        [ValBar(b["time"], b["high"], b["low"], b["close"]) for b in c["bars"]],
        datetime.fromisoformat(c["now"].replace("Z", "+00:00")),
        threshold_pct=c["thresholdPct"], horizon=c["horizon"])
    mio = {"score": r.score, "whr": r.weighted_hit_rate, "mfe": r.avg_mfe, "mae": r.avg_mae,
           "thr": r.threshold_pct, "verdict": r.verdict, "nout": len(r.outcomes),
           "ids": [o.id for o in r.outcomes[:6]],
           "det": [[o.resolved, o.validated, o.sessions_observed, o.days_to_validate,
                    r6(o.mfe_pct), r6(o.mae_pct)] for o in r.outcomes[:6]]}
    suyo = {k: v[k] for k in mio}
    fallos += [(f"validation#{i}",) + d for d in dif(suyo, mio, "")]
    n += 1

for i, (c, v) in enumerate(zip(C["levels"], V["levels"])):
    r = find_levels(
        bars=[LvlBar(b["time"], b["high"], b["low"], b["close"]) for b in c["bars"]],
        spot=c["spot"], now=datetime.fromisoformat(c["now"].replace("Z", "+00:00")),
        chain=[ChainLevel(x["strike"], x["contractType"], x["openInterest"], x["notionalValue"]) for x in c["chain"]],
        flows=[FlowLevel(x["strike"], x["type"], x["aggression"], x["premium"]) for x in c["flows"]],
        gex=[GexLevel(x["strike"], x["netGex"]) for x in c["gex"]],
        tolerance_pct=c["tolerancePct"], rango_pct=c["rangePct"])
    m = lambda a: [[r6(l.price), l.kind, r6(l.strength), r6(l.distance_pct),
                    [l.sources.touches, l.sources.last_touch, l.sources.open_interest,
                     r6(l.sources.notional), r6(l.sources.flow_premium), r6(l.sources.net_gex)],
                    l.flipped, l.why] for l in a]
    mio = {"spot": r.spot, "sup": m(r.supports), "res": m(r.resistances),
           "ks": r6(r.key_support.price) if r.key_support else None,
           "kr": r6(r.key_resistance.price) if r.key_resistance else None,
           "tol": r.tolerance_pct}
    fallos += [(f"levels#{i}",) + d for d in dif({k: v[k] for k in mio}, mio, "")]
    n += 1

for i, (rows, v) in enumerate(zip(C["structure"], V["structure"])):
    r = structure_score([ChainRow(x["contractType"], x["expiration"], x["strike"],
                                  x["openInterest"], x["volume"], x["notionalValue"]) for x in rows])
    st = r.strikes
    mio = {"score": r.score,
           "strikes": {"dominantCount": st["dominant_count"], "consideredCount": st["considered_count"],
                       "points": st["points"], "callPct": st["call_pct"], "putPct": st["put_pct"],
                       "dominantSide": st["dominant_side"],
                       "top": [[r6(t.strike), r6(t.notional), t.side, t.dominant,
                                r6(t.dominance_pct), t.open_interest, t.volume] for t in st["top"][:4]]},
           "volOI": {"pct": r.vol_oi["pct"], "exceeded": r.vol_oi["exceeded"],
                     "considered": r.vol_oi["considered"], "points": r.vol_oi["points"]},
           "exps": [[e.expiration, e.contracts, r6(e.notional)] for e in r.expirations[:4]]}
    fallos += [(f"structure#{i}",) + d for d in dif({k: v[k] for k in mio}, mio, "")]
    n += 1

print(f"  {n} casos · validation + levels + structure")
for f in fallos[:15]:
    print(f"   ✗ {f[0]} {f[1]}\n       víctor = {str(f[2])[:110]}\n       port   = {str(f[3])[:110]}")
print(f"\n  {n - len({f[0] for f in fallos})}/{n} casos idénticos"
      + (f" · {len(fallos)} diferencias" if fallos else " · sin diferencias (1 ULP indultado)"))
sys.exit(1 if fallos else 0)
