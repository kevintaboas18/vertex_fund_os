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


# --- the unscored metrics that are merely undeclared ----------------------


def _out_with(metrics):
    return SimpleNamespace(metrics=metrics)


def _metric(metric_id, warnings, state=None):
    from wbj.core.nullstates import NullState

    return SimpleNamespace(metric_id=metric_id, warnings=warnings,
                           state=state or NullState.NOT_SCORABLE)


def test_the_gaps_an_analyst_can_close_are_listed_as_work():
    """Six dimensions come back NOT_SCORABLE on a typical run and the
    report said only that. Most are inputs `Entradas/<TICKER>.json` is
    designed to carry — nobody reads 117 metric rows to discover that."""
    from wbj.report import _analyst_input_gaps

    gaps = _analyst_input_gaps({
        "market": _out_with([
            _metric("MKT-CAT-019", ["CATALYST_REGISTRY_UNAVAILABLE"]),
            _metric("MKT-SAM-002", ["SAM_UNAVAILABLE"]),
        ]),
        "valuation": _out_with([
            _metric("VAL-TVE-013", ["EXIT_MULTIPLE_NOT_SUPPLIED: set `exit_multiple` "
                                    "(EV/EBIT on the terminal year) in Entradas/X.json"]),
        ]),
    }, "NVDA")

    assert len(gaps) == 1
    note = gaps[0]
    assert "Entradas/NVDA.json" in note
    assert "catalysts" in note and "sam_inputs" in note
    # Read straight out of the warning when the warning already names it.
    assert "exit_multiple" in note
    assert "the engine will not impute them" in note


def test_a_metric_that_does_not_apply_is_not_a_gap():
    """Subscription metrics on a chip maker are NOT_APPLICABLE, and
    `Category.points()` already rescales them away rather than charging for
    them. Listing them as work would send the reader to invent figures the
    business does not have."""
    from wbj.core.nullstates import NullState
    from wbj.report import _analyst_input_gaps

    assert _analyst_input_gaps({
        "business": _out_with([
            _metric("BUS-NRR-020", ["NRR_UNAVAILABLE_NOT_A_SUBSCRIPTION_BUSINESS"],
                    state=NullState.NOT_APPLICABLE),
        ]),
    }, "NVDA") == []


def test_one_key_serving_several_metrics_is_counted_once():
    from wbj.report import _analyst_input_gaps

    note = _analyst_input_gaps({
        "financial": _out_with([
            _metric("FIN-GR-005", ["MARKET_SHARE_SERIES_UNAVAILABLE_JUDGMENT_REQUIRED"]),
            _metric("MKT-SHDELTA-007", ["MARKET_SHARE_DELTA_UNAVAILABLE"]),
        ]),
    }, "NVDA")[0]
    assert "1 key(s)" in note
    assert "share_history (2 metrics)" in note


def test_the_keys_are_ranked_by_how_much_they_unlock():
    from wbj.report import _analyst_input_gaps

    note = _analyst_input_gaps({
        "market": _out_with([
            _metric("A-1", ["SAM_UNAVAILABLE"]),
            _metric("B-1", ["CATALYST_REGISTRY_UNAVAILABLE"]),
            _metric("B-2", ["CATALYST_REGISTRY_UNAVAILABLE"]),
        ]),
    }, "NVDA")[0]
    assert note.index("catalysts") < note.index("sam_inputs")


def test_nothing_missing_means_no_work_list():
    from wbj.report import _analyst_input_gaps

    assert _analyst_input_gaps({"market": _out_with([])}, "NVDA") == []
    assert _analyst_input_gaps({}, "NVDA") == []


def test_a_metric_the_analyst_researched_is_not_asked_for_again(tmp_path):
    """`Entradas/<TICKER>.json` carries notes recording the OUTCOME of
    research. NVDA's says "MKT-SHARE-006 y MKT-SHDELTA-007 quedan
    NOT_SCORABLE tras research", because Gartner's TAM measures end-user
    spend while NVDA sells upstream to OEM/ODM — dividing one by the other
    compares different layers of the chain.

    The work list asked for exactly those, turning a settled finding into
    a recurring chore."""
    import json

    from wbj.report import _analyst_input_gaps, _researched_and_declared

    entradas = tmp_path / "Entradas"
    entradas.mkdir()
    (entradas / "TEST.json").write_text(json.dumps({
        "_share_no_scorable": "MKT-SHARE-006 queda NOT_SCORABLE tras research.",
        "_fuente": "Gartner, MKT-SAM-002 se cita aqui solo como procedencia",
        "tam": 1_000,
    }), encoding="utf-8")
    settings = SimpleNamespace(repo_root=tmp_path)

    declared = _researched_and_declared(settings, "TEST")
    assert declared == {"MKT-SHARE-006": "_share_no_scorable"}

    gaps = _analyst_input_gaps({
        "market": _out_with([
            _metric("MKT-SHARE-006", ["MARKET_SHARE_UNAVAILABLE_PROHIBITED_IMPUTATION: "
                                      "set `share` = {company_sales, total_market_sales}"]),
            _metric("MKT-SAM-002", ["SAM_UNAVAILABLE"]),
        ]),
    }, "TEST", settings)

    pendiente = next(g for g in gaps if "would score metrics" in g)
    resuelto = next(g for g in gaps if "researched and declared" in g)
    # La investigada sale de la lista de trabajo...
    assert "share" not in pendiente
    assert "sam_inputs" in pendiente
    # ...y se reporta como hallazgo cerrado, no como tarea.
    assert "MKT-SHARE-006" in resuelto
    assert "not pending work" in resuelto


def test_a_note_that_is_only_provenance_declares_nothing():
    """El archivo lleva citas y fuentes además de decisiones. Una nota que
    menciona un metric id sin declarar una ausencia no lo silencia."""
    import json
    import tempfile
    from pathlib import Path

    from wbj.report import _researched_and_declared

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "Entradas").mkdir()
        (root / "Entradas" / "TEST.json").write_text(json.dumps({
            "_fuente": "Gartner. Alimenta MKT-TAM-001 y MKT-SAM-002.",
        }), encoding="utf-8")
        assert _researched_and_declared(SimpleNamespace(repo_root=root), "TEST") == {}


def test_no_entradas_file_declares_nothing():
    from pathlib import Path

    from wbj.report import _researched_and_declared

    assert _researched_and_declared(SimpleNamespace(repo_root=Path("/no/existe")), "TEST") == {}
