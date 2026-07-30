"""Tests for `wbj.overlay.merge` (Task 20): `collect_requests`,
`schema_hint_ok`, and `merge_overlay`.

Uses the REAL `schema_hint` strings and the REAL `NOT_SCORABLE`-placeholder
confidence (0.0) the 6 specialists emit -- not invented ones -- plus
end-to-end tests that run a specialist's real `run()` on the NVDA fixture,
answer its actual judgment requests, and assert category points and
coverage both increase (the metric_id -> `judgment_slots` -> dimension-slot
wiring, not just the flat metrics row): financial's `FIN-GR-004`/`FIN-GR-005`,
business's `moat_classification`, and market's `tam_source_tier_assignment`.

`risk_analysis` is deliberately NOT covered by an end-to-end test here:
both of its JudgmentRequests (`thesis_killers`, `regulatory_legal_exposure`)
use "array of ..." schema_hints, whose answers are `{"items": [...]}` dicts
-- `merge.py`'s own documented rule 3 never reduces a dict answer to a
score, so neither request can ever move a dimension slot regardless of how
`risk.py` registers its `judgment_slots`. Forcing a slot for either would be
the "fake slot" the wiring brief says to avoid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension
from wbj.overlay.merge import (
    UnknownJudgmentRequestError,
    collect_requests,
    merge_overlay,
    schema_hint_ok,
)
from wbj.schemas.overlay import Judgment
from wbj.schemas.packet import Packet
import wbj.specialists.business as bus
import wbj.specialists.financial as fin
import wbj.specialists.market as mkt
from wbj.specialists.common import (
    CategoryStats,
    JudgmentRequest,
    MetricRow,
    SecurityRef,
    SpecialistOutput,
    status_from_coverage,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "packet" / "NVDA_packet.json"

# Real schema_hints emitted by the specialists (grep 'schema_hint' in wbj/specialists/).
HINT_ENUM_BUSINESS = "one of Wide|Narrow|None"       # business moat (first=best)
HINT_ENUM_FINANCIAL = "one of BAD|GOOD|EXCELLENT"     # financial FIN-GR-004/005 (last=best)
HINT_DICT_MARKET = "{probability: 0-1, impact: usd, evidence_quality: 0-1}"  # market catalyst
HINT_INT_MARKET = "integer 1-5"                       # market TAM tier
HINT_ARRAY_RISK = "array of {event, impact, probability_assumption}"          # risk regulatory
HINT_ARRAY_BUSINESS = "array of exactly 3 strings"    # business/market thesis killers


def _security() -> SecurityRef:
    return SecurityRef(ticker="NVDA", exchange="NASDAQ", currency="USD")


# ---------------------------------------------------------------------------
# schema_hint_ok: every REAL hint shape accepts its correct answer and
# rejects a wrong-typed one. HINT_DICT_MARKET is the regression case -- its
# "probability"/"0-1" tokens must NOT force a numeric answer (CRITICAL 1).
# ---------------------------------------------------------------------------


def test_schema_hint_ok_enum_accepts_matching_string():
    assert schema_hint_ok(HINT_ENUM_BUSINESS, "Wide")
    assert schema_hint_ok(HINT_ENUM_FINANCIAL, "EXCELLENT")
    assert not schema_hint_ok(HINT_ENUM_BUSINESS, "Medium")
    assert not schema_hint_ok(HINT_ENUM_FINANCIAL, 5.0)


def test_schema_hint_ok_dict_hint_accepts_dict_not_number():
    # regression: "{probability: 0-1, impact: usd, evidence_quality: 0-1}"
    # must validate a dict answer, and must NOT accept a bare number just
    # because "probability"/"0-1" appear inside the braces.
    assert schema_hint_ok(HINT_DICT_MARKET, {"probability": 0.6, "impact": 5e8, "evidence_quality": 0.7})
    assert not schema_hint_ok(HINT_DICT_MARKET, 0.6)
    assert not schema_hint_ok(HINT_DICT_MARKET, "high")


def test_schema_hint_ok_integer_hint_accepts_integer():
    assert schema_hint_ok(HINT_INT_MARKET, 3)
    assert not schema_hint_ok(HINT_INT_MARKET, "3")
    assert not schema_hint_ok(HINT_INT_MARKET, True)  # bool is not a real numeric answer


def test_schema_hint_ok_array_hint_accepts_items_list():
    assert schema_hint_ok(HINT_ARRAY_RISK, {"items": [{"event": "antitrust"}]})
    assert schema_hint_ok(HINT_ARRAY_BUSINESS, {"items": ["a", "b", "c"]})
    assert not schema_hint_ok(HINT_ARRAY_RISK, "not an array")
    assert not schema_hint_ok(HINT_ARRAY_BUSINESS, {"foo": []})


# ---------------------------------------------------------------------------
# Small hand-built fixtures for merge_overlay unit behavior. Confidence 0.0
# on the placeholder rows mirrors financial.py's real `_confidence_for` on a
# NOT_SCORABLE value (IMPORTANT 1).
# ---------------------------------------------------------------------------

BIZ_REQ = "business_analysis:moat_classification"
FIN_REQ = "financial_analysis:FIN-GR-004"


def _financial_like_output() -> SpecialistOutput:
    """A financial-shaped output with a DIM_REVENUE-like dimension whose
    slot 1 (weight 0.5) is a NOT_SCORABLE judgment slot for FIN-GR-004,
    wired via judgment_slots. Pre-merge valid weight 0.5/1.0 = 0.5 < 0.70,
    so the dimension is NOT_SCORABLE; answering FIN-GR-004 pushes it to 1.0
    -> scorable -> points and coverage both rise."""
    dim = Dimension(
        name="revenue_quality_and_growth",
        max_points=3.0,
        metric_scores=[
            (0.5, Value.of(6.0, unit="score")),
            (0.5, Value.null(NullState.NOT_SCORABLE, unit="score")),
        ],
    )
    cat = Category(name="financial_analysis", max_points=15.0, dimensions=[dim])
    metrics = [
        MetricRow(
            metric_id="FIN-GR-004",
            state=NullState.NOT_SCORABLE,
            unit="ratio",
            formula_id="FIN-GR-004",
            formula_version="2.0.0",
            score="NOT_SCORABLE",
            confidence=0.0,  # real placeholder: _confidence_for on a null Value
        )
    ]
    return SpecialistOutput(
        agent_id="financial_analysis",
        status=status_from_coverage(cat.coverage()),
        security=_security(),
        knowledge_timestamp="2026-07-16T21:00:00+00:00",
        category=CategoryStats(
            max_points=15.0, awarded_points=cat.points(), score_10=cat.score10(), confidence=80.0
        ),
        coverage=cat.coverage(),
        dimensions=[dim],
        metrics=metrics,
        judgment_requests=[
            JudgmentRequest(
                request_id=FIN_REQ,
                agent_id="financial_analysis",
                metric_id="FIN-GR-004",
                question="Classify organic growth quality.",
                schema_hint=HINT_ENUM_FINANCIAL,
            )
        ],
        judgment_slots={"FIN-GR-004": ("revenue_quality_and_growth", 1)},
    )


def _business_context_only_output() -> SpecialistOutput:
    """A business-shaped output whose moat_classification judgment has NO
    judgment_slots entry (business doesn't wire moat to a dimension slot) --
    so answering it updates the flat row only, never dimension math."""
    dim = Dimension(name="MOAT", max_points=5.0, metric_scores=[(1.0, Value.of(6.0, unit="score"))])
    cat = Category(name="business_analysis", max_points=20.0, dimensions=[dim])
    metrics = [
        MetricRow(
            metric_id="moat_classification",
            state=NullState.NOT_SCORABLE,
            unit="score",
            formula_id="moat_classification",
            formula_version="2.0.0",
            score="NOT_SCORABLE",
            confidence=0.0,
        )
    ]
    return SpecialistOutput(
        agent_id="business_analysis",
        status=status_from_coverage(cat.coverage()),
        security=_security(),
        knowledge_timestamp="2026-07-16T21:00:00+00:00",
        category=CategoryStats(
            max_points=20.0, awarded_points=cat.points(), score_10=cat.score10(), confidence=80.0
        ),
        coverage=cat.coverage(),
        dimensions=[dim],
        metrics=metrics,
        judgment_requests=[
            JudgmentRequest(
                request_id=BIZ_REQ,
                agent_id="business_analysis",
                metric_id="moat_classification",
                question="Classify the moat.",
                schema_hint=HINT_ENUM_BUSINESS,
            )
        ],
        # no judgment_slots entry for moat_classification
    )


# --- collect_requests ------------------------------------------------------


def test_collect_requests_gathers_across_all_outputs_in_order():
    outputs = [_business_context_only_output(), _financial_like_output()]
    assert [r.request_id for r in collect_requests(outputs)] == [BIZ_REQ, FIN_REQ]


def test_collect_requests_empty_when_none():
    out = _financial_like_output().model_copy(update={"judgment_requests": []})
    assert collect_requests([out]) == []


# --- merge_overlay: enum answer scored via ladder, moves dimension math ----


def test_merge_overlay_financial_enum_scores_and_increases_points_and_coverage():
    before = _financial_like_output()
    assert before.coverage == pytest.approx(0.5)
    assert before.category.awarded_points == pytest.approx(0.0)  # dim NOT_SCORABLE -> 0 pts

    judgment = Judgment(
        request_id=FIN_REQ,
        answer="EXCELLENT",  # last of BAD|GOOD|EXCELLENT -> best -> 10.0 (NOT positional first=best)
        evidence_class=EvidenceClass.E,
        source="claude-sub-agent:financial",
        rationale="Organic bridge clean; no acquisitions or FX distortion.",
    )
    after = merge_overlay([before], [judgment])[0]

    assert after is not before
    assert after.coverage == pytest.approx(1.0)
    assert after.coverage > before.coverage
    # dim now scorable: weighted mean 0.5*6 + 0.5*10 = 8.0 -> 3.0 * 8/10 = 2.4
    assert after.category.awarded_points == pytest.approx(2.4)
    assert after.category.awarded_points > before.category.awarded_points

    row = next(r for r in after.metrics if r.metric_id == "FIN-GR-004")
    assert row.state is None
    assert row.value == pytest.approx(10.0)  # EXCELLENT via the documented ladder table
    assert row.confidence == pytest.approx(65.0)  # from evidence_class E, NOT the stale 0.0
    assert row.evidence_class == EvidenceClass.E
    assert any("JUDGMENT_SCORE_FROM_ENUM_LADDER" in w for w in row.warnings)

    # input untouched (frozen models)
    assert before.metrics[0].confidence == pytest.approx(0.0)
    assert before.metrics[0].state == NullState.NOT_SCORABLE


def test_only_a_cerebro_defined_ladder_converts_to_a_score():
    """DATA_POLICY.md: "`Q` - qualitative evidence that is not scored
    unless a conversion rule exists." A ladder earns its place in
    `_ENUM_SCORE_TABLE` by citing the document that defines it.

    Financial's does: `02_financial_analysis/DECISION_RULES.md` states
    "BAD = 0 points / GOOD = 1 point / EXCELLENT = 2 points", which is
    0/5/10 on the 0-10 scale. Business's Wide/Narrow/None -> 10/5/0 had
    no such rule anywhere in Cerebro; it existed only in this file, and
    it scored a quarter of the moat dimension."""
    from wbj.overlay.merge import _ENUM_SCORE_AUTHORITY, _ENUM_SCORE_TABLE

    assert frozenset({"BAD", "GOOD", "EXCELLENT"}) in _ENUM_SCORE_TABLE
    assert frozenset({"Wide", "Narrow", "None"}) not in _ENUM_SCORE_TABLE

    # Every authorised ladder names the rule that authorises it.
    assert set(_ENUM_SCORE_TABLE) == set(_ENUM_SCORE_AUTHORITY)
    for options, authority in _ENUM_SCORE_AUTHORITY.items():
        assert ".md" in authority, sorted(options)


def test_a_moat_answer_is_recorded_as_context_and_not_as_points():
    """The answer is still evidence: it fills OUTPUT_SCHEMA.md's required
    `moat.classification` and feeds DECISION_RULES.md's wide-moat gate.
    What it no longer does is become points."""
    before = _business_context_only_output()
    judgment = Judgment(
        request_id=BIZ_REQ, answer="Wide", evidence_class=EvidenceClass.E, source="analyst"
    )
    after = merge_overlay([before], [judgment])[0]
    row = next(r for r in after.metrics if r.metric_id == "moat_classification")
    assert row.score == "NOT_SCORABLE"
    assert any("Wide" in a for a in after.assumptions), "kept as context"


def test_merge_overlay_context_only_when_no_dimension_slot():
    """moat_classification has no judgment_slots entry -> flat row updates,
    but dimension points/coverage are untouched (correctly, nothing to move)."""
    before = _business_context_only_output()
    judgment = Judgment(
        request_id=BIZ_REQ, answer="Wide", evidence_class=EvidenceClass.E, source="analyst"
    )
    after = merge_overlay([before], [judgment])[0]
    assert after.coverage == pytest.approx(before.coverage)
    assert after.category.awarded_points == pytest.approx(before.category.awarded_points)
    row = next(r for r in after.metrics if r.metric_id == "moat_classification")
    # Recorded, not scored: Cerebro defines no Wide/Narrow/None conversion.
    assert row.score == "NOT_SCORABLE"


# --- merge_overlay: confidence reflects evidence class ----------------------


@pytest.mark.parametrize(
    "ec,expected",
    [
        (EvidenceClass.R, 90.0),
        (EvidenceClass.C, 80.0),
        (EvidenceClass.E, 65.0),
        (EvidenceClass.A, 45.0),
        (EvidenceClass.Q, 30.0),
    ],
)
def test_merge_overlay_confidence_from_evidence_class(ec, expected):
    before = _financial_like_output()
    judgment = Judgment(request_id=FIN_REQ, answer="GOOD", evidence_class=ec, source="analyst")
    after = merge_overlay([before], [judgment])[0]
    row = next(r for r in after.metrics if r.metric_id == "FIN-GR-004")
    assert row.confidence == pytest.approx(expected)
    assert row.confidence > 0.0  # never inherits the placeholder's 0.0


# --- merge_overlay: numeric answer ------------------------------------------


def test_merge_overlay_numeric_answer_for_enum_hint_soft_rejected():
    before = _financial_like_output()
    j = Judgment(request_id=FIN_REQ, answer=15.0, evidence_class=EvidenceClass.E, source="a")
    # numeric answer for an enum hint fails schema_hint_ok -> soft-rejected
    assert merge_overlay([before], [j])[0] is before


def test_merge_overlay_numeric_answer_clamped_via_generic_numeric_hint():
    """A genuinely scalar-numeric hint accepts and clamps a numeric answer."""
    req = JudgmentRequest(
        request_id="x", agent_id="a", metric_id="m", question="q", schema_hint="float 0-10"
    )
    out = _financial_like_output().model_copy(
        update={"judgment_requests": [req], "judgment_slots": {}, "metrics": []}
    )
    after = merge_overlay([out], [Judgment(request_id="x", answer=15.0, evidence_class=EvidenceClass.C, source="a")])[0]
    row = next(r for r in after.metrics if r.metric_id == "m")
    assert row.value == pytest.approx(10.0)


# --- merge_overlay: dict answer stays context-only --------------------------


def test_merge_overlay_dict_answer_context_only():
    req = JudgmentRequest(
        request_id="market_analysis:catalyst_0",
        agent_id="market_analysis",
        metric_id="catalyst_0_probability_impact_evidence",
        question="Assess catalyst.",
        schema_hint=HINT_DICT_MARKET,
    )
    dim = Dimension(name="CATALYST", max_points=5.0, metric_scores=[(1.0, Value.of(5.0, unit="score"))])
    cat = Category(name="market_analysis", max_points=20.0, dimensions=[dim])
    out = SpecialistOutput(
        agent_id="market_analysis",
        status=status_from_coverage(cat.coverage()),
        security=_security(),
        knowledge_timestamp="2026-07-16T21:00:00+00:00",
        category=CategoryStats(max_points=20.0, awarded_points=cat.points(), score_10=cat.score10(), confidence=70.0),
        coverage=cat.coverage(),
        dimensions=[dim],
        metrics=[],
        judgment_requests=[req],
    )
    judgment = Judgment(
        request_id="market_analysis:catalyst_0",
        answer={"probability": 0.6, "impact": 5e8, "evidence_quality": 0.7},
        evidence_class=EvidenceClass.A,
        source="analyst",
    )
    after = merge_overlay([out], [judgment])[0]
    assert after.category.awarded_points == pytest.approx(out.category.awarded_points)  # untouched
    row = next(r for r in after.metrics if r.metric_id == "catalyst_0_probability_impact_evidence")
    assert row.state == NullState.NOT_SCORABLE  # dict answer never scored
    assert row.evidence_class == EvidenceClass.A  # but recorded as context
    assert any("NOT_REDUCIBLE_TO_SCORE" in w for w in row.warnings)


# --- merge_overlay: rejections ----------------------------------------------


def test_merge_overlay_unknown_request_id_raises():
    before = _financial_like_output()
    j = Judgment(request_id="financial_analysis:nope", answer="GOOD", evidence_class=EvidenceClass.E, source="a")
    with pytest.raises(UnknownJudgmentRequestError):
        merge_overlay([before], [j])


def test_merge_overlay_missing_evidence_class_rejected_silently():
    before = _financial_like_output()
    j = Judgment(request_id=FIN_REQ, answer="GOOD", evidence_class=None, source="a")
    assert merge_overlay([before], [j])[0] is before


def test_merge_overlay_missing_source_rejected_silently():
    before = _financial_like_output()
    j = Judgment(request_id=FIN_REQ, answer="GOOD", evidence_class=EvidenceClass.E, source="")
    assert merge_overlay([before], [j])[0] is before


def test_merge_overlay_schema_mismatch_rejected_silently():
    before = _financial_like_output()
    j = Judgment(request_id=FIN_REQ, answer="Medium", evidence_class=EvidenceClass.E, source="a")
    assert merge_overlay([before], [j])[0] is before


# --- merge_overlay: routing / batching --------------------------------------


def test_merge_overlay_only_touches_matching_output():
    biz, financ = _business_context_only_output(), _financial_like_output()
    j = Judgment(request_id=FIN_REQ, answer="EXCELLENT", evidence_class=EvidenceClass.E, source="a")
    after = merge_overlay([biz, financ], [j])
    assert after[0] is biz
    assert after[1] is not financ
    assert after[1].coverage == pytest.approx(1.0)


def test_merge_overlay_empty_judgments_unchanged():
    before = [_business_context_only_output(), _financial_like_output()]
    after = merge_overlay(before, [])
    assert after[0] is before[0] and after[1] is before[1]


# ---------------------------------------------------------------------------
# END-TO-END: real financial.run on the NVDA fixture -> collect -> answer the
# actual FIN-GR-004/FIN-GR-005 requests -> merge -> points AND coverage rise.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nvda_packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def test_end_to_end_financial_run_answer_judgments_moves_points_and_coverage(nvda_packet):
    before = fin.run(nvda_packet)

    # Sanity: the real specialist emits FIN-GR-004/005 as judgment requests,
    # wired to revenue_quality_and_growth slots via judgment_slots.
    reqs = collect_requests([before])
    req_ids = {r.request_id for r in reqs}
    assert "financial_analysis:FIN-GR-004" in req_ids
    assert "financial_analysis:FIN-GR-005" in req_ids
    assert before.judgment_slots["FIN-GR-004"][0] == "revenue_quality_and_growth"

    judgments = [
        Judgment(
            request_id=r.request_id,
            answer="EXCELLENT",
            evidence_class=EvidenceClass.E,
            source="claude-sub-agent:financial",
            rationale="answered from the organic/market-share bridge",
        )
        for r in reqs
        if r.metric_id in ("FIN-GR-004", "FIN-GR-005")
    ]
    after = merge_overlay([before], judgments)[0]

    assert after.category.awarded_points > before.category.awarded_points
    assert after.coverage > before.coverage

    for mid in ("FIN-GR-004", "FIN-GR-005"):
        row = next(r for r in after.metrics if r.metric_id == mid)
        assert row.state is None
        assert row.value == pytest.approx(10.0)
        assert row.confidence == pytest.approx(65.0)

    # inherited contract still holds: category points reproduce from dimensions
    recomputed = Category(
        name=after.agent_id, max_points=after.category.max_points, dimensions=after.dimensions
    ).points()
    assert after.category.awarded_points == pytest.approx(recomputed)


def test_end_to_end_a_moat_judgment_does_not_move_business_points(nvda_packet):
    """`moat_classification` holds no dimension slot at all, and the
    category total is unchanged however the question is answered.

    DATA_POLICY.md scores `Q` evidence "unless a conversion rule exists",
    and Cerebro defines no conversion from Wide/Narrow/None to a 0-10
    score. The answer is kept as context — it fills the schema's moat
    block and feeds the wide-moat gate — but the category total is
    unchanged, which is CLAUDE.md's own rule: "jamas se convierte en
    score salvo que una regla del Cerebro lo defina explicitamente".
    """
    before = bus.run(nvda_packet, overlay={"wacc": 0.09})

    reqs = collect_requests([before])
    assert "business_analysis:moat_classification" in {r.request_id for r in reqs}
    # No dimension slot: SCORING.md's moat row lists three inputs and the
    # classification is not one of them.
    assert "moat_classification" not in before.judgment_slots

    judgments = [
        Judgment(
            request_id=r.request_id,
            answer="Wide",
            evidence_class=EvidenceClass.E,
            source="claude-sub-agent:business",
            rationale="persistent spread, stable margins, no unresolved concentration",
        )
        for r in reqs
        if r.metric_id == "moat_classification"
    ]
    after = merge_overlay([before], judgments)[0]

    assert after.category.awarded_points == pytest.approx(before.category.awarded_points)
    assert after.coverage == pytest.approx(before.coverage)

    row = next(r for r in after.metrics if r.metric_id == "moat_classification")
    assert row.score == "NOT_SCORABLE"
    assert any("Wide" in a for a in after.assumptions), "the answer is kept as context"

    recomputed = Category(
        name=after.agent_id, max_points=after.category.max_points, dimensions=after.dimensions
    ).points()
    assert after.category.awarded_points == pytest.approx(recomputed)


def test_a_tam_source_tier_is_recorded_as_context_and_never_scored(nvda_packet):
    """A tier is not a score.

    `03_market_analysis/DECISION_RULES.md` maps the tier to a *confidence
    component* -- tier 1 (government/audited) 100, tier 4 (issuer estimate)
    45, tier 5 (unattributed) "0; not scorable" -- which is exactly what
    `TAM_TIER_CONFIDENCE` already holds.

    The judgment used to fill a weighted slot in the TAM dimension, and
    merge's numeric path turned the answer straight into a 0-10 score. That
    ladder was inverted and measured the wrong quantity: tier 1, the best
    source Victor recognises, scored 1.0, while tier 5 -- the one he calls
    not scorable -- scored 5.0. The test that stood here asserted the answer
    "moves points and coverage", pinning the defect in place; its own
    docstring conceded the ladder ran "not through TAM_TIER_CONFIDENCE".

    DATA_POLICY.md settles it: "`Q` - qualitative evidence that is not scored
    unless a conversion rule exists." No rule converts a tier into points.
    """
    overlay = {
        "tam": 100_000.0,
        "tam_history": [100_000.0, 100_000.0],
        "company_relevant_revenue": 50_000.0,
        "share": {"company_sales": 0.0, "total_market_sales": 10_000.0},
        "share_history": [0.0, 0.0],
        "adoption": {"current_units": 5_000.0, "eventual_units": 10_000.0},
    }
    before = mkt.run(nvda_packet, overlay=overlay)

    reqs = collect_requests([before])
    assert "market_analysis:tam_source_tier_assignment" in {r.request_id for r in reqs}, (
        "the tier is still worth asking for -- it feeds confidence"
    )
    assert "tam_source_tier_assignment" not in before.judgment_slots, (
        "a tier must not hold a scored dimension slot"
    )

    judgments = [
        Judgment(request_id=r.request_id, answer="5", evidence_class=EvidenceClass.E,
                 source="claude-sub-agent:market", rationale="unattributed claim")
        for r in reqs if r.metric_id == "tam_source_tier_assignment"
    ]
    after = merge_overlay([before], judgments)[0]

    # Victor's worst tier must not raise the score by a single point.
    assert after.category.awarded_points == pytest.approx(before.category.awarded_points)

    row = next(r for r in after.metrics if r.metric_id == "tam_source_tier_assignment")
    assert row.value is None, "a tier reached the score after all"
    assert any("5" in (w or "") for w in (row.warnings or [])), "the answer must still be recorded"


def test_victors_tier_table_is_the_one_the_engine_holds():
    """Tier -> confidence, straight out of DECISION_RULES.md's table."""
    assert mkt.TAM_TIER_CONFIDENCE == {1: 100.0, 2: 85.0, 3: 70.0, 4: 45.0, 5: 0.0}
