"""El TAM es mundial y viene de quien mide el mercado.

Tres intentos hicieron falta, y cada uno dejó un test aquí:

1. Buscar en Google y aceptar lo que salga: devolvía el comunicado de prensa
   sobre el dato en vez del dato.
2. Descargar del Census vía FRED: oficial y tier 1, pero sólo EE.UU. — y
   `market.py::sam()` estrecha el TAM por geografía, o sea que lo espera
   mundial. A AAPL le salía un 1.900% de participación.
3. Sumar los ingresos de todas las cotizadas del sector: mundial, pero apila
   capas. Medido: $921.000M contra los ~$790.000M reales en semiconductores.

Lo que funciona es preguntarle a la asociación de industria, que mide su
propio mercado, publica mundial y gratis, y cubre UNA capa de la cadena. WSTS
da $795.600M de ventas mundiales de chips: tier 2, por encima de Omdia.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from wbj.overlay import tam_mundial as tm

@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    """Los tests no salen a internet. Ver `test_tam_acervo_no_es_flujo.py`."""
    monkeypatch.setattr(tm, "_verificar_en_la_fuente",
                        lambda cita, tam, fuente: (True, cita))



BUENO = {
    "tam": 795_600_000_000,
    "tam_history": [630_428_000_000, 795_600_000_000],
    "tam_source": "WSTS — Global semiconductor sales 2025",
    "cita": "https://www.wsts.org/76/103/Global-Semiconductor-Market-grows-26-in-2025",
    "cita_textual": "Global semiconductor sales reached USD 795.6 billion in 2025.",
    "ambito": "mundial",
    "capa": "facturación mundial de chips",
    "capa_coincide": "AMD factura la venta de sus chips",
    "segmento_patrones": ["Data Center"],
}


def test_a_complete_answer_becomes_overlay():
    fuera, error = tm._validar(dict(BUENO), "Semiconductors")
    assert error == ""
    assert fuera["tam"] == 795_600_000_000
    assert fuera["tam_history"] == [630_428_000_000, 795_600_000_000]
    assert fuera["_ambito"] == "mundial"
    assert fuera["_segmento_patrones"] == ["data center"]


# --- quién firma: la asociación gana ---------------------------------------

def test_an_industry_association_outranks_a_research_house():
    """WSTS mide su propio mercado y publica gratis: es el ORIGEN del dato.
    Omdia lo estima y cobra por el informe. Tier 2 (confianza 85) contra tier 3
    (70) no es una preferencia estética — decide si la dimensión se capea."""
    assert tm._tier_de_la_fuente("WSTS — Global semiconductor sales") == 2
    assert tm._tier_de_la_fuente("Semiconductor Industry Association") == 2
    assert tm._tier_de_la_fuente("Omdia AI Processors Forecast") == 3
    assert tm._tier_de_la_fuente("IDC Worldwide Tracker") == 3


def test_aggregators_are_rejected_even_when_they_sound_official():
    """Mordor Intelligence salió en la primera búsqueda real de bebidas.
    Recopila cifras de terceros sin firmar metodología: tier 5."""
    for agregador in ("Mordor Intelligence", "Grand View Research",
                      "MarketsandMarkets", "Precedence Research", "Statista"):
        assert tm._tier_de_la_fuente(agregador) is None, agregador
        fuera, error = tm._validar(dict(BUENO, tam_source=agregador), "X")
        assert fuera is None and "no aceptada" in error


def test_the_prompt_and_the_validation_share_one_list():
    """Si el prompt pidiera fuentes que la validación rechaza, cada búsqueda
    gastaría cuota para acabar en un rechazo."""
    texto = tm._fuentes_aceptadas().lower()
    for f in ("wsts", "idc", "omdia", "gartner", "iata", "ifpi"):
        assert f in texto, f
        assert tm._tier_de_la_fuente(f) is not None, f


# --- mundial, no regional --------------------------------------------------

def test_a_regional_figure_is_rejected():
    """El error que mató la versión anterior: el Census mide EE.UU. y los
    ingresos de un emisor son mundiales. A AAPL le salía 1.900%."""
    for ambito in ("Estados Unidos", "United States", "Europa", "China", ""):
        fuera, error = tm._validar(dict(BUENO, ambito=ambito), "X")
        assert fuera is None, f"acepto un ambito {ambito!r}"
        assert "mundial" in error


def test_the_worldwide_words_are_accepted_in_either_language():
    for ambito in ("mundial", "Worldwide", "global market", "World"):
        fuera, _ = tm._validar(dict(BUENO, ambito=ambito), "X")
        assert fuera is not None, ambito


# --- una sola capa ---------------------------------------------------------

def test_a_figure_without_a_declared_layer_is_rejected():
    """El error de Gartner/NVDA: la cifra medía gasto del usuario final y NVDA
    factura en componentes. Daba 39,6%, perfectamente creíble. Ningún chequeo
    aritmético lo caza, así que declarar la capa es obligatorio."""
    fuera, error = tm._validar(dict(BUENO, capa="   "), "X")
    assert fuera is None and "capa" in error


# --- la serie --------------------------------------------------------------

def test_years_in_the_place_of_dollars_are_caught():
    """Medido con JPM: el modelo devolvió `[2024, 2025]` como serie de TAM. La
    participación habría salido de dividir los ingresos entre 2024."""
    fuera, _ = tm._validar(dict(BUENO, tam_history=[2024, 2025]), "X")
    assert fuera is not None, "el nivel sigue valiendo; lo malo es la serie"
    assert "tam_history" not in fuera and "_sin_historia" in fuera


def test_the_series_must_close_on_the_current_tam():
    """`_share_automatico` divide el año anterior entre `historia[-2]` y el
    actual entre `tam`. Si el último punto fuera otra cifra, las dos mitades
    hablarían de mercados distintos."""
    fuera, _ = tm._validar(
        dict(BUENO, tam_history=[630_428_000_000, 700_000_000_000]), "X")
    assert "tam_history" not in fuera


def test_a_missing_figure_is_a_gap_with_a_reason():
    for valor in (None, 0, -5, "no encontrado"):
        fuera, error = tm._validar(dict(BUENO, tam=valor), "X")
        assert fuera is None and error


# --- la cita ---------------------------------------------------------------

def test_a_grounding_redirect_needs_the_literal_quote():
    """Gemini cita con enlaces que caducan y no dicen de quién es la página.
    Con la fuente y la frase literal, la cifra se reencuentra igual."""
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZ"
    fuera, error = tm._validar(dict(BUENO, cita=redirect, cita_textual=""), "X")
    assert fuera is None and "caduca" in error

    fuera, _ = tm._validar(dict(BUENO, cita=redirect), "X")
    assert "_cita_es_redirect" in fuera


def test_a_figure_without_a_url_is_rejected():
    fuera, error = tm._validar(dict(BUENO, cita="ver el informe"), "X")
    assert fuera is None and "URL" in error


def test_a_second_source_is_recorded_when_it_confirms():
    """Victor pidió máximo dos fuentes: una cifra con dos orígenes verificables
    vale más que cinco a medio comprobar."""
    fuera, _ = tm._validar(dict(BUENO, segunda_fuente="SIA — $791.7B en 2025"), "X")
    assert "SIA" in fuera["_segunda_fuente"]
    fuera, _ = tm._validar(dict(BUENO, segunda_fuente="null"), "X")
    assert "_segunda_fuente" not in fuera


# --- el archivo de industria -----------------------------------------------

class _S:
    def __init__(self, root):
        self.inputs_dir = str(root)
        self.gemini_api_key = None
        self.openai_api_key = None


def test_an_analyst_file_is_never_touched(tmp_path, monkeypatch):
    """El Omdia de `semiconductors.json` mide aceleradores de datacenter — un
    mercado más ajustado a NVDA que el total de chips de WSTS. Quien leyó el
    estudio sabe algo que la búsqueda no sabe."""
    d = tmp_path / "_industrias"
    d.mkdir()
    original = {"tam": 207_000_000_000, "tam_source": "Omdia", "tam_source_tier": 3}
    (d / "semiconductors.json").write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(tm, "_investigar", lambda *a: pytest.fail(
        "no debio investigar un archivo de analista"))
    assert "analista" in tm.asegurar_tam_industria(_S(tmp_path), "Semiconductors", "NVDA")
    assert json.loads((d / "semiconductors.json").read_text(encoding="utf-8")) == original


def test_a_fresh_answer_is_not_asked_again(tmp_path, monkeypatch):
    d = tmp_path / "_industrias"
    d.mkdir()
    (d / "semiconductors.json").write_text(json.dumps({
        "_generado_por": "vertex/tam_mundial", "_resuelto_en": "2026-07-01",
        "tam": 1}), encoding="utf-8")
    monkeypatch.setattr(tm, "_investigar", lambda *a: pytest.fail("no debio preguntar"))
    assert "vigente" in tm.asegurar_tam_industria(
        _S(tmp_path), "Semiconductors", "NVDA", hoy=date(2026, 8, 5))


def test_a_failed_search_never_erases_a_good_tam(tmp_path, monkeypatch):
    """Lo peor que podría hacer este módulo es borrar un TAM que funcionaba
    porque hoy se acabó la cuota."""
    d = tmp_path / "_industrias"
    d.mkdir()
    (d / "semiconductors.json").write_text(json.dumps({
        "_generado_por": "vertex/tam_mundial", "_resuelto_en": "2026-01-01",
        "tam": 795_600_000_000, "tam_source": "WSTS", "tam_source_tier": 2}),
        encoding="utf-8")
    monkeypatch.setattr(tm, "_investigar", lambda *a: (None, ["gemini: 429"]))
    msg = tm.asegurar_tam_industria(_S(tmp_path), "Semiconductors", "NVDA",
                                    hoy=date(2026, 8, 5))
    assert "429" in msg, "el motivo se nombra, no se traga"
    assert json.loads((d / "semiconductors.json").read_text(
        encoding="utf-8"))["tam"] == 795_600_000_000


def test_all_the_failure_reasons_are_reported(tmp_path, monkeypatch):
    """Un TAM ausente por cuota agotada y uno ausente porque ninguna asociación
    publica ese mercado son problemas distintos."""
    monkeypatch.setattr(tm, "_preguntar_gemini", lambda s, p: ("", "gemini: 429"))
    monkeypatch.setattr(tm, "_preguntar_openai", lambda s, p: ("", "openai: sin clave"))
    datos, fallos = tm._investigar(_S(tmp_path), "Semiconductors", "NVDA")
    assert datos is None and fallos == ["gemini: 429", "openai: sin clave"]


def test_without_an_industry_there_is_nothing_to_resolve(tmp_path):
    for ind in (None, "", "   "):
        assert "no hay TAM" in tm.asegurar_tam_industria(_S(tmp_path), ind, "X")
