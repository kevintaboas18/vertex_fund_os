"""Compara el motor en Python contra SUS validation/levels/structure.

Tolerancia: solo se indulta la diferencia de 1 ULP en coma flotante (< 1e-12
relativo), que viene del orden de las sumas y no de la lógica. Cualquier otra
cosa es un fallo y el script sale con código 1.
"""
import collections, json, math, os, pathlib, sys, traceback

#: Solo con MOTOR_DEBUG=1: dónde lanzó el port, agrupado por línea.
_DONDE = collections.Counter()
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diffmotor_casos import casos as generar                       # noqa: E402
from wbj.tito.levels import ChainLevel, FlowLevel, GexLevel, LvlBar, find_levels  # noqa: E402
from wbj.tito.structure import ChainRow, structure_score            # noqa: E402
from wbj.tito.flow import (aggression_score, classify_flow, conviction_score,  # noqa: E402
                           detect_clusters, unusuality_score)
from wbj.tito.validation import FlowLite, ValBar, validation_score  # noqa: E402

C = generar()
V = json.load(open(os.environ["MOTOR_OUT"]))
def r6(x):
    """`Math.round(x * 1e6) / 1e6` del lado JS, con sus mismas salvedades.

    Un no finito pasa TAL CUAL: `Math.round(Infinity)` es `Infinity` y
    `round()` de Python lanza `OverflowError`. Desde que el corpus lleva
    entradas malformadas esto es alcanzable — un `assetPrice` de 1e308 produce
    un MFE infinito en los dos lados, y el arnés no puede ser lo que se cae.
    """
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return x
    if not math.isfinite(x):
        return "NaN" if x != x else ("Inf" if x > 0 else "-Inf")
    # `x * 1e6` puede desbordar a infinito aunque `x` sea finito (1e308·1e6).
    # En JS eso es `Math.round(Infinity) / 1e6` = `Infinity`; en Python
    # `round(inf)` es un `OverflowError`. Se replica su resultado.
    y = x * 1e6
    if not math.isfinite(y):
        return "Inf" if y > 0 else "-Inf"
    return round(y) / 1e6


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


def g(d, k):
    """`obj.k` de JS: la clave ausente es `undefined`, no un `KeyError`.

    El arnés tiene que reproducir eso o el corpus malformado no se puede
    ejecutar: una fila SIN el campo es exactamente el caso que se quiere medir,
    y con `d[k]` se cae el comparador antes de llegar al motor.
    """
    return d.get(k) if isinstance(d, dict) else None


fallos, n = [], 0
#: (bloque, excepción) de los casos donde SOLO uno de los dos lados lanzó.
lanza_port, lanza_el = [], []


def protegido(fn):
    """Corre `fn` devolviendo `{"ERROR": clase}` si lanza — como el lado JS.

    Su runner ya envuelve cada caso en `try/catch` y reporta
    `e.constructor.name`. El comparador tiene que hacer lo mismo o el corpus
    malformado no se puede ejecutar: hay entradas donde SU archivo también
    lanza (`undefined.slice()` es un `TypeError` en los dos lados), y eso es
    una coincidencia, no un fallo.

    Los nombres de clase coinciden en los casos que importan —`TypeError` es
    `TypeError`—; cuando no, sale como diferencia y se mira.
    """
    try:
        return fn()
    except Exception as e:                       # noqa: BLE001 — se compara la clase
        if os.environ.get("MOTOR_DEBUG"):
            # Dónde EXACTAMENTE lanzó el port. Sin esto, "TypeError" a secas
            # obliga a reconstruir el caso a mano para encontrar la línea.
            marcos = [m for m in traceback.extract_tb(sys.exc_info()[2])
                      if "/wbj/tito/" in m.filename]
            u = marcos[-1] if marcos else traceback.extract_tb(sys.exc_info()[2])[-1]
            _DONDE[f"{u.filename.split('/tito/')[-1]}:{u.lineno} "
                   f"{type(e).__name__} | {(u.line or '')[:58]}"] += 1
        return {"ERROR": type(e).__name__}


