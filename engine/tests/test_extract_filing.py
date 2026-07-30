"""Filing extraction must transcribe, never infer.

Customer concentration is on PROHIBITED_IMPUTATION: it may be read out
of the filing, never derived. The guarantee that keeps those two apart
is quote verification — a figure is accepted only when the sentence it
came from is found verbatim in the filing. These tests are that
guarantee's regression net.
"""

from types import SimpleNamespace

from wbj.extract.filing import _relevant_context, extract_disclosures

_FILING = (
    "Item 1A. Risk Factors. We depend on a limited number of customers. "
    "For fiscal year 2026, sales to one direct customer represented 22% of "
    "total revenue and sales to another direct customer represented 14% of "
    "total revenue. "
    "Separately, we acquired a licence from Groq, Inc. No customer contracts, "
    "existing products, or equity interests were purchased."
)


def _settings():
    return SimpleNamespace(anthropic_api_key="k", extract_model="test-model")


def _client(**fields):
    parsed = SimpleNamespace(
        largest_customer_share=fields.get("largest"),
        largest_customer_quote=fields.get("largest_quote"),
        other_customer_shares=fields.get("others", []),
        other_customer_quote=fields.get("others_quote"),
        recurring_revenue_share=fields.get("recurring"),
        recurring_revenue_quote=fields.get("recurring_quote"),
        rpo_total_usd=fields.get("rpo_total"),
        rpo_total_quote=fields.get("rpo_total_quote"),
        rpo_share_next_12m=fields.get("rpo_share"),
        rpo_share_next_12m_quote=fields.get("rpo_share_quote"),
        # The catalyst pass reuses this same double.
        catalysts=fields.get("catalysts", []),
    )
    messages = SimpleNamespace(
        parse=lambda **kw: SimpleNamespace(parsed_output=parsed))
    return SimpleNamespace(messages=messages)


def _filing():
    return {"text": _FILING, "url": "https://sec.gov/x.htm"}


_REAL_QUOTE = ("sales to one direct customer represented 22% of total revenue")


def test_quote_backed_figure_is_accepted():
    out = extract_disclosures(
        _filing(), _settings(),
        _client(largest=0.22, largest_quote=_REAL_QUOTE,
                others=[0.14], others_quote="represented 14% of total revenue"))
    assert out["largest_customer_share"] == 0.22
    assert out["customer_shares"] == [0.22, 0.14]


def test_figure_with_a_fabricated_quote_is_dropped():
    """The whole point: a confident number whose sentence is not in the
    filing is a hallucination, and must never reach the score."""
    out = extract_disclosures(
        _filing(), _settings(),
        _client(largest=0.35,
                largest_quote="sales to one customer represented 35% of total revenue"))
    assert "largest_customer_share" not in out


def test_figure_without_any_quote_is_dropped():
    out = extract_disclosures(_filing(), _settings(), _client(largest=0.22))
    assert out == {}


def test_quote_matching_ignores_typography_not_content():
    """Filings use curly quotes, non-breaking hyphens and ragged
    whitespace; those must not fail an otherwise faithful quote."""
    noisy = "sales  to one   direct customer represented 22% of total revenue"
    out = extract_disclosures(
        _filing(), _settings(), _client(largest=0.22, largest_quote=noisy))
    assert out["largest_customer_share"] == 0.22


def test_out_of_range_share_is_dropped():
    """A share above 1.0 means the model returned a percent, not a
    decimal — rescaling it silently would invent precision."""
    out = extract_disclosures(
        _filing(), _settings(), _client(largest=22.0, largest_quote=_REAL_QUOTE))
    assert "largest_customer_share" not in out


def test_undisclosed_figure_stays_absent():
    """NVDA discloses no recurring-revenue share. Absent must mean
    absent, so the metric stays NOT_SCORABLE."""
    out = extract_disclosures(
        _filing(), _settings(), _client(largest=0.22, largest_quote=_REAL_QUOTE))
    assert "recurring_revenue" not in out


def test_others_are_dropped_when_only_their_quote_fails():
    """A verified largest customer must not drag in unverified peers —
    the HHI would then be computed off a fabricated set."""
    out = extract_disclosures(
        _filing(), _settings(),
        _client(largest=0.22, largest_quote=_REAL_QUOTE,
                others=[0.14], others_quote="a sentence not in this filing"))
    assert out["customer_shares"] == [0.22]


def test_extraction_without_an_api_key_is_silent():
    out = extract_disclosures(_filing(), SimpleNamespace(anthropic_api_key=None))
    assert out == {}


def test_context_narrows_the_filing_around_the_disclosures():
    """The filing runs ~370k chars; only the windows that can hold these
    disclosures are worth paying tokens for."""
    padding = "irrelevant prose. " * 5000
    ctx = _relevant_context(padding + _FILING + padding)
    assert "22% of total revenue" in ctx
    assert len(ctx) < len(padding)


