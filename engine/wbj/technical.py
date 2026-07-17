"""Technical & Momentum category scorer (20 pts) — deterministic, from OHLCV.

Completa el engine de Victor para la categoría Technical siguiendo la
arquitectura de dimensiones de Cerebro/04_technical_momentum/SCORING.md, con el
mismo estilo de anclajes que `quick.py`. Sin LLM: todos los scores salen de la
serie de precios/volumen. Sin datos suficientes → NOT_SCORABLE (nunca se inventa).

Dimensiones (puntos de categoría): Tendencia primaria (4) · Fuerza relativa (4)
· Volumen y demanda (3) · Comportamiento en gaps de earnings (3) · Base y
breakout (3) · Amplitud sectorial y volatilidad (3).
"""

from __future__ import annotations

from statistics import mean, pstdev

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension, anchor_score

# --- Anclajes (0-10), alineados con las bandas de Cerebro (defaults MVP) ---
_A_PCT_ABOVE = [(-0.15, 0.0), (-0.05, 3.0), (0.0, 5.0), (0.05, 7.0), (0.15, 10.0)]      # % sobre una media
_A_STACK = [(-0.10, 0.0), (-0.02, 3.0), (0.0, 5.0), (0.03, 7.5), (0.10, 10.0)]          # SMA50 vs SMA200
_A_SLOPE = [(-0.05, 0.0), (0.0, 4.0), (0.02, 7.0), (0.06, 10.0)]                        # pendiente SMA200 (20d)
_A_RET = [(-0.30, 0.0), (-0.10, 3.0), (0.0, 5.0), (0.15, 7.5), (0.40, 10.0)]            # retorno de momentum
_A_UPDOWN = [(0.7, 0.0), (0.9, 3.0), (1.0, 5.0), (1.2, 7.5), (1.6, 10.0)]               # volumen up/down
_A_OBV_SLOPE = [(-1.0, 1.0), (0.0, 5.0), (1.0, 9.0)]                                    # signo de pendiente OBV
_A_52W_POS = [(0.0, 1.0), (0.3, 5.0), (0.6, 8.0), (0.85, 9.5), (1.0, 7.0)]              # posición en rango 52s (cerca del techo = extendido)
_A_VOL_CONTRACT = [(0.5, 10.0), (0.8, 8.0), (1.0, 5.0), (1.4, 2.0), (2.0, 0.0)]         # ATR reciente / ATR previo (contracción = bueno)
_A_ANN_VOL = [(0.15, 9.0), (0.30, 7.0), (0.50, 5.0), (0.80, 2.5), (1.20, 0.0)]          # vol anualizada (controlada = mejor)
_A_DOLLAR_LIQ = [(1e5, 0.0), (1e6, 4.0), (1e7, 7.0), (1e8, 10.0)]                       # $ volumen diario medio


def _sv(x, anchors):
    if x is None:
        return Value.null(NullState.MISSING, unit="score", warnings=["MISSING"])
    return Value.of(anchor_score(x, anchors), unit="score", evidence_class=EvidenceClass.C)


def _ns(reason):
    return Value.null(NullState.NOT_SCORABLE, unit="score", warnings=[reason])


def _dim(name, max_points, scores):
    w = 1.0 / len(scores)
    return Dimension(name=name, max_points=max_points, metric_scores=[(w, s) for s in scores])


def _sma(series, n):
    return mean(series[-n:]) if len(series) >= n else None


def _ret(series, n):
    if len(series) <= n or series[-n - 1] <= 0:
        return None
    return series[-1] / series[-n - 1] - 1.0


def _obv_slope(closes, volumes):
    """Signo/normalizado de la pendiente del On-Balance-Volume en la ventana."""
    if len(closes) < 20 or len(volumes) < 20:
        return None
    obv = [0.0]
    for i in range(1, len(closes)):
        v = volumes[i] if i < len(volumes) else 0
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + v)
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - v)
        else:
            obv.append(obv[-1])
    tail = obv[-20:]
    rng = (max(tail) - min(tail)) or 1.0
    return (tail[-1] - tail[0]) / rng          # -1..1 aprox


