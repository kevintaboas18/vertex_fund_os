"""VAL-APV-019 (adjusted present value) and VAL-ROPT-038 (real options).

Both are scoped by DECISION_RULES.md's model-selection matrix to company types
the default FCFF DCF misprices, and both take inputs no provider carries.

- APV is primary for "changing leverage / LBO-like", where the matrix's
  rejected model is "static WACC without debt path". Separating the pieces is
  the point: a WACC folds the tax shield into one discount rate and so assumes
  a capital structure that holds still -- the assumption an LBO breaks.
- ROPT is a secondary check for pre-revenue biotech, admitted "only for
  material, separable, discretionary projects" and "after avoiding DCF double
  count".
"""

import json
import math
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.engines import valuation_engine as ve
from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

_UFCF = [100.0, 110.0, 120.0, 130.0, 140.0]
_DEBT = [1000.0, 800.0, 600.0, 400.0, 200.0]  # a deleveraging path


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def _rows(out):
    return {m.metric_id: m for m in out.metrics}


# --- VAL-APV-019 ------------------------------------------------------------

def test_apv_sums_victors_four_components():
    r = ve.adjusted_present_value(_UFCF, 0.10, _DEBT, 0.21, 0.05,
                                  distress_probability=0.10, distress_cost=500.0,
                                  financing_side_effects=25.0)
    expected = (r["pv_unlevered_operations"] + r["pv_tax_shields"]
                - r["pv_distress"] + r["financing_side_effects"])
    assert r["value"].value == pytest.approx(expected)


def test_unlevered_operations_discount_at_the_unlevered_cost():
    r = ve.adjusted_present_value(_UFCF, 0.10, [], 0.21, 0.05)
    expected = sum(f / 1.10 ** (t + 1) for t, f in enumerate(_UFCF))
    assert r["pv_unlevered_operations"] == pytest.approx(expected)


def test_the_shield_follows_the_debt_path_not_a_single_balance():
    """The whole reason APV exists for changing leverage: a deleveraging
    schedule must be worth less in shields than a flat one."""
    falling = ve.adjusted_present_value(_UFCF, 0.10, _DEBT, 0.21, 0.05)
    flat = ve.adjusted_present_value(_UFCF, 0.10, [1000.0] * 5, 0.21, 0.05)
    assert flat["pv_tax_shields"] > falling["pv_tax_shields"]


def test_the_shield_is_zero_without_debt():
    r = ve.adjusted_present_value(_UFCF, 0.10, [], 0.21, 0.05)
    assert r["pv_tax_shields"] == pytest.approx(0.0)


def test_expected_distress_is_probability_times_cost():
    r = ve.adjusted_present_value(_UFCF, 0.10, _DEBT, 0.21, 0.05,
                                  distress_probability=0.25, distress_cost=800.0)
    assert r["pv_distress"] == pytest.approx(200.0)


def test_a_probability_outside_zero_to_one_refuses():
    for p in (-0.1, 1.5):
        r = ve.adjusted_present_value(_UFCF, 0.10, _DEBT, 0.21, 0.05, distress_probability=p)
        assert r["value"].is_null and r["value"].state is NullState.NOT_MEANINGFUL


def test_terminal_growth_at_the_unlevered_cost_refuses():
    r = ve.adjusted_present_value(_UFCF, 0.10, _DEBT, 0.21, 0.05, terminal_growth=0.10)
    assert r["value"].is_null and r["value"].state is NullState.NOT_MEANINGFUL


def test_apv_row_is_missing_without_a_debt_path(packet):
    row = _rows(val.run(packet, {}))["VAL-APV-019"]
    assert row.value is None and row.state is NullState.MISSING
    assert any("debt_schedule" in w for w in row.warnings)


def test_apv_remedy_explains_why_one_balance_is_not_enough(packet):
    row = _rows(val.run(packet, {}))["VAL-APV-019"]
    assert any("static-WACC" in w for w in row.warnings)


def test_a_supplied_path_produces_a_value(packet):
    out = val.run(packet, {"unlevered_fcf": _UFCF, "debt_schedule": _DEBT,
                           "unlevered_cost": 0.10, "pretax_kd": 0.05})
    assert _rows(out)["VAL-APV-019"].value is not None


