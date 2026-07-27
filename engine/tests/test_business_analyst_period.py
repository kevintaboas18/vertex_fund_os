"""The period an analyst-supplied quarterly metric declares.

FORMULAS.md registers BUS-CHURN-022, BUS-LTV-023, BUS-CAC-024,
BUS-LTVCAC-025, BUS-PAYBACK-026, BUS-GUIDE-027, and the retention pair as
quarterly or monthly, and DATASET.md asks for "8 quarters" of that
history. They are analyst-supplied, so their period belongs to the
analyst — but they inherited the annual filing's FY, the one period they
could not be.

Guidance derives its period from the quarters its own entries already
carry; the retention and cohort snapshots take a period the analyst
states in `Entradas/<TICKER>.json`, and fall back to the filing FY when
none is given, unchanged from before.
"""

from datetime import datetime, timezone

import pytest

from wbj.schemas.packet import AnalysisMeta, Packet, Security
from wbj.specialists import business as bus


def _row(**over):
    row = dict(fiscalYear="2025", filingDate="2026-02-20", revenue=1000.0,
               gross_profit=600.0, ebit=200.0, net_income=150.0,
               operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
               total_debt=150.0, total_equity=600.0, cash=200.0,
               total_assets=1000.0, total_liabilities=400.0,
               income_before_tax=190.0, income_tax_expense=40.0,
               diluted_shares=100.0, stock_based_compensation=10.0,
               changeInWorkingCapital=-15.0)
    row.update(over)
    return row


def _packet():
    return Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Software - Application"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": [_row() for _ in range(6)]},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})


FULL = {
    "wacc": 0.09,
    "retention": {"begin": 1000.0, "expansion": 120.0, "contraction": 30.0, "churn": 50.0},
    "churn": {"lost": 2.1, "begin_customers": 100.0},
    "customer_economics": {"arpu": 180.0, "monthly_arpu": 15.0, "gross_margin": 0.45,
                           "customer_life_years": 4.5, "cac_spend": 2.4e9, "new_customers": 30e6},
}

QUARTERLY = ("BUS-NRR-020", "BUS-GRR-021", "BUS-CHURN-022", "BUS-LTV-023",
             "BUS-CAC-024", "BUS-LTVCAC-025", "BUS-PAYBACK-026")


def _period(out, metric_id):
    return next(m for m in out.metrics if m.metric_id == metric_id).period


# --- the stated period -----------------------------------------------------


def test_a_declared_period_reaches_the_snapshot_metrics():
    out = bus.run(_packet(), {**FULL, "period": "FY2026Q2"})
    for metric_id in QUARTERLY:
        assert _period(out, metric_id) == "FY2026Q2", metric_id


def test_without_a_declared_period_the_filing_fy_stands():
    """No worse than before: the fallback is unchanged."""
    out = bus.run(_packet(), FULL)
    for metric_id in QUARTERLY:
        assert _period(out, metric_id) == "FY2025", metric_id


@pytest.mark.parametrize("bad", [None, "", "   ", 42, ["FY2026Q2"]])
def test_a_non_string_period_falls_back(bad):
    """A malformed period must not become the label."""
    assert bus._stated_analyst_period({"period": bad}) is None
    out = bus.run(_packet(), {**FULL, "period": bad})
    assert _period(out, "BUS-NRR-020") == "FY2025"


def test_the_period_is_stripped():
    assert bus._stated_analyst_period({"period": "  FY2026Q2  "}) == "FY2026Q2"


# --- guidance derives its own -----------------------------------------------


def test_guidance_period_spans_its_entries():
    """The metric averages accuracy across the quarters supplied, so its
    period is their span — read from the `period` each entry already
    carries, no new field."""
    out = bus.run(_packet(), {
        "wacc": 0.09,
        "guidance_history": [
            {"period": "FY2026Q1", "guidance_midpoint": 43.0, "actual": 44.0},
            {"period": "FY2026Q2", "guidance_midpoint": 45.0, "actual": 46.0}]})
    assert _period(out, "BUS-GUIDE-027") == "FY2026Q1-FY2026Q2"


def test_a_single_guidance_quarter_is_not_a_span():
    out = bus.run(_packet(), {
        "wacc": 0.09,
        "guidance_history": [
            {"period": "FY2026Q2", "guidance_midpoint": 45.0, "actual": 46.0}]})
    assert _period(out, "BUS-GUIDE-027") == "FY2026Q2"


def test_the_guidance_helper_reads_only_dated_entries():
    assert bus._guidance_period([]) is None
    assert bus._guidance_period([{"guidance_midpoint": 1.0}]) is None
    assert bus._guidance_period([{"period": "FY2026Q1"}, {"period": "FY2026Q3"}]) == \
        "FY2026Q1-FY2026Q3"
    # Out-of-order entries still span correctly.
    assert bus._guidance_period([{"period": "FY2026Q3"}, {"period": "FY2026Q1"}]) == \
        "FY2026Q1-FY2026Q3"


def test_guidance_ignores_the_top_level_period():
    """Its span is more precise than a single stated period, so the
    declared one does not override it."""
    out = bus.run(_packet(), {
        "wacc": 0.09, "period": "FY2099Q9",
        "guidance_history": [
            {"period": "FY2026Q1", "guidance_midpoint": 43.0, "actual": 44.0},
            {"period": "FY2026Q2", "guidance_midpoint": 45.0, "actual": 46.0}]})
    assert _period(out, "BUS-GUIDE-027") == "FY2026Q1-FY2026Q2"


# --- everything else is untouched ------------------------------------------


def test_filings_metrics_keep_the_filing_period():
    """A stated analyst period must not touch a metric derived from the
    statements."""
    out = bus.run(_packet(), {**FULL, "period": "FY2026Q2"})
    assert _period(out, "BUS-OM-008") == "FY2025"
    assert _period(out, "BUS-ROIC-013") == "FY2025"


def test_annual_analyst_metrics_keep_the_fiscal_year():
    """Concentration is annual per FORMULAS.md, so the FY is right for it
    and the quarterly period does not apply."""
    out = bus.run(_packet(), {**FULL, "period": "FY2026Q2",
                              "largest_customer_share": 0.22})
    assert _period(out, "BUS-CONC-003") == "FY2025"


def test_a_window_metric_keeps_its_span():
    out = bus.run(_packet(), {**FULL, "period": "FY2026Q2"})
    assert "-" in _period(out, "BUS-RANGE-010")   # e.g. FY2021-FY2025
