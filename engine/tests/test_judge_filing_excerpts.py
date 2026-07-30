"""The judge must see the filing it is asked to judge.

Customer concentration, backlog/RPO and recurring revenue are disclosed only
in the 10-K narrative and exist in no structured endpoint at any price. The
judge was handed the facts table and the FMP profile blurb, so it was being
asked about a document it had never read.

Regex extraction was tried first and rejected. The patterns matched NVDA
("sales to one direct customer represented 22% of total revenue") and
silently mismatched Apple, whose only customer-percentage sentence is about
*trade receivables*. A wrong number wearing the right shape is worse than a
gap, so the passages go to the judge and the judge decides.
"""

from __future__ import annotations

from wbj.judge import _excerpt_block, filing_excerpts


class _FakeEdgar:
    def __init__(self, text=None, cik=123, raises=False):
        self._text, self._cik, self._raises = text, cik, raises

    def cik_for(self, ticker):
        return self._cik

    def latest_10k_text(self, cik):
        if self._raises:
            raise RuntimeError("SEC unreachable")
        if self._text is None:
            return None
        return {"text": self._text, "accession": "0000-26-000001",
                "filing_date": "2026-02-25"}


_FILING = (
    "Item 1A. Risk Factors. " + ("padding. " * 40) +
    "For fiscal year 2026, sales to one direct customer represented 22% of "
    "total revenue and sales to another represented 14% of total revenue. " +
    ("padding. " * 40) +
    "As of January 25, 2026, revenue related to remaining performance "
    "obligations was $2.1 billion. " + ("padding. " * 40) +
    "We compete with a number of established companies. "
)


def test_the_disclosed_passages_reach_the_judge():
    bundle = filing_excerpts("NVDA", _FakeEdgar(_FILING))
    assert bundle["accession"] == "0000-26-000001"
    joined = " ".join(p for v in bundle["excerpts"].values() for p in v)
    assert "22% of total revenue" in joined
    assert "remaining performance obligations" in joined


def test_an_unreadable_filing_leaves_the_judge_no_worse_off():
    """A filing we cannot fetch must not fail the analysis."""
    assert filing_excerpts("NVDA", _FakeEdgar(raises=True)) == {}
    assert filing_excerpts("NVDA", _FakeEdgar(text=None)) == {}
    assert filing_excerpts("NVDA", None) == {}
    assert filing_excerpts("", _FakeEdgar(_FILING)) == {}


def test_an_unknown_ticker_yields_nothing():
    assert filing_excerpts("ZZZZ", _FakeEdgar(_FILING, cik=None)) == {}


def test_the_bundle_stays_within_its_character_budget():
    """A 10-K runs to 1.4M characters; the prompt must not."""
    from wbj.judge import _MAX_TOTAL_CHARS

    huge = ("we compete with everyone. of total revenue matters. backlog is big. "
            "recurring revenue counts. " * 5000)
    bundle = filing_excerpts("X", _FakeEdgar(huge))
    total = sum(len(p) for v in bundle["excerpts"].values() for p in v)
    assert total <= _MAX_TOTAL_CHARS * 1.2  # window overshoot on the last hit


def test_the_block_tells_the_judge_these_are_candidates_not_answers():
    """The search is keyword-based and returns near misses -- Apple's only
    customer-percentage sentence is about receivables. The prompt has to say
    so, or a near miss reads as a finding."""
    block = _excerpt_block(filing_excerpts("NVDA", _FakeEdgar(_FILING)))
    assert "CANDIDATOS" in block
    assert "INSUFFICIENT" in block
    assert "CITA" in block
    assert "0000-26-000001" in block  # citable accession


def test_an_empty_bundle_adds_nothing_to_the_prompt():
    assert _excerpt_block({}) == ""
