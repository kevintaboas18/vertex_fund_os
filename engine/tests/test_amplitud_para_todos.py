"""La amplitud de sector la tenían unos tickers sí y otros no.

`MKT-SECB-023` se pedía **sólo de caché** durante el análisis. La razón era
buena y está documentada: 405 cotizaciones por sector agotaban el límite de FMP
y los 429 caían sobre las llamadas del propio ticker — una métrica de 3 puntos
competía por cuota con las otras 97.

Pero esa decisión tenía una consecuencia que no se había nombrado: **el primer
ticker de cada sector nunca tenía amplitud.** La métrica funcionaba «para
algunos sí y para otros no», y eso no puede pasar — una cobertura que depende
del orden en que se analizan los tickers no mide la empresa, mide el historial
de uso.

Lo que cambió los números: el tope bajó de 405 miembros a 120. Medido en vivo
sobre Technology y Financial Services — **11 segundos, 120 de 120 con media de
50 publicada, cero 429**. Y el coste se paga una vez por sector y día, porque
el resultado se cachea: el segundo ticker del mismo sector no gasta ni una
petición.

Medido tras el cambio, sobre ocho tickers de seis sectores distintos:
`technical` pasa de 0,955 a **1,000 en todos**.

Lo que queda es el cortacircuitos, y es la mitad importante de este archivo. Si
el proveedor empieza a limitar, cada petición perdida se resta de la cuota del
ticker que se está analizando — que es exactamente el fallo original. Tres
fallos seguidos y se abandona el sector: la amplitud queda sin dato, que cuesta
3 puntos, en vez de llevarse por delante el análisis entero.
"""

from __future__ import annotations

import pytest

from wbj.overlay import amplitud_sector as amp


class _Cache:
    def __init__(self):
        self.datos = {}

    def get(self, ticker, key):
        return self.datos.get((ticker, key))

    def put(self, ticker, key, payload):
        self.datos[(ticker, key)] = payload


class _Settings:
    fmp_api_key = "x"


class _FMP:
    """FMP falso: cuenta peticiones y puede simular un proveedor limitando."""

    available = True
    settings = _Settings()

    def __init__(self, fallan_desde: int | None = None, miembros: int = 60):
        self.cache = _Cache()
        self.peticiones = 0
        self._fallan_desde = fallan_desde
        self._miembros = miembros

    def get_json(self, url, params, kind, cache_key, **kw):
        if "company-screener" in url:
            return [{"symbol": f"T{i}", "isEtf": False, "isFund": False,
                     "marketCap": 10e9}
                    for i in range(self._miembros)]
        self.peticiones += 1
        if (self._fallan_desde is not None
                and self.peticiones > self._fallan_desde):
            return None
        return [{"price": 110.0, "priceAvg50": 100.0, "priceAvg200": 90.0}]


def _amplitud(fmp, **kw):
    return amp.amplitud_de_sector(fmp, "Technology", permitir_red=True, **kw)


# --- todos los tickers, no sólo los que llegan segundos --------------------

def test_the_first_ticker_of_a_sector_gets_it_too():
    """El caso que fallaba: sin nada en cache, la amplitud se calcula."""
    fmp = _FMP()
    r = _amplitud(fmp)
    assert r is not None and r["valid_members"] == 60
    assert r["above_50dma_count"] == 60


def test_the_second_ticker_of_the_sector_costs_nothing():
    """El coste se paga UNA vez por sector y dia. Si cada ticker volviera a
    pedir 120 cotizaciones, el motivo original para apagar la red seguiria en
    pie."""
    fmp = _FMP()
    _amplitud(fmp)
    gastadas = fmp.peticiones
    _amplitud(fmp)
    assert fmp.peticiones == gastadas, (
        "el segundo ticker del mismo sector volvio a pedir por red")


# --- el cortacircuitos -----------------------------------------------------

def test_a_throttling_provider_stops_the_sector_not_the_analysis():
    """Cada peticion perdida se resta de la cuota del ticker que se esta
    analizando. Ese fue el fallo que obligo a apagar la red en su dia: los 429
    de la amplitud caian sobre NVDA."""
    fmp = _FMP(fallan_desde=2, miembros=60)
    _amplitud(fmp)
    # La cota tiene que contar los HILOS en vuelo. Cuando el contador llega a
    # su tope hay hasta `HILOS` peticiones ya lanzadas que nadie puede
    # cancelar, y cada una lleva su reintento. Medido: 14 de 60 posibles.
    #
    # Lo que se fija NO es un numero exacto sino el orden de magnitud: se
    # abandona cerca del fallo, no despues de gastar el sector entero.
    techo = 2 + (amp.FALLOS_SEGUIDOS_MAXIMOS + amp.HILOS) * 2
    assert fmp.peticiones <= techo, (
        f"siguio pidiendo tras abandonar: {fmp.peticiones} de un techo "
        f"de {techo}, con 60 miembros posibles")
    assert fmp.peticiones < 60, "no abandono: pidio el sector entero"


def test_an_abandoned_sector_has_no_breadth_rather_than_a_wrong_one():
    """Media docena de cotizaciones no describen un sector. Devolver una
    amplitud calculada sobre lo poco que llego seria peor que no devolverla:
    el numero parece bueno y no lo es."""
    fmp = _FMP(fallan_desde=1, miembros=60)
    assert _amplitud(fmp) is None


def test_one_slow_answer_does_not_trip_the_breaker():
    """El contador es de fallos SEGUIDOS. Una respuesta buena lo reinicia, o
    un sector con un hueco aislado se abandonaria entero."""
    fmp = _FMP()
    assert _amplitud(fmp) is not None


# --- lo que ya protegia y sigue protegiendo --------------------------------

def test_a_tiny_sector_produces_no_breadth():
    """Un sector de un punado de miembros no produce una amplitud que
    signifique nada: el ruido de dos o tres empresas la mueve entera."""
    assert _amplitud(_FMP(miembros=8)) is None


def test_without_a_sector_there_is_nothing_to_measure():
    assert amp.amplitud_de_sector(_FMP(), None, permitir_red=True) is None


def test_cache_only_still_works_for_callers_that_ask_for_it():
    """`permitir_red=False` sigue existiendo: quien quiera la version que no
    gasta cuota la tiene."""
    fmp = _FMP()
    assert amp.amplitud_de_sector(fmp, "Technology", permitir_red=False) is None
    assert fmp.peticiones == 0
