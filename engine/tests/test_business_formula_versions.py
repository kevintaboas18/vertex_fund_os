"""The formula version a registered metric publishes.

VERSION.md line 5 versions the registry as a whole: `Formula-registry
version: 2026.1`. FORMULA_REGISTRY.md's formula-result object carries the
same string. So every id the registry lists publishes that one version,
and an id it does not list is not a registered formula and says so — the
way `core.formulas` answers an unknown id with "unknown".

An earlier audit pass mistook engine corrections for formula changes and
bumped nine rows to a "2026.2" that appears in none of Victor's documents.
FORMULA_REGISTRY.md's change control is about the *formula* — "A threshold
or formula change is a model change" — its text, sign convention, inputs,
or thresholds. Correcting code that computed something other than the
registered formula moves none of those; the formula text is byte-identical
to Victor's (`git diff upstream/main -- FORMULAS.md` is empty). Publishing
a higher version would tell a reader his model changed when it did not, and
the increment plus VERSION.md's paired backtest note belong to whoever
edits FORMULAS.md — not to this module.
"""

import pytest

from wbj.specialists import business as bus


def test_a_registered_formula_publishes_the_registry_version():
    """One number for the whole registry, and it is Victor's: 2026.1."""
    assert bus.FORMULA_REGISTRY_VERSION == "2026.1"
    assert bus.formula_version("BUS-GM-007") == "2026.1"
    assert bus.formula_version("BUS-IROIC-016") == "2026.1"


def test_an_unregistered_id_is_not_dressed_as_registered():
    """An id the registry does not list must not borrow 2026.1 to look
    registered; it reports "unknown", as core.formulas does."""
    assert bus.formula_version("BUS-DOES-NOT-EXIST") == "unknown"


def test_all_thirty_registered_formulas_report_one_version():
    """No row carries a version Victor's documents do not state."""
    for metric_id in bus.BUS_30_IDS:
        assert bus.formula_version(metric_id) == bus.FORMULA_REGISTRY_VERSION


def test_no_row_publishes_the_invented_2026_2():
    """The regression guard: 2026.2 is in none of Victor's documents, so
    no id may ever publish it again."""
    for metric_id in bus.BUS_30_IDS:
        assert bus.formula_version(metric_id) != "2026.2", metric_id


def test_the_changelog_documents_engine_corrections_not_version_bumps():
    """The changelog survives as an audit record of where the engine's
    reading of a formula was corrected against registry 2026.1 — reason and
    named test, per change control's spirit — but it moves no version, and
    every key is a real registered formula."""
    for key, (reason, test) in bus.FORMULA_CHANGELOG.items():
        assert key in bus.BUS_30_IDS, key
        assert len(reason) > 40, f"{key}: reason is too thin to audit"
        assert "::" in test or test.endswith(".py"), f"{key}: no test named"


# --- thresholds that must come from Victor, not from here -----------------


def test_the_market_definition_floor_comes_from_the_confidence_table():
    """CONFIDENCE_ENGINE.md: 85-100 High, 70-84 Medium, 50-69 Low.
    SCORING.md caps competitive position "if market definition is low
    confidence", so the cap lifts where Low ends — at 70. The floor was
    60, a number in none of Victor's documents, which would have lifted
    the cap for a source his own table calls Low."""
    assert bus._CONFIDENCE_FLOOR == pytest.approx(70.0)
    assert bus._LOW_CONFIDENCE_CEILING == pytest.approx(69.0)


def test_the_tam_tiers_are_victors_verbatim():
    """`03_market_analysis/DECISION_RULES.md`'s source-quality table:
    1=100, 2=85, 3=70, 4=45, 5=0."""
    assert bus._TAM_TIER_CONFIDENCE == {1: 100.0, 2: 85.0, 3: 70.0, 4: 45.0, 5: 0.0}


