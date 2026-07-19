"""Important-levels engine: pivots, zones, strength, breakouts, AVWAP, gaps.

Implements `Cerebro/special_sauces/IMPORTANT_LEVELS_ENGINE.md` sections A-J
and `Cerebro/04_technical_momentum/FORMULAS.md` TECH-PIV-022..LIQ-040 — the
authoritative sources for every threshold below; see the task-12 commit
message for the one place this module documents a deviation.

Like `wbj.engines.indicators`, this module is plain pandas/numpy over
adjusted daily OHLCV (`open`/`high`/`low`/`close`/`volume`, ascending by
date, `close` already split/dividend-adjusted) — no `Value` wrapping.
`atr14` is imported from `wbj.engines.indicators` rather than
reimplemented (per the task-12 brief).

Internal building blocks (`Pivot`, `ZoneCandidate`, `TouchRecord`,
`ZoneState`) are plain dataclasses distinct from the public pydantic
`wbj.schemas.levels.Zone`/`Touch` — the dataclasses carry engine-only
bookkeeping (bar index, per-touch age in sessions) that the public schema
intentionally omits. `compute_levels` is the only function that produces
the public schema types.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from wbj.engines import indicators
from wbj.schemas.levels import (
    AVWAPLevel,
    EarningsGap,
    LevelsOutput,
    MovingAverageLevel,
    Touch,
    Zone,
    ZoneType,
)

__all__ = [
    "Pivot",
    "ZoneCandidate",
    "TouchRecord",
    "ZoneState",
    "VolumeProfileBin",
    "VolumeProfile",
    "zone_tolerance",
    "weighted_median",
    "find_pivots",
    "zigzag_pivots",
    "cluster_zones",
    "count_touches",
    "strength",
    "classify",
    "breakout_confirmed",
    "failed_breakout",
    "avwap",
    "gap_is_material",
    "volume_profile",
    "earnings_gaps",
    "rank_levels",
    "compute_levels",
]

_HALF_LIFE_SESSIONS = 126.0
_LN2 = math.log(2)


def _recency_weight(age_sessions: float) -> float:
    """`exp(-ln2 * age_sessions / 126)` — the 126-session recency half-life
    used throughout A2-A8 (zone center, N_eff, strength recency term)."""
    return math.exp(-_LN2 * age_sessions / _HALF_LIFE_SESSIONS)


def _dates_of(df: pd.DataFrame) -> list[str]:
    if "date" in df.columns:
        return df["date"].astype(str).tolist()
    return [str(i) for i in df.index]


# --- A1/TECH-PIV-022/TECH-ZIG-023: swing detection -------------------------


@dataclass(frozen=True)
class Pivot:
    """One confirmed swing pivot (symmetric or zigzag)."""

    index: int
    date: str
    kind: Literal["high", "low"]
    price: float
    confirmed_index: int


def find_pivots(df: pd.DataFrame, k: int) -> list[Pivot]:
    """Symmetric pivot high/low (TECH-PIV-022): `High_t` is a pivot high iff
    `High_t == max(High[t-k:t+k])` (inclusive both ends); low is the analog.

    A pivot at `t` is only returned once its full `2k+1`-bar window exists in
    `df` — i.e. only after the `k` future confirmation bars have closed.
    Ties within a window are resolved to the earliest bar achieving the
    extreme ("one canonical pivot" per Cerebro A1).
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    n = len(df)
    pivots: list[Pivot] = []
    if n < 2 * k + 1:
        return pivots

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    dates = _dates_of(df)

    for t in range(k, n - k):
        window_high = highs[t - k : t + k + 1]
        if highs[t] == window_high.max() and int(np.argmax(window_high)) == k:
            pivots.append(
                Pivot(index=t, date=dates[t], kind="high", price=float(highs[t]), confirmed_index=t + k)
            )
        window_low = lows[t - k : t + k + 1]
        if lows[t] == window_low.min() and int(np.argmin(window_low)) == k:
            pivots.append(
                Pivot(index=t, date=dates[t], kind="low", price=float(lows[t]), confirmed_index=t + k)
            )
    return pivots


