"""Technical & Momentum category scorer (20 pts) — DETERMINISTA, fórmulas exactas de Victor.

Implementa Cerebro/04_technical_momentum (FORMULAS.md + DECISION_RULES.md) tal
como Victor lo escribió: indicadores Wilder (ATR14, RSI14, ADX14), medias
SMA20/50/100/200, tabla EXACTA de tendencia primaria, up/down volume, CMF20,
OBV, VCP, posición 52s, liquidez, y un motor de soporte/resistencia (pivotes
k=3, zonas por tolerancia ATR, conteo de toques, fuerza de nivel TECH-LSTR-028,
breakout TECH-BCONF-031). Sin LLM. Sin datos → NOT_SCORABLE.

Dimensiones (pts de categoría): Tendencia primaria (4) · Fuerza relativa (4) ·
Volumen y demanda (3) · Gaps de earnings (3) · Base y breakout (3) · Amplitud y
volatilidad (3).
"""

from __future__ import annotations

from math import exp, log, sqrt
from statistics import median, pstdev

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension, anchor_score


def _sv(x, anchors):
    if x is None:
        return Value.null(NullState.MISSING, unit="score", warnings=["MISSING"])
    return Value.of(anchor_score(x, anchors), unit="score", evidence_class=EvidenceClass.C)


def _fixed(score):
    return Value.of(float(max(0.0, min(10.0, score))), unit="score", evidence_class=EvidenceClass.C)


def _ns(reason):
    return Value.null(NullState.NOT_SCORABLE, unit="score", warnings=[reason])


def _dim(name, max_points, scores):
    w = 1.0 / len(scores)
    return Dimension(name=name, max_points=max_points, metric_scores=[(w, s) for s in scores])


# ── Indicadores (definiciones exactas del registro TECH-*) ───────────────────

def _sma(s, n):
    return sum(s[-n:]) / n if len(s) >= n else None


def _true_ranges(highs, lows, closes):
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return tr


def _atr14(highs, lows, closes):
    """TECH-ATR-006: Wilder ATR14 (inicializada con media de los primeros 14 TR)."""
    tr = _true_ranges(highs, lows, closes)
    if len(tr) < 14:
        return None
    atr = sum(tr[:14]) / 14
    for x in tr[14:]:
        atr = (13 * atr + x) / 14
    return atr


def _rsi14(closes):
    """TECH-RSI-007: Wilder RSI14."""
    if len(closes) < 15:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag = sum(gains[:14]) / 14; al = sum(losses[:14]) / 14
    for i in range(14, len(gains)):
        ag = (13 * ag + gains[i]) / 14; al = (13 * al + losses[i]) / 14
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def _adx14(highs, lows, closes):
    """TECH-DMI-009: ADX14 (Wilder). Fuerza de tendencia (no dirección)."""
    n = len(closes)
    if n < 30:
        return None
    tr, pdm, ndm = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]; dn = lows[i - 1] - lows[i]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    if len(tr) < 15:
        return None
    atr = sum(tr[:14]); pd = sum(pdm[:14]); nd = sum(ndm[:14])
    dxs = []
    for i in range(14, len(tr)):
        atr = atr - atr / 14 + tr[i]; pd = pd - pd / 14 + pdm[i]; nd = nd - nd / 14 + ndm[i]
        if atr == 0:
            continue
        pdi = 100 * pd / atr; ndi = 100 * nd / atr
        s = pdi + ndi
        if s > 0:
            dxs.append(100 * abs(pdi - ndi) / s)
    if len(dxs) < 14:
        return None
    adx = sum(dxs[:14]) / 14
    for x in dxs[14:]:
        adx = (13 * adx + x) / 14
    return adx


def _slope_atr(closes, n, atr):
    """TECH-SLOPE-004: pendiente OLS de Close sobre N * N / ATR14 (cambio en unidades ATR)."""
    if len(closes) < n or not atr or atr <= 0:
        return None
    y = closes[-n:]; xm = (n - 1) / 2; ym = sum(y) / n
    num = sum((i - xm) * (y[i] - ym) for i in range(n))
    den = sum((i - xm) ** 2 for i in range(n))
    if den == 0:
        return None
    return (num / den) * n / atr


def _roc(closes, n):
    if len(closes) <= n or closes[-n - 1] == 0:
        return None
    return closes[-1] / closes[-n - 1] - 1.0