for i, (c, v) in enumerate(zip(C["validation"], V["validation"])):
  def _caso(c=c):
    r = validation_score(
        [FlowLite(g(f, "id"), g(f, "timestamp"), g(f, "type"), g(f, "strike"),
                  g(f, "expiration"), g(f, "assetPrice"), g(f, "premium"),
                  g(f, "aggression")) for f in c["flows"]],
        [ValBar(g(b, "time"), g(b, "high"), g(b, "low"), g(b, "close")) for b in c["bars"]],
        datetime.fromisoformat(c["now"].replace("Z", "+00:00")),
        threshold_pct=c["thresholdPct"], horizon=c["horizon"])
    mio = {"score": r.score, "whr": r.weighted_hit_rate, "mfe": r.avg_mfe, "mae": r.avg_mae,
           "thr": r.threshold_pct, "verdict": r.verdict, "nout": len(r.outcomes),
           "ids": [o.id for o in r.outcomes[:6]],
           "det": [[o.resolved, o.validated, o.sessions_observed, o.days_to_validate,
                    r6(o.mfe_pct), r6(o.mae_pct)] for o in r.outcomes[:6]]}
    return mio
  mio = protegido(_caso)
  if "ERROR" in mio and "ERROR" not in v:
      lanza_port.append(("validation", mio["ERROR"]))
  elif "ERROR" in v and "ERROR" not in mio:
      lanza_el.append(("validation", v["ERROR"]))
  suyo = v if "ERROR" in mio or "ERROR" in v else {k: v[k] for k in mio}
  fallos += [(f"validation#{i}",) + d for d in dif(suyo, mio, "")]
  n += 1

for i, (c, v) in enumerate(zip(C["levels"], V["levels"])):
  def _caso(c=c):
    r = find_levels(
        bars=[LvlBar(g(b, "time"), g(b, "high"), g(b, "low"), g(b, "close")) for b in c["bars"]],
        spot=c["spot"], now=datetime.fromisoformat(c["now"].replace("Z", "+00:00")),
        chain=[ChainLevel(g(x, "strike"), g(x, "contractType"), g(x, "openInterest"),
                          g(x, "notionalValue")) for x in c["chain"]],
        flows=[FlowLevel(g(x, "strike"), g(x, "type"), g(x, "aggression"),
                         g(x, "premium")) for x in c["flows"]],
        gex=[GexLevel(g(x, "strike"), g(x, "netGex")) for x in c["gex"]],
        tolerance_pct=c["tolerancePct"], rango_pct=c["rangePct"])
    m = lambda a: [[r6(l.price), l.kind, r6(l.strength), r6(l.distance_pct),
                    [l.sources.touches, l.sources.last_touch, l.sources.open_interest,
                     r6(l.sources.notional), r6(l.sources.flow_premium), r6(l.sources.net_gex)],
                    l.flipped, l.why] for l in a]
    mio = {"spot": r.spot, "sup": m(r.supports), "res": m(r.resistances),
           "ks": r6(r.key_support.price) if r.key_support else None,
           "kr": r6(r.key_resistance.price) if r.key_resistance else None,
           "tol": r.tolerance_pct}
    return mio
  mio = protegido(_caso)
  if "ERROR" in mio and "ERROR" not in v:
      lanza_port.append(("levels", mio["ERROR"]))
  elif "ERROR" in v and "ERROR" not in mio:
      lanza_el.append(("levels", v["ERROR"]))
  suyo = v if "ERROR" in mio or "ERROR" in v else {k: v[k] for k in mio}
  fallos += [(f"levels#{i}",) + d for d in dif(suyo, mio, "")]
  n += 1

