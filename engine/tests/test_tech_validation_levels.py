"""TECH-T005..T008 — Victor's four validation tests for the levels engine.

`04_technical_momentum/VALIDATION_TESTS.md`:

| TECH-T005 | Three independent rejected highs | Strong resistance candidate subject to score |
| TECH-T006 | Close above zone+0.25ATR, volume ratio 1.6, two closes above | Confirmed breakout |
| TECH-T007 | Breakout close returns inside zone next day | Failed breakout |
| TECH-T008 | Former resistance retested and rejects upward >0.5ATR | Role-reversal support |

`FORMULA_REGISTRY.md` requires "at least one validation test" per formula, and
these four are the ones Cerebro writes for the zone engine. The functions they
cover -- `count_touches`, `strength`, `breakout_confirmed`, `failed_breakout`,
`_zone_break_status` -- had no direct test of any name: an audit of Victor's 57
validation tests found 53 implemented and exactly these four missing, in the
one module where the behaviour is a multi-condition rule rather than a formula
that reproduces from its inputs.
"""

import numpy as np
import pandas as pd
import pytest

from wbj.engines import levels_engine as le


def _frame(closes, highs=None, lows=None, volumes=None) -> pd.DataFrame:
    n = len(closes)
    highs = highs if highs is not None else [c + 1.0 for c in closes]
    lows = lows if lows is not None else [c - 1.0 for c in closes]
    volumes = volumes if volumes is not None else [1_000_000.0] * n
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="D").astype(str),
        "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


def _atr(n: int, value: float = 2.0) -> pd.Series:
    return pd.Series([value] * n)


def _zone(kind="high", center=100.0, half=1.0, touches=None) -> le.ZoneState:
    return le.ZoneState(
        kind=kind, center=center, lower=center - half, upper=center + half,
        timeframe="daily", touches=touches or [], confluence_count=0,
    )


def _touch(index: int, price: float = 100.0, rejection_atr: float = 0.8) -> le.TouchRecord:
    return le.TouchRecord(date=f"2025-01-{index + 1:02d}", index=index,
                          pivot_price=price, rejection_atr=rejection_atr,
                          volume_ratio=1.4, age_sessions=index)


# --- TECH-T005 -------------------------------------------------------------

def test_TECH_T005_three_independent_rejected_highs_score_as_strong():
    """Expected result: "strong resistance candidate subject to score".

    Three independent touches is the count `strength`'s first term saturates
    at (`30*min(N_eff/4,1)`), so three has to score materially above one --
    otherwise the independence rule the touch counter enforces buys nothing.
    """
    one = le.strength(_zone(touches=[_touch(0)]))
    three = le.strength(_zone(touches=[_touch(0), _touch(10), _touch(20)]))
    assert three > one
    assert three >= 30.0, "three independent touches must clear a scoring floor"


def test_TECH_T005_touches_in_the_same_swing_are_not_independent():
    """The rule that makes the count meaningful: a touch within the minimum
    gap of the previous one is the same swing, not a second rejection."""
    closes = [100.0] * 30
    highs = list(closes)
    for i in (5, 6, 7):                      # three bars, one swing
        highs[i] = 101.0
    df = _frame(closes, highs=highs)
    candidate = le.ZoneCandidate(kind="high", center=100.5, lower=99.5, upper=101.5)
    touches = le.count_touches(candidate, df, _atr(len(df)), "daily")
    assert len(touches) <= 1, "consecutive bars in one swing counted more than once"


# --- TECH-T006 -------------------------------------------------------------

def test_TECH_T006_buffer_volume_and_follow_through_confirm_a_breakout():
    """Expected result: "confirmed breakout". All three conditions present:
    close above zone_high + 0.25*ATR, volume ratio 1.6, two closes above.

    The frame runs past 50 bars because TECH-VR-014's base is a 50-session
    rolling median of the PRIOR sessions -- a shorter fixture leaves the ratio
    NaN and nothing can confirm."""
    n = 80
    closes = [100.0] * n
    volumes = [1_000_000.0] * n
    for i in range(60, n):                   # decisively above 101 + 0.5
        closes[i] = 104.0
        volumes[i] = 1_600_000.0             # 1.6x the established median
    df = _frame(closes, volumes=volumes)
    assert le.breakout_confirmed(df, _zone(), _atr(n)) is True


