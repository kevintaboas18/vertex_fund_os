"""Tests for the qualitative judgment agent (offline — API client mocked)."""

from wbj.config import Settings
from wbj.core.nullstates import EvidenceClass
from wbj.judge import _coerce_answer, answer_judgments
from wbj.specialists.common import JudgmentRequest


def _req(rid, hint, metric="m"):
    return JudgmentRequest(
        request_id=rid, agent_id="business_analysis", metric_id=metric,
        question="q?", schema_hint=hint,
    )


class _FakeParsed:
    def __init__(self, answers):
        self.parsed_output = answers


class _FakeAnswerModel:
    def __init__(self, request_id, answer, evidence_class="C", source="10-K", rationale="x"):
        self.request_id = request_id
        self.answer = answer
        self.evidence_class = evidence_class
        self.source = source
        self.rationale = rationale


class _FakeAnswers:
    def __init__(self, answers):
        self.answers = answers


class _FakeClient:
    """Stub anthropic client: records the request, returns canned answers."""

    def __init__(self, answers):
        self._answers = answers
        self.last_call = None

        class _Messages:
            def __init__(self, outer):
                self._outer = outer

            def parse(self, **kwargs):
                self._outer.last_call = kwargs
                return _FakeParsed(_FakeAnswers(self._outer._answers))

        self.messages = _Messages(self)


def test_no_requests_returns_empty():
    s = Settings(anthropic_api_key="k")
    assert answer_judgments({}, [], s, client=_FakeClient([])) == []


def test_no_api_key_returns_empty_without_calling():
    s = Settings(anthropic_api_key=None)
    # client None + no key → graceful [] (never raises)
    assert answer_judgments({}, [_req("business_analysis:moat", "one of Wide|Narrow|None")], s) == []


def test_enum_answer_becomes_judgment():
    s = Settings(anthropic_api_key="k")
    reqs = [_req("business_analysis:moat", "one of Wide|Narrow|None", metric="moat_classification")]
    client = _FakeClient([_FakeAnswerModel("business_analysis:moat", "Wide", "R", "10-K FY25", "durable network effects")])
    out = answer_judgments({}, reqs, s, client=client)
    assert len(out) == 1
    j = out[0]
    assert j.request_id == "business_analysis:moat"
    assert j.answer == "Wide"  # enum stays a string for schema_hint_ok
    assert j.evidence_class == EvidenceClass.R
    assert j.source == "10-K FY25"
    assert j.rationale


def test_batches_all_requests_into_one_call():
    s = Settings(anthropic_api_key="k")
    reqs = [_req(f"a:{i}", "one of Wide|Narrow|None") for i in range(3)]
    client = _FakeClient([_FakeAnswerModel(f"a:{i}", "Narrow") for i in range(3)])
    out = answer_judgments({}, reqs, s, client=client)
    assert len(out) == 3
    # one API call, and it used the configured model
    assert client.last_call["model"] == "claude-opus-4-8"
    assert f"{len(reqs)}" in client.last_call["messages"][0]["content"]


def test_unknown_request_id_dropped():
    s = Settings(anthropic_api_key="k")
    reqs = [_req("a:1", "one of Wide|Narrow|None")]
    client = _FakeClient([_FakeAnswerModel("a:999", "Wide")])
    assert answer_judgments({}, reqs, s, client=client) == []


def test_insufficient_answer_preserved():
    s = Settings(anthropic_api_key="k")
    reqs = [_req("a:1", "one of Wide|Narrow|None")]
    client = _FakeClient([_FakeAnswerModel("a:1", "INSUFFICIENT", "Q", "n/a", "no segment data")])
    out = answer_judgments({}, reqs, s, client=client)
    assert out[0].answer == "INSUFFICIENT"
    assert out[0].evidence_class == EvidenceClass.Q


def test_coerce_numeric_and_dict_and_array_hints():
    assert _coerce_answer("7.5", "float 0-10") == 7.5
    assert _coerce_answer("3", "integer 1-5") == 3.0
    d = _coerce_answer('{"probability": 0.6, "impact": 5e8}', "{probability: 0-1, impact: usd}")
    assert isinstance(d, dict) and d["probability"] == 0.6
    a = _coerce_answer('["antitrust", "supply shock"]', "array of strings")
    assert a == {"items": ["antitrust", "supply shock"]}
    assert _coerce_answer("Wide", "one of Wide|Narrow|None") == "Wide"


def test_bad_evidence_class_becomes_none():
    from wbj.judge import _to_evidence
    assert _to_evidence("R") == EvidenceClass.R
    assert _to_evidence("nonsense") is None
