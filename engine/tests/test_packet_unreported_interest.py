"""A zero interest expense on a debt-carrying company is absence, not zero.

FMP reports `interestExpense: 0` for AAPL FY2025 and FY2024 — along with every
other interest field — because Apple stopped disclosing the line separately
after FY2023. EDGAR's `InterestExpense` series stops in the same year, so no
source carries it. Apple held $112B of debt that year.

Read as a reported zero, the claim propagated as fact: FIN-BS-020,
RSK-ICOV-011 and RSK-FCC-012 each came back NOT_MEANINGFUL citing
"ZERO_INTEREST_EXPENSE" — telling the reader Apple has no interest cost rather
than that nobody reported one.

`MISSING_DATA_POLICY.md` step 2 decides it: "Is the source expected to report
it? If yes but absent, use `MISSING`."
"""

from wbj.packet.builder import _merge_statement_period, _unreported_interest_to_absent


def test_zero_interest_with_debt_outstanding_becomes_absent():
    """The AAPL FY2025 case."""
    row = {"interest_expense": 0, "total_debt": 112_377_000_000}
    assert _unreported_interest_to_absent(row)["interest_expense"] is None


def test_a_debt_free_company_keeps_its_genuine_zero():
    """Step 2 turns on whether the source is *expected* to report. A company
    with no debt is not, so its zero is a real figure and must survive."""
    row = {"interest_expense": 0, "total_debt": 0}
    assert _unreported_interest_to_absent(row)["interest_expense"] == 0


def test_unknown_debt_is_not_grounds_to_discard_the_zero():
    """Without a debt figure there is no basis to call the zero unreported.
    Discarding it anyway would delete data on the strength of a guess."""
    row = {"interest_expense": 0, "total_debt": None}
    assert _unreported_interest_to_absent(row)["interest_expense"] == 0
    assert _unreported_interest_to_absent({"interest_expense": 0})["interest_expense"] == 0


def test_a_reported_nonzero_expense_is_untouched():
    row = {"interest_expense": 2_385_000_000, "total_debt": 112_184_000_000}
    assert _unreported_interest_to_absent(row)["interest_expense"] == 2_385_000_000


def test_an_already_absent_expense_stays_absent():
    row = {"interest_expense": None, "total_debt": 5_000_000}
    assert _unreported_interest_to_absent(row)["interest_expense"] is None


def test_no_other_field_is_disturbed():
    """The rule edits one field. A merge step that quietly dropped or altered
    a neighbouring balance would be a worse defect than the one it fixes."""
    row = {"interest_expense": 0, "total_debt": 100, "revenue": 900, "ebit": 300}
    out = _unreported_interest_to_absent(row)
    assert {k: v for k, v in out.items() if k != "interest_expense"} == {
        "total_debt": 100, "revenue": 900, "ebit": 300}


def test_the_input_row_is_not_mutated():
    """`_merge_statement_period` hands us a fresh dict today, but a caller that
    reuses its row must not find it silently rewritten."""
    row = {"interest_expense": 0, "total_debt": 100}
    _unreported_interest_to_absent(row)
    assert row["interest_expense"] == 0


def test_the_rule_is_wired_into_the_merge():
    """The unit rule is worth nothing if the packet never applies it."""
    merged = _merge_statement_period(
        {"date": "2025-09-27", "interestExpense": 0, "revenue": 400_000_000_000},
        {"totalDebt": 112_377_000_000},
        {},
    )
    assert merged["interest_expense"] is None
    assert merged["total_debt"] == 112_377_000_000


def test_the_merge_keeps_a_debt_free_zero():
    merged = _merge_statement_period(
        {"date": "2025-12-31", "interestExpense": 0},
        {"totalDebt": 0},
        {},
    )
    assert merged["interest_expense"] == 0
