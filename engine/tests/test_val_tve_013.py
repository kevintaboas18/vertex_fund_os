"""VAL-TVE-013: exit-multiple terminal value, and the implied multiple.

FORMULAS.md: "Terminal metric * Selected normalized exit multiple", noted
"Cross-check only; multiple must be justified by terminal fundamentals".
Section 6.5 is blunter: "use only as a cross-check ... a current-cycle
multiple cannot be copied blindly into perpetuity."

Two consequences the tests pin down. First, it must never reach the DCF: the
priced value has to be identical whether or not an exit multiple is supplied.
Second, the checklist requires the *implied* exit multiple be shown -- the one
terminal growth, margin, ROIC and WACC already justify -- because that is the
reference a supplied multiple has to be defended against.

`terminal_year_metrics` was extracted from `_constant_growth_per_share` rather
than reimplemented, so the implied multiple divides the same terminal value by
the same terminal metric the DCF actually priced.
"""

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.engines import valuation_engine as ve
from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

_TERMINAL_ARGS = dict(growth=0.08, margin=0.30, wacc_value=0.09, tv_growth=0.025,
                      revenue0=1000.0, tax_rate=0.21, roic_value=0.25, years=5)


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def _rows(out):
    return {m.metric_id: m for m in out.metrics}


def test_formula_is_metric_times_multiple():
    assert ve.exit_multiple_terminal_value(500.0, 12.0).value == pytest.approx(6000.0)


def test_non_positive_metric_refuses():
    """A multiple on a negative EBIT is arithmetic, not a valuation."""
    v = ve.exit_multiple_terminal_value(-500.0, 12.0)
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_non_positive_multiple_refuses():
    v = ve.exit_multiple_terminal_value(500.0, 0.0)
    assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_implied_multiple_inverts_the_terminal_value():
    v = ve.implied_exit_multiple(6000.0, 500.0)
    assert v.value == pytest.approx(12.0)
    assert v.unit == "x"


def test_implied_multiple_round_trips_through_the_formula():
    """Applying the implied multiple reproduces the terminal value it came
    from -- the property that makes it a usable reference."""
    tm = ve.terminal_year_metrics(**_TERMINAL_ARGS)
    implied = ve.implied_exit_multiple(tm["terminal_value"], tm["ebit"]).value
    back = ve.exit_multiple_terminal_value(tm["ebit"], implied).value
    assert back == pytest.approx(tm["terminal_value"], rel=1e-9)


def test_terminal_metrics_come_from_the_priced_path():
    """Extracted, not reimplemented: terminal revenue is revenue0 compounded
    at the scenario growth for the full horizon."""
    tm = ve.terminal_year_metrics(**_TERMINAL_ARGS)
    assert tm["revenue"] == pytest.approx(1000.0 * 1.08 ** 5)
    assert tm["ebit"] == pytest.approx(tm["revenue"] * 0.30)
    assert len(tm["explicit_fcffs"]) == 5


def test_terminal_metrics_refuse_when_growth_reaches_wacc():
    """The same refusal the pricing core makes, not a silent negative."""
    with pytest.raises(ValueError):
        ve.terminal_year_metrics(**{**_TERMINAL_ARGS, "tv_growth": 0.09})


def test_row_is_emitted(packet):
    assert "VAL-TVE-013" in _rows(val.run(packet, {}))


def test_no_supplied_multiple_is_missing_with_the_remedy_named(packet):
    """FORMULAS.md requires the multiple be justified by terminal
    fundamentals, so no peer median is assumed in its place."""
    row = _rows(val.run(packet, {}))["VAL-TVE-013"]
    if row.value is not None:
        pytest.skip("fixture supplies an exit multiple")
    assert row.state is NullState.MISSING
    assert any("Entradas/<TICKER>.json" in w for w in row.warnings)


def test_a_supplied_multiple_is_used(packet):
    out = val.run(packet, {"exit_multiple": 11.0})
    row = _rows(out)["VAL-TVE-013"]
    if row.value is None:
        pytest.skip("scenario inputs unavailable in this fixture")
    assert row.value > 0


def test_the_implied_multiple_is_disclosed(packet):
    """"Terminal-value share and implied exit multiple are shown"."""
    out = val.run(packet, {})
    note = [a for a in out.assumptions if "VAL-TVE-013" in a]
    if not note:
        pytest.skip("scenario inputs unavailable in this fixture")
    assert "implies an exit multiple" in note[0]
    assert "Cross-check only" in note[0]


def test_the_exit_multiple_never_changes_the_priced_value(packet):
    """Section 6.5's "use only as a cross-check", enforced rather than
    trusted: supplying a multiple must not move the DCF."""
    without = val.run(packet, {})
    with_mult = val.run(packet, {"exit_multiple": 25.0})
    a = _rows(without).get("VAL-MOS-040")
    b = _rows(with_mult).get("VAL-MOS-040")
    assert (a.value is None) == (b.value is None)
    if a.value is not None:
        assert a.value == pytest.approx(b.value)
    assert [s.per_share_value for s in without.scenarios] == \
           [s.per_share_value for s in with_mult.scenarios]
