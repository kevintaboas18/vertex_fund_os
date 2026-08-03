"""Compara `review_predictions` contra SU `predictionStore.reviewPredictions`."""
import collections
import json
import math
import os
import pathlib
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diffcalib_casos import casos as generar                     # noqa: E402

from wbj.tito.jsmath import UNDEFINED                             # noqa: E402
from wbj.tito.stores import review_predictions                    # noqa: E402

C = generar()
V = json.load(open(os.environ["CALIB_OUT"]))
_DONDE = collections.Counter()


def r6(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return x
    if not math.isfinite(x):
        return "NaN" if x != x else ("Inf" if x > 0 else "-Inf")
    y = x * 1e6
    if not math.isfinite(y):
        return "Inf" if y > 0 else "-Inf"
    return math.floor(y + 0.5) / 1e6


def d6(x):
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


class _Barra:
    """Una barra tal y como la lee su `reviewPredictions`: `time/high/low/close`."""

    __slots__ = ("time", "high", "low", "close")

    def __init__(self, d):
        self.time, self.high = g(d, "time"), g(d, "high")
        self.low, self.close = g(d, "low"), g(d, "close")


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


fallos, lanza_port, lanza_el = [], [], []
from datetime import datetime                                     # noqa: E402

for i, (c, v) in enumerate(zip(C, V)):
    try:
        # Las claves del corpus van en camelCase (son las de SU archivo); el
        # port habla snake_case, igual que `stores.save_prediction` escribe.
        fotos = [{("horizon_days" if k == "horizonDays"
                   else "saved_at" if k == "savedAt" else k): w
                  for k, w in s.items()} for s in c["snapshots"]]
        r = review_predictions(
            fotos, [_Barra(b) for b in c["bars"]],
            datetime.fromisoformat(c["now"].replace("Z", "+00:00")))
        mio = d6({
            "matured": r["matured_count"], "mae": r["mean_abs_error_pct"],
            "bias": r["bias_pct"], "touch": r["base_touch_rate"],
            "dir": r["direction_hit_rate"], "best": r["best_counts"],
            "n": len(r["evals"]),
            "evals": [[e["date"], e["horizon_days"], e["sessions"], e["matured"],
                       e["actual_close"], e["actual_high"], e["actual_low"],
                       e["base_error_pct"], e["base_abs_error_pct"],
                       e["base_touched"], e["bull_touched"], e["bear_touched"],
                       e["direction_hit"], e["best"]] for e in r["evals"][:4]],
        })
    except Exception as e:                       # noqa: BLE001
        if os.environ.get("CALIB_DEBUG"):
            marcos = [m for m in traceback.extract_tb(sys.exc_info()[2])
                      if "/wbj/tito/" in m.filename]
            u = marcos[-1] if marcos else traceback.extract_tb(sys.exc_info()[2])[-1]
            _DONDE[f"{u.filename.split('/tito/')[-1]}:{u.lineno} "
                   f"{type(e).__name__} | {(u.line or '')[:58]}"] += 1
        mio = {"ERROR": type(e).__name__}
    if "ERROR" in mio and "ERROR" not in v:
        lanza_port.append(mio["ERROR"])
    elif "ERROR" in v and "ERROR" not in mio:
        lanza_el.append(v["ERROR"])
    suyo = v if ("ERROR" in mio or "ERROR" in v) else {k: v.get(k) for k in mio}
    fallos += [(f"#{i}",) + d for d in dif(suyo, mio, "")]

n = len(C)
malos = {f[0] for f in fallos}
print(f"  {n} diarios · reviewPredictions (el motor de calibración)")
if lanza_port:
    print("\n  ✗ EL PORT LANZA Y ÉL NO")
    for k, c in collections.Counter(lanza_port).most_common():
        print(f"      {k:<40} {c:>4} caso(s)")
if lanza_el:
    print("\n  · él lanza y el port no")
    for k, c in collections.Counter(lanza_el).most_common():
        print(f"      {k:<40} {c:>4} caso(s)")
campos = collections.Counter(ruta for _, ruta, s, m in fallos if ruta != ".ERROR")
if campos:
    print("\n  ✗ CAMPOS QUE DIFIEREN")
    for k, c in campos.most_common(12):
        ej = next(f for f in fallos if f[1] == k)
        print(f"      {k:<26} {c:>4}   ej {ej[0]}: víctor={str(ej[2])[:38]} "
              f"port={str(ej[3])[:38]}")
if os.environ.get("CALIB_LISTA"):
    print("\n  · DETALLE")
    for b, ruta, s, m in fallos:
        print(f"      {b}{ruta:<24} víctor={str(s)[:60]!r} port={str(m)[:60]!r}")
if _DONDE:
    print("\n  · DÓNDE LANZA EL PORT (CALIB_DEBUG)")
    for k, c in _DONDE.most_common(10):
        print(f"      {c:>4}  {k}")

BASE = pathlib.Path(__file__).with_name("_diffcalib_base.json")
conocidos = set(json.loads(BASE.read_text())) if BASE.exists() else set()
if os.environ.get("CALIB_CONGELA"):
    BASE.write_text(json.dumps(sorted(malos), indent=0))
    print(f"  · línea base actualizada: {len(malos)} casos declarados")
    conocidos = malos
nuevos, curados = sorted(malos - conocidos), sorted(conocidos - malos)
if nuevos:
    print(f"\n  ✗ {len(nuevos)} DIVERGENCIA(S) NUEVA(S) sin declarar: {nuevos[:8]}")
if curados:
    print(f"\n  ! {len(curados)} declarado(s) que ya NO divergen (CALIB_CONGELA=1)")
print(f"\n  {n - len(malos)}/{n} diarios idénticos"
      + (f" · {len(malos)} declarados" if malos else " · sin diferencias"))
sys.exit(1 if (nuevos or curados) else 0)