@pytest.mark.parametrize("tier,confident", [
    (1, True),    # government / audited industry volume
    (2, True),    # reputable industry association
    (3, True),    # independent research, disclosed methodology — exactly 70
    (4, False),   # issuer estimate — 45, Insufficient
    (5, False),   # unattributed — 0, not scorable
])
def test_each_tam_tier_lands_on_the_right_side_of_the_cap(tier, confident):
    assert bus._market_definition_is_confident({"tam_source_tier": tier}) is confident


def test_an_unsourced_market_definition_keeps_the_cap():
    """No TAM is the lowest confidence there is."""
    assert bus._market_definition_is_confident({}) is False
    assert bus._market_definition_is_confident({"tam_source_tier": "3"}) is False


# --- anchor provenance ----------------------------------------------------


def _anchor_scored_metrics() -> set[str]:
    """Every `add("BUS-...", ...)` in `_compute_all` scored off an anchor scale.

    This is a completeness check over a registry, so it reads the code
    rather than a run: no single packet exercises all ~90 metrics, and a
    metric that scores without a registered scale has to be caught the
    moment it is written, not when some future fixture happens to reach
    it.

    Parsed as a syntax tree, not matched line by line. The regex this
    replaces required the `add(...)` call and `_score_from_anchor` to
    share a source line, so wrapping a long call dropped the metric from
    the scan silently — the test kept passing while checking less.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(inspect.getmodule(bus)))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_compute_all")

    found: set[str] = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "add"):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("BUS-")):
            continue
        uses_anchor = any(
            isinstance(inner, ast.Name) and inner.id == "_score_from_anchor"
            for arg in node.args[1:] for inner in ast.walk(arg))
        if uses_anchor:
            found.add(node.args[0].value)
    return found


def test_every_scored_metric_declares_where_its_anchors_came_from():
    """SCORING_ENGINE.md asks for interpolation between *registered*
    anchors. A metric that scores without its scale being traceable to
    either Victor or a declared calibration is a number nobody can
    audit."""
    scored = _anchor_scored_metrics()
    # A scan that stops matching passes vacuously, which is the one way
    # this check can rot without anyone noticing. Seventeen metrics score
    # off an anchor scale today; the floor only has to catch a scan that
    # has gone blind, not track the exact count.
    assert len(scored) >= 15, f"the scan found only {len(scored)} metrics"
    missing = scored - set(bus.ANCHOR_PROVENANCE)
    assert not missing, f"scored with unregistered anchors: {sorted(missing)}"


@pytest.mark.parametrize("metric_id", sorted(bus.ANCHOR_PROVENANCE))
def test_each_provenance_entry_is_usable(metric_id):
    source, note = bus.ANCHOR_PROVENANCE[metric_id]
    assert source in ("VICTOR", "MIXED", "PROXY", "ENGINE")
    assert len(note) > 20, f"{metric_id}: note does not say where the numbers came from"
    if source == "VICTOR":
        assert ".md" in note, f"{metric_id}: a Victor anchor must name the document"


def test_the_engine_calibrated_anchors_are_disclosed_in_the_output():
    """AGENT.md permits a number that is not Victor's only as an
    "explicitly disclosed assumption". Without the disclosure the output
    presented his thresholds and this module's interchangeably."""
    lines = bus._anchor_disclosures()
    blob = " ".join(lines)
    assert "partly derived" in blob
    assert "registered proxy" in blob
    assert "Not scored" in blob

    for metric_id, (source, _) in bus.ANCHOR_PROVENANCE.items():
        if source == "VICTOR":
            assert metric_id not in blob, f"{metric_id} is Victor's, do not disclaim it"
        else:
            assert metric_id in blob, metric_id

    # The three groups make different claims and must not be conflated:
    # MIXED means the deciding boundary is his; PROXY means step 4
    # authorises the substitution; ENGINE means the metric is not scored
    # at all.
    partly = next(l for l in lines if "partly derived" in l)
    as_proxy = next(l for l in lines if "registered proxy" in l)
    unscored = next(l for l in lines if "Not scored" in l)
    groups = {"MIXED": partly, "PROXY": as_proxy, "ENGINE": unscored}
    for metric_id, (source, _) in bus.ANCHOR_PROVENANCE.items():
        if source not in groups:
            continue
        assert metric_id in groups[source], metric_id
        for other, line in groups.items():
            if other != source:
                assert metric_id not in line, f"{metric_id} appears under {other}"