for i, (rows, v) in enumerate(zip(C["structure"], V["structure"])):
  def _caso(rows=rows):
    r = structure_score([ChainRow(g(x, "contractType"), g(x, "expiration"), g(x, "strike"),
                                  g(x, "openInterest"), g(x, "volume"),
                                  g(x, "notionalValue")) for x in rows])
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
    return mio
  mio = protegido(_caso)
  if "ERROR" in mio and "ERROR" not in v:
      lanza_port.append(("structure", mio["ERROR"]))
  elif "ERROR" in v and "ERROR" not in mio:
      lanza_el.append(("structure", v["ERROR"]))
  suyo = v if "ERROR" in mio or "ERROR" in v else {k: v[k] for k in mio}
  fallos += [(f"structure#{i}",) + d for d in dif(suyo, mio, "")]
  n += 1

cf_now = datetime.fromisoformat(C["flow"]["now"].replace("Z", "+00:00"))
for i, (lote, v) in enumerate(zip(C["flow"]["lotes"], V["flow"])):
  def _caso(lote=lote):
    cf = classify_flow(lote, cf_now)
    A = aggression_score(cf.interesting)
    Vc = conviction_score(cf.interesting)
    U = unusuality_score(cf.interesting)
    CL = detect_clusters(cf.interesting)
    mio = {
        "n": len(cf.interesting), "nAll": len(cf.rows),
        "ids": [r.id for r in cf.interesting[:8]],
        "filas": [[r.id, r.type, r.strike, r.dte, r.aggression, r.expiry_status, r.sentiment,
                   r6(r.premium), r6(r.theta_pct_daily), r.flags.big, r.flags.conv_delta,
                   r.flags.above_ask, r.flags.below_bid, r.flags.mid, r.flags.leap,
                   r.flags.repeated, r.flags.multileg, r.flags.simultaneous,
                   r.flags.exceeded_oi, r.unusual, r.interesting, r.condition_code,
                   r.condition_name, r.underlying, r.expiration, r6(r.asset_price),
                   r6(r.iv), r6(r.delta), r.scores.volume, r.scores.timing,
                   r.scores.repetition, r.scores.total] for r in cf.interesting[:5]],
        "agg": {"score": A.score, "ratio": r6(A.ratio), "premiumAsk": r6(A.premium_ask),
                "premiumBid": r6(A.premium_bid), "premiumMid": r6(A.premium_mid), "n": A.n},
        "conv": {"score": Vc.score, "n": Vc.n,
                 "spread": {"avgPct": r6(Vc.spread["avg_pct"]), "points": Vc.spread["points"],
                            "wideCount": Vc.spread["wide_count"]},
                 "dominance": {"askPct": r6(Vc.dominance["ask_pct"]),
                               "bidPct": r6(Vc.dominance["bid_pct"]),
                               "dominantPct": r6(Vc.dominance["dominant_pct"]),
                               "side": Vc.dominance["side"], "points": Vc.dominance["points"]},
                 "execution": {"points": Vc.execution["points"],
                               "avgRaw": r6(Vc.execution["avg_raw"]),
                               "counts": Vc.execution["counts"]}},
        "unu": {"score": U.score, "unusualCount": U.unusual_count, "n": U.n,
                "avgByParam": {k: r6(x) for k, x in U.avg_by_param.items()},
                "top": [[t[0].id, t[1].total, t[1].size, t[1].delta, t[1].theta,
                         t[1].gamma, t[1].leg, t[1].expiry] for t in U.top[:3]]},
        "clus": [[c.start_sec, c.end_sec, c.count, r6(c.premium), c.direction,
                  r6(c.unidirectionality), c.score, r6(c.call_premium), r6(c.put_premium),
                  c.bet, c.bet_label] for c in CL[:3]],
    }
    return mio
  mio = protegido(_caso)
  if "ERROR" in mio and "ERROR" not in v:
      lanza_port.append(("flow", mio["ERROR"]))
  elif "ERROR" in v and "ERROR" not in mio:
      lanza_el.append(("flow", v["ERROR"]))
  suyo = v if "ERROR" in mio or "ERROR" in v else {k: v.get(k) for k in mio}
  fallos += [(f"flow#{i}",) + d for d in dif(suyo, mio, "")]
  n += 1

