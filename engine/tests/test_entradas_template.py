"""`Entradas/EJEMPLO.<TICKER>.json` documents what an analyst can supply.

The file is the only place the analyst-supplied inputs are written down, so it
drifts silently: a key renamed in the specialist leaves the template teaching a
name nothing reads, and a new overlay input added without a template entry is
invisible to the person expected to provide it. Both failures look like
"the formula just doesn't work".

These tests tie the template to the code that consumes it, and check the
guard that keeps illustrative figures from ever scoring.
"""

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
_TEMPLATE = _ROOT / "Entradas" / "EJEMPLO.NVDA.json"
_VALUATION = _ROOT / "engine" / "wbj" / "specialists" / "valuation.py"

#: Inputs the *engine* fills from EDGAR/FMP. They may appear in the template as
#: documentation of an override, but an analyst is not expected to supply them.
_AUTO_POPULATED = {
    "dividend_per_share", "dividends_per_share_history", "dividend_tag",
    "dividend_period_end", "segment_revenue", "segment_shares", "product_shares",
    "geographic_shares", "historical_multiples", "eps_growth_pct", "wacc",
    "cost_of_equity", "ntm_contracted", "ntm_revenue_estimate", "peer_multiples",
    "peer_roic", "peer_operating_margin", "peer_recession_drawdown",
    "backlog_history", "recession_years", "rs_universe", "macro_series",
    "company_series", "margin_of_safety", "estimates", "scenarios",
    "interest_expense", "risk_free_rate", "beta", "erp", "terminal_growth",
    # Located by the EDGAR provider and cited in the output; the analyst reads
    # figures OUT of it, but never supplies the locator itself.
    "reit_supplement",
    # Derived by the packet builder from the earnings calendar and price
    # evidence; the overlay read is an override, not the normal path.
    # Derivada por el overlay desde EDGAR, no tecleada: cuando consta que
    # ningun cliente llega al 10% de los ingresos (ASC 280-10-50-42), el HHI
    # queda acotado por arriba. El analista suministra `customer_shares`; esta
    # cota la calcula el motor y por eso no va en la plantilla.
    "customer_hhi_upper_bound",
    "earnings_dates",
}


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(_TEMPLATE.read_text(encoding="utf-8"))


#: Every regex below is anchored with `(?<![a-z_])` so `breadth_overlay.get(...)`
#: does not read as `overlay.get(...)`: keys nested inside a block are not
#: top-level analyst inputs, and matching them reported `above_50dma` as an
#: undocumented input when it lives under `sector_breadth`.
#:
#: Every regex below also matches `[a-z0-9_]+`, not `[a-z_]+`: an overlay key can
#: carry a digit (`arpu_t1`, the prior period of a growth rate), and a pattern
#: that excludes digits reports such a key as read by nothing -- a blind spot
#: in the drift check is a blind spot in the template it guards.
#:
#: Internal plumbing, not analyst inputs: warning channels and keys nested
#: inside another block (`valid_members` lives under `sector_breadth`).
_INTERNAL = {"analyst_input_warnings", "valid_members", "warnings", "period",
             "contract_protection",
    # Lo calcula el MOTOR, no lo escribe un analista: dice si el emisor
    # presenta comunicado de resultados ante la SEC, que es la fuente
    # que DATASET.md declara para el guidance. Pedirlo en el esqueleto
    # invitaria a declarar a mano un hecho que EDGAR ya responde.
    "_sin_comunicado_de_resultados",
}


def _specialist_overlay_keys() -> set[str]:
    """Every overlay key ANY specialist reads.

    This checked `valuation.py` alone, which is how 27 inputs across the other
    five specialists came to be read by the engine and documented nowhere --
    BUS-GUIDE-027 sat MISSING on every ticker for want of a `guidance_history`
    nobody knew to supply. A blind spot in the test is a blind spot in the
    template.
    """
    import re
    keys: set[str] = set()
    for mod in ("business", "financial", "market", "technical", "risk", "valuation"):
        src = (_ROOT / "engine" / "wbj" / "specialists" / f"{mod}.py").read_text(encoding="utf-8")
        keys |= set(re.findall(r'(?<![a-z_])overlay\.get\("([a-z0-9_]+)"', src))
        keys |= set(re.findall(r'_overlay_numbers?\(overlay, "([a-z0-9_]+)"', src))
    return keys - _INTERNAL


