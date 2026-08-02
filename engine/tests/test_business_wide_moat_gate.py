"""DECISION_RULES.md's wide-moat gate, all four conditions.

Two of them are written with an alternative and neither was implemented,
so the gate was stricter than the rule:

  1. "ROIC exceeds WACC by at least 5 percentage points in at least four
     of the last five fiscal years, *or an approved financial-sector
     adapter shows equivalent excess returns*."
  2. "Five-year operating-margin range is no more than 5 percentage
     points, *or peer-relative resilience is in the top quartile through
     a cycle*."

A bank whose ROE beat its cost of equity every year of the window, or a
company whose margin held up better through the downturn than three
peers in four, was refused a label his own rule allows.
"""

import pytest

from wbj.specialists import business as bus


def _year(net_income, equity=100.0):
    return {"net_income": net_income, "total_equity": equity}


# --- condition 1's alternative --------------------------------------------


def test_the_adapter_branch_uses_the_same_standard_as_the_first():
    """"Equivalent excess returns" is read as the same standard, not a
    softer one: the same five points over the same five-year window."""
    # ke = 8%, so 5pp of excess needs ROE >= 13%.
    every_year = bus.adapter_excess_return_persistence([_year(15.0)] * 5, 0.08)
    assert every_year == pytest.approx(1.0)
    assert every_year >= bus.WIDE_MOAT_MIN_PERSISTENCE


def test_four_of_five_years_meets_it_and_three_does_not():
    """His threshold, on his window."""
    four = bus.adapter_excess_return_persistence(
        [_year(9.0)] + [_year(15.0)] * 4, 0.08)
    three = bus.adapter_excess_return_persistence(
        [_year(9.0)] * 2 + [_year(15.0)] * 3, 0.08)
    assert four == pytest.approx(0.8) and four >= bus.WIDE_MOAT_MIN_PERSISTENCE
    assert three == pytest.approx(0.6) and three < bus.WIDE_MOAT_MIN_PERSISTENCE


def test_only_the_last_five_years_count():
    """The same window the first branch reads. A long history of excess
    returns that stopped four years ago does not meet a rule about the
    last five."""
    lapsed = [_year(20.0)] * 6 + [_year(5.0)] * 5
    assert bus.adapter_excess_return_persistence(lapsed, 0.08) == pytest.approx(0.0)


def test_a_missing_cost_of_equity_leaves_the_branch_unavailable():
    """Without the comparison rate there is no excess to measure, and an
    unavailable branch must not read as a passing one."""
    assert bus.adapter_excess_return_persistence([_year(15.0)] * 5, None) is None


@pytest.mark.parametrize("rows", [
    [], [_year(15.0, equity=0.0)] * 5, [_year(15.0, equity=-100.0)] * 5,
    [{"total_equity": 100.0}] * 5,          # no net income reported
])
def test_an_unreadable_return_leaves_the_branch_unavailable(rows):
    """Negative or zero equity makes ROE meaningless, which
    CALCULATION_CONVENTIONS.md treats as not meaningful rather than as a
    number to score."""
    assert bus.adapter_excess_return_persistence(rows, 0.08) is None


def test_the_branch_only_applies_to_a_financial_sector_adapter():
    """His words are "an approved financial-sector adapter". The
    conventional formulas already produce a ROIC-WACC spread for every
    other security, so the alternative is not theirs to take.

    Same numbers, same WACC, only the adapter differs: the bank may reach
    condition 1 through its ROE, the software company may not.
    """
    rows = [_annual(y) for y in (2025, 2024, 2023, 2022, 2021, 2020)]
    # A WACC no ROIC here can beat, so the first branch cannot carry
    # condition 1 and only the alternative is left.
    overlay = {"wacc": 0.50, "cost_of_equity": 0.08}

    bank = bus.run(_packet(rows, adapter="banks",
                           sector="Financial Services",
                           industry="Banks - Diversified"), overlay)
    other = bus.run(_packet(rows), overlay)

    assert any("condition 1 met through its alternative" in a
               for a in bank.assumptions)
    assert not any("condition 1 met through its alternative" in a
                   for a in other.assumptions)
    assert other.moat_gate_inputs.mechanical_conditions_pass is False


# --- condition 2's alternative --------------------------------------------


#: Peer drawdowns, worst-to-best spread. A smaller fall is more resilient.
PEERS = [0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25]