def test_a_derived_anchor_names_the_band_text_it_comes_from():
    """A MIXED entry claims a boundary is Victor's, so it has to say
    which sentence of his fixes it — otherwise the classification is an
    assertion rather than a citation."""
    for metric_id, (source, note) in bus.ANCHOR_PROVENANCE.items():
        if source != "MIXED":
            continue
        assert ".md" in note, f"{metric_id}: no document named"
        assert any(w in note for w in ("'", '"', "band", "clips", "signal", "row")), \
            f"{metric_id}: does not quote the band text it rests on"


def test_the_victor_anchors_are_the_ones_he_actually_states():
    """Spot-check against the documents: these four thresholds appear
    verbatim in DECISION_RULES.md and FORMULAS.md."""
    for metric_id in ("BUS-CONC-003", "BUS-RANGE-010", "BUS-ROIC-013",
                      "BUS-SPREAD-014", "BUS-DIL-028"):
        assert bus.ANCHOR_PROVENANCE[metric_id][0] == "VICTOR", metric_id


# --- MISSING_DATA_POLICY.md step 5 ----------------------------------------


def test_a_metric_with_no_registered_scale_publishes_no_score():
    """SCORING_ENGINE.md opens two scoring paths: an absolute band
    interpolated "between registered anchors", and a peer percentile
    needing "a minimum of 8 valid peers". MISSING_DATA_POLICY.md closes
    the list: "Otherwise, use `NOT_SCORABLE`".

    An ENGINE-provenance metric has neither — Cerebro states no threshold
    and no peer set exists for that quantity — so it keeps its computed
    value and withholds the score."""
    assert bus._authorised_score("BUS-PAYBACK-026", 8.0) is None
    assert bus._authorised_score("BUS-SBC-030", 8.0) is None


def test_a_registered_proxy_is_allowed_to_score():
    """Step 4 of the same tree: "Is a proxy explicitly registered? If
    yes, use it with a proxy flag and lower model-fit confidence." That
    authorises the score, so a proxy is not swept up by step 5."""
    assert bus._authorised_score("BUS-CAGR-006", 7.5) == pytest.approx(7.5)


@pytest.mark.parametrize("metric_id", ["BUS-CONC-003", "BUS-ROIC-013",
                                       "BUS-SPREAD-014", "BUS-NRR-020"])
def test_a_metric_anchored_on_victors_thresholds_still_scores(metric_id):
    """The rule must not swallow the metrics whose scales are his."""
    assert bus._authorised_score(metric_id, 6.0) == pytest.approx(6.0)


def test_an_unknown_metric_is_not_silently_blocked():
    """A metric absent from the provenance table is a bookkeeping gap,
    not a scoring decision — `test_every_scored_metric_declares_where_its
    _anchors_came_from` is what catches that, and it should not be
    masked by silently withholding the score here."""
    assert bus._authorised_score("BUS-NOT-IN-TABLE", 5.0) == pytest.approx(5.0)


def test_withholding_a_score_does_not_withhold_the_value():
    """"Otherwise, use NOT_SCORABLE" governs the score. The computed
    figure is real evidence and stays in the audit trail."""
    assert bus._authorised_score("BUS-SBC-030", 9.0) is None
    # sbc_burden itself still computes.
    assert bus.sbc_burden(42.5, 1000.0).is_valid


# --- calibration this engine chose ----------------------------------------