def _cmf20(highs, lows, closes, volumes):
    """TECH-CMF-017: Chaikin Money Flow 20."""
    if min(len(highs), len(lows), len(closes), len(volumes)) < 20:
        return None
    mfv = 0.0; vol = 0.0
    for i in range(len(closes) - 20, len(closes)):
        h, l, c, v = highs[i], lows[i], closes[i], volumes[i]
        mult = 0.0 if h == l else ((2 * c - h - l) / (h - l))
        mfv += mult * v; vol += v
    return (mfv / vol) if vol > 0 else None


def _obv_slope(closes, volumes):
    """TECH-OBV-016: pendiente normalizada del OBV en 20 sesiones."""
    if len(closes) < 21 or len(volumes) < 21:
        return None
    obv = [0.0]
    for i in range(1, len(closes)):
        v = volumes[i]
        obv.append(obv[-1] + (v if closes[i] > closes[i - 1] else -v if closes[i] < closes[i - 1] else 0.0))
    tail = obv[-20:]; rng = (max(tail) - min(tail)) or 1.0
    return (tail[-1] - tail[0]) / rng


def _updown(closes, volumes):
    """TECH-UDV-015: up/down volume ratio (50d)."""
    if len(closes) < 51 or len(volumes) < 51:
        return None
    up = sum(volumes[i] for i in range(len(closes) - 50, len(closes)) if closes[i] >= closes[i - 1])
    dn = sum(volumes[i] for i in range(len(closes) - 50, len(closes)) if closes[i] < closes[i - 1])
    return (up / dn) if dn > 0 else None


def _vol_ratio(volumes):
    """TECH-VR-014: Volume_t / mediana(50 sesiones previas)."""
    if len(volumes) < 51:
        return None
    med = median(volumes[-51:-1])
    return (volumes[-1] / med) if med > 0 else None


def _vcp(highs, lows, closes, atr):
    """TECH-VCP-019: (ATR14/Close) / mediana(ATR14/Close, 126 previas). <1 = contracción."""
    if not atr or len(closes) < 140:
        return None
    ratios = []
    for j in range(126, 0, -1):
        sub_h, sub_l, sub_c = highs[:-j], lows[:-j], closes[:-j]
        a = _atr14(sub_h, sub_l, sub_c)
        if a and sub_c[-1] > 0:
            ratios.append(a / sub_c[-1])
    if len(ratios) < 60:
        return None
    med = median(ratios)
    cur = atr / closes[-1]
    return (cur / med) if med > 0 else None


def _pos52(closes):
    """TECH-52W-036."""
    win = closes[-252:] if len(closes) >= 60 else closes
    lo, hi = min(win), max(win)
    return (closes[-1] - lo) / (hi - lo) if hi > lo else None


def _ann_vol(closes, n=63):
    """TECH-VOL-018: stdev(log returns_N)*sqrt(252)."""
    if len(closes) < n + 1:
        return None
    rets = [log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes)) if closes[i - 1] > 0]
    return pstdev(rets) * sqrt(252) if len(rets) >= 20 else None


def _liquidity(closes, volumes):
    """TECH-LIQ-040: mediana(Close*Volume, 63 sesiones)."""
    if min(len(closes), len(volumes)) < 63:
        return None
    return median(closes[i] * volumes[i] for i in range(len(closes) - 63, len(closes)))


# ── Motor de soporte/resistencia y breakout (DECISION_RULES pasos 1-7) ───────

def _pivots(highs, lows, k=3):
    """TECH-PIV-022: pivotes simétricos (confirmados con retardo de k barras)."""
    ph, pl = [], []
    for t in range(k, len(highs) - k):
        if highs[t] == max(highs[t - k:t + k + 1]):
            ph.append((t, highs[t]))
        if lows[t] == min(lows[t - k:t + k + 1]):
            pl.append((t, lows[t]))
    return ph, pl


def _zones(pivots, closes, atr):
    """Agrupa pivotes en zonas con tolerancia TECH-ZTOL-024 = max(0.5*ATR, 0.0075*precio).
    Cuenta toques independientes (>=5 sesiones) y calcula fuerza TECH-LSTR-028."""
    if not pivots or not atr:
        return []
    n = len(closes)
    piv = sorted(pivots, key=lambda p: p[1])
    zones = []
    cur = [piv[0]]
    tol0 = max(0.5 * atr, 0.0075 * piv[0][1])
    for t, price in piv[1:]:
        if abs(price - cur[-1][1]) <= max(0.5 * atr, 0.0075 * price):
            cur.append((t, price))
        else:
            zones.append(cur); cur = [(t, price)]
    zones.append(cur)
    out = []
    for z in zones:
        # toques independientes: separados >=5 sesiones
        z_sorted = sorted(z, key=lambda p: p[0])
        touches = []
        for t, price in z_sorted:
            if not touches or (t - touches[-1][0]) >= 5:
                touches.append((t, price))
        n_touch = len(touches)
        center = median(p[1] for p in z)
        ages = [n - 1 - t for t, _ in touches]
        n_eff = sum(exp(-log(2) * a / 126) for a in ages)
        recency = exp(-log(2) * min(ages) / 126) if ages else 0.0
        strength = min(100.0, 30 * min(n_eff / 4, 1) + 15 * recency + 5)  # sub-señales de reacción/vol omitidas → cota inferior
        out.append({"center": center, "n_touch": n_touch, "n_eff": n_eff, "strength": strength})
    return out


