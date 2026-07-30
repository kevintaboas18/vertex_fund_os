"""Tests for `wbj.engines.levels_engine`.

Verified against hand-computed synthetic OHLCV per the task-12 brief's Step
1 list: k=3 pivot detection (and confirmation delay), zone tolerance
formula, 5-sessions-apart independent touches, exact strength-formula hand
computation, breakout requiring volume AND close, and the gap-material
threshold. Supplementary tests cover the remaining interface functions
(zigzag, weighted median, cluster_zones, classify, avwap, volume_profile,
earnings_gaps, rank_levels) plus an end-to-end `compute_levels` smoke test.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from wbj.engines.levels_engine import (
    Pivot,
    TouchRecord,
    VolumeProfile,
    ZoneCandidate,
    ZoneState,
    avwap,
    breakout_confirmed,
    classify,
    cluster_zones,
    compute_levels,
    count_touches,
    earnings_gaps,
    failed_breakout,
    find_pivots,
    gap_is_material,
    rank_levels,
    strength,
    volume_profile,
    weighted_median,
    zigzag_pivots,
    zone_tolerance,
)


def _dates(n: int) -> list[str]:
    return [f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]


def _flat_df(n: int, high=95.0, low=90.0, close=92.0, volume=1000.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": _dates(n),
            "open": [close] * n,
            "high": [high] * n,
            "low": [low] * n,
            "close": [close] * n,
            "volume": [volume] * n,
        }
    )


# --- brief Step 1 tests (verbatim shape) ------------------------------------


def test_symmetric_pivot_k3_detects_local_max():
    # bar 10 is highest of bars 7..13 -> pivot high at index 10, only after
    # bar 13 closes.
    n = 20
    highs = [10.0 + (i % 3) for i in range(n)]
    highs[10] = 50.0
    lows = [h - 5.0 for h in highs]
    df = pd.DataFrame({"date": _dates(n), "high": highs, "low": lows, "close": lows})

    pivots = find_pivots(df, k=3)
    high_pivots = [p for p in pivots if p.kind == "high"]
    assert any(p.index == 10 and p.price == 50.0 and p.confirmed_index == 13 for p in high_pivots)


def test_pivot_not_confirmed_until_k_future_bars_close():
    n = 20
    highs = [10.0 + (i % 3) for i in range(n)]
    highs[10] = 50.0
    lows = [h - 5.0 for h in highs]
    df_full = pd.DataFrame({"date": _dates(n), "high": highs, "low": lows, "close": lows})
    # Truncate before bar 13 (the k=3 confirmation bar) closes.
    df_short = df_full.iloc[:13].reset_index(drop=True)

    pivots = find_pivots(df_short, k=3)
    assert not any(p.index == 10 for p in pivots)


def test_zone_tolerance_formula():
    # atr=2, price=100 -> tolerance = max(1.0, 0.75) = 1.0
    assert zone_tolerance(atr_at_pivot=2.0, pivot_price=100.0) == 1.0


def test_zone_tolerance_price_dominates_for_high_priced_low_vol_pivot():
    assert zone_tolerance(atr_at_pivot=0.1, pivot_price=1000.0) == pytest.approx(7.5)


def test_touches_5_sessions_apart():
    n = 20
    df = _flat_df(n)
    high = df["high"].tolist()
    low = df["low"].tolist()
    for idx in (5, 12):  # 7 sessions apart -> both independent
        high[idx] = 100.5
    df["high"] = high
    df["low"] = low
    atr = pd.Series([2.0] * n)

    zone = ZoneCandidate(
        kind="high", center=100.0, lower=99.0, upper=101.0,
        members=[
            Pivot(index=5, date=df["date"].iloc[5], kind="high", price=100.5, confirmed_index=8),
            Pivot(index=12, date=df["date"].iloc[12], kind="high", price=100.5, confirmed_index=15),
        ],
    )
    touches = count_touches(zone, df, atr, timeframe="daily", as_of_index=n - 1)
    assert [t.index for t in touches] == [5, 12]


def test_touches_less_than_5_sessions_apart_collapse_to_one():
    n = 20
    df = _flat_df(n)
    high = df["high"].tolist()
    for idx in (5, 8):  # 3 sessions apart -> not independent
        high[idx] = 100.5
    df["high"] = high
    atr = pd.Series([2.0] * n)

    zone = ZoneCandidate(
        kind="high", center=100.0, lower=99.0, upper=101.0,
        members=[
            Pivot(index=5, date=df["date"].iloc[5], kind="high", price=100.5, confirmed_index=8),
            Pivot(index=8, date=df["date"].iloc[8], kind="high", price=100.5, confirmed_index=11),
        ],
    )
    touches = count_touches(zone, df, atr, timeframe="daily", as_of_index=n - 1)
    assert [t.index for t in touches] == [5]


def test_strength_formula_exact():
    # Hand-computed: 2 fresh touches (age 0), rejection_atr=1.0 each,
    # volume_ratio=1.5 each, confluence_count=3, daily timeframe.
    # Touch = 30*min(2/4,1) = 15
    # Reaction = 20*min(1.0/2,1) = 10
    # Volume = 15*min(1.5/1.5,1) = 15
    # Recency = 15*exp(0) = 15
    # Timeframe (daily) = 5
    # Confluence = 10*min(3/3,1) = 10
    # Total = 70
    touches = [
        TouchRecord(date="d0", index=0, pivot_price=100.0, rejection_atr=1.0, volume_ratio=1.5, age_sessions=0),
        TouchRecord(date="d1", index=1, pivot_price=100.2, rejection_atr=1.0, volume_ratio=1.5, age_sessions=0),
    ]
    zone = ZoneState(kind="high", center=100.0, lower=99.0, upper=101.0, timeframe="daily",
                      touches=touches, confluence_count=3)
    assert strength(zone) == pytest.approx(70.0)


def test_strength_capped_at_100():
    touches = [
        TouchRecord(date=f"d{i}", index=i, pivot_price=100.0, rejection_atr=5.0, volume_ratio=5.0, age_sessions=0)
        for i in range(10)
    ]
    zone = ZoneState(kind="high", center=100.0, lower=99.0, upper=101.0, timeframe="weekly",
                      touches=touches, confluence_count=10)
    assert strength(zone) == 100.0


def test_breakout_requires_volume_and_close():
    n = 60
    dates = _dates(n)
    close = [95.0] * n
    high = [96.0] * n
    low = [94.0] * n
    volume = [1000.0] * n
    zone = ZoneState(kind="high", center=100.0, lower=99.0, upper=101.0, timeframe="daily", touches=[])
    atr = pd.Series([2.0] * n)  # buffer = 0.25*2 = 0.5 -> trigger level 101.5

    close_break = close.copy()
    close_break[55] = 102.0
    close_break[56] = 102.0
    close_break[57] = 102.0

    # Scenario A: close breaks the buffer with two consecutive closes above,
    # but volume never spikes -> not confirmed (fails the volume condition).
    df_a = pd.DataFrame({"date": dates, "high": high, "low": low, "close": close_break, "volume": volume})
    assert breakout_confirmed(df_a, zone, atr) is False

    # Scenario B: same close pattern, but volume spikes >=1.5x its prior-50
    # median on the trigger day -> confirmed.
    volume_b = volume.copy()
    volume_b[55] = 2000.0
    df_b = pd.DataFrame({"date": dates, "high": high, "low": low, "close": close_break, "volume": volume_b})
    assert breakout_confirmed(df_b, zone, atr) is True


def test_failed_breakout_close_back_inside_within_3_sessions():
    n = 20
    dates = _dates(n)
    close = [95.0] * n
    high = [96.0] * n
    low = [94.0] * n
    zone = ZoneState(kind="high", center=100.0, lower=99.0, upper=101.0, timeframe="daily", touches=[])
    atr = pd.Series([2.0] * n)
    close[10] = 102.0  # breakout trigger bar
    close[11] = 100.0  # closes back inside the zone within 3 sessions -> failed
    df = pd.DataFrame({"date": dates, "high": high, "low": low, "close": close, "volume": [1000.0] * n})
    assert failed_breakout(df, zone, atr, breakout_index=10) is True


def test_failed_breakout_false_when_price_holds_above():
    n = 20
    dates = _dates(n)
    close = [95.0] * n
    high = [96.0] * n
    low = [94.0] * n
    zone = ZoneState(kind="high", center=100.0, lower=99.0, upper=101.0, timeframe="daily", touches=[])
    atr = pd.Series([2.0] * n)
    close[10] = 102.0
    close[11] = 102.5
    close[12] = 103.0
    close[13] = 103.5
    df = pd.DataFrame({"date": dates, "high": high, "low": low, "close": close, "volume": [1000.0] * n})
    assert failed_breakout(df, zone, atr, breakout_index=10) is False


def test_gap_material_threshold():
    # atr=1, close=100: threshold = max(1.0, 3.0) = 3.0
    assert gap_is_material(gap_abs=2.0, atr=1.0, prior_close=100.0) is False  # 2% gap
    assert gap_is_material(gap_abs=3.1, atr=1.0, prior_close=100.0) is True  # 3.1% gap


# --- supplementary coverage --------------------------------------------------


def test_weighted_median_equal_weights_is_plain_median():
    assert weighted_median([1.0, 2.0, 3.0], [1.0, 1.0, 1.0]) == 2.0


def test_weighted_median_dominant_weight_wins():
    assert weighted_median([1.0, 5.0, 9.0], [0.01, 10.0, 0.01]) == 5.0


def test_zigzag_reversal_threshold():
    high = [10.0, 10.0, 10.0, 10.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 19.0, 17.0, 15.0, 13.0, 11.0, 9.0]
    low = [h - 1.0 for h in high]
    atr = pd.Series([1.0] * len(high))  # threshold = 1.5
    df = pd.DataFrame({"date": _dates(len(high)), "high": high, "low": low})

    pivots = zigzag_pivots(df, atr)
    high_pivots = [p for p in pivots if p.kind == "high"]
    assert any(p.index == 9 and p.price == 20.0 for p in high_pivots)


def test_cluster_zones_merges_overlapping_and_separates_distant():
    # Two close pivots (100, 100.5) with tolerance 1.0 each -> overlap and
    # merge into one zone; a distant pivot (150) forms its own zone.
    n = 10
    price = pd.Series([100.0] * n)
    atr = pd.Series([2.0] * n)  # tol = max(1.0, 0.75) = 1.0 for the ~100 pivots
    pivots = [
        Pivot(index=0, date="d0", kind="high", price=100.0, confirmed_index=3),
        Pivot(index=1, date="d1", kind="high", price=100.5, confirmed_index=4),
        Pivot(index=2, date="d2", kind="high", price=150.0, confirmed_index=5),
    ]
    zones = cluster_zones(pivots, atr, price)
    high_zones = [z for z in zones if z.kind == "high"]
    assert len(high_zones) == 2
    merged = next(z for z in high_zones if len(z.members) == 2)
    assert merged.lower < 100.0 < merged.upper
    solo = next(z for z in high_zones if len(z.members) == 1)
    assert solo.center == 150.0


def test_classify_candidate_confirmed_strong():
    def touch(reaction, vol, idx=0):
        return TouchRecord(date=f"d{idx}", index=idx, pivot_price=100.0, rejection_atr=reaction, volume_ratio=vol, age_sessions=0)

    candidate = ZoneState(kind="high", center=100, lower=99, upper=101, timeframe="daily", touches=[touch(0.6, 1.0)])
    assert classify(candidate) == "candidate"

    confirmed = ZoneState(kind="high", center=100, lower=99, upper=101, timeframe="daily",
                           touches=[touch(0.6, 1.0, 0), touch(0.6, 1.0, 1)])
    assert classify(confirmed) == "confirmed"

    strong_by_count = ZoneState(kind="high", center=100, lower=99, upper=101, timeframe="daily",
                                 touches=[touch(0.6, 1.0, i) for i in range(3)])
    assert classify(strong_by_count) == "strong"

    strong_by_reaction_volume = ZoneState(kind="high", center=100, lower=99, upper=101, timeframe="daily",
                                           touches=[touch(1.2, 1.6, 0), touch(1.2, 1.6, 1)])
    assert classify(strong_by_reaction_volume) == "strong"


def test_classify_break_status_overrides_touch_count():
    z = ZoneState(kind="high", center=100, lower=99, upper=101, timeframe="daily",
                  touches=[TouchRecord(date="d", index=0, pivot_price=100, rejection_atr=1.0, volume_ratio=1.0, age_sessions=0)] * 3)
    assert classify(z, break_status="broken") == "broken"
    assert classify(z, break_status="role_reversed") == "role_reversed"


def test_avwap_hand_computed():
    df = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "high": [10.0, 12.0, 11.0],
            "low": [8.0, 10.0, 9.0],
            "close": [9.0, 11.0, 10.0],
            "volume": [100.0, 200.0, 300.0],
        }
    )
    # TP = (H+L+C)/3 = [9.0, 11.0, 10.0]. Anchored at 2024-01-02 (drop row 0):
    # (11*200 + 10*300) / 500 = 5200/500 = 10.4
    result = avwap(df, anchor_date="2024-01-02")
    assert result == pytest.approx(10.4)


def test_volume_profile_poc_near_high_volume_cluster():
    df = pd.DataFrame(
        {
            "high": [10.0, 10.0, 10.0, 20.0, 20.0],
            "low": [9.0, 9.0, 9.0, 19.0, 19.0],
            "close": [9.5, 9.5, 9.5, 19.5, 19.5],
            "volume": [100.0, 100.0, 100.0, 50.0, 50.0],
        }
    )
    atr = pd.Series([1.0] * 5)
    vp = volume_profile(df, atr)
    assert isinstance(vp, VolumeProfile)
    assert vp.poc == pytest.approx(9.5, abs=0.5)
    assert sum(b.volume for b in vp.bins) == pytest.approx(400.0)


def test_earnings_gaps_material_and_hold_ratios():
    n = 30
    dates = _dates(n)
    open_ = [100.0] * n
    high = [101.0] * n
    low = [99.0] * n
    close = [100.0] * n
    volume = [1000.0] * n

    open_[10] = 105.0
    high[10] = 106.0
    low[10] = 104.0
    close[10] = 105.5
    for i in range(11, n):
        close[i] = 105.5
        open_[i] = 105.5  # no gap on any later session

    df = pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low, "close": close, "volume": volume})
    atr = pd.Series([1.0] * n)

    gaps = earnings_gaps(df, earnings_dates=[dates[10], dates[20]], atr=atr)
    assert len(gaps) == 2

    material_gap = gaps[0]
    assert material_gap.material is True
    assert material_gap.prior_close == pytest.approx(100.0)
    assert material_gap.gap_open == pytest.approx(105.0)
    # day1 hold ratio = (close[11]-prior_close)/(gap_open-prior_close) = (105.5-100)/5 = 1.1
    assert material_gap.day1_hold_ratio == pytest.approx(1.1)
    assert material_gap.fill_status == "open"  # never closes back to prior_close=100

    no_gap = gaps[1]
    assert no_gap.material is False


def test_rank_levels_formula():
    result = rank_levels(strength=80.0, recency=60.0, cross_lens_confluence=50.0, liquidity_confidence=40.0)
    expected = 0.45 * 80 + 0.25 * 60 + 0.20 * 50 + 0.10 * 40
    assert result == pytest.approx(expected)


# --- compute_levels: end-to-end smoke test -----------------------------------


def _synthetic_history(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n).strftime("%Y-%m-%d").tolist()
    drift = np.linspace(0, 20, n)
    wave = 8.0 * np.sin(np.linspace(0, 10 * math.pi, n))
    noise = rng.normal(0, 0.5, n)
    close = 100.0 + drift + wave + noise
    close = np.clip(close, 50.0, None)
    open_ = close + rng.normal(0, 0.3, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0.5, 0.3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.5, 0.3, n))
    volume = rng.normal(1_000_000, 100_000, n).clip(min=100_000)
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_compute_levels_smoke():
    df = _synthetic_history()
    earnings_dates = [df["date"].iloc[100], df["date"].iloc[250]]

    result = compute_levels(df, earnings_dates)

    assert len(result.nearest_support) <= 3
    assert len(result.nearest_resistance) <= 3
    assert all(z.type == "support" for z in result.nearest_support) or not result.nearest_support
    assert all(z.type == "resistance" for z in result.nearest_resistance) or not result.nearest_resistance
    labels = {ma.label for ma in result.moving_averages}
    assert labels <= {"SMA50", "SMA200"}
    assert len(result.avwaps) >= 1

    # Nearest zones should be sorted by ascending ATR distance.
    resistance_distances = [abs(z.distance_atr) for z in result.nearest_resistance if z.distance_atr is not None]
    assert resistance_distances == sorted(resistance_distances)
    support_distances = [abs(z.distance_atr) for z in result.nearest_support if z.distance_atr is not None]
    assert support_distances == sorted(support_distances)

    for zone in [*result.nearest_support, *result.nearest_resistance]:
        assert 0.0 <= zone.strength_0_100 <= 100.0
        assert zone.status in {"candidate", "confirmed", "strong", "role_reversed"}
        assert len(zone.touches) >= 1