def test_every_unbacked_constant_is_registered():
    """A number that is not Victor's must be findable in one place, not
    scattered through the module. If a new one appears without an entry
    here, this fails."""
    import re
    from pathlib import Path

    src = Path(bus.__file__).read_text(encoding="utf-8")
    declared = set(re.findall(r"^([A-Z][A-Z_]{6,})\s*(?::[^=]+)?=\s*[\d.]+\s*$",
                              src, re.M))
    # Constants derived from Cerebro rather than chosen here.
    derived = {
        "MAX_POINTS",                     # SCORING.md: category maximum 20
        "WIDE_MOAT_PERSISTENCE_WINDOW",   # DECISION_RULES.md: last five years
        "WIDE_MOAT_MIN_SPREAD",           # DECISION_RULES.md: 5 percentage points
        "WIDE_MOAT_MIN_PERSISTENCE",      # DECISION_RULES.md: four of five
        "REQUIRED_THESIS_KILLERS",        # DECISION_RULES.md: "list three"
        "INCREMENTAL_ROIC_WINDOW_YEARS",  # FORMULAS.md: BUS-IROIC-016 "3y rolling"
        "WIDE_MOAT_MIN_EFFECTS",          # DECISION_RULES.md: "At least two"
        "CAGR_WINDOW_YEARS",              # FORMULAS.md: BUS-CAGR-006/DIL-028 "3y and 5y"
    }
    unaccounted = declared - derived - set(bus.CALIBRATION_REGISTRY)
    assert not unaccounted, f"unregistered constants: {sorted(unaccounted)}"


@pytest.mark.parametrize("name", sorted(bus.CALIBRATION_REGISTRY))
def test_each_calibration_entry_quotes_its_cerebro_basis(name):
    """The entry has to name the sentence that fixes the direction, so a
    reader can check that Cerebro really is silent on the magnitude."""
    basis, why = bus.CALIBRATION_REGISTRY[name]
    assert ".md" in basis or "BUS-" in basis, f"{name}: no source named"
    assert '"' in basis, f"{name}: does not quote the text"
    assert len(why) > 40, f"{name}: does not say why no number follows"
    assert hasattr(bus, name), f"{name}: registered but does not exist"


def test_the_calibration_is_disclosed_in_the_output():
    """AGENT.md permits a number that is not a reported value only as an
    "explicitly disclosed assumption"."""
    lines = bus.calibration_disclosures()
    assert len(lines) == len(bus.CALIBRATION_REGISTRY)
    blob = " ".join(lines)
    for name in bus.CALIBRATION_REGISTRY:
        assert name in blob
        assert repr(getattr(bus, name)) in blob, f"{name}: value not shown"


def test_a_derived_constant_is_not_disclaimed_as_calibration():
    """Claiming Victor's own numbers as this engine's would be as wrong
    as the reverse."""
    blob = " ".join(bus.calibration_disclosures())
    for name in ("WIDE_MOAT_MIN_SPREAD", "WIDE_MOAT_PERSISTENCE_WINDOW",
                 "MODEL_FIT_PROXY_FLOOR"):
        assert name not in blob, f"{name} is derived from Cerebro"


# --- provenance notes must cite their own row ------------------------------


def _formulas_md():
    from pathlib import Path

    path = (Path(bus.__file__).parents[3] / "Cerebro" / "01_business_analysis"
            / "FORMULAS.md")
    if not path.is_file():
        pytest.skip("Cerebro not present")
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 7 and cells[1].startswith("BUS-"):
            rows[cells[1]] = line
    return rows


@pytest.mark.parametrize("metric_id", sorted(bus.ANCHOR_PROVENANCE))
def test_a_provenance_note_citing_formulas_md_quotes_its_own_row(metric_id):
    """A note that credits a formula with a threshold has to find that
    threshold in that formula's own row.

    BUS-STAB-009 was classified MIXED on the strength of "<=3pp as the
    positive signal" — a threshold belonging to BUS-RANGE-010, the row
    below it. Its own caveat is "Lower is better; also report range and
    recession-year drawdown": a ranking direction and no threshold at
    all. The mis-citation credited it with a scale Cerebro never gives
    it, and nothing checked."""
    import re

    source, note = bus.ANCHOR_PROVENANCE[metric_id]
    if "FORMULAS.md" not in note:
        return
    row = _formulas_md().get(metric_id)
    assert row, f"{metric_id} is not a row in FORMULAS.md"

    # Thresholds the note claims this row states, e.g. "<=3pp", ">=0.9x".
    # `(?<!-)` keeps an arrow like "burden->10" from reading as ">10".
    claimed = re.findall(r"(?<!-)[<>]=?\s*[\d.]+\s*(?:pp|%|x)?", note)
    for threshold in claimed:
        digits = re.search(r"[\d.]+", threshold).group()
        assert digits in row, (
            f"{metric_id}: note cites {threshold!r} but its own FORMULAS.md "
            f"row does not contain {digits!r}")