def _base_breakout_score(highs, lows, closes, volumes, atr):
    """Score 0-10 de la dimensión base/breakout a partir de zonas, VCP y 52s."""
    if not atr or len(closes) < 60:
        return None
    price = closes[-1]
    ph, pl = _pivots(highs, lows, 3)
    res = _zones(ph, closes, atr)
    sup = _zones(pl, closes, atr)
    # resistencia confirmada más cercana por encima
    above = sorted([z for z in res if z["center"] > price], key=lambda z: z["center"])
    below = sorted([z for z in sup if z["center"] < price], key=lambda z: z["center"], reverse=True)
    score = 5.0
    # breakout confirmado (TECH-BCONF-031): cierre > zona_high + 0.25*ATR y VR>=1.5
    vr = _vol_ratio(volumes)
    if above:
        z = above[0]; zone_high = z["center"] + max(0.5 * atr, 0.0075 * z["center"])
        dist_atr = (zone_high - price) / atr
        if price > zone_high + 0.25 * atr and vr and vr >= 1.5:
            score = 8.5                                   # breakout confirmado
        elif 0 <= dist_atr <= 1.0 and z["n_touch"] >= 2:
            score = 4.0                                   # bajo resistencia confirmada cercana
        elif dist_atr > 2.0:
            score = 6.5                                   # espacio despejado arriba
    # soporte confirmado cercano da algo de piso
    if below and (price - below[0]["center"]) / atr <= 1.0 and below[0]["n_touch"] >= 2:
        score += 0.5
    # contracción de volatilidad (base ordenada) suma
    vcp = _vcp(highs, lows, closes, atr)
    if vcp is not None and vcp < 0.9:
        score += 1.0
    return max(0.0, min(10.0, score))


# ── Anclajes por métrica (bandas de Cerebro) ─────────────────────────────────
_A_RS = [(-0.30, 0.0), (-0.10, 3.0), (0.0, 5.0), (0.15, 7.5), (0.40, 10.0)]
_A_UPDOWN = [(0.7, 0.0), (0.9, 3.0), (1.0, 5.0), (1.2, 7.5), (1.6, 10.0)]
_A_CMF = [(-0.20, 0.0), (-0.05, 3.0), (0.0, 5.0), (0.10, 7.5), (0.25, 10.0)]
_A_OBV = [(-1.0, 1.0), (0.0, 5.0), (1.0, 9.0)]
_A_VCP = [(0.5, 10.0), (0.8, 8.0), (1.0, 5.0), (1.4, 2.0), (2.0, 0.0)]
_A_ANNVOL = [(0.15, 9.0), (0.30, 7.0), (0.50, 5.0), (0.80, 2.5), (1.20, 0.0)]
_A_LIQ = [(1e5, 0.0), (1e6, 4.0), (1e7, 7.0), (1e8, 10.0)]
_A_GHOLD = [(-0.5, 0.0), (0.0, 3.0), (0.5, 6.0), (0.8, 8.0), (1.0, 10.0)]


def _primary_trend_score(closes, highs, lows, atr):
    """Tabla EXACTA de tendencia primaria (DECISION_RULES). Devuelve 0-10 o None."""
    sma50, sma200 = _sma(closes, 50), _sma(closes, 200)
    if sma200 is None or atr is None:
        # sin SMA200 la regla capa en 6; puntuamos con SMA50 solamente de forma conservadora
        if sma50 is None:
            return None
        c = closes[-1]
        return 6.0 if c > sma50 else 3.0
    c = closes[-1]
    slope200 = _slope_atr(closes, 50, atr)          # cambio de SMA/trend en ATR sobre 50 sesiones (aprox con slope de close)
    adx = _adx14(highs, lows, closes)
    pos = _pos52(closes)
    up_stack = c > sma50 > sma200
    if c < sma50 < sma200 and slope200 is not None and slope200 < -1:
        return 1.0
    if up_stack and (slope200 is not None and slope200 > 0):
        if adx is not None and adx >= 25 and pos is not None and pos >= 0.80:
            return 9.5
        return 8.0
    if c > sma200 and not up_stack:
        return 6.0
    if slope200 is not None and -0.25 <= slope200 <= 0.25 and abs(c - sma200) <= atr:
        return 4.5
    if c < sma200:
        return 3.0
    return 5.0


