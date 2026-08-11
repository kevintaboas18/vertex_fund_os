"""Compara ivcontext / gex / prediction / risk contra SUS archivos en Node.

Mismo contrato que `_diffmotor_compare.py`: se indulta 1 ULP de coma flotante y
nada más. `MOTOR2_DEBUG=1` imprime dónde lanza el port, agrupado por línea.
"""
import collections
import json
import math
import os
import pathlib
import sys
import traceback
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diffmotor2_casos import casos as generar                     # noqa: E402

from wbj.tito.expected_move import LevelInput                      # noqa: E402
from wbj.tito.jsmath import UNDEFINED                              # noqa: E402
from wbj.tito.flow import FlowFlags, FlowRow, TradeScores          # noqa: E402
from wbj.tito.gex import TradeLite, gex_analysis                   # noqa: E402
from wbj.tito.ivcontext import iv_context_score                    # noqa: E402
from wbj.tito.prediction import SubScores, predict_pro             # noqa: E402
from wbj.tito.risk import (RiskProfile, budgets_of,                # noqa: E402
                           is_tradeable_idea, size_flow,
                           within_moneyness)
from wbj.tito.structure import ChainRow                            # noqa: E402

C = generar()
V = json.load(open(os.environ["MOTOR2_OUT"]))
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
    # `Math.round`, no `round()`: el de Python es bancario y en el sexto decimal
    # eso produce diferencias de 1e-6 que no son del port sino del arnés.
    return math.floor(y + 0.5) / 1e6


def g(d, k):
    """`obj.k` de JS: la clave ausente es `undefined` — que NO es `null`.

    La distinción importa de verdad: `Number(undefined)` es `NaN` y
    `Number(null)` es 0, así que un campo AUSENTE y un campo con `null` toman
    caminos distintos dentro de su archivo. Ver `jsmath.UNDEFINED`.
    """
    if not isinstance(d, dict):
        return UNDEFINED
    return d.get(k, UNDEFINED)


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
        return fn()
    except Exception as e:                       # noqa: BLE001 — se compara la clase
        if os.environ.get("MOTOR2_DEBUG"):
            marcos = [m for m in traceback.extract_tb(sys.exc_info()[2])
                      if "/wbj/tito/" in m.filename]
            u = marcos[-1] if marcos else traceback.extract_tb(sys.exc_info()[2])[-1]
            _DONDE[f"{u.filename.split('/tito/')[-1]}:{u.lineno} "
                   f"{type(e).__name__} | {(u.line or '')[:58]}"] += 1
        return {"ERROR": type(e).__name__}


class _Nulo:
    """El `null` de JS como receptor: leer CUALQUIER propiedad lanza TypeError.

    Sin esto el arnés aplanaba una fila `null` del corpus en un dataclass con
    todos los campos a `None`, y el port seguía tan tranquilo donde su archivo
    revienta con *Cannot read properties of null*. La divergencia era del arnés,
    no del port: el port nunca llegaba a ver el `null`.
    """

    __slots__ = ()

    def __getattr__(self, k):
        raise TypeError(f"Cannot read properties of null (reading '{k}')")


class _Primitivo:
    """`"basura"`, `42`, `[]` como receptor: leer una propiedad da `undefined`."""

    __slots__ = ()

    def __getattr__(self, k):
        return UNDEFINED


def _receptor(d):
    """Traduce una entrada cruda del corpus a lo que JS pondría a la izquierda
    del punto. Solo los objetos llegan al constructor del dataclass."""
    if d is None:
        return _Nulo()
    if not isinstance(d, dict):
        return _Primitivo()
    return None


def _lista(v, opcional=False):
    """La lista tal cual la recibe su archivo.

    `opcional` marca los parámetros con valor por defecto en su desestructurado
    (`const { trades = [] } = input`). Un valor por defecto de JS entra SOLO con
    `undefined`, **no con `null`**: `trades: null` se queda en `null` y el
    `for…of` de después lanza. Los demás parámetros —`nodes` de `predictPro`—
    entran crudos.
    """
    if opcional and v is UNDEFINED:
        return []
    return v