def zigzag_pivots(df: pd.DataFrame, atr: pd.Series) -> list[Pivot]:
    """ATR zigzag swing (TECH-ZIG-023): confirm a reversal once price moves
    `>= 1.5*ATR14` (evaluated with the ATR *at the reversal bar*) away from
    the running candidate extreme.

    Standard zigzag bookkeeping: track a running extreme high and extreme
    low; once price reverses from one extreme by the threshold, the extreme
    is emitted as a confirmed pivot and tracking flips to the opposite
    extreme starting from the reversal bar.
    """
    n = len(df)
    pivots: list[Pivot] = []
    if n == 0:
        return pivots

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    dates = _dates_of(df)

    direction: str | None = None
    extreme_high, extreme_high_idx = highs[0], 0
    extreme_low, extreme_low_idx = lows[0], 0

    for i in range(1, n):
        a = atr.iloc[i] if i < len(atr) else float("nan")
        if pd.isna(a):
            continue
        thr = 1.5 * float(a)

        if highs[i] > extreme_high:
            extreme_high, extreme_high_idx = highs[i], i
        if lows[i] < extreme_low:
            extreme_low, extreme_low_idx = lows[i], i

        if direction != "down" and (extreme_high - lows[i]) >= thr:
            pivots.append(
                Pivot(
                    index=extreme_high_idx, date=dates[extreme_high_idx], kind="high",
                    price=float(extreme_high), confirmed_index=i,
                )
            )
            direction = "down"
            extreme_low, extreme_low_idx = lows[i], i
        elif direction != "up" and (highs[i] - extreme_low) >= thr:
            pivots.append(
                Pivot(
                    index=extreme_low_idx, date=dates[extreme_low_idx], kind="low",
                    price=float(extreme_low), confirmed_index=i,
                )
            )
            direction = "up"
            extreme_high, extreme_high_idx = highs[i], i
    return pivots


# --- A2/A3/TECH-ZTOL-024/TECH-ZONE-025: zone width and center ---------------


def zone_tolerance(atr_at_pivot: float, pivot_price: float) -> float:
    """Level-zone tolerance (TECH-ZTOL-024): `max(0.50*ATR_at_pivot, 0.0075*pivot_price)`."""
    return max(0.50 * atr_at_pivot, 0.0075 * pivot_price)


