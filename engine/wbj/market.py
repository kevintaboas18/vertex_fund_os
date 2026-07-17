"""Market & Growth category scorer (20 pts) — deterministic.

Completa el engine de Victor para Market siguiendo
Cerebro/03_market_analysis/SCORING.md. Sin LLM. Lo que requiere evidencia
externa que no tenemos (tamaño de mercado/TAM, catalizadores narrativos) se
deja NOT_SCORABLE con honestidad; lo que sale de los fundamentales de EDGAR
(pista de crecimiento, apalancamiento operativo) y de estimados de consenso
opcionales (revisiones) se puntúa con anclajes.

Dimensiones: TAM y viento de cola (5) · Revisiones (4) · Catalizadores (4)
· Pista de crecimiento y captura de share (4) · Apalancamiento operativo (3).
"""

from __future__ import annotations

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension, anchor_score

_A_CAGR = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.0), (0.25, 9.0), (0.40, 10.0)]
_A_YOY = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.0), (0.25, 9.0), (0.45, 10.0)]
_A_OPLEV = [(-2.0, 0.0), (0.0, 3.0), (1.0, 5.0), (1.5, 8.0), (2.5, 10.0)]      # margen incremental / margen actual
_A_EGROWTH = [(-0.15, 0.0), (0.0, 3.0), (0.12, 6.5), (0.30, 10.0)]            # crecimiento de EPS estimado
_A_ANALYST = [(0.0, 2.0), (0.05, 5.0), (0.15, 8.0), (0.30, 10.0)]            # upside vs target medio de analistas


def _sv(x, anchors):
    if x is None:
        return Value.null(NullState.MISSING, unit="score", warnings=["MISSING"])
    return Value.of(anchor_score(x, anchors), unit="score", evidence_class=EvidenceClass.C)


def _ns(reason):
    return Value.null(NullState.NOT_SCORABLE, unit="score", warnings=[reason])


def _dim(name, max_points, scores):
    w = 1.0 / len(scores)
    return Dimension(name=name, max_points=max_points, metric_scores=[(w, s) for s in scores])


def _series_cagr(series, years=5):
    rows = [r["val"] for r in series[-years:] if r.get("val") is not None]
    if len(rows) < 3 or rows[0] <= 0 or rows[-1] <= 0:
        return None
    return (rows[-1] / rows[0]) ** (1.0 / (len(rows) - 1)) - 1.0


def _latest_yoy(series):
    vals = [r["val"] for r in series if r.get("val") is not None]
    if len(vals) < 2 or vals[-2] == 0:
        return None
    return vals[-1] / vals[-2] - 1.0


def market_category(packet: dict, estimates: dict | None = None) -> Category:
    """Category 'Market & Growth' (max 20).
    packet: paquete EDGAR de Victor (annual.revenue, annual.operating_income...).
    estimates (opcional): {'eps_growth', 'analyst_upside'} de consenso — E-class."""
    a = (packet or {}).get("annual", {}) if packet else {}
    rev = a.get("revenue", []) or []
    op = a.get("operating_income", []) or []
    est = estimates or {}

    # TAM y viento de cola (5) — requiere evidencia de tamaño de mercado
    tam = _dim("TAM y viento de cola", 5.0, [_ns("requiere evidencia de tamaño de mercado (no inyectada)")])

    # Revisiones (4) — desde estimados de consenso si están disponibles
    eps_g = est.get("eps_growth")
    upside = est.get("analyst_upside")
    if eps_g is not None or upside is not None:
        revisions = _dim("Revisiones de earnings/ingresos", 4.0, [
            _sv(eps_g, _A_EGROWTH) if eps_g is not None else _ns("sin crecimiento de EPS estimado"),
            _sv(upside, _A_ANALYST) if upside is not None else _ns("sin target de analistas")])
    else:
        revisions = _dim("Revisiones de earnings/ingresos", 4.0, [_ns("requiere consenso timestamped (>=5 estimados)")])

    # Catalizadores (4) — narrativos/evento; no puntuables deterministamente
    catalysts = _dim("Catalizadores", 4.0, [_ns("catalizadores requieren evidencia de eventos (no puntuable sin datos)")])

    # Pista de crecimiento y captura de share (4) — desde EDGAR
    rev_cagr = _series_cagr(rev, 5)
    rev_yoy = _latest_yoy(rev)
    runway = _dim("Pista de crecimiento", 4.0, [
        _sv(rev_cagr, _A_CAGR) if rev_cagr is not None else _ns("CAGR de ingresos requiere >=3 años"),
        _sv(rev_yoy, _A_YOY) if rev_yoy is not None else _ns("YoY de ingresos requiere 2 años")])

    # Apalancamiento operativo (3) — margen incremental desde EDGAR
    oplev = None
    rv = [r["val"] for r in rev if r.get("val") is not None]
    ov = [r["val"] for r in op if r.get("val") is not None]
    if len(rv) >= 2 and len(ov) >= 2 and (rv[-1] - rv[-2]) != 0:
        incr_margin = (ov[-1] - ov[-2]) / (rv[-1] - rv[-2])       # Δ margen operativo incremental
        cur_margin = (ov[-1] / rv[-1]) if rv[-1] else None
        oplev = (incr_margin / cur_margin) if (cur_margin and cur_margin != 0) else incr_margin
    opleverage = _dim("Apalancamiento operativo", 3.0, [
        _sv(oplev, _A_OPLEV) if oplev is not None else _ns("requiere 2 años de ingresos y op. income")])

    return Category(name="market", max_points=20.0,
                    dimensions=[tam, revisions, catalysts, runway, opleverage])