#: Both ways a specialist reads a scalar off the overlay. `overlay_float`
#: is the null-safe wrapper -- `float(overlay.get(k, d))` applies its default
#: only when the key is ABSENT, so an unfilled skeleton's `null` reached
#: `float(None)` and killed the run. Rewriting those sites hid nine keys from
#: this detector until it learned the second form.
_READ_PATTERNS = (
    r'(?<![a-z_])overlay\.get\("([a-z0-9_]+)"',
    r'overlay_float\(\s*overlay,\s*"([a-z0-9_]+)"',
    # Third form, and the one that hid the customer-economics block:
    # `_overlay_mapping(overlay, "retention", ...)`. Three ways to read the
    # same dict is two too many, but a detector that knows only one reports
    # documented keys as orphans and undocumented ones as covered.
    r'_overlay_(?:mapping|number|numbers)\(\s*overlay,\s*"([a-z0-9_]+)"',
)


def _overlay_reads(src: str) -> set[str]:
    import re
    found: set[str] = set()
    for pattern in _READ_PATTERNS:
        found |= set(re.findall(pattern, src))
    return found


def _valuation_overlay_keys() -> set[str]:
    return _overlay_reads(_VALUATION.read_text(encoding="utf-8"))


def test_the_template_parses(template):
    assert isinstance(template, dict) and template


def test_every_analyst_input_any_specialist_reads_is_documented(template):
    """A key the engine reads but the template never mentions is an input
    nobody knows to provide -- and its metric sits MISSING forever."""
    expected = _specialist_overlay_keys() - _AUTO_POPULATED
    missing = sorted(expected - set(template))
    assert not missing, f"undocumented analyst inputs: {missing}"


def test_the_template_teaches_no_key_the_engine_ignores(template):
    """The reverse drift: a renamed key leaves the template teaching a name
    nothing reads."""
    from wbj.overlay import from_packet

    engine_keys = _valuation_overlay_keys()
    import re
    engine_keys |= set(re.findall(r'(?:(?<![a-z_])overlay\.get\(|overlay_float\(overlay, |_overlay_(?:mapping|number|numbers)\(overlay, )"([a-z0-9_]+)"',
                                  Path(from_packet.__file__).read_text(encoding="utf-8")))
    overlay_src = Path(from_packet.__file__).read_text(encoding="utf-8")
    engine_keys |= set(re.findall(r'overlay\["([a-z0-9_]+)"\]', overlay_src))
    # The Entradas loader validates some keys directly off the parsed file
    # (`data.get("tam_source")`), never through the overlay dict.
    engine_keys |= set(re.findall(r'data\.get\("([a-z0-9_]+)"', overlay_src))
    engine_keys |= set(re.findall(r'"([a-z0-9_]+)"', 
                                  re.search(r'for k in \(("tam".*?)\)', overlay_src, re.S).group(1)))
    for mod in ("business", "financial", "market", "technical", "risk"):
        src = (_ROOT / "engine" / "wbj" / "specialists" / f"{mod}.py").read_text(encoding="utf-8")
        engine_keys |= set(re.findall(r'(?:(?<![a-z_])overlay\.get\(|overlay_float\(overlay, |_overlay_(?:mapping|number|numbers)\(overlay, )"([a-z0-9_]+)"', src))
        engine_keys |= set(re.findall(r'_overlay_numbers?\(overlay, "([a-z0-9_]+)"', src))

    # `judgments` is read by deep.py's analyst channel, not by a specialist.
    engine_keys |= set(re.findall(r'\.get\("(judgments)"',
                                  (_ROOT / "engine" / "wbj" / "deep.py").read_text(encoding="utf-8")))
    documented = {k for k in template if not k.startswith("_comment")}
    orphans = sorted(documented - engine_keys)
    assert not orphans, f"template documents keys nothing reads: {orphans}"