@pytest.mark.parametrize("own,expected", [
    (0.01, True),    # better than every peer
    (0.06, True),    # better than seven of nine
    (0.13, False),   # middle of the field
    (0.30, False),   # worse than every peer
])
def test_top_quartile_means_better_than_three_peers_in_four(own, expected):
    assert bus.peer_resilience_is_top_quartile(own, PEERS) is expected


def test_a_smaller_drawdown_ranks_better():
    """Resilience is the inverse of the fall: the direction has to be
    right or the branch would reward the least resilient company."""
    assert bus.peer_resilience_is_top_quartile(0.01, PEERS) is True
    assert bus.peer_resilience_is_top_quartile(0.30, PEERS) is False


def test_the_percentile_needs_victors_eight_peers():
    """SCORING_ENGINE.md: "Use a minimum of 8 valid peers. If fewer than 8
    peers exist, use absolute rules or mark the peer component
    NOT_SCORABLE." With fewer, the branch is unavailable — not passed."""
    assert bus._MIN_PEERS_FOR_PERCENTILE == 8
    assert bus.peer_resilience_is_top_quartile(0.01, [0.1, 0.2, 0.3]) is False
    assert bus.peer_resilience_is_top_quartile(0.01, PEERS[:7]) is False
    assert bus.peer_resilience_is_top_quartile(0.01, PEERS[:8]) is True


def test_no_own_drawdown_leaves_the_branch_unavailable():
    """A company whose history does not span a recession has no
    resilience reading, which is not the same as a poor one."""
    assert bus.peer_resilience_is_top_quartile(None, PEERS) is False


def test_no_peer_drawdowns_leave_the_branch_unavailable():
    assert bus.peer_resilience_is_top_quartile(0.01, []) is False


# --- the gate reads them ---------------------------------------------------


def _wide_range_shallow_dip():
    """Margins falling 25%..17% over the window — a 7pp range, so
    condition 2's first branch (a range of 5pp or less) cannot carry it —
    with only a 1pp dip into the 2020 recession, more resilient than any
    of PEERS. Newest first, as the packet carries them."""
    return [_annual(2025, ebit=170.0), _annual(2024, ebit=190.0),
            _annual(2023, ebit=210.0), _annual(2022, ebit=230.0),
            _annual(2021, ebit=250.0), _annual(2020, ebit=250.0),
            _annual(2019, ebit=260.0)]


def test_the_gate_treats_both_conditions_as_alternatives():
    """The defect in its structural form: each condition must be
    satisfiable two ways.

    Condition 1's second branch is covered end to end by the bank tests
    below; this is condition 2's. The margin range fails it either way,
    so the peer comparison is the only thing that can carry the gate —
    supply the peers and it passes, withhold them and it does not.
    """
    with_peers = bus.run(_packet(_wide_range_shallow_dip()),
                         {"wacc": 0.09, "recession_years": [2020],
                          "peer_recession_drawdown": PEERS})
    without = bus.run(_packet(_wide_range_shallow_dip()),
                      {"wacc": 0.09, "recession_years": [2020]})

    assert with_peers.moat_gate_inputs.mechanical_conditions_pass is True
    assert without.moat_gate_inputs.mechanical_conditions_pass is False


def test_taking_an_alternative_is_declared_in_the_output():
    """A Wide-moat label reached through the alternative rests on a
    different measurement than one reached through the first branch, and
    a reader has to be able to tell which. Both alternatives have to say
    so in the output the reader sees, not only in the code."""
    out = bus.run(_packet(_wide_range_shallow_dip()),
                  {"wacc": 0.09, "recession_years": [2020],
                   "peer_recession_drawdown": PEERS})
    assert any("wide-moat condition 2 met through its alternative" in a
               for a in out.assumptions)

    bank = bus.run(
        _packet([_annual(y) for y in (2025, 2024, 2023, 2022, 2021, 2020)],
                adapter="banks", sector="Financial Services",
                industry="Banks - Diversified"),
        {"wacc": 0.50, "cost_of_equity": 0.08})
    assert any("wide-moat condition 1 met through its alternative" in a
               for a in bank.assumptions)


def test_the_thresholds_are_the_ones_he_states():
    """Both branches lean on the same three numbers, all his:
    five percentage points, four of the last five years, top quartile."""
    assert bus.WIDE_MOAT_MIN_SPREAD == pytest.approx(0.05)
    assert bus.WIDE_MOAT_PERSISTENCE_WINDOW == 5
    assert bus.WIDE_MOAT_MIN_PERSISTENCE == pytest.approx(0.8)
    assert bus._TOP_QUARTILE_SCORE == pytest.approx(7.5)


