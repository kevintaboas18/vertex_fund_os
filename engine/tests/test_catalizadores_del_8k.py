"""El registro de catalizadores tenía fuente declarada y nadie la leía.

`DATASET.md` pide `catalyst_registry` así: "Product, capacity, regulatory,
contract, pricing, and launch events | event array | forward 24 months |
**official issuer/regulatory evidence** | required".

Nada lo poblaba. Los catalizadores sólo entraban si un analista los escribía en
`Entradas/<TICKER>.json`, así que MKT-CAT-019 y MKT-TDEC-020 quedaban sin dato
en 9 de 12 tickers medidos.

Un 8-K es exactamente esa evidencia, y tiene una propiedad que lo separa de los
dos intentos de extracción que este motor ya rechazó con evidencia (la
concentración de clientes y el guidance): **la SEC numera el tipo de evento**.
El item 1.01 es un contrato firmado, el 2.01 una adquisición completada. El
tipo viene en un campo, no hay prosa que interpretar.

Lo que sí hay que leer del documento es de qué trata, porque el item 8.01 es
"otros eventos materiales" — un cajón de sastre que son 16 de los 17 avisos de
Walmart y 31 de los 40 de Realty Income. Sin descripción, la fila le llega al
juez como "otros eventos" y un juez que no puede valorar o se abstiene o se lo
inventa. Con ella, se leen los eventos de verdad: la brecha de datos de
fairlife en Coca-Cola, la licencia de exportación a China que el gobierno de
EE.UU. impuso a NVIDIA, la fusión de redomiciliación de Exxon.

Y lo que NO sale de aquí: probabilidad, impacto y calidad de evidencia.
FORMULAS.md lo dice sin rodeos — "Probabilities and impacts are assumptions;
never disguise as reported facts" — así que el proveedor entrega el evento y su
fecha, y el juez pone los números como asunciones declaradas.
"""

from __future__ import annotations

from datetime import date

import pytest

from wbj.providers.edgar import EdgarProvider


class _Cache:
    def get(self, ticker, key):
        return None

    def put(self, ticker, key, payload):
        pass


class _Edgar(EdgarProvider):
    def __init__(self, sub, textos=None):
        super().__init__(None, _Cache())
        self._sub, self._txt = sub, textos or {}

    def get_json(self, url, params, kind, cache_key, **kw):
        return self._sub

    def get_text(self, url, params, kind, cache_key, **kw):
        return self._txt.get(url, "")


_HOY = date(2026, 8, 6)
_CIK = 21344


def _submissions(*filas):
    """`filas` son `(items, fecha, accession, documento)`."""
    return {"filings": {"recent": {
        "form": ["8-K"] * len(filas),
        "items": [f[0] for f in filas],
        "filingDate": [f[1] for f in filas],
        "accessionNumber": [f[2] for f in filas],
        "primaryDocument": [f[3] for f in filas]}}}


_ACC = "0000021344-26-000050"
_DOC = "ko-20260716.htm"
_URL = f"https://www.sec.gov/Archives/edgar/data/{_CIK}/{_ACC.replace('-', '')}/{_DOC}"


def _con_cuerpo(items, cuerpo):
    return _Edgar(_submissions((items, "2026-07-16", _ACC, _DOC)),
                  {_URL: f"<html><body>{cuerpo}</body></html>"})


# --- el tipo de evento viene en un campo, no en la prosa -------------------

@pytest.mark.parametrize("item,categoria", [
    ("1.01", "contract"),
    ("2.01", "capacity"),
    ("5.03", "regulatory"),
    ("8.01", "product"),
])
def test_the_sec_numbers_the_event_type(item, categoria):
    """Ahí está la diferencia con las extracciones que se rechazaron: el tipo
    es un dato estructurado, no algo que haya que deducir leyendo."""
    cuerpo = f"Item {item}. Algo material ocurrio. " + "detalle " * 40
    fuera = _con_cuerpo(item, cuerpo).catalyst_registry(_CIK, hoy=_HOY)
    assert len(fuera) == 1
    assert fuera[0]["category"] == categoria
    assert item in fuera[0]["event"]


def test_results_and_board_changes_are_not_catalysts():
    """El 2.02 son resultados —la corrida normal del negocio, y ya la lee
    `latest_earnings_release`— y el 5.02 es gobernanza. Ninguno es un
    catalizador de mercado."""
    ed = _Edgar(_submissions(("2.02", "2026-07-16", _ACC, _DOC),
                             ("5.02", "2026-07-10", _ACC, _DOC)))
    assert ed.catalyst_registry(_CIK, hoy=_HOY) == []


