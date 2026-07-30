"""Degenerate inputs must degrade, not crash.

`Entradas/<TICKER>.json` is written by hand, so any block can arrive the
wrong shape: a string where a number belongs, a list of nulls, a bare
value where a mapping is expected. Nine of ten malformed shapes tested
raised out of `run()` and took the whole specialist with them — the
analyst got no output at all rather than one metric degrading to MISSING,
which is what AGENT.md's contract requires.

`guidance_history` had already been hardened once for exactly this
reason. These cover the rest.
"""

from datetime import datetime, timezone

import pytest

from wbj.schemas.packet import AnalysisMeta, Packet, Security
from wbj.specialists import business as bus


def _year(**over):
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


def _packet(annual=None, adapter="default_nonfinancial"):
    return Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Software - Application"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter=adapter),
        fundamentals={"annual": annual if annual is not None else [_year() for _ in range(5)]},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})


# --- packet-side degeneracy ------------------------------------------------


@pytest.mark.parametrize("label,annual", [
    ("no annual rows", []),
    ("a single year", [_year()]),
    ("zero revenue", [_year(revenue=0.0) for _ in range(5)]),
    ("negative equity", [_year(total_equity=-100.0) for _ in range(5)]),
    ("negative EBIT", [_year(ebit=-50.0) for _ in range(5)]),
    ("every field zero", [_year(**{k: 0.0 for k in (
        "revenue", "gross_profit", "ebit", "net_income", "operating_cash_flow",
        "capex", "fcf", "total_debt", "total_equity", "cash", "total_assets",
        "total_liabilities", "income_before_tax", "income_tax_expense",
        "diluted_shares", "stock_based_compensation", "changeInWorkingCapital")})
        for _ in range(5)]),
    ("fields absent", [{"fiscalYear": "2025"} for _ in range(5)]),
    ("fields null", [_year(revenue=None, ebit=None, total_debt=None) for _ in range(5)]),
])
def test_a_degenerate_packet_produces_an_output(label, annual):
    out = bus.run(_packet(annual), {})
    assert out.status in ("COMPLETE", "INCOMPLETE", "ERROR"), label
    assert 0.0 <= out.coverage <= 1.0
    assert out.category.awarded_points >= 0.0


def test_no_annual_data_is_an_error_not_a_score():
    """Nothing to analyse must not read as an analysis that found
    nothing good."""
    out = bus.run(_packet([]), {})
    assert out.status == "ERROR"
    assert out.coverage == 0.0


# --- analyst-input degeneracy ---------------------------------------------


MALFORMED = {
    "retention": "texto",
    "churn": "texto",
    "customer_economics": "texto",
    "peer_roic": "no-es-lista",
    "customer_shares": [None, "x"],
    "segment_shares": [None, "x"],
    "guidance_history": [1, 2],
    "wacc": "alto",
    "largest_customer_share": "mucho",
    "recurring_revenue": "casi todo",
    "capitalized_rd_adjustment": "algo",
}


@pytest.mark.parametrize("key", sorted(MALFORMED))
def test_one_malformed_block_does_not_take_down_the_run(key):
    out = bus.run(_packet(), {key: MALFORMED[key]})
    assert out.category.max_points == 20.0


def test_every_block_malformed_at_once_still_runs():
    out = bus.run(_packet(), dict(MALFORMED))
    assert out.status in ("COMPLETE", "INCOMPLETE", "ERROR")


def test_a_malformed_block_says_so_in_the_report():
    """Silence would leave the analyst believing the file was read. The
    warnings are collected after every read — an earlier placement
    extended an empty list and none of them reached the output."""
    out = bus.run(_packet(), {"wacc": "alto", "retention": "texto",
                              "peer_roic": 5, "customer_shares": [None, "x"]})
    notices = [a for a in out.assumptions if "analyst input" in a]
    assert len(notices) >= 4
    assert any("'wacc'" in n and "number" in n for n in notices)
    assert any("'retention'" in n and "object" in n for n in notices)
    assert any("'peer_roic'" in n and "list" in n for n in notices)
    assert any("'customer_shares'" in n and "dropped" in n for n in notices)


def test_a_well_formed_block_is_not_warned_about():
    """The warning must mean something: a correct file produces none."""
    out = bus.run(_packet(), {
        "wacc": 0.09,
        "peer_roic": [0.1, 0.2, 0.3],
        "customer_shares": [0.22, 0.14],
        "retention": {"begin": 1000.0, "expansion": 120.0,
                      "contraction": 30.0, "churn": 50.0},
    })
    assert not [a for a in out.assumptions if "analyst input" in a and "ignored" in a]


# --- determinism -----------------------------------------------------------


def test_the_same_packet_produces_the_same_output_twice():
    """A specialist that drifts between runs cannot be audited."""
    import json

    packet, overlay = _packet(), {"wacc": 0.09}
    first = json.dumps(bus.run(packet, overlay).model_dump(mode="json"),
                       sort_keys=True, default=str)
    second = json.dumps(bus.run(packet, overlay).model_dump(mode="json"),
                        sort_keys=True, default=str)
    assert first == second
