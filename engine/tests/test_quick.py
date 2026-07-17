"""Tests for the quick 6-category scorecard."""

from wbj.quick import quick_scorecard


def _series(vals: list[tuple[str, float]]) -> list[dict]:
    return [{"end": end, "val": val, "form": "10-K", "fp": "FY"} for end, val in vals]


def _packet() -> dict:
    years = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    return {
        "ticker": "TEST",
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
        },
    }


def test_six_categories_present_in_fixed_order():
    sc = quick_scorecard(_packet())
    assert [r["key"] for r in sc["categories"]] == [
        "business", "financial", "market", "technical", "risk", "valuation",
    ]


def test_unscorable_categories_marked_with_reason():
    sc = quick_scorecard(_packet())
    ns = {r["key"]: r for r in sc["categories"] if r["status"] == "not_scorable"}
    assert set(ns) == {"market", "technical", "valuation"}
    assert all(r["score10"] is None and r["reason"] for r in ns.values())


def test_scored_categories_in_range_and_overall_weighted():
    sc = quick_scorecard(_packet())
    scored = {r["key"]: r for r in sc["categories"] if r["status"] == "scored"}
    assert set(scored) == {"business", "financial", "risk"}
    for r in scored.values():
        assert 0 <= r["score10"] <= 10
    assert sc["evidence_points_covered"] == 50  # 20 + 15 + 15
    expected = sum(r["max_points"] * r["score10"] for r in scored.values()) / 50
    assert abs(sc["overall_10"] - round(expected, 1)) < 0.05


def test_strong_company_scores_high():
    sc = quick_scorecard(_packet())  # 25% margins, 14% growth, low leverage
    assert sc["overall_10"] >= 7.0


def test_missing_everything_gives_no_overall():
    empty = {"ticker": "X", "annual": {k: [] for k in (
        "revenue", "net_income", "operating_cash_flow", "capex",
        "long_term_debt", "equity", "operating_income", "gross_profit",
        "interest_expense")}}
    sc = quick_scorecard(empty)
    assert sc["overall_10"] is None
    assert sc["evidence_points_covered"] == 0