def weighted_median(values: list[float], weights: list[float]) -> float:
    """Weighted median: the value at which cumulative weight first reaches
    half the total weight, sorted ascending by value. Falls back to the
    plain median if total weight is non-positive."""
    pairs = sorted(zip(values, weights), key=lambda vw: vw[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        return statistics.median(values)
    half = total / 2.0
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= half:
            return v
    return pairs[-1][0]


@dataclass
class ZoneCandidate:
    """A clustered group of same-kind pivots, before touch counting."""

    kind: Literal["high", "low"]
    center: float
    lower: float
    upper: float
    members: list[Pivot] = field(default_factory=list)


def cluster_zones(pivots: list[Pivot], atr: pd.Series, price: pd.Series) -> list[ZoneCandidate]:
    """Cluster overlapping pivot tolerance intervals into zones (A2/A3).

    Highs and lows are clustered separately. `atr` supplies `ATR_at_pivot`
    (looked up per pivot's bar index) for `zone_tolerance`; `price` is the
    full close-price series used only to anchor "now" (`len(price)-1`) for
    each pivot's `age_sessions` in the recency weighting.
    """
    as_of_index = len(price) - 1
    zones: list[ZoneCandidate] = []

    for kind in ("high", "low"):
        enriched: list[tuple[Pivot, float]] = []
        for p in pivots:
            if p.kind != kind or p.index >= len(atr):
                continue
            a = atr.iloc[p.index]
            if pd.isna(a):
                continue
            enriched.append((p, zone_tolerance(float(a), p.price)))
        enriched.sort(key=lambda pt: pt[0].price)

        clusters: list[list[tuple[Pivot, float]]] = []
        current: list[tuple[Pivot, float]] = []
        current_upper: float | None = None
        for p, tol in enriched:
            lo, hi = p.price - tol, p.price + tol
            if not current:
                current, current_upper = [(p, tol)], hi
            elif lo <= current_upper:
                current.append((p, tol))
                current_upper = max(current_upper, hi)
            else:
                clusters.append(current)
                current, current_upper = [(p, tol)], hi
        if current:
            clusters.append(current)

        for cluster in clusters:
            weights = [_recency_weight(as_of_index - p.index) for p, _ in cluster]
            prices = [p.price for p, _ in cluster]
            tols = [tol for _, tol in cluster]
            center = weighted_median(prices, weights)
            half_width = weighted_median(tols, weights)
            zones.append(
                ZoneCandidate(
                    kind=kind, center=center, lower=center - half_width,
                    upper=center + half_width, members=[p for p, _ in cluster],
                )
            )
    return zones


# --- A4/A5/TECH-NEFF-026/TECH-REJ-027: independent touches ------------------


@dataclass(frozen=True)
class TouchRecord:
    date: str
    index: int
    pivot_price: float
    rejection_atr: float
    volume_ratio: float
    age_sessions: int


def count_touches(
    zone: ZoneCandidate, df: pd.DataFrame, atr: pd.Series, timeframe: str, as_of_index: int | None = None,
) -> list[TouchRecord]:
    """Independent, valid touches of `zone` (A4/A5).

    A touch only counts once per swing: touches within `min_gap` bars of the
    previously counted touch (5 daily sessions / 2 weekly bars) are dropped
    as non-independent. A touch only counts if it produced a valid rejection
    (`>=0.5 ATR` reaction away from the zone center within the next 3 bars).
    """
    min_gap = 5 if timeframe == "daily" else 2
    n = len(df)
    if as_of_index is None:
        as_of_index = n - 1
    vr_series = indicators.volume_ratio(df["volume"])

    touches: list[TouchRecord] = []
    last_index: int | None = None
    for p in sorted(zone.members, key=lambda p: p.index):
        if last_index is not None and (p.index - last_index) < min_gap:
            continue
        window_end = min(p.index + 3, n - 1)
        if window_end <= p.index or p.index >= len(atr):
            continue
        a = atr.iloc[p.index]
        if pd.isna(a) or a == 0:
            continue
        if zone.kind == "high":
            future_low = df["low"].iloc[p.index + 1 : window_end + 1].min()
            reaction = (zone.center - future_low) / a
        else:
            future_high = df["high"].iloc[p.index + 1 : window_end + 1].max()
            reaction = (future_high - zone.center) / a
        if reaction < 0.5:
            continue
        vr = float(vr_series.iloc[p.index]) if p.index < len(vr_series) else float("nan")
        touches.append(
            TouchRecord(
                date=p.date, index=p.index, pivot_price=p.price, rejection_atr=float(reaction),
                volume_ratio=vr, age_sessions=as_of_index - p.index,
            )
        )
        last_index = p.index
    return touches


# --- A6/A7: classification and strength -------------------------------------


@dataclass
class ZoneState:
    """A zone plus its independent touches and confluence count — the input
    to `strength` and `classify`."""

    kind: Literal["high", "low"]
    center: float
    lower: float
    upper: float
    timeframe: Literal["daily", "weekly"]
    touches: list[TouchRecord]
    confluence_count: int = 0


def strength(zone: ZoneState) -> float:
    """Level strength score (A7/TECH-LSTR-028), exactly:

    `30*min(N_eff/4,1) + 20*min(median_reaction_ATR/2,1) +
    15*min(median_volume_ratio/1.5,1) + 15*exp(-ln2*age_latest/126) +
    (10 weekly | 5 daily) + 10*min(confluence_count/3,1)`, capped at 100.
    """
    touches = zone.touches
    if not touches:
        return 0.0

    n_eff = sum(_recency_weight(t.age_sessions) for t in touches)
    touch_pts = 30 * min(n_eff / 4, 1.0)

    median_reaction = statistics.median(t.rejection_atr for t in touches)
    reaction_pts = 20 * min(median_reaction / 2, 1.0)

    vol_ratios = [t.volume_ratio for t in touches if not math.isnan(t.volume_ratio)]
    median_vol_ratio = statistics.median(vol_ratios) if vol_ratios else 0.0
    volume_pts = 15 * min(median_vol_ratio / 1.5, 1.0)

    age_latest = min(t.age_sessions for t in touches)
    recency_pts = 15 * _recency_weight(age_latest)

    timeframe_pts = 10.0 if zone.timeframe == "weekly" else 5.0
    confluence_pts = 10 * min(zone.confluence_count / 3, 1.0)

    total = touch_pts + reaction_pts + volume_pts + recency_pts + timeframe_pts + confluence_pts
    return min(100.0, total)


def classify(zone: ZoneState, break_status: Literal["none", "broken", "role_reversed"] = "none") -> str:
    """Level label (A6). `break_status` (from price-action scanning, see
    `_zone_break_status`) takes precedence over the touch-count rules."""
    if break_status == "role_reversed":
        return "role_reversed"
    if break_status == "broken":
        return "broken"

    n = len(zone.touches)
    if n == 0:
        return "candidate"

    median_reaction = statistics.median(t.rejection_atr for t in zone.touches)
    vol_ratios = [t.volume_ratio for t in zone.touches if not math.isnan(t.volume_ratio)]
    any_vol_ge_15 = any(v >= 1.5 for v in vol_ratios)

    if n >= 3 or (n == 2 and median_reaction >= 1.0 and any_vol_ge_15):
        return "strong"
    if n >= 2:
        return "confirmed"
    return "candidate"


# --- C/TECH-BRK-030..ROLE-033: breakout / failed breakout / role reversal ---


def _follow_through(df: pd.DataFrame, zone: ZoneState, atr: pd.Series, i: int, n: int) -> bool:
    """Two consecutive closes above/below the buffered level, OR one close
    plus 3 sessions with no close back inside the zone."""
    if zone.kind == "high":
        two_consec = False
        if i + 1 < n and i + 1 < len(atr):
            a_next = atr.iloc[i + 1]
            two_consec = not pd.isna(a_next) and df["close"].iloc[i + 1] > zone.upper + 0.25 * a_next
        no_close_inside_3 = (i + 3 < n) and all(df["close"].iloc[j] > zone.upper for j in range(i + 1, i + 4))
    else:
        two_consec = False
        if i + 1 < n and i + 1 < len(atr):
            a_next = atr.iloc[i + 1]
            two_consec = not pd.isna(a_next) and df["close"].iloc[i + 1] < zone.lower - 0.25 * a_next
        no_close_inside_3 = (i + 3 < n) and all(df["close"].iloc[j] < zone.lower for j in range(i + 1, i + 4))
    return two_consec or no_close_inside_3


def _first_confirmed_breakout(df: pd.DataFrame, zone: ZoneState, atr: pd.Series, vr: pd.Series) -> int | None:
    n = len(df)
    for i in range(1, n):
        a = atr.iloc[i] if i < len(atr) else float("nan")
        if pd.isna(a):
            continue
        if zone.kind == "high":
            trigger = df["close"].iloc[i] > zone.upper + 0.25 * a
        else:
            trigger = df["close"].iloc[i] < zone.lower - 0.25 * a
        if not trigger:
            continue
        v = vr.iloc[i] if i < len(vr) else float("nan")
        if pd.isna(v) or v < 1.5:
            continue
        if _follow_through(df, zone, atr, i, n):
            return i
    return None


def breakout_confirmed(df: pd.DataFrame, zone: ZoneState, atr: pd.Series) -> bool:
    """Confirmed breakout (TECH-BCONF-031): ALL of close > zone_high (or, for
    a support zone, close < zone_low) buffered by `0.25*ATR14`; volume ratio
    `>=1.5`; AND follow-through (`_follow_through`)."""
    vr = indicators.volume_ratio(df["volume"])
    return _first_confirmed_breakout(df, zone, atr, vr) is not None


def failed_breakout(df: pd.DataFrame, zone: ZoneState, atr: pd.Series, breakout_index: int) -> bool:
    """Failed breakout (TECH-FBRK-032): close back inside/below (support:
    inside/above) the zone within 3 sessions of `breakout_index`."""
    n = len(df)
    end = min(breakout_index + 3, n - 1)
    if zone.kind == "high":
        return any(df["close"].iloc[j] <= zone.upper for j in range(breakout_index + 1, end + 1))
    return any(df["close"].iloc[j] >= zone.lower for j in range(breakout_index + 1, end + 1))


def _zone_break_status(df: pd.DataFrame, zone: ZoneState, atr: pd.Series) -> Literal["none", "broken", "role_reversed"]:
    """Scan `df` for the first confirmed breakout of `zone` and, if found,
    whether it later failed, held (broken), or role-reversed (retest +
    reclaim + >=0.5 ATR reaction, TECH-ROLE-033)."""
    n = len(df)
    vr = indicators.volume_ratio(df["volume"])
    idx = _first_confirmed_breakout(df, zone, atr, vr)
    if idx is None:
        return "none"
    if failed_breakout(df, zone, atr, idx):
        return "none"

    for j in range(idx + 1, n):
        a = atr.iloc[j] if j < len(atr) else float("nan")
        if pd.isna(a):
            continue
        retested = zone.lower <= df["close"].iloc[j] <= zone.upper
        if not retested:
            continue
        window_end = min(j + 3, n - 1)
        if window_end <= j:
            continue
        if zone.kind == "high":
            reaction = (df["high"].iloc[j + 1 : window_end + 1].max() - zone.center) / a
            reclaimed = df["close"].iloc[j + 1 : window_end + 1].max() > zone.upper
        else:
            reaction = (zone.center - df["low"].iloc[j + 1 : window_end + 1].min()) / a
            reclaimed = df["close"].iloc[j + 1 : window_end + 1].min() < zone.lower
        if reaction >= 0.5 and reclaimed:
            return "role_reversed"
    return "broken"


# --- D/TECH-AVWAP-034: anchored VWAP ----------------------------------------


def avwap(df: pd.DataFrame, anchor_date: str) -> float:
    """Anchored VWAP (TECH-AVWAP-034): `sum(TypicalPrice*Volume)/sum(Volume)`
    from `anchor_date` (inclusive) to the last row of `df`; TP=(H+L+C)/3."""
    dates = df["date"].astype(str)
    sub = df.loc[dates >= anchor_date]
    if sub.empty or sub["volume"].sum() == 0:
        return float("nan")
    tp = (sub["high"] + sub["low"] + sub["close"]) / 3.0
    return float((tp * sub["volume"]).sum() / sub["volume"].sum())


# --- E/TECH-VP-035: approximate volume profile ------------------------------


@dataclass(frozen=True)
class VolumeProfileBin:
    low: float
    high: float
    volume: float


@dataclass(frozen=True)
class VolumeProfile:
    bins: list[VolumeProfileBin]
    poc: float
    hvn: list[VolumeProfileBin]
    lvn: list[VolumeProfileBin]


def volume_profile(df: pd.DataFrame, atr: pd.Series) -> VolumeProfile:
    """Approximate volume-at-price profile (E/TECH-VP-035):
    `bin_width = max(0.50*ATR14, 0.005*current_price)`; POC = max-volume bin
    (approximated by daily typical price); HVN = bins above p75; LVN = bins
    below p25 of bin volume."""
    current_price = float(df["close"].iloc[-1])
    current_atr = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
    bin_width = max(0.50 * current_atr, 0.005 * current_price)
    if bin_width <= 0:
        bin_width = max(current_price * 0.005, 1e-6)

    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    min_p, max_p = float(tp.min()), float(tp.max())
    span = max_p - min_p
    n_bins = max(1, int(np.ceil(span / bin_width))) if span > 0 else 1
    edges = min_p + np.arange(n_bins + 1) * bin_width

    bin_idx = np.clip(((tp.to_numpy() - min_p) / bin_width).astype(int), 0, n_bins - 1)
    volumes = np.zeros(n_bins)
    for idx, v in zip(bin_idx, df["volume"].to_numpy()):
        volumes[idx] += v

    bins = [VolumeProfileBin(low=float(edges[i]), high=float(edges[i + 1]), volume=float(volumes[i])) for i in range(n_bins)]
    poc_i = int(np.argmax(volumes))
    poc = (bins[poc_i].low + bins[poc_i].high) / 2

    p75 = float(np.percentile(volumes, 75))
    p25 = float(np.percentile(volumes, 25))
    hvn = [b for b in bins if b.volume > p75]
    lvn = [b for b in bins if b.volume < p25]
    return VolumeProfile(bins=bins, poc=poc, hvn=hvn, lvn=lvn)


# --- F/TECH-GAP-020/TECH-GHOLD-021: earnings gaps ---------------------------


def gap_is_material(gap_abs: float, atr: float, prior_close: float) -> bool:
    """Material earnings gap (F): `|gap| >= max(1.0*ATR, 0.03*prior_close)`."""
    return gap_abs >= max(1.0 * atr, 0.03 * prior_close)


def _gap_fill_status(df: pd.DataFrame, gap_index: int, prior_close: float, gap_open: float, gap_abs: float) -> str:
    if gap_abs == 0:
        return "fully_filled"
    up_gap = gap_abs > 0
    closes_after = df["close"].iloc[gap_index + 1 :]
    if closes_after.empty:
        return "open"

    filled_mask = closes_after <= prior_close if up_gap else closes_after >= prior_close
    if not filled_mask.any():
        partial_mask = closes_after < gap_open if up_gap else closes_after > gap_open
        return "partially_filled" if partial_mask.any() else "open"

    fill_pos = filled_mask[filled_mask].index[0]
    after_fill = df["close"].loc[fill_pos:].iloc[1:]
    if after_fill.empty:
        return "fully_filled"
    reclaimed = (after_fill > gap_open).any() if up_gap else (after_fill < gap_open).any()
    return "reclaimed" if reclaimed else "fully_filled"


def earnings_gaps(df: pd.DataFrame, earnings_dates: list[str], atr: pd.Series) -> list[EarningsGap]:
    """Earnings-gap levels (F/TECH-GAP-020/TECH-GHOLD-021) for every date in
    `earnings_dates` that has a matching row in `df` and a prior session.
    `earnings_dates` are expected to already be resolved to each event's
    first regular gap session (per TECH-GAP-020's "first regular session
    after after-hours release" rule) — this function does not itself infer
    that mapping from a separate after-hours timestamp.
    """
    dates = df["date"].astype(str).tolist()
    date_to_index = {d: i for i, d in enumerate(dates)}
    records: list[EarningsGap] = []

    for ed in earnings_dates:
        i = date_to_index.get(ed)
        if i is None or i == 0:
            continue
        prior_close = float(df["close"].iloc[i - 1])
        gap_open = float(df["open"].iloc[i])
        gap_high = float(df["high"].iloc[i])
        gap_low = float(df["low"].iloc[i])
        gap_abs = gap_open - prior_close
        a = float(atr.iloc[i]) if i < len(atr) and not pd.isna(atr.iloc[i]) else 0.0
        material = gap_is_material(abs(gap_abs), a, prior_close)
        gap_percent = (gap_abs / prior_close * 100) if prior_close else 0.0
        midpoint = (gap_open + prior_close) / 2

        def hold_ratio(k: int) -> float | None:
            j = i + k
            if j >= len(df) or gap_abs == 0:
                return None
            return (float(df["close"].iloc[j]) - prior_close) / gap_abs

        records.append(
            EarningsGap(
                event_date=ed, prior_close=prior_close, gap_open=gap_open, gap_high=gap_high,
                gap_low=gap_low, gap_midpoint=midpoint, gap_percent=gap_percent, material=material,
                day1_hold_ratio=hold_ratio(1), day5_hold_ratio=hold_ratio(5), day20_hold_ratio=hold_ratio(20),
                fill_status=_gap_fill_status(df, i, prior_close, gap_open, gap_abs),
            )
        )
    return records


# --- I: final ranking --------------------------------------------------------


def rank_levels(strength: float, recency: float, cross_lens_confluence: float, liquidity_confidence: float) -> float:
    """Relevance ranking (I): `0.45*strength + 0.25*recency +
    0.20*cross_lens_confluence + 0.10*liquidity_confidence`. All inputs
    0-100."""
    return 0.45 * strength + 0.25 * recency + 0.20 * cross_lens_confluence + 0.10 * liquidity_confidence


# --- compute_levels: full pipeline ------------------------------------------


def _intervals_overlap(lo1: float, hi1: float, lo2: float, hi2: float) -> bool:
    return lo1 <= hi2 and lo2 <= hi1


def _resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["_dt"] = pd.to_datetime(d["date"])
    d = d.set_index("_dt")
    weekly = d.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(how="any")
    weekly = weekly.reset_index().rename(columns={"_dt": "date"})
    weekly["date"] = weekly["date"].dt.strftime("%Y-%m-%d")
    return weekly


def _confirmation_rule(zone_type: ZoneType) -> str:
    return (
        "Confirmed by >=2 independent touches (>=5 sessions apart daily / "
        ">=2 weeks apart weekly), each with >=0.5 ATR rejection within 3 "
        "sessions (Cerebro IMPORTANT_LEVELS_ENGINE A4/A6)."
    )


def _invalidation_rule(zone_type: ZoneType) -> str:
    side = "close > zone_high + 0.25*ATR14" if zone_type == "resistance" else "close < zone_low - 0.25*ATR14"
    return (
        f"Broken by a confirmed {side} with volume/median(50d) >= 1.5 and "
        "follow-through: two consecutive closes beyond the buffer, or one "
        "close plus three sessions with no close back inside the zone "
        "(Cerebro IMPORTANT_LEVELS_ENGINE C)."
    )


def compute_levels(
    df_daily: pd.DataFrame,
    earnings_dates: list[str],
    valuation_bands: dict[str, tuple[float, float]] | None = None,
) -> LevelsOutput:
    """Full important-levels pipeline (Cerebro J): nearest three support
    zones, nearest three resistance zones, major moving averages, key
    anchored VWAPs, and material earnings gaps.

    `valuation_bands` (optional `{label: (lower, upper)}`) is accepted per
    the task-12 interface but is not folded into `Zone.strength_0_100` here:
    Cerebro A8 fixes the strength confluence classes to five purely
    technical/volume sources, and the cross-lens valuation/technical
    confluence in section H is explicitly a *separate* 0-100 input to
    `rank_levels` (section I), consumed by a later aggregation stage
    (Task 21), not by this task's `LevelsOutput`.
    """
    df = df_daily.reset_index(drop=True)
    atr_daily = indicators.atr14(df)
    current_close = float(df["close"].iloc[-1])
    current_atr = float(atr_daily.iloc[-1]) if not pd.isna(atr_daily.iloc[-1]) else float("nan")

    daily_pivots = find_pivots(df, 3)
    daily_candidates = cluster_zones(daily_pivots, atr_daily, df["close"])

    weekly_df = _resample_weekly(df)
    weekly_atr = indicators.atr14(weekly_df)
    weekly_pivots = find_pivots(weekly_df, 2)
    weekly_candidates = cluster_zones(weekly_pivots, weekly_atr, weekly_df["close"]) if weekly_pivots else []

    zone_entries: list[tuple[ZoneCandidate, ZoneState, str]] = []
    for cand in daily_candidates:
        touches = count_touches(cand, df, atr_daily, timeframe="daily")
        if not touches:
            continue
        state = ZoneState(kind=cand.kind, center=cand.center, lower=cand.lower, upper=cand.upper,
                           timeframe="daily", touches=touches)
        zone_entries.append((cand, state, "daily"))
    for cand in weekly_candidates:
        touches = count_touches(cand, weekly_df, weekly_atr, timeframe="weekly")
        if not touches:
            continue
        state = ZoneState(kind=cand.kind, center=cand.center, lower=cand.lower, upper=cand.upper,
                           timeframe="weekly", touches=touches)
        zone_entries.append((cand, state, "weekly"))

    sma50 = indicators.sma(df["close"], 50)
    sma200 = indicators.sma(df["close"], 200)
    sma50_now = float(sma50.iloc[-1]) if not pd.isna(sma50.iloc[-1]) else None
    sma200_now = float(sma200.iloc[-1]) if not pd.isna(sma200.iloc[-1]) else None

    gap_records = earnings_gaps(df, earnings_dates, atr_daily)
    material_gaps = [g for g in gap_records if g.material]

    avwap_levels: list[AVWAPLevel] = []
    if material_gaps:
        last_gap = material_gaps[-1]
        avwap_levels.append(
            AVWAPLevel(anchor_date=last_gap.event_date, anchor_reason="material earnings gap",
                       value=avwap(df, last_gap.event_date))
        )
    dates = df["date"].astype(str)
    swing_high_pos = int(df["high"].to_numpy().argmax())
    swing_low_pos = int(df["low"].to_numpy().argmin())
    avwap_levels.append(
        AVWAPLevel(anchor_date=dates.iloc[swing_high_pos], anchor_reason="major swing high",
                   value=avwap(df, dates.iloc[swing_high_pos]))
    )
    avwap_levels.append(
        AVWAPLevel(anchor_date=dates.iloc[swing_low_pos], anchor_reason="major swing low",
                   value=avwap(df, dates.iloc[swing_low_pos]))
    )

    vol_profile = volume_profile(df, atr_daily)

    for cand, state, tf in zone_entries:
        count = 0
        other = weekly_candidates if tf == "daily" else daily_candidates
        if any(o.kind == state.kind and _intervals_overlap(state.lower, state.upper, o.lower, o.upper) for o in other):
            count += 1
        if (sma50_now is not None and state.lower <= sma50_now <= state.upper) or (
            sma200_now is not None and state.lower <= sma200_now <= state.upper
        ):
            count += 1
        if any(not math.isnan(a.value) and state.lower <= a.value <= state.upper for a in avwap_levels):
            count += 1
        if any(
            state.lower <= boundary <= state.upper
            for g in material_gaps
            for boundary in (g.gap_open, g.prior_close, g.gap_midpoint)
        ):
            count += 1
        if state.lower <= vol_profile.poc <= state.upper or any(
            _intervals_overlap(state.lower, state.upper, b.low, b.high) for b in vol_profile.hvn
        ):
            count += 1
        state.confluence_count = count

    support_zones: list[Zone] = []
    resistance_zones: list[Zone] = []
    for cand, state, tf in zone_entries:
        atr_series = atr_daily if tf == "daily" else weekly_atr
        source_df = df if tf == "daily" else weekly_df
        break_status = _zone_break_status(source_df, state, atr_series)
        status = classify(state, break_status)
        strength_score = strength(state)

        if status == "role_reversed":
            zone_type: ZoneType = "support" if state.kind == "high" else "resistance"
        else:
            zone_type = "resistance" if state.kind == "high" else "support"

        if zone_type == "resistance":
            distance_atr = (state.lower - current_close) / current_atr if current_atr and not math.isnan(current_atr) else None
            distance_percent = (state.lower - current_close) / current_close * 100
        else:
            distance_atr = (current_close - state.upper) / current_atr if current_atr and not math.isnan(current_atr) else None
            distance_percent = (current_close - state.upper) / current_close * 100

        zone = Zone(
            zone_id=f"{tf}-{zone_type}-{state.center:.2f}",
            type=zone_type,
            lower=state.lower, center=state.center, upper=state.upper,
            timeframe=tf, status=status, strength_0_100=strength_score,
            touches=[
                Touch(date=t.date, pivot_price=t.pivot_price, rejection_atr=t.rejection_atr, volume_ratio=t.volume_ratio)
                for t in state.touches
            ],
            distance_percent=distance_percent, distance_atr=distance_atr,
            confirmation_rule=_confirmation_rule(zone_type),
            invalidation_rule=_invalidation_rule(zone_type),
        )

        if status == "broken":
            continue
        if zone_type == "resistance" and zone.lower >= current_close:
            resistance_zones.append(zone)
        elif zone_type == "support" and zone.upper <= current_close:
            support_zones.append(zone)

    resistance_zones.sort(key=lambda z: abs(z.distance_atr) if z.distance_atr is not None else float("inf"))
    support_zones.sort(key=lambda z: abs(z.distance_atr) if z.distance_atr is not None else float("inf"))

    moving_averages: list[MovingAverageLevel] = []
    if sma50_now is not None:
        moving_averages.append(MovingAverageLevel(label="SMA50", value=sma50_now))
    if sma200_now is not None:
        moving_averages.append(MovingAverageLevel(label="SMA200", value=sma200_now))

    return LevelsOutput(
        nearest_support=support_zones[:3],
        nearest_resistance=resistance_zones[:3],
        moving_averages=moving_averages,
        avwaps=avwap_levels,
        earnings_gaps=material_gaps,
    )
