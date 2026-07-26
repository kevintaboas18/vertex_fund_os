"""El veredicto de Victor tiene que llegar a la respuesta de /api/analyze.

Regresion de un fallo real: una limpieza borro `_WBJ_PROFILE_TO_RECO` y dejo
sus 3 usos en pie. `_wbj_reco_from_profile` lanzaba NameError justo despues de
calcular los targets, dentro del try del endpoint -> 500, tirando a la basura
el analisis completo de Victor que ya estaba hecho.

Solo se disparaba cuando el engine SI producia perfil, asi que ninguna prueba
con el engine caido lo habria visto.
"""
import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def api():
    spec = importlib.util.spec_from_file_location("vapi_verdict", _ROOT / "vertex_api.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vapi_verdict"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("profile,expected_reco", [
    ("Momentum Candidate",  "BUY"),
    ("Quality Opportunity", "BUY"),
    ("Value Opportunity",   "BUY"),
    ("Conditional / Watch", "HOLD"),
    ("Speculative",         "SPECULATIVE"),
    ("Avoid / Wait",        "AVOID"),
    ("Weak / Wait",         "AVOID"),
])
def test_every_victor_profile_maps_to_a_recommendation(api, profile, expected_reco):
    reco, clasif = api._wbj_reco_from_profile(profile)
    assert reco == expected_reco
    assert clasif, "toda recomendacion lleva clasificacion en espanol"


def test_unknown_profile_defaults_to_avoid(api):
    """Un perfil nuevo de Victor no puede convertirse en una compra por accidente."""
    reco, _ = api._wbj_reco_from_profile("Perfil Que No Existe Todavia")
    assert reco == "AVOID"


def test_none_profile_does_not_raise(api):
    assert api._wbj_reco_from_profile(None)[0] == "AVOID"


def test_mapping_covers_every_caller(api):
    """El mapa tiene que existir: su ausencia era el NameError."""
    assert isinstance(api._WBJ_PROFILE_TO_RECO, dict)
    assert api._WBJ_PROFILE_TO_RECO, "mapa vacio = todo cae a AVOID en silencio"


# ---------------------------------------------------------------------------
# Frase obligatoria #2 del FINAL_REPORT_SCHEMA ("Why value creation is or is not
# durable") = el foso. El Cerebro no tiene campo `moat` en el reporte final: la
# etiqueta vive en el envelope del sub-agente y llega al reporte como esta frase.
# Se llenaba con prosa del LLM (`posicion_competitiva`), dejando la conclusion de
# durabilidad sin un solo numero aunque el motor ya tenia ROIC, WACC, spread,
# persistencia y rango de margen.
# ---------------------------------------------------------------------------

_MOAT_NVDA = {
    "classification": "NotScorable", "quantitative_evidence": [],
    "roic": 1.1218, "spread": 1.0025, "margin_range": 0.46758,
    "persistence": {"years_at_or_above_threshold": 3, "years_measured": 4},
}


def test_durability_sentence_cites_the_numbers(api):
    s = api._wbj_durability_sentence(_MOAT_NVDA)
    assert "112.2%" in s and "11.9%" in s          # ROIC vs WACC
    assert "+100.2 pp" in s                        # spread
    assert "3 de 4" in s                           # persistencia medida
    assert "46.8 pp" in s                          # rango de margen


def test_durability_sentence_quotes_the_gate_thresholds(api):
    """Un numero sin su umbral no dice si pasa o falla."""
    s = api._wbj_durability_sentence(_MOAT_NVDA)
    assert "≥4 de 5" in s
    assert "≤5 pp" in s


def test_durability_sentence_says_when_the_label_is_missing(api):
    s = api._wbj_durability_sentence(_MOAT_NVDA)
    assert "sin clasificar" in s and "juez" in s
    assert "Foso: " not in s


def test_durability_sentence_uses_the_judged_label_and_evidence(api):
    m = dict(_MOAT_NVDA, classification="Wide",
             quantitative_evidence=["cost advantage", "network scale"])
    s = api._wbj_durability_sentence(m)
    assert s.startswith("Foso: Wide.")
    assert "cost advantage, network scale" in s


@pytest.mark.parametrize("moat", [None, {}, "Wide", {"classification": "Wide"}])
def test_durability_sentence_is_empty_without_evidence(api, moat):
    """Sin numeros no se publica una frase: el llamador cae a la narrativa."""
    assert api._wbj_durability_sentence(moat) == ""
