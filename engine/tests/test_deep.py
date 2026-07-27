"""Tests for `wbj.deep` — the never-zero rule and the i18n helper.

`deep.py` carries the rule the whole report leans on: a category with no
evidence is reported N/S, never 0. It had no test coverage; this file
locks the behaviour down.
"""

import pytest

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension
from wbj.deep import is_unscored, unscorable_summary
from wbj.i18n import L


def _valid(score: float) -> Value:
    return Value.of(score, unit="score", evidence_class=EvidenceClass.C)


def _missing() -> Value:
    return Value.null(NullState.MISSING, unit="score", warnings=["MISSING: test"])


def _dim(name: str, metrics: list[tuple[float, Value]]) -> Dimension:
    return Dimension(name=name, max_points=10.0, metric_scores=metrics)


class _Out:
    """Minimal stand-in for a SpecialistOutput (only `dimensions` is read)."""

    def __init__(self, dimensions, max_points: float = 20.0):
        self.dimensions = dimensions
        self.category = Category(name="test", max_points=max_points, dimensions=dimensions)


# --- is_unscored -------------------------------------------------------------


def test_unscored_when_every_dimension_lacks_coverage():
    """All dimensions below the 70% floor -> unscored, so callers show N/S
    instead of a 0 that reads as 'scored badly'."""
    d = _dim("all missing", [(0.5, _missing()), (0.5, _missing())])
    assert is_unscored(_Out([d])) is True


def test_not_unscored_when_a_dimension_scores():
    d = _dim("covered", [(0.5, _valid(8.0)), (0.5, _valid(6.0))])
    assert is_unscored(_Out([d])) is False


def test_not_unscored_when_only_some_dimensions_are_null():
    """A partially covered category is a real (low) score, not 'no evidence'."""
    covered = _dim("covered", [(1.0, _valid(7.0))])
    empty = _dim("empty", [(1.0, _missing())])
    assert is_unscored(_Out([covered, empty])) is False


def test_a_genuine_zero_is_not_treated_as_unscored():
    """Scoring 0 on real evidence must stay 0 — the N/S rule only covers
    *missing* evidence, it must not hide a truly bad score."""
    d = _dim("real zero", [(1.0, _valid(0.0))])
    assert is_unscored(_Out([d])) is False


def test_an_empty_dimension_list_is_unscored():
    """A specialist that declines the security outright returns an output
    with no dimensions — valuation does this for a bank or a REIT, whose
    adapter replaces its model entirely. That is "no evidence scored",
    not "scored zero": reading it the other way charged the category its
    full maximum as a zero earned on merit."""
    assert is_unscored(_Out([])) is True


def test_handles_object_without_dimensions():
    """An object with no `dimensions` attribute is not a specialist
    output at all. Nothing can be concluded from it, so the score
    stands — that is a different case from an empty list."""
    assert is_unscored(object()) is False


# --- the zero actually comes from NOT_SCORABLE dimensions --------------------


def test_category_points_are_zero_but_category_is_flagged_unscored():
    """Documents the trap: the engine awards 0 points to an uncovered
    category, which is why callers must consult `is_unscored` rather than
    reading the 0 at face value."""
    d = _dim("uncovered", [(0.7, _missing()), (0.3, _valid(9.0))])  # 30% valid < 70%
    cat = Category(name="business", max_points=20.0, dimensions=[d])
    assert cat.points() == 0.0
    assert is_unscored(_Out([d])) is True


# --- i18n --------------------------------------------------------------------


def test_language_helper_defaults_to_english():
    assert L("en", "Score", "Puntaje") == "Score"
    assert L("es", "Score", "Puntaje") == "Puntaje"
    assert L("", "Score", "Puntaje") == "Score"
    assert L("fr", "Score", "Puntaje") == "Score"


# --- unscorable_summary ------------------------------------------------------


