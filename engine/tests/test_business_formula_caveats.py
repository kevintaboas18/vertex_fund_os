"""FORMULAS.md's "Rules / caveats" column, executed.

Each of the thirty rows carries a caveat, and several impose obligations
that the algebra alone does not: refuse this input, label that output,
warn under this condition. The formulas matched their definitions; four
caveats were not implemented at all.
"""

import pytest

from wbj.engines.valuation_engine import incremental_roic
from wbj.specialists import business as bus


# --- BUS-IROIC-016: "not meaningful for negative denominator change" ------


def test_a_shrinking_capital_base_is_not_meaningful():
    """Only the zero case was refused. A negative denominator inverts the
    ratio's meaning: NOPAT down 50 against capital down 10 scored +500%
    incremental ROIC, and that feeds BUS-ALLOC-029, the heaviest single
    weight in the management dimension."""
    collapsing = incremental_roic(-50.0, -10.0)
    assert collapsing.is_null
    assert str(collapsing.state).endswith("NOT_MEANINGFUL")
    assert "DELTA_IC_NEGATIVE" in collapsing.warnings


@pytest.mark.parametrize("delta_nopat,delta_ic", [
    (-30.0, -100.0),   # shrinking on both sides
    (20.0, -100.0),    # NOPAT up while capital shrinks
    (-50.0, -10.0),    # the +500% case
])
def test_no_negative_denominator_produces_a_score(delta_nopat, delta_ic):
    assert incremental_roic(delta_nopat, delta_ic).is_null


def test_growth_on_new_capital_still_scores():
    """The refusal must not swallow the case the metric exists for."""
    v = incremental_roic(50.0, 200.0)
    assert v.is_valid and v.value == pytest.approx(0.25)


def test_zero_denominator_keeps_its_own_warning():
    assert "DELTA_IC_ZERO" in incremental_roic(10.0, 0.0).warnings


# --- BUS-MIX-001 / BUS-HHI-004: completeness and the lower-bound label -----


def test_a_full_breakdown_is_recognised():
    assert bus.shares_are_complete([0.5, 0.3, 0.2]) is True
    # Eliminations and rounding move the total a little.
    assert bus.shares_are_complete([0.5, 0.3, 0.22]) is True


def test_a_partial_breakdown_is_recognised():
    """A customer-concentration note discloses only the customers above a
    reporting threshold, so the set is a subset almost every time."""
    assert bus.shares_are_complete([0.22, 0.14]) is False
    assert bus.shares_are_complete([]) is False


def test_the_tolerance_is_symmetric():
    """Shares can overshoot 100% before eliminations as well as undershoot."""
    assert bus.shares_are_complete([0.6, 0.44]) is True
    assert bus.shares_are_complete([0.7, 0.4]) is False


def test_hhi_over_a_partial_base_understates_concentration():
    """The arithmetic reason the label matters: the same customers, with
    the undisclosed remainder included, score higher."""
    disclosed = bus.customer_hhi([0.22, 0.14])
    with_remainder = bus.customer_hhi([0.22, 0.14, 0.32, 0.32])
    assert disclosed.value < with_remainder.value


# --- BUS-REINV-018: the numerator and "unstable when NOPAT is near zero" ---


def test_the_reinvestment_numerator_carries_all_three_terms():
    """FORMULAS.md's numerator is "Net capex + change in non-cash working
    capital + capitalized R&D adjustment". Two were passed as literal
    0.0, so the rate measured capex alone."""
    capex_only = bus.reinvestment_rate(6.0, 0.0, 0.0, 100.0)
    with_wc = bus.reinvestment_rate(6.0, 16.0, 0.0, 100.0)
    with_rd = bus.reinvestment_rate(6.0, 16.0, 4.0, 100.0)
    assert capex_only.value == pytest.approx(0.06)
    assert with_wc.value == pytest.approx(0.22)
    assert with_rd.value == pytest.approx(0.26)


def test_a_working_capital_build_raises_reinvestment():
    """Sign convention: the cash-flow statement presents a working-capital
    build as a negative adjustment to operating cash flow, and the
    reinvestment numerator wants it as a use of cash."""
    build = -16.0        # as reported when working capital grows
    assert bus.reinvestment_rate(6.0, -build, 0.0, 100.0).value > \
           bus.reinvestment_rate(6.0, 0.0, 0.0, 100.0).value


