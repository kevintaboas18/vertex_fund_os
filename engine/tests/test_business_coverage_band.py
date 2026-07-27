"""MISSING_DATA_POLICY.md's three coverage bands, made visible.

The policy names three bands:

  - coverage >= 0.85: complete
  - 0.70 <= coverage < 0.85: usable with caveat
  - coverage < 0.70: incomplete and gate-ineligible

The handoff `status` literal has only COMPLETE / INCOMPLETE / ERROR, so a
category at 0.80 read the same as one at 0.50 — both INCOMPLETE. The
middle band's own name is "usable *with caveat*", and the caveat is what
was missing. `coverage_band_caveat` emits it, naming the band a reader of
`status` alone could not see, without touching the shared status literal
HANDOFF_CONTRACT.md fixes.
"""

from datetime import datetime, timezone

import pytest

from wbj.core.scoring import COVERAGE_COMPLETE, COVERAGE_USABLE
from wbj.schemas.packet import AnalysisMeta, Packet, Security
from wbj.specialists import business as bus


# --- the band function -----------------------------------------------------


def test_a_complete_category_needs_no_caveat():
    assert bus.coverage_band_caveat(0.95) is None
    assert bus.coverage_band_caveat(COVERAGE_COMPLETE) is None   # 0.85 is complete


def test_the_middle_band_reads_usable_with_caveat():
    caveat = bus.coverage_band_caveat(0.80)
    assert caveat is not None
    assert "usable with caveat" in caveat


def test_the_low_band_reads_incomplete_and_gate_ineligible():
    caveat = bus.coverage_band_caveat(0.50)
    assert caveat is not None
    assert "incomplete and gate-ineligible" in caveat


def test_the_two_empty_looking_bands_are_now_distinguishable():
    """The literal defect: 0.80 and 0.50 both showed INCOMPLETE. Their
    caveats differ."""
    assert bus.coverage_band_caveat(0.80) != bus.coverage_band_caveat(0.50)


@pytest.mark.parametrize("coverage,band", [
    (0.9999, None),
    (COVERAGE_COMPLETE, None),           # 0.85, complete
    (0.8499, "usable with caveat"),
    (COVERAGE_USABLE, "usable with caveat"),   # 0.70, the floor of usable
    (0.6999, "incomplete and gate-ineligible"),
    (0.01, "incomplete and gate-ineligible"),
])
def test_each_boundary_lands_in_its_band(coverage, band):
    caveat = bus.coverage_band_caveat(coverage)
    if band is None:
        assert caveat is None
    else:
        assert caveat is not None and band in caveat


def test_zero_coverage_gets_no_band_caveat():
    """Coverage of zero is ERROR, which `status` already carries; there is
    no evidence to caveat."""
    assert bus.coverage_band_caveat(0.0) is None
    assert bus.coverage_band_caveat(-1.0) is None


def test_the_boundaries_are_victors_numbers():
    assert COVERAGE_COMPLETE == pytest.approx(0.85)
    assert COVERAGE_USABLE == pytest.approx(0.70)


# --- it reaches the output -------------------------------------------------


def _packet(customer_shares=None):
    """A packet whose coverage lands in the usable-with-caveat band for a
    non-subscription company: filings score, but the analyst-only metrics
    are absent."""
    row = dict(fiscalYear="2025", filingDate="2026-02-20", revenue=1000.0,
               gross_profit=600.0, ebit=200.0, net_income=150.0,
               operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
               total_debt=150.0, total_equity=600.0, cash=200.0,
               total_assets=1000.0, total_liabilities=400.0,
               income_before_tax=190.0, income_tax_expense=40.0,
               diluted_shares=100.0, stock_based_compensation=10.0,
               changeInWorkingCapital=-15.0)
    return Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Semiconductors"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": [dict(row) for _ in range(6)]},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})


def test_the_band_caveat_appears_in_the_output_assumptions():
    out = bus.run(_packet(), {"wacc": 0.09})
    band_caveats = [a for a in out.assumptions
                    if "MISSING_DATA_POLICY.md" in a
                    and ("usable with caveat" in a or "incomplete" in a)]
    # The caveat matches the coverage the run actually produced.
    expected = bus.coverage_band_caveat(out.coverage)
    if expected is None:
        assert band_caveats == []
    else:
        assert expected in out.assumptions


def test_a_complete_run_emits_no_band_caveat():
    """When coverage clears 0.85, nothing is added."""
    out = bus.run(_packet(), {"wacc": 0.09})
    if out.coverage >= COVERAGE_COMPLETE:
        assert not any("usable with caveat" in a or "incomplete and gate" in a
                       for a in out.assumptions)


# --- status is untouched ---------------------------------------------------


def test_the_status_literal_is_not_changed():
    """HANDOFF_CONTRACT.md fixes three status values, and this repair does
    not add a fourth: the caveat carries the band, the status stays."""
    from wbj.specialists.common import status_from_coverage

    assert status_from_coverage(0.80) == "INCOMPLETE"   # unchanged
    assert status_from_coverage(0.50) == "INCOMPLETE"   # unchanged
    assert status_from_coverage(0.95) == "COMPLETE"
    assert status_from_coverage(0.0) == "ERROR"