def test_summary_is_conclusive_when_every_category_scored():
    outs = {"business": _Out([_dim("d", [(1.0, _valid(7.0))])], 20.0),
            "risk": _Out([_dim("d", [(1.0, _valid(5.0))])], 15.0)}
    assert unscorable_summary(outs) == (True, [], 0.0)


def test_summary_reports_points_that_never_had_evidence():
    """A low raw total caused by missing evidence must be reportable as
    such — CLAUDE.md forbids presenting it as an investment verdict."""
    outs = {"business": _Out([_dim("d", [(1.0, _missing())])], 20.0),
            "risk": _Out([_dim("d", [(1.0, _valid(5.0))])], 15.0)}
    conclusive, names, points = unscorable_summary(outs)
    assert conclusive is False
    assert names == ["business"]
    assert points == 20.0


def test_summary_does_not_count_a_genuine_zero_as_unscorable():
    """Scoring 0 on real evidence is a verdict, not missing data — it must
    not inflate the points-never-in-play figure."""
    outs = {"business": _Out([_dim("d", [(1.0, _valid(0.0))])], 20.0)}
    assert unscorable_summary(outs) == (True, [], 0.0)


# --- NOT_APPLICABLE vs NOT_SCORABLE ------------------------------------------


def test_not_applicable_metrics_leave_the_coverage_denominator():
    """MISSING_DATA_POLICY defines coverage as valid / *applicable*, and
    its decision tree opens by asking whether the metric applies at all.
    Cash runway is defined only for a company that burns cash, so
    counting its absence against a profitable one penalised it for being
    profitable."""
    from wbj.core.nullstates import NullState, Value

    na = Value.null(NullState.NOT_APPLICABLE, unit="score")
    d = Dimension(name="d", max_points=3.0,
                  metric_scores=[(0.25, _valid(8.0)), (0.25, _valid(6.0)),
                                 (0.25, _valid(7.0)), (0.25, na)])
    assert d.applicable_weight() == 0.75
    # 3 valid of 3 applicable clears the 70% floor; counting the
    # inapplicable metric would drop it to 75% of a 4-slot denominator.
    assert d.score10_value().is_null is False


def test_missing_metrics_stay_in_the_denominator():
    """Only NOT_APPLICABLE is excused. Evidence we merely failed to
    source must still count against coverage."""
    d = Dimension(name="d", max_points=3.0,
                  metric_scores=[(0.5, _valid(8.0)), (0.5, _missing())])
    assert d.applicable_weight() == 1.0
    assert d.score10_value().is_null is True


# --- judged catalysts --------------------------------------------------------


class _Judgment:
    def __init__(self, request_id, answer):
        self.request_id, self.answer = request_id, answer


class _MarketOut:
    def __init__(self, assumptions=None):
        self.assumptions = assumptions or []

    def model_copy(self, update):
        out = _MarketOut(self.assumptions)
        out.assumptions = update.get("assumptions", self.assumptions)
        return out


def test_judged_catalysts_trigger_a_market_rerun(monkeypatch):
    """A catalyst answer is a {probability, impact, evidence_quality}
    triple, not a 0-10 score, so it cannot come back through
    judgment_slots — merge_overlay never re-derives point math. It goes
    back into the overlay and Market runs again."""
    from wbj import deep

    called = {}

    def _fake_run(packet, overlay):
        called["catalysts"] = overlay["catalysts"]
        return _MarketOut()

    monkeypatch.setattr("wbj.specialists.market.run", _fake_run)

    overlay = {"catalysts": [{"event": "Launch", "months_to_event": 6.0}]}
    labelled = [("Market & Growth", _MarketOut())]
    judgments = [_Judgment("market_analysis:catalyst_0",
                           {"probability": 0.5, "impact": 1000.0,
                            "evidence_quality": 0.8})]

    out = deep._rerun_market_with_judged_catalysts(labelled, judgments, None, overlay)
    assert called["catalysts"][0]["probability"] == 0.5
    assert called["catalysts"][0]["impact"] == 1000.0
    assert out[0][1].assumptions  # the trail survived