def test_condition_four_still_stands_alone():
    """"No unresolved customer/product concentration threat invalidates
    durability" is written without an alternative, and must not acquire
    one."""
    rows = _wide_range_shallow_dip()
    passing = {"wacc": 0.09, "recession_years": [2020],
               "peer_recession_drawdown": PEERS}

    assert bus.run(_packet(rows),
                   passing).moat_gate_inputs.mechanical_conditions_pass is True

    # DECISION_RULES.md flags concentration above 30% of revenue. One
    # customer at 45% must sink the gate on its own, with conditions 1
    # and 2 left exactly as they were.
    flagged = bus.run(_packet(rows),
                      {**passing, "largest_customer_share": 0.45})
    assert flagged.moat_gate_inputs.mechanical_conditions_pass is False

    # And a share below the threshold must not.
    assert bus.run(_packet(rows), {**passing, "largest_customer_share": 0.10}) \
        .moat_gate_inputs.mechanical_conditions_pass is True


def test_a_financial_sector_packet_runs_the_adapter_branch_end_to_end():
    """The wiring, not the helper. This shipped broken once: the gate
    reached for statement rows that are not in scope where it runs, and
    only a financial-sector adapter reaches that line — every other
    security short-circuits before it. The whole suite passed, because
    every test exercised the helper directly or ran a non-financial
    packet."""
    from datetime import datetime, timezone

    from wbj.schemas.packet import AnalysisMeta, Packet, Security

    row = dict(fiscalYear="2025", filingDate="2026-02-20", revenue=1000.0,
               gross_profit=600.0, ebit=200.0, net_income=150.0,
               operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
               total_debt=150.0, total_equity=600.0, cash=200.0,
               total_assets=1000.0, total_liabilities=400.0,
               income_before_tax=190.0, income_tax_expense=40.0,
               diluted_shares=100.0, stock_based_compensation=10.0,
               changeInWorkingCapital=-15.0)
    packet = Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Financial Services", industry="Banks - Diversified"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="banks"),
        fundamentals={"annual": [dict(row) for _ in range(6)]},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})

    # A WACC high enough that the first branch cannot pass, so only the
    # alternative can carry condition 1 — otherwise the first branch
    # would satisfy it and the alternative would go untested.
    out = bus.run(packet, {"wacc": 0.50, "cost_of_equity": 0.08})
    assert out.category.max_points == 20.0

    # net_income 150 / equity 600 = 25% ROE against an 8% cost of equity:
    # 17 points of excess, every year of the window.
    assert any("condition 1 met through its alternative" in a
               for a in out.assumptions)


def test_the_alternative_is_not_announced_when_the_first_branch_carries_it():
    """The declaration marks a label reached a different way. A company
    that meets condition 1 outright did not need the alternative and must
    not be described as having used it."""
    from datetime import datetime, timezone

    from wbj.schemas.packet import AnalysisMeta, Packet, Security

    row = dict(fiscalYear="2025", filingDate="2026-02-20", revenue=1000.0,
               gross_profit=600.0, ebit=200.0, net_income=150.0,
               operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
               total_debt=150.0, total_equity=600.0, cash=200.0,
               total_assets=1000.0, total_liabilities=400.0,
               income_before_tax=190.0, income_tax_expense=40.0,
               diluted_shares=100.0, stock_based_compensation=10.0,
               changeInWorkingCapital=-15.0)
    packet = Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Financial Services", industry="Banks - Diversified"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="banks"),
        fundamentals={"annual": [dict(row) for _ in range(6)]},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})

    out = bus.run(packet, {"wacc": 0.09, "cost_of_equity": 0.08})
    assert not any("condition 1 met through its alternative" in a
                   for a in out.assumptions)


@pytest.mark.parametrize("adapter", ["banks", "insurers", "reits", "biotech",
                                     "default_nonfinancial", "saas_subscriptions",
                                     "commodities_cyclicals"])
