"""VAL-SCEN-036's weighted value must reach a row.

`06_valuation_analysis/DECISION_RULES.md`: "Probabilities sum to 100%. The
main report shows each value and the weighted value; it does not show only the
average." The scenario engine computed `weighted_value`, the specialist read it
into a local, and nothing ever read that local -- so the one figure the rule
names by name existed for two statements and then vanished. The per-scenario
values reached `scenarios`; only the weighted figure was lost.

The Fair-value dimension's *score* is unaffected: it comes from the
price-vs-scenario read already disclosed in `_SCORED_WITHOUT_ROW`, so this row
carries `score=NOT_SCORABLE` and cannot double-count the dimension.
"""

import sys
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "fixtures" / "packet"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

from make_packet_fixture import FIXED_NOW, make_default_providers  # noqa: E402

from wbj.packet.builder import build_packet  # noqa: E402
from wbj.specialists import valuation as val  # noqa: E402


@pytest.fixture
def packet():
    return build_packet("NVDA", make_default_providers(FIXED_NOW), now=FIXED_NOW)


@pytest.fixture
def overlay():
    """Enough for the scenario engine to run: it needs a WACC."""
    return {"wacc": 0.09}


def _rows(out):
    return {m.metric_id: m for m in out.metrics}


def test_val_scen_036_is_emitted(packet, overlay):
    out = val.run(packet, overlay)
    assert "VAL-SCEN-036" in _rows(out), (
        "DECISION_RULES.md requires the weighted value in the report; "
        "no row means it never gets there"
    )


def test_weighted_value_matches_the_scenario_probabilities(packet, overlay):
    """sum(p_i * v_i) over the emitted scenarios, per the formula."""
    out = val.run(packet, overlay)
    row = _rows(out)["VAL-SCEN-036"]
    if row.value is None:
        pytest.skip("scenarios unavailable in this fixture; null row covered elsewhere")
    expected = sum(s.probability * s.per_share_value for s in out.scenarios
                   if s.per_share_value is not None)
    assert row.value == pytest.approx(expected, rel=1e-6)


def test_row_is_not_scored_so_the_dimension_is_not_double_counted(packet, overlay):
    out = val.run(packet, overlay)
    assert _rows(out)["VAL-SCEN-036"].score == "NOT_SCORABLE"


def test_row_is_emitted_without_an_overlay_and_is_well_formed(packet):
    """Present with no overlay at all (the specialist derives its own WACC),
    and OUTPUT_CONTRACT.md-shaped: exactly one of `value`/`state`, so a
    weighted value that cannot be computed reads as MISSING, not as absent."""
    row = _rows(val.run(packet, {})).get("VAL-SCEN-036")
    assert row is not None, "the row must exist even when it has no number"
    assert (row.value is None) != (row.state is None)
    assert row.unit == "usd_per_share"