def test_a_receivables_quote_is_rejected_even_though_it_is_verbatim():
    """The subtler failure: Apple's 10-K really does say a customer was
    12% of trade *receivables*. Quoted verbatim it passes quote
    verification while answering a different question, so the base has
    to be checked too."""
    filing = {"text": "The Company had one customer that represented 10% or more "
                      "of total trade receivables, which accounted for 12%.",
              "url": "https://sec.gov/aapl.htm"}
    out = extract_disclosures(
        filing, _settings(),
        _client(largest=0.12,
                largest_quote="one customer that represented 10% or more of "
                              "total trade receivables, which accounted for 12%"))
    assert "largest_customer_share" not in out


_RPO_FILING = {
    "text": "As of January 25, 2026, revenue related to remaining performance "
            "obligations from contracts greater than one year in length was "
            "$2.3 billion. Approximately 42% of revenue from contracts greater "
            "than one year in length will be recognized over the next twelve months.",
    "url": "https://sec.gov/nvda.htm",
}


def test_ntm_contracted_is_the_product_of_both_disclosed_parts():
    """MKT-COVER-016 wants next-twelve-month contracted backlog. Filings
    disclose the total RPO and the share due within a year separately;
    taking the $2.3bn headline as the 12-month figure would overstate
    coverage by more than double."""
    out = extract_disclosures(
        _RPO_FILING, _settings(),
        _client(rpo_total=2_300_000_000.0,
                rpo_total_quote="remaining performance obligations from contracts "
                                "greater than one year in length was $2.3 billion",
                rpo_share=0.42,
                rpo_share_quote="Approximately 42% of revenue from contracts greater "
                                "than one year in length will be recognized over the "
                                "next twelve months"))
    assert out["ntm_contracted"] == 966_000_000.0


def test_rpo_total_without_the_12m_share_yields_nothing():
    """Half the pair is not a usable answer — the window is the whole
    point of the metric."""
    out = extract_disclosures(
        _RPO_FILING, _settings(),
        _client(rpo_total=2_300_000_000.0,
                rpo_total_quote="remaining performance obligations from contracts "
                                "greater than one year in length was $2.3 billion"))
    assert "ntm_contracted" not in out


def test_quote_survives_entity_and_currency_typesetting():
    """Filings render "$ 2.3 billion" and "42 %" once HTML is stripped,
    and carry &#160; inside dates. A faithful quote written the human way
    must still verify — the guard exists to catch fabrication, not
    typesetting.

    The sentence carries "remaining performance obligation" because a filing
    that discloses RPO says so. `_relevant_context` windows on that phrase and
    now skips the request entirely when no anchor appears, so a fixture
    without it would exercise an excerpt no real filing produces. What this
    test is about is unchanged: "$ 2.3 billion" against "$2.3 billion".
    """
    filing = {"text": "As of January 25, 2026, remaining performance obligation was $ 2.3 billion "
                      "and 42 % is due within a year.",
              "url": "https://sec.gov/x.htm"}
    out = extract_disclosures(
        filing, _settings(),
        _client(rpo_total=2_300_000_000.0,
                rpo_total_quote="As of January 25, 2026, remaining performance obligation was $2.3 billion",
                rpo_share=0.42, rpo_share_quote="42% is due within a year"))
    assert out["ntm_contracted"] == 966_000_000.0


def test_catalysts_carry_only_the_dated_fact():
    """FORMULAS.md calls probability and impact "assumptions; never
    disguise as reported facts", so extraction supplies the event and
    its timing and leaves the triple to the judgment layer."""
    filing = {"text": "We expect to begin volume production in the second half "
                      "of fiscal 2027.", "url": "https://sec.gov/x.htm"}
    out = extract_disclosures(
        filing, _settings(),
        _client(catalysts=[SimpleNamespace(
            event="Volume production begins", months_to_event=9.0,
            quote="We expect to begin volume production in the second half of "
                  "fiscal 2027.")]))
    assert out["catalysts"] == [{"event": "Volume production begins",
                                 "months_to_event": 9.0,
                                 "source": "https://sec.gov/x.htm"}]
    assert "probability" not in out["catalysts"][0]


def test_catalyst_beyond_the_24_month_horizon_is_dropped():
    """DATASET.md scopes the registry to "forward 24 months"."""
    filing = {"text": "We expect to open the facility in 2031.",
              "url": "https://sec.gov/x.htm"}
    out = extract_disclosures(
        filing, _settings(),
        _client(catalysts=[SimpleNamespace(
            event="Facility opens", months_to_event=60.0,
            quote="We expect to open the facility in 2031.")]))
    assert "catalysts" not in out


def test_catalyst_with_an_unverifiable_quote_is_dropped():
    filing = {"text": "Nothing forward-looking here.", "url": "https://sec.gov/x.htm"}
    out = extract_disclosures(
        filing, _settings(),
        _client(catalysts=[SimpleNamespace(
            event="Invented launch", months_to_event=6.0,
            quote="We expect to launch in June.")]))
    assert "catalysts" not in out
