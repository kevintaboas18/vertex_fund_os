"""Hay empresas que compiten ENTERAS en su mercado, y no había forma de decirlo.

El mecanismo para conectar el TAM con el numerador era `_segmento_patrones`:
una lista de palabras que reconoce, en la segmentación del 10-K, el segmento
que compite en ese mercado. Funciona para NVIDIA, donde «Data Center» es un
segmento entre cinco y es el único que vende aceleradores.

No funciona para el caso opuesto, que resultó ser el mayoritario. Los segmentos
de Walmart son `Walmart U.S.`, `Walmart International` y `Sam's Club`; los de
JPMorgan, `Consumer & Community Banking`, `Commercial and Investment Bank` y
`Asset and Wealth Management`. Los tres de cada uno son comercio minorista y
banca respectivamente. Ningún patrón elige uno — y elegir uno habría sido peor
que no elegir, porque dividiría un trozo del numerador entre el mercado entero.

Medido: la participación faltaba en 10 de 12 tickers teniendo TAM en 9 de 12.
El denominador estaba, el numerador estaba, y lo que faltaba era la frase que
los une.

Con `_ingreso_relevante` declarado, WMT / JPM / LLY pasan de 0,419 a 0,613 de
cobertura en Market, y sus participaciones salen 3,9% / 4,4% / 4,1% — cifras
que un humano reconoce como razonables para esas empresas en esos mercados.

Lo que NO cambia, y por eso está la mitad de este archivo: Coca-Cola sigue sin
participación. Su TAM mide valor al público ($418.000M) y ella factura
concentrado (~$47.000M). Son dos capas de la cadena, y el numerador no se
arregla cambiándolo por otro más grande.
"""

from __future__ import annotations

import pytest

from wbj.overlay import from_packet as fp


class _FMP:
    """FMP sin red: sólo lo que `_share_automatico` le pide."""

    def __init__(self, previo: float | None = None):
        self._previo = previo

    def income_annual(self, ticker):
        # El nombre importa: la primera version llamo a `income_statement`,
        # que no existe en el proveedor. El `except Exception` de
        # `_ingreso_total_previo` se lo trago y `share_history` quedo vacio
        # sin que nada fallara. Este doble usa el nombre real a proposito, y
        # `test_a_missing_prior_year_leaves_the_level_alone` cubre el otro
        # lado: cuando de verdad no hay ano anterior.
        if self._previo is None:
            return []
        return [{"revenue": 0.0}, {"revenue": self._previo}]

    def revenue_product_segmentation(self, ticker):
        return [{"data": {"Walmart U S": 4.0, "Walmart International": 3.0,
                          "Sams Club": 2.0}},
                {"data": {"Walmart U S": 3.5, "Walmart International": 2.8,
                          "Sams Club": 1.9}}]


_SEGMENTOS = {"Walmart U S": 4.0, "Walmart International": 3.0,
              "Sams Club": 2.0}


def test_the_whole_company_can_be_the_numerator():
    salida = fp._share_automatico(
        _FMP(), "WMT", _SEGMENTOS, tam=100.0, tam_history=None,
        patrones=["retail"], ingreso_total=9.0, total_es_relevante=True)
    assert salida["share"]["company_sales"] == 9.0, (
        "sumar los tres segmentos es el punto: ninguno compite solo")
    assert salida["share"]["total_market_sales"] == 100.0
    assert salida["company_relevant_revenue"] == 9.0


def test_without_the_declaration_nothing_changes():
    """El comportamiento por defecto es el de antes. Si esto fuera un valor
    por defecto, Coca-Cola habría estrenado una participación calculada contra
    un denominador de otra capa."""
    salida = fp._share_automatico(
        _FMP(), "KO", {"Pacific": 4.0, "Bottling investments": 1.0},
        tam=100.0, tam_history=None, patrones=["soft drinks"],
        ingreso_total=9.0, total_es_relevante=False)
    assert salida == {}, "sin declaracion no puede aparecer una participacion"


def test_a_declared_total_without_reported_revenue_is_still_nothing():
    """La declaración dice qué numerador usar, no lo inventa."""
    assert fp._share_automatico(
        _FMP(), "WMT", _SEGMENTOS, tam=100.0, tam_history=None,
        patrones=None, ingreso_total=None, total_es_relevante=True) == {}


