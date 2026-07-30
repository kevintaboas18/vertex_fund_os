"""Pydantic output schema for `wbj.engines.levels_engine`.

Mirrors the Task-12 brief's interface list and
`Cerebro/special_sauces/IMPORTANT_LEVELS_ENGINE.md` section J ("Required
output"): a `Zone` (support or resistance, daily or weekly) carries its
interval, classification status, 0-100 strength score, and the individual
`Touch` events that justify it. `LevelsOutput` is the top-level return of
`compute_levels`: nearest three support zones, nearest three resistance
zones, major moving averages, key anchored VWAPs, and material earnings
gaps (Cerebro J's last paragraph).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ZoneType = Literal["support", "resistance"]
Timeframe = Literal["daily", "weekly"]
ZoneStatus = Literal["candidate", "confirmed", "strong", "broken", "role_reversed"]
FillStatus = Literal["open", "partially_filled", "fully_filled", "reclaimed"]


class Touch(BaseModel):
    """One independent, valid touch of a zone (Cerebro A4/A5)."""

    date: str
    pivot_price: float
    rejection_atr: float
    volume_ratio: float


class Zone(BaseModel):
    """A clustered support/resistance zone (Cerebro A2-A7)."""

    zone_id: str
    type: ZoneType
    lower: float
    center: float
    upper: float
    timeframe: Timeframe
    status: ZoneStatus
    strength_0_100: float
    touches: list[Touch] = Field(default_factory=list)
    distance_percent: float | None = None
    distance_atr: float | None = None
    confirmation_rule: str
    invalidation_rule: str


class MovingAverageLevel(BaseModel):
    """A major moving average reported as a level (Cerebro A8 class 2)."""

    label: str
    value: float


class AVWAPLevel(BaseModel):
    """An anchored VWAP with its required anchor rationale (Cerebro D)."""

    anchor_date: str
    anchor_reason: str
    value: float


class EarningsGap(BaseModel):
    """A material earnings gap and its hold/fill tracking (Cerebro F)."""

    event_date: str
    prior_close: float
    gap_open: float
    gap_high: float
    gap_low: float
    gap_midpoint: float
    gap_percent: float
    material: bool
    day1_hold_ratio: float | None = None
    day5_hold_ratio: float | None = None
    day20_hold_ratio: float | None = None
    fill_status: FillStatus = "open"


class LevelsOutput(BaseModel):
    """Return type of `compute_levels` (Cerebro J)."""

    nearest_support: list[Zone] = Field(default_factory=list)
    nearest_resistance: list[Zone] = Field(default_factory=list)
    moving_averages: list[MovingAverageLevel] = Field(default_factory=list)
    avwaps: list[AVWAPLevel] = Field(default_factory=list)
    earnings_gaps: list[EarningsGap] = Field(default_factory=list)
