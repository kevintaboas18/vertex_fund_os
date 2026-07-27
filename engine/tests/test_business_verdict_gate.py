"""The verdict label, the wide-moat gate, and the moat schema field.

`DECISION_RULES.md`'s Excellent row requires "moat gate passes", and the
wide-moat gate has four conditions. Three are mechanical. The fourth --
"At least two independent moat effects are quantitatively visible" --
needs a reader, so it arrives through the judgment layer as
`moat.classification`. Three defects met at that seam:

* `rescore` recomputed the category but not the verdict, so a judgment
  that moved the score across a band boundary left the old label.
* the gate omitted the fourth condition entirely, treating an unanswered
  question as answered yes.
* `moat.classification` was never written from the judgment at all, so
  OUTPUT_SCHEMA.md's required moat block kept its "NotScorable"
  placeholder next to a score the judgment had already moved.
"""

import pytest

from wbj.core.nullstates import NullState, Value
from wbj.core.scoring import Category, Dimension
from wbj.specialists import business as bus
from wbj.specialists.common import CategoryStats, SecurityRef


def _dims(fourth_moat_slot):
    """Scores chosen so the moat judgment crosses the 8.0 Excellent line:
    7.75 without it, 8.31 with it."""
    return [
        Dimension(name=bus.DIM_MOAT, max_points=5.0,
                  metric_scores=[(0.25, Value.of(1.0, unit="score"))] * 3
                  + [(0.25, fourth_moat_slot)]),
        Dimension(name=bus.DIM_COMPETITIVE, max_points=4.0,
                  metric_scores=[(1.0, Value.of(10.0, unit="score"))]),
        Dimension(name=bus.DIM_MANAGEMENT, max_points=4.0,
                  metric_scores=[(1.0, Value.of(10.0, unit="score"))]),
        Dimension(name=bus.DIM_DURABILITY, max_points=4.0,
                  metric_scores=[(1.0, Value.of(10.0, unit="score"))]),
        Dimension(name=bus.DIM_CUSTOMER, max_points=3.0,
                  metric_scores=[(1.0, Value.of(10.0, unit="score"))]),
    ]


def _output(*, score10, verdict, classification, gate=None, dimensions=None,
            effects=()):
    return bus.BusinessOutput(
        agent_id=bus.AGENT_ID, status="COMPLETE",
        security=SecurityRef(ticker="TEST", exchange="NASDAQ", currency="USD"),
        knowledge_timestamp="2026-01-01T00:00:00Z",
        category=CategoryStats(max_points=20.0, awarded_points=score10 * 2.0,
                               score_10=score10, confidence=80.0),
        verdict=verdict, coverage=1.0, dimensions=dimensions or [], metrics=[],
        moat=bus.MoatSummary(classification=classification,
                             quantitative_evidence=list(effects)),
        moat_gate_inputs=gate or bus.MoatGateInputs(),
    )


_ALL_PASS = bus.MoatGateInputs(
    mechanical_conditions_pass=True, roic_at_least_20pct=True,
    positive_spread=True, fcf_conversion_at_least_0_9=True,
)


def test_a_moat_judgment_can_cross_the_excellent_boundary():
    """The premise the other tests rest on: this judgment is worth enough
    to change the band, so a stale label is a visible error."""
    unjudged = Category(name="business_analysis", max_points=20.0,
                        dimensions=_dims(Value.null(NullState.NOT_SCORABLE, unit="score")))
    judged = Category(name="business_analysis", max_points=20.0,
                      dimensions=_dims(Value.of(10.0, unit="score")))
    assert unjudged.score10() == pytest.approx(7.75)
    assert judged.score10() == pytest.approx(8.3125)
    assert bus.verdict(unjudged.score10()) == "Good business"
    assert bus.verdict(judged.score10()) == "Excellent business"


def test_the_excellent_gate_requires_two_visible_moat_effects():
    """Wide-moat condition 3 is "At least two independent moat effects
    are quantitatively visible" — a count, from `moat.quantitative_
    evidence`. Fewer than two is an unmet condition, and AGENT.md's
    no-speculation rule forbids treating it as satisfied by default."""
    assert _ALL_PASS.excellent_gate_passes(2) is True
    assert _ALL_PASS.excellent_gate_passes(3) is True
    assert _ALL_PASS.excellent_gate_passes(1) is False
    # The unjudged case: an empty evidence list.
    assert _ALL_PASS.excellent_gate_passes(0) is False


