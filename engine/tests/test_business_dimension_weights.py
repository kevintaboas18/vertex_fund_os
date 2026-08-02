"""Intra-dimension weights, against what SCORING.md actually registers.

`SCORING.md` gives each dimension a "Primary inputs" list and no weights —
`grep -c weight` on that file returns zero. SCORING_ENGINE.md says where
they would go if there were any: "Weights must be registered in that
metric's scoring file."

Management carried 0.35 / 0.30 / 0.20 / 0.15 and customer economics
0.4 / 0.3 / 0.3 — numbers in none of Victor's eighty-three documents.
Enumerating inputs without ranking them supports one reading: they weigh
the same.

Customer economics had the worse problem. He names five primary inputs
and only three were scored: GRR and CAC payback were computed, given a
0-10 score, and then dropped, appearing in no dimension at all.
"""

import itertools

import pytest

from wbj.core.nullstates import NullState, Value
from wbj.core.scoring import Dimension
from wbj.specialists import business as bus


def _run_rows():
    """Six plain annual statements — enough for every dimension to be built."""
    row = dict(fiscalYear="2025", filingDate="2026-02-20", revenue=1000.0,
               gross_profit=600.0, ebit=200.0, net_income=150.0,
               operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
               total_debt=150.0, total_equity=600.0, cash=200.0,
               total_assets=1000.0, total_liabilities=400.0,
               income_before_tax=190.0, income_tax_expense=40.0,
               diluted_shares=100.0, stock_based_compensation=10.0,
               changeInWorkingCapital=-15.0)
    return [dict(row, fiscalYear=str(y), filingDate=f"{y + 1}-02-20")
            for y in (2025, 2024, 2023, 2022, 2021, 2020)]


def _run_packet(rows=None):
    from datetime import datetime, timezone

    from wbj.schemas.packet import AnalysisMeta, Packet, Security

    return Packet(
        security=Security(ticker="T", exchange="X",
                          security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Semiconductors"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": rows if rows is not None else _run_rows()},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})


def _emitted(dimension_name, overlay=None):
    """The dimension a real run emits, by name.

    Slot counts and weights are properties of the dimension the engine
    builds, so read them off the output. The previous version sliced them
    out of `_run_once`'s source text between a `"<name>_scores: list"`
    line and the next line equal to `"]"` — which broke whenever the
    block moved or was reindented, and silently measured the wrong block
    if a nested list ever closed first.
    """
    out = bus.run(_run_packet(), {"wacc": 0.09, **(overlay or {})})
    return next(d for d in out.dimensions if d.name == dimension_name)


def _slot_weights(dimension_name):
    return [w for w, _ in _emitted(dimension_name).metric_scores]


def _slot_refs(block_name):
    """What each slot of `<block_name>` refers to, per slot.

    Returns one `(metric_ids, names)` pair per slot, parsed as a syntax
    tree so reindenting or wrapping a slot changes nothing. Which metric
    feeds which slot is not visible on the emitted `Dimension` — it
    carries weights and values, not identifiers — so this is the one
    thing that still has to be read off the code.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(inspect.getmodule(bus)))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_once")

    def target_of(node):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return node.target.id
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            return node.targets[0].id
        return None

    # The name is rebound later (the scores get merged); the list literal
    # that builds the slots is the one this asks about.
    literal = next(n.value for n in ast.walk(fn)
                   if target_of(n) == block_name and isinstance(n.value, ast.List))
    return [
        ({c.value for c in ast.walk(slot)
          if isinstance(c, ast.Constant) and isinstance(c.value, str)},
         {x.id for x in ast.walk(slot) if isinstance(x, ast.Name)})
        for slot in literal.elts
    ]


def _slot_metric_ids(block_name):
    ids: set[str] = set()
    for metric_ids, _ in _slot_refs(block_name):
        ids |= {i for i in metric_ids if i.startswith("BUS-")}
    return ids


def _slot_names(block_name):
    names: set[str] = set()
    for _, slot_names in _slot_refs(block_name):
        names |= slot_names
    return names


def _dim(present):
    """The management dimension with `present` slots scored, rest null."""
    return Dimension(
        name=bus.DIM_MANAGEMENT, max_points=4.0,
        metric_scores=[(0.25, Value.of(10.0, unit="score") if i in present
                        else Value.null(NullState.NOT_SCORABLE, unit="score"))
                       for i in range(4)])


def test_the_four_slots_are_victors_four_primary_inputs():
    """SCORING.md: "Incremental ROIC, allocation history, dilution,
    guidance accuracy". Four inputs, four slots."""
    assert len(_slot_weights(bus.DIM_MANAGEMENT)) == 4
    assert _slot_metric_ids("management_scores") == {
        "BUS-ALLOC-029",    # Incremental ROIC
        "BUS-DIL-028",      # dilution
        "BUS-GUIDE-027",    # guidance accuracy
    }
    assert "capital_return_value" in _slot_names("management_scores")


