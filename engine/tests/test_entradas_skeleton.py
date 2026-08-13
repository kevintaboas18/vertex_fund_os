"""The `Entradas/<TICKER>.json` skeleton: complete, inert, and never clobbering.

Writing this file by hand means knowing fourteen key names and their shapes,
which is how `debt_schedule` got documented once without `unlevered_fcf` — the
metric needs both and stayed MISSING with only one.

Three properties matter and each has to hold on its own:

- **Complete**: every key a specialist names in its own "set `key`" warning is
  in the skeleton. Asserted by grepping the specialists, so an input added
  later cannot silently go undocumented.
- **Inert**: all values are `null`. A skeleton that scored would put invented
  market sizing behind a real number — PROHIBITED_IMPUTATION, and the reason
  the loader refuses `EJEMPLO.*` files outright.
- **Non-destructive**: `Entradas/NVDA.json` holds hand-captured figures. A
  generator that overwrote them would destroy work no provider can return.
"""

import json
import re
from pathlib import Path

from wbj.entradas import SKELETON_KEYS, render_skeleton, write_skeleton
from wbj.overlay.from_packet import _manual_overlay

_SPECIALISTS = Path(__file__).parent.parent / "wbj" / "specialists"


def _keys_the_engine_asks_for() -> set[str]:
    """Every analyst-supplied key the specialists read.

    Two phrasings, because the engine uses both and the first pass only
    caught one. A metric whose remedy reads "set `key`" names it outright;
    one that reads its input through `_overlay_mapping(overlay, "key", ...)`
    or `_overlay_number(overlay, "key", ...)` names it in the call instead.

    The second form is how the seven customer-economics metrics
    (BUS-NRR-020..BUS-PAYBACK-026) ask for `retention`, `churn` and
    `customer_economics` -- their warning says "_NO_OVERLAY", never "set
    `key`", so this guard passed while all three went undocumented and the
    3-point dimension had no way to be filled.

    Static, so it runs without network or an API key.
    """
    found: set[str] = set()
    for path in _SPECIALISTS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"set `([a-z_0-9]+)`", text):
            found.add(match.group(1))
        for match in re.finditer(
                r"_overlay_(?:mapping|number|numbers)\(\s*overlay,\s*\"([a-z_0-9]+)\"", text):
            found.add(match.group(1))
        # Third phrasing, and the one that hid the catalyst dimension:
        # `overlay.get("catalysts")` names no remedy and calls no helper, so
        # both earlier passes walked past it while four inputs the engine
        # reads had nowhere to be written down.
        for match in re.finditer(r"overlay\.get\(\s*\"([a-z_0-9]+)\"", text):
            found.add(match.group(1))
    return found


class _S:
    def __init__(self, path):
        self.inputs_dir = path


# --- complete ---------------------------------------------------------------

def test_the_skeleton_covers_every_key_the_engine_names():
    """The drift guard. `share` is excluded: MKT-SHARE-006's own warning says
    market share is PROHIBITED_IMPUTATION, and its remedy names an overlay the
    analyst file does not carry."""
    # Not analyst inputs, so the skeleton must not offer them as blanks:
    #
    # - computed by `build_overlay` from the packet;
    # - read straight off the packet (beta, rates, filed lines);
    # - documented defaults the analyst overrides only rarely;
    # - internal bookkeeping the loader itself writes;
    # - nested inside a template key that IS documented -- `above_50dma_count`
    #   lives in `sector_breadth`, `committed_liquidity` in `cash_burn`.
    _COMPUTED = {"wacc", "cost_of_equity", "segment_shares", "peer_roic",
                 "peer_operating_margin", "peer_recession_drawdown",
                 "recession_years", "dividend_per_share",
                 "dividends_per_share_history", "eps_growth_pct",
                 "geographic_shares", "historical_multiples", "macro_series",
                 "margin_of_safety", "product_shares", "reit_supplement",
                 "rs_universe", "segment_revenue",
                 # Lo escribe `overlay/from_packet.py` mirando si el emisor
                 # etiqueta RevenueRemainingPerformanceObligation en su XBRL.
                 # No se le pide a nadie: es la evidencia de EDGAR con la que
                 # MKT-BACK-015 y MKT-COVER-016 distinguen "no lo tengo" de
                 # "no aplica".
                 "_backlog_reportado"}
    _FROM_PACKET = {"beta", "risk_free_rate", "interest_expense",
                    "company_series", "earnings_dates", "depreciation", "ppe"}
    _DEFAULTS = {"erp", "terminal_growth", "tv_growth", "forecast_years"}
    _INTERNAL = {"analyst_input_warnings", "period", "estimates", "judgments",
                 "debt_due",
        # Lo calcula el MOTOR, no lo escribe un analista: dice si el emisor
        # presenta comunicado de resultados ante la SEC, que es la fuente
        # que DATASET.md declara para el guidance. Pedirlo en el esqueleto
        # invitaria a declarar a mano un hecho que EDGAR ya responde.
        "_sin_comunicado_de_resultados",
        # Igual: la calcula el OVERLAY desde EDGAR. Cuando consta que ningun
        # cliente llega al 10% de los ingresos (ASC 280-10-50-42), el HHI
        # queda acotado por arriba, y esa cota se DEMUESTRA -- si toda cuota
        # <= 0,10 y suman 1, sum(s^2) <= 0,10. No se teclea. Lo que el
        # analista si suministra es `customer_shares`, que ya esta en el
        # esqueleto.
        "customer_hhi_upper_bound",
    }
    _NESTED = {"above_50dma", "above_50dma_count", "committed_liquidity"}
    asked = (_keys_the_engine_asks_for() - {"share"} - _COMPUTED
             - _FROM_PACKET - _DEFAULTS - _INTERNAL - _NESTED)
    missing = asked - set(SKELETON_KEYS)
    assert not missing, f"el esqueleto no documenta: {sorted(missing)}"


