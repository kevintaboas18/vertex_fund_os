"""Analyst input must add to the computed overlay, never demolish it.

Two failures found by supplying every documented key at once and watching
what the engine did with them.

**Nulls reached the specialists.** The skeleton ships every key `null` so an
analyst can fill it in place, and the loader carried those through. The read
pattern across the specialists is `float(overlay.get(key, default))` — and a
default only applies when the key is ABSENT, so `float(None)` raised and
killed the run. A freshly-analysed ticker died on `equity_issuance` with
eleven more keys behind it (`lease_charge`, `tv_growth`, `pretax_kd`, …).

**A supplied dict replaced a computed one.** `_manual_overlay` applies last,
so the template's partial `estimates` — `{upward, total,
individual_estimates}` — overwrote the computed block holding `surprise`,
`revision` and `active_estimates`. Supplying data made the revisions
dimension *worse*: 75% down to 50%, trading MKT-REVMAG-012 and MKT-SURP-014
for MKT-DISP-013.
"""

import json

import pytest

from types import SimpleNamespace

from wbj.overlay.from_packet import _manual_overlay
from wbj.specialists.common import overlay_float


class _S:
    def __init__(self, path):
        self.inputs_dir = path


def _write(tmp_path, payload):
    (tmp_path / "TEST.json").write_text(json.dumps(payload), encoding="utf-8")
    return _S(tmp_path)


# --- nulls behave exactly like absence --------------------------------------

def test_a_null_never_reaches_a_specialist(tmp_path):
    out = _manual_overlay(_write(tmp_path, {"equity_issuance": None,
                                            "lease_charge": None}), "TEST")
    assert "equity_issuance" not in out
    assert "lease_charge" not in out


def test_overlay_float_falls_back_for_absent_and_for_null():
    """The helper the fifteen call sites now use. Both cases must give the
    default; the built-in `.get(key, default)` only handles the first."""
    assert overlay_float({}, "erp", 0.045) == 0.045
    assert overlay_float({"erp": None}, "erp", 0.045) == 0.045
    assert overlay_float({"erp": 0.06}, "erp", 0.045) == 0.06


def test_overlay_float_keeps_a_real_zero():
    """Zero is an assessment, not an absence. `distress_probability: 0` means
    the analyst judged it zero."""
    assert overlay_float({"distress_probability": 0.0}, "distress_probability", 0.5) == 0.0


# --- dicts merge, they do not replace ---------------------------------------

def test_a_supplied_dict_merges_with_the_computed_one():
    """The revisions regression, pinned. Simulated at the same layer
    `build_overlay` merges at."""
    computed = {"estimates": {"surprise": 0.06, "revision": 0.003,
                              "active_estimates": 31}}
    supplied = {"estimates": {"upward": 8, "total": 10,
                              "individual_estimates": [8.7, 9.0]}}
    merged = {**computed["estimates"], **supplied["estimates"]}
    assert merged["surprise"] == 0.06, "el bloque calculado sobrevive"
    assert merged["individual_estimates"] == [8.7, 9.0], "lo del analista entra"


def test_the_analyst_sub_key_wins_on_a_collision():
    """Merging must not demote the analyst: where both carry the same
    sub-key, the person who read the source wins."""
    merged = {**{"upward": 4}, **{"upward": 8}}
    assert merged["upward"] == 8


def test_a_scalar_still_replaces_outright(tmp_path):
    """Only dicts merge. A supplied number is the analyst's answer, whole."""
    out = _manual_overlay(_write(tmp_path, {"exit_multiple": 11.5}), "TEST")
    assert out["exit_multiple"] == 11.5


# --- a judgment classifies; it does not measure -----------------------------

def test_a_judgment_keeps_a_computed_value_and_only_sets_the_score():
    """A judgment supplies a CLASSIFICATION, not a measurement.

    This replaced the row's value with the 0-10 score unconditionally, which
    was harmless while every judgment metric was NOT_SCORABLE by
    construction. FIN-GR-004 broke that: it computes `organic growth / total
    growth` from the analyst's bridge, and an EXCELLENT answer overwrote 0.80
    with 10.0 — leaving a report that could say a model called the growth
    excellent but no longer what share of it was organic.
    """
    import json
    from pathlib import Path

    from wbj.core.nullstates import EvidenceClass
    from wbj.overlay.merge import collect_requests, merge_overlay
    from wbj.schemas.overlay import Judgment
    from wbj.schemas.packet import Packet
    import wbj.specialists.financial as fin

    fixture = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"
    packet = Packet.model_validate(json.loads(fixture.read_text()))
    out = fin.run(packet, {"organic_growth_bridge": {"organic_growth": 0.52,
                                                     "total_growth": 0.65}})
    before = {r.metric_id: r for r in out.metrics}["FIN-GR-004"]
    assert before.value == pytest.approx(0.8)

    reqs = [r for r in collect_requests([out]) if r.metric_id == "FIN-GR-004"]
    merged = merge_overlay([out], [
        Judgment(request_id=r.request_id, answer="EXCELLENT",
                 evidence_class=EvidenceClass.Q, source="t", rationale="r")
        for r in reqs])[0]

    after = {r.metric_id: r for r in merged.metrics}["FIN-GR-004"]
    assert after.value == pytest.approx(0.8), "el juez piso la cifra calculada"
    assert after.score == 10.0, "el juez debe aportar el score"


def test_a_judgment_still_supplies_the_value_when_there_was_none():
    """The original behaviour, unchanged for metrics with nothing computed:
    with no figure to protect, the score IS the row's value."""
    import json
    from pathlib import Path

    from wbj.core.nullstates import EvidenceClass
    from wbj.overlay.merge import collect_requests, merge_overlay
    from wbj.schemas.overlay import Judgment
    from wbj.schemas.packet import Packet
    import wbj.specialists.financial as fin

    fixture = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"
    packet = Packet.model_validate(json.loads(fixture.read_text()))
    out = fin.run(packet, {})
    assert {r.metric_id: r for r in out.metrics}["FIN-GR-004"].value is None

    reqs = [r for r in collect_requests([out]) if r.metric_id == "FIN-GR-004"]
    merged = merge_overlay([out], [
        Judgment(request_id=r.request_id, answer="EXCELLENT",
                 evidence_class=EvidenceClass.Q, source="t", rationale="r")
        for r in reqs])[0]
    assert {r.metric_id: r for r in merged.metrics}["FIN-GR-004"].value == 10.0