def test_the_weights_are_equal_because_no_ranking_is_registered():
    """Any other split is this engine deciding that one of his inputs
    matters more than another, which is a methodological judgement his
    documents do not make."""
    weights = _slot_weights(bus.DIM_MANAGEMENT)
    assert weights == [pytest.approx(0.25)] * 4
    assert sum(weights) == pytest.approx(1.0)


def test_scoring_md_registers_no_weights_for_this_dimension():
    """The premise. If Victor ever registers them, this test fails and
    the weights above must be changed to match his file — not the other
    way round."""
    from pathlib import Path

    scoring = (Path(bus.__file__).parents[3] / "Cerebro" / "01_business_analysis"
               / "SCORING.md")
    if not scoring.is_file():        # repo layout differs in CI
        pytest.skip("Cerebro not present")
    text = scoring.read_text(encoding="utf-8").lower()
    assert "weight" not in text, "SCORING.md now registers weights — adopt them"


# --- the gate SCORING.md does state ---------------------------------------


def test_qualitative_reputation_alone_cannot_score_at_all():
    """SCORING.md: "Qualitative reputation alone cannot exceed 5."
    `capital_return_value` is the one slot with no registered formula
    behind it. At 0.25 it cannot reach MISSING_DATA_POLICY.md's 70%
    floor, so alone the dimension is NOT_SCORABLE — it scores nothing,
    let alone more than 5."""
    assert _dim({1}).score10_value().is_null


@pytest.mark.parametrize("pair", list(itertools.combinations(range(4), 2)))
def test_no_two_inputs_reach_the_floor(pair):
    """Two of four is 0.50, below 0.70. Every scorable configuration
    carries at least three of Victor's four inputs."""
    assert _dim(set(pair)).score10_value().is_null


@pytest.mark.parametrize("trio", list(itertools.combinations(range(4), 3)))
def test_any_three_inputs_score(trio):
    """0.75 clears the floor. This includes the trio without incremental
    ROIC — allocation history, dilution and guidance accuracy — which the
    old unequal weights blocked at 0.65. Victor's only gate on this row
    is the qualitative-reputation one, and dilution and guidance accuracy
    are registered formulas, so that trio is not "qualitative reputation
    alone". Blocking it was a side effect of numbers he never wrote."""
    assert not _dim(set(trio)).score10_value().is_null


def test_all_four_inputs_score():
    assert _dim({0, 1, 2, 3}).score10_value().value == pytest.approx(10.0)


# --- customer economics ----------------------------------------------------


def test_all_five_of_victors_customer_inputs_are_scored():
    """SCORING.md: "NRR, GRR, churn, LTV/CAC, payback or repeat-purchase
    proxies". Five inputs, and only three were scored — BUS-GRR-021 and
    BUS-PAYBACK-026 were computed, given a 0-10 score, and then dropped.
    Neither appeared in any dimension, so their scores went nowhere.

    Payback is not a minor omission: his 7-10 band reads "NRR/retention
    and payback show compounding customer value", so the metric his top
    band names was absent from the dimension it decides."""
    assert _slot_metric_ids("customer_scores") == {
        "BUS-NRR-020", "BUS-GRR-021", "BUS-CHURN-022",
        "BUS-LTVCAC-025", "BUS-PAYBACK-026",
    }
    assert len(_slot_weights(bus.DIM_CUSTOMER)) == 5


def test_the_customer_weights_are_equal():
    """Same reasoning as management: five inputs enumerated without rank,
    no weight registered in Cerebro. This carried 0.4 / 0.3 / 0.3 over
    three slots — numbers from none of his documents, on an input list
    that was itself incomplete."""
    weights = _slot_weights(bus.DIM_CUSTOMER)
    assert weights == [pytest.approx(0.2)] * 5
    assert sum(weights) == pytest.approx(1.0)