def test_margin_stability_is_scored_by_the_range_his_documents_tie_to_the_moat():
    """Both formulas could answer to "margin stability", and his own text
    settles it. BUS-RANGE-010's caveat is "A range <=3 percentage points
    is a positive moat signal in the source framework", and
    DECISION_RULES.md's wide-moat gate condition 2 is "Five-year
    operating-margin range is no more than 5 percentage points".
    BUS-STAB-009 carries the input's name and no threshold, so neither of
    SCORING_ENGINE.md's scoring paths is open to it."""
    assert bus.ANCHOR_PROVENANCE["BUS-RANGE-010"][0] == "VICTOR"
    assert bus.ANCHOR_PROVENANCE["BUS-STAB-009"][0] == "ENGINE"
    assert bus._authorised_score("BUS-STAB-009", 8.0) is None

    row = _formulas_md()["BUS-RANGE-010"]
    assert "moat signal" in row


# --- documentation that outlived its code ---------------------------------


def test_no_comment_cites_a_constant_that_no_longer_exists():
    """A comment describing removed code is worse than no comment: it
    tells a reader the module does something it does not.

    `MOAT_CLASSIFICATION_SLOT_INDEX` survived its own deletion this way,
    in a block sitting directly above the comment that contradicted it —
    two opposite explanations, back to back.

    A name cited in backticks and appearing nowhere else in the file is
    the signature: every live reference occurs at least once as code, as
    a string literal, or as an enum member.
    """
    import re
    from pathlib import Path

    src = Path(bus.__file__).read_text(encoding="utf-8")
    cited = set(re.findall(r"`([A-Z][A-Z0-9_]{5,})`", src))
    stale = []
    for name in sorted(cited):
        # Occurrences outside backticks: code, string literals, enum members.
        elsewhere = len(re.findall(rf"(?<!`)\b{re.escape(name)}\b(?!`)", src))
        if elsewhere == 0:
            stale.append(name)
    assert not stale, f"comments cite constants that no longer exist: {stale}"


# --- FORMULAS.md: "Show calculation inputs in the output packet" -----------


def test_every_formula_declares_the_inputs_the_registry_states():
    """One of FORMULAS.md's five execution rules, and DATA_POLICY.md's
    lineage block carries `calculation_inputs`. Nothing implemented either,
    so a reader saw a value, a formula id and a version but not which
    quantities went in. The text is his "Required inputs" column, not a
    restatement, so this reads the registry and compares."""
    import re
    from pathlib import Path

    registry = Path(__file__).resolve().parents[2] / "Cerebro" / \
        "01_business_analysis" / "FORMULAS.md"
    stated = {}
    for line in registry.read_text(encoding="utf-8").splitlines():
        if line.startswith("| BUS-"):
            cells = [c.strip() for c in line.split("|")]
            stated[cells[1]] = cells[4]

    assert set(stated) == set(bus.BUS_30_IDS), "registry drifted from BUS_30_IDS"
    for metric_id, inputs in stated.items():
        assert bus.FORMULA_INPUTS[metric_id] == inputs, metric_id
        # The lineage field wants a list, split on the registry's own commas.
        assert bus.formula_inputs(metric_id) == [p.strip() for p in inputs.split(",")]


def test_every_metric_row_carries_its_calculation_inputs():
    from pathlib import Path

    from wbj.schemas.packet import Packet

    fixture = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"
    packet = Packet.model_validate_json(fixture.read_text(encoding="utf-8"))
    out = bus.run(packet, {"wacc": 0.09})
    assert out.metrics
    for row in out.metrics:
        assert row.calculation_inputs, f"{row.metric_id}: no calculation_inputs"
