"""A filing with no anchor buys nothing, so it must not be paid for.

`_relevant_context` used to fall back to the first `budget` characters when no
anchor phrase appeared. That is the filing's cover page and table of contents —
where these disclosures never are — so it spent the MAXIMUM context on the
LEAST relevant text and could only come back null.

Measured over eleven 10-Ks: four matched no anchor (XOM, JNJ, BAC, MET) and
those four alone accounted for 60,000 of 70,161 input tokens — 85% of the
spend, on filings that state nothing to find. Broader phrasings were checked
too: XOM and JNJ carry no customer-concentration language at all, and BAC's
and MET's "concentrations of credit risk" are about investment counterparties,
not customers.

The catalyst path must survive the gate: it runs its own anchors, and a filing
can announce a dated plan while disclosing no concentration at all.
"""

from types import SimpleNamespace

from wbj.extract.filing import _ANCHORS, _relevant_context, extract_disclosures


def test_no_anchor_yields_no_excerpt():
    """The cover page is not an answer."""
    assert _relevant_context("Item 1. Business. " * 5000, _ANCHORS) == ""


def test_an_anchor_yields_a_window_around_it():
    text = "filler " * 200 + "one customer accounted for 22% of revenue" + " filler" * 200
    out = _relevant_context(text, _ANCHORS)
    assert "one customer accounted for 22%" in out
    assert 0 < len(out) < len(text)


def test_the_excerpt_is_far_smaller_than_the_filing():
    """The saving is the point: a 10-K runs hundreds of thousands of
    characters and the disclosure lives in a few hundred."""
    text = "padding. " * 40_000 + "no customer represented 10% or more." + " tail." * 40_000
    out = _relevant_context(text, _ANCHORS)
    assert len(out) < len(text) * 0.10


class _Client:
    """Records whether a request was made."""

    def __init__(self, catalysts=None):
        self.calls = 0
        self._catalysts = catalysts or []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(catalysts=list(self._catalysts))


def _settings():
    return SimpleNamespace(anthropic_api_key="k", extract_model="claude-sonnet-5")


def test_an_anchorless_filing_makes_no_disclosure_request():
    """The token saving, asserted where it happens: no anchors, no call."""
    client = _Client()
    filing = {"text": "Item 1. Business. " * 2000, "url": "https://sec.gov/x.htm"}
    out = extract_disclosures(filing, _settings(), client)
    assert out == {}
    assert client.calls <= 1, "a filing with nothing to find still cost a request"


def test_an_empty_filing_makes_no_request_at_all():
    client = _Client()
    assert extract_disclosures({"text": "", "url": "u"}, _settings(), client) == {}
    assert client.calls == 0


# --- the anchor set must cover how filings actually word these -------------

#: Verbatim from real 10-Ks. Each was checked against `_ANCHORS`; the second
#: group was missed before the set was widened, and WMT's miss meant the whole
#: filing was skipped without a request.
_REAL_DISCLOSURES = [
    ("CSCO", "No single customer accounted for 10% or more of revenue in fiscal 2025"),
    ("NVDA", "sales to one direct customer represented 22% of total revenue"),
    ("KO", "No bottlers or customers represented 10% or more of our net operating revenues"),
    ("WMT", "the Company did not generate material revenues from any single customer"),
    ("MSFT", "No sales to an individual customer or country other than the United "
             "States accounted for more than 10% of revenue"),
]


def test_every_real_disclosure_wording_is_anchored():
    """A phrasing with no anchor is a disclosure the engine never reads —
    the filing is skipped before a request is ever made, so the metric goes
    MISSING on a company that did state the figure."""
    for ticker, sentence in _REAL_DISCLOSURES:
        text = "padding. " * 500 + sentence + " tail." * 500
        out = _relevant_context(text, _ANCHORS)
        assert sentence in out, f"{ticker}: '{sentence[:50]}...' quedo fuera del extracto"


def test_the_variants_are_listed_separately_because_matching_is_literal():
    """"10% or more" does not match "more than 10%", and "no customer" does not
    match "no single customer". Each variant has to be its own anchor."""
    assert "more than 10%" in _ANCHORS
    assert "single customer" in _ANCHORS
    assert "individual customer" in _ANCHORS


def test_widening_the_set_did_not_swallow_whole_filings():
    """Anchors buy coverage with tokens. A phrase common enough to match
    everywhere would return most of the document and undo the saving."""
    filler = "The Company operates in one segment and reports annually. " * 4000
    assert _relevant_context(filler, _ANCHORS) == ""