def _fila(d):
    """Un `FlowRow` del corpus. Los campos ausentes entran como `None`, que es
    lo que ve su TS (`undefined`)."""
    r = _receptor(d)
    if r is not None:
        return r
    fl, sc = g(d, "flags"), g(d, "scores")
    return FlowRow(
        id=g(d, "id"), symbol=g(d, "symbol"), underlying=g(d, "underlying"),
        type=g(d, "type"), strike=g(d, "strike"), expiration=g(d, "expiration"),
        dte=g(d, "dte"), price=g(d, "price"), size=g(d, "size"), side=g(d, "side"),
        aggression=g(d, "aggression"), asset_price=g(d, "assetPrice"),
        bid=g(d, "bid"), ask=g(d, "ask"), premium=g(d, "premium"),
        delta=g(d, "delta"), gamma=g(d, "gamma"), theta=g(d, "theta"),
        vega=g(d, "vega"), theta_pct_daily=g(d, "thetaPctDaily"), iv=g(d, "iv"),
        open_interest=g(d, "openInterest"), volume=g(d, "volume"),
        score=g(d, "score"), sentiment=g(d, "sentiment"),
        timestamp=g(d, "timestamp"), condition_code=g(d, "conditionCode"),
        condition_name=g(d, "conditionName"),
        # `row.flags` y `row.scores` son objetos anidados: un `null` ahí lanza
        # en su archivo al leer `row.flags.big`, así que no se aplanan.
        flags=_receptor(fl) or FlowFlags(
            big=g(fl, "big") or False, conv_delta=g(fl, "convDelta") or False,
            above_ask=g(fl, "aboveAsk") or False, below_bid=g(fl, "belowBid") or False,
            mid=g(fl, "mid") or False, leap=g(fl, "leap") or False,
            repeated=g(fl, "repeated") or False, multileg=g(fl, "multileg") or False,
            simultaneous=g(fl, "simultaneous") or False,
            exceeded_oi=g(fl, "exceededOI") or False),
        scores=_receptor(sc) or TradeScores(volume=g(sc, "volume") or 0, timing=g(sc, "timing") or 0,
                           repetition=g(sc, "repetition") or 0,
                           total=g(sc, "total") or 0),
        unusual=g(d, "unusual") or False, interesting=g(d, "interesting") or False,
        expiry_status=g(d, "expiryStatus") or "desconocido",
    )


fallos, n = [], 0
lanza_port, lanza_el = [], []


def compara(bloque, i, mio, suyo_full):
    global n
    n += 1
    if "ERROR" in mio and "ERROR" not in suyo_full:
        lanza_port.append((bloque, mio["ERROR"]))
    elif "ERROR" in suyo_full and "ERROR" not in mio:
        lanza_el.append((bloque, suyo_full["ERROR"]))
    suyo = (suyo_full if "ERROR" in mio or "ERROR" in suyo_full
            else {k: suyo_full.get(k) for k in mio})
    fallos.extend((f"{bloque}#{i}",) + d for d in dif(suyo, mio, ""))


# ─────────────────────────────── ivcontext ────────────────────────────────
for i, (c, v) in enumerate(zip(C["ivcontext"], V["ivcontext"])):
    def _caso(c=c):
        # El corpus habla el idioma de SU archivo y NO se traduce, porque el
        # archivo del port ahora es el mismo: `stores.save_iv_snapshot` escribe
        # `avgIv` y `ivcontext` lee `avgIv`. Antes aquí se renombraba a
        # `avg_iv`, y ese renombre tapaba el fallo que costó el historial: los
        # dos lados coincidían en el diferencial mientras el archivo del port
        # era ilegible para su app y viceversa. La compatibilidad del archivo
        # la mide `diff_series.sh`; aquí solo se le pasa lo que hay dentro.
        hist = c.get("ivHistory", UNDEFINED)
        r = iv_context_score([_fila(x) for x in c["rows"]], c["closes"], hist)
        return {
            "score": r.score,
            "iv": [r6(r.iv["current"]), r6(r.iv["simple_avg"]), r6(r.iv["min"]),
                   r6(r.iv["max"]), r.iv["contracts"], r.iv["points"],
                   r.iv["band"], r.iv["special"]],
            "rank": [r6(r.rank["value"]), r.rank["source"], r.rank["days"],
                     r6(r.rank["low"]), r6(r.rank["high"]), r6(r.rank["reference"]),
                     r.rank["points"], r.rank["band"]],
            "byExp": [[e.expiration, r6(e.avg_iv), e.trades, r6(e.max_iv), r6(e.premium)]
                      for e in r.by_expiration[:4]],
            "top": [[t.symbol, r6(t.iv)] for t in r.top_contracts[:3]],
            "frontSkew": r6(r.front_skew), "regime": r.regime, "note": r.note,
        }
    compara("ivcontext", i, protegido(_caso), v)