@pytest.mark.parametrize("field", [
    "mechanical_conditions_pass", "roic_at_least_20pct",
    "positive_spread", "fcf_conversion_at_least_0_9",
])
def test_every_excellent_condition_is_necessary(field):
    """DECISION_RULES.md lists them conjunctively; none is decorative."""
    kwargs = dict(mechanical_conditions_pass=True, roic_at_least_20pct=True,
                  positive_spread=True, fcf_conversion_at_least_0_9=True)
    kwargs[field] = False
    assert bus.MoatGateInputs(**kwargs).excellent_gate_passes(2) is False


def test_value_destruction_still_caps_the_label():
    """DECISION_RULES.md names "ROIC below WACC" as sufficient for the
    weakest band, whatever else scored well."""
    assert bus.capped_verdict(9.5, value_destruction=True,
                              excellent_gate_passes=True) == "Weak business"


def test_the_verdict_is_recomputed_after_a_judgment_lands():
    """`rescore` leaves `verdict` alone by design. Something has to
    re-derive it once the score has moved."""
    from wbj.deep import _recompute_business_verdict

    # Condition 3 is now the effect count: two visible effects, plus the
    # three other conditions in `_ALL_PASS`, carry the Excellent gate.
    stale = _output(score10=8.3125, verdict="Good business",
                    classification="Wide", gate=_ALL_PASS,
                    effects=["network scale", "cost advantage"])
    assert _recompute_business_verdict(stale).verdict == "Excellent business"


def test_a_narrow_moat_keeps_the_label_capped_at_good():
    """The recomputation must be able to move the label down as well."""
    from wbj.deep import _recompute_business_verdict

    # One visible effect is short of condition 3's "at least two", so
    # the Excellent gate fails and the label caps at Good.
    out = _output(score10=8.3125, verdict="Excellent business",
                  classification="Narrow", gate=_ALL_PASS,
                  effects=["network scale"])
    assert _recompute_business_verdict(out).verdict == "Good business"


def test_recomputation_leaves_a_non_business_output_alone():
    """The hook keys off fields only business carries; every other
    specialist must pass through untouched."""
    from wbj.deep import _recompute_business_verdict

    class _Other:
        verdict = "Strong"

    other = _Other()
    assert _recompute_business_verdict(other) is other


def test_moat_classification_is_written_into_the_schema_field():
    """OUTPUT_SCHEMA.md requires `moat: {classification, ...}`. The
    judgment moved the dimension slot but never the field, so a report
    could show a Wide-moat score beside "NotScorable"."""
    from wbj.deep import _apply_judgments_to_output_fields
    from wbj.judge import Judgment
    from wbj.specialists.common import JudgmentRequest

    out = _output(score10=5.0, verdict="Mediocre / mixed business",
                  classification="NotScorable")
    request = JudgmentRequest(
        request_id="business_analysis:moat_classification", agent_id=bus.AGENT_ID,
        metric_id="moat_classification", question="Wide/Narrow/None?",
        schema_hint="one of Wide|Narrow|None|NotScorable")
    judgment = Judgment(request_id=request.request_id, answer="Wide",
                        evidence_class="C", source="analyst")

    result = _apply_judgments_to_output_fields([("Business", out)],
                                               [judgment], [request])
    assert result[0][1].moat.classification == "Wide"


def test_an_unrecognised_moat_answer_is_refused():
    """A judgment is a declared assumption, not a free-text field: an
    answer outside the documented four must not reach the schema."""
    from wbj.deep import _apply_judgments_to_output_fields
    from wbj.judge import Judgment
    from wbj.specialists.common import JudgmentRequest

    out = _output(score10=5.0, verdict="Mediocre / mixed business",
                  classification="NotScorable")
    request = JudgmentRequest(
        request_id="business_analysis:moat_classification", agent_id=bus.AGENT_ID,
        metric_id="moat_classification", question="Wide/Narrow/None?",
        schema_hint="one of Wide|Narrow|None|NotScorable")
    judgment = Judgment(request_id=request.request_id, answer="very wide indeed",
                        evidence_class="C", source="analyst")

    result = _apply_judgments_to_output_fields([("Business", out)],
                                               [judgment], [request])
    assert result[0][1].moat.classification == "NotScorable"


