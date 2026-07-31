"""The report's two price answers, and the table row that judged them.

A report carries a discounted-cash-flow value (5-year intrinsic worth,
`valuation_scenarios` and the football-field chart) and a 12-month price
target (today's P/E carried forward on earnings growth, the scenario-fan
chart). On NVDA they read $111 and $273 in one document — one 43% below
the price, one 40% above — with nothing saying they answer different
questions.

CONTRADICTION_RESOLUTION.md rule 5: "If the contradiction is economic
rather than data-driven, show both views and name the condition that would
resolve it."

And its table's sixth row — "DCF high, reverse DCF demanding | Model
assumptions may be optimistic | Lower valuation confidence" — was fully
implemented in `contradictions()` and never reachable: `run_report` called
it without the `reverse_dcf` context the row needs, so the six-row table
was five rows deep in practice.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wbj.aggregate.contradiction import CategoryScore10s, ReverseDCFContext, contradictions
from wbj.report import _price_view_divergence, _reverse_dcf_context


def _valuation(base_per_share, growth=0.36, implied=None):
    return SimpleNamespace(
        scenarios=[SimpleNamespace(name="Base", per_share_value=base_per_share,
                                   assumptions={"growth": growth})],
        reverse_dcf=SimpleNamespace(implied_revenue_cagr=implied),
    )


def _targets(base_target):
    return {"status": "ok",
            "scenarios": [{"key": "base", "target": base_target}]}


# --- rule 5: show both views, name the condition --------------------------


def test_opposite_views_are_stated_together():
    gaps = _price_view_divergence(_valuation(111.38), _targets(273.06), 195.04)
    assert len(gaps) == 1
    note = gaps[0]

    # Both numbers, both distances, and which question each answers.
    assert "111.38" in note and "273.06" in note
    assert "-43%" in note and "+40%" in note
    assert "5-year DCF" in note and "12-month target" in note
    # And the condition that would resolve it, per rule 5.
    assert "whether that multiple persists" in note


def test_agreeing_views_need_no_reconciliation():
    """Both below the price, or both above it, is one message — not a
    contradiction to explain away."""
    assert _price_view_divergence(_valuation(120.0), _targets(150.0), 195.04) == []
    assert _price_view_divergence(_valuation(250.0), _targets(280.0), 195.04) == []


def test_it_fires_in_either_direction():
    """A DCF above the price with a target below it is the same defect
    seen from the other side."""
    gaps = _price_view_divergence(_valuation(260.0), _targets(150.0), 195.04)
    assert len(gaps) == 1


@pytest.mark.parametrize("valuation,targets,price", [
    (_valuation(111.38), _targets(273.06), None),                 # no price
    (_valuation(111.38), _targets(273.06), 0.0),                  # no price
    (_valuation(111.38), None, 195.04),                           # targets failed
    (_valuation(111.38), {"status": "not_scorable"}, 195.04),     # targets refused
    (_valuation(None), _targets(273.06), 195.04),                 # no DCF base
    (SimpleNamespace(scenarios=[], reverse_dcf=None), _targets(273.06), 195.04),
    (_valuation(111.38), {"status": "ok", "scenarios": []}, 195.04),
])
def test_it_abstains_when_a_view_is_missing(valuation, targets, price):
    """One view is not a disagreement."""
    assert _price_view_divergence(valuation, targets, price) == []


# --- the sixth row was unreachable ----------------------------------------


def _flat(**kw):
    base = dict(business=5.0, financial=5.0, market=5.0,
                technical=5.0, risk=5.0, valuation=5.0)
    base.update(kw)
    return CategoryScore10s(**base)


def test_row_six_fires_when_the_context_is_supplied():
    """Implemented all along; `run_report` just never handed it the
    context, so the row could not fire on any company."""
    ctx = ReverseDCFContext(base_case_upside_pct=0.35,
                            reverse_dcf_implied_growth=0.55,
                            reference_growth=0.36)
    labels = [c.label for c in contradictions(_flat(), 50.0, reverse_dcf=ctx)]
    assert "Lower valuation confidence" in labels


def test_without_the_context_the_row_cannot_fire():
    """The regression, stated as the behaviour it caused."""
    labels = [c.label for c in contradictions(_flat(), 50.0)]
    assert "Lower valuation confidence" not in labels


def test_a_cheap_dcf_is_not_the_row_six_case():
    """Row 6 is "DCF HIGH, reverse DCF demanding". A DCF below the price
    agreeing with a demanding reverse DCF is coherent, not contradictory —
    both say expensive."""
    ctx = ReverseDCFContext(base_case_upside_pct=-0.43,
                            reverse_dcf_implied_growth=0.55,
                            reference_growth=0.36)
    labels = [c.label for c in contradictions(_flat(), 50.0, reverse_dcf=ctx)]
    assert "Lower valuation confidence" not in labels


def test_the_context_is_read_off_the_frozen_output():
    ctx = _reverse_dcf_context(_valuation(260.0, growth=0.36, implied=0.55), 195.04)
    assert ctx.base_case_upside_pct == pytest.approx(260.0 / 195.04 - 1)
    assert ctx.reverse_dcf_implied_growth == pytest.approx(0.55)
    # The reference is the base case's OWN growth assumption — now the
    # consensus forecast, which is what "demanding" should be measured
    # against.
    assert ctx.reference_growth == pytest.approx(0.36)


def test_the_context_survives_a_valuation_that_did_not_run():
    ctx = _reverse_dcf_context(SimpleNamespace(scenarios=[], reverse_dcf=None), None)
    assert ctx.base_case_upside_pct is None
    assert ctx.reverse_dcf_implied_growth is None
    # And it is inert rather than fatal.
    assert contradictions(_flat(), 50.0, reverse_dcf=ctx) == []
