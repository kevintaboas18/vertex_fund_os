"""Explore descubre empresas; ya no cuenta menciones en redes.

Lo que vivia en `/api/explore` era el "Market Buzz Explorer": ordenaba acciones
por cuantas veces se las menciona en X, Reddit y foros, y le pedia a un LLM que
resumiera el animo. Eso mide popularidad, no evidencia -- y la regla del
proyecto es que sin numero no hay score.

Ahora sirve el screener del engine (`wbj.screener`, identico al de Victor):
prefiltro sobre TODAS las frames XBRL de la SEC y luego el scorecard de los 6
agentes. Estos tests fijan las dos cosas que no pueden volver atras: que el
endpoint entrega la lista del engine con su prefiltro declarado, y que un fallo
del screener no se traga en silencio.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "engine"))

import vertex_api  # noqa: E402

cliente = TestClient(vertex_api.app)


_FILA = {
    "ticker": "IDCC", "name": "Interdigital Inc", "revenue": 8.7e8,
    "growth": 0.31, "margin": 0.42, "score10": 7.4, "evidence": 61,
    "price": 210.0, "target_base": 260.0, "upside_base": 0.238,
}


@pytest.fixture
def _screener(monkeypatch):
    """Sustituye la corrida real: construye 40 packets y tarda 1-2 min."""
    def _instalar(salida):
        import wbj.screener as sc
        if isinstance(salida, Exception):
            monkeypatch.setattr(sc, "screen",
                                lambda **kw: (_ for _ in ()).throw(salida))
        else:
            monkeypatch.setattr(sc, "screen", lambda **kw: salida)
    return _instalar


def test_explore_entrega_las_empresas_descubiertas(_screener):
    _screener([_FILA])
    r = cliente.get("/api/explore")
    assert r.status_code == 200, r.text
    fila = r.json()["companies"][0]
    assert fila["ticker"] == "IDCC"
    assert fila["score10"] == 7.4
    assert fila["target_base"] == 260.0


def test_el_prefiltro_viaja_con_la_respuesta(_screener):
    """La tabla declara sus criterios en pantalla. Si el endpoint no los manda,
    el usuario ve una lista de empresas sin saber que las une -- y un umbral
    que cambia en `screener.py` dejaria mintiendo al texto de la web."""
    _screener([_FILA])
    pf = cliente.get("/api/explore").json()["prefilter"]
    from wbj.screener import GROWTH_MIN, MARGIN_MIN, REV_MAX, REV_MIN
    assert (pf["revenue_min"], pf["revenue_max"]) == (REV_MIN, REV_MAX)
    assert (pf["margin_min"], pf["growth_min"]) == (MARGIN_MIN, GROWTH_MIN)


def test_un_screener_caido_no_se_finge_lista_vacia(_screener):
    """Cero empresas y "no pude preguntar" se ven igual en pantalla y son cosas
    distintas. La lista vacia es un resultado; el fallo es un error."""
    _screener(RuntimeError("EDGAR 503"))
    assert cliente.get("/api/explore").status_code == 500


def test_el_buzz_de_redes_ya_no_existe():
    """Los tres endpoints del explorador de menciones se retiraron enteros --
    no quedaron colgando sirviendo datos que la interfaz ya no pinta."""
    for ruta in ("/api/explore-screens", "/api/explore-deep?ticker=NVDA"):
        assert cliente.get(ruta).status_code == 404, ruta


# ============================================================================
# Dos numeros distintos para la misma accion, y ninguno decia por que
# ============================================================================


def test_explore_declara_que_su_puntaje_no_es_el_del_analisis(_screener):
    """APH salia 7,9 en Descubrir y 5,7 en el analisis, y las dos columnas se
    llamaban "Puntaje".

    Los dos numeros son correctos para lo que miden -- el rapido puntua cada
    categoria con unas pocas metricas de EDGAR para barrer miles de
    declarantes en segundos; el completo corre las 208 y aplica el piso de
    cobertura del 70%. Casi todo el hueco de APH es UNA categoria: market,
    17,2 puntos contra 1,82, porque el completo le pide TAM/SAM/SOM y se
    queda en 0,355 de cobertura.

    Lo que estaba mal era dejar que parecieran comparables.
    """
    cuerpo = cliente.get("/api/explore?limit=1").json()
    assert "metodologia" in cuerpo, (
        "la respuesta tiene que declarar que su puntaje es el rapido -- y el "
        "response_model tiene que nombrarlo, o FastAPI lo descarta")
    m = cuerpo["metodologia"]
    assert m["tipo"] == "rapido"
    assert "208" in m["vs_analisis_completo"]
    assert "70" in m["vs_analisis_completo"]


def test_la_tabla_no_llama_Puntaje_a_secas_al_rapido():
    """La columna decia exactamente la misma palabra que el analisis completo,
    que es de donde venia la confusion."""
    from pathlib import Path

    html = Path(__file__).parent.parent / "vertex_fund_os_platform.html"
    texto = html.read_text(encoding="utf-8")
    assert "Puntaje rápido</th>" in texto
    assert "no es el del análisis completo" in texto