def test_the_gate_runs_for_every_adapter(adapter):
    """The branch that broke was reachable only through one adapter. Walk
    all of them."""
    from datetime import datetime, timezone

    from wbj.schemas.packet import AnalysisMeta, Packet, Security

    row = dict(fiscalYear="2025", filingDate="2026-02-20", revenue=1000.0,
               gross_profit=600.0, ebit=200.0, net_income=150.0,
               operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
               total_debt=150.0, total_equity=600.0, cash=200.0,
               total_assets=1000.0, total_liabilities=400.0,
               income_before_tax=190.0, income_tax_expense=40.0,
               diluted_shares=100.0, stock_based_compensation=10.0,
               changeInWorkingCapital=-15.0)
    packet = Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Semiconductors"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter=adapter),
        fundamentals={"annual": [dict(row) for _ in range(6)]},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})

    out = bus.run(packet, {"wacc": 0.09, "cost_of_equity": 0.08})
    assert out.moat_gate_inputs is not None


# --- what "positive spread" governs, and what it does not ------------------


def _packet(rows, adapter="default_nonfinancial",
            sector="Technology", industry="Semiconductors"):
    from datetime import datetime, timezone

    from wbj.schemas.packet import AnalysisMeta, Packet, Security

    return Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector=sector, industry=industry),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter=adapter),
        fundamentals={"annual": rows}, facts_table={}, market_data={},
        estimates={}, capital_structure={}, staleness={})


def _annual(year, *, equity=600.0, ebit=200.0):
    """One statement row. `equity` moves invested capital without touching
    the operating margin, so ROIC can be varied on its own."""
    return dict(fiscalYear=str(year), filingDate=f"{year + 1}-02-20",
                revenue=1000.0, gross_profit=600.0, ebit=ebit, net_income=150.0,
                operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
                total_debt=150.0, total_equity=equity, cash=200.0,
                total_assets=1000.0, total_liabilities=400.0,
                income_before_tax=190.0, income_tax_expense=40.0,
                diluted_shares=100.0, stock_based_compensation=10.0,
                changeInWorkingCapital=-15.0)


#: Four strong years and a latest one whose ROIC collapses on a capital
#: raise — the margin is untouched, so only the spread moves. Newest
#: first, as the packet carries them.
_LATEST_YEAR_NEGATIVE = ([_annual(2025, equity=20000.0)]
                         + [_annual(y) for y in (2024, 2023, 2022, 2021, 2020)])


def test_a_negative_latest_spread_no_longer_fails_the_gate():
    """His condition 1 is "ROIC exceeds WACC by at least 5 percentage
    points in at least four of the last five fiscal years" — persistence
    across the window, not the latest year. `positive_spread` held inside
    the gate denied a Wide-moat label to a company meeting all four
    conditions whose most recent year alone had turned."""
    out = bus.run(_packet(_LATEST_YEAR_NEGATIVE), {"wacc": 0.09})
    assert out.moat_gate_inputs.positive_spread is False
    assert out.moat_gate_inputs.mechanical_conditions_pass is True


def test_the_dimension_cap_still_uses_it():
    """SCORING.md's moat row: "Score capped at 6 without positive
    ROIC-WACC spread". Removing it from the gate must not remove it from
    the cap."""
    out = bus.run(_packet(_LATEST_YEAR_NEGATIVE), {"wacc": 0.09})
    dim = next(d for d in out.dimensions if d.name == bus.DIM_MOAT)
    assert dim.score10_value().value == pytest.approx(6.0)


def test_the_excellent_verdict_row_still_requires_it():
    """DECISION_RULES.md's verdict table, Excellent row: "ROIC >=20% or
    top-decile adapter return, positive spread, FCF conversion >=0.9x,
    moat gate passes". That is where the latest spread belongs."""
    out = bus.run(_packet(_LATEST_YEAR_NEGATIVE), {"wacc": 0.09})
    assert out.moat_gate_inputs.excellent_gate_passes(2) is False


def test_the_gate_expression_is_only_his_four_conditions():
    """`positive_spread` is not one of the four, and the three that are
    have to be required together. Each of the three tests above trips one
    condition on its own and sinks the gate; this one holds the gate open
    while the spread turns, which must not."""
    out = bus.run(_packet(_LATEST_YEAR_NEGATIVE), {"wacc": 0.09})
    assert out.moat_gate_inputs.positive_spread is False
    assert out.moat_gate_inputs.mechanical_conditions_pass is True


# --- the cap's own alternative ---------------------------------------------


_WEAK = [_annual(y, ebit=5.0) for y in (2025, 2024, 2023, 2022, 2021, 2020)]


