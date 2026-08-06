"""Escribir un ticker no puede costar una petición HTTP por tecla.

Medido antes de este arreglo, tecleando "NVDA" letra a letra:

    'N'    1007 ms      'NVD'   800 ms
    'NV'    729 ms      'NVDA'  750 ms      TOTAL 3287 ms

Y una "Z" contestaba en 11 ms. La diferencia lo delataba: cuando el índice
local llenaba el cupo de 8 no se llamaba a FMP, y cuando no lo llenaba se
hacían DOS peticiones seguidas — `search-symbol` y luego `search-name`.

Tres causas, las tres arregladas y fijadas aquí:

1. El índice local se cargaba con la PRIMERA búsqueda y tarda 1,8 s, así que
   las primeras teclas del usuario siempre caían a FMP. En Render, donde el
   servicio se duerme, ése es el estreno de cada visita.
2. Se preguntaba a FMP en cuanto había menos de 8 resultados locales. El
   autocompletado no necesita ocho: necesita que el que buscas esté arriba.
3. Las dos rutas de FMP iban en serie sumando latencias sin depender una de
   la otra.

Ahora: 42 ms desde arranque frío para escribir "NVDA" entero.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import vertex_api


@pytest.fixture
def indice_caliente():
    """El índice cargado, como en un servidor que lleva un rato en pie."""
    vertex_api._indice_actual()
    t0 = time.perf_counter()
    while not vertex_api._INDICE["filas"] and time.perf_counter() - t0 < 60:
        time.sleep(0.2)
    if not vertex_api._INDICE["filas"]:
        pytest.skip("el indice de tickers no cargo (sin red o sin FMP_API_KEY)")
    vertex_api._BUSQUEDA_CACHE.clear()
    return vertex_api._INDICE["filas"]


def test_el_indice_se_precalienta_al_arrancar():
    """Se dispara en el `lifespan`, no en la primera búsqueda.

    Es la diferencia entre que la primera tecla de la visita cueste 1000 ms o
    10 ms, y en Render la primera visita es la norma, no la excepción.
    """
    import inspect

    fuente = inspect.getsource(vertex_api._vertex_lifespan)
    assert "_indice_actual()" in fuente, (
        "el lifespan ya no precalienta el indice: la primera busqueda de cada "
        "arranque frio volvera a caer a FMP")


def test_teclear_un_ticker_conocido_no_llama_a_la_red(indice_caliente, monkeypatch):
    """El caso común —una empresa grande de EE.UU.— se resuelve entero en local.

    Es la prueba de verdad: si alguien vuelve a subir el umbral, este test
    falla porque `requests.get` se habrá llamado.
    """
    llamadas = []
    monkeypatch.setattr(vertex_api.requests, "get",
                        lambda *a, **k: llamadas.append(a) or pytest.fail(
                            "se llamo a FMP para un ticker que el indice local cubre"))
    c = TestClient(vertex_api.app)
    for q in ("N", "NV", "NVD", "NVDA"):
        d = c.get(f"/api/search?q={q}").json()
        assert d["resultados"], q
    assert llamadas == []


def test_el_ticker_buscado_sale_el_primero(indice_caliente):
    """Rápido no sirve de nada si la lista es inútil. NVDA tiene que estar
    arriba desde la primera letra, que es lo que hace innecesario ir a FMP."""
    c = TestClient(vertex_api.app)
    for q in ("N", "NV", "NVD", "NVDA"):
        res = c.get(f"/api/search?q={q}").json()["resultados"]
        primero = res[0].get("ticker") or res[0].get("symbol")
        assert primero == "NVDA", f"con '{q}' el primero fue {primero}"


def test_la_cola_larga_pide_las_dos_rutas_a_la_vez(monkeypatch):
    """ASML y TSM no están en el índice local y sí hacen falta.

    Cuando toca preguntar, las dos rutas van en paralelo: iban en serie y
    sumaban sus latencias sin que ninguna dependiera de la otra.
    """
    vertex_api._BUSQUEDA_CACHE.clear()
    monkeypatch.setattr(vertex_api, "_indice_actual", lambda *a, **k: {})
    monkeypatch.setenv("FMP_API_KEY", "x")
    vivas = []
    tope = {"n": 0}

    class _R:
        status_code = 200

        def json(self):
            return []

    def _lenta(url, **k):
        vivas.append(url)
        tope["n"] = max(tope["n"], len(vivas))
        time.sleep(0.25)
        vivas.pop()
        return _R()

    monkeypatch.setattr(vertex_api.requests, "get", _lenta)
    t0 = time.perf_counter()
    TestClient(vertex_api.app).get("/api/search?q=ZZQQ")
    transcurrido = time.perf_counter() - t0

    assert tope["n"] == 2, "las dos rutas no se solaparon: siguen en serie"
    assert transcurrido < 0.45, (
        f"tardo {transcurrido:.2f}s; en serie serian ~0,50s y en paralelo ~0,25s")


def test_el_timeout_no_supera_la_paciencia_de_un_teclado():
    """12 s era el valor anterior. A los 12 s el usuario ya escribió tres letras
    más y esta respuesta se descarta por vieja: hacerle esperar no compra nada."""
    assert vertex_api._TIMEOUT_BUSQUEDA_S <= 3.0