def test_apv_needs_both_of_its_keys_and_both_are_present():
    """The concrete miss this guard exists for."""
    assert "debt_schedule" in SKELETON_KEYS
    assert "unlevered_fcf" in SKELETON_KEYS


def test_the_market_sizing_block_carries_its_attribution_keys():
    """DECISION_RULES.md drops a `tam` with no source and tier, so a skeleton
    offering `tam` alone would document a figure that cannot score."""
    for key in ("tam", "tam_source", "tam_source_tier", "tam_history"):
        assert key in SKELETON_KEYS


# --- inert ------------------------------------------------------------------

def test_every_value_is_null():
    data = json.loads(render_skeleton("TEST"))
    for key in SKELETON_KEYS:
        assert data[key] is None, f"{key} trae un valor inventado"


def test_the_skeleton_is_valid_json_and_names_its_ticker():
    text = render_skeleton("aapl")
    data = json.loads(text)
    assert any("AAPL" in str(v) for v in data.values())


def test_a_written_skeleton_loads_to_nothing_at_all(tmp_path):
    """End to end: an unfilled skeleton must reach the specialists as if no
    file existed.

    This asserted the opposite -- that the nulls were *carried* -- and that is
    what shipped the crash. The read pattern across the specialists is
    `float(overlay.get(key, default))`, and a default only applies when the
    key is ABSENT: `float(None)` raises. Once the skeleton covered the full
    input set, a freshly-analysed ticker killed the financial specialist on
    `equity_issuance`, with eleven more keys behind it.
    """
    write_skeleton(tmp_path, "TEST")
    out = _manual_overlay(_S(tmp_path), "TEST")
    assert not any(k.startswith("_") for k in out), "comentarios filtrados al overlay"
    assert not any(v is None for v in out.values()), "un null llego al overlay"


def test_an_unfilled_skeleton_cannot_crash_a_specialist(tmp_path):
    """The concrete failure, pinned: every `float(overlay.get(k, d))` site in
    the engine must survive a skeleton where nothing is filled in."""
    write_skeleton(tmp_path, "TEST")
    out = _manual_overlay(_S(tmp_path), "TEST")
    for key, default in (("equity_issuance", 0.0), ("lease_charge", 0.0),
                         ("erp", 0.045), ("tv_growth", 0.025),
                         ("forecast_years", 5), ("pretax_kd", 0.05)):
        float(out.get(key, default))  # raises TypeError if a null survived


# --- non-destructive --------------------------------------------------------

def test_an_existing_file_is_never_overwritten(tmp_path):
    """The NVDA case: real captured figures must survive a regeneration."""
    path = tmp_path / "NVDA.json"
    path.write_text(json.dumps({"tam": 400_000_000_000, "tam_source_tier": 4}),
                    encoding="utf-8")
    written, message = write_skeleton(tmp_path, "NVDA")
    assert written is False
    assert "ya existe" in message
    assert json.loads(path.read_text(encoding="utf-8"))["tam"] == 400_000_000_000


def test_force_overwrites_only_when_asked(tmp_path):
    path = tmp_path / "NVDA.json"
    path.write_text(json.dumps({"tam": 1}), encoding="utf-8")
    written, _ = write_skeleton(tmp_path, "NVDA", force=True)
    assert written is True
    assert json.loads(path.read_text(encoding="utf-8"))["tam"] is None


def test_the_ticker_is_uppercased_in_the_filename(tmp_path):
    write_skeleton(tmp_path, "msft")
    assert (tmp_path / "MSFT.json").is_file()


def test_a_missing_directory_is_created(tmp_path):
    target = tmp_path / "Entradas"
    written, _ = write_skeleton(target, "KO")
    assert written is True and (target / "KO.json").is_file()


# --- created on first analysis ----------------------------------------------

def test_analysing_a_ticker_puts_its_skeleton_in_place(tmp_path):
    """Request: analysing a stock should leave its input file ready to fill,
    instead of making the analyst discover the twelve keys one NOT_SCORABLE at
    a time."""
    from types import SimpleNamespace

    from wbj.report import _ensure_entradas_skeleton

    _ensure_entradas_skeleton(SimpleNamespace(inputs_dir=tmp_path), "tsla")
    assert (tmp_path / "TSLA.json").is_file()


def test_analysing_again_never_overwrites_captured_figures(tmp_path):
    """The safety property. Re-analysing NVDA must not destroy the numbers
    someone read out of its 10-K."""
    from types import SimpleNamespace

    from wbj.report import _ensure_entradas_skeleton

    path = tmp_path / "NVDA.json"
    path.write_text(json.dumps({"tam": 400_000_000_000, "tam_source_tier": 4}),
                    encoding="utf-8")
    _ensure_entradas_skeleton(SimpleNamespace(inputs_dir=tmp_path), "NVDA")
    assert json.loads(path.read_text(encoding="utf-8"))["tam"] == 400_000_000_000


def test_a_failure_to_write_never_breaks_the_analysis(tmp_path):
    """A convenience must not be able to fail a report. A settings object with
    no usable path stands in for a read-only directory."""
    from types import SimpleNamespace

    from wbj.report import _ensure_entradas_skeleton

    _ensure_entradas_skeleton(SimpleNamespace(inputs_dir=object()), "AAPL")