def test_a_zero_distress_term_is_disclosed_as_an_assumption(packet):
    """Not supplied is not the same as zero, and the output says which."""
    out = val.run(packet, {"unlevered_fcf": _UFCF, "debt_schedule": _DEBT,
                           "unlevered_cost": 0.10, "pretax_kd": 0.05})
    note = [a for a in out.assumptions if "VAL-APV-019" in a]
    assert note and "an assumption, not an absence" in note[0]


# --- VAL-ROPT-038 -----------------------------------------------------------

def test_real_option_matches_black_scholes():
    s, k, sigma, t, r = 1000.0, 900.0, 0.40, 3.0, 0.04
    st = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + sigma ** 2 / 2) * t) / st
    d2 = d1 - st
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    expected = s * n(d1) - k * math.exp(-r * t) * n(d2)
    assert ve.real_option_value(s, k, sigma, t, r).value == pytest.approx(expected)


def test_an_out_of_the_money_project_still_carries_value():
    """The asymmetry a plain NPV cannot express: a project abandoned when it
    turns out badly loses only the premium."""
    v = ve.real_option_value(500.0, 1000.0, 0.50, 4.0, 0.04)
    assert v.value > 0


def test_more_volatility_is_worth_more():
    low = ve.real_option_value(1000.0, 900.0, 0.20, 3.0, 0.04).value
    high = ve.real_option_value(1000.0, 900.0, 0.60, 3.0, 0.04).value
    assert high > low


def test_more_time_is_worth_more():
    short = ve.real_option_value(1000.0, 900.0, 0.40, 1.0, 0.04).value
    long_ = ve.real_option_value(1000.0, 900.0, 0.40, 5.0, 0.04).value
    assert long_ > short


def test_no_uncertainty_is_not_applicable():
    """A project with no uncertainty has no option premium; reporting one
    would invent optionality the inputs deny."""
    v = ve.real_option_value(1000.0, 900.0, 0.0, 3.0, 0.04)
    assert v.is_null and v.state is NullState.NOT_APPLICABLE


def test_non_positive_pv_investment_or_horizon_refuses():
    for args in ((0.0, 900.0, 0.4, 3.0), (1000.0, 0.0, 0.4, 3.0), (1000.0, 900.0, 0.4, 0.0)):
        v = ve.real_option_value(*args, 0.04)
        assert v.is_null and v.state is NullState.NOT_MEANINGFUL


def test_ropt_row_is_missing_without_a_project(packet):
    row = _rows(val.run(packet, {}))["VAL-ROPT-038"]
    assert row.value is None and row.state is NullState.MISSING
    assert any("separable, discretionary" in w for w in row.warnings)


def test_a_supplied_project_produces_a_value_and_the_double_count_warning(packet):
    out = val.run(packet, {"real_option_project": {
        "project_pv": 1e9, "investment": 8e8, "volatility": 0.45,
        "years": 4.0, "risk_free": 0.042}})
    row = _rows(out)["VAL-ROPT-038"]
    assert row.value is not None and row.value > 0
    note = [a for a in out.assumptions if "VAL-ROPT-038" in a]
    assert note and "double count" in note[0]


def test_both_rows_are_reported_not_scored(packet):
    rows = _rows(val.run(packet, {}))
    assert rows["VAL-APV-019"].score == "NOT_SCORABLE"
    assert rows["VAL-ROPT-038"].score == "NOT_SCORABLE"


def test_the_choice_between_victors_two_methods_is_disclosed():
    """FORMULAS.md offers "option-pricing OR decision-tree". Implementing the
    first is a choice between alternatives Cerebro leaves open, and it is not
    neutral: closed-form pricing assumes one decision point, where a decision
    tree carries staged, path-dependent outcomes this cannot."""
    # Normalised: the assertion is about what the docstring says, not about
    # where its line breaks fall.
    doc = " ".join((ve.real_option_value.__doc__ or "").split())
    assert "decision-tree" in doc
    assert "not implemented here" in doc