# --- FORMULAS.md execution rules ------------------------------------------


def test_a_proxy_reduces_model_fit_confidence():
    """FORMULAS.md: "Record any proxy in `warnings` and reduce model-fit
    confidence." Both proxy declarations ended with "Model-fit confidence
    reduced accordingly" and nothing reduced it — `_category_confidence`
    read the industry adapter and nothing else."""
    from types import SimpleNamespace

    packet = SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter="default_nonfinancial"),
        staleness={"quarterly_fundamentals": "FRESH"},
    )
    none = bus._category_confidence(1.0, packet, [])
    one = bus._category_confidence(1.0, packet, [bus.PROXY_REVENUE_CAGR_FOR_SHARE])
    both = bus._category_confidence(1.0, packet,
                                    [bus.PROXY_REVENUE_CAGR_FOR_SHARE,
                                     bus.PROXY_BINARY_CAPITAL_RETURN])
    assert none > one > both


def test_the_proxy_penalty_floors_above_a_replaced_model():
    """A substituted input degrades the fit; it is still a closer read
    than running formulas the methodology says do not apply at all."""
    from types import SimpleNamespace

    packet = SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter="default_nonfinancial"),
        staleness={"quarterly_fundamentals": "FRESH"},
    )
    many = bus._category_confidence(1.0, packet, ["p"] * 20)
    replaced = bus._category_confidence(
        1.0, SimpleNamespace(analysis=SimpleNamespace(industry_adapter="banks"),
                             staleness={"quarterly_fundamentals": "FRESH"}), [])
    assert many >= replaced


def test_source_lineage_names_the_analyst_supplied_inputs():
    """AGENT.md's sequence ends with "Return the required schema and
    complete audit trail". The field was the literal
    `["packet.fundamentals.annual"]`, so every conditional DATASET.md
    input — WACC, segment shares, concentration, retention, guidance —
    was consumed without ever appearing in the trail."""
    from types import SimpleNamespace

    packet = SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter="default_nonfinancial"),
        security=SimpleNamespace(ticker="TEST"),
    )
    bare = bus._source_lineage(packet, {})
    assert bare == ["packet.fundamentals.annual", "packet.analysis", "packet.security"]

    rich = bus._source_lineage(packet, {"wacc": 0.09, "retention": {"begin": 1.0},
                                        "peer_roic": [0.1, 0.2]})
    assert any("overlay.wacc" in x and "wacc_inputs" in x for x in rich)
    assert any("overlay.retention" in x for x in rich)
    # An absent key must not be claimed as a source.
    assert not any("guidance_history" in x for x in rich)


def test_every_overlay_key_business_reads_is_in_the_lineage_map():
    """A key added to `run()` but not to `_OVERLAY_LINEAGE` would be read
    without ever being disclosed — the exact failure this fixes."""
    import re
    from pathlib import Path

    src = Path(bus.__file__).read_text(encoding="utf-8")
    # `(overlay or {}).get("...")` reads the overlay just as `overlay.get(...)`
    # does, and the narrower pattern missed it — `tam_source_tier`, which
    # decides whether the competitive cap holds at 8 or lifts to 10, was read
    # for exactly that reason without ever reaching `source_lineage`.
    read = set(re.findall(r'overlay(?: or \{\})?\)?\.get\("([a-z_]+)"', src))
    # Not data inputs: metadata about the analyst's inputs rather than
    # DATASET.md fields. `analyst_input_warnings` carries load warnings;
    # `period` is the period label the analyst states for their snapshot
    # metrics, which qualifies the data rather than being a field of it.
    read -= {"analyst_input_warnings", "period"}
    assert read <= set(bus._OVERLAY_LINEAGE), read - set(bus._OVERLAY_LINEAGE)
