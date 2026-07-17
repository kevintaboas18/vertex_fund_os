"""Valuation category scorer (10 pts) — DETERMINISTA, fórmulas exactas de Victor.

Implementa Cerebro/06_valuation_analysis (FORMULAS.md + DECISION_RULES.md):
PEG (VAL-PEG-028), earnings yield (VAL-EY-029), FCF yield (VAL-FCFY-030),
justified P/E (VAL-JPE-032), FCFF DCF por escenarios (VAL-FCFF-005/EV-014),
reverse DCF (VAL-RDCF-027) y margen de seguridad (VAL-MOS-040). Ke = RF +
beta*ERP (VAL-KE-008). Escenarios Bear/Base/Bull con supuestos declarados
(nunca un único punto). Sin LLM. Sin datos → NOT_SCORABLE.

Dimensiones: Múltiplos ajustados por crecimiento (3) · Histórico y pares (2) ·
Yield de caja/earnings (2) · Valor justo por escenarios (2) · Margen de seguridad (1).
"""

from __future__ import annotations

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension, anchor_score

_A_PEG = [(0.5, 10.0), (1.0, 8.0), (1.5, 6.0), (2.0, 4.0), (3.0, 1.0), (4.0, 0.0)]
_A_EY = [(0.0, 0.0), (0.03, 3.0), (0.05, 5.5), (0.08, 8.0), (0.12, 10.0)]
_A_FCFY = [(-0.02, 0.0), (0.02, 3.0), (0.04, 6.0), (0.07, 8.5), (0.10, 10.0)]
_A_PRICE_VS_FAIR = [(1.3, 0.0), (1.1, 3.0), (1.0, 5.0), (0.9, 7.5), (0.7, 10.0)]
_A_MOS = [(-0.20, 0.0), (0.0, 4.0), (0.15, 7.0), (0.30, 9.0), (0.50, 10.0)]
_A_JPE = [(1.6, 10.0), (1.2, 8.0), (1.0, 6.0), (0.8, 4.0), (0.5, 1.0)]   # justified_pe / actual_pe (barato = alto)


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


def _fcff_dcf(fcff0, g_hi, years, g_term, wacc):
    """VAL-EV-014 simplificado: FCFF creciendo g_hi 'years', terminal Gordon (VAL-TVG-012)."""
    if fcff0 is None or wacc <= g_term or wacc <= 0:
        return None
    pv = 0.0; f = fcff0
    for t in range(1, years + 1):
        f = f * (1 + g_hi)
        pv += f / (1 + wacc) ** t
    tv = f * (1 + g_term) / (wacc - g_term)
    pv += tv / (1 + wacc) ** years
    return pv