def test_over_one_hundred_percent_is_still_rejected():
    """La red de seguridad que ya existía tiene que seguir cubriendo la ruta
    nueva: por encima del 100% el denominador no es el de esta empresa."""
    assert fp._share_automatico(
        _FMP(), "WMT", _SEGMENTOS, tam=5.0, tam_history=None,
        patrones=None, ingreso_total=9.0, total_es_relevante=True) == {}


def test_the_delta_compares_the_same_numerator():
    """`MKT-SHDELTA-007` mide si la empresa gana o pierde captura. Comparar el
    ingreso total de este año contra un SEGMENTO del anterior restaría peras y
    manzanas, que es justo lo que el resto del archivo evita."""
    salida = fp._share_automatico(
        _FMP(previo=8.0), "WMT", _SEGMENTOS, tam=100.0,
        tam_history=[90.0, 100.0], patrones=None,
        ingreso_total=9.0, total_es_relevante=True)
    assert salida["share_history"] == [round(8.0 / 90.0, 6),
                                       round(9.0 / 100.0, 6)]


def test_a_missing_prior_year_leaves_the_level_alone():
    """Sin el año anterior se entrega el nivel y nada más — no un delta
    calculado contra un numerador distinto."""
    salida = fp._share_automatico(
        _FMP(previo=None), "WMT", _SEGMENTOS, tam=100.0,
        tam_history=[90.0, 100.0], patrones=None,
        ingreso_total=9.0, total_es_relevante=True)
    assert "share_history" not in salida
    assert salida["share"]["company_sales"] == 9.0


# --- la declaración se lee del archivo de industria -------------------------

class _Settings:
    def __init__(self, raiz):
        self.inputs_dir = str(raiz)


def _industria(tmp_path, slug, cuerpo):
    import json
    d = tmp_path / "_industrias"
    d.mkdir(exist_ok=True)
    (d / f"{slug}.json").write_text(json.dumps(cuerpo), encoding="utf-8")
    return _Settings(tmp_path)


_TAM = {"tam": 100.0, "tam_source": "WSTS", "tam_source_tier": 2}


def test_the_flag_travels_from_the_industry_file(tmp_path):
    s = _industria(tmp_path, "discount-stores",
                   {**_TAM, "_ingreso_relevante": "total"})
    fuera = fp._overlay_industria(s, "Discount Stores", "WMT")
    assert fuera.get("_ingreso_relevante") == "total"


@pytest.mark.parametrize("ticker,espera", [("PLTR", "total"), ("MSFT", None)])
def test_a_list_of_tickers_splits_an_industry(tmp_path, ticker, espera):
    """Una industria de GICS puede mezclar los dos casos. El TAM de
    `software-infrastructure` mide software de datos y analítica: eso es
    Palantir entera, y sólo un trozo de Microsoft. Con un campo binario había
    que elegir entre dejar a PLTR sin participación o darle a MSFT un
    numerador que no compite ahí."""
    s = _industria(tmp_path, "software-infrastructure",
                   {**_TAM, "_ingreso_relevante": ["PLTR"]})
    fuera = fp._overlay_industria(s, "Software - Infrastructure", ticker)
    assert fuera.get("_ingreso_relevante") == espera


def test_the_provider_method_actually_exists():
    """El doble de arriba puede mentir: si el proveedor real renombra el
    método, los tests siguen verdes y `share_history` vuelve a vaciarse en
    silencio. Esto ata el doble al proveedor de verdad."""
    from wbj.providers.fmp import FMPProvider
    assert hasattr(FMPProvider, "income_annual"), (
        "`_ingreso_total_previo` llama a income_annual: si ya no existe, el "
        "except se lo traga y el delta de participacion desaparece sin ruido")


def test_the_flag_does_not_travel_without_a_tam(tmp_path):
    """Sin denominador no hay participación que calcular, así que la
    declaración no tiene a dónde ir."""
    s = _industria(tmp_path, "reit-retail", {"_ingreso_relevante": "total"})
    assert fp._overlay_industria(s, "REIT - Retail", "O") == {}