def _customer_dim(present, applicable=True):
    from wbj.core.nullstates import NullState as NS

    absent = NS.NOT_SCORABLE if applicable else NS.NOT_APPLICABLE
    return Dimension(
        name=bus.DIM_CUSTOMER, max_points=3.0,
        metric_scores=[(0.2, Value.of(10.0, unit="score") if i in present
                        else Value.null(absent, unit="score"))
                       for i in range(5)])


@pytest.mark.parametrize("n", [1, 2, 3])
def test_fewer_than_four_inputs_cannot_score(n):
    """MISSING_DATA_POLICY.md's floor is 70% of metric weight. Three of
    five is 0.60. This is stricter than before — two of the old three
    slots cleared 0.70 — but the old denominator was wrong: it counted
    three inputs where Victor names five."""
    assert _customer_dim(set(range(n))).score10_value().is_null


@pytest.mark.parametrize("n", [4, 5])
def test_four_or_more_inputs_score(n):
    assert not _customer_dim(set(range(n))).score10_value().is_null


def test_an_inapplicable_dimension_leaves_the_denominator_whole():
    """SCORING.md's gate for this row: "If not applicable, use adapter
    metrics; do not impute." All five slots must carry NOT_APPLICABLE
    together, so the dimension leaves the category maximum rather than
    scoring zero."""
    from wbj.core.scoring import Category

    inapplicable = _customer_dim(set(), applicable=False)
    scored = Dimension(name="rest", max_points=17.0,
                       metric_scores=[(1.0, Value.of(6.0, unit="score"))])
    cat = Category(name="business_analysis", max_points=20.0,
                   dimensions=[scored, inapplicable])
    assert cat.applicable_max_points() == pytest.approx(17.0)
    assert cat.score10() == pytest.approx(6.0)


# --- moat and pricing power ------------------------------------------------


def test_moat_has_exactly_victors_three_inputs():
    """SCORING.md's moat row lists three: "ROIC-WACC persistence, margin
    stability, pricing/retention evidence". It carried four slots — the
    spread *level*, the margin range, persistence, and a judgment slot."""
    weights = _slot_weights(bus.DIM_MOAT)
    assert len(weights) == 3, "one slot per stated input"
    assert weights == [pytest.approx(1 / 3)] * 3


def test_the_spread_level_is_the_gate_not_an_input():
    """His two columns say different things: "Primary inputs: ROIC-WACC
    *persistence*" and "Gate / cap: Score capped at 6 without positive
    ROIC-WACC *spread*". The level scored a quarter of the dimension as an
    input he does not list, while also driving the cap he does — counted
    twice."""
    assert "BUS-SPREAD-014" not in _slot_metric_ids("moat_scores"), \
        "the spread level is the gate, not a slot"

    # The gate itself must remain: a company whose latest spread has
    # turned is still capped at 6, which is where the level belongs.
    negative = [dict(r, ebit=5.0) for r in _run_rows()]
    dim = next(d for d in bus.run(_run_packet(negative), {"wacc": 0.09}).dimensions
               if d.name == bus.DIM_MOAT)
    scored = dim.score10_value()
    assert scored.is_null or scored.value <= 6.0


def test_the_moat_judgment_holds_no_dimension_weight():
    """It is not one of his three inputs, and DATA_POLICY.md does not
    score `Q` evidence without a conversion rule Cerebro defines."""
    assert "moat_classification" not in _slot_names("moat_scores")


def test_pricing_retention_evidence_has_a_slot():
    """The input that had none. DATASET.md splits the evidence:
    `competitive_evidence` is "context only" and cannot score, while
    `retention_churn_cohorts` is conditional and measured by three
    registered formulas — so the scorable half is retention."""
    assert "v_retention" in _slot_names("moat_scores")

    # And the slot is those three registered formulas, not a judgment
    # call: they are the metrics the customer-economics dimension scores
    # retention from, and the moat slot reads the same ones.
    assert {"BUS-NRR-020", "BUS-GRR-021", "BUS-CHURN-022"} <= \
        _slot_metric_ids("customer_scores")


def _moat_dim(states):
    """`states` is three of "ok" / "ns" / "na", in his input order."""
    from wbj.core.nullstates import NullState as NS

    scores = []
    for st in states:
        if st == "ok":
            scores.append((1 / 3, Value.of(9.0, unit="score")))
        else:
            null = NS.NOT_APPLICABLE if st == "na" else NS.NOT_SCORABLE
            scores.append((1 / 3, Value.null(null, unit="score")))
    return Dimension(name=bus.DIM_MOAT, max_points=5.0, metric_scores=scores)


