"""Tests for wbj.packet.builder.build_packet.

Uses fake provider objects (see tests/fixtures/packet/make_packet_fixture.py)
reading the fmp/edgar/finnhub/fred fixture JSON — no MockTransport needed
at this layer per the task-10 design decision.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from wbj.packet import builder
from wbj.packet.builder import PacketRejected, Providers, build_packet
from wbj.schemas.packet import Packet

from .conftest import FakeEdgarProvider, FakeFinnhubProvider, FakeFMPProvider, FakeFredProvider, generate_ohlcv_sessions


# --- canonical field mapping -------------------------------------------------


def test_annual_fundamentals_use_canonical_field_names(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    latest = packet.fundamentals["annual"][0]
    assert latest["net_income"] == 72880000000
    assert latest["operating_cash_flow"] == 64089000000
    assert latest["capex"] == -3236000000
    assert latest["diluted_shares"] == 24700000000
    assert latest["cash"] == 8589000000
    assert latest["total_debt"] == 9710000000
    assert latest["revenue"] == 130497000000
    assert latest["ebit"] == 81453000000
    # raw FMP keys must not leak through
    assert "netIncome" not in latest
    assert "weightedAverageShsOutDil" not in latest


def test_annual_fundamentals_has_five_fiscal_years(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    assert len(packet.fundamentals["annual"]) == 5


def test_quarterly_fundamentals_use_canonical_field_names(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    latest_q = packet.fundamentals["quarterly"][0]
    assert latest_q["net_income"] == 18775000000
    assert latest_q["operating_cash_flow"] == 27414000000


def test_annual_fundamentals_maps_additional_statement_fields(fake_providers, fixed_now):
    """Task: fuller FMP statement mapping -- interest expense, D&A, SG&A,
    accounts payable, retained earnings, and net PP&E should all appear
    under their canonical names once FMP's raw statement rows carry them,
    even though today's specialist formulas for RSK-ICOV-011/AQI-023/etc.
    read these from `overlay` rather than `Packet.fundamentals` (see
    CANONICAL_FIELD_MAP's docstring)."""
    income = copy.deepcopy(FakeFMPProvider()._get("income_annual"))
    income[0]["interestExpense"] = 257000000
    income[0]["sellingGeneralAndAdministrativeExpenses"] = 3200000000
    balance = copy.deepcopy(FakeFMPProvider()._get("balance_annual"))
    balance[0]["accountPayables"] = 6300000000
    balance[0]["retainedEarnings"] = 55000000000
    balance[0]["propertyPlantEquipmentNet"] = 6200000000
    fmp = FakeFMPProvider(
        ohlcv=fake_providers.fmp._ohlcv,
        benchmark_ohlcv=fake_providers.fmp._benchmark_ohlcv,
        overrides={"income_annual": income, "balance_annual": balance},
    )
    providers = Providers(fmp=fmp, edgar=fake_providers.edgar, finnhub=fake_providers.finnhub, fred=fake_providers.fred)

    packet = build_packet("NVDA", providers, fixed_now)

    latest = packet.fundamentals["annual"][0]
    assert latest["interest_expense"] == 257000000
    assert latest["sga"] == 3200000000
    assert latest["accounts_payable"] == 6300000000
    assert latest["retained_earnings"] == 55000000000
    assert latest["ppe_net"] == 6200000000
    # raw FMP keys must not leak through
    assert "interestExpense" not in latest
    assert "accountPayables" not in latest
    assert "propertyPlantEquipmentNet" not in latest


# --- >=252 daily sessions enforced ------------------------------------------


def test_full_ohlcv_history_accepted(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    assert len(packet.market_data.daily) >= 252


def test_fewer_than_252_sessions_rejects(fixed_now):
    short_ohlcv = generate_ohlcv_sessions(end=(fixed_now - timedelta(days=1)).date(), sessions=100)
    providers = Providers(
        fmp=FakeFMPProvider(ohlcv=short_ohlcv),
        edgar=FakeEdgarProvider(),
        finnhub=FakeFinnhubProvider(),
        fred=FakeFredProvider(),
    )

    with pytest.raises(PacketRejected, match="daily sessions"):
        build_packet("NVDA", providers, fixed_now)


def test_exactly_252_sessions_accepted(fixed_now):
    ohlcv = generate_ohlcv_sessions(end=(fixed_now - timedelta(days=1)).date(), sessions=252)
    providers = Providers(
        fmp=FakeFMPProvider(ohlcv=ohlcv),
        edgar=FakeEdgarProvider(),
        finnhub=FakeFinnhubProvider(),
        fred=FakeFredProvider(),
    )

    packet = build_packet("NVDA", providers, fixed_now)

    assert len(packet.market_data.daily) == 252


# --- benchmark/sector series --------------------------------------------


def test_benchmark_and_sector_populated_from_spy(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    assert len(packet.market_data.benchmark) > 0
    assert packet.market_data.benchmark == packet.market_data.sector
    # Genuinely different data from the stock's own series (fixture uses a
    # different synthetic start price for the SPY-like benchmark).
    assert packet.market_data.benchmark[0].close != packet.market_data.daily[0].close


def test_benchmark_aligned_to_stock_trading_dates(fixed_now):
    """Inner-join alignment: a benchmark bar on a date the stock didn't
    trade must be dropped; a stock date missing from the raw benchmark
    series must not appear in the aligned output either."""
    stock_ohlcv = generate_ohlcv_sessions(end=(fixed_now - timedelta(days=1)).date(), sessions=260)
    stock_dates = {bar["date"] for bar in stock_ohlcv}
    benchmark_ohlcv = generate_ohlcv_sessions(
        end=(fixed_now - timedelta(days=1)).date(), sessions=260, start_price=400.0
    )
    # Inject one extra benchmark bar on a date the stock never traded.
    benchmark_ohlcv = [{**benchmark_ohlcv[0], "date": "1999-01-01"}, *benchmark_ohlcv]

    providers = Providers(
        fmp=FakeFMPProvider(ohlcv=stock_ohlcv, benchmark_ohlcv=benchmark_ohlcv),
        edgar=FakeEdgarProvider(),
        finnhub=FakeFinnhubProvider(),
        fred=FakeFredProvider(),
    )

    packet = build_packet("NVDA", providers, fixed_now)

    benchmark_dates = {row.date for row in packet.market_data.benchmark}
    assert "1999-01-01" not in benchmark_dates
    assert benchmark_dates <= stock_dates


def test_benchmark_empty_when_provider_returns_no_data(fake_providers, fixed_now):
    fmp = FakeFMPProvider(
        ohlcv=fake_providers.fmp._ohlcv, overrides={"benchmark_ohlcv": []}
    )
    providers = Providers(fmp=fmp, edgar=fake_providers.edgar, finnhub=fake_providers.finnhub, fred=fake_providers.fred)

    packet = build_packet("NVDA", providers, fixed_now)

    assert packet.market_data.benchmark == []
    assert packet.market_data.sector == []


# --- hash stability -----------------------------------------------------


def test_hash_stable_across_rebuilds_with_same_inputs(fake_providers, fixed_now):
    packet_a = build_packet("NVDA", fake_providers, fixed_now)
    packet_b = build_packet("NVDA", fake_providers, fixed_now)

    assert packet_a.packet_hash == packet_b.packet_hash
    assert packet_a.packet_hash != ""


def test_hash_changes_when_one_input_changes(fake_providers, fixed_now):
    packet_a = build_packet("NVDA", fake_providers, fixed_now)

    mutated_profile = copy.deepcopy(fake_providers.fmp._get("profile"))
    mutated_profile[0]["price"] = mutated_profile[0]["price"] + 1.0
    mutated_fmp = FakeFMPProvider(
        ohlcv=fake_providers.fmp._ohlcv, overrides={"profile": mutated_profile}
    )
    mutated_providers = Providers(
        fmp=mutated_fmp, edgar=fake_providers.edgar, finnhub=fake_providers.finnhub, fred=fake_providers.fred
    )
    packet_b = build_packet("NVDA", mutated_providers, fixed_now)

    assert packet_a.packet_hash != packet_b.packet_hash


def test_hash_excludes_itself_from_the_hashed_payload(fake_providers, fixed_now):
    """Sanity check: packet_hash is not self-referential garbage — it's a
    real 64-char hex sha256 digest."""
    packet = build_packet("NVDA", fake_providers, fixed_now)

    assert len(packet.packet_hash) == 64
    int(packet.packet_hash, 16)  # raises ValueError if not hex


# --- staleness table ------------------------------------------------------


def test_daily_market_staleness_fresh_for_recent_ohlcv(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    assert packet.staleness["daily_market"] == "FRESH"


def test_quarterly_fundamentals_staleness_stale_for_old_filings(fake_providers, fixed_now):
    # fixture quarterly statements are dated ~2025-04, `fixed_now` is
    # 2026-07 -> well past the 120-day quarterly_fundamentals threshold.
    packet = build_packet("NVDA", fake_providers, fixed_now)

    assert packet.staleness["quarterly_fundamentals"] == "STALE"


def test_quarterly_fundamentals_staleness_fresh_for_recent_filing(fixed_now):
    recent_date = (fixed_now - timedelta(days=10)).date().isoformat()
    income_q = [{**row, "date": recent_date} for row in FakeFMPProvider()._get("income_quarterly")]
    ohlcv = generate_ohlcv_sessions(end=(fixed_now - timedelta(days=1)).date())
    fmp = FakeFMPProvider(ohlcv=ohlcv, overrides={"income_quarterly": income_q})
    providers = Providers(fmp=fmp, edgar=FakeEdgarProvider(), finnhub=FakeFinnhubProvider(), fred=FakeFredProvider())

    packet = build_packet("NVDA", providers, fixed_now)

    assert packet.staleness["quarterly_fundamentals"] == "FRESH"


def test_consensus_staleness_stale_for_old_earnings_print(fake_providers, fixed_now):
    # fixture fmp earnings_calendar's latest actual print is 2026-05-27,
    # 50 days before fixed_now (2026-07-16) -> past the 7-day threshold.
    packet = build_packet("NVDA", fake_providers, fixed_now)

    assert packet.staleness["consensus"] == "STALE"


def test_peer_set_staleness_stale_for_old_13f(fake_providers, fixed_now):
    # fixture institutional_holders dateReported is 2026-03-31, well past
    # the 90-day peer_set threshold relative to fixed_now.
    packet = build_packet("NVDA", fake_providers, fixed_now)

    assert packet.staleness["peer_set"] == "STALE"


# --- facts table --------------------------------------------------------


def test_facts_table_revenue_reconciled_from_fmp_and_edgar(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    revenue = packet.facts_table["revenue"]
    assert revenue.is_valid
    assert revenue.value == 130497000000


def test_facts_table_diluted_shares_reconciled(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    shares = packet.facts_table["diluted_shares"]
    assert shares.is_valid
    assert shares.value == 24700000000


def test_facts_table_cash_reconciled(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    cash = packet.facts_table["cash"]
    assert cash.is_valid
    assert cash.value == 8589000000


def test_facts_table_total_debt_reconciled(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    total_debt = packet.facts_table["total_debt"]
    assert total_debt.is_valid
    assert total_debt.value == 9710000000


def test_facts_table_price_is_fmp_only(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    price = packet.facts_table["price"]
    assert price.is_valid
    assert price.value == 120.5
    assert price.source_name == "FMP"


def test_facts_table_revenue_conflicted_when_sources_disagree(fixed_now):
    companyfacts = FakeEdgarProvider()._companyfacts
    import copy as _copy

    conflicted_facts = _copy.deepcopy(companyfacts)
    conflicted_facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"][1]["val"] = 200000000000  # >5% off FMP's 130.497B
    providers = Providers(
        fmp=FakeFMPProvider(ohlcv=generate_ohlcv_sessions(end=(fixed_now - timedelta(days=1)).date())),
        edgar=FakeEdgarProvider(companyfacts=conflicted_facts),
        finnhub=FakeFinnhubProvider(),
        fred=FakeFredProvider(),
    )

    packet = build_packet("NVDA", providers, fixed_now)

    assert packet.facts_table["revenue"].is_null
    assert packet.facts_table["revenue"].state == "CONFLICTED"


# --- hard rejects ---------------------------------------------------------


def test_missing_currency_rejects(fake_providers, fixed_now):
    no_currency_profile = copy.deepcopy(fake_providers.fmp._get("profile"))
    del no_currency_profile[0]["currency"]
    fmp = FakeFMPProvider(ohlcv=fake_providers.fmp._ohlcv, overrides={"profile": no_currency_profile})
    providers = Providers(fmp=fmp, edgar=fake_providers.edgar, finnhub=fake_providers.finnhub, fred=fake_providers.fred)

    with pytest.raises(PacketRejected, match="currency"):
        build_packet("NVDA", providers, fixed_now)


def test_missing_timestamps_rejects_when_no_market_data_at_all(fixed_now):
    fmp = FakeFMPProvider(ohlcv=[])
    finnhub = FakeFinnhubProvider(overrides={"quote": None})
    providers = Providers(fmp=fmp, edgar=FakeEdgarProvider(), finnhub=finnhub, fred=FakeFredProvider())

    with pytest.raises(PacketRejected, match="timestamp"):
        build_packet("NVDA", providers, fixed_now)


def test_no_diluted_share_count_from_any_source_rejects(fixed_now):
    income_annual_no_shares = [
        {k: v for k, v in row.items() if k != "weightedAverageShsOutDil"}
        for row in FakeFMPProvider()._get("income_annual")
    ]
    income_quarterly_no_shares = [
        {k: v for k, v in row.items() if k != "weightedAverageShsOutDil"}
        for row in FakeFMPProvider()._get("income_quarterly")
    ]
    companyfacts_no_shares = copy.deepcopy(FakeEdgarProvider()._companyfacts)
    del companyfacts_no_shares["facts"]["us-gaap"]["WeightedAverageNumberOfDilutedSharesOutstanding"]
    del companyfacts_no_shares["facts"]["dei"]["EntityCommonStockSharesOutstanding"]

    fmp = FakeFMPProvider(
        ohlcv=generate_ohlcv_sessions(end=(fixed_now - timedelta(days=1)).date()),
        overrides={
            "income_annual": income_annual_no_shares,
            "income_quarterly": income_quarterly_no_shares,
        },
    )
    edgar = FakeEdgarProvider(companyfacts=companyfacts_no_shares)
    providers = Providers(fmp=fmp, edgar=edgar, finnhub=FakeFinnhubProvider(), fred=FakeFredProvider())

    with pytest.raises(PacketRejected, match="diluted share"):
        build_packet("NVDA", providers, fixed_now)


def test_diluted_shares_falls_back_to_edgar_basic_shares_when_no_weighted_tag(fixed_now):
    """EDGAR-only fallback: no FMP diluted count, no EDGAR weighted-diluted
    tag, but EDGAR's dei:EntityCommonStockSharesOutstanding is present ->
    should NOT reject."""
    income_annual_no_shares = [
        {k: v for k, v in row.items() if k != "weightedAverageShsOutDil"}
        for row in FakeFMPProvider()._get("income_annual")
    ]
    income_quarterly_no_shares = [
        {k: v for k, v in row.items() if k != "weightedAverageShsOutDil"}
        for row in FakeFMPProvider()._get("income_quarterly")
    ]
    companyfacts_basic_only = copy.deepcopy(FakeEdgarProvider()._companyfacts)
    del companyfacts_basic_only["facts"]["us-gaap"]["WeightedAverageNumberOfDilutedSharesOutstanding"]

    fmp = FakeFMPProvider(
        ohlcv=generate_ohlcv_sessions(end=(fixed_now - timedelta(days=1)).date()),
        overrides={
            "income_annual": income_annual_no_shares,
            "income_quarterly": income_quarterly_no_shares,
        },
    )
    edgar = FakeEdgarProvider(companyfacts=companyfacts_basic_only)
    providers = Providers(fmp=fmp, edgar=edgar, finnhub=FakeFinnhubProvider(), fred=FakeFredProvider())

    packet = build_packet("NVDA", providers, fixed_now)

    assert packet is not None


# --- top-level packet shape -----------------------------------------------


def test_packet_is_a_valid_pydantic_packet(fake_providers, fixed_now):
    packet = build_packet("NVDA", fake_providers, fixed_now)

    assert isinstance(packet, Packet)
    assert packet.security.ticker == "NVDA"
    assert packet.security.reporting_currency == "USD"
    assert packet.security.valuation_currency == "USD"
    assert packet.analysis.knowledge_timestamp == fixed_now.isoformat()
    assert packet.analysis.market_timestamp is not None


def test_knowledge_timestamp_uses_the_now_parameter_not_wall_clock(fake_providers):
    frozen = datetime(2020, 1, 1, tzinfo=timezone.utc)
    ohlcv = generate_ohlcv_sessions(end=(frozen - timedelta(days=1)).date())
    providers = Providers(
        fmp=FakeFMPProvider(ohlcv=ohlcv), edgar=FakeEdgarProvider(), finnhub=FakeFinnhubProvider(), fred=FakeFredProvider()
    )

    packet = build_packet("NVDA", providers, frozen)

    assert packet.analysis.knowledge_timestamp == frozen.isoformat()


def test_sector_etf_mapping():
    from wbj.packet.builder import _sector_etf_for
    assert _sector_etf_for({"sector": "Technology"}) == "XLK"
    assert _sector_etf_for({"sector": "Energy"}) == "XLE"
    assert _sector_etf_for({"sector": "Financial Services"}) == "XLF"
    # Unknown/blank sector falls back to the broad benchmark.
    assert _sector_etf_for({"sector": "Nonexistent"}) == "SPY"
    assert _sector_etf_for({}) == "SPY"


# --- industry adapter selection ----------------------------------------------


def test_adapter_is_derived_from_the_company_not_hardcoded():
    """INDUSTRY_ADAPTERS.md opens with "Select an adapter before
    scoring". Every specialist already read analysis.industry_adapter —
    valuation refuses to run a FCFF DCF on a non-default one — but the
    builder hardcoded default_nonfinancial, so a bank, a REIT, a biotech
    and a SaaS company were all scored as generic operating companies."""
    from wbj.packet.builder import _industry_adapter_for

    cases = [
        ({"sector": "Financial Services", "industry": "Banks - Diversified"}, "banks"),
        ({"sector": "Financial Services",
          "industry": "Insurance - Property & Casualty"}, "insurers"),
        ({"sector": "Real Estate", "industry": "REIT - Retail"}, "reits"),
        ({"sector": "Technology", "industry": "Software - Application"},
         "saas_subscriptions"),
        ({"sector": "Healthcare", "industry": "Biotechnology"}, "biotech"),
        ({"sector": "Energy", "industry": "Oil & Gas Integrated"},
         "commodities_cyclicals"),
        ({"sector": "Technology", "industry": "Semiconductors"}, "default_nonfinancial"),
        ({"sector": "Consumer Defensive", "industry": "Beverages - Non-Alcoholic"},
         "default_nonfinancial"),
    ]
    for profile, expected in cases:
        assert _industry_adapter_for(profile) == expected, profile["industry"]


def test_industry_wins_over_sector():
    """A REIT sits in the Real Estate sector but so do brokers; the
    specific classification decides."""
    from wbj.packet.builder import _industry_adapter_for

    assert _industry_adapter_for(
        {"sector": "Real Estate", "industry": "Real Estate Services"}) == "reits"
    assert _industry_adapter_for(
        {"sector": "Technology", "industry": "Software - Infrastructure"}
    ) == "saas_subscriptions"


def test_unknown_classification_falls_back_to_the_documented_default():
    from wbj.packet.builder import _industry_adapter_for

    assert _industry_adapter_for({}) == "default_nonfinancial"
    assert _industry_adapter_for(
        {"sector": "Industrials", "industry": "Airlines"}) == "default_nonfinancial"


# ============================================================================
# SOURCE_HIERARCHY.md step 2: prefer a later official restatement
# ============================================================================


def _facts(entries: list[dict]) -> dict:
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}


def test_a_later_restatement_supersedes_the_original_filing():
    """SOURCE_HIERARCHY.md conflict resolution step 2: "Prefer a later
    official restatement over the original filing" (QA_CHECKLIST.md:
    "Historical statements use latest valid restatement for current
    analysis").

    companyfacts lists one period once per filing that reports it, in
    ascending `filed` order: the 10-K that first stated FY2024, then the
    FY2025 10-K repeating it in its comparative column with a restated
    figure. Taking the first entry handed back the superseded original.
    """
    entries = [
        {"end": "2024-12-31", "val": 1000.0, "fp": "FY", "form": "10-K",
         "filed": "2025-02-20", "accn": "0000-24-000001"},   # original
        {"end": "2024-12-31", "val": 1100.0, "fp": "FY", "form": "10-K",
         "filed": "2026-02-19", "accn": "0000-26-000001"},   # restated
    ]
    v = builder._edgar_value_at(_facts(entries), "us-gaap", "Revenues", "USD", "2024-12-31")
    assert v.value == 1100.0, "must take the later restatement, not the original"
    assert v.source_locator == "0000-26-000001"
    assert v.as_of == "2026-02-19"


def test_the_restatement_preference_survives_payload_order():
    """Deterministic regardless of how the payload happens to be ordered."""
    original = {"end": "2024-12-31", "val": 1000.0, "fp": "FY", "form": "10-K",
                "filed": "2025-02-20", "accn": "0000-24-000001"}
    restated = {"end": "2024-12-31", "val": 1100.0, "fp": "FY", "form": "10-K",
                "filed": "2026-02-19", "accn": "0000-26-000001"}
    for order in ([original, restated], [restated, original]):
        v = builder._edgar_value_at(_facts(order), "us-gaap", "Revenues", "USD", "2024-12-31")
        assert v.value == 1100.0


def test_the_annual_preference_still_wins_over_a_later_quarterly():
    """A later-filed *quarterly* fact must not displace the annual one for an
    annual reconciliation -- the FY filter runs before the restatement sort."""
    entries = [
        {"end": "2024-12-31", "val": 1000.0, "fp": "FY", "form": "10-K",
         "filed": "2025-02-20", "accn": "0000-24-000001"},
        {"end": "2024-12-31", "val": 250.0, "fp": "Q4", "form": "10-Q",
         "filed": "2025-05-01", "accn": "0000-25-000009"},
    ]
    v = builder._edgar_value_at(_facts(entries), "us-gaap", "Revenues", "USD", "2024-12-31")
    assert v.value == 1000.0


# ============================================================================
# TECH-GAP-020: earnings events resolved to the session carrying the gap
# ============================================================================


def _bars(spec: list[tuple[str, float, float]]) -> list[dict]:
    """(date, open, close) ascending -> newest-first raw OHLCV rows."""
    return [{"date": d, "open": o, "close": c} for d, o, c in reversed(spec)]


def test_an_after_hours_reporter_resolves_to_the_next_session():
    """TECH-GAP-020: "Use first regular session after after-hours release."
    A company reporting after the close is flat on the announcement date and
    gaps the next morning -- the pattern AAPL shows on live data (+0.10% on
    the date, +2.77% the session after)."""
    bars = _bars([
        ("2026-01-27", 100.0, 100.0),
        ("2026-01-28", 100.0, 100.0),   # announcement day: no reaction yet
        ("2026-01-29", 108.0, 108.0),   # the gap
    ])
    cal = [{"date": "2026-01-28", "epsActual": 2.85}]
    assert builder._resolve_gap_sessions(cal, bars) == ["2026-01-29"]


def test_a_before_open_reporter_resolves_to_the_announcement_date():
    """A company reporting before the open gaps that same morning -- JPM and
    XOM on live data (-2.25% on the date, +0.86% the session after)."""
    bars = _bars([
        ("2026-07-13", 100.0, 100.0),
        ("2026-07-14", 92.0, 92.0),     # announcement day: the gap
        ("2026-07-15", 92.5, 92.5),
    ])
    cal = [{"date": "2026-07-14", "epsActual": 7.59}]
    assert builder._resolve_gap_sessions(cal, bars) == ["2026-07-14"]


def test_a_scheduled_future_event_contributes_no_session():
    """A date with no reported actual has no gap to measure."""
    bars = _bars([("2026-07-13", 100.0, 100.0), ("2026-07-14", 100.0, 100.0)])
    cal = [{"date": "2026-07-14", "epsActual": None, "epsEstimated": 1.88}]
    assert builder._resolve_gap_sessions(cal, bars) == []


def test_an_announcement_outside_the_price_window_is_skipped():
    bars = _bars([("2026-07-13", 100.0, 100.0), ("2026-07-14", 100.0, 100.0)])
    cal = [{"date": "2019-01-01", "epsActual": 1.0}]
    assert builder._resolve_gap_sessions(cal, bars) == []


def test_equal_moves_keep_the_announcement_date():
    """No evidence of a delayed reaction is no reason to shift the event."""
    bars = _bars([
        ("2026-03-02", 100.0, 100.0),
        ("2026-03-03", 100.0, 100.0),
        ("2026-03-04", 100.0, 100.0),
    ])
    cal = [{"date": "2026-03-03", "epsActual": 1.0}]
    assert builder._resolve_gap_sessions(cal, bars) == ["2026-03-03"]


def test_resolved_sessions_are_sorted_and_deduplicated():
    bars = _bars([(f"2026-01-{d:02d}", 100.0, 100.0) for d in range(1, 10)])
    cal = [{"date": "2026-01-05", "epsActual": 1.0},
           {"date": "2026-01-03", "epsActual": 1.0},
           {"date": "2026-01-05", "epsActual": 1.0}]
    out = builder._resolve_gap_sessions(cal, bars)
    assert out == sorted(out)
    assert len(out) == len(set(out))


# ============================================================================
# DATASET.md `peer_growth_margin_returns`: the peer panel
# ============================================================================


class _PeerFMP:
    """Minimal FMP stand-in for `_peer_panel`."""

    def __init__(self, income=None, profile=None, raises=()):
        self._income = income or {}
        self._profile = profile or {}
        self._raises = set(raises)

    def income_annual(self, symbol, limit=2):
        if symbol in self._raises:
            raise RuntimeError("unreachable")
        return self._income.get(symbol)

    def profile(self, symbol):
        return self._profile.get(symbol)


def test_peer_panel_carries_growth_margins_and_a_multiple():
    fmp = _PeerFMP(
        income={"MSFT": [
            {"date": "2025-06-30", "revenue": 1100.0, "grossProfit": 770.0,
             "operatingIncome": 440.0, "netIncome": 330.0},
            {"date": "2024-06-30", "revenue": 1000.0},
        ]},
        profile={"MSFT": [{"marketCap": 5500.0}]},
    )
    panel = builder._peer_panel([{"symbol": "MSFT"}], fmp)
    assert len(panel) == 1
    row = panel[0]
    assert row["revenue_growth"] == pytest.approx(0.10)
    assert row["gross_margin"] == pytest.approx(0.70)
    assert row["operating_margin"] == pytest.approx(0.40)
    assert row["net_margin"] == pytest.approx(0.30)
    assert row["price_to_sales"] == pytest.approx(5.0)   # 5500 / 1100


def test_peer_panel_reads_the_retired_peerslist_shape_too():
    fmp = _PeerFMP(income={"AMD": [{"date": "2025-12-31", "revenue": 100.0}]})
    panel = builder._peer_panel([{"peersList": ["AMD"]}], fmp)
    assert [r["symbol"] for r in panel] == ["AMD"]


def test_an_unreachable_peer_is_skipped_not_guessed():
    """One bad peer must not fail the packet, and must not become a zero."""
    fmp = _PeerFMP(
        income={"GOOD": [{"date": "2025-12-31", "revenue": 100.0}]},
        raises={"BAD"},
    )
    panel = builder._peer_panel([{"symbol": "BAD"}, {"symbol": "GOOD"}], fmp)
    assert [r["symbol"] for r in panel] == ["GOOD"]


def test_a_peer_without_revenue_is_skipped():
    fmp = _PeerFMP(income={"X": [{"date": "2025-12-31", "revenue": 0}]})
    assert builder._peer_panel([{"symbol": "X"}], fmp) == []


def test_peer_panel_is_capped_to_bound_request_count():
    symbols = [{"symbol": f"P{i}"} for i in range(30)]
    fmp = _PeerFMP(income={f"P{i}": [{"date": "2025-12-31", "revenue": 100.0}]
                           for i in range(30)})
    assert len(builder._peer_panel(symbols, fmp)) == builder._MAX_PEER_PANEL
