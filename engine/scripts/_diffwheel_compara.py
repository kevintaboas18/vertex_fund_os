"""Comparador del diferencial de la WHEEL.

Corre el PORT sobre los mismos casos y lo coteja contra lo que devolvió su
TypeScript, bloque a bloque. Estricto: cualquier diferencia que no esté
declarada en `DECLARADAS` falla el script, y una declarada que ya no ocurre
también —significaría que la guarda desapareció en silencio—.

Se comparan también las CONSTANTES (presets, recortes, umbrales y los 40
símbolos del universo). Un preset con otro rango de delta elige otros strikes y
ningún caso lo delataría, porque los dos lados usarían cada uno el suyo.
"""
import json
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wbj.tito.earnings import earnings_flag, estimate_next_earnings   # noqa: E402
from wbj.tito.levels import Level, LevelSource                        # noqa: E402
from wbj.tito.wheel import (                                          # noqa: E402
    HAIRCUT, MAX_SPREAD_PCT, MIN_OI, WHEEL_PRESETS, CandidatesInput, ChainQuote,
    ScoreInput, atm_iv, liquidity_block, pick_premium, score_candidate,
    spread_pct_of, wheel_candidates, wheel_metrics)
from wbj.tito.wheel_universe import (                                 # noqa: E402
    WHEEL_UNIVERSE, afford_of, sort_by_afford_then_score)

C = json.load(open(os.environ["WH_CASOS"]))
V = json.load(open(os.environ["WH_OUT"]))


def r6(x):
    """Mismo redondeo y misma convención de no finitos que el lado JS."""
    if isinstance(x, bool) or x is None or not isinstance(x, (int, float)):
        return x
    f = float(x)
    if f != f:
        return "NaN"
    if math.isinf(f):
        return "Inf" if f > 0 else "-Inf"
    y = f * 1e6
    if math.isinf(y):
        return "Inf" if y > 0 else "-Inf"
    return round(y) / 1e6


def d6(x):
    if isinstance(x, (list, tuple)):
        return [d6(v) for v in x]
    if isinstance(x, dict):
        return {k: d6(v) for k, v in x.items()}
    return r6(x)


def protegido(fn):
    """Como su runner: `{"ERROR": clase}` si lanza, para poder comparar también
    los casos en que los dos lados se caen (y los que solo se cae uno)."""
    try:
        return d6(fn())
    except Exception as e:                       # noqa: BLE001
        return {"ERROR": type(e).__name__}


def _nivel(d):
    s = d["sources"]
    return Level(price=d["price"], kind=d["kind"], strength=d["strength"],
                 distance_pct=d["distancePct"], flipped=d["flipped"], why=d["why"],
                 sources=LevelSource(touches=s["touches"], last_touch=s["lastTouch"],
                                      open_interest=s["openInterest"],
                                      notional=s["notional"],
                                      flow_premium=s["flowPremium"],
                                      net_gex=s["netGex"]))


def _quote(q):
    return ChainQuote(strike=q["strike"], expiration=q["expiration"], dte=q["dte"],
                      bid=q["bid"], ask=q["ask"], last_trade=q["lastTrade"],
                      open_interest=q["openInterest"])


#: bloque → por qué difiere a propósito. Ninguna por ahora: la única
#: divergencia declarada de la Wheel (`allow_missing_quote`) está APAGADA por
#: defecto, y el diferencial la ejercita con su valor literal.
DECLARADAS: dict[str, str] = {}


