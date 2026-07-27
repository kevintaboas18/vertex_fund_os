"""BUS-IROIC-016 / BUS-ALLOC-029: the 3-year window FORMULAS.md registers.

The registry gives these a frequency of "3y rolling". The change was
measured from the oldest available row to the latest instead, so raising
the packet's history depth from six years to eleven silently turned a
3-year change into a 10-year one — a company that read 311% over the long
window reads 28% over the three years the registry asks for.

`INCREMENTAL_ROIC_WINDOW_YEARS` fixes the window so history depth cannot
move it, the same latency `WIDE_MOAT_PERSISTENCE_WINDOW` guards against.
"""

from datetime import datetime, timezone

import pytest

from wbj.schemas.packet import AnalysisMeta, Packet, Security
from wbj.specialists import business as bus


def _year(y, *, ebit, equity):
    """One statement row. Moving `equity` moves invested capital and so
    ROIC; moving `ebit` moves NOPAT."""
    return dict(fiscalYear=str(y), filingDate=f"{y + 1}-02-20", revenue=1000.0,
                gross_profit=600.0, ebit=ebit, net_income=150.0,
                operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
                total_debt=150.0, total_equity=equity, cash=200.0,
                total_assets=1000.0, total_liabilities=400.0,
                income_before_tax=190.0, income_tax_expense=40.0,
                diluted_shares=100.0, stock_based_compensation=10.0,
                changeInWorkingCapital=-15.0)


def _packet(rows_newest_first):
    return Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Semiconductors"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": rows_newest_first}, facts_table={},
        market_data={}, estimates={}, capital_structure={}, staleness={})


def _iroic(out):
    return next(m for m in out.metrics if m.metric_id == "BUS-IROIC-016").value


def test_the_window_is_the_three_years_the_registry_states():
    assert bus.INCREMENTAL_ROIC_WINDOW_YEARS == 3


def test_history_before_the_window_does_not_move_the_change():
    """Eleven years, but only the last three should count. Years older
    than the window are set to wild values; the metric must ignore them.
    Newest first, as the packet carries them."""
    # Last four rows (the 3-year change) share one steady trajectory;
    # everything older is noise the metric must not see.
    recent = [_year(2025, ebit=300.0, equity=900.0),   # latest
              _year(2024, ebit=280.0, equity=850.0),
              _year(2023, ebit=260.0, equity=800.0),
              _year(2022, ebit=240.0, equity=750.0)]    # window start
    ancient = [_year(y, ebit=9999.0, equity=10.0) for y in (2021, 2020, 2019,
                                                            2018, 2017, 2016, 2015)]
    with_noise = _iroic(bus.run(_packet(recent + ancient), {"wacc": 0.09}))
    without = _iroic(bus.run(_packet(recent), {"wacc": 0.09}))
    assert with_noise == pytest.approx(without)


def test_a_deeper_packet_does_not_change_the_result():
    """The regression itself: the same recent trajectory must give the
    same incremental ROIC whether the packet holds four years or eleven."""
    recent = [_year(2025, ebit=300.0, equity=900.0),
              _year(2024, ebit=280.0, equity=850.0),
              _year(2023, ebit=260.0, equity=800.0),
              _year(2022, ebit=240.0, equity=750.0)]
    padded = recent + [_year(y, ebit=200.0, equity=600.0)
                       for y in range(2021, 2014, -1)]
    assert _iroic(bus.run(_packet(recent), {"wacc": 0.09})) == pytest.approx(
        _iroic(bus.run(_packet(padded), {"wacc": 0.09})))


def test_a_short_history_uses_what_it_has():
    """Fewer than four rows cannot form a 3-year change, so the start
    clamps to the oldest row rather than the metric going MISSING — the
    best available change, which is the pre-existing behaviour for short
    histories and unchanged by this fix."""
    two = [_year(2025, ebit=300.0, equity=900.0),
           _year(2024, ebit=240.0, equity=750.0)]
    assert _iroic(bus.run(_packet(two), {"wacc": 0.09})) is not None


def test_the_dependent_allocation_metric_still_scores():
    """BUS-ALLOC-029 is incremental ROIC minus WACC, and carries a
    quarter of the management dimension. The window fix must not strand
    it."""
    # Both NOPAT and invested capital have to move, or the change has a
    # zero denominator and BUS-IROIC-016 is NOT_MEANINGFUL by design.
    rows = [_year(y, ebit=200.0 + (y - 2015) * 10, equity=600.0 + (y - 2015) * 30)
            for y in range(2025, 2014, -1)]
    out = bus.run(_packet(rows), {"wacc": 0.09})
    alloc = next(m for m in out.metrics if m.metric_id == "BUS-ALLOC-029")
    assert alloc.value is not None