def test_a_model_replacing_adapter_lifts_the_cap():
    """SCORING.md's cap in full: "Score capped at 6 without positive
    ROIC-WACC spread *or a valid adapter*." The alternative was missing,
    so a bank was capped on a spread INDUSTRY_ADAPTERS.md tells this
    engine not to use for it — "Replace ROIC with ROE, ROTCE"."""
    out = bus.run(_packet(_WEAK, adapter="banks"), {"wacc": 0.50})
    assert any("moat cap lifted through its alternative" in a
               for a in out.assumptions)


@pytest.mark.parametrize("adapter", ["default_nonfinancial", "saas_subscriptions",
                                     "commodities_cyclicals"])
def test_an_adapter_that_keeps_the_formulas_keeps_the_cap(adapter):
    """SaaS only adds metrics and commodities only normalise inputs, so
    the conventional spread is still the right measure for them and the
    cap stands. "A valid adapter" means one that replaces the model."""
    out = bus.run(_packet(_WEAK, adapter=adapter), {"wacc": 0.50})
    assert not any("moat cap lifted" in a for a in out.assumptions)


def test_a_positive_spread_needs_no_alternative():
    """The cap does not fire, so nothing is lifted and nothing is
    announced."""
    out = bus.run(_packet([_annual(y) for y in
                           (2025, 2024, 2023, 2022, 2021, 2020)]), {"wacc": 0.09})
    assert out.moat_gate_inputs.positive_spread is True
    assert not any("moat cap lifted" in a for a in out.assumptions)


# --- condition 3 is a count, not a label -----------------------------------


def test_condition_three_counts_visible_moat_effects():
    """DECISION_RULES.md's condition 3: "At least two independent moat
    effects are quantitatively visible". The gate read it through
    `moat_classification == "Wide"` — a summary label — where the
    `moat_quantitative_effects_count` judgment answers the count itself,
    now stored in `moat.quantitative_evidence`."""
    all_else = bus.MoatGateInputs(
        mechanical_conditions_pass=True, roic_at_least_20pct=True,
        positive_spread=True, fcf_conversion_at_least_0_9=True)
    assert bus.WIDE_MOAT_MIN_EFFECTS == 2
    assert all_else.excellent_gate_passes(0) is False
    assert all_else.excellent_gate_passes(1) is False
    assert all_else.excellent_gate_passes(2) is True
    assert all_else.excellent_gate_passes(3) is True


def test_the_verdict_reads_the_count_from_the_evidence_list():
    """End to end through the recomputation: two visible effects lift the
    label, one does not, so the gate tracks the evidence rather than the
    classification word."""
    from wbj.deep import _recompute_business_verdict
    from wbj.specialists.common import CategoryStats, SecurityRef

    gate = bus.MoatGateInputs(
        mechanical_conditions_pass=True, roic_at_least_20pct=True,
        positive_spread=True, fcf_conversion_at_least_0_9=True)

    def _out(effects):
        return bus.BusinessOutput(
            agent_id=bus.AGENT_ID, status="COMPLETE",
            security=SecurityRef(ticker="T", exchange="X", currency="USD"),
            knowledge_timestamp="2026-01-01T00:00:00Z",
            category=CategoryStats(max_points=20.0, awarded_points=16.62,
                                   score_10=8.31, confidence=80.0),
            verdict="Good business", coverage=1.0, dimensions=[], metrics=[],
            moat=bus.MoatSummary(classification="Wide",
                                 quantitative_evidence=list(effects)),
            moat_gate_inputs=gate)

    two = _recompute_business_verdict(
        _out(["network scale", "cost advantage"]))
    one = _recompute_business_verdict(_out(["network scale"]))
    assert two.verdict == "Excellent business"
    assert one.verdict == "Good business"


def test_the_gate_no_longer_reads_the_classification_word():
    """Condition 3 is "at least two independent moat effects are
    quantitatively visible", which is a count of evidence. The word
    "Wide" is the judge's label for that evidence, and reading the label
    let an unjudged moat through: it is the count that decides, and the
    classification must not change the answer either way."""
    passing = bus.MoatGateInputs(
        mechanical_conditions_pass=True, roic_at_least_20pct=True,
        positive_spread=True, fcf_conversion_at_least_0_9=True)

    # The count decides, on its own.
    assert passing.excellent_gate_passes(bus.WIDE_MOAT_MIN_EFFECTS) is True
    assert passing.excellent_gate_passes(bus.WIDE_MOAT_MIN_EFFECTS - 1) is False
    # An unjudged moat has no evidence, so it fails rather than passing by
    # default — which is what reading the "Wide" label used to allow.
    assert passing.excellent_gate_passes(0) is False