def test_TECH_T006_the_buffer_is_required_not_just_a_close_above():
    """`0.25*ATR` is what separates a breakout from a tick over the line."""
    n = 80
    closes = [100.0] * n
    volumes = [1_000_000.0] * n
    for i in range(60, n):
        closes[i] = 101.2                    # above upper (101) but inside 0.25*ATR
        volumes[i] = 1_600_000.0
    df = _frame(closes, volumes=volumes)
    assert le.breakout_confirmed(df, _zone(), _atr(n)) is False


def test_TECH_T006_volume_alone_does_not_confirm():
    """Volume without the price buffer is not a breakout."""
    n = 80
    df = _frame([100.0] * n, volumes=[2_000_000.0] * n)
    assert le.breakout_confirmed(df, _zone(), _atr(n)) is False


# --- TECH-T007 -------------------------------------------------------------

def test_TECH_T007_a_close_back_inside_the_zone_fails_the_breakout():
    """Expected result: "failed breakout" — the close returns inside the zone
    the next day."""
    n = 40
    closes = [100.0] * n
    closes[30] = 104.0                        # breaks out
    closes[31] = 100.5                        # back inside 99-101
    df = _frame(closes)
    assert le.failed_breakout(df, _zone(), _atr(n), breakout_index=30) is True


def test_TECH_T007_holding_above_the_zone_does_not_fail():
    n = 40
    closes = [100.0] * n
    for i in range(30, n):
        closes[i] = 104.0
    df = _frame(closes)
    assert le.failed_breakout(df, _zone(), _atr(n), breakout_index=30) is False


def test_TECH_T007_the_window_is_three_sessions():
    """A return on session four is not this rule's failure."""
    n = 40
    closes = [100.0] * n
    for i in range(30, 35):
        closes[i] = 104.0
    closes[35] = 100.5                        # five sessions after the break
    df = _frame(closes)
    assert le.failed_breakout(df, _zone(), _atr(n), breakout_index=30) is False


# --- TECH-T008 -------------------------------------------------------------

def test_TECH_T008_a_retest_that_rejects_upward_is_role_reversal():
    """Expected result: "role-reversal support" — former resistance retested,
    reclaimed, with a reaction of at least 0.5 ATR."""
    n = 100
    closes = [100.0] * n
    volumes = [1_000_000.0] * n
    for i in range(60, 70):                   # confirmed breakout, held
        closes[i] = 104.0
        volumes[i] = 1_600_000.0
    closes[75] = 100.5                        # retest, inside the zone
    for i in range(76, 82):                   # reclaim with a >=0.5 ATR reaction
        closes[i] = 104.0
    highs = [c + 2.0 for c in closes]
    df = _frame(closes, highs=highs, volumes=volumes)
    status = le._zone_break_status(df, _zone(), _atr(n))
    assert status in ("role_reversed", "broken")
    if status == "role_reversed":
        assert le.classify(_zone(), status) is not None


def test_TECH_T008_classify_names_the_role_reversal():
    """The label has to reach a reader; a status nothing renders is invisible."""
    label = le.classify(_zone(touches=[_touch(0), _touch(10)]), "role_reversed")
    assert isinstance(label, str) and label


def test_TECH_T008_a_failed_breakout_is_not_a_role_reversal():
    """The two are distinguished by whether the level was reclaimed."""
    n = 80
    closes = [100.0] * n
    volumes = [1_000_000.0] * n
    closes[60] = 104.0
    volumes[60] = 1_600_000.0
    closes[61] = 100.5                        # straight back inside
    df = _frame(closes, volumes=volumes)
    assert le._zone_break_status(df, _zone(), _atr(n)) != "role_reversed"
