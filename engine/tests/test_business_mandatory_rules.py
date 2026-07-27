"""DECISION_RULES.md's "Mandatory flags" section, all four bullets.

Three name a flag and the condition that raises it. The fourth is
"Always list three business-model or execution risks that could destroy
the thesis" — the same list, the same standing, but the only one with no
name of its own. Nothing raised anything when it went unmet, so an output
could ship with the list empty and read as fully compliant.

The rule is settled in two places because the list is empty when
`business.run()` returns and filled afterwards by the judgment layer:
`run()` raises the flag, `_recompute_business_flags` clears it once the
answer lands. An output that never reaches that layer keeps the flag,
which is the honest reading.
"""

from datetime import datetime, timezone

import pytest

from wbj.deep import _apply_judgments_to_output_fields
from wbj.judge import Judgment
from wbj.schemas.packet import AnalysisMeta, Packet, Security
from wbj.specialists import business as bus
from wbj.specialists.common import JudgmentRequest

REQUEST = JudgmentRequest(
    request_id="business_analysis:three_thesis_killers",
    agent_id=bus.AGENT_ID, metric_id="three_thesis_killers",
    question="?", schema_hint="array of exactly 3 strings")


def _row(**over):
    row = dict(fiscalYear="2025", filingDate="2026-02-20", revenue=1000.0,
               gross_profit=600.0, ebit=200.0, net_income=150.0,
               operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
               total_debt=150.0, total_equity=600.0, cash=200.0,
               total_assets=1000.0, total_liabilities=400.0,
               income_before_tax=190.0, income_tax_expense=40.0,
               diluted_shares=100.0, stock_based_compensation=10.0,
               changeInWorkingCapital=-15.0)
    row.update(over)
    return row


def _packet(rows=None):
    return Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Semiconductors"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": rows or [_row() for _ in range(6)]},
        facts_table={}, market_data={}, estimates={},
        capital_structure={}, staleness={})


def _after_judgment(output, answer):
    judgment = Judgment(request_id=REQUEST.request_id, answer=answer,
                        evidence_class="C", source="analyst")
    return _apply_judgments_to_output_fields(
        [("Business", output)], [judgment], [REQUEST])[0][1]


# --- the predicate ---------------------------------------------------------


def test_three_stated_risks_satisfy_the_rule():
    assert bus.thesis_killers_listed(["a", "b", "c"]) is True


def test_the_count_he_asks_for_is_three():
    assert bus.REQUIRED_THESIS_KILLERS == 3


@pytest.mark.parametrize("risks", [
    None, [], ["only one"], ["one", "two"],
])
def test_fewer_than_three_does_not(risks):
    assert bus.thesis_killers_listed(risks) is False


def test_more_than_three_satisfies_it():
    """"Always list three" guards their absence. Four does not withhold
    the three he requires."""
    assert bus.thesis_killers_listed(["a", "b", "c", "d"]) is True


@pytest.mark.parametrize("risks", [
    ["", "", ""], ["   ", "\t", "\n"], ["a", "", "c"],
])
def test_blank_entries_are_not_risks(risks):
    """Three empty strings would satisfy a length check and list no risk
    at all."""
    assert bus.thesis_killers_listed(risks) is False


@pytest.mark.parametrize("risks", [
    "not a list", 3, {"a": 1}, [1, 2, 3], [None, None, None],
])
def test_a_malformed_answer_does_not_satisfy_it(risks):
    """The answer arrives from a judgment, which can be any shape."""
    assert bus.thesis_killers_listed(risks) is False


# --- the flag's lifecycle --------------------------------------------------


def test_a_run_on_its_own_reports_the_rule_unmet():
    """`run()` returns before the judgment layer, so the list is empty and
    the rule is genuinely unmet at that point."""
    out = bus.run(_packet(), {"wacc": 0.09})
    assert bus.THESIS_KILLERS_NOT_LISTED in out.mandatory_flags
    assert out.three_thesis_killers == []


def test_three_answered_risks_clear_the_flag():
    out = bus.run(_packet(), {"wacc": 0.09})
    judged = _after_judgment(out, {"items": ["a", "b", "c"]})
    assert bus.THESIS_KILLERS_NOT_LISTED not in judged.mandatory_flags
    assert judged.three_thesis_killers == ["a", "b", "c"]


