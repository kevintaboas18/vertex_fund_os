"""Tests for the discovery screener prefilter (network-free)."""

from wbj.screener import GROWTH_MIN, MARGIN_MIN, REV_MAX, REV_MIN, prefilter


class FakeEdgar:
    """Serves canned frame payloads keyed by (tag, year)."""

    def __init__(self, frames: dict[tuple[str, int], dict[int, float]]):
        self.frames = frames

    def get_json(self, url, params, cache_key, ticker, max_age_days=None, headers=None):
        for (tag, year), rows in self.frames.items():
            if f"/{tag}/USD/CY{year}.json" in url:
                return {"data": [{"cik": c, "val": v} for c, v in rows.items()]}
        return None


def _edgar():
    rev_tag = "RevenueFromContractWithCustomerExcludingAssessedTax"
    return FakeEdgar({
        (rev_tag, 2025): {
            1: 5e9,     # good: growing, profitable, mid-size -> PASSES
            2: 50e9,    # too big (mega-cap territory)
            3: 5e9,     # not profitable enough
            4: 5e9,     # not growing
            5: 0.5e9,   # too small
        },
        (rev_tag, 2024): {1: 4.5e9, 2: 45e9, 3: 4.5e9, 4: 5e9, 5: 0.4e9},
        ("Revenues", 2025): {6: 3e9},      # fallback tag, no prior year -> skipped
        ("Revenues", 2024): {},
        ("NetIncomeLoss", 2025): {1: 1e9, 2: 10e9, 3: 0.2e9, 4: 1e9, 5: 0.1e9},
    })


def test_prefilter_applies_all_bounds():
    rows = prefilter(_edgar(), 2025)
    assert [r["cik"] for r in rows] == [1]
    r = rows[0]
    assert REV_MIN <= r["revenue"] <= REV_MAX
    assert r["margin"] >= MARGIN_MIN and r["growth"] >= GROWTH_MIN


def test_prefilter_ranks_by_growth_plus_margin():
    rev_tag = "RevenueFromContractWithCustomerExcludingAssessedTax"
    edgar = FakeEdgar({
        (rev_tag, 2025): {1: 5e9, 2: 5e9},
        (rev_tag, 2024): {1: 4.0e9, 2: 4.7e9},   # cik 1 grows 25%, cik 2 grows ~6%
        ("Revenues", 2025): {}, ("Revenues", 2024): {},
        ("NetIncomeLoss", 2025): {1: 1e9, 2: 1e9},
    })
    rows = prefilter(edgar, 2025)
    assert [r["cik"] for r in rows] == [1, 2]


def test_prefilter_falls_back_a_year_when_frame_empty():
    rev_tag = "RevenueFromContractWithCustomerExcludingAssessedTax"
    edgar = FakeEdgar({
        (rev_tag, 2026): {},  # not published yet
        (rev_tag, 2025): {1: 5e9},
        (rev_tag, 2024): {1: 4e9},
        ("Revenues", 2026): {}, ("Revenues", 2025): {}, ("Revenues", 2024): {},
        ("NetIncomeLoss", 2025): {1: 1e9},
    })
    rows = prefilter(edgar, 2026)
    assert [r["cik"] for r in rows] == [1]