def test_all_three_inputs_score():
    assert _moat_dim(["ok", "ok", "ok"]).score10_value().value == pytest.approx(9.0)


def test_two_of_three_falls_below_the_floor():
    """0.67 against MISSING_DATA_POLICY.md's 0.70. His 0-3 band is "no
    measurable retention/pricing evidence" — a company that cannot show
    it does not get a moat score on the other two inputs alone."""
    assert _moat_dim(["ok", "ok", "ns"]).score10_value().is_null


def test_an_inapplicable_retention_input_leaves_the_denominator():
    """A model with no repeat customers has no retention evidence to
    measure. MISSING_DATA_POLICY.md step 1 takes the dimension's other
    inputs rather than charging it for one its model does not produce."""
    scored = _moat_dim(["ok", "ok", "na"]).score10_value()
    assert not scored.is_null
    assert scored.value == pytest.approx(9.0)


def test_the_dimension_had_no_headroom_before_this_change():
    """The four-slot arrangement left 0.75 as the best case against a
    0.70 floor, so the dimension scored only when every real input was
    present. Three slots restore the margin his row implies."""
    assert _moat_dim(["ok", "ok", "ok"]).valid_weight() == pytest.approx(1.0)


# --- competitive position --------------------------------------------------


def test_the_peer_comparison_covers_margins_as_well_as_roic():
    """SCORING.md's input is "peer ROIC/margins" — one comparison carrying
    a slash, the same shape as durability's "margin/ROIC persistence".
    Only ROIC was compared; the margin half was never fetched, though it
    comes from the same peer income statement the ROIC fetch already
    reads.

    Both halves have to change the answer: supplying only one of them
    used to give the same score as supplying both, because the margin
    half was never read.
    """
    peers = [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25]
    ahead = [0.90] * 9        # a peer set this company beats outright

    def score(**overlay):
        return _emitted(bus.DIM_COMPETITIVE, overlay).score10_value()

    both_weak = score(peer_roic=ahead, peer_operating_margin=ahead)
    roic_only_weak = score(peer_roic=ahead, peer_operating_margin=peers)
    margin_only_weak = score(peer_roic=peers, peer_operating_margin=ahead)

    assert not both_weak.is_null
    # Swapping which half is the hard comparison has to move the score,
    # and neither one-sided read may equal the two-sided one.
    assert roic_only_weak.value != pytest.approx(both_weak.value)
    assert margin_only_weak.value != pytest.approx(both_weak.value)


def test_a_peer_margin_percentile_reaches_the_dimension():
    """The path that shipped broken: `by_id` holds rows whose `.value` is
    a `Value`, and the percentile needs the number inside it. Every test
    passed because none supplied a peer margin set, so the branch never
    ran."""
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
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": [dict(row) for _ in range(6)]},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})

    peers = [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25]
    out = bus.run(packet, {"wacc": 0.09,
                           "peer_roic": peers,
                           "peer_operating_margin": peers})
    dim = next(d for d in out.dimensions if d.name == bus.DIM_COMPETITIVE)
    assert not dim.score10_value().is_null


def test_the_two_peer_halves_weigh_equally():
    """He names ROIC and margins together and ranks neither, so a company
    ahead on one and behind on the other lands between them — and lands
    in the same place whichever half it is ahead on."""
    strong = [0.01] * 9      # a peer set this company beats
    weak = [0.90] * 9        # a peer set that beats this company

    def score(roic, margin):
        return _emitted(bus.DIM_COMPETITIVE,
                        {"peer_roic": roic,
                         "peer_operating_margin": margin}).score10_value()

    best = score(strong, strong)
    worst = score(weak, weak)
    ahead_on_roic = score(strong, weak)
    ahead_on_margin = score(weak, strong)

    assert best.value > worst.value
    # Equal weight: swapping which half is the strong one changes nothing.
    assert ahead_on_roic.value == pytest.approx(ahead_on_margin.value)
    # And a split verdict lands between the two one-sided ones.
    assert worst.value < ahead_on_roic.value < best.value


def test_a_missing_peer_set_is_not_scorable_not_zero():
    """SCORING_ENGINE.md: "If fewer than 8 peers exist, use absolute rules
    or mark the peer component NOT_SCORABLE." Absent peers must not read
    as a company that ranks badly."""
    from wbj.core.scoring import peer_score

    assert peer_score(0.15, [0.1, 0.2, 0.3]).is_null      # only three peers
    assert peer_score(0.15, [0.1] * 8).is_valid


