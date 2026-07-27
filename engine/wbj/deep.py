"""Shared analysis pipeline: packet -> 6 Cerebro specialists -> aggregation.

Feeds the two user-facing analyses that follow Victor's methodology:

- `run_aggregate` — the strict Cerebro profile (overrides, gates,
  contradictions), alongside the quick scorecard for comparison.
- `run_levels` — real price-action levels for entry/exit timing.

`wbj.report` builds on the same helpers for the full research report.
Victor's judgment layer (`wbj.judge` + `wbj.overlay.merge`) answers the
specialists' open qualitative questions and is cached per ticker per day.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from wbj.cli import _build_packet
from wbj.packet.builder import Providers, build_packet
from wbj.providers.cache import Cache
from wbj.providers.edgar import EdgarProvider
from wbj.providers.finnhub import FinnhubProvider
from wbj.providers.fmp import FMPProvider
from wbj.providers.fred import FredProvider
from wbj.quick import quick_scorecard
from wbj.specialists import business, financial, market, risk, technical, valuation

logger = logging.getLogger(__name__)

_SPECIALISTS = [
    ("Business", business), ("Financial", financial), ("Market & Growth", market),
    ("Technical & Momentum", technical), ("Risk & Resilience", risk), ("Valuation", valuation),
]


def build_providers(settings: Any) -> Providers:
    """Wire all four live data providers off one shared cache."""
    cache = Cache(settings.cache_dir)
    return Providers(
        fmp=FMPProvider(settings, cache), edgar=EdgarProvider(settings, cache),
        finnhub=FinnhubProvider(settings, cache), fred=FredProvider(settings, cache),
    )


_ABSENT = object()


def _unscored_dimension_points(output: Any) -> float:
    """Category max points sitting behind dimensions that could not be scored.

    SCORING_ENGINE.md reweights "only within a dimension": a dimension whose
    valid metric weight falls below 70% is NOT_SCORABLE and simply earns none
    of its points, with no rescaling at the category level. Those points are
    not a judgment on the company — nothing was measured — so the display has
    to be able to separate them from points genuinely lost on merit.
    """
    dims = getattr(output, "dimensions", None) or []
    total = 0.0
    for d in dims:
        try:
            if d.score10_value().is_null:
                total += d.max_points
        except Exception:  # a malformed dimension tells us nothing
            continue
    return round(total, 2)


def is_unscored(output: Any) -> bool:
    """True when every dimension fell below the coverage threshold.

    Such a category is awarded 0 points, but that zero means "no evidence
    to score", not "scored zero on merit" — callers should render it as
    N/S so the Cerebro's "sin evidencia, no hay número" rule holds in the
    presentation too.
    """
    dims = getattr(output, "dimensions", _ABSENT)
    if dims is _ABSENT or dims is None:
        # Not a specialist output at all. Nothing can be concluded about
        # scoring from a malformed object, so leave the score standing.
        return False
    if not dims:
        # No dimensions at all means nothing was scored — which is what a
        # specialist returns when it declines the security outright, as
        # valuation does for a bank or a REIT whose adapter replaces its
        # model. Reading that as "scored" charged the category its full
        # maximum as a zero earned on merit, so every bank, REIT, biotech
        # and commodity producer silently lost ten points for a valuation
        # the engine had refused to attempt.
        return True
    try:
        return all(d.score10_value().is_null for d in dims)
    except Exception:
        logger.warning("could not determine scorability; treating as scored", exc_info=True)
        return False


_CATALYST_REQUEST_PREFIX = "market_analysis:catalyst_"
_MARKET_LABEL = "Market & Growth"


# OUTPUT_SCHEMA.md declares fields that a judgment answers but that
# `merge_overlay` cannot fill: it is generic across the six specialists
# and knows only metrics and dimension slots. So a judged
# `three_thesis_killers` landed in the metrics row and left the schema
# field empty, even though DECISION_RULES.md calls that list mandatory
# ("Always list three business-model or execution risks").
_JUDGMENT_OUTPUT_FIELDS = {
    "three_thesis_killers": "three_thesis_killers",
    "business_in_one_sentence": "business_in_one_sentence",
    "three_growth_thesis_killers": "three_growth_thesis_killers",
    "thesis_killers": "thesis_killers",
}

#: `moat_classification` is the one judgment that both scores a dimension
#: slot *and* fills a schema field. `merge_overlay` handled the slot, so
#: the points moved, but OUTPUT_SCHEMA.md's required
#: `moat: {classification, ...}` kept the "NotScorable" placeholder
#: `run()` writes — a report could show a Wide-moat score with a moat
#: block that said the classification was unavailable. Its answer is a
#: bare string, not a `{field: value}` map, so it needs its own path.
_MOAT_CLASSIFICATION_METRIC = "moat_classification"
_VALID_MOAT_CLASSIFICATIONS = ("Wide", "Narrow", "None", "NotScorable")

#: OUTPUT_SCHEMA.md's `moat` block declares `quantitative_evidence`, and
#: the agent asks for it — `moat_quantitative_effects_count` — but the
#: answer landed nowhere and the field stayed `[]` on every security. Its
#: vocabulary is DECISION_RULES.md's wide-moat condition 3, verbatim: the
#: five independent moat effects a reader can name. Answers are filtered
#: to those five, the same discipline the classification path uses, so an
#: off-list phrase is dropped rather than written through.
_MOAT_EFFECTS_METRIC = "moat_quantitative_effects_count"
_VALID_MOAT_EFFECTS = (
    "retention/switching costs",
    "cost advantage",
    "network scale",
    "regulated/intangible protection",
    "efficient scale",
)
_MOAT_EFFECTS_BY_KEY = {e.lower(): e for e in _VALID_MOAT_EFFECTS}

#: DECISION_RULES.md's DILUTION_RED_FLAG has a qualitative third condition,
#: "without commensurate per-share value creation", with no registered
#: formula. Business raises the flag on the mechanical >5%/3-year part and
#: asks this: a "yes" (value kept pace) clears it, "no" leaves it. The
#: answer lands in `capital_allocation` so `_recompute_business_flags` can
#: read it, mirroring how the thesis-killer flag reads its schema field.
_DILUTION_VALUE_METRIC = "dilution_commensurate_value"


#: The judgment fields OUTPUT_SCHEMA.md declares as arrays. `model_copy`
#: does not validate, so an answer of the wrong shape landed in the field
#: as written: a sentence answering `three_thesis_killers` was stored as a
#: *string* in a `list[str]` field, leaving the schema holding text where a
#: list belongs and jamming DECISION_RULES.md's "always list three ... risks"
#: flag, since `thesis_killers_listed` requires a list and a string is not
#: one — so the flag could never clear however the analyst answered.
_LIST_SHAPED_JUDGMENT_FIELDS = frozenset({
    "three_thesis_killers", "three_growth_thesis_killers", "thesis_killers",
})


def _coerce_string_list(answer: Any) -> list[str] | None:
    """The non-empty strings an array-shaped answer names, or None when the
    answer is not array-shaped.

    An answer that is not a list is refused rather than written into a list
    field — the same discipline `_coerce_moat_effects` applies, and the same
    one `moat_classification` applies to an off-vocabulary label.
    """
    if not isinstance(answer, (list, tuple)):
        return None
    items = [s.strip() for s in answer if isinstance(s, str) and s.strip()]
    return items or None


def _coerce_moat_effects(answer: Any) -> list[str] | None:
    """The distinct moat effects an answer names, or None when it names none.

    Keeps only DECISION_RULES.md's five categories, matched case-
    insensitively, in that document's order and without repeats — so an
    off-list phrase is dropped rather than written into the schema, and
    the two callers cannot disagree about what "efficient scale" is.
    """
    if not isinstance(answer, (list, tuple)):
        return None
    found = {
        _MOAT_EFFECTS_BY_KEY[item.strip().lower()]
        for item in answer
        if isinstance(item, str) and item.strip().lower() in _MOAT_EFFECTS_BY_KEY
    }
    if not found:
        return None
    return [e for e in _VALID_MOAT_EFFECTS if e in found]


def _apply_judgments_to_output_fields(labelled: list, judgments: list,
                                      requests: list) -> list:
    """Write context-only judgment answers into the schema fields that
    declare them.

    These answers carry no 0-10 score, so `merge_overlay` records them as
    context and stops. The output contract still requires the field, so
    the answer is copied across here.
    """
    metric_by_request = {r.request_id: r.metric_id for r in requests}
    answers: dict[str, Any] = {}
    moat_classification: str | None = None
    moat_effects: list[str] | None = None
    dilution_commensurate: bool | None = None
    for j in judgments:
        metric = metric_by_request.get(getattr(j, "request_id", ""))
        answer = j.answer
        # `_coerce_answer` wraps array-shaped answers as {"items": [...]}.
        if isinstance(answer, dict) and "items" in answer:
            answer = answer["items"]
        if metric == _MOAT_CLASSIFICATION_METRIC:
            if isinstance(answer, str) and answer in _VALID_MOAT_CLASSIFICATIONS:
                moat_classification = answer
            else:
                logger.warning("ignoring moat_classification %r: not one of %s",
                               answer, _VALID_MOAT_CLASSIFICATIONS)
            continue
        if metric == _MOAT_EFFECTS_METRIC:
            moat_effects = _coerce_moat_effects(answer)
            continue
        if metric == _DILUTION_VALUE_METRIC:
            dilution_commensurate = _coerce_yes_no(answer)
            continue
        field = _JUDGMENT_OUTPUT_FIELDS.get(metric or "")
        if not field:
            continue
        if field in _LIST_SHAPED_JUDGMENT_FIELDS:
            items = _coerce_string_list(answer)
            if items is None:
                logger.warning(
                    "ignoring %s %r: %s is an array field and the answer is not "
                    "a list of strings", metric, answer, field)
                continue
            answers[field] = items
        else:
            answers[field] = answer

    if (not answers and moat_classification is None and moat_effects is None
            and dilution_commensurate is None):
        return labelled

    out = []
    for label, o in labelled:
        updates: dict[str, Any] = {f: v for f, v in answers.items() if hasattr(o, f)}
        existing = getattr(o, "moat", None)
        if existing is not None and (moat_classification is not None
                                     or moat_effects is not None):
            moat_update: dict[str, Any] = {}
            if moat_classification is not None:
                moat_update["classification"] = moat_classification
            if moat_effects is not None:
                moat_update["quantitative_evidence"] = moat_effects
            updates["moat"] = existing.model_copy(update=moat_update)
        allocation = getattr(o, "capital_allocation", None)
        if dilution_commensurate is not None and isinstance(allocation, dict):
            updates["capital_allocation"] = {
                **allocation,
                "commensurate_per_share_value_creation": dilution_commensurate,
            }
        out.append((label, o.model_copy(update=updates) if updates else o))
    out = [(label, _recompute_business_flags(o)) for label, o in out]
    return [(label, _recompute_business_verdict(o)) for label, o in out]


def _coerce_yes_no(answer: Any) -> bool | None:
    """A yes/no judgment as a bool, or None when it is neither.

    Kept strict, like the moat-classification vocabulary: only a clear
    affirmative or negative counts, so an ambiguous answer leaves the
    flag exactly as it was rather than resolving it by guess.
    """
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, str):
        text = answer.strip().lower()
        if text in ("yes", "y", "true"):
            return True
        if text in ("no", "n", "false"):
            return False
    return None


def _recompute_business_flags(output: Any) -> Any:
    """Settle the two mandatory flags whose condition the judgment layer
    completes, once it has answered.

    THESIS_KILLERS_NOT_LISTED (DECISION_RULES.md's fourth rule, "Always
    list three... risks") is raised in `business.run()` with the list
    still empty and cleared here when three arrive. DILUTION_RED_FLAG is
    raised on the mechanical part of the rule — ">5% CAGR for three
    years" — and its qualitative part, "without commensurate per-share
    value creation", is cleared here when the analyst confirms per-share
    value kept pace. An output that never reaches this layer keeps both,
    which is the honest reading for each.
    """
    flags = getattr(output, "mandatory_flags", None)
    if flags is None:
        return output

    from wbj.specialists.business import (
        THESIS_KILLERS_NOT_LISTED,
        thesis_killers_listed,
    )

    drop: set[str] = set()
    if (THESIS_KILLERS_NOT_LISTED in flags
            and thesis_killers_listed(getattr(output, "three_thesis_killers", None))):
        drop.add(THESIS_KILLERS_NOT_LISTED)

    allocation = getattr(output, "capital_allocation", None)
    if ("DILUTION_RED_FLAG" in flags and isinstance(allocation, dict)
            and allocation.get("commensurate_per_share_value_creation") is True):
        drop.add("DILUTION_RED_FLAG")

    if not drop:
        return output
    return output.model_copy(update={
        "mandatory_flags": [f for f in flags if f not in drop]})


def _recompute_business_verdict(output: Any) -> Any:
    """Re-derive the business verdict label after judgments landed.

    `merge_overlay` calls `rescore`, which recomputes the category totals
    but deliberately leaves the rest of the envelope alone. `verdict` was
    computed inside `run()` from a score that no longer exists: a
    `moat_classification` answer worth a quarter of the moat dimension
    moved one test case from 7.75 to 8.31 — across the Excellent
    boundary — while the label still read "Good business".

    The `moat_quantitative_effects_count` answer settles the wide-moat
    gate's third condition — "At least two independent moat effects are
    quantitatively visible" — landing in `moat.quantitative_evidence`, so
    both the score and the gate are current only now.
    """
    gate = getattr(output, "moat_gate_inputs", None)
    moat = getattr(output, "moat", None)
    score10 = getattr(getattr(output, "category", None), "score_10", None)
    if gate is None or moat is None or score10 is None:
        return output

    from wbj.specialists.business import capped_verdict

    effects = getattr(moat, "quantitative_evidence", None) or []
    label = capped_verdict(
        score10,
        value_destruction="VALUE_DESTRUCTION" in (output.mandatory_flags or []),
        excellent_gate_passes=gate.excellent_gate_passes(len(effects)),
    )
    if label == output.verdict:
        return output
    return output.model_copy(update={"verdict": label})


#: Which of Cerebro's three admissible sources each judgment slot draws on.
#:
#: `01_business_analysis/AGENT.md` and `03_market_analysis/AGENT.md`, verbatim:
#: "a claim that cannot be tied to a formula result, reported evidence, or
#: explicitly disclosed assumption is context only and receives no score."
#: Formula results are the deterministic engine's. The other two have different
#: natural suppliers, and the split is not a preference -- it follows from what
#: the claim IS:
#:
#: - `assumption` — Cerebro assigns these to the model owner by name. Nobody
#:   can be accountable for an assumption they did not make, so no model
#:   should be asked to author one on the analyst's behalf.
#: - `filing` — reported evidence, which means reading the 10-K narrative. The
#:   judge receives the filing passages and can cite what it used.
#: - `external` — neither: the datum is not in the filing and is not an
#:   assumption, so it belongs in `Entradas/<TICKER>.json` as data with a
#:   source, not in a judgment channel at all.
#:
#: Keys ending in `_` are prefixes (catalyst slots are numbered per catalyst).
JUDGMENT_SOURCE: dict[str, tuple[str, str]] = {
    "catalyst_": ("assumption",
                  "03_market_analysis/AGENT.md: 'catalyst probabilities and "
                  "impacts are model assumptions and must be labeled'"),
    "thesis_killers": ("assumption",
                       "05_risk_analysis/DECISION_RULES.md: the schema's own "
                       "field is `probability_assumption`"),
    "three_thesis_killers": ("assumption",
                             "01_business_analysis/DECISION_RULES.md: the three "
                             "risks that would destroy YOUR thesis"),
    "three_growth_thesis_killers": ("assumption",
                                    "03_market_analysis/DECISION_RULES.md: three "
                                    "events that would invalidate the growth thesis"),
    "moat_classification": ("assumption",
                            "01_business_analysis/DECISION_RULES.md leaves the "
                            "Wide/Narrow/None call to a reader"),
    "moat_quantitative_effects_count": ("filing",
                                        "which effects are 'quantitatively visible' "
                                        "has to be read out of the disclosures"),
    "business_in_one_sentence": ("filing",
                                 "'using only what the packet and filings state'"),
    "dilution_commensurate_value": ("filing",
                                    "per-share value creation against the reported "
                                    "dilution"),
    "regulatory_legal_exposure": ("filing",
                                  "the 10-K's legal-proceedings disclosure"),
    "tam_source_tier_assignment": ("filing",
                                   "03_market_analysis/DECISION_RULES.md's tier "
                                   "table, applied to the source the analyst chose"),
    "FIN-GR-004": ("filing",
                   "the organic/acquired/FX/divestiture revenue bridge, disclosed "
                   "in the MD&A"),
    "FIN-GR-005": ("external",
                   "a 3-year share series on one consistent market definition -- "
                   "not in any filing and not an assumption; supply it as data"),
}


def judgment_source(metric_id: str) -> tuple[str, str]:
    """Which source Cerebro assigns this slot, and the line that says so."""
    if metric_id in JUDGMENT_SOURCE:
        return JUDGMENT_SOURCE[metric_id]
    for key, value in JUDGMENT_SOURCE.items():
        if key.endswith("_") and metric_id.startswith(key):
            return value
    return ("filing", "unclassified: defaults to the judge, which cites what it reads")


def pending_judgments(outputs: Any, overlay: dict | None = None) -> dict[str, list[dict]]:
    """Open judgment slots, grouped by who Cerebro assigns them to.

    Answering "should I write this or pay for it?" required reading a table
    somewhere else. The routing rule already lives in the code that consumes
    these requests, so the split is computed from it: what is still open, and
    which of Victor's three sources each one draws on.

    Already-answered slots drop out, so this shrinks as `Entradas/` fills.
    """
    answered = set((overlay or {}).get("judgments") or {})
    groups: dict[str, list[dict]] = {"assumption": [], "filing": [], "external": []}
    for out in (outputs or []):
        for req in (getattr(out, "judgment_requests", None) or []):
            if req.metric_id in answered:
                continue
            kind, why = judgment_source(req.metric_id)
            groups.setdefault(kind, []).append({
                "metric_id": req.metric_id, "agent": req.agent_id,
                "schema_hint": req.schema_hint, "why": why,
            })
    return groups


def _analyst_judgments(overlay: dict, requests: list) -> list:
    """Judgment answers the analyst wrote in `Entradas/<TICKER>.json`.

    Several dimension slots are judgment-only by design:
    `moat_classification` is the Wide/Narrow/None call DECISION_RULES.md
    leaves to a reader, since the mechanical gate conditions cannot
    enumerate *which* moat effects are visible. Those slots were
    reachable only through the Claude sub-agent, so an exhausted API
    budget left them permanently null.

    An answer is a declared assumption whoever makes it, so the analyst's
    goes in the same channel with `source` recording that a person
    supplied it. Matching is by `metric_id`, which is stable, rather than
    by `request_id`, which carries a per-run prefix.
    """
    answers = (overlay or {}).get("judgments") or {}
    if not isinstance(answers, dict) or not answers:
        return []

    from wbj.core.nullstates import EvidenceClass
    from wbj.schemas.overlay import Judgment

    out = []
    for req in requests:
        supplied = answers.get(req.metric_id)
        if supplied is None:
            continue
        # An analyst may write the bare answer, or wrap it with the reasoning
        # and the evidence behind it. INSTITUTIONAL_VALUATION_ENGINE.md sets
        # the bar this exists to clear: "the most important outputs ... are
        # the assumptions, sensitivities, invalidation conditions, and
        # evidence that allow another analyst to reproduce or challenge the
        # result". The judge already carries both -- it records what it cited
        # and why -- while this channel recorded a role and a file path, so a
        # hand-written "Wide" arrived with nothing to argue with.
        rationale, source, klass = "", "analyst-input", EvidenceClass.Q
        if isinstance(supplied, dict) and "answer" in supplied:
            rationale = str(supplied.get("rationale") or "")
            source = str(supplied.get("source") or "analyst-input")
            # DATA_POLICY.md separates `Q` (qualitative evidence) from `A`
            # (explicit model assumption), and Cerebro assigns some of these
            # slots to A by name: "catalyst probabilities and impacts are
            # model assumptions and must be labeled"
            # (03_market_analysis/AGENT.md), and the risk schema's own field
            # is `probability_assumption`. Hardcoding Q filed a declared
            # assumption as observed evidence, which is the one distinction
            # the evidence classes exist to make.
            declared = str(supplied.get("evidence_class") or "").strip().upper()
            if declared in ("Q", "A", "R", "C", "E"):
                klass = EvidenceClass(declared)
            supplied = supplied["answer"]
        if not rationale:
            logger.info(
                "analyst judgment for %s carries no rationale; another analyst "
                "cannot challenge it", req.metric_id)
        answer = _shape_answer(supplied, req.schema_hint)
        try:
            out.append(Judgment(
                request_id=req.request_id,
                answer=answer,
                evidence_class=klass,
                source=source,
                rationale=rationale or "supplied in Entradas/<TICKER>.json",
            ))
        except Exception:
            logger.warning("analyst judgment for %s rejected", req.metric_id,
                           exc_info=True)
    return out


def _shape_answer(value: Any, schema_hint: str) -> Any:
    """Put an analyst's native value into the shape `Judgment.answer` holds.

    `Answer` is `float | str | dict`, deliberately: it never accepts a bare
    list. The judge's `_coerce_answer` already knows this and wraps an
    array-shaped answer into `{"items": [...]}` before building the Judgment.
    The analyst path did no coercion at all, so a list went straight into the
    model, failed validation, and was swallowed by the `except` below.

    That silently defeated the whole point of the channel for exactly the
    answers most worth writing by hand. `thesis_killers` is the case: its
    schema_hint asks for "array of >=3 {risk, probability_assumption, ...}",
    which is Victor's own seven-field structure, and an analyst who wrote
    precisely that got a warning in a log and a still-empty metric -- while
    the same structure from the judge went through fine. Two channels for one
    kind of evidence, one of which quietly did not work.

    Scalars pass through untouched; the judge coerces strings because its
    answers arrive as text, and an analyst's do not.
    """
    if isinstance(value, list):
        return {"items": value}
    return value


def _rerun_market_with_judged_catalysts(labelled: list, judgments: list,
                                        packet: Any, overlay: dict) -> list:
    """Second pass at Market once the judge has priced the catalysts.

    MKT-CAT-019 is `probability * impact * evidence_quality * time_decay`.
    The filing supplies the event and its date; the other three are, per
    FORMULAS.md, assumptions — so market.py raises one JudgmentRequest
    per catalyst and leaves the metric NOT_SCORABLE.

    Those answers cannot come back through `judgment_slots`: that path
    replaces a dimension slot with a ready 0-10 score, whereas a catalyst
    answer is a `{probability, impact, evidence_quality}` triple that
    still has to run through the formula — and `merge_overlay` states
    plainly that it never re-derives point math. Several catalysts also
    feed a single aggregate slot, so there is no one-to-one mapping to
    make. The answers therefore go back where they came from, into
    `overlay["catalysts"]`, and Market is simply run again.

    Left un-rerun if nothing was priced, so a silent judge changes
    nothing.
    """
    catalysts = list(overlay.get("catalysts") or [])
    if not catalysts:
        return labelled

    priced = 0
    for j in judgments:
        rid = getattr(j, "request_id", "") or ""
        if not rid.startswith(_CATALYST_REQUEST_PREFIX):
            continue
        answer = j.answer
        if not isinstance(answer, dict):
            continue
        try:
            i = int(rid[len(_CATALYST_REQUEST_PREFIX):])
        except ValueError:
            continue
        if not 0 <= i < len(catalysts):
            continue
        triple = {k: answer.get(k) for k in
                  ("probability", "impact", "evidence_quality")}
        if any(v is None for v in triple.values()):
            continue
        catalysts[i] = {**catalysts[i], **triple}
        priced += 1

    if not priced:
        return labelled

    try:
        from wbj.specialists import market
        rejudged = market.run(packet, {**overlay, "catalysts": catalysts})
    except Exception:
        logger.warning("market re-run with judged catalysts failed; "
                       "keeping the unpriced output", exc_info=True)
        return labelled

    # A fresh run starts with fresh assumptions, which would silently drop
    # the record merge_overlay wrote of *which* judgments were applied.
    # Those probabilities and impacts are assumptions driving a real
    # score, so the trail that says so has to survive the second pass.
    previous = next((o for lbl, o in labelled if lbl == _MARKET_LABEL), None)
    carried = list(getattr(previous, "assumptions", None) or [])
    carried += [
        f"catalyst {c['event']!r} priced by judgment: probability="
        f"{c['probability']}, impact={c['impact']}, "
        f"evidence_quality={c['evidence_quality']} (assumption, not a "
        f"reported figure; source {c.get('source')})"
        for c in catalysts
        if c.get("probability") is not None
    ]
    rejudged = rejudged.model_copy(
        update={"assumptions": [*(rejudged.assumptions or []), *carried]})

    return [(lbl, rejudged if lbl == _MARKET_LABEL else o) for lbl, o in labelled]


def unscorable_summary(outs: dict) -> tuple[bool, list[str], float]:
    """How much of the 100-point scale never had evidence behind it.

    CLAUDE.md draws a hard line between "this scored badly" and "I could
    not score this". Victor's gate math still treats an unscored
    category as 0 points and gate-ineligible (MISSING_DATA_POLICY.md),
    so a company can land on a low raw total purely because evidence was
    absent. Callers use this to say so out loud instead of presenting
    that total as a verdict.

    Returns `(conclusive, unscored_category_names, points_never_in_play)`.
    """
    unscored = sorted(k for k, o in outs.items() if is_unscored(o))
    points = round(sum(outs[k].category.max_points for k in unscored), 2)
    return (not unscored, unscored, points)


# Qualitative judgments are stable within a trading day; cache them so a
# re-run (or `deep` followed by `report`) doesn't pay the ~30s Claude call twice.
_JUDGMENT_CACHE_KEY = "judgments"
_JUDGMENT_MAX_AGE_DAYS = 1.0

_KEY_BY_LABEL = {
    "Business": "business", "Financial": "financial", "Market & Growth": "market",
    "Technical & Momentum": "technical", "Risk & Resilience": "risk", "Valuation": "valuation",
}


def _run_specialists(full_packet: Any, settings: Any = None,
                     judge: bool = True) -> tuple[list[tuple[str, Any]], int]:
    """The 6 specialists, with Victor's judgment overlay merged in.

    Returns (labelled outputs, judgments applied). Judgment is enrichment:
    any failure leaves the un-judged outputs intact.
    """
    # Specialists read their hardest inputs (WACC above all) from an
    # overlay. Nothing built one, so those metrics were NOT_SCORABLE on
    # every ticker and Business always came back N/S. Anything the packet
    # cannot legitimately source stays absent, so honest N/S is preserved.
    overlay: dict[str, Any] = {}
    if settings is not None:
        try:
            from wbj.overlay.from_packet import build_overlay
            overlay = build_overlay(full_packet, settings)
        except Exception:
            logger.warning("overlay build failed; specialists run unassisted", exc_info=True)

    labelled: list[tuple[str, Any]] = []
    for label, mod in _SPECIALISTS:
        try:
            # Not every specialist takes an overlay; those that do treat
            # it as optional enrichment.
            try:
                out = mod.run(full_packet, overlay) if overlay else mod.run(full_packet)
            except TypeError:
                out = mod.run(full_packet)
            labelled.append((label, out))
        except Exception:
            logger.warning("specialist %s failed; category omitted", label, exc_info=True)
            continue

    judged = 0
    if judge and settings is not None and getattr(settings, "anthropic_api_key", None):
        try:
            from wbj.judge import answer_judgments
            from wbj.overlay.merge import collect_requests, merge_overlay
            from wbj.schemas.overlay import Judgment

            outs = [o for _, o in labelled]
            ticker = getattr(getattr(full_packet, "security", None), "ticker", None)
            cache = Cache(settings.cache_dir)
            judgments: list[Judgment] | None = None

            # The judge is ~30s of the ~34s an analysis takes, and its answers
            # (moat class, thesis killers, TAM tier) are stable intraday — so
            # cache them per ticker per day instead of re-billing every run.
            requests = collect_requests(outs)
            # The question set is part of the cache key. Catalyst requests
            # are one-per-extracted-catalyst, and extraction is not
            # deterministic — a cached answer to `catalyst_1` from a run
            # that found two catalysts makes merge_overlay reject the
            # whole overlay when today's run finds one. That strictness is
            # right; stale answers to questions nobody asked are the bug.
            asked = sorted(r.request_id for r in requests)

            if ticker:
                age = cache.age_days(ticker, _JUDGMENT_CACHE_KEY)
                if age is not None and age <= _JUDGMENT_MAX_AGE_DAYS:
                    payload = cache.get(ticker, _JUDGMENT_CACHE_KEY) or {}
                    rows = payload.get("judgments")
                    if rows and payload.get("requests") == asked:
                        judgments = [Judgment.model_validate(r) for r in rows]

            # Answers the analyst wrote by hand come first and are never
            # re-asked. A judgment is a declared assumption either way —
            # `moat_classification` is a Wide/Narrow/None call that
            # DECISION_RULES.md leaves to a reader — so a person supplying
            # it is the same kind of evidence as the sub-agent supplying
            # it, and it keeps these slots working with no API budget.
            analyst = _analyst_judgments(overlay, requests)
            if analyst:
                answered = {j.request_id for j in analyst}
                requests = [r for r in requests if r.request_id not in answered]

            if judgments is None:
                asked_of_claude = []
                if requests:
                    try:
                        # The 10-K narrative is where customer concentration, backlog/RPO
                        # and recurring revenue live; the judge needs the filing to
                        # answer for them, not just the facts table.
                        asked_of_claude = answer_judgments(
                            full_packet, requests, settings,
                            edgar=getattr(build_providers(settings), "edgar", None),
                            # The specialists' frozen rows: several questions
                            # refer to values "computed mechanically above",
                            # which only exist here.
                            outputs=outs,
                        )
                    except Exception:
                        # An exhausted or unreachable API must not discard
                        # the answers the analyst already wrote down.
                        logger.warning("judge unavailable; keeping analyst answers only",
                                       exc_info=True)
                judgments = [*analyst, *asked_of_claude]
                if ticker and judgments:
                    cache.put(ticker, _JUDGMENT_CACHE_KEY,
                              {"requests": asked,
                               "judgments": [j.model_dump(mode="json") for j in judgments]})

            if judgments:
                merged = merge_overlay(outs, judgments)
                labelled = list(zip([lbl for lbl, _ in labelled], merged))
                judged = len(judgments)
                labelled = _rerun_market_with_judged_catalysts(
                    labelled, judgments, full_packet, overlay)
                labelled = _apply_judgments_to_output_fields(
                    labelled, judgments, collect_requests(outs))
        except Exception:
            # Enrichment only: keep the un-judged outputs, but say so.
            logger.warning("judgment overlay failed; continuing unjudged", exc_info=True)
    return labelled, judged


def run_aggregate(ticker: str, settings: Any, now: datetime | None = None) -> dict:
    """Victor's full aggregation (Task 21): the rigorous 100-point profile.

    Feeds the 6 judged specialist outputs through `wbj.aggregate` —
    handoff overrides, SCORING_AND_GATES.md profile gates, and the
    contradiction lookup table — producing the Cerebro's own label
    (Quality / Value / Momentum / Speculative / Avoid / ...), raw score,
    confidence and the gates that passed or failed.

    This is stricter than the quick scorecard by design: categories the
    data can't cover score low, so the raw total here is normally *below*
    the quick score. Both are honest; this one applies the veto rules.
    """
    from wbj.aggregate import (
        AggregateInputs, CategoryConfidences, CategoryPoints, CategoryScore10s,
        apply_gates, apply_overrides, contradictions, descriptive_band,
        raw_total, total_confidence,
    )

    now = now or datetime.now(timezone.utc)
    packet = build_packet(ticker, build_providers(settings), now=now)
    labelled, judged = _run_specialists(packet, settings)
    outs = {_KEY_BY_LABEL[lbl]: o for lbl, o in labelled if lbl in _KEY_BY_LABEL}
    missing = [k for k in _KEY_BY_LABEL.values() if k not in outs]
    if missing:
        return {"ticker": ticker.upper(), "status": "incomplete", "missing": missing}

    # The quick scorecard alongside, so the two numbers can be compared in
    # one place instead of looking like a contradiction: they score
    # different metric sets, and the coverage column shows why they differ.
    quick_by_key: dict[str, float | None] = {}
    try:
        quick = quick_scorecard(_build_packet(ticker))
        quick_by_key = {c["key"]: c.get("score10") for c in quick.get("categories", [])}
    except Exception:
        logger.warning("quick scorecard unavailable for comparison", exc_info=True)

    pts = CategoryPoints(**{k: (o.category.awarded_points or 0.0) for k, o in outs.items()})
    confs = CategoryConfidences(**{k: (o.category.confidence or 0.0) for k, o in outs.items()})
    scores10 = CategoryScore10s(**{k: (o.category.score_10 or 0.0) for k, o in outs.items()})

    overrides = apply_overrides(AggregateInputs(
        **outs, facts_table=getattr(packet, "facts_table", None)))
    raw = raw_total(pts)
    profile = apply_gates(raw, pts, confs, overrides)

    # A category with no evidence enters the score model as 0.0, which
    # `_weak()` reads as "weak" — that would fire contradictions the data
    # never supported. SCORING_ENGINE.md forbids assigning a score to
    # missing evidence, so drop any contradiction that leans on one.
    unscored_keys = {k for k, o in outs.items() if is_unscored(o)}
    clashes = [
        c for c in contradictions(scores10, raw)
        if not any(k in c.combination.lower() for k in unscored_keys)
    ]

    # CLAUDE.md: when evidence is missing the answer is "I do not have
    # enough data to reach an investment conclusion" -- NOT a verdict.
    # Victor's gate math is left untouched: an unscored category still
    # contributes 0 points and is still gate-ineligible per
    # MISSING_DATA_POLICY.md. What changes is only the headline, so a
    # low total driven by *absent evidence* is never read as "bad
    # company". `unscorable_points` says how much of the 100 was never
    # in play, which is what makes the raw score interpretable.
    conclusive, unscored_names, unscorable_points = unscorable_summary(outs)
    return {
        "ticker": ticker.upper(),
        "as_of": now.date().isoformat(),
        "status": "ok",
        "judgments_applied": judged,
        "label": profile.label,
        "conclusive": conclusive,
        "unscored_categories": unscored_names,
        "unscorable_points": unscorable_points,
        "raw_score": round(profile.raw_score, 2),
        "confidence": round(profile.total_confidence, 1),
        "band": profile.descriptive_band,
        "passed_gates": list(profile.passed_gates),
        "failed_gates": list(profile.failed_gates),
        "overrides": [getattr(o, "name", str(o)) for o in profile.overrides],
        "warnings": list(profile.warnings),
        "categories": [
            {"key": k,
             # Unscored => null, never 0: "do not assign a score to missing
             # evidence" (SCORING_ENGINE.md) applies to zero too.
             "points": None if is_unscored(o) else round(o.category.awarded_points or 0.0, 2),
             "max_points": o.category.max_points,
             "score10": None if is_unscored(o) else (
                 round(o.category.score_10, 1) if o.category.score_10 is not None else None),
             # Same category as the agent scorecard shows, for side-by-side.
             "quick_score10": quick_by_key.get(k),
             "coverage": round(o.coverage, 2) if o.coverage is not None else None,
             # A category whose every dimension fell below the coverage
             # threshold scores 0 for lack of evidence, not on merit —
             # "sin evidencia no hay número" means showing N/S, not a zero.
             "unscored": is_unscored(o),
             # How much of this category's maximum was never assessable.
             # SCORING_ENGINE.md reweights "only within a dimension", so a
             # dimension below the coverage threshold earns 0 of its points
             # and the category score falls even though nothing was measured
             # and found wanting. Without this number a reader cannot tell
             # "measured and weak" from "never measured": NVDA's market
             # category reads 1.1/10 while the one dimension that could be
             # scored came in at 7.2.
             "unscored_points": _unscored_dimension_points(o)}
            for k, o in outs.items()
        ],
        "contradictions": [
            {"combination": c.combination, "interpretation": c.interpretation, "label": c.label}
            for c in clashes
        ],
    }


def run_levels(ticker: str, settings: Any, now: datetime | None = None) -> dict:
    """Real price-action levels for entry/exit timing.

    Surfaces Victor's levels engine (support/resistance zones with touch
    counts, SMA50/200, anchored VWAPs, earnings gaps) — computed inside the
    technical specialist but never exposed to the UI until now. No Claude,
    no cost: pure price-structure math over the live packet.
    """
    now = now or datetime.now(timezone.utc)
    packet = build_packet(ticker, build_providers(settings), now=now)
    out = technical.run(packet)
    lv = out.important_levels
    # FMP returns bars newest-first, so pick by date rather than position.
    daily = packet.market_data.daily
    price = max(daily, key=lambda r: r.date).close if daily else None

    def _zone(z: Any) -> dict:
        return {
            "center": round(z.center, 2), "lower": round(z.lower, 2), "upper": round(z.upper, 2),
            "status": z.status, "timeframe": z.timeframe,
            "distance_percent": round(z.distance_percent, 2) if z.distance_percent is not None else None,
            "touches": len(z.touches or []),
            "invalidation_rule": z.invalidation_rule,
        }

    return {
        "ticker": ticker.upper(),
        "as_of": now.date().isoformat(),
        "price": round(price, 2) if price is not None else None,
        "support": [_zone(z) for z in lv.nearest_support],
        "resistance": [_zone(z) for z in lv.nearest_resistance],
        "moving_averages": [{"label": m.label, "value": round(m.value, 2)} for m in lv.moving_averages],
        "avwaps": [{"reason": a.anchor_reason, "anchor_date": a.anchor_date,
                    "value": round(a.value, 2)} for a in lv.avwaps],
        "earnings_gaps": [{"date": g.event_date, "prior_close": round(g.prior_close, 2),
                           "gap_open": round(g.gap_open, 2)} for g in lv.earnings_gaps],
    }


