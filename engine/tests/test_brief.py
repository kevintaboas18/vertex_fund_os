"""Tests for the plain-Spanish company brief (wbj.brief)."""

import math

from wbj.brief import (
    annualized_vol,
    company_brief,
    prob_reach,
    _category_meaning,
    _classification,
    _insider_highlights,
    _insiders_flow,
    _next_earnings,
)
from wbj.quick import quick_scorecard
from wbj.targets import price_targets


def _series(vals: list[tuple[str, float]]) -> list[dict]:
    return [{"end": end, "val": val, "form": "10-K", "fp": "FY"} for end, val in vals]


def _ohlcv_rising(n: int = 260) -> list[dict]:
    """Newest-first daily bars, gently rising (FMP order)."""
    rows = [{"date": f"D{i:05d}", "close": 100.0 + i * 0.2} for i in range(n)]
    return list(reversed(rows))


def _packet() -> dict:
    years = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    return {
        "ticker": "TEST",
        "as_of": "2026-07-17",
        "entity": "Test Corp",
        "annual": {
            "revenue": _series(list(zip(years, [80e9, 90e9, 100e9, 110e9, 125e9]))),
            "net_income": _series(list(zip(years, [20e9, 23e9, 26e9, 28e9, 32e9]))),
            "operating_cash_flow": _series(list(zip(years, [25e9, 28e9, 31e9, 34e9, 38e9]))),
            "capex": _series(list(zip(years, [5e9, 6e9, 6e9, 7e9, 8e9]))),
            "long_term_debt": _series([(years[-1], 40e9)]),
            "equity": _series(list(zip(years[-2:], [70e9, 80e9]))),
            "operating_income": _series([(years[-1], 35e9)]),
            "gross_profit": _series([(years[-1], 60e9)]),
            "interest_expense": _series([(years[-1], 2e9)]),
            "diluted_shares": _series([(years[-1], 10e9)]),
        },
        "market_data": {
            "price": 100.0,
            "market_cap": 1000e9,
            "ohlcv": _ohlcv_rising(260),
            "estimates": [{"date": "2026-12-31", "revenueAvg": 150e9,
                           "numAnalystsRevenue": 40}],
            "earnings": [
                {"date": "2025-05-20", "epsActual": 3.1, "epsEstimated": 3.0},
                {"date": "2026-08-26", "epsActual": None, "epsEstimated": 3.4,
                 "revenueEstimated": 40e9},
            ],
            "insiders": [
                {"transactionDate": "2026-06-08", "reportingName": "Big Boss",
                 "transactionType": "P-Purchase", "securitiesTransacted": 20000,
                 "price": 100.0},   # $2.0M buy -> kept
                {"transactionDate": "2026-05-01", "reportingName": "Small Fry",
                 "transactionType": "S-Sale", "securitiesTransacted": 100,
                 "price": 100.0},   # $10k -> dropped
                {"transactionDate": "2026-04-01", "reportingName": "Gifter",
                 "transactionType": "G-Gift", "securitiesTransacted": 500000,
                 "price": 0.0},     # $0 gift -> dropped
            ],
        },
    }


# --- probability math ---


def test_prob_reach_at_current_price_is_half():
    assert prob_reach(100.0, 100.0, 0.30) == 0.5


def test_prob_reach_upside_target_below_half():
    p = prob_reach(100.0, 130.0, 0.30)
    assert 0.0 < p < 0.5


def test_prob_reach_downside_target_above_half():
    assert prob_reach(100.0, 80.0, 0.30) > 0.5


def test_prob_reach_matches_normal_cdf():
    from statistics import NormalDist
    s0, k, sigma = 100.0, 120.0, 0.35
    expected = NormalDist().cdf(math.log(s0 / k) / (sigma * math.sqrt(1.0)))
    assert abs(prob_reach(s0, k, sigma) - expected) < 1e-9


# --- volatility ---


def test_annualized_vol_flat_series_is_zero():
    closes = [100.0] * 100
    assert annualized_vol(closes) == 0.0


