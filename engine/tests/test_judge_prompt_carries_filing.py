"""The 10-K passages must reach the prompt, not just get fetched.

`filing_excerpts` pulled the filing from EDGAR and `_excerpt_block` formatted
it, but the formatted block was assigned to a local that the prompt string
never interpolated. Every judgment was therefore answered from the numeric
packet alone, while customer concentration, backlog/RPO and recurring revenue
are disclosed *only* in the 10-K narrative -- the judge was being asked about
a filing it had never seen, which is exactly what the fetch existed to fix.

These tests assert on the prompt the client actually receives, because that
is the only place the defect was visible: the fetch, the parsing and the
formatting were all working.
"""

from types import SimpleNamespace

from wbj.config import Settings
from wbj.judge import answer_judgments
from wbj.specialists.common import JudgmentRequest

from tests.test_judge import _FakeClient

_PASSAGE = (
    "No single customer accounted for more than 10% of total revenue "
    "in fiscal 2025, and our remaining performance obligation was $12.4 billion."
)


def _req(rid="business_analysis:conc", metric="customer_concentration"):
    return JudgmentRequest(
        request_id=rid, agent_id="business_analysis", metric_id=metric,
        question="q?", schema_hint="free text",
    )


class _FakeEdgar:
    """Minimal EDGAR stand-in: one CIK, one 10-K."""

    def __init__(self, text=_PASSAGE):
        self._text = text
        self.calls = []

    def cik_for(self, ticker):
        self.calls.append(ticker)
        return "0000320193"

    def latest_10k_text(self, cik):
        return {"text": self._text, "accession": "0000320193-25-000073",
                "filing_date": "2025-11-01"}


def _packet(ticker="AAPL"):
    return SimpleNamespace(security=SimpleNamespace(ticker=ticker))


def _prompt_of(client):
    return client.last_call["messages"][0]["content"]


def test_filing_passage_reaches_the_prompt():
    client = _FakeClient([])
    answer_judgments(_packet(), [_req()], Settings(anthropic_api_key="k"),
                     client=client, edgar=_FakeEdgar())
    prompt = _prompt_of(client)
    assert "more than 10% of total revenue" in prompt
    assert "remaining performance obligation" in prompt


def test_prompt_carries_the_accession_so_the_answer_is_attributable():
    client = _FakeClient([])
    answer_judgments(_packet(), [_req()], Settings(anthropic_api_key="k"),
                     client=client, edgar=_FakeEdgar())
    prompt = _prompt_of(client)
    # Without the accession and filing date the judge can cite "the 10-K" but
    # nobody can check WHICH one -- the Cerebro's source rule needs both.
    assert "0000320193-25-000073" in prompt
    assert "2025-11-01" in prompt


def test_passages_are_labelled_candidates_not_answers():
    """The excerpt search is keyword-based and returns near misses."""
    client = _FakeClient([])
    answer_judgments(_packet(), [_req()], Settings(anthropic_api_key="k"),
                     client=client, edgar=_FakeEdgar())
    prompt = _prompt_of(client)
    assert "CANDIDATOS" in prompt
    assert "INSUFFICIENT" in prompt


def test_no_edgar_still_produces_a_usable_prompt():
    """A missing reader degrades to numbers-only, never to a crash."""
    client = _FakeClient([])
    answer_judgments(_packet(), [_req()], Settings(anthropic_api_key="k"),
                     client=client, edgar=None)
    prompt = _prompt_of(client)
    assert "Datos de la empresa:" in prompt
    assert "10-K" not in prompt


def test_unreadable_filing_does_not_break_the_prompt():
    """EDGAR raising must leave the judge exactly as informed as before."""

    class _Broken(_FakeEdgar):
        def latest_10k_text(self, cik):
            raise RuntimeError("EDGAR 503")

    client = _FakeClient([])
    answer_judgments(_packet(), [_req()], Settings(anthropic_api_key="k"),
                     client=client, edgar=_Broken())
    assert "Datos de la empresa:" in _prompt_of(client)