def _atr(highs, lows, closes, n):
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(len(closes) - n, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return mean(trs) if trs else None


def technical_category(closes, highs=None, lows=None, volumes=None, benchmark_closes=None) -> Category:
    """Devuelve la Category 'Technical & Momentum' (max 20) desde OHLCV.
    closes/highs/lows/volumes: listas alineadas y ajustadas (idealmente ~252 sesiones)."""
    closes = [float(c) for c in (closes or []) if c is not None]
    highs = [float(x) for x in (highs or closes)]
    lows = [float(x) for x in (lows or closes)]
    volumes = [float(x) for x in (volumes or [])]
    price = closes[-1] if closes else None

    # 1) Tendencia primaria (4)
    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    m_close_sma50 = (price / sma50 - 1.0) if (sma50 and price) else None
    m_stack = (sma50 / sma200 - 1.0) if (sma50 and sma200) else None
    sma200_prev = mean(closes[-220:-20]) if len(closes) >= 220 else None
    m_slope = (sma200 / sma200_prev - 1.0) if (sma200 and sma200_prev) else None
    trend = _dim("Tendencia primaria", 4.0, [
        _sv(m_close_sma50, _A_PCT_ABOVE),
        _sv(m_stack, _A_STACK) if m_stack is not None else _ns("SMA200 requiere >=200 sesiones"),
        _sv(m_slope, _A_SLOPE) if m_slope is not None else _ns("pendiente SMA200 requiere >=220 sesiones")])

    # 2) Fuerza relativa (4)
    r63, r126, r252 = _ret(closes, 63), _ret(closes, 126), _ret(closes, 252)
    rel = None
    if benchmark_closes and len(benchmark_closes) > 126:
        br = benchmark_closes[-1] / benchmark_closes[-127] - 1.0 if benchmark_closes[-127] > 0 else None
        if r126 is not None and br is not None:
            rel = r126 - br
    rs = _dim("Fuerza relativa", 4.0, [
        _sv(r63, _A_RET), _sv(r126, _A_RET),
        _sv(r252, _A_RET) if r252 is not None else _ns("retorno 252d requiere 1 año"),
        _sv(rel, _A_RET) if rel is not None else _ns("RS vs benchmark: sin serie de índice")])

    # 3) Volumen y demanda institucional (3)
    updown = None
    if len(closes) >= 21 and len(volumes) >= 21:
        up = sum(volumes[i] for i in range(len(closes) - 20, len(closes)) if closes[i] >= closes[i - 1])
        dn = sum(volumes[i] for i in range(len(closes) - 20, len(closes)) if closes[i] < closes[i - 1])
        updown = (up / dn) if dn > 0 else (2.0 if up > 0 else None)
    obv_sl = _obv_slope(closes, volumes)
    volume = _dim("Volumen y demanda", 3.0, [
        _sv(updown, _A_UPDOWN) if updown is not None else _ns("sin volumen ajustado"),
        _sv(obv_sl, _A_OBV_SLOPE) if obv_sl is not None else _ns("OBV requiere volumen")])

    # 4) Comportamiento en gaps de earnings (3) — requiere fechas/eventos de earnings
    gaps = _dim("Gaps de earnings", 3.0, [_ns("requiere fechas y reacción de earnings (no inyectadas)")])

    # 5) Base y breakout (3)
    pos52 = None
    win = closes[-252:] if len(closes) >= 60 else closes
    if win:
        lo, hi = min(win), max(win)
        pos52 = (price - lo) / (hi - lo) if hi > lo else None
    atr_recent = _atr(highs, lows, closes, 14)
    atr_prev = _atr(highs[:-14], lows[:-14], closes[:-14], 14) if len(closes) >= 30 else None
    vol_contract = (atr_recent / atr_prev) if (atr_recent and atr_prev) else None
    base = _dim("Base y breakout", 3.0, [
        _sv(pos52, _A_52W_POS) if pos52 is not None else _ns("rango 52s insuficiente"),
        _sv(vol_contract, _A_VOL_CONTRACT) if vol_contract is not None else _ns("contracción ATR insuficiente")])

    # 6) Amplitud sectorial y calidad de volatilidad (3)
    ann_vol = None
    if len(closes) >= 30:
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(len(closes) - 60, len(closes)) if i > 0 and closes[i - 1] > 0]
        if len(rets) >= 20:
            ann_vol = pstdev(rets) * (252 ** 0.5)
    dollar_liq = (mean(volumes[-20:]) * price) if (len(volumes) >= 20 and price) else None
    breadth = _dim("Volatilidad y liquidez", 3.0, [
        _sv(ann_vol, _A_ANN_VOL) if ann_vol is not None else _ns("volatilidad requiere >=30 sesiones"),
        _sv(dollar_liq, _A_DOLLAR_LIQ) if dollar_liq is not None else _ns("liquidez requiere volumen")])

    return Category(name="technical", max_points=20.0,
                    dimensions=[trend, rs, volume, gaps, base, breadth])