# --- business durability ---------------------------------------------------


def _persistence_slot(wacc):
    """The margin/ROIC-persistence slot, and the margin half's own score.

    The margins fall 25%..17% over the window, so the margin half lands
    well short of 10 and the two halves can be told apart. `wacc` moves
    the ROIC half on its own: below every year's ROIC it scores 10, above
    every year's it scores 0, and omitted it is unavailable.
    """
    rows = [dict(r, ebit=e) for r, e in
            zip(_run_rows(), [170.0, 190.0, 210.0, 230.0, 250.0, 250.0])]
    out = bus.run(_run_packet(rows), {} if wacc is None else {"wacc": wacc})
    dim = next(d for d in out.dimensions if d.name == bus.DIM_DURABILITY)
    margin_half = next(m for m in out.metrics if m.metric_id == "BUS-RANGE-010")
    return dim.metric_scores[2][1], margin_half.score


def test_margin_roic_persistence_is_one_input_not_two():
    """SCORING.md: "Recurring revenue, concentration, margin/ROIC
    persistence, cyclicality". The slash marks one input measured two
    ways, the same shape as competitive position's "peer ROIC/margins".

    It held two slots, so that single input carried half the dimension
    while "Recurring revenue" and "concentration" carried a quarter
    each."""
    weights = _slot_weights(bus.DIM_DURABILITY)
    assert len(weights) == 3
    assert weights == [pytest.approx(1 / 3)] * 3
    assert _slot_metric_ids("durability_scores") == {"BUS-REC-002", "BUS-CONC-003"}
    assert "v_margin_roic_persistence" in _slot_names("durability_scores")


def test_both_halves_of_persistence_weigh_equally_inside_their_slot():
    """The slot is the mean of the two halves, so a 10-point swing in one
    of them moves the slot by five. An unequal split — or a slot that read
    only one half — would not land on the midpoint."""
    strong_roic, margin = _persistence_slot(wacc=0.05)   # ROIC half at 10
    weak_roic, margin_again = _persistence_slot(wacc=0.90)  # ROIC half at 0
    assert margin == pytest.approx(margin_again)

    assert strong_roic.value == pytest.approx((margin + 10.0) / 2)
    assert weak_roic.value == pytest.approx((margin + 0.0) / 2)


def test_one_half_of_persistence_still_carries_the_slot():
    """A company with margin history but too little spread history — or
    the reverse — still has persistence evidence. The slot is
    NOT_SCORABLE only when neither half is available."""
    alone, margin = _persistence_slot(wacc=None)    # no WACC, no ROIC half
    assert not alone.is_null
    assert alone.value == pytest.approx(margin)

    # Neither half: one statement row gives no range and no spread window.
    out = bus.run(_run_packet(_run_rows()[:1]), {"wacc": 0.09})
    dim = next(d for d in out.dimensions if d.name == bus.DIM_DURABILITY)
    empty = dim.metric_scores[2][1]
    assert empty.is_null
    assert "MARGIN_ROIC_PERSISTENCE_UNAVAILABLE" in empty.warnings


def test_cyclicality_has_no_slot_because_it_has_no_formula():
    """His 0-3 band names it — "fragile cycle economics" — but no formula
    in the thirty-metric registry measures it and no threshold is stated,
    so it meets neither of SCORING_ENGINE.md's scoring paths. A slot that
    can never score would repeat the moat dimension's mistake: weight held
    for something unscoreable, crowding the inputs that work."""
    slot_text = " ".join(
        sorted(_slot_metric_ids("durability_scores") | _slot_names("durability_scores")))
    assert "cyclical" not in slot_text.lower()
    assert len(_slot_weights(bus.DIM_DURABILITY)) == 3

    # And no registered formula claims to measure it.
    import re
    from pathlib import Path

    formulas = (Path(bus.__file__).parents[3] / "Cerebro" / "01_business_analysis"
                / "FORMULAS.md")
    if not formulas.is_file():
        pytest.skip("Cerebro not present")
    text = formulas.read_text(encoding="utf-8").lower()
    assert "cyclicality" not in text, "a formula now measures it — give it a slot"


def _durability_dim(n_valid):
    return Dimension(
        name=bus.DIM_DURABILITY, max_points=4.0,
        metric_scores=[(1 / 3, Value.of(9.0, unit="score") if i < n_valid
                        else Value.null(NullState.NOT_SCORABLE, unit="score"))
                       for i in range(3)])