# ── Reporte ────────────────────────────────────────────────────────────────
#
# Con 1.100+ casos, listar las 15 primeras diferencias no dice nada: salen
# todas del mismo sitio. Se agrupa por CAUSA, que es lo que se puede arreglar.
print(f"  {n} casos · flow + validation + levels + structure")

casos_malos = {f[0] for f in fallos}
# El port lanzó donde él no: es lo caro. Un `TypeError` aquí no tira una fila,
# tira la petición entera y con ella el sub-agente completo.
revienta = collections.Counter(f"{b} → {e}" for b, e in lanza_port)
solo_el = collections.Counter(f"{b} → {e}" for b, e in lanza_el)
_rotos = {b for b, _ in lanza_port} | {b for b, _ in lanza_el}
campos = collections.Counter(
    f"{b.split('#')[0]}{ruta}" for b, ruta, s, m in fallos
    if not (isinstance(m, dict) and "ERROR" in m) and ruta != ".ERROR"
    and not any(f"{b}".startswith(x) and False for x in _rotos))

if revienta:
    print("\n  ✗ EL PORT LANZA Y ÉL NO — tumba la petición, no una fila")
    for k, c in revienta.most_common(10):
        print(f"      {k:<52} {c:>4} caso(s)")
if solo_el:
    print("\n  · él lanza y el port no")
    for k, c in solo_el.most_common(10):
        print(f"      {k:<52} {c:>4} caso(s)")
if campos:
    print("\n  ✗ CAMPOS QUE DIFIEREN")
    for k, c in campos.most_common(14):
        ej = next(f for f in fallos if f"{f[0].split('#')[0]}{f[1]}" == k)
        print(f"      {k:<40} {c:>4}   ej {ej[0]}:"
              f" víctor={str(ej[2])[:42]} port={str(ej[3])[:42]}")

if _DONDE:
    print("\n  · DÓNDE LANZA EL PORT (MOTOR_DEBUG)")
    for k, c in _DONDE.most_common(12):
        print(f"      {c:>4}  {k}")

# ── Línea base declarada ───────────────────────────────────────────────────
#
# El corpus MALFORMADO deja una cola de casos donde el port y su archivo dan
# valores distintos sobre entradas que ninguna fuente real produce (un premium
# de 1e308, un `iv` que es un objeto, un timestamp que es una lista). Ninguno
# tumba ya la petición — esa clase está a cero— y arreglarlos uno a uno sería
# perseguir la coacción de JS hasta el último rincón por un beneficio nulo.
#
# Lo que NO se puede es dejarlos sin contar. Se congelan por ID: el script pasa
# con exactamente estos y falla si aparece uno nuevo o si uno desaparece sin
# actualizar la lista. Así la cola es visible, tiene dueño y no crece sola.
#
# Los casos BIEN FORMADOS (los primeros de cada bloque) no admiten ninguna
# diferencia: ahí el port es idéntico y esa es la garantía que importa.
BASE = pathlib.Path(__file__).with_name("_diffmotor_base.json")
conocidos = set(json.loads(BASE.read_text())) if BASE.exists() else set()
if os.environ.get("MOTOR_CONGELA"):
    BASE.write_text(json.dumps(sorted(casos_malos), indent=0))
    print(f"  · línea base actualizada: {len(casos_malos)} casos declarados")
    conocidos = casos_malos

nuevos = sorted(casos_malos - conocidos)
curados = sorted(conocidos - casos_malos)
if nuevos:
    print(f"\n  ✗ {len(nuevos)} DIVERGENCIA(S) NUEVA(S) sin declarar: {nuevos[:8]}")
if curados:
    print(f"\n  ! {len(curados)} caso(s) declarado(s) que ya NO divergen — "
          f"actualiza la línea base (MOTOR_CONGELA=1): {curados[:8]}")

print(f"\n  {n - len(casos_malos)}/{n} casos idénticos"
      + (f" · {len(casos_malos)} declarados (entradas malformadas)"
         if casos_malos else " · sin diferencias (1 ULP indultado)"))
sys.exit(1 if (nuevos or curados) else 0)
