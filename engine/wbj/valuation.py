"""Valuation category scorer (10 pts) — deterministic.

Completa el engine de Victor para Valuation siguiendo
Cerebro/06_valuation_analysis/SCORING.md. Sin LLM. Rangos con supuestos
declarados (nunca un único punto). Lo que necesita pares/histórico se deja
NOT_SCORABLE. Múltiplos ajustados por crecimiento, yields de caja/earnings,
valor justo por escenarios y margen de seguridad se calculan desde precio +
fundamentales.

Dimensiones: Múltiplos ajustados por crecimiento (3) · Histórico y pares (2)
· Yield de caja/earnings (2) · Valor justo por escenarios (2) · Margen de
seguridad (1).
"""

from __future__ import annotations

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension, anchor_score

# PEG: <1 barato para el crecimiento, >2 caro. (score alto = barato)
_A_PEG = [(0.5, 10.0), (1.0, 8.0), (1.5, 6.0), (2.0, 4.0), (3.0, 1.0), (4.0, 0.0)]
_A_EY = [(0.0, 0.0), (0.03, 3.0), (0.05, 5.5), (0.08, 8.0), (0.12, 10.0)]        # earnings yield
_A_FCFY = [(-0.02, 0.0), (0.02, 3.0), (0.04, 6.0), (0.07, 8.5), (0.10, 10.0)]    # FCF yield
_A_PRICE_VS_FAIR = [(1.3, 0.0), (1.1, 3.0), (1.0, 5.0), (0.9, 7.5), (0.7, 10.0)] # precio/valor justo (barato = alto)
_A_MOS = [(-0.20, 0.0), (0.0, 4.0), (0.15, 7.0), (0.30, 9.0), (0.50, 10.0)]      # margen de seguridad


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


def valuation_category(packet: dict, price: float | None, market_cap: float | None = None) -> Category:
    """Category 'Valuation' (max 10) desde EDGAR + precio de mercado."""
    a = (packet or {}).get("annual", {}) if packet else {}
    ni = [r["val"] for r in a.get("net_income", []) if r.get("val") is not None]
    rev = a.get("revenue", []) or []
    ocf = [r["val"] for r in a.get("operating_cash_flow", []) if r.get("val") is not None]
    capex = [r["val"] for r in a.get("capex", []) if r.get("val") is not None]
    shares = [r["val"] for r in a.get("diluted_shares", []) if r.get("val") is not None]

    ni_l = ni[-1] if ni else None
    sh_l = shares[-1] if shares else None
    eps = (ni_l / sh_l) if (ni_l and sh_l) else None
    pe = (price / eps) if (eps and eps > 0 and price) else None
    growth = _series_cagr(a.get("net_income", []), 5) or _series_cagr(rev, 5)
    fcf = (ocf[-1] - capex[-1]) if (ocf and capex) else None
    mcap = market_cap or ((price * sh_l) if (price and sh_l) else None)

    # 1) Múltiplos ajustados por crecimiento (PEG)
    peg = (pe / (growth * 100)) if (pe and growth and growth > 0) else None
    m_growth = _dim("Múltiplos ajustados por crecimiento", 3.0, [
        _sv(peg, _A_PEG) if peg is not None else _ns("PEG requiere P/E>0 y crecimiento>0")])

    # 2) Histórico y pares — requiere >=8 pares o histórico de múltiplos
    hist_peer = _dim("Histórico y pares", 2.0, [_ns("requiere >=8 pares o histórico de múltiplos (no inyectado)")])

    # 3) Yield de caja/earnings
    ey = (eps / price) if (eps and price) else None
    fcfy = (fcf / mcap) if (fcf is not None and mcap) else None
    yields = _dim("Yield de caja/earnings", 2.0, [
        _sv(ey, _A_EY) if ey is not None else _ns("earnings yield requiere EPS y precio"),
        _sv(fcfy, _A_FCFY) if fcfy is not None else _ns("FCF yield requiere FCF y market cap")])

    # 4) Valor justo por escenarios (múltiplo justificado con supuestos declarados)
    #    fair = EPS * (1 + g) * PE_justificado; PE_justificado = min(max(8, 12 + g*100), 30)
    price_vs_fair = None
    fair_base = None
    if eps and eps > 0 and growth is not None:
        just_pe = min(max(8.0, 12.0 + growth * 100.0), 30.0)
        fair_base = eps * (1 + max(-0.1, min(0.4, growth))) * just_pe
        price_vs_fair = (price / fair_base) if (price and fair_base > 0) else None
    scenarios = _dim("Valor justo por escenarios", 2.0, [
        _sv(price_vs_fair, _A_PRICE_VS_FAIR) if price_vs_fair is not None
        else _ns("valor justo requiere EPS>0 y crecimiento")])

    # 5) Margen de seguridad
    mos = ((fair_base - price) / fair_base) if (fair_base and price and fair_base > 0) else None
    margin = _dim("Margen de seguridad", 1.0, [
        _sv(mos, _A_MOS) if mos is not None else _ns("margen de seguridad requiere valor justo")])

    return Category(name="valuation", max_points=10.0,
                    dimensions=[m_growth, hist_peer, yields, scenarios, margin])
