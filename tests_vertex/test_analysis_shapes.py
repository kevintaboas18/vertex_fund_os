"""Regresion: /api/analyze devolvia 500 cuando el LLM mandaba un campo anidado
con el tipo equivocado.

Traceback real de produccion (AAPL):

    File "vertex_api.py", line 8511, in analyze_ticker
        p_pos = _pi("p_positive_12m", 50)
    File "vertex_api.py", line 8508, in _pi
        return max(0, min(100, int(probs.get(k, d) or d)))
    AttributeError: 'str' object has no attribute 'get'

Los 6 especialistas de Victor ya habian terminado y los targets estaban
calculados. La prosa del LLM tumbo el reporte entero, otra vez.
"""
import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def api():
    spec = importlib.util.spec_from_file_location("vapi_shapes", _ROOT / "vertex_api.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vapi_shapes"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_string_probabilities_becomes_empty_dict(api):
    """El caso exacto que reventaba."""
    out = api._coerce_analysis_shapes({"probabilities": "60% de probabilidad alcista"})
    assert out["probabilities"] == {}


def test_json_string_probabilities_is_parsed(api):
    """Si el string SI trae el JSON dentro, se recupera en vez de tirarlo."""
    out = api._coerce_analysis_shapes({"probabilities": '{"p_positive_12m": 55}'})
    assert out["probabilities"] == {"p_positive_12m": 55}


def test_malformed_json_string_is_discarded(api):
    out = api._coerce_analysis_shapes({"probabilities": '{"p_positive_12m": '})
    assert out["probabilities"] == {}


def test_json_string_of_wrong_type_is_discarded(api):
    """Un JSON valido que no es objeto tampoco sirve."""
    out = api._coerce_analysis_shapes({"probabilities": "[55, 30]"})
    assert out["probabilities"] == {}


@pytest.mark.parametrize("bad", [None, 55, 5.5, True, ["a"], ()])
def test_any_non_dict_ends_as_dict(api, bad):
    out = api._coerce_analysis_shapes({"probabilities": bad})
    assert isinstance(out["probabilities"], dict)


def test_valid_dict_is_left_untouched(api):
    good = {"p_positive_12m": 55, "p_hit_bull_12m": 25}
    out = api._coerce_analysis_shapes({"probabilities": dict(good)})
    assert out["probabilities"] == good


def test_missing_key_is_filled(api):
    assert api._coerce_analysis_shapes({})["probabilities"] == {}


def test_non_dict_payload_is_safe(api):
    assert api._coerce_analysis_shapes("no soy un dict") == {}
    assert api._coerce_analysis_shapes(None) == {}


def test_other_fields_survive(api):
    """El normalizador no puede comerse el resto del analisis."""
    out = api._coerce_analysis_shapes({"probabilities": "x", "recommendation": "AVOID",
                                       "fair_value": 347.31})
    assert out["recommendation"] == "AVOID"
    assert out["fair_value"] == 347.31