# ─────────────────────────────────── gex ──────────────────────────────────
for i, (c, v) in enumerate(zip(C["gex"], V["gex"])):
    def _caso(c=c):
        r = gex_analysis(
            [_receptor(x) or ChainRow(g(x, "contractType"), g(x, "expiration"),
                                      g(x, "strike"), g(x, "openInterest"),
                                      g(x, "volume"), g(x, "notionalValue"))
             for x in c["rows"]],
            c.get("closes", UNDEFINED), c.get("spot", UNDEFINED),
            datetime.fromisoformat(c["now"].replace("Z", "+00:00")),
            trades=[_receptor(t) or TradeLite(g(t, "strike"), g(t, "type"),
                                             g(t, "premium"), g(t, "gamma"))
                    for t in _lista(c.get("trades", UNDEFINED), opcional=True)],
            conviction_score=c.get("convictionScore", UNDEFINED),
            structure_score=c.get("structureScore", UNDEFINED),
            low_liquidity=c.get("lowLiquidity", UNDEFINED))
        return {
            "spot": r6(r.spot), "iv": r6(r.iv), "regime": r.regime,
            "direction": r.direction, "totalNetGex": r6(r.total_net_gex),
            "flipStrike": r6(r.flip_strike), "kingStrike": r6(r.king_strike),
            "confidence": r6(r.confidence), "lowLiquidity": r.low_liquidity,
            "n": r.n,
            "nodes": [[r6(x.strike), r6(x.net_gex), r6(x.call_gex), r6(x.put_gex),
                       r6(x.trade_premium), r6(x.concentration), x.side]
                      for x in r.nodes[:5]],
        }
    compara("gex", i, protegido(_caso), v)

# ──────────────────────────────── prediction ──────────────────────────────
for i, (c, v) in enumerate(zip(C["prediction"], V["prediction"])):
    def _caso(c=c):
        sc = c.get("scores", UNDEFINED)
        r = predict_pro(
            spot=c.get("spot", UNDEFINED), iv=c.get("iv", UNDEFINED), horizon_days=c.get("horizonDays", UNDEFINED),
            nodes=[_receptor(x) or LevelInput(strike=g(x, "strike"),
                                             concentration=g(x, "concentration"),
                                             side=g(x, "side"), net_gex=g(x, "netGex"))
                   for x in _lista(c.get("nodes", UNDEFINED))],
            scores=_receptor(sc) or SubScores(**{k: g(sc, j) for k, j in (
                ("aggression", "aggression"), ("conviction", "conviction"),
                ("unusuality", "unusuality"), ("structure", "structure"),
                ("iv_context", "ivContext"), ("validation", "validation"))}),
            regime=c.get("regime", UNDEFINED), callvpct=c.get("callPct", UNDEFINED),
            hit_rate=c.get("hitRate", UNDEFINED), low_liquidity=c.get("lowLiquidity", UNDEFINED),
            # `biasPct` → `bias_pct`, como el `avgIv` de arriba: el corpus habla
            # camelCase porque viene de su archivo, y `stores.calibration_for`
            # —el único productor real de este dict— escribe snake_case.
            calibration=({("bias_pct" if k == "biasPct" else k): v
                          for k, v in c["calibration"].items()}
                         if isinstance(c.get("calibration", UNDEFINED), dict)
                         else c.get("calibration", UNDEFINED)))
        s = lambda x: [x.kind, r6(x.target), r6(x.change_pct), r6(x.probability),
                       x.driver]
        return {
            "horizonDays": r6(r.horizon_days), "spot": r6(r.spot), "iv": r6(r.iv),
            "bear": s(r.bear), "base": s(r.base), "bull": s(r.bull),
            "score": r6(r.score), "active": r.active, "confidence": r6(r.confidence),
            "direction": r.direction, "summary": r.summary, "caveat": r.caveat,
            "calibration": [r.calibration["applied"], r6(r.calibration["shift_pct"]),
                            r.calibration["samples"]],
            "nLevels": len(r.levels),
            "levels": [[r6(l.strike), r6(l.touch), r6(l.band), r6(l.magnet), l.side]
                       for l in r.levels[:3]],
        }
    compara("prediction", i, protegido(_caso), v)

