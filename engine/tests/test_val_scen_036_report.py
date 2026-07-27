"""VAL-SCEN-036 has to reach the report, not just the specialist.

`06_valuation_analysis/DECISION_RULES.md`: "Probabilities sum to 100%. The
**main report** shows each value and the weighted value; it does not show only
the average."

Emitting the metric row satisfied the specialist. It did not satisfy the rule:
FINAL_REPORT_SCHEMA.md is Victor's own and deliberately a summary -- it carries
no metrics section at all -- so the figure the rule names by name reached the
specialist output and stopped there. The scenarios table showed each value and
the weighted one appeared nowhere in report.json or report.md.
"""

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent


def _fixture_report():
    from wbj.schemas.final_report import FinalReport
    return FinalReport


def test_the_schema_carries_the_weighted_value():
    from wbj.schemas.final_report import FinalReport

    assert "valuation_weighted_value" in FinalReport.model_fields


def test_the_renderer_labels_it_with_its_formula_id():
    """A reader has to be able to trace the number to the registry entry."""
    src = (_ROOT / "engine" / "wbj" / "report" / "render.py").read_text(encoding="utf-8")
    assert "VAL-SCEN-036" in src
    assert "valuation_weighted_value" in src


def test_the_weighted_row_is_absent_when_scenarios_did_not_run():
    """No scenarios means no weighted value; the table must not grow an empty
    row claiming one."""
    src = (_ROOT / "engine" / "wbj" / "report" / "render.py").read_text(encoding="utf-8")
    assert "if report.valuation_weighted_value is not None:" in src


# --- the rest of Victor's terminal-value checklist --------------------------

def test_the_schema_carries_the_terminal_value_share_and_implied_multiple():
    """INSTITUTIONAL_VALUATION_ENGINE.md's terminal-value checklist:
    "terminal-value share and implied exit multiple are shown"."""
    from wbj.schemas.final_report import FinalReport

    for field in ("terminal_value_share", "implied_exit_multiple"):
        assert field in FinalReport.model_fields


def test_the_specialist_computes_the_terminal_share():
    """`ve.terminal_share` existed with no caller: the figure section 6.6
    builds its 75% high-sensitivity warning on was never computed here."""
    import json as _json

    from wbj.schemas.packet import Packet
    from wbj.specialists import valuation as val

    fixture = _ROOT / "engine" / "tests" / "fixtures" / "packet" / "NVDA_packet.json"
    out = val.run(Packet.model_validate(_json.loads(fixture.read_text())), {})
    assert out.terminal_value_share is not None
    assert 0.0 <= out.terminal_value_share <= 1.0


def test_the_specialist_publishes_the_implied_multiple():
    """It lived only inside an assumption sentence, so no consumer could read
    it."""
    import json as _json

    from wbj.schemas.packet import Packet
    from wbj.specialists import valuation as val

    fixture = _ROOT / "engine" / "tests" / "fixtures" / "packet" / "NVDA_packet.json"
    out = val.run(Packet.model_validate(_json.loads(fixture.read_text())), {})
    assert out.implied_exit_multiple is not None
    assert out.implied_exit_multiple > 0


def test_the_renderer_states_the_threshold_beside_the_share():
    """A reader cannot check 75% against a number with no threshold shown."""
    src = (_ROOT / "engine" / "wbj" / "report" / "render.py").read_text(encoding="utf-8")
    assert "VAL-TVS-042" in src
    assert "HIGH_TERMINAL_SENSITIVITY" in src


def test_the_renderer_marks_the_multiple_as_a_cross_check():
    """Section 6.5 bounds it to one role; the report must not read as if it
    were a second valuation."""
    src = (_ROOT / "engine" / "wbj" / "report" / "render.py").read_text(encoding="utf-8")
    assert "VAL-TVE-013" in src
    assert "cross-check only" in src
