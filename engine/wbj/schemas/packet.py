"""Pydantic models for the wbj analysis `Packet`.

Mirrors `Cerebro/examples/INPUT_PACKET_EXAMPLE.md`'s top-level shape
(`security`, `analysis`, ...), extended per Task 10's brief: Cerebro's
example shows `fundamentals`/`market_data` only as *summary counts*
(`annual_history_years`, `daily_sessions`, ...), but the packet builder
must hand specialists actual canonical-name records, so those blocks carry
plain dicts here (Cerebro does not fix a schema for statement rows) while
`security`, `analysis`, `market_data`, `facts_table`, and `staleness` are
typed, per the task-10 brief.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from wbj.core.nullstates import Value

StalenessState = Literal["FRESH", "STALE"]


class Security(BaseModel):
    """`packet.security` — identity and currency of the analyzed security."""

    ticker: str
    exchange: str
    security_type: str
    reporting_currency: str
    valuation_currency: str


class AnalysisMeta(BaseModel):
    """`packet.analysis` — the frozen analysis clock (Phase 0, ORCHESTRATION.md)."""

    knowledge_timestamp: str
    market_timestamp: str | None = None
    industry_adapter: str


class OHLCVRow(BaseModel):
    """One split/dividend-adjusted daily bar."""

    date: str
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: float


class MarketData(BaseModel):
    """`packet.market_data` — adjusted daily bars for the security,
    benchmark, and sector index."""

    daily: list[OHLCVRow] = Field(default_factory=list)
    benchmark: list[OHLCVRow] = Field(default_factory=list)
    sector: list[OHLCVRow] = Field(default_factory=list)
    adjusted: bool = True


class Packet(BaseModel):
    """The full analysis packet handed to the six Cerebro specialists.

    `fundamentals`, `estimates`, `capital_structure`, `insiders`, and
    `institutional_holders` carry canonical-name records as plain dicts —
    Cerebro doesn't fix a schema for these (statements, estimate panels,
    ownership rows vary by source). `security`, `analysis`, `market_data`,
    `facts_table`, and `staleness` are typed.
    """

    security: Security
    analysis: AnalysisMeta
    fundamentals: dict[str, Any]
    market_data: MarketData
    estimates: dict[str, Any]
    capital_structure: dict[str, Any]
    insiders: list[Any] = Field(default_factory=list)
    institutional_holders: list[Any] = Field(default_factory=list)
    facts_table: dict[str, Value]
    staleness: dict[str, StalenessState]
    packet_hash: str = ""