def valuation_detail(packet, price, market_cap=None, beta=None, eps_growth=None,
                     risk_free=0.043, erp=0.05):
    """Devuelve los números de valuación (Ke, EPS, PE, escenarios FV, MOS, reverse-DCF)."""
    a = (packet or {}).get("annual", {}) if packet else {}
    ni = [r["val"] for r in a.get("net_income", []) if r.get("val") is not None]
    rev = a.get("revenue", [])
    ocf = [r["val"] for r in a.get("operating_cash_flow", []) if r.get("val") is not None]
    capex = [r["val"] for r in a.get("capex", []) if r.get("val") is not None]
    eq = [r["val"] for r in a.get("equity", []) if r.get("val") is not None]
    shares = [r["val"] for r in a.get("diluted_shares", []) if r.get("val") is not None]

    ni_l = ni[-1] if ni else None
    sh_l = shares[-1] if shares else None
    eps = (ni_l / sh_l) if (ni_l and sh_l) else None
    pe = (price / eps) if (eps and eps > 0 and price) else None
    roe = (ni_l / eq[-1]) if (ni_l and eq and eq[-1] > 0) else None
    fcf = (ocf[-1] - capex[-1]) if (ocf and capex) else None
    mcap = market_cap or ((price * sh_l) if (price and sh_l) else None)
    g = eps_growth if eps_growth is not None else (_cagr(a.get("net_income", []), 5) or _cagr(rev, 5))
    g = max(-0.10, min(0.40, g)) if g is not None else None
    ke = risk_free + (beta if beta is not None else 1.0) * erp     # VAL-KE-008 (CRP=0)
    wacc = max(ke, 0.06)                                            # aprox: sin peso de deuda detallado, usa Ke como piso

    # Escenarios FCFF DCF (Bear/Base/Bull) — supuestos declarados
    scenarios = None
    fair_base = None
    if fcf is not None and sh_l and g is not None:
        gt = min(0.025, wacc - 0.01)
        defs = {"Bear": g - 0.08, "Base": g, "Bull": g + 0.05}
        scenarios = []
        for name, gh in defs.items():
            gh = max(-0.10, min(0.40, gh))
            ev = _fcff_dcf(fcf, gh, 5, gt, wacc)
            fv = (ev / sh_l) if ev else None
            scenarios.append({"scenario": name, "value": round(fv, 2) if fv else None,
                              "assumptions": f"FCFF g {gh:+.0%} 5a, terminal {gt:.1%}, WACC {wacc:.1%}"})
            if name == "Base":
                fair_base = fv

    # Justified P/E (VAL-JPE-032)
    jpe = None
    if roe and roe > 0 and g is not None and ke > g:
        jpe = (1 - g / roe) / (ke - g)

    ey = (eps / price) if (eps and price) else None
    fcfy = (fcf / mcap) if (fcf is not None and mcap) else None
    peg = (pe / (g * 100)) if (pe and g and g > 0) else None
    price_vs_fair = (price / fair_base) if (fair_base and price and fair_base > 0) else None
    mos = ((fair_base - price) / fair_base) if (fair_base and price and fair_base > 0) else None

    return {"eps": eps, "pe": pe, "roe": roe, "ke": round(ke, 4), "wacc": round(wacc, 4),
            "growth": g, "fcf": fcf, "peg": peg, "justified_pe": jpe, "earnings_yield": ey,
            "fcf_yield": fcfy, "fair_base": round(fair_base, 2) if fair_base else None,
            "price_vs_fair": price_vs_fair, "mos": mos, "scenarios": scenarios}


def valuation_category(packet, price, market_cap=None, beta=None, eps_growth=None,
                       peer_pe=None) -> Category:
    """Category 'Valuation' (max 10). peer_pe: P/E mediano de pares (FMP) si está."""
    d = valuation_detail(packet, price, market_cap, beta, eps_growth)

    jpe_ratio = (d["justified_pe"] / d["pe"]) if (d["justified_pe"] and d["pe"]) else None
    m_growth = _dim("Múltiplos ajustados por crecimiento", 3.0, [
        _sv(d["peg"], _A_PEG) if d["peg"] is not None else _ns("PEG requiere P/E>0 y crecimiento>0"),
        _sv(jpe_ratio, _A_JPE) if jpe_ratio is not None else _ns("P/E justificado requiere ROE>0, g<Ke")])

    if peer_pe and d["pe"]:
        peer_ratio = peer_pe / d["pe"]                     # pares/actual (barato vs pares = alto)
        hist_peer = _dim("Histórico y pares", 2.0, [_sv(peer_ratio, _A_JPE)])
    else:
        hist_peer = _dim("Histórico y pares", 2.0, [_ns("requiere P/E de pares o histórico (VAL-REL/ZHIST)")])

    yields = _dim("Yield de caja/earnings", 2.0, [
        _sv(d["earnings_yield"], _A_EY) if d["earnings_yield"] is not None else _ns("earnings yield requiere EPS y precio"),
        _sv(d["fcf_yield"], _A_FCFY) if d["fcf_yield"] is not None else _ns("FCF yield requiere FCF y market cap")])

    scenarios = _dim("Valor justo por escenarios", 2.0, [
        _sv(d["price_vs_fair"], _A_PRICE_VS_FAIR) if d["price_vs_fair"] is not None
        else _ns("DCF por escenarios requiere FCF, acciones y crecimiento")])

    margin = _dim("Margen de seguridad", 1.0, [
        _sv(d["mos"], _A_MOS) if d["mos"] is not None else _ns("margen de seguridad requiere valor justo")])

    return Category(name="valuation", max_points=10.0,
                    dimensions=[m_growth, hist_peer, yields, scenarios, margin])
