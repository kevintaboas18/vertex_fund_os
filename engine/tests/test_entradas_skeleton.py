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
    """Every key named in a "set `key`" remedy across the specialists.

    Static, so it runs without network or an API key.
    """
    found: set[str] = set()
    for path in _SPECIALISTS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"set `([a-z_0-9]+)`", text):
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
    asked = _keys_the_engine_asks_for() - {"share"}
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


def test_a_written_skeleton_loads_to_nothing_but_nulls(tmp_path):
    """End to end: what the generator writes, the loader reads — and the
    comment lines must not survive into the overlay."""
    write_skeleton(tmp_path, "TEST")
    out = _manual_overlay(_S(tmp_path), "TEST")
    assert not any(k.startswith("_") for k in out), "comentarios filtrados al overlay"
    assert all(v is None for k, v in out.items() if k != "judgments")
    assert set(SKELETON_KEYS).issubset(out)


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
