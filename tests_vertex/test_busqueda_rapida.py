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

Y una cuarta, que se descubrió al quitarle a estos tests la dependencia de la
red (antes se saltaban solos sin FMP, o sea siempre en integración continua):

4. El umbral contaba CANDIDATOS, y eso falla justo en el mejor caso. "NVD" deja
   un solo candidato local —NVDA— y "NVDA" también, así que las dos últimas
   teclas del ticker más buscado seguían bajando a la cola larga: dos peticiones
   HTTP por tecla para no añadir nada, porque NVDA ya salía el primero. Ahora la
   pregunta no es "¿hay pocos?" sino "¿hay una respuesta buena?": si algún
   símbolo local empieza por lo que tecleaste, no se consulta a FMP.

Ahora: 42 ms desde arranque frío para escribir "NVDA" entero, con clave de FMP
puesta y sin una sola petición.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import vertex_api


#: Las empresas de EE.UU. que compiten con NVDA en el autocompletado: todas las
#: de más de $300M de capitalización cuyo SÍMBOLO empieza por N, más una que
#: solo coincide por el nombre ("The Bank of **New** York Mellon"). Son las
#: filas reales del índice —mismo formato `{símbolo: (nombre, bolsa, cap)}` que
#: llena `_fmp_cargar_indice`—, no inventadas.
#:
#: Esto sustituye a esperar 60 s a que FMP conteste. Ese fixture se saltaba los
#: dos tests que de verdad miden algo cada vez que no había red o clave, o sea
#: SIEMPRE en integración continua: por eso el fallo que arreglan estuvo ahí sin
#: que nada lo dijera. Un test que no corre no protege nada.
INDICE_N = {
    "NVDA": ("NVIDIA Corporation", "NASDAQ", 4.40e12),
    "NFLX": ("Netflix, Inc.", "NASDAQ", 5.00e11),
    "NVO": ("Novo Nordisk A/S", "NYSE", 2.50e11),
    "NOW": ("ServiceNow, Inc.", "NYSE", 1.90e11),
    "NEE": ("NextEra Energy, Inc.", "NYSE", 1.50e11),
    "NKE": ("NIKE, Inc.", "NYSE", 1.00e11),
    "NOC": ("Northrop Grumman Corporation", "NYSE", 8.00e10),
    "NTES": ("NetEase, Inc.", "NASDAQ", 7.50e10),
    "NEM": ("Newmont Corporation", "NYSE", 7.00e10),
    "NSC": ("Norfolk Southern Corporation", "NYSE", 6.00e10),
    "NU": ("Nu Holdings Ltd.", "NYSE", 6.00e10),
    "NXPI": ("NXP Semiconductors N.V.", "NASDAQ", 5.50e10),
    "NDAQ": ("Nasdaq, Inc.", "NASDAQ", 4.50e10),
    "NRG": ("NRG Energy, Inc.", "NYSE", 2.40e10),
    "NTAP": ("NetApp, Inc.", "NASDAQ", 2.30e10),
    "NVR": ("NVR, Inc.", "NYSE", 2.20e10),
    "NVT": ("nVent Electric plc", "NYSE", 1.20e10),
    "NIO": ("NIO Inc.", "NYSE", 1.00e10),
    "NVMI": ("Nova Ltd.", "NASDAQ", 7.00e9),
    "NVAX": ("Novavax, Inc.", "NASDAQ", 1.20e9),
    "BK": ("The Bank of New York Mellon Corporation", "NYSE", 5.00e10),
}


@pytest.fixture
def indice_caliente(monkeypatch):
    """El índice cargado, como en un servidor que lleva un rato en pie.

    `ts` en «ahora» es parte del montaje: con el índice fresco `_indice_actual`
    no lanza el hilo de recarga, así que el test tampoco toca la red por detrás.
    """
    monkeypatch.setitem(vertex_api._INDICE, "filas", dict(INDICE_N))
    monkeypatch.setitem(vertex_api._INDICE, "ts", time.time())
    vertex_api._BUSQUEDA_CACHE.clear()
    yield vertex_api._INDICE["filas"]
    vertex_api._BUSQUEDA_CACHE.clear()


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

    Aquí se ponía la clave de FMP a propósito: sin ella `_consultar` no corre y
    el test pasaba sin probar nada, que es como el fallo que arregla estuvo
    escondido. Con clave, las dos últimas teclas —"NVD" y "NVDA", un solo
    candidato local cada una— sí bajaban a la cola larga: dos peticiones HTTP
    por tecla para no añadir nada, porque NVDA ya salía el primero.
    """
    monkeypatch.setenv("FMP_API_KEY", "clave-de-prueba")
    llamadas = []

    def _prohibida(*a, **k):
        llamadas.append(a[0] if a else k)
        raise AssertionError("se llamo a FMP para un ticker que el indice local cubre")

    monkeypatch.setattr(vertex_api.requests, "get", _prohibida)
    c = TestClient(vertex_api.app)
    for q in ("N", "NV", "NVD", "NVDA"):
        d = c.get(f"/api/search?q={q}").json()
        assert d["resultados"], q
    assert llamadas == [], f"{len(llamadas)} peticiones: {llamadas}"


def test_un_termino_que_el_indice_no_cubre_si_baja_a_la_cola_larga(indice_caliente,
                                                                   monkeypatch):
    """La otra cara: cortar las llamadas no puede cortar la cola larga.

    Si ningún símbolo local empieza por lo que tecleaste, FMP es lo único que
    puede tener ese ADR o esa small cap, y hay que preguntarle.
    """
    monkeypatch.setenv("FMP_API_KEY", "clave-de-prueba")
    vertex_api._BUSQUEDA_CACHE.clear()
    pedidas = []

    class _R:
        status_code = 200

        def json(self):
            return []

    monkeypatch.setattr(vertex_api.requests, "get",
                        lambda url, **k: (pedidas.append(url), _R())[1])
    TestClient(vertex_api.app).get("/api/search?q=QRSTU")
    assert len(pedidas) == 2, "la cola larga dejo de consultarse"


def test_el_ticker_buscado_sale_el_primero(indice_caliente):
    """Rápido no sirve de nada si la lista es inútil. NVDA tiene que estar
    arriba desde la primera letra, que es lo que hace innecesario ir a FMP."""
    c = TestClient(vertex_api.app)
    for q in ("N", "NV", "NVD", "NVDA"):
        res = c.get(f"/api/search?q={q}").json()["resultados"]
        primero = res[0].get("ticker") or res[0].get("symbol")
        assert primero == "NVDA", f"con '{q}' el primero fue {primero}"


def test_coincidir_por_simbolo_gana_a_coincidir_por_nombre(indice_caliente):
    """Con "NE": NEM ($70B, el símbolo empieza por NE) va antes que NFLX
    ($500B, que solo coincide porque se llama "**Ne**tflix").

    Siete veces más capitalización y aun así detrás. Es lo que hace que el
    orden sea el de `_rango_coincidencia` y no el del market cap a secas: quien
    teclea "NE" está escribiendo un símbolo, no un nombre.
    """
    res = [x["ticker"] for x in
           TestClient(vertex_api.app).get("/api/search?q=NE&limite=20").json()["resultados"]]
    assert res.index("NEM") < res.index("NFLX")


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