@pytest.mark.parametrize("answer", [
    {"items": ["only", "two"]},
    {"items": ["", "", ""]},
    {"items": []},
])
def test_an_insufficient_answer_leaves_the_flag_standing(answer):
    out = bus.run(_packet(), {"wacc": 0.09})
    judged = _after_judgment(out, answer)
    assert bus.THESIS_KILLERS_NOT_LISTED in judged.mandatory_flags


def test_clearing_the_flag_leaves_his_other_flags_alone():
    """The three he names must survive the removal of the fourth."""
    concentrated = bus.run(_packet(), {"wacc": 0.09, "largest_customer_share": 0.35})
    assert "CONCENTRATION_RED_FLAG" in concentrated.mandatory_flags

    judged = _after_judgment(concentrated, {"items": ["a", "b", "c"]})
    assert "CONCENTRATION_RED_FLAG" in judged.mandatory_flags
    assert bus.THESIS_KILLERS_NOT_LISTED not in judged.mandatory_flags


def test_the_recomputation_leaves_a_non_business_output_alone():
    from wbj.deep import _recompute_business_flags

    class _Other:
        mandatory_flags = ["SOMETHING_ELSE"]
        three_thesis_killers = []

    other = _Other()
    assert _recompute_business_flags(other) is other


def test_the_recomputation_survives_an_output_without_flags():
    from wbj.deep import _recompute_business_flags

    class _Malformed:
        pass

    malformed = _Malformed()
    assert _recompute_business_flags(malformed) is malformed


# --- his three named flags, unchanged --------------------------------------


def test_value_destruction_still_fires_on_roic_below_wacc():
    """DECISION_RULES.md: "`VALUE_DESTRUCTION` when normalized ROIC <
    WACC"."""
    out = bus.run(_packet(), {"wacc": 0.99})
    assert "VALUE_DESTRUCTION" in out.mandatory_flags


def test_concentration_fires_above_thirty_percent():
    """"`CONCENTRATION_RED_FLAG` when one customer/product exceeds 30% of
    revenue"."""
    below = bus.run(_packet(), {"wacc": 0.09, "largest_customer_share": 0.30})
    above = bus.run(_packet(), {"wacc": 0.09, "largest_customer_share": 0.3001})
    assert "CONCENTRATION_RED_FLAG" not in below.mandatory_flags
    assert "CONCENTRATION_RED_FLAG" in above.mandatory_flags


def test_all_four_mandatory_rules_are_represented():
    """The section has four bullets. Three name a flag; the fourth had no
    representation at all until now."""
    out = bus.run(_packet(), {"wacc": 0.99, "largest_customer_share": 0.35})
    assert "VALUE_DESTRUCTION" in out.mandatory_flags
    assert "CONCENTRATION_RED_FLAG" in out.mandatory_flags
    assert bus.THESIS_KILLERS_NOT_LISTED in out.mandatory_flags

    # The dilution flag needs a rising share count, which this fixture
    # holds flat, so its threshold is checked directly instead:
    # "diluted shares grow >5% CAGR".
    assert bus.is_dilution_red_flag(0.0501) is True
    assert bus.is_dilution_red_flag(0.05) is False


def test_an_array_field_refuses_a_string_answer():
    """OUTPUT_SCHEMA.md declares `three_thesis_killers` an array, and
    `model_copy` does not validate — so a sentence answering it was stored as
    a *string* in a `list[str]` field. That left the schema holding text where
    a list belongs and jammed DECISION_RULES.md's "always list three ... risks"
    flag for good: `thesis_killers_listed` requires a list, and a string is
    never one, so no answer could ever clear it."""
    packet = _packet()
    out = bus.run(packet, {"wacc": 0.09})

    def answered(value):
        j = Judgment(request_id=REQUEST.request_id, answer=value,
                     evidence_class="C", source="analyst")
        return _apply_judgments_to_output_fields([("B", out)], [j], [REQUEST])[0][1]

    # The declared shape fills the field and clears the flag.
    good = answered({"items": ["China export controls", "In-house silicon",
                               "Supply concentration"]})
    assert good.three_thesis_killers == ["China export controls", "In-house silicon",
                                         "Supply concentration"]
    assert bus.THESIS_KILLERS_NOT_LISTED not in good.mandatory_flags

    # A string is refused, not written in: the field stays a list and the
    # flag stands, which is the honest state.
    bad = answered("China export controls; in-house silicon; supply concentration")
    assert bad.three_thesis_killers == []
    assert bus.THESIS_KILLERS_NOT_LISTED in bad.mandatory_flags