def test_rerun_records_the_priced_assumption(monkeypatch):
    """FORMULAS.md: probabilities and impacts are assumptions and must
    never read as reported facts. A fresh run starts with fresh
    assumptions, so the record has to be re-attached explicitly."""
    from wbj import deep

    monkeypatch.setattr("wbj.specialists.market.run",
                        lambda packet, overlay: _MarketOut())

    overlay = {"catalysts": [{"event": "Launch", "months_to_event": 6.0}]}
    out = deep._rerun_market_with_judged_catalysts(
        [("Market & Growth", _MarketOut())],
        [_Judgment("market_analysis:catalyst_0",
                   {"probability": 0.5, "impact": 1000.0, "evidence_quality": 0.8})],
        None, overlay)
    trail = " ".join(out[0][1].assumptions)
    assert "assumption, not a reported figure" in trail
    assert "probability=0.5" in trail


def test_partial_catalyst_answer_is_ignored(monkeypatch):
    """All three inputs or none — a missing impact would otherwise be
    read as zero and quietly price the catalyst at nothing."""
    from wbj import deep

    monkeypatch.setattr("wbj.specialists.market.run",
                        lambda packet, overlay: _MarketOut(["rerun happened"]))

    overlay = {"catalysts": [{"event": "Launch", "months_to_event": 6.0}]}
    original = _MarketOut(["original"])
    out = deep._rerun_market_with_judged_catalysts(
        [("Market & Growth", original)],
        [_Judgment("market_analysis:catalyst_0", {"probability": 0.5})],
        None, overlay)
    assert out[0][1] is original


def test_no_catalysts_means_no_rerun():
    from wbj import deep

    original = _MarketOut(["original"])
    labelled = [("Market & Growth", original)]
    assert deep._rerun_market_with_judged_catalysts(
        labelled, [], None, {}) is labelled


# --- inapplicable dimensions vs unsourced ones -------------------------------


def _na():
    from wbj.core.nullstates import NullState, Value
    return Value.null(NullState.NOT_APPLICABLE, unit="score")


def test_a_wholly_inapplicable_dimension_is_not_scorable_but_not_a_hole():
    """Nothing in it applies to this security — a subscription dimension
    on a chip maker. MISSING_DATA_POLICY's decision tree opens on exactly
    that distinction."""
    from wbj.core.nullstates import NullState

    d = Dimension(name="customer_economics", max_points=3.0,
                  metric_scores=[(0.4, _na()), (0.3, _na()), (0.3, _na())])
    assert d.score10_value().state is NullState.NOT_APPLICABLE


def test_category_rescales_past_an_inapplicable_dimension():
    """SCORING.md: "If not applicable, use adapter metrics; do not
    impute." Leaving the hole would cap a chip maker at 17/20 for not
    selling subscriptions."""
    scored = Dimension(name="a", max_points=17.0,
                       metric_scores=[(1.0, _valid(5.0))])
    na = Dimension(name="b", max_points=3.0,
                   metric_scores=[(1.0, _na())])
    c = Category(name="business", max_points=20.0, dimensions=[scored, na])
    assert c.applicable_max_points() == 17.0
    # 17 * 0.5 = 8.5 raw, rescaled onto the full 20-point scale.
    assert c.points() == pytest.approx(10.0)
    assert c.score10() == pytest.approx(5.0)


def test_an_unsourced_dimension_keeps_its_hole():
    """A dimension we merely failed to source is a real absence and must
    keep costing — only NOT_APPLICABLE is rescaled away."""
    scored = Dimension(name="a", max_points=17.0,
                       metric_scores=[(1.0, _valid(5.0))])
    missing = Dimension(name="b", max_points=3.0,
                        metric_scores=[(1.0, _missing())])
    c = Category(name="business", max_points=20.0, dimensions=[scored, missing])
    assert c.applicable_max_points() == 20.0
    assert c.points() == pytest.approx(8.5)