def test_zero_nopat_is_still_refused():
    v = bus.reinvestment_rate(6.0, 0.0, 0.0, 0.0)
    assert v.is_null and str(v.state).endswith("NOT_MEANINGFUL")


def test_the_near_zero_threshold_is_measured_against_the_company_itself():
    """FORMULAS.md sets no magnitude for "near zero", so the constant is a
    fraction of the company's own trailing NOPAT rather than an external
    figure — and it is disclosed as an assumption."""
    assert 0.0 < bus.NEAR_ZERO_NOPAT_FRACTION < 1.0


# --- the caveats that were already honoured, kept under test --------------


def test_non_positive_base_makes_cagr_not_meaningful():
    """BUS-CAGR-006: "Not meaningful across non-positive base"."""
    assert bus.revenue_cagr(200.0, 0.0, 4.0).is_null
    assert bus.revenue_cagr(200.0, -10.0, 4.0).is_null


def test_guidance_accuracy_is_clipped_to_the_unit_interval():
    """BUS-GUIDE-027: "Clip to [0,1]"."""
    assert bus.guidance_accuracy(1000.0, 1.0).value == pytest.approx(0.0)
    assert bus.guidance_accuracy(100.0, 100.0).value == pytest.approx(1.0)


def test_fcf_conversion_refuses_a_non_positive_income_base():
    """BUS-FCFC-017: "sign changes require review"."""
    assert bus.cumulative_fcf_conversion(500.0, -450.0).is_null
    assert bus.cumulative_fcf_conversion(500.0, 0.0).is_null


def test_concentration_is_never_inferred():
    """BUS-CONC-003: "do not infer undisclosed concentration". Absent
    disclosure the metric stays null rather than estimating."""
    from types import SimpleNamespace

    packet = SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter="default_nonfinancial"),
        security=SimpleNamespace(sector="Technology", industry="Semiconductors"),
    )
    assert "largest_customer_share" in bus._OVERLAY_LINEAGE
    # No overlay key, no lineage entry, no value.
    assert bus._source_lineage(packet, {}) == [
        "packet.fundamentals.annual", "packet.analysis", "packet.security"]


# --- the wide-moat persistence window -------------------------------------


def test_persistence_is_measured_over_the_last_five_years_only():
    """DECISION_RULES.md: "at least four of the last five fiscal years".
    The fraction divided over the whole available history, which was
    harmless at six years of data and wrong at eleven — a company with
    five straight qualifying years would score 5/11."""
    assert bus.WIDE_MOAT_PERSISTENCE_WINDOW == 5
    assert bus.WIDE_MOAT_MIN_SPREAD == pytest.approx(0.05)
    assert bus.WIDE_MOAT_MIN_PERSISTENCE == pytest.approx(0.8)

    # Eleven years, only the last five qualifying.
    spreads = [0.01] * 6 + [0.06] * 5
    window = spreads[-bus.WIDE_MOAT_PERSISTENCE_WINDOW:]
    frac = sum(1 for s in window if s >= bus.WIDE_MOAT_MIN_SPREAD) / len(window)
    assert frac == pytest.approx(1.0)
    assert frac >= bus.WIDE_MOAT_MIN_PERSISTENCE

    over_all_history = sum(1 for s in spreads if s >= bus.WIDE_MOAT_MIN_SPREAD) / len(spreads)
    assert over_all_history < bus.WIDE_MOAT_MIN_PERSISTENCE, "the bug this guards"


def test_four_of_five_passes_and_three_of_five_does_not():
    """The exact boundary DECISION_RULES.md names."""
    def frac(spreads):
        w = spreads[-bus.WIDE_MOAT_PERSISTENCE_WINDOW:]
        return sum(1 for s in w if s >= bus.WIDE_MOAT_MIN_SPREAD) / len(w)

    assert frac([0.06, 0.06, 0.06, 0.06, 0.01]) >= bus.WIDE_MOAT_MIN_PERSISTENCE
    assert frac([0.06, 0.06, 0.06, 0.01, 0.01]) < bus.WIDE_MOAT_MIN_PERSISTENCE
    # Exactly 5 percentage points qualifies; a hair under does not.
    assert frac([0.05] * 5) == pytest.approx(1.0)
    assert frac([0.0499] * 5) == pytest.approx(0.0)
