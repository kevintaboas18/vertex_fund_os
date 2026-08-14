"""Un TAM es un flujo anual. Una capitalización bursátil no lo es.

Al añadir Nareit a la lista de asociaciones, REIT-Retail resolvió por fin — y
resolvió mal. La respuesta fue "$252.000M, retail sector market capitalization
within the FTSE Nareit All Equity REITs index", y pasó los cuatro filtros que
había: traía cifra, traía fuente de tier 2, declaró ámbito mundial y declaró
capa.

Y era la magnitud equivocada. Realty Income factura $5.500M al año; contra ese
denominador su participación de mercado sale 2,2% — un número de aspecto
perfectamente razonable que no significa nada, porque los ingresos de un año no
se dividen entre un valor acumulado en un instante.

Ese es el mismo error que ya había costado semanas con NVDA, cuando el TAM
medía gasto de usuario final en vez de chips. Por eso se declara la capa: no
para que exista el campo, sino para poder rechazarla cuando no es la que
divide. Este archivo fija que ahora se rechaza.
"""

from __future__ import annotations

import pytest

from wbj.overlay import tam_mundial as tm
from wbj.overlay.tam_mundial import _validar

@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    """Los tests no salen a internet.

    `_validar` comprueba la cifra contra la pagina de su fuente -- ver
    `_verificar_en_la_fuente` y la regla del `judge.py` de Victor, "Nunca
    inventes cifras". Eso es una descarga, y una suite que la hace deja de ser
    determinista y tarda minuto y medio. Aqui se sustituye por un verificador
    que acepta, para que cada test siga midiendo lo suyo; la verificacion
    tiene sus propios tests en `test_tam_verificado_en_la_fuente.py`.
    """
    monkeypatch.setattr(tm, "_verificar_en_la_fuente",
                        lambda cita, tam, fuente: (True, cita))


_BASE = {"tam": 252_000_000_000, "tam_anio": 2026, "ambito": "mundial",
         "cita": "https://www.reit.com/data-research"}


def _respuesta(**cambios):
    return {**_BASE, "tam_source": "Nareit",
            "capa": "ingresos anuales del mercado", **cambios}


@pytest.mark.parametrize("acervo", [
    "market capitalization",      # lo que contestó Nareit
    "enterprise value",
    "assets under management",
    "net asset value",
    "total assets",
    "installed base value",
])
def test_a_stock_of_value_is_not_a_market_size(acervo):
    salida, motivo = _validar(_respuesta(capa=f"{acervo} del sector"),
                              "REIT - Retail")
    assert salida is None, f"{acervo!r} se acepto como TAM"
    assert "acervo" in motivo and "flujo" in motivo


def test_the_wrong_layer_is_caught_in_the_source_name_too():
    """Nareit lo puso en el nombre de la fuente, no en la capa: "Nareit +
    Retail sector market capitalization within the FTSE Nareit ... index". Si
    sólo se mirara `capa`, la misma respuesta habría vuelto a pasar."""
    salida, motivo = _validar(
        _respuesta(tam_source=("Nareit + Retail sector market capitalization "
                               "within the FTSE Nareit All Equity REITs index"),
                   capa="REITs minoristas"),
        "REIT - Retail")
    assert salida is None and "acervo" in motivo


def test_an_annual_flow_still_passes():
    """El filtro rechaza una magnitud, no una industria: el TAM de Omdia para
    chips de datacenter —ingresos anuales— sigue entrando igual que antes."""
    salida, motivo = _validar(
        {"tam": 207_000_000_000, "tam_anio": 2026, "tam_source": "Omdia", "ambito": "worldwide",
         "cita": "https://omdia.tech.informa.com/pr/2026/datacenter",
         "capa": "ingresos anuales de chips aceleradores de datacenter"},
        "Semiconductors")
    assert salida is not None, f"un flujo anual legitimo fue rechazado: {motivo}"
    assert salida["tam"] == 207_000_000_000
    assert salida["tam_source_tier"] == 3