def test_a_filing_carrying_several_items_yields_several_events():
    """Un mismo 8-K puede anunciar un contrato Y cerrar una adquisición.
    Verificado en Exxon: su aviso del 1 de julio de 2026 lleva 1.01 y 2.01."""
    cuerpo = ("Item 1.01. Entry Into a Material Definitive Agreement. " + "x " * 80
              + " Item 2.01. Completion of Acquisition. " + "y " * 80)
    fuera = _con_cuerpo("1.01,2.01", cuerpo).catalyst_registry(_CIK, hoy=_HOY)
    assert {c["category"] for c in fuera} == {"contract", "capacity"}


# --- la ventana ------------------------------------------------------------

def test_events_older_than_the_window_are_dropped():
    """`DATASET.md` dice "forward 24 months". Un contrato de hace tres años no
    es un catalizador, es historia."""
    ed = _Edgar(_submissions(("1.01", "2022-01-05", _ACC, _DOC)))
    assert ed.catalyst_registry(_CIK, hoy=_HOY) == []


def test_the_time_decay_input_is_never_negative():
    """MKT-TDEC-020 es `exp(-ln(2) * months_to_event / 12)`. Con meses
    negativos el factor pasa de 1 y el catalizador pesaría MÁS que uno
    inminente. Un evento ya presentado va a cero: el anuncio está aquí y su
    efecto económico está por delante, que es lo que el decaimiento mide."""
    cuerpo = "Item 8.01. Other Events. " + "detalle " * 40
    fuera = _con_cuerpo("8.01", cuerpo).catalyst_registry(_CIK, hoy=_HOY)
    assert fuera[0]["months_to_event"] == 0.0


# --- la descripción --------------------------------------------------------

def test_the_description_starts_at_the_item_heading():
    """Un 8-K en iXBRL empieza con la carátula etiquetada. Sus primeros 900
    caracteres son `ko-20260716 0000021344 False ...` — marcado, no aviso. La
    SEC exige el encabezado "Item X.XX", y ahí empieza lo que el emisor dice.
    """
    caratula = "ko-20260716 0000021344 False ko:CommonStock0.25ParValueMember " * 12
    real = ("Other Events. On July 16, 2026, The Coca-Cola Company announced "
            "that fairlife identified unauthorized access by a third party. ") * 3
    fuera = _con_cuerpo("8.01", caratula + " Item 8.01. " + real
                        ).catalyst_registry(_CIK, hoy=_HOY)
    d = fuera[0]["descripcion"]
    assert "fairlife" in d, "la descripcion se quedo en la caratula"
    assert "CommonStock0.25ParValueMember" not in d


def test_a_filing_that_is_only_a_cover_page_gets_no_description():
    """Sin cuerpo no hay nada que describir, y una fila sin descripción no
    viaja al juez: no puede valorar lo que no sabe qué es."""
    fuera = _con_cuerpo("8.01", "Item 8.01. corto").catalyst_registry(_CIK, hoy=_HOY)
    assert not fuera[0].get("descripcion")


# --- lo que el proveedor NO decide ----------------------------------------

def test_no_probability_or_impact_is_invented():
    """FORMULAS.md: "Probabilities and impacts are assumptions; never disguise
    as reported facts". El proveedor entrega el evento y la fecha; los números
    los pone el juez, declarados como asunciones."""
    cuerpo = "Item 1.01. Entry Into a Material Definitive Agreement. " + "x " * 80
    c = _con_cuerpo("1.01", cuerpo).catalyst_registry(_CIK, hoy=_HOY)[0]
    for prohibido in ("probability", "impact", "evidence_quality"):
        assert prohibido not in c, (
            f"{prohibido} salio del proveedor: eso lo convierte en un hecho "
            "reportado, que es justo lo que FORMULAS.md prohibe")
    assert c["source"] == "SEC EDGAR 8-K"
    assert c["filed"] == "2026-07-16"


def test_an_issuer_with_no_material_events_gets_an_empty_registry():
    """Y eso deja las dos métricas sin dato, que es la respuesta honesta."""
    assert _Edgar({"filings": {"recent": {}}}).catalyst_registry(
        _CIK, hoy=_HOY) == []