@pytest.mark.parametrize("n,scores", [(1, False), (2, False), (3, True)])
def test_the_dimension_needs_all_three_measurable_inputs(n, scores):
    """Three equal thirds against a 0.70 floor: two is 0.67."""
    assert (not _durability_dim(n).score10_value().is_null) is scores


# --- the structural guard --------------------------------------------------
#
# Three separate defects had one shape: a dimension whose slots did not
# match the "Primary inputs" SCORING.md lists. Customer economics scored
# three of his five. Moat held four slots for his three — the spread level
# he assigns to the gate column, plus a judgment slot absent from his
# inputs entirely. Durability gave "margin/ROIC persistence" two slots, so
# one input carried half the dimension.
#
# Fixing each instance does not stop the next one. These read his file.


def _victors_inputs():
    """Every dimension's "Primary inputs", parsed from SCORING.md."""
    from pathlib import Path

    scoring = (Path(bus.__file__).parents[3] / "Cerebro" / "01_business_analysis"
               / "SCORING.md")
    if not scoring.is_file():
        pytest.skip("Cerebro not present")
    out = {}
    for line in scoring.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or "---" in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 4 and cells[1] not in ("Dimension", ""):
            out[cells[1]] = [i.strip() for i in cells[3].split(",")]
    return out


def _slot_count(dimension_name):
    """How many slots the dimension actually carries.

    Counted off the emitted `Dimension`, so it is the arrangement the
    engine scores with — not a regex over the source lines that build it,
    which counted whatever happened to start with a weight-shaped token
    and missed any slot written across two lines.
    """
    return len(_slot_weights(dimension_name))


#: His dimension name -> this module's dimension and slot list, and the
#: inputs he names that no registered formula measures. Those two are the
#: only reason a dimension may hold fewer slots than he lists; see the
#: durability and competitive-position tests above for why each is absent.
_DIMENSION_BLOCKS = {
    "Moat and pricing power": (bus.DIM_MOAT, "moat_scores", set()),
    "Competitive position": (bus.DIM_COMPETITIVE, "competitive_scores", {"rank"}),
    "Management and capital allocation": (
        bus.DIM_MANAGEMENT, "management_scores", set()),
    "Business durability": (bus.DIM_DURABILITY, "durability_scores", {"cyclicality"}),
    "Customer economics": (bus.DIM_CUSTOMER, "customer_scores", set()),
}


@pytest.mark.parametrize("dimension", sorted(_DIMENSION_BLOCKS))
def test_no_dimension_holds_more_slots_than_victor_lists_inputs(dimension):
    """The moat failure: a slot for the spread level, which his row puts
    in the gate column, and another for a judgment absent from his inputs.
    Weight held for things his own row assigns elsewhere."""
    dim_name, _, _ = _DIMENSION_BLOCKS[dimension]
    inputs = _victors_inputs()[dimension]
    assert _slot_count(dim_name) <= len(inputs), (
        f"{dimension}: more slots than the inputs SCORING.md lists")


@pytest.mark.parametrize("dimension", sorted(_DIMENSION_BLOCKS))
def test_every_measurable_input_has_its_own_slot(dimension):
    """The customer-economics failure in one direction and the durability
    failure in the other: an input scored nowhere, and an input scored
    twice. Both show up as a slot count that does not equal the number of
    inputs a registered formula can measure."""
    dim_name, _, unmeasurable = _DIMENSION_BLOCKS[dimension]
    inputs = _victors_inputs()[dimension]
    measurable = [i for i in inputs
                  if not any(u in i.lower() for u in unmeasurable)]
    assert _slot_count(dim_name) == len(measurable), (
        f"{dimension}: {len(measurable)} measurable inputs, "
        f"{_slot_count(dim_name)} slots")


def test_the_unmeasurable_inputs_are_still_unmeasurable():
    """`_DIMENSION_BLOCKS` excuses two inputs because no registered
    formula measures them. If FORMULAS.md ever gains one, the excuse
    expires and the input earns a slot."""
    from pathlib import Path

    formulas = (Path(bus.__file__).parents[3] / "Cerebro" / "01_business_analysis"
                / "FORMULAS.md")
    if not formulas.is_file():
        pytest.skip("Cerebro not present")
    text = formulas.read_text(encoding="utf-8").lower()
    for _, _, unmeasurable in _DIMENSION_BLOCKS.values():
        for name in unmeasurable:
            assert name not in text, f"{name} now has a formula — give it a slot"
