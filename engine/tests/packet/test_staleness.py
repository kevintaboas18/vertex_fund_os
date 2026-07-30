"""Tests for wbj.packet.staleness.staleness_state, per Cerebro/shared/
DATA_POLICY.md's "Staleness defaults" table.
"""

import pytest

from wbj.packet.staleness import staleness_state

_THRESHOLDS = [
    ("daily_market", 3),
    ("consensus", 7),
    ("quarterly_fundamentals", 120),
    ("market_size_study", 548),
    ("peer_set", 90),
]


@pytest.mark.parametrize("data_type,threshold", _THRESHOLDS)
def test_fresh_below_threshold(data_type, threshold):
    assert staleness_state(data_type, threshold - 1) == "FRESH"


@pytest.mark.parametrize("data_type,threshold", _THRESHOLDS)
def test_fresh_at_exactly_threshold(data_type, threshold):
    assert staleness_state(data_type, threshold) == "FRESH"


@pytest.mark.parametrize("data_type,threshold", _THRESHOLDS)
def test_stale_just_past_threshold(data_type, threshold):
    assert staleness_state(data_type, threshold + 1) == "STALE"


@pytest.mark.parametrize("data_type,_threshold", _THRESHOLDS)
def test_fresh_at_zero_age(data_type, _threshold):
    assert staleness_state(data_type, 0) == "FRESH"


def test_unknown_data_type_raises_value_error():
    with pytest.raises(ValueError, match="unknown staleness data_type"):
        staleness_state("not_a_real_type", 1)