# ─────────────────────────────────── risk ─────────────────────────────────
for i, (c, v) in enumerate(zip(C["risk"], V["risk"])):
    def _caso(c=c):
        p = c.get("profile", UNDEFINED)
        # Sin `_receptor`: su `sizeFlow` lee el perfil con encadenamiento
        # opcional (`safe(profile?.accountSize)`), así que un `null` NO lanza.
        perfil = RiskProfile(account_size=g(p, "accountSize"),
                             tolerance_pct=g(p, "tolerancePct"))
        fila = _fila(c["row"])
        r = size_flow(fila, perfil, c.get("horizonDays", UNDEFINED),
                      low_liquidity=g(c.get("ctx", UNDEFINED), "lowLiquidity"))
        b = budgets_of(perfil)
        return {
            "maxContracts": r6(r.max_contracts), "binding": r.binding,
            "costPerContract": r6(r.cost_per_contract),
            "totalCost": r6(r.total_cost),
            "costPctOfAccount": r6(r.cost_pct_of_account),
            "burnDays": r6(r.burn_days),
            "thetaBurnPerContract": r6(r.theta_burn_per_contract),
            "totalBurn": r6(r.total_burn),
            "burnPctOfAccount": r6(r.burn_pct_of_account),
            "fullyDecays": r.fully_decays, "blocked": r.blocked,
            "tradeable": is_tradeable_idea(fila),
            # Se respeta la ARIDAD: su `cap` tiene default, y en JS el default
            # solo entra con `undefined`. Un caso sin `cap` llama con un
            # argumento; uno con `cap: null` llama con dos y el `null` manda.
            "cercano": (within_moneyness(fila, c["cap"]) if "cap" in c
                        else within_moneyness(fila)),
            "budgets": [r6(b.premium), r6(b.theta)],
        }
    compara("risk", i, protegido(_caso), v)


# ── Reporte ────────────────────────────────────────────────────────────────
print(f"  {n} casos · ivcontext + gex + prediction + risk")
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
if os.environ.get("MOTOR2_LISTA"):
    # `MOTOR2_LISTA=prediction` (o `=1` para todos): vuelca caso por caso, que
    # es lo único que sirve para cerrar divergencias una a una.
    filtro = os.environ["MOTOR2_LISTA"]
    print("\n  · DETALLE")
    for b, ruta, s, m in fallos:
        if filtro in ("1", b.split("#")[0]):
            print(f"      {b}{ruta:<24} víctor={str(s)[:70]!r} port={str(m)[:70]!r}")
if _DONDE:
    print("\n  · DÓNDE LANZA EL PORT (MOTOR2_DEBUG)")
    for k, c in _DONDE.most_common(12):
        print(f"      {c:>4}  {k}")

BASE = pathlib.Path(__file__).with_name("_diffmotor2_base.json")
conocidos = set(json.loads(BASE.read_text())) if BASE.exists() else set()
if os.environ.get("MOTOR2_CONGELA"):
    BASE.write_text(json.dumps(sorted(casos_malos), indent=0))
    print(f"  · línea base actualizada: {len(casos_malos)} casos declarados")
    conocidos = casos_malos

nuevos = sorted(casos_malos - conocidos)
curados = sorted(conocidos - casos_malos)
if nuevos:
    print(f"\n  ✗ {len(nuevos)} DIVERGENCIA(S) NUEVA(S) sin declarar: {nuevos[:8]}")
if curados:
    print(f"\n  ! {len(curados)} declarado(s) que ya NO divergen — actualiza la "
          f"línea base (MOTOR2_CONGELA=1): {curados[:8]}")

print(f"\n  {n - len(casos_malos)}/{n} casos idénticos"
      + (f" · {len(casos_malos)} declarados" if casos_malos else " · sin diferencias"))
sys.exit(1 if (nuevos or curados) else 0)
