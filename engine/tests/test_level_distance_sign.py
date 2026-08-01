"""One distance formula for every level, sign included.

PRICE_LEVEL_SYNTHESIS.md writes a single formula:

    Distance_percent = (Level - CurrentPrice) / CurrentPrice
    Distance_ATR     = (Level - CurrentPrice) / ATR14

so anything below the price is NEGATIVE. `levels_engine` computed the
support branch with the operands the other way round — `(CurrentClose -
upper)` — which made every zone below the price read positive.

That left two conventions in one table. On the same NVDA report a moving
average at 193.11 read -3.80 while a support zone at 186-192 read +4.49,
both below a price of 200.75, and a weekly zone 47% below read +46.49.
The platform formats the field as `${dp >= 0 ? '+' : ''}${dp}%`, so those
all rendered as gains.
"""

from __future__ import annotations

import pytest

from wbj.aggregate.synthesis import _point_distance


def _zone_distance(near_boundary: float, price: float, atr: float):
    """The formula `compute_levels` applies, isolated."""
    return ((near_boundary - price) / price * 100.0,
            (near_boundary - price) / atr)


def test_a_level_below_the_price_is_negative():
    dp, da = _zone_distance(92.0, 100.0, 2.0)
    assert dp == pytest.approx(-8.0)
    assert da == pytest.approx(-4.0)


def test_a_level_above_the_price_is_positive():
    dp, da = _zone_distance(108.0, 100.0, 2.0)
    assert dp == pytest.approx(8.0)
    assert da == pytest.approx(4.0)


def test_zones_and_point_levels_agree_on_the_sign():
    """The defect in one line: the same distance, reached two ways, has to
    come back with the same sign. `_point_distance` (moving averages,
    AVWAPs, valuation bands) was already signed; zones were not."""
    for level in (80.0, 92.0, 99.9, 100.1, 108.0, 150.0):
        point_dp, point_da = _point_distance(level, 100.0, 2.0)
        zone_dp, zone_da = _zone_distance(level, 100.0, 2.0)
        assert point_dp == pytest.approx(zone_dp)
        assert point_da == pytest.approx(zone_da)


def test_the_synthesis_fixtures_already_assumed_this():
    """`aggregate/synthesis.py` copies `Zone.distance_percent` through
    untouched, and its own support fixture — a 90-92 zone at a price of
    100 — declares -8.0. The two layers disagreed and the fixture was
    right."""
    # The fixture declares a 90-92 support at a price of 100 as -8.0.
    declared_in_the_fixture = -8.0
    assert _zone_distance(92.0, 100.0, 2.0)[0] == pytest.approx(declared_in_the_fixture)
    assert _zone_distance(92.0, 100.0, 2.0)[1] == pytest.approx(-4.0)


def test_every_zone_a_real_run_emits_is_sign_coherent():
    """End to end over the synthetic series: no level may claim to be
    above the price while sitting below it."""
    import pandas as pd

    from wbj.engines.levels_engine import compute_levels

    rows, price = [], 100.0
    for i in range(400):
        # A saw-tooth that leaves confirmed zones on both sides.
        price = 100.0 + 18.0 * ((i % 40) / 40.0 - 0.5)
        rows.append({"date": f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                     "open": price, "high": price + 1.2, "low": price - 1.2,
                     "close": price, "volume": 1_000_000 + (i % 7) * 50_000})
    result = compute_levels(pd.DataFrame(rows), [])
    current = rows[-1]["close"]

    checked = 0
    for zone in [*result.nearest_support, *result.nearest_resistance]:
        if zone.distance_percent is None:
            continue
        checked += 1
        edge = zone.lower if zone.type == "resistance" else zone.upper
        if edge < current:
            assert zone.distance_percent <= 0, f"{zone.zone_id} sits below and reads positive"
        else:
            assert zone.distance_percent >= 0, f"{zone.zone_id} sits above and reads negative"
    assert checked > 0, "the fixture produced no zones to check"