# --- analyst-supplied judgments ----------------------------------------------


class _Req:
    def __init__(self, request_id, metric_id, schema_hint=""):
        self.request_id, self.metric_id = request_id, metric_id
        self.schema_hint = schema_hint


def test_analyst_answer_is_matched_by_metric_id():
    """`request_id` carries a per-run prefix; `metric_id` is the stable
    handle an analyst can write down."""
    from wbj.deep import _analyst_judgments

    out = _analyst_judgments(
        {"judgments": {"moat_classification": "Wide"}},
        [_Req("business_analysis:moat_classification", "moat_classification")])
    assert len(out) == 1
    assert out[0].request_id == "business_analysis:moat_classification"
    assert out[0].answer == "Wide"
    assert out[0].source == "analyst-input"


def test_unanswered_requests_are_left_for_the_judge():
    from wbj.deep import _analyst_judgments

    out = _analyst_judgments(
        {"judgments": {"moat_classification": "Wide"}},
        [_Req("a:moat_classification", "moat_classification"),
         _Req("a:three_thesis_killers", "three_thesis_killers")])
    assert [j.request_id for j in out] == ["a:moat_classification"]


def test_no_judgments_block_is_not_an_error():
    from wbj.deep import _analyst_judgments

    assert _analyst_judgments({}, [_Req("a:x", "x")]) == []
    assert _analyst_judgments({"judgments": None}, [_Req("a:x", "x")]) == []


# --- schema fields a judgment answers ----------------------------------------


class _Out2:
    """Stand-in carrying the two context-only schema fields."""

    def __init__(self):
        self.three_thesis_killers = []
        self.business_in_one_sentence = None

    def model_copy(self, update):
        out = _Out2()
        out.three_thesis_killers = update.get("three_thesis_killers",
                                              self.three_thesis_killers)
        out.business_in_one_sentence = update.get("business_in_one_sentence",
                                                  self.business_in_one_sentence)
        return out


def test_context_only_judgments_reach_their_schema_fields():
    """merge_overlay is generic across six specialists and knows only
    metrics and dimension slots, so a judged three_thesis_killers landed
    in the metrics row and left the schema field empty — while
    DECISION_RULES.md calls that list mandatory."""
    from wbj.deep import _apply_judgments_to_output_fields

    reqs = [_Req("business_analysis:three_thesis_killers", "three_thesis_killers"),
            _Req("business_analysis:business_in_one_sentence",
                 "business_in_one_sentence")]
    js = [_Judgment("business_analysis:three_thesis_killers",
                    {"items": ["A", "B", "C"]}),
          _Judgment("business_analysis:business_in_one_sentence", "Sells GPUs.")]

    out = _apply_judgments_to_output_fields([("Business", _Out2())], js, reqs)[0][1]
    assert out.three_thesis_killers == ["A", "B", "C"]
    assert out.business_in_one_sentence == "Sells GPUs."


def test_array_answers_are_unwrapped_from_the_items_envelope():
    """_coerce_answer wraps array-shaped answers as {"items": [...]}; the
    schema field wants the list itself."""
    from wbj.deep import _apply_judgments_to_output_fields

    out = _apply_judgments_to_output_fields(
        [("Business", _Out2())],
        [_Judgment("a:three_thesis_killers", {"items": ["x"]})],
        [_Req("a:three_thesis_killers", "three_thesis_killers")])[0][1]
    assert out.three_thesis_killers == ["x"]


def test_a_specialist_without_the_field_is_left_alone():
    """The same judgment list crosses all six outputs; only the one that
    declares the field is touched."""
    from wbj.deep import _apply_judgments_to_output_fields

    class _NoFields:
        pass

    original = _NoFields()
    out = _apply_judgments_to_output_fields(
        [("Financial", original)],
        [_Judgment("a:three_thesis_killers", {"items": ["x"]})],
        [_Req("a:three_thesis_killers", "three_thesis_killers")])
    assert out[0][1] is original