def test_every_valuation_block_carries_an_explanation(template):
    """Each new input arrived with a comment saying what it is for and what
    happens without it; a bare number teaches nothing."""
    comments = " ".join(v for k, v in template.items()
                        if k.startswith("_comment") and isinstance(v, str))
    for metric_id in ("VAL-TVE-013", "VAL-SOTP-026", "VAL-CONV-039",
                      "VAL-APV-019", "VAL-ROPT-038"):
        assert metric_id in comments, f"{metric_id} inputs are undocumented"


def test_the_prohibitions_are_written_down(template):
    """The template is where an analyst learns the rules that make a refusal
    correct rather than broken."""
    comments = " ".join(v for k, v in template.items()
                        if k.startswith("_comment") and isinstance(v, str))
    assert "blended multiple" in comments        # SOTP: no borrowed multiple
    assert "INCOMPLETO" in comments              # VAL-T008
    assert "dos veces" in comments               # ROPT: DCF double count
    assert "WACC estatico" in comments           # APV's scope


def test_a_template_never_scores():
    """`EJEMPLO.*` ships with illustrative figures; loading them would put
    invented numbers behind a real score."""
    from types import SimpleNamespace

    from wbj.overlay.from_packet import _manual_overlay

    settings = SimpleNamespace(reports_dir=str(_TEMPLATE.parent.parent / "Reportes"))
    assert _manual_overlay(settings, "EJEMPLO.NVDA") == {}
    # And the real file for the same ticker still loads, so the guard is on
    # the EJEMPLO prefix rather than on the directory.
    assert isinstance(_manual_overlay(settings, "NVDA"), dict)


def test_holding_discount_and_distress_default_to_zero(template):
    """Victor's "_if_justified": an unjustified conglomerate discount or an
    assumed distress cost is a thumb on the scale, so the template ships the
    neutral value rather than an illustrative one."""
    assert template["holding_discount"] == 0.0
    assert template["distress_probability"] == 0.0
    assert template["distress_cost"] == 0.0


def test_sector_breadth_accepts_both_spellings_of_its_count():
    """One overlay key, two spellings: market.py read `above_50dma_count` and
    technical.py read `above_50dma`, both undocumented. Filling either name
    satisfied one metric and left the other MISSING -- and market's spelling
    passed technical's `valid_members` guard and then KeyError'd on a direct
    index, crashing the specialist rather than leaving its metric empty."""
    import json as _json

    from wbj.schemas.packet import Packet
    from wbj.specialists import market as mkt
    from wbj.specialists import technical as tech

    fixture = _ROOT / "engine" / "tests" / "fixtures" / "packet" / "NVDA_packet.json"
    packet = Packet.model_validate(_json.loads(fixture.read_text()))

    for spelling in ("above_50dma_count", "above_50dma"):
        overlay = {"sector_breadth": {spelling: 300, "valid_members": 500}}
        m = {r.metric_id: r for r in mkt.run(packet, overlay).metrics}["MKT-SECB-023"]
        t = {r.metric_id: r for r in tech.run(packet, overlay).metrics}["TECH-BREAD-039"]
        assert m.value == pytest.approx(0.6), f"market missed {spelling}"
        assert t.value == pytest.approx(0.6), f"technical missed {spelling}"


def test_the_documented_sector_breadth_shape_works():
    """The template ships one shape; it has to be the one that works."""
    import json as _json

    from wbj.schemas.packet import Packet
    from wbj.specialists import technical as tech

    shipped = _json.loads(_TEMPLATE.read_text(encoding="utf-8"))["sector_breadth"]
    fixture = _ROOT / "engine" / "tests" / "fixtures" / "packet" / "NVDA_packet.json"
    packet = Packet.model_validate(_json.loads(fixture.read_text()))
    row = {r.metric_id: r for r in tech.run(packet, {"sector_breadth": shipped}).metrics}
    assert row["TECH-BREAD-039"].value is not None


