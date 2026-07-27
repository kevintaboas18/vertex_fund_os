"""Biotech: a methodology gap reported in Cerebro's own vocabulary.

`DECISION_RULES.md`'s matrix assigns pre-revenue biotech risk-adjusted NPV or a
decision tree as its primary models -- and `INDUSTRY_ADAPTERS.md` repeats them. A full search of the
Cerebro finds every one of those only as a NAME in those two tables: no id, no
formula text, no required inputs, no validation test in any FORMULAS.md. Only
`RSK-RUN-015` (cash runway) exists, and it is the secondary check.

REITs took this path too until their matrix row's two *secondary* checks became
reachable -- see `test_valuation_reit_adapter.py`. The primary three are still
unregistered, so a REIT is still INCOMPLETE; it just no longer reports nothing.

So the refusal is right, and writing FFO ourselves would be choosing a
definition Cerebro has not made -- FFO alone differs between the NAREIT
definition and an adjusted one. What was wrong is how the refusal read:

- `status="ERROR"` said something broke. Nothing did.
  `SCORING_ENGINE.md` has a word for this exact state -- INCOMPLETE, "may be
  displayed but cannot pass a profile gate" -- and a documented methodology gap
  and a broken run call for opposite actions.
- dimensions carried empty score lists, where `MISSING_DATA_POLICY.md` step 1
  says "if no, use NOT_APPLICABLE and invoke the industry adapter". An absent
  dimension and one that does not apply read the same from outside.
- `ADAPTER_UNSUPPORTED` named nothing, so a reader could not tell whether the
  fix was data (Entradas) or methodology (Victor).
"""

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.deep import is_unscored
from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"
_CEREBRO = Path(__file__).parent.parent.parent / "Cerebro"


def _packet(adapter: str) -> Packet:
    data = json.loads(_FIXTURE.read_text())
    data["analysis"]["industry_adapter"] = adapter
    return Packet.model_validate(data)


@pytest.mark.parametrize("adapter", ["biotech"])
def test_status_is_incomplete_not_error(adapter):
    """SCORING_ENGINE.md's own word for below-70% valid dimension weight."""
    assert val.run(_packet(adapter), {}).status == "INCOMPLETE"


@pytest.mark.parametrize("adapter", ["biotech"])
def test_every_dimension_is_explicitly_not_applicable(adapter):
    """MISSING_DATA_POLICY.md step 1. An empty score list says nothing; a
    NOT_APPLICABLE says the dimension does not apply to this company type."""
    out = val.run(_packet(adapter), {})
    assert out.dimensions
    for d in out.dimensions:
        assert d.metric_scores, f"{d.name} carries no state at all"
        for _, v in d.metric_scores:
            assert v.is_null and v.state is NullState.NOT_APPLICABLE


@pytest.mark.parametrize("adapter", ["biotech"])
def test_the_zero_is_still_unscored_not_a_zero_on_merit(adapter):
    """The distinction `is_unscored()` exists to preserve, kept across the
    change from empty dimensions to NOT_APPLICABLE ones."""
    out = val.run(_packet(adapter), {})
    assert out.category.awarded_points == 0.0
    assert is_unscored(out) is True


@pytest.mark.parametrize("adapter", ["biotech"])
def test_the_gate_ineligibility_is_flagged(adapter):
    out = val.run(_packet(adapter), {})
    assert "CATEGORY_INCOMPLETE_GATE_INELIGIBLE" in out.mandatory_flags


def test_the_biotech_matrix_row_is_named(adapter="biotech"):
    note = " ".join(val.run(_packet(adapter), {}).assumptions)
    for model in ("RISK_ADJUSTED_NPV", "DECISION_TREE"):
        assert model in note


@pytest.mark.parametrize("adapter", ["biotech"])
def test_the_gap_is_named_as_methodology_not_data(adapter):
    """The actionable distinction: an analyst cannot fix this in Entradas, and
    telling them to try wastes their time."""
    note = " ".join(val.run(_packet(adapter), {}).assumptions)
    assert "no entry in any" in note and "FORMULAS.md" in note
    assert "not a data gap" in note


@pytest.mark.parametrize("adapter", ["biotech"])
def test_no_value_is_invented(adapter):
    """The refusal itself, unchanged: no metric row, no fabricated model."""
    out = val.run(_packet(adapter), {})
    assert out.metrics == []
    assert out.model_selection.primary == []


def test_the_claim_that_cerebro_never_registers_these_still_holds():
    """This is the fact the whole behaviour rests on, so it is checked rather
    than trusted: if Victor registers FFO/AFFO/NAV/cap-rate/risk-adjusted NPV
    later, this test fails and the refusal should be revisited."""
    import re

    registered = ""
    for d in _CEREBRO.iterdir():
        f = d / "FORMULAS.md"
        if f.is_file():
            registered += f.read_text(encoding="utf-8")
    ids_and_names = registered.lower()
    for absent in ("ffo", "affo", "cap rate", "cap-rate", "ebitdare",
                   "risk-adjusted npv", "phase-transition"):
        assert absent not in ids_and_names, (
            f"{absent!r} now appears in a FORMULAS.md -- the refusal in "
            "_unsupported_adapter_output may no longer be correct"
        )
    # And the one that IS registered, so the test cannot pass by accident.
    assert re.search(r"RSK-RUN-015\s*\|\s*Cash runway", registered)
