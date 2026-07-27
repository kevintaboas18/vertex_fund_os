"""moat.quantitative_evidence, the schema field nothing filled.

OUTPUT_SCHEMA.md's `moat` block declares `quantitative_evidence`, and the
agent asks for it — the `moat_quantitative_effects_count` judgment
request. But the answer landed nowhere: the routing in
`_apply_judgments_to_output_fields` handled the classification and stopped,
so the field stayed `[]` on every security however the judge answered.

Its vocabulary is DECISION_RULES.md's wide-moat condition 3, verbatim —
the five independent moat effects. Answers are filtered to those five,
the same discipline the classification path uses.
"""

from wbj.deep import (
    _apply_judgments_to_output_fields,
    _coerce_moat_effects,
    _VALID_MOAT_EFFECTS,
)
from wbj.judge import Judgment
from wbj.specialists import business as bus
from wbj.specialists.common import CategoryStats, JudgmentRequest, SecurityRef


EFFECTS_REQUEST = JudgmentRequest(
    request_id="business_analysis:moat_quantitative_effects_count",
    agent_id=bus.AGENT_ID, metric_id="moat_quantitative_effects_count",
    question="?", schema_hint="array of 0-5 strings")

CLASS_REQUEST = JudgmentRequest(
    request_id="business_analysis:moat_classification",
    agent_id=bus.AGENT_ID, metric_id="moat_classification",
    question="?", schema_hint="Wide|Narrow|None|NotScorable")


def _output():
    return bus.BusinessOutput(
        agent_id=bus.AGENT_ID, status="COMPLETE",
        security=SecurityRef(ticker="T", exchange="X", currency="USD"),
        knowledge_timestamp="2026-01-01T00:00:00Z",
        category=CategoryStats(max_points=20.0, awarded_points=10.0,
                               score_10=5.0, confidence=80.0),
        verdict="Mediocre / mixed business", coverage=1.0,
        dimensions=[], metrics=[])


def _evidence_after(answer):
    j = Judgment(request_id=EFFECTS_REQUEST.request_id, answer=answer,
                 evidence_class="C", source="analyst")
    result = _apply_judgments_to_output_fields(
        [("Business", _output())], [j], [EFFECTS_REQUEST])
    return result[0][1].moat.quantitative_evidence


# --- the field is filled ---------------------------------------------------


def test_the_field_starts_empty():
    """The condition the fix addresses."""
    assert _output().moat.quantitative_evidence == []


def test_a_moat_effects_answer_reaches_the_schema_field():
    assert _evidence_after({"items": ["network scale", "cost advantage"]}) == [
        "cost advantage", "network scale"]


def test_the_answer_is_kept_in_victors_order():
    """His list is retention/switching costs, cost advantage, network
    scale, regulated/intangible protection, efficient scale. The output
    reads in that order however the answer was given."""
    scrambled = {"items": ["efficient scale", "cost advantage",
                           "retention/switching costs"]}
    assert _evidence_after(scrambled) == [
        "retention/switching costs", "cost advantage", "efficient scale"]


# --- filtered to his five, like the classification path --------------------


def test_an_off_list_phrase_is_dropped():
    assert _evidence_after({"items": ["a strong brand"]}) == []
    assert _evidence_after({"items": ["network scale", "a strong brand"]}) == [
        "network scale"]


def test_matching_is_case_and_whitespace_insensitive():
    assert _evidence_after({"items": ["  Network Scale  "]}) == ["network scale"]


def test_repeats_collapse():
    assert _evidence_after({"items": ["cost advantage", "cost advantage"]}) == [
        "cost advantage"]


def test_an_empty_or_non_list_answer_leaves_the_field_empty():
    assert _evidence_after({"items": []}) == []
    assert _evidence_after("network scale") == []


def test_the_vocabulary_is_the_five_categories():
    assert set(_VALID_MOAT_EFFECTS) == {
        "retention/switching costs", "cost advantage", "network scale",
        "regulated/intangible protection", "efficient scale"}
    assert len(_VALID_MOAT_EFFECTS) == 5


def test_coerce_returns_none_when_nothing_matches():
    """None, not [] — the caller uses it to decide whether to touch the
    field at all, so a rejected answer must not overwrite an existing
    one with a blank."""
    assert _coerce_moat_effects(["off-list"]) is None
    assert _coerce_moat_effects([]) is None
    assert _coerce_moat_effects("network scale") is None
    assert _coerce_moat_effects(["network scale"]) == ["network scale"]


# --- it does not disturb the classification --------------------------------


def test_classification_and_evidence_are_written_together():
    j_class = Judgment(request_id=CLASS_REQUEST.request_id, answer="Wide",
                       evidence_class="C", source="analyst")
    j_eff = Judgment(request_id=EFFECTS_REQUEST.request_id,
                     answer={"items": ["network scale", "efficient scale"]},
                     evidence_class="C", source="analyst")
    result = _apply_judgments_to_output_fields(
        [("Business", _output())], [j_class, j_eff],
        [CLASS_REQUEST, EFFECTS_REQUEST])
    moat = result[0][1].moat
    assert moat.classification == "Wide"
    assert moat.quantitative_evidence == ["network scale", "efficient scale"]


def test_a_classification_alone_still_works():
    """The path this fix touched must not have broken the case that
    already worked."""
    j = Judgment(request_id=CLASS_REQUEST.request_id, answer="Narrow",
                 evidence_class="C", source="analyst")
    result = _apply_judgments_to_output_fields(
        [("Business", _output())], [j], [CLASS_REQUEST])
    assert result[0][1].moat.classification == "Narrow"
    assert result[0][1].moat.quantitative_evidence == []


def test_no_moat_judgment_leaves_the_block_untouched():
    unrelated = JudgmentRequest(
        request_id="business_analysis:business_in_one_sentence",
        agent_id=bus.AGENT_ID, metric_id="business_in_one_sentence",
        question="?", schema_hint="string")
    j = Judgment(request_id=unrelated.request_id, answer="A sentence.",
                 evidence_class="C", source="analyst")
    result = _apply_judgments_to_output_fields(
        [("Business", _output())], [j], [unrelated])
    moat = result[0][1].moat
    assert moat.classification == "NotScorable"
    assert moat.quantitative_evidence == []