def test_annualized_vol_too_short_is_none():
    assert annualized_vol([100.0, 101.0, 102.0]) is None


def test_annualized_vol_matches_formula():
    from statistics import pstdev
    closes = [100.0 + i * 0.2 for i in range(260)]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    expected = pstdev(rets) * math.sqrt(252)
    assert abs(annualized_vol(closes) - expected) < 1e-9


# --- interpretation bands ---


def test_category_meaning_bands():
    assert "muy fuerte" in _category_meaning(8.5).lower()
    assert "mixto" in _category_meaning(5.5).lower()
    assert "problem" in _category_meaning(2.0).lower()
    assert _category_meaning(None) is not None  # N/S has a phrase, not a crash


def test_classification_bands():
    assert _classification(8.0)[0] == "favorece"
    assert _classification(6.0)[0] == "neutral"
    assert _classification(4.0)[0] == "evitar"
    assert _classification(None)[0] is None


# --- watch-list helpers ---


def test_insider_highlights_keeps_only_over_1m():
    hi = _insider_highlights(_packet()["market_data"]["insiders"])
    assert len(hi) == 1
    assert hi[0]["name"] == "Big Boss"
    assert hi[0]["side"] == "compra"
    assert hi[0]["value"] >= 1_000_000


def test_insiders_flow_sums_buys_and_sells():
    flow = _insiders_flow(_packet()["market_data"]["insiders"])
    assert flow["buy_usd"] == 2_000_000.0   # Big Boss 20000 * 100
    assert flow["sell_usd"] == 10_000.0     # Small Fry 100 * 100
    assert flow["net_usd"] == 1_990_000.0
    assert flow["buy_count"] == 1 and flow["sell_count"] == 1


def test_insiders_flow_empty_when_no_open_market_trades():
    flow = _insiders_flow([{"transactionType": "G-Gift",
                            "securitiesTransacted": 500000, "price": 0.0}])
    assert flow["buy_usd"] == 0.0 and flow["sell_usd"] == 0.0
    assert flow["net_usd"] == 0.0


def test_next_earnings_picks_soonest_future():
    row = _next_earnings(_packet()["market_data"]["earnings"], "2026-07-17")
    assert row["date"] == "2026-08-26"


def test_next_earnings_none_when_all_past():
    row = _next_earnings([{"date": "2020-01-01"}], "2026-07-17")
    assert row is None


# --- full brief ---


def test_company_brief_has_all_sections():
    p = _packet()
    sc = quick_scorecard(p)
    tg = price_targets(p, p["market_data"]["price"])
    b = company_brief(p, sc, tg)
    assert set(b) >= {"interpretation", "probability", "where", "watch"}
    assert b["interpretation"]["classification"] in ("favorece", "neutral", "evitar")
    assert b["probability"]["status"] == "ok"
    assert b["probability"]["price"] == 100.0
    assert 0 <= b["probability"]["targets"][0]["prob_reach"] <= 1
    assert b["watch"]["insiders_flow"]["buy_count"] >= 0
    assert isinstance(b["where"], list) and b["where"]
    assert "levels" in b["watch"] and "insiders" in b["watch"]
    assert b["watch"]["catalysts"]["next_earnings"]["date"] == "2026-08-26"
    assert len(b["watch"]["insiders"]) == 1


def test_company_brief_probability_ns_without_targets():
    p = _packet()
    sc = quick_scorecard(p)
    tg = price_targets(p, None)  # no price -> targets not_scorable
    b = company_brief(p, sc, tg)
    assert b["probability"]["status"] == "not_scorable"


def test_company_brief_risks_flag_weak_company():
    p = _packet()
    # wreck the balance sheet: heavy debt + cash burn + shrinking revenue
    p["annual"]["long_term_debt"] = _series([("2025-12-31", 300e9)])
    p["annual"]["capex"] = _series(list(zip(
        ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"],
        [50e9, 50e9, 50e9, 50e9, 60e9])))
    sc = quick_scorecard(p)
    tg = price_targets(p, p["market_data"]["price"])
    b = company_brief(p, sc, tg)
    assert b["watch"]["risks"]
