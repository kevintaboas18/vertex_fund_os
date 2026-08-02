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


# M-09: el campo dejo de emitir ordenes (BUY/AVOID) y ahora emite CLASES DE
# RESEARCH, porque CLAUDE.md prohibe convertir el analisis en una instruccion
# de compra/venta. Los valores esperados se toman de las constantes, no
# literales: asi el test verifica el mapeo y no una cadena concreta.
@pytest.mark.parametrize("profile,expected_key", [
    ("Momentum Candidate",  "RESEARCH_FAVORABLE"),
    ("Quality Opportunity", "RESEARCH_FAVORABLE"),
    ("Value Opportunity",   "RESEARCH_FAVORABLE"),
    ("Conditional / Watch", "RESEARCH_CONDICIONAL"),
    ("Speculative",         "RESEARCH_ESPECULATIVO"),
    ("Avoid / Wait",        "RESEARCH_DESFAVORABLE"),
    ("Weak / Wait",         "RESEARCH_DESFAVORABLE"),
])
def test_every_victor_profile_maps_to_a_recommendation(api, profile, expected_key):
    reco, clasif = api._wbj_reco_from_profile(profile)
    assert reco == getattr(api, expected_key)
    assert clasif, "toda recomendacion lleva clasificacion en espanol"


def test_no_emite_ordenes_de_compra_venta(api):
    """M-09: CLAUDE.md ('Limites del sistema') prohibe que el output sea una
    orden. Ningun perfil puede devolver BUY/SELL/HOLD."""
    prohibidos = {"BUY", "SELL", "HOLD", "STRONG BUY", "COMPRAR", "VENDER"}
    for profile in list(api._WBJ_PROFILE_TO_RECO) + ["Avoid / Wait", None]:
        reco, _ = api._wbj_reco_from_profile(profile)
        assert reco not in prohibidos, f"{profile} devolvio la orden {reco!r}"


def test_historico_de_la_db_sigue_leyendose(api):
    """El track record compara reportes ya guardados con el esquema anterior;
    renombrar sin puente habria invalidado todo el historial."""
    assert api._reco_norm("BUY") == api.RESEARCH_FAVORABLE
    assert api._reco_norm("AVOID") == api.RESEARCH_DESFAVORABLE
    assert api._dir_hit("BUY", 8.0) is True        # fila vieja
    assert api._dir_hit(api.RESEARCH_FAVORABLE, 8.0) is True   # fila nueva


def test_unknown_profile_defaults_to_avoid(api):
    """Un perfil nuevo de Victor no puede convertirse en una compra por accidente."""
    reco, _ = api._wbj_reco_from_profile("Perfil Que No Existe Todavia")
    assert reco == api.RESEARCH_DESFAVORABLE


def test_none_profile_does_not_raise(api):
    assert api._wbj_reco_from_profile(None)[0] == api.RESEARCH_DESFAVORABLE


def test_mapping_covers_every_caller(api):
    """El mapa tiene que existir: su ausencia era el NameError."""
    assert isinstance(api._WBJ_PROFILE_TO_RECO, dict)
    assert api._WBJ_PROFILE_TO_RECO, "mapa vacio = todo cae a AVOID en silencio"
