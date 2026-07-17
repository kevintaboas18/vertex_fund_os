"""Market & Growth category scorer (20 pts) — DETERMINISTA, fórmulas exactas de Victor.

Implementa Cerebro/03_market_analysis (FORMULAS.md + DECISION_RULES.md):
crecimiento de ingresos (MKT-CAGR), penetración/runway (MKT-RUN-010),
capacidad fundamental de crecimiento (MKT-GCAP-009 = reinvestment*ROIC),
apalancamiento/margen incremental (MKT-OPLEV-017/INCM-018), y revisiones de
consenso (MKT-REVBR-011 breadth, MKT-REVMAG-012 magnitud, MKT-SURP-014 sorpresa)
cuando hay estimados FMP. TAM (MKT-TAM-001, tiers de fuente) y catalizadores
(MKT-CAT-019) quedan NOT_SCORABLE sin evidencia — política de Victor. Sin LLM.

Dimensiones: TAM y viento de cola (5) · Revisiones (4) · Catalizadores (4) ·
Pista de crecimiento y captura de share (4) · Apalancamiento operativo (3).
"""

from __future__ import annotations

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension, anchor_score

_A_CAGR = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.0), (0.25, 9.0), (0.40, 10.0)]
_A_YOY = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.0), (0.25, 9.0), (0.45, 10.0)]
_A_OPLEV = [(-2.0, 0.0), (0.0, 3.0), (1.0, 5.0), (1.5, 8.0), (2.5, 10.0)]
_A_GCAP = [(0.0, 0.0), (0.05, 4.0), (0.10, 6.5), (0.18, 8.5), (0.30, 10.0)]        # reinvestment*ROIC
_A_EGROWTH = [(-0.15, 0.0), (0.0, 3.0), (0.12, 6.5), (0.30, 10.0)]
_A_REVMAG = [(-0.10, 0.0), (-0.02, 3.0), (0.0, 5.0), (0.03, 7.5), (0.10, 10.0)]    # magnitud de revisión
_A_BREADTH = [(0.0, 0.0), (0.4, 3.0), (0.5, 5.0), (0.7, 8.0), (0.9, 10.0)]         # % revisiones al alza
_A_SURP = [(-0.10, 0.0), (0.0, 5.0), (0.05, 8.0), (0.15, 10.0)]                    # sorpresa de earnings


def _sv(x, anchors):
    if x is None:
        return Value.null(NullState.MISSING, unit="score", warnings=["MISSING"])
    return Value.of(anchor_score(x, anchors), unit="score", evidence_class=EvidenceClass.C)


def _ns(reason):
    return Value.null(NullState.NOT_SCORABLE, unit="score", warnings=[reason])


def _dim(name, max_points, scores):
    w = 1.0 / len(scores)
    return Dimension(name=name, max_points=max_points, metric_scores=[(w, s) for s in scores])


def _cagr(series, years=5):
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
    estimates (FMP/consenso, todos opcionales): eps_growth, analyst_upside,
    revision_up, revision_total, consensus_now, consensus_prior, surprise,
    roic, reinvestment."""
    a = (packet or {}).get("annual", {}) if packet else {}
    rev = a.get("revenue", []) or []
    op = a.get("operating_income", []) or []
    est = estimates or {}

    # 1) TAM y viento de cola (5) — requiere fuente de tamaño de mercado (tiers)
    tam = _dim("TAM y viento de cola", 5.0, [_ns("TAM requiere fuente de tamaño de mercado (MKT-TAM-001, tiers 1-5)")])

    # 2) Revisiones (4): breadth (MKT-REVBR-011) + magnitud (MKT-REVMAG-012) + EPS estimado + sorpresa
    breadth = None
    if est.get("revision_total"):
        breadth = est["revision_up"] / est["revision_total"] if est.get("revision_up") is not None else None
    magnitude = None
    if est.get("consensus_prior"):
        magnitude = (est.get("consensus_now", 0) - est["consensus_prior"]) / abs(est["consensus_prior"])
    # Puntúa con los metrics DISPONIBLES (breadth/magnitud/EPS estimado/sorpresa).
    # Solo N/S si no hay ninguno — así 2 metrics reales no se pierden por el 70%.
    rev_scores = []
    if breadth is not None:
        rev_scores.append(_sv(breadth, _A_BREADTH))
    if magnitude is not None:
        rev_scores.append(_sv(magnitude, _A_REVMAG))
    elif est.get("eps_growth") is not None:
        rev_scores.append(_sv(est["eps_growth"], _A_EGROWTH))
    if est.get("surprise") is not None:
        rev_scores.append(_sv(est["surprise"], _A_SURP))
    revisions = _dim("Revisiones de earnings/ingresos", 4.0,
                     rev_scores if rev_scores else [_ns("requiere consenso/revisiones/sorpresa (>=5 estimados)")])

    # 3) Catalizadores (4) — narrativos/evento
    catalysts = _dim("Catalizadores", 4.0, [_ns("catalizadores requieren evidencia de eventos (MKT-CAT-019)")])

    # 4) Pista de crecimiento y captura de share (4): CAGR + YoY + capacidad fundamental
    rev_cagr = _cagr(rev, 5)
    rev_yoy = _latest_yoy(rev)
    gcap = None
    if est.get("roic") is not None and est.get("reinvestment") is not None:
        gcap = est["roic"] * est["reinvestment"]                 # MKT-GCAP-009
    runway = _dim("Pista de crecimiento", 4.0, [
        _sv(rev_cagr, _A_CAGR) if rev_cagr is not None else _ns("CAGR ingresos requiere >=3 años"),
        _sv(rev_yoy, _A_YOY) if rev_yoy is not None else _ns("YoY ingresos requiere 2 años"),
        _sv(gcap, _A_GCAP) if gcap is not None else _ns("capacidad de crecimiento requiere ROIC y reinversión")])

    # 5) Apalancamiento operativo (3): margen incremental (MKT-INCM-018)
    oplev = None
    rv = [r["val"] for r in rev if r.get("val") is not None]
    ov = [r["val"] for r in op if r.get("val") is not None]
    if len(rv) >= 2 and len(ov) >= 2 and (rv[-1] - rv[-2]) != 0:
        incr = (ov[-1] - ov[-2]) / (rv[-1] - rv[-2])
        cur = (ov[-1] / rv[-1]) if rv[-1] else None
        oplev = (incr / cur) if (cur and cur != 0) else incr
    opleverage = _dim("Apalancamiento operativo", 3.0, [
        _sv(oplev, _A_OPLEV) if oplev is not None else _ns("requiere 2 años de ingresos y op. income")])

    return Category(name="market", max_points=20.0,
                    dimensions=[tam, revisions, catalysts, runway, opleverage])
