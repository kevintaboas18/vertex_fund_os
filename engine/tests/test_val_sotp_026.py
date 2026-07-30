"""VAL-SOTP-026: sum-of-the-parts equity value.

FORMULAS.md: "sum(Segment enterprise/equity values) + corporate assets -
corporate claims - holding discount_if_justified", noted "avoid applying a
multiple to consolidated metrics twice". DECISION_RULES.md's matrix makes it
the primary model for a multi-segment conglomerate and names what it replaces:
"one blended multiple without segment logic".

That caution is the whole design constraint. The segments and their revenue
are reported data (FMP segmentation: AAPL splits into iPhone, Service,
Wearables, Mac, iPad); what each segment is *worth* is not. Substituting the
company's own multiple across all of them would be precisely the blended
multiple the matrix rejects, so a missing per-segment multiple is MISSING with
the remedy named, never filled in.
"""

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.engines import valuation_engine as ve
from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

_SEGMENTS = {"iPhone": 209_586_000_000.0, "Service": 109_158_000_000.0,
             "Wearables": 35_686_000_000.0, "Mac": 33_708_000_000.0,
             "iPad": 28_023_000_000.0}


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def _rows(out):
    return {m.metric_id: m for m in out.metrics}


# --- the formula ------------------------------------------------------------

def test_formula_sums_segments_and_bridges_to_equity():
    """(100 + 200) + 50 cash - 80 debt = 270."""
    v = ve.sum_of_the_parts([100.0, 200.0], corporate_assets=50.0, corporate_claims=80.0)
    assert v.value == pytest.approx(270.0)


def test_holding_discount_applies_to_the_summed_segments_only():
    """Victor's term sits on the sum of the parts, not on the cash bridge --
    a conglomerate discount is about the parts being held together."""
    v = ve.sum_of_the_parts([100.0, 200.0], 50.0, 80.0, holding_discount=0.10)
    assert v.value == pytest.approx(300.0 * 0.90 + 50.0 - 80.0)


def test_no_discount_by_default():
    """"_if_justified": an unjustified conglomerate discount is a thumb on the
    scale rather than a valuation."""
    a = ve.sum_of_the_parts([100.0, 200.0], 50.0, 80.0)
    b = ve.sum_of_the_parts([100.0, 200.0], 50.0, 80.0, holding_discount=0.0)
    assert a.value == pytest.approx(b.value)


def test_a_single_segment_is_not_applicable():
    """A one-segment SOTP is a consolidated valuation under another name --
    the double-count the caution is about."""
    v = ve.sum_of_the_parts([100.0], 50.0, 80.0)
    assert v.is_null and v.state is NullState.NOT_APPLICABLE


def test_a_discount_outside_zero_to_one_refuses():
    for bad in (-0.1, 1.0, 1.5):
        v = ve.sum_of_the_parts([100.0, 200.0], 50.0, 80.0, holding_discount=bad)
        assert v.is_null and v.state is NullState.NOT_MEANINGFUL


# --- specialist wiring ------------------------------------------------------

def test_row_is_emitted(packet):
    assert "VAL-SOTP-026" in _rows(val.run(packet, {}))


def test_segments_without_multiples_are_missing_with_the_remedy_named(packet):
    out = val.run(packet, {"segment_revenue": _SEGMENTS})
    row = _rows(out)["VAL-SOTP-026"]
    assert row.value is None and row.state is NullState.MISSING
    assert any("Entradas/<TICKER>.json" in w for w in row.warnings)


def test_no_company_multiple_is_substituted(packet):
    """The matrix's rejected model for this company type is "one blended
    multiple without segment logic". A partial multiple set must not borrow
    another segment's."""
    partial = {"iPhone": 4.0, "Service": 8.0}  # three segments unpriced
    out = val.run(packet, {"segment_revenue": _SEGMENTS, "segment_multiples": partial})
    row = _rows(out)["VAL-SOTP-026"]
    assert row.value is None
    assert any("blended-multiple" in w for w in row.warnings)


def test_a_complete_multiple_set_produces_a_value(packet):
    multiples = {k: 4.0 for k in _SEGMENTS}
    out = val.run(packet, {"segment_revenue": _SEGMENTS, "segment_multiples": multiples})
    row = _rows(out)["VAL-SOTP-026"]
    assert row.value is not None
    assert row.value > 0


def test_each_segment_carries_its_own_multiple(packet):
    """The point of the model: raising one segment's multiple must move the
    total by that segment's revenue alone."""
    base = {k: 4.0 for k in _SEGMENTS}
    bumped = {**base, "Service": 5.0}
    a = _rows(val.run(packet, {"segment_revenue": _SEGMENTS, "segment_multiples": base}))
    b = _rows(val.run(packet, {"segment_revenue": _SEGMENTS, "segment_multiples": bumped}))
    delta = b["VAL-SOTP-026"].value - a["VAL-SOTP-026"].value
    assert delta == pytest.approx(_SEGMENTS["Service"] * 1.0)


def test_the_reported_segments_are_disclosed(packet):
    """An analyst asked for per-segment multiples needs to know the segments."""
    out = val.run(packet, {"segment_revenue": _SEGMENTS})
    note = [a for a in out.assumptions if "VAL-SOTP-026" in a]
    assert note
    for name in _SEGMENTS:
        assert name in note[0]


def test_row_is_reported_not_scored(packet):
    out = val.run(packet, {"segment_revenue": _SEGMENTS,
                           "segment_multiples": {k: 4.0 for k in _SEGMENTS}})
    assert _rows(out)["VAL-SOTP-026"].score == "NOT_SCORABLE"