def test_gdp_is_not_a_market_either():
    """El PIB es el flujo de una economía entera, no de un mercado. Se rechaza
    por la misma razón por la que se rechaza el gasto de usuario final: mide
    una capa que no es donde compite la empresa."""
    salida, motivo = _validar(_respuesta(capa="GDP mundial"), "Oil & Gas")
    assert salida is None and "acervo" in motivo


def test_no_figure_at_all_keeps_its_own_reason():
    """El motivo tiene que seguir distinguiendo "no encontre nada" de
    "encontre la magnitud equivocada": son dos problemas distintos y se
    arreglan distinto."""
    salida, motivo = _validar({"tam": None}, "Consumer Electronics")
    assert salida is None
    assert "ninguna asociacion" in motivo and "acervo" not in motivo


# --- el juez de la capa ----------------------------------------------------

def test_the_judge_rejects_a_different_layer(monkeypatch):
    """El caso real de Coca-Cola, verificado contra el modelo: "La cifra mide
    el valor de venta al publico de bebidas no alcoholicas, mientras que
    Coca-Cola factura concentrado".

    Es justo el que la lista de acervos NO atrapa -- "valor al publico" es un
    flujo anual, no una capitalizacion. Lo que falla es la CAPA, y eso es una
    pregunta cualitativa.
    """
    monkeypatch.setattr(tm, "_preguntar_gemini", lambda s, p: (
        '{"veredicto": "CAPA_DISTINTA", "porque": "mide valor al publico y KO '
        'factura concentrado"}', None))
    ok, porque = tm._juzgar_capa(None, {"tam": 418e9, "tam_source": "NielsenIQ",
                                        "capa": "valor al publico"}, "KO", 47e9)
    assert ok is False and "concentrado" in porque


def test_the_judge_accepts_a_matching_layer(monkeypatch):
    monkeypatch.setattr(tm, "_preguntar_gemini", lambda s, p: (
        '{"veredicto": "COINCIDE", "porque": "WSTS mide facturacion de chips y '
        'NVDA vende chips"}', None))
    ok, _ = tm._juzgar_capa(None, {"tam": 1655e9, "tam_source": "WSTS",
                                   "capa": "facturacion de semiconductores"},
                            "NVDA", 216e9)
    assert ok is True


@pytest.mark.parametrize("respuesta", [
    ('{"veredicto": "NO_SE_PUEDE_SABER", "porque": "la descripcion no basta"}', None),
    ("", "gemini: ServerError 503 UNAVAILABLE"),
    ("no soy json", None),
])
def test_the_judge_never_blocks_on_doubt(respuesta):
    """Ante la duda NO se rechaza, y eso es deliberado. El juez solo puede
    QUITAR TAM malos; bloquear uno bueno por timidez -- o por un 503 pasajero
    -- dejaria la industria sin denominador tres meses.

    Verificado en vivo: de cuatro consultas, tres devolvieron 503 o 429. Si
    esos errores rechazaran, un mal minuto de la API borraria TAM correctos.
    """
    import pytest as _p
    tm._preguntar_gemini_original = tm._preguntar_gemini
    tm._preguntar_gemini = lambda s, p: respuesta
    try:
        ok, _ = tm._juzgar_capa(None, {"tam": 1e9, "tam_source": "X",
                                       "capa": "y"}, "NVDA", 1e9)
        assert ok is True
    finally:
        tm._preguntar_gemini = tm._preguntar_gemini_original


def test_without_a_ticker_there_is_no_layer_to_contrast():
    """El barrido resuelve industrias sin ticker de referencia. Sin empresa
    contra la que contrastar, la pregunta no tiene sentido."""
    ok, porque = tm._juzgar_capa(None, {"tam": 1e9}, "", None)
    assert ok is True and "sin ticker" in porque
