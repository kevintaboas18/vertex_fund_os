"""SOURCE_HIERARCHY.md's conflict rule, and the definitional traps in it.

The packet builder reconciles FMP against SEC EDGAR and records the
verdict in `packet.facts_table`. Business never read that table, so it
scored invested capital, ROIC, spread and EVA straight off the provider
row — including when the packet had already marked the input contested.

Wiring it up then exposed the harder half: most "conflicts" are two
sources naming different things, not one of them being wrong.
"""

import pytest

from wbj.specialists import business as bus


def test_the_reconciled_fact_is_preferred_over_the_raw_row():
    """SOURCE_HIERARCHY.md's default order puts the regulatory filing
    first. The builder already picked the higher-tier source; reading the
    provider row instead would discard that choice."""
    from types import SimpleNamespace

    from wbj.core.nullstates import Value

    packet = SimpleNamespace(facts_table={"total_debt": Value.of(8.47e9, unit="USD")})
    value, conflicted = bus._reconciled_fact(packet, "total_debt")
    assert value == pytest.approx(8.47e9)
    assert conflicted is False


def test_a_conflicted_fact_yields_no_value():
    """"mark the metric CONFLICTED and do not score it" — for the metric
    whose sources disagree, which the packet enforces by refusing to
    value it."""
    from types import SimpleNamespace

    from wbj.core.nullstates import NullState, Value

    packet = SimpleNamespace(
        facts_table={"total_debt": Value.null(NullState.CONFLICTED, unit="USD")})
    value, conflicted = bus._reconciled_fact(packet, "total_debt")
    assert value is None
    assert conflicted is True


def test_an_absent_facts_table_is_not_a_conflict():
    """A packet without the table is not a packet whose sources fought."""
    from types import SimpleNamespace

    assert bus._reconciled_fact(SimpleNamespace(), "total_debt") == (None, False)
    assert bus._reconciled_fact(SimpleNamespace(facts_table={}), "cash") == (None, False)


def test_a_contested_input_costs_confidence_rather_than_the_score():
    """A metric *derived* from a contested input is still computable, so
    it keeps its score and carries the contest into confidence.

    Withholding the derived scores was tried and is wrong: every conflict
    examined turned out definitional — JPMorgan's cash gap is "cash and
    due from banks" against "cash and equivalents", Salesforce's revenue
    gap was a quarterly figure against an annual one — and withholding
    removed six of business's core metrics over a naming difference."""
    from types import SimpleNamespace

    packet = SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter="default_nonfinancial"),
        staleness={"quarterly_fundamentals": "FRESH"},
        market_data={},
        estimates={},
        capital_structure={},
    )
    clean = bus._category_confidence(1.0, packet, [])
    contested = bus._category_confidence(1.0, packet, [bus.PROXY_CONFLICTED_SOURCE_INPUT])
    assert contested < clean


def test_the_inputs_that_can_be_contested_map_to_the_metrics_they_feed():
    """A new reconciled input added without its dependants listed would
    be contested silently."""
    assert set(bus._RECONCILED_INPUTS) == {"total_debt", "cash"}
    for metrics in bus._RECONCILED_INPUTS.values():
        assert "BUS-IC-012" in metrics    # both feed invested capital
        assert "BUS-ROIC-013" in metrics  # and everything downstream of it


# --- the reconciler's own comparability -----------------------------------


def test_lease_liabilities_join_the_edgar_debt_figure():
    """The provider counts leases in `totalDebt`; the two EDGAR borrowing
    tags do not. NVIDIA's borrowings tie out to the cent ($8.468B both
    sides) and the whole 34.8% "disagreement" was its $2.944B of leases —
    a definitional gap that `reconcile` reported as a source conflict.

    Both lease kinds are summed: NVIDIA files under the *operating* tags,
    and under ASC 842 an operating lease liability sits on the balance
    sheet like any other obligation, which is what DATA_DICTIONARY.md
    means by "debt-like obligations"."""
    from wbj.packet.builder import _EDGAR_LEASE_LIABILITY_TAGS

    for kind in ("FinanceLease", "OperatingLease", "CapitalLease"):
        assert any(t.startswith(kind) for t in _EDGAR_LEASE_LIABILITY_TAGS), kind
    # Current and noncurrent halves both, since a filer may report either.
    assert sum(t.endswith("Current") for t in _EDGAR_LEASE_LIABILITY_TAGS) == 3
    assert sum(t.endswith("Noncurrent") for t in _EDGAR_LEASE_LIABILITY_TAGS) == 3


def test_a_conflicted_packet_runs_end_to_end():
    """The regression this file was written without: the conflict branch
    referenced a name defined in `run()` from inside `_compute_all`, so
    any packet with a contested input raised NameError. Every unit test
    passed — none of them built a packet that actually conflicts, so the
    branch never executed. Exercise it.
    """
    from datetime import datetime, timezone

    from wbj.core.nullstates import NullState, Value
    from wbj.schemas.packet import AnalysisMeta, Packet, Security

    annual = [
        {"fiscalYear": str(y), "filingDate": f"{y + 1}-02-20",
         "revenue": 1000.0, "gross_profit": 600.0, "ebit": 200.0,
         "net_income": 150.0, "operating_cash_flow": 250.0, "capex": -40.0,
         "fcf": 210.0, "total_debt": 150.0, "total_equity": 600.0,
         "cash": 200.0, "total_assets": 1000.0, "total_liabilities": 400.0,
         "income_before_tax": 190.0, "income_tax_expense": 40.0,
         "diluted_shares": 100.0, "stock_based_compensation": 10.0,
         "changeInWorkingCapital": -15.0}
        for y in range(2016, 2027)
    ]
    packet = Packet(
        security=Security(ticker="TEST", exchange="NASDAQ",
                          security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Semiconductors"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": list(reversed(annual))},
        facts_table={
            # Exactly the shape JPMorgan produces: sources disagree on cash.
            "cash": Value.null(NullState.CONFLICTED, unit="USD"),
            "total_debt": Value.of(150.0, unit="USD"),
        },
        staleness={"quarterly_fundamentals": "FRESH"},
        market_data={},
        estimates={},
        capital_structure={},
    )

    out = bus.run(packet, {})   # must not raise

    # The contest is declared where a reader will see it.
    assert any("SOURCE_HIERARCHY" in a for a in out.assumptions)

    contested = [m for m in out.metrics
                 if bus.WARN_INPUT_CONFLICTED in (m.warnings or [])]
    assert contested, "the contest must be visible on the metrics it touches"

    # It costs confidence, not the score. Comparing against the same
    # packet with agreeing sources is the precise statement: every score
    # is unchanged, and only the confidence moves. (Several business
    # metrics — BUS-IC-012 among them — carry a value and no score by
    # design, so "is it scored" is the wrong question to ask directly.)
    clean = packet.model_copy(update={
        "facts_table": {"cash": Value.of(200.0, unit="USD"),
                        "total_debt": Value.of(150.0, unit="USD")}})
    clean_out = bus.run(clean, {})

    scores = {m.metric_id: m.score for m in out.metrics}
    clean_scores = {m.metric_id: m.score for m in clean_out.metrics}
    assert scores == clean_scores, "a contested input must not move any score"
    assert clean_out.category.awarded_points == out.category.awarded_points
    assert clean_out.category.confidence > out.category.confidence