def test_the_shipped_template_runs_through_every_specialist():
    """The template's own values must work in the engine that reads them.

    Documenting 27 inputs without checking their inner shapes put
    `sam_inputs` in the file as `{customers, annual_spend}` when market.py
    indexes `geography_share`/`product_share`/`reachable_share` -- and the
    direct index turned a wrong shape into a KeyError that took down the whole
    specialist. The same failure `sector_breadth` had. A template nobody
    executes is documentation nobody can trust.
    """
    import json as _json

    from wbj.schemas.packet import Packet
    from wbj.specialists import business as biz
    from wbj.specialists import financial as fin
    from wbj.specialists import market as mkt
    from wbj.specialists import risk as rsk
    from wbj.specialists import technical as tech
    from wbj.specialists import valuation as val

    overlay = {k: v for k, v in
               _json.loads(_TEMPLATE.read_text(encoding="utf-8")).items()
               if not k.startswith("_comment") and v is not None}
    fixture = _ROOT / "engine" / "tests" / "fixtures" / "packet" / "NVDA_packet.json"
    packet = Packet.model_validate(_json.loads(fixture.read_text()))

    for name, mod in (("business", biz), ("financial", fin), ("market", mkt),
                      ("technical", tech), ("risk", rsk), ("valuation", val)):
        out = mod.run(packet, overlay)          # must not raise
        assert out.metrics, f"{name} produced no metrics from the template"


def test_the_sam_and_som_blocks_are_fractions_of_the_tam():
    """They are shares, not standalone currency amounts -- the shape the
    template originally taught. A wrong shape now leaves the metric MISSING
    with the required keys named, instead of crashing."""
    import json as _json

    from wbj.schemas.packet import Packet
    from wbj.specialists import market as mkt

    tpl = _json.loads(_TEMPLATE.read_text(encoding="utf-8"))
    assert set(tpl["sam_inputs"]) == {"geography_share", "product_share", "reachable_share"}
    assert set(tpl["som_inputs"]) == {"target_share"}

    fixture = _ROOT / "engine" / "tests" / "fixtures" / "packet" / "NVDA_packet.json"
    packet = Packet.model_validate(_json.loads(fixture.read_text()))
    bad = {"tam": 1.0e12, "tam_source": "x", "tam_source_tier": 3,
           "sam_inputs": {"customers": 5, "annual_spend": 10}}
    rows = {r.metric_id: r for r in mkt.run(packet, bad).metrics}   # must not raise
    assert rows["MKT-SAM-002"].value is None
    assert any("SAM_INPUTS_INCOMPLETE" in w for w in rows["MKT-SAM-002"].warnings)


#: What each documented analyst input is supposed to make computable, and the
#: engine-filled companion it needs when there is one. Written from the code
#: that reads the key, not from the metric's name -- reading it the other way
#: is how `sam_inputs` came to be documented as `{customers, annual_spend}`
#: when market.py indexes `{geography_share, product_share, reachable_share}`.
_INPUT_PRODUCES = {
    "tam": ("MKT-TAM-001", {}),
    "tam_history": ("MKT-CAGR-004", {}),
    "sam_inputs": ("MKT-SAM-002", {}),
    "som_inputs": ("MKT-SOM-003", {}),
    "share": ("MKT-SHARE-006", {}),
    "share_history": ("MKT-SHDELTA-007", {}),
    "competitor_shares": ("MKT-HHI-008", {}),
    "current_revenue": ("MKT-RUN-010", {}),
    "estimates": ("MKT-REVBR-011", {}),
    "adoption": ("MKT-ADOPT-021", {}),
    # A growth rate needs both periods; one alone leaves it MISSING.
    "arpu_t": ("MKT-ARPU-022", {}),
    "sector_breadth": ("MKT-SECB-023", {}),
    # Divided by a consensus figure the engine fetches.
    "ntm_contracted": ("MKT-COVER-016", {"ntm_revenue_estimate": 5.0e9}),
    "customer_shares": ("BUS-HHI-004", {}),
    "largest_customer_share": ("BUS-CONC-003", {}),
    "recurring_revenue": ("BUS-REC-002", {}),
    "guidance_history": ("BUS-GUIDE-027", {}),
    "exit_multiple": ("VAL-TVE-013", {}),
    "convertibles": ("VAL-CONV-039", {}),
    "debt_schedule": ("VAL-APV-019", {}),
    "real_option_project": ("VAL-ROPT-038", {}),
    # Multiplied by the segmentation the engine fetches from FMP.
    "segment_multiples": ("VAL-SOTP-026",
                          {"segment_revenue": {"A": 3.0e9, "B": 2.0e9},
                           "segment_multiples": {"A": 4.0, "B": 5.0}}),
}


