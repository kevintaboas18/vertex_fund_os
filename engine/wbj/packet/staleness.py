"""Staleness classification per Cerebro/shared/DATA_POLICY.md's "Staleness
defaults" table.

`staleness_state` is a pure function of a data-type name and an age in
days — it does not know how the caller computed that age. The packet
builder (`wbj.packet.builder`) is responsible for deriving `age_days` per
data type from packet inputs and calling this for each one.
"""

from __future__ import annotations

from typing import Literal

StalenessState = Literal["FRESH", "STALE"]

# Cerebro/shared/DATA_POLICY.md "Staleness defaults", in days. "Daily
# market data | 1 completed trading session" has no calendar-day figure in
# the table; per the task-10 brief we use 3 calendar days as a proxy for
# one trading session (covers weekends without a trading calendar).
THRESHOLDS_DAYS: dict[str, int] = {
    "daily_market": 3,
    "consensus": 7,
    "quarterly_fundamentals": 120,
    "market_size_study": 548,
    "peer_set": 90,
}


def staleness_state(data_type: str, age_days: float) -> StalenessState:
    """FRESH if `age_days` is within `data_type`'s threshold, else STALE.

    The boundary is inclusive: `age_days == threshold` is still FRESH,
    consistent with `wbj.packet.reconcile`'s `<=` boundary convention.

    Raises `ValueError` for an unrecognized `data_type` — callers should
    only ever pass one of `THRESHOLDS_DAYS`'s keys.
    """
    try:
        threshold = THRESHOLDS_DAYS[data_type]
    except KeyError:
        raise ValueError(f"unknown staleness data_type: {data_type!r}") from None
    return "STALE" if age_days > threshold else "FRESH"
