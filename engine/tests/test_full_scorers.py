"""Tests para los scorers deterministas que completan el engine (Market/Technical/
Valuation) y el orquestador full_scorecard. Datos sintéticos, sin red, sin LLM."""

from __future__ import annotations

import math

from wbj.full import full_scorecard
from wbj.market import market_category
from wbj.technical import technical_category
from wbj.valuation import valuation_category


def _series(vals, start=2020):
    return [{"end": f"{start + i}-12-31", "val": v} for i, v in enumerate(vals)]


def _packet():
    return {
        "ticker": "TEST", "entity": "Test Corp",
        "annual": {
            "revenue": _series([100, 120, 150, 185, 230]),
            "net_income": _series([10, 13, 18, 25, 34]),
            "operating_cash_flow": _series([15, 20, 26, 34, 45]),
            "capex": _series([5, 6, 7, 8, 9]),
            "long_term_debt": _series([20, 20, 18, 15, 12]),
            "equity": _series([60, 70, 85, 105, 130]),
            "operating_income": _series([14, 18, 24, 33, 45]),
            "gross_profit": _series([50, 62, 80, 100, 128]),
            "interest_expense": _series([2, 2, 1.8, 1.5, 1.2]),
            "diluted_shares": _series([10, 10, 10, 10, 10]),
        },
    }


def _uptrend(n=260, start=50.0):
    closes = [start * (1 + 0.002 * i) + 2 * math.sin(i / 9) for i in range(n)]
    return {
        "closes": closes,
        "highs": [c * 1.01 for c in closes],
        "lows": [c * 0.99 for c in closes],
        "volumes": [1_000_000 * (1 + 0.3 * math.sin(i / 7)) for i in range(n)],
    }


def test_technical_scores_uptrend():
    cat = technical_category(**_uptrend())
    assert cat.name == "technical" and cat.max_points == 20.0
    assert cat.coverage() >= 0.70            # 5 de 6 dims puntúan
    assert cat.points() > 8.0                # tendencia alcista → puntaje decente


def test_technical_no_data_is_not_scorable():
    cat = technical_category([])
    assert cat.coverage() == 0.0             # sin OHLCV nada puntúa
    assert cat.points() == 0.0


def test_valuation_scores_from_price_and_fundamentals():
    # con P/E de pares (FMP) la dimensión histórico/pares también puntúa
    cat = valuation_category(_packet(), price=40.0, market_cap=400.0, beta=1.1, peer_pe=15.0)
    assert cat.name == "valuation" and cat.max_points == 10.0
    assert cat.coverage() >= 0.70            # PEG + pares + yields + escenarios + MOS
    assert 0.0 <= cat.points() <= 10.0


def test_valuation_justified_pe_respects_g_below_ke():
    # regla VAL-JPE-032: no aplica si g >= Ke; sin pares, la categoría queda "usable con caveat"
    cat = valuation_category(_packet(), price=40.0, market_cap=400.0)
    assert 0.55 <= cat.coverage() < 0.85


def test_valuation_no_price_degrades():
    cat = valuation_category(_packet(), price=None)
    assert cat.coverage() < 0.70             # sin precio casi nada puntúa


def test_market_partial_without_estimates():
    cat = market_category(_packet(), estimates=None)
    # runway + opleverage puntúan; TAM/revisiones/catalizadores NOT_SCORABLE
    assert 0.0 < cat.coverage() < 0.70
    assert cat.points() > 0.0


def test_market_improves_with_estimates():
    base = market_category(_packet(), estimates=None).coverage()
    withest = market_category(_packet(), estimates={"eps_growth": 0.18, "analyst_upside": 0.12}).coverage()
    assert withest > base


def test_full_scorecard_six_categories_and_deterministic():
    p, o = _packet(), _uptrend()
    sc = full_scorecard(p, ohlcv=o, price=o["closes"][-1], market_cap=o["closes"][-1] * 10,
                        estimates={"eps_growth": 0.18, "analyst_upside": 0.12})
    assert set(sc["categories"]) == {"business", "financial", "market", "technical", "risk", "valuation"}
    assert 0 <= sc["raw_total"] <= 100
    assert sc["categories"]["business"]["score10"] > 6.0     # negocio sólido
    # determinismo
    sc2 = full_scorecard(p, ohlcv=o, price=o["closes"][-1], market_cap=o["closes"][-1] * 10,
                         estimates={"eps_growth": 0.18, "analyst_upside": 0.12})
    assert sc["categories"] == sc2["categories"] and sc["raw_total"] == sc2["raw_total"]


def test_full_scorecard_max_points_sum_to_100():
    sc = full_scorecard(_packet(), ohlcv=_uptrend(), price=90.0)
    assert sum(c["max"] for c in sc["categories"].values()) == 100.0


def test_market_revisions_score_with_surprise_and_growth():
    # con eps_growth + sorpresa (FMP) la dimensión de revisiones puntúa (ya no N/S)
    cat = market_category(_packet(), estimates={"eps_growth": 0.18, "surprise": 0.06,
                                                "roic": 0.19, "reinvestment": 0.5})
    rev = [d for d in cat.dimensions if "Revisiones" in d.name][0]
    assert rev.score10_value().is_valid
    # TAM y catalizadores siguen NOT_SCORABLE (sin fuente) → cobertura tope ~0.55
    assert 0.45 <= cat.coverage() <= 0.60


def test_technical_gaps_score_when_events_present():
    egaps = [{"gap": 0.05, "hold5": 0.8}, {"gap": 0.03, "hold5": 0.9},
             {"gap": -0.02, "hold5": 0.4}, {"gap": 0.04, "hold5": 0.7}]
    cat = technical_category(**_uptrend(), earnings_gaps=egaps)
    gaps = [d for d in cat.dimensions if "Gaps" in d.name][0]
    assert gaps.score10_value().is_valid            # ya no NOT_SCORABLE