def test_every_documented_input_actually_produces_its_metric():
    """Not crashing is not the same as working.

    The earlier check ran the template through all six specialists and
    asserted only that nothing raised. A key documented with the wrong inner
    shape does not raise once the callers stop indexing blindly -- it leaves
    the metric MISSING, which looks identical to having supplied nothing. That
    is how three inputs sat in the file teaching a reader they would do
    something they did not: `arpu_t` without its `arpu_t1` pair, and two
    whose engine-filled companion was never mentioned.
    """
    import json as _json

    from wbj.schemas.packet import Packet
    from wbj.specialists import business as biz
    from wbj.specialists import market as mkt
    from wbj.specialists import valuation as val

    template = {k: v for k, v in
                _json.loads(_TEMPLATE.read_text(encoding="utf-8")).items()
                if not k.startswith("_comment") and v is not None}
    fixture = _ROOT / "engine" / "tests" / "fixtures" / "packet" / "NVDA_packet.json"
    packet = Packet.model_validate(_json.loads(fixture.read_text()))

    failures = []
    for key, (metric_id, companions) in _INPUT_PRODUCES.items():
        assert key in template, f"{key} is no longer documented"
        overlay = {**template, **companions}
        rows = {}
        for mod in (biz, mkt, val):
            rows.update({m.metric_id: m for m in mod.run(packet, overlay).metrics})
        row = rows.get(metric_id)
        if row is None or row.value is None:
            reason = (row.warnings or ["metric not emitted"])[0] if row else "metric not emitted"
            failures.append(f"{key} -> {metric_id}: {reason[:60]}")
    assert not failures, "documented inputs that do not produce their metric:\n" + "\n".join(failures)


def test_arpu_is_documented_as_a_pair():
    """MKT-ARPU-022 is `(ARPU_t - ARPU_t-1) / ARPU_t-1`. Documenting one side
    of a growth rate teaches a reader to supply half of it."""
    template = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
    assert template.get("arpu_t") is not None
    assert template.get("arpu_t1") is not None


def test_the_companion_dependencies_are_written_down():
    """An input whose partner comes from a provider fails silently when that
    provider is unavailable; the file has to say so."""
    comments = " ".join(v for k, v in json.loads(_TEMPLATE.read_text(encoding="utf-8")).items()
                        if k.startswith("_comment") and isinstance(v, str))
    assert "arpu_t1" in comments
    assert "ntm_revenue_estimate" in comments
    assert "segment_revenue" in comments


def test_nested_inputs_are_documented_inside_their_block():
    """`committed_liquidity` is read as `cash_burn["committed_liquidity"]`,
    not off the overlay. Documenting it as a top-level key taught a shape the
    engine never reads -- caught only once the drift check stopped matching
    `breadth_overlay.get(...)` as `overlay.get(...)`."""
    template = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
    assert "committed_liquidity" not in template
    assert isinstance(template.get("cash_burn"), dict)
    assert "committed_liquidity" in template["cash_burn"]
