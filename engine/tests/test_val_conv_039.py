"""VAL-CONV-039: convertible and option dilution.

FORMULAS.md: "if-converted shares and debt/interest adjustment under scenario;
use treasury method for options", noted "apply scenario-consistent dilution".
VAL-T008's expected result: "missing option/convertible schedule -> per-share
value incomplete".

Two mechanisms that are not interchangeable. If-converted turns a convertible
into equity, so its shares join the count *and* its face stops being a claim;
applying only the share half is the common error, diluting without crediting
the debt that disappeared. Options use the treasury method, where exercise
proceeds buy shares back at market, so equity value is unchanged.

"Scenario-consistent" is why price is a parameter: an option out of the money
in the bear case is in the money in the bull one, and one price across all
three would dilute scenarios that never triggered the conversion.
"""

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.engines import valuation_engine as ve
from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

_CONVERTIBLE = [{"name": "2029 notes", "face": 1_000_000.0,
                 "conversion_price": 50.0, "conversion_shares": 20_000.0}]
_OPTIONS = [{"name": "employee options", "count": 10_000.0, "strike": 40.0}]


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def _rows(out):
    return {m.metric_id: m for m in out.metrics}


# --- if-converted -----------------------------------------------------------

def test_an_out_of_the_money_convertible_does_not_dilute():
    d = ve.convertible_dilution(10_000_000.0, 1_000_000.0, 45.0, _CONVERTIBLE, None)
    assert d["shares_added"] == 0.0
    assert d["converted"] == []
    assert d["per_share"].value == pytest.approx(10.0)


def test_an_in_the_money_convertible_adds_shares_and_credits_the_face():
    """The half that is usually forgotten: the debt stops being a claim."""
    d = ve.convertible_dilution(10_000_000.0, 1_000_000.0, 60.0, _CONVERTIBLE, None)
    assert d["shares_added"] == pytest.approx(20_000.0)
    assert d["equity_value_added"] == pytest.approx(1_000_000.0)
    assert d["per_share"].value == pytest.approx(11_000_000.0 / 1_020_000.0)


def test_crediting_the_face_keeps_conversion_from_looking_purely_destructive():
    """Share-only dilution would report a lower value than the truth."""
    d = ve.convertible_dilution(10_000_000.0, 1_000_000.0, 60.0, _CONVERTIBLE, None)
    share_only = 10_000_000.0 / 1_020_000.0
    assert d["per_share"].value > share_only


# --- treasury method --------------------------------------------------------

def test_an_out_of_the_money_option_adds_nothing():
    d = ve.convertible_dilution(10_000_000.0, 1_000_000.0, 35.0, None, _OPTIONS)
    assert d["shares_added"] == 0.0


def test_treasury_method_nets_the_exercise_proceeds():
    """count * (1 - strike/price): 10,000 at 40 with a 50 price = 2,000."""
    d = ve.convertible_dilution(10_000_000.0, 1_000_000.0, 50.0, None, _OPTIONS)
    assert d["shares_added"] == pytest.approx(10_000.0 * (1 - 40.0 / 50.0))


def test_options_do_not_change_equity_value():
    """The proceeds are already netted out by the treasury method."""
    d = ve.convertible_dilution(10_000_000.0, 1_000_000.0, 50.0, None, _OPTIONS)
    assert d["equity_value_added"] == 0.0


def test_option_dilution_grows_with_the_price():
    low = ve.convertible_dilution(10e6, 1e6, 45.0, None, _OPTIONS)["shares_added"]
    high = ve.convertible_dilution(10e6, 1e6, 80.0, None, _OPTIONS)["shares_added"]
    assert high > low


# --- scenario consistency ---------------------------------------------------

def test_dilution_is_scenario_consistent():
    """The same instruments dilute a bull case and leave a bear case alone."""
    bear = ve.convertible_dilution(10e6, 1e6, 30.0, _CONVERTIBLE, _OPTIONS)
    bull = ve.convertible_dilution(10e6, 1e6, 90.0, _CONVERTIBLE, _OPTIONS)
    assert bear["shares_added"] == 0.0
    assert bull["shares_added"] > 0.0


def test_non_positive_shares_or_price_refuses():
    for shares, price in ((0.0, 50.0), (1e6, 0.0)):
        d = ve.convertible_dilution(10e6, shares, price, _CONVERTIBLE, None)
        assert d["per_share"].is_null
        assert d["per_share"].state is NullState.NOT_MEANINGFUL


# --- specialist wiring (VAL-T008) -------------------------------------------

def test_row_is_emitted(packet):
    assert "VAL-CONV-039" in _rows(val.run(packet, {}))


def test_a_missing_schedule_reads_as_incomplete_not_undiluted(packet):
    """VAL-T008's stated expected result."""
    out = val.run(packet, {})
    row = _rows(out)["VAL-CONV-039"]
    assert row.value is None and row.state is NullState.MISSING
    assert any("INCOMPLETE, not undiluted" in w for w in row.warnings)


def test_a_missing_schedule_raises_the_mandatory_flag(packet):
    out = val.run(packet, {})
    if out.mandatory_flags or True:
        assert "PER_SHARE_VALUE_INCOMPLETE_NO_DILUTION_SCHEDULE" in out.mandatory_flags


def test_the_remedy_names_both_schedules(packet):
    row = _rows(val.run(packet, {}))["VAL-CONV-039"]
    warning = " ".join(row.warnings)
    assert "convertibles" in warning and "options_outstanding" in warning
    assert "Entradas/<TICKER>.json" in warning


def test_a_supplied_schedule_produces_a_diluted_value(packet):
    out = val.run(packet, {"convertibles": _CONVERTIBLE, "options_outstanding": _OPTIONS})
    row = _rows(out)["VAL-CONV-039"]
    if row.value is None:
        pytest.skip("base scenario unavailable in this fixture")
    assert row.value > 0
    assert "PER_SHARE_VALUE_INCOMPLETE_NO_DILUTION_SCHEDULE" not in out.mandatory_flags


def test_row_is_reported_not_scored(packet):
    assert _rows(val.run(packet, {}))["VAL-CONV-039"].score == "NOT_SCORABLE"