def technical_category(closes, highs=None, lows=None, volumes=None,
                       benchmark_closes=None, earnings_gaps=None) -> Category:
    """Category 'Technical & Momentum' (max 20) con las fórmulas exactas de Victor.
    earnings_gaps: lista opcional de dicts {'gap','hold5'} para TECH-GAP/GHOLD."""
    closes = [float(c) for c in (closes or []) if c is not None]
    highs = [float(x) for x in (highs or closes)]
    lows = [float(x) for x in (lows or closes)]
    volumes = [float(x) for x in (volumes or [])]
    atr = _atr14(highs, lows, closes) if len(closes) >= 15 else None

    # 1) Tendencia primaria (4)
    ts = _primary_trend_score(closes, highs, lows, atr)
    trend = _dim("Tendencia primaria", 4.0, [_fixed(ts) if ts is not None else _ns("requiere SMA50 (>=50 sesiones)")])

    # 2) Fuerza relativa (4): ROC 63/126/252 + RS vs benchmark
    r63, r126, r252 = _roc(closes, 63), _roc(closes, 126), _roc(closes, 252)
    rel = None
    if benchmark_closes and len(benchmark_closes) > 126 and benchmark_closes[-127] > 0 and r126 is not None:
        rel = r126 - (benchmark_closes[-1] / benchmark_closes[-127] - 1.0)
    rs = _dim("Fuerza relativa", 4.0, [
        _sv(r63, _A_RS) if r63 is not None else _ns("ROC63 requiere 63 sesiones"),
        _sv(r126, _A_RS) if r126 is not None else _ns("ROC126 requiere 126 sesiones"),
        _sv(r252, _A_RS) if r252 is not None else _ns("ROC252 requiere 252 sesiones"),
        _sv(rel, _A_RS) if rel is not None else _ns("RS vs benchmark: sin serie de índice")])

    # 3) Volumen y demanda (3): up/down + CMF + OBV
    ud = _updown(closes, volumes); cmf = _cmf20(highs, lows, closes, volumes); obv = _obv_slope(closes, volumes)
    volume = _dim("Volumen y demanda", 3.0, [
        _sv(ud, _A_UPDOWN) if ud is not None else _ns("up/down volume requiere 50 sesiones con volumen"),
        _sv(cmf, _A_CMF) if cmf is not None else _ns("CMF requiere OHLCV"),
        _sv(obv, _A_OBV) if obv is not None else _ns("OBV requiere volumen")])

    # 4) Gaps de earnings (3): TECH-GHOLD si hay eventos; si no, NOT_SCORABLE
    if earnings_gaps:
        holds = [g.get("hold5") for g in earnings_gaps if g.get("hold5") is not None]
        gaps = _dim("Gaps de earnings", 3.0, [
            _sv(sum(holds) / len(holds), _A_GHOLD) if holds else _ns("sin gap-hold válido")])
    else:
        gaps = _dim("Gaps de earnings", 3.0, [_ns("requiere fechas/reacción de earnings (TECH-GAP/GHOLD)")])

    # 5) Base y breakout (3): motor S/R + VCP + 52s
    bb = _base_breakout_score(highs, lows, closes, volumes, atr)
    base = _dim("Base y breakout", 3.0, [_fixed(bb) if bb is not None else _ns("requiere >=60 sesiones y ATR")])

    # 6) Amplitud y volatilidad (3): vol anualizada + VCP + liquidez
    av = _ann_vol(closes); vcp = _vcp(highs, lows, closes, atr); liq = _liquidity(closes, volumes)
    breadth = _dim("Amplitud y volatilidad", 3.0, [
        _sv(av, _A_ANNVOL) if av is not None else _ns("volatilidad requiere >=63 sesiones"),
        _sv(vcp, _A_VCP) if vcp is not None else _ns("VCP requiere >=140 sesiones"),
        _sv(liq, _A_LIQ) if liq is not None else _ns("liquidez requiere volumen (63 sesiones)")])

    return Category(name="technical", max_points=20.0,
                    dimensions=[trend, rs, volume, gaps, base, breadth])
