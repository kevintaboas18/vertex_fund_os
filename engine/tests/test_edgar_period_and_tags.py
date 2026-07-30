"""Picking the right XBRL fact: the right element, for the right period.

Reconciliation only means something when both sides measure the same
thing. Two defects made that false in opposite directions.
"""

import pytest

from wbj.packet.builder import (
    _EDGAR_REVENUE_TAGS,
    _edgar_first_available,
    _edgar_value_at,
)


def _facts(tag, entries, taxonomy="us-gaap", unit="USD"):
    return {"facts": {taxonomy: {tag: {"units": {unit: entries}}}}}


ANNUAL = {"start": "2025-02-01", "end": "2026-01-31", "val": 41.525e9,
          "fp": "FY", "form": "10-K", "accn": "0001-25-000001", "filed": "2026-03-04"}
Q4 = {"start": "2025-11-01", "end": "2026-01-31", "val": 11.3e9,
      "fp": "Q4", "form": "10-Q", "accn": "0001-25-000002", "filed": "2026-03-04"}
HALF_YEAR = {"start": "2025-02-01", "end": "2025-07-31", "val": 20.065e9,
             "fp": "Q2", "form": "10-Q", "accn": "0001-25-000003", "filed": "2025-09-03"}
QUARTER = {"start": "2025-05-01", "end": "2025-07-31", "val": 10.236e9,
           "fp": "Q2", "form": "10-Q", "accn": "0001-25-000004", "filed": "2025-09-03"}


def test_an_annual_target_takes_the_annual_fact():
    """Salesforce files a half-year cumulative and a standalone quarter
    that end on the same day, and an annual figure that ends on the same
    day as Q4. Matching on `end` alone picks whichever came first in the
    payload — a coin flip between $41.5B and $11.3B, which `reconcile`
    then reports as a source conflict."""
    facts = _facts("Revenues", [Q4, ANNUAL])
    v = _edgar_value_at(facts, "us-gaap", "Revenues", "USD", "2026-01-31")
    assert v.value == pytest.approx(41.525e9)


def test_the_order_of_the_payload_does_not_decide_the_answer():
    for entries in ([Q4, ANNUAL], [ANNUAL, Q4]):
        v = _edgar_value_at(_facts("Revenues", entries), "us-gaap",
                            "Revenues", "USD", "2026-01-31")
        assert v.value == pytest.approx(41.525e9)


def test_two_interim_periods_sharing_an_end_date_are_distinguished():
    """Neither is annual, so the request still has to resolve to one
    of them rather than raising or averaging."""
    v = _edgar_value_at(_facts("Revenues", [HALF_YEAR, QUARTER]), "us-gaap",
                        "Revenues", "USD", "2025-07-31")
    assert v.value in (pytest.approx(20.065e9), pytest.approx(10.236e9))


def test_a_balance_sheet_fact_is_unaffected():
    """Stock facts carry no `start`, so nothing to disambiguate."""
    debt = {"end": "2026-01-31", "val": 8.468e9, "form": "10-K",
            "accn": "0001-25-000001", "filed": "2026-03-04"}
    v = _edgar_value_at(_facts("DebtCurrent", [debt]), "us-gaap",
                        "DebtCurrent", "USD", "2026-01-31")
    assert v.value == pytest.approx(8.468e9)


# --- tag selection --------------------------------------------------------


def test_the_asc_606_element_is_preferred():
    """`Revenues` is the older element and, where a filer tags both,
    often carries a narrower figure — Salesforce reported $8.39B under it
    against the $41.525B it actually earned."""
    assert _EDGAR_REVENUE_TAGS[0] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert "Revenues" in _EDGAR_REVENUE_TAGS

    facts = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [ANNUAL]}},
        "Revenues": {"units": {"USD": [{**ANNUAL, "val": 8.39e9}]}},
    }}}
    v = _edgar_first_available(facts, "us-gaap", _EDGAR_REVENUE_TAGS, "USD", "2026-01-31")
    assert v.value == pytest.approx(41.525e9)


def test_a_tag_without_the_period_defers_to_one_that_has_it():
    """The regression this guards: taking the first tag that returns *any*
    value read "some value is better than none" as a match, so NVIDIA's
    unused ASC 606 element returned a stale quarterly figure instead of
    deferring to the element it does file. Exact agreement became 702%
    apart."""
    stale_quarter = {"start": "2023-05-01", "end": "2023-07-31", "val": 26.914e9,
                     "fp": "Q2", "form": "10-Q", "accn": "x", "filed": "2023-08-01"}
    facts = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [stale_quarter]}},
        "Revenues": {"units": {"USD": [{**ANNUAL, "val": 215.938e9}]}},
    }}}
    v = _edgar_first_available(facts, "us-gaap", _EDGAR_REVENUE_TAGS, "USD", "2026-01-31")
    assert v.value == pytest.approx(215.938e9)


def test_no_tag_covering_the_period_still_returns_the_best_available():
    """Reporting nothing would be worse than reporting something
    `reconcile` can judge and flag."""
    stale = {"start": "2023-05-01", "end": "2023-07-31", "val": 26.914e9,
             "fp": "Q2", "form": "10-Q", "accn": "x", "filed": "2023-08-01"}
    facts = _facts("Revenues", [stale])
    v = _edgar_first_available(facts, "us-gaap", _EDGAR_REVENUE_TAGS, "USD", "2026-01-31")
    assert v.value == pytest.approx(26.914e9)


def test_no_tag_at_all_is_missing():
    v = _edgar_first_available({"facts": {"us-gaap": {}}}, "us-gaap",
                               _EDGAR_REVENUE_TAGS, "USD", "2026-01-31")
    assert v.is_null
