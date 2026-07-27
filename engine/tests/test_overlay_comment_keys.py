"""Comment keys in `Entradas/<TICKER>.json` must never reach the overlay.

The file is hand-written and needs room to explain itself — `EJEMPLO.NVDA.json`
carries thirty-odd notes. The loader popped only the exact key `_comment`, so
every numbered variant (`_1`, `_comment_7`, …) travelled into the overlay as if
it were supplied data. Nothing reads them, but an overlay is audit-trail
material and prose sitting in it can be mistaken for a figure the analyst gave.
"""

import json

import pytest

from wbj.overlay.from_packet import _manual_overlay


class _S:
    def __init__(self, path):
        self.inputs_dir = path


def _write(tmp_path, ticker, payload):
    (tmp_path / f"{ticker}.json").write_text(json.dumps(payload), encoding="utf-8")
    return _S(tmp_path)


def test_numbered_comment_keys_are_stripped(tmp_path):
    s = _write(tmp_path, "TEST", {
        "_1": "una nota", "_comment_7": "otra", "_comment": "la tercera",
        "largest_customer_share": 0.22,
    })
    out = _manual_overlay(s, "TEST")
    assert out == {"largest_customer_share": 0.22}


def test_real_data_survives(tmp_path):
    s = _write(tmp_path, "TEST", {
        "_note": "x", "exit_multiple": 11.5, "customer_shares": [0.2, 0.1],
    })
    out = _manual_overlay(s, "TEST")
    assert out["exit_multiple"] == 11.5
    assert out["customer_shares"] == [0.2, 0.1]
    assert not any(k.startswith("_") for k in out)


def test_a_file_of_only_comments_yields_nothing(tmp_path):
    s = _write(tmp_path, "TEST", {"_1": "a", "_2": "b"})
    assert _manual_overlay(s, "TEST") == {}


def test_null_values_are_carried_not_crashed_on(tmp_path):
    """A skeleton file ships every key as null so an analyst can fill it in
    place. It must behave exactly like no file at all, never like a supplied
    zero."""
    s = _write(tmp_path, "TEST", {"_1": "nota", "exit_multiple": None,
                                  "customer_shares": None})
    out = _manual_overlay(s, "TEST")
    assert out["exit_multiple"] is None
    assert out["customer_shares"] is None
