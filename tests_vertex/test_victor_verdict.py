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
