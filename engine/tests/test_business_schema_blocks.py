"""OUTPUT_SCHEMA.md's extension blocks, checked for content.

The schema declares nine fields beyond the shared envelope. Six carried
their dimension's figures. `customer_economics` was declared `{}` and
stayed `{}` — on every security, for every business model, however much
retention data an analyst supplied. Nothing filled it: it was neither
computed into nor written by the judgment layer, unlike
`business_in_one_sentence` and `three_thesis_killers`.

The same drop had taken SBC/FCF, which BUS-SBC-030's caveat asks for
alongside the diluted-share trend: computed, stored, and reported
nowhere.
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


def _packet(sector="Technology", industry="Software - Application"):
    return Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector=sector, industry=industry),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": [_row() for _ in range(6)]},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})


#: A complete analyst disclosure, so every one of the seven formulas has
#: its inputs.
FULL = {
    "wacc": 0.09,
    "retention": {"begin": 1000.0, "expansion": 120.0,
                  "contraction": 30.0, "churn": 50.0},
    "churn": {"lost": 2.1, "begin_customers": 100.0},
    "customer_economics": {"arpu": 180.0, "monthly_arpu": 15.0,
                           "gross_margin": 0.45, "customer_life_years": 4.5,
                           "cac_spend": 2.4e9, "new_customers": 30e6},
}

#: The seven registered formulas of SCORING.md's customer-economics row.
SEVEN = ("net_revenue_retention", "gross_revenue_retention", "logo_churn",
         "customer_ltv", "customer_acquisition_cost", "ltv_to_cac",
         "cac_payback_months")


def test_the_block_carries_every_formula_of_its_row():
    out = bus.run(_packet(), FULL)
    for key in SEVEN:
        assert key in out.customer_economics, key


def test_each_reported_figure_reproduces_its_formula():
    """The values are the registered formulas' own output, not a
    re-derivation — so each must reproduce by hand from the same inputs."""
    ce = bus.run(_packet(), FULL).customer_economics

    # (1000 + 120 - 30 - 50) / 1000
    assert ce["net_revenue_retention"] == pytest.approx(1.04)
    # (1000 - 30 - 50) / 1000
    assert ce["gross_revenue_retention"] == pytest.approx(0.92)
    # 2.1 / 100
    assert ce["logo_churn"] == pytest.approx(0.021)
    # 180 * 0.45 * 4.5
    assert ce["customer_ltv"] == pytest.approx(364.5)
    # 2.4e9 / 30e6
    assert ce["customer_acquisition_cost"] == pytest.approx(80.0)
    # 364.5 / 80
    assert ce["ltv_to_cac"] == pytest.approx(4.55625)
    # 80 / (15 * 0.45)
    assert ce["cac_payback_months"] == pytest.approx(11.85185, rel=1e-4)


def test_the_reported_figures_match_the_metric_rows():
    """One number, two places. A block that drifted from `metrics` would
    put two different answers in the same output."""
    out = bus.run(_packet(), FULL)
    by_id = {m.metric_id: m for m in out.metrics}
    pairs = {
        "net_revenue_retention": "BUS-NRR-020",
        "gross_revenue_retention": "BUS-GRR-021",
        "logo_churn": "BUS-CHURN-022",
        "customer_ltv": "BUS-LTV-023",
        "customer_acquisition_cost": "BUS-CAC-024",
        "ltv_to_cac": "BUS-LTVCAC-025",
        "cac_payback_months": "BUS-PAYBACK-026",
    }
    for key, metric_id in pairs.items():
        assert out.customer_economics[key] == pytest.approx(by_id[metric_id].value)


# --- applicability ---------------------------------------------------------


def test_a_subscription_model_that_disclosed_nothing_is_applicable():
    """SCORING.md's gate for this row is "If not applicable, use adapter
    metrics; do not impute", which makes applicability a fact about the
    dimension rather than an inference. Without it a transactional
    model's seven nulls read identically to a subscription model that
    disclosed nothing."""
    ce = bus.run(_packet(), {"wacc": 0.09}).customer_economics
    assert ce["applicable"] is True
    assert all(ce[k] is None for k in SEVEN)


def test_a_transactional_model_is_not_applicable():
    ce = bus.run(_packet("Technology", "Semiconductors"),
                 {"wacc": 0.09}).customer_economics
    assert ce["applicable"] is False
    assert all(ce[k] is None for k in SEVEN)


def test_the_two_empty_cases_are_distinguishable():
    """The whole point of reporting applicability."""
    disclosed_nothing = bus.run(_packet(), {"wacc": 0.09}).customer_economics
    does_not_apply = bus.run(_packet("Technology", "Semiconductors"),
                             {"wacc": 0.09}).customer_economics
    assert disclosed_nothing != does_not_apply


def test_analyst_data_makes_a_transactional_model_applicable():
    """Supplied data settles it: an analyst who writes a retention bridge
    has established the metric exists for that company, whatever its
    industry label says."""
    ce = bus.run(_packet("Technology", "Semiconductors"), FULL).customer_economics
    assert ce["applicable"] is True
    assert ce["net_revenue_retention"] == pytest.approx(1.04)


# --- BUS-SBC-030's "also report" -------------------------------------------


def test_capital_allocation_reports_sbc_over_fcf():
    """FORMULAS.md, BUS-SBC-030: "Also report SBC/FCF and diluted-share
    trend." The share trend was reported; SBC/FCF was computed, stored in
    the working context, and dropped."""
    ca = bus.run(_packet(), {"wacc": 0.09}).capital_allocation
    assert "diluted_share_cagr" in ca
    assert ca["sbc_burden"] == pytest.approx(10.0 / 1000.0)   # SBC / revenue
    assert ca["sbc_to_fcf"] == pytest.approx(10.0 / 210.0)    # SBC / FCF


def test_sbc_to_fcf_is_not_meaningful_without_positive_fcf():
    """A negative denominator would invert the burden's meaning."""
    assert bus.sbc_to_fcf(10.0, -50.0).is_null
    assert bus.sbc_to_fcf(10.0, 0.0).is_null


# --- the drop itself -------------------------------------------------------


def test_no_schema_block_is_declared_and_left_empty():
    """The defect in general form. A block that ships empty on a packet
    with full data is a field nothing fills."""
    out = bus.run(_packet(), FULL)
    for name in ("moat", "margin_stability", "customer_economics",
                 "capital_allocation", "competitive_position"):
        block = getattr(out, name)
        assert block, f"{name} is declared by OUTPUT_SCHEMA.md and ships empty"


def test_the_history_blocks_are_populated_too():
    out = bus.run(_packet(), FULL)
    assert out.roic_history
    assert out.roic_wacc_spread_history
