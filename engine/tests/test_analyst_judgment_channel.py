"""An analyst's answer must reach the same slot the judge's does.

`_analyst_judgments` exists so the qualitative slots stay reachable with no
API budget: DECISION_RULES.md leaves `moat_classification` to a reader, and an
answer is a declared assumption whoever makes it. Both paths land in the same
`Judgment` with evidence class Q.

They did not behave the same. `Judgment.answer` is `float | str | dict` and
never a bare list -- the judge's `_coerce_answer` knows that and wraps an
array-shaped answer into `{"items": [...]}`, while the analyst path passed the
value through raw. A list therefore failed validation and was swallowed by a
bare `except`, leaving a log line and an empty metric.

It failed for exactly the answers most worth writing by hand. `thesis_killers`
asks for "array of >=3 {risk, probability_assumption, impact,
early_warning_metric, trigger_level, time_horizon, mitigant}" -- Victor's own
seven-field structure -- so an analyst who wrote precisely what was asked got
nothing, while the identical structure from the judge went through.
"""

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import EvidenceClass
from wbj.deep import _analyst_judgments, _shape_answer
from wbj.schemas.packet import Packet
from wbj.specialists import business as biz
from wbj.specialists import risk as rsk

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

_KILLER = {"risk": "key-customer loss", "probability_assumption": 0.15,
           "impact": "high", "early_warning_metric": "BUS-CONC-003",
           "trigger_level": 0.35, "time_horizon": "12m",
           "mitigant": "diversification"}


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def test_a_list_answer_is_wrapped_like_the_judge_wraps_it():
    """`_coerce_answer`'s array branch produces `{"items": [...]}`; the
    analyst path has to produce the same shape or the model rejects it."""
    assert _shape_answer([1, 2, 3], "array of exactly 3 strings") == {"items": [1, 2, 3]}


def test_scalars_pass_through_untouched():
    """The judge coerces strings because its answers arrive as text. An
    analyst's do not."""
    assert _shape_answer("Wide", "one of Wide|Narrow|None") == "Wide"
    assert _shape_answer(0.22, "float") == 0.22


def test_victors_seven_field_thesis_killers_are_accepted(packet):
    """The structure DECISION_RULES.md specifies, written by hand."""
    out = rsk.run(packet, {})
    answers = _analyst_judgments({"judgments": {"thesis_killers": [_KILLER] * 3}},
                                 out.judgment_requests)
    assert len(answers) == 1
    assert answers[0].request_id == "risk_analysis:thesis_killers"


def test_an_analyst_answer_is_evidence_class_q_and_says_who_supplied_it(packet):
    """Same class as the judge's; the source is what distinguishes them, and
    the audit trail needs to carry that."""
    out = rsk.run(packet, {})
    answer = _analyst_judgments({"judgments": {"thesis_killers": [_KILLER] * 3}},
                                out.judgment_requests)[0]
    assert answer.evidence_class is EvidenceClass.Q
    assert answer.source == "analyst-input"
    assert "Entradas" in answer.rationale


def test_both_scalar_and_list_slots_answer_together(packet):
    """A realistic file mixes them: a Wide/Narrow/None call and a list."""
    out = biz.run(packet, {})
    answers = _analyst_judgments(
        {"judgments": {"moat_classification": "Wide",
                       "three_thesis_killers": ["a", "b", "c"]}},
        out.judgment_requests)
    by_metric = {j.request_id.split(":")[-1]: j.answer for j in answers}
    assert by_metric["moat_classification"] == "Wide"
    assert by_metric["three_thesis_killers"] == {"items": ["a", "b", "c"]}


def test_an_unanswered_slot_is_left_for_the_judge(packet):
    """Partial answers are the normal case: what the analyst wrote is removed
    from the queue and the rest still goes to the API."""
    out = biz.run(packet, {})
    answered = {j.request_id for j in _analyst_judgments(
        {"judgments": {"moat_classification": "Wide"}}, out.judgment_requests)}
    remaining = [r for r in out.judgment_requests if r.request_id not in answered]
    assert answered
    assert remaining, "the other slots must still reach the judge"