def dif(a, b, ruta=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append((f"{ruta}.{k}", "<ausente>", b[k]))
            elif k not in b:
                out.append((f"{ruta}.{k}", a[k], "<ausente>"))
            else:
                out += dif(a[k], b[k], f"{ruta}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [(f"{ruta}[]", f"{len(a)} elementos", f"{len(b)} elementos")]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += dif(x, y, f"{ruta}[{i}]")
        return out
    return [] if a == b else [(ruta or ".", a, b)]


fallos, n = [], 0


def revisa(bloque, i, suyo, mio):
    global n
    n += 1
    ds = dif(suyo, mio)
    if ds:
        fallos.append((bloque, i, ds))


# ── constantes ───────────────────────────────────────────────────────────────
mias = d6({
    "presets": {k: {"id": p.id, "label": p.label, "deltaMin": p.delta_min,
                    "deltaMax": p.delta_max, "dteMin": p.dte_min, "dteMax": p.dte_max,
                    "takeProfitPct": p.take_profit_pct, "rollDte": p.roll_dte,
                    "explain": p.explain}
                for k, p in WHEEL_PRESETS.items()},
    "haircut": HAIRCUT, "maxSpreadPct": MAX_SPREAD_PCT, "minOi": MIN_OI,
    "universo": [{"ticker": s.ticker, "tier": s.tier, "razon": s.razon}
                 for s in WHEEL_UNIVERSE],
})
revisa("constantes", 0, V["constantes"], mias)

# ── wheelMetrics ─────────────────────────────────────────────────────────────
for i, c in enumerate(C["metrics"]):
    def _c(c=c):
        m = wheel_metrics(strike=c["strike"], price=c["price"], spot=c["spot"],
                          dte=c["dte"], iv=c["iv"])
        return {"credit": m.credit, "collateral": m.collateral,
                "returnPct": m.return_pct, "annualizedPct": m.annualized_pct,
                "breakeven": m.breakeven, "cushionPct": m.cushion_pct,
                "probExpireWorthless": m.prob_expire_worthless}
    revisa("metrics", i, V["metrics"][i], protegido(_c))

# ── pickPremium ──────────────────────────────────────────────────────────────
for i, c in enumerate(C["premium"]):
    def _c(c=c):
        p = pick_premium(bid=c["bid"], ask=c["ask"], last_trade=c["lastTrade"],
                         model=c["model"])
        return None if p is None else {"price": p.price, "source": p.source, "raw": p.raw}
    revisa("premium", i, V["premium"][i], protegido(_c))

# ── spreadPctOf / liquidityBlock ─────────────────────────────────────────────
for i, c in enumerate(C["spread"]):
    revisa("spread", i, V["spread"][i], protegido(lambda c=c: spread_pct_of(c["bid"], c["ask"])))
for i, c in enumerate(C["liquidity"]):
    revisa("liquidity", i, V["liquidity"][i],
           protegido(lambda c=c: liquidity_block(c["bid"], c["ask"], c["openInterest"])))

# ── scoreCandidate ───────────────────────────────────────────────────────────
for i, c in enumerate(C["score"]):
    def _c(c=c):
        s = score_candidate(ScoreInput(
            annualized_pct=c["annualizedPct"], iv_rank=c["ivRank"], strike=c["strike"],
            spot=c["spot"], cushion_pct=c["cushionPct"],
            supports=[_nivel(x) for x in c["supports"]],
            open_interest=c["openInterest"], spread_pct=c["spreadPct"],
            earnings=c["earnings"]))
        parte = lambda p: {"points": p.points, "max": p.max, "band": p.band, "why": p.why}
        return {"total": s.total, "annualized": parte(s.annualized),
                "ivRank": parte(s.iv_rank), "cushion": parte(s.cushion),
                "liquidity": parte(s.liquidity), "earnings": parte(s.earnings)}
    revisa("score", i, V["score"][i], protegido(_c))

# ── atmIv ────────────────────────────────────────────────────────────────────
for i, c in enumerate(C["atmIv"]):
    def _c(c=c):
        class R:
            def __init__(self, s, v):
                self.strike, self.iv = s, v
        return atm_iv([R(x["strike"], x["iv"]) for x in c["rows"]], c["spot"])
    revisa("atmIv", i, V["atmIv"][i], protegido(_c))

# ── wheelCandidates ──────────────────────────────────────────────────────────
for i, c in enumerate(C["candidates"]):
    def _c(c=c):
        filas = wheel_candidates(CandidatesInput(
            ticker=c["ticker"], spot=c["spot"],
            quotes=[_quote(q) for q in c["quotes"]],
            preset=WHEEL_PRESETS[c["preset"]], iv_rank=c["ivRank"],
            supports=[_nivel(x) for x in c["supports"]], earnings=c["earnings"],
            fallback_iv=c["fallbackIv"]))
        return {"n": len(filas), "filas": [[
            f.strike, f.expiration, f.dte, f.delta, f.iv, f.iv_source,
            f.open_interest, f.spread_pct, f.blocked, f.block_reason,
            f.premium and [f.premium.price, f.premium.source, f.premium.raw],
            f.metrics and [f.metrics.credit, f.metrics.collateral,
                           f.metrics.return_pct, f.metrics.annualized_pct,
                           f.metrics.breakeven, f.metrics.cushion_pct,
                           f.metrics.prob_expire_worthless],
            f.score and [f.score.total, f.score.annualized.points,
                         f.score.iv_rank.points, f.score.cushion.points,
                         f.score.liquidity.points, f.score.earnings.points],
        ] for f in filas[:8]]}
    revisa("candidates", i, V["candidates"][i], protegido(_c))

# ── affordOf / sortByAffordThenScore ─────────────────────────────────────────
for i, c in enumerate(C["afford"]):
    def _c(c=c):
        from wbj.tito.wheel import WheelCandidate, WheelMetrics, WheelScore, ScorePart

        def _sc(s):
            if s is None:
                return None
            p = ScorePart(points=0, max=0, band="", why="")
            return WheelScore(total=s["total"], annualized=p, iv_rank=p, cushion=p,
                              liquidity=p, earnings=p)

        filas = [WheelCandidate(
            ticker="X", strike=r["strike"], expiration="2026-09-18", dte=30, spot=100,
            delta=-0.2, iv=0.5, iv_source="implicita", open_interest=500, spread_pct=5,
            premium=None, blocked=r["blocked"], block_reason=None,
            metrics=WheelMetrics(credit=0, collateral=r["strike"] * 100, return_pct=0,
                                 annualized_pct=0, breakeven=0, cushion_pct=0,
                                 prob_expire_worthless=0),
            score=_sc(r["score"])) for r in c["rows"]]
        indices = {id(f): k for k, f in enumerate(filas)}
        ordenadas = sort_by_afford_then_score(filas, c["cash"])
        return {
            "orden": [indices[id(cand)] for cand, _ in ordenadas],
            "afford": [[a.affordable, a.shortfall] for _, a in ordenadas],
            "sueltos": [[afford_of(f, c["cash"]).affordable,
                         afford_of(f, c["cash"]).shortfall] for f in filas],
        }
    revisa("afford", i, V["afford"][i], protegido(_c))

# ── estimateNextEarnings / earningsFlag ──────────────────────────────────────
for i, c in enumerate(C["earnings"]):
    def _c(c=c):
        ahora = datetime.fromisoformat(c["now"].replace("Z", "+00:00"))
        return estimate_next_earnings(c["filings"], ahora)
    revisa("earnings", i, V["earnings"][i], protegido(_c))
for i, c in enumerate(C["earningsFlag"]):
    revisa("earningsFlag", i, V["earningsFlag"][i],
           protegido(lambda c=c: earnings_flag(c["next"], c["expiration"], c["frontSkew"])))

# ── informe ──────────────────────────────────────────────────────────────────
sin_declarar = [f for f in fallos if f[0] not in DECLARADAS]
declaradas = [f for f in fallos if f[0] in DECLARADAS]

for bloque, i, ds in sin_declarar[:14]:
    print(f"  ✗ {bloque}#{i}")
    for ruta, suyo, mio in ds[:5]:
        print(f"      {ruta}\n        víctor: {json.dumps(suyo)[:180]}"
              f"\n        port  : {json.dumps(mio)[:180]}")
if len(sin_declarar) > 14:
    print(f"  … y {len(sin_declarar) - 14} caso(s) más")

if declaradas:
    print("\n  · DIVERGENCIAS DECLARADAS")
    for g in sorted({DECLARADAS[f[0]] for f in declaradas}):
        print(f"      {g}")

print(f"\n  {n - len(fallos)}/{n} idénticos a su wheel.ts / wheelAfford.ts / earnings.ts"
      + (f" · {len(sin_declarar)} SIN DECLARAR" if sin_declarar else " · sin diferencias"))
sys.exit(1 if sin_declarar else 0)