def test_no_judgments_block_answers_nothing(packet):
    out = rsk.run(packet, {})
    assert _analyst_judgments({}, out.judgment_requests) == []
    assert _analyst_judgments({"judgments": {}}, out.judgment_requests) == []


# --- Victor's bar: reproducible OR challengeable ---------------------------

def test_an_answer_may_carry_its_reasoning_and_evidence(packet):
    """INSTITUTIONAL_VALUATION_ENGINE.md: "the most important outputs ... are
    the assumptions, sensitivities, invalidation conditions, and evidence that
    allow another analyst to reproduce or challenge the result."

    The judge already met that -- it records what it cited and why. This
    channel recorded a role ("analyst-input") and a file path, so a
    hand-written "Wide" arrived with nothing to argue with."""
    out = biz.run(packet, {})
    answers = _analyst_judgments({"judgments": {"moat_classification": {
        "answer": "Wide",
        "rationale": "switching costs visible in 92% services attach rate",
        "source": "10-K FY2025 Item 1, services disclosure",
    }}}, out.judgment_requests)
    assert len(answers) == 1
    assert answers[0].answer == "Wide"
    assert "switching costs" in answers[0].rationale
    assert answers[0].source == "10-K FY2025 Item 1, services disclosure"


def test_a_bare_answer_still_works(packet):
    """The envelope is optional; the simple case must not get harder."""
    out = biz.run(packet, {})
    answer = _analyst_judgments(
        {"judgments": {"moat_classification": "Narrow"}}, out.judgment_requests)[0]
    assert answer.answer == "Narrow"
    assert answer.source == "analyst-input"


def test_a_dict_answer_is_not_mistaken_for_an_envelope(packet):
    """Some answers ARE dicts -- a catalyst is {probability, impact,
    evidence_quality}. Only a dict carrying an `answer` key is an envelope."""
    from wbj.deep import _shape_answer as _s
    payload = {"probability": 0.7, "impact": 5e9, "evidence_quality": 0.6}
    assert _s(payload, "{probability: 0-1, impact: usd}") == payload


def test_an_enveloped_list_answer_is_still_wrapped(packet):
    """Both features have to compose: reasoning on a seven-field list."""
    out = rsk.run(packet, {})
    answer = _analyst_judgments({"judgments": {"thesis_killers": {
        "answer": [_KILLER] * 3,
        "rationale": "ranked by trigger proximity",
        "source": "portfolio review 2026-07",
    }}}, out.judgment_requests)[0]
    assert answer.answer == {"items": [_KILLER] * 3}
    assert answer.rationale == "ranked by trigger proximity"


def test_an_analyst_may_declare_the_evidence_class(packet):
    """DATA_POLICY.md separates `Q` (qualitative evidence) from `A` (explicit
    model assumption), and Cerebro assigns some slots to A by name:
    "catalyst probabilities and impacts are model assumptions and must be
    labeled" (03_market_analysis/AGENT.md), and the risk schema's own field is
    `probability_assumption`. Filing a declared assumption as observed
    evidence erases the one distinction the classes exist to make."""
    out = rsk.run(packet, {})
    answer = _analyst_judgments({"judgments": {"thesis_killers": {
        "answer": [_KILLER] * 3,
        "evidence_class": "A",
        "rationale": "probabilities are my own estimates",
        "source": "portfolio review",
    }}}, out.judgment_requests)[0]
    assert answer.evidence_class is EvidenceClass.A


def test_the_default_stays_qualitative(packet):
    """Only an explicit declaration changes it; a bare answer is still Q."""
    out = biz.run(packet, {})
    answer = _analyst_judgments(
        {"judgments": {"moat_classification": "Wide"}}, out.judgment_requests)[0]
    assert answer.evidence_class is EvidenceClass.Q


def test_an_unrecognised_class_falls_back_rather_than_crashing(packet):
    out = biz.run(packet, {})
    answer = _analyst_judgments({"judgments": {"moat_classification": {
        "answer": "Wide", "evidence_class": "Z"}}}, out.judgment_requests)[0]
    assert answer.evidence_class is EvidenceClass.Q
