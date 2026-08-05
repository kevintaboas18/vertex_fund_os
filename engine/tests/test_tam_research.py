"""El TAM se investiga solo — y se rechaza solo cuando no cumple.

Investigar el tamaño de mercado con un buscador es la parte fácil. La parte
que importa es qué se hace con lo que vuelve, porque un TAM equivocado no se
nota: produce una participación de aspecto razonable y contamina tres
dimensiones de Market sin que salte ninguna alarma.

Estos tests fijan los rechazos, no los aciertos. Cada uno nació de algo que
pasó de verdad al probar el módulo contra industrias reales.
"""

from __future__ import annotations

import json

import pytest

from wbj.overlay import tam_research as tr


BUENO = {
    "tam": 899_900_000_000,
    "tam_history": [785_100_000_000, 899_900_000_000],
    "tam_source": "Gartner - Market Share: Enterprise Software, Worldwide, 2024",
    "cita": "https://www.gartner.com/en/newsroom/enterprise-software-2024",
    "cita_textual": "The worldwide enterprise software market grew by 11.9% to "
                    "reach $899.9 billion in 2024.",
    "capa": "Ingresos de fabricantes",
    "capa_coincide": "Las empresas de esta industria facturan licencias y suscripciones",
    "segmento_patrones": ["Data Platform"],
}


def test_a_complete_answer_becomes_overlay():
    fuera, error = tr._validar(dict(BUENO), "Software - Infrastructure")
    assert error == ""
    assert fuera["tam"] == 899_900_000_000
    assert fuera["tam_history"] == [785_100_000_000, 899_900_000_000]
    assert fuera["tam_source_tier"] == 3
    # Los patrones bajan a minúscula porque así se comparan contra el nombre
    # del segmento del 10-K.
    assert fuera["_segmento_patrones"] == ["data platform"]


# --- quién firma ----------------------------------------------------------

def test_an_unsigned_house_is_rejected_not_downgraded():
    """Mordor Intelligence salió en la primera búsqueda real de bebidas.
    Recopila cifras de terceros sin firmar metodología: es tier 5. Rebajarla a
    tier 4 en vez de rechazarla convertiría un desconocido en un score."""
    datos = dict(BUENO, tam_source="Mordor Intelligence - Non-alcoholic Beverages")
    fuera, error = tr._validar(datos, "Beverages")
    assert fuera is None
    assert "no reconocida" in error and "tier 5" in error


def test_a_consultancy_publication_is_research():
    """El Global Banking Annual Review de McKinsey es el caso que descubrió que
    la lista blanca estaba pensada sólo para casas de datos tecnológicos."""
    datos = dict(BUENO, tam_source="McKinsey Global Banking Annual Review 2026")
    fuera, _ = tr._validar(datos, "Banks - Diversified")
    assert fuera is not None and fuera["tam_source_tier"] == 3


def test_the_tier_comes_from_the_house_not_from_the_model():
    assert tr._tier_de_la_fuente("FDIC Quarterly Banking Profile") == 1
    assert tr._tier_de_la_fuente("WSTS forecast") == 2
    assert tr._tier_de_la_fuente("Omdia AI Processors") == 3
    assert tr._tier_de_la_fuente("un blog cualquiera") is None
    assert tr._tier_de_la_fuente("") is None


def test_the_prompt_and_the_validation_share_one_list():
    """Si el prompt pidiera casas que la validación rechaza, cada búsqueda
    gastaría cuota para acabar en un rechazo."""
    casas = tr._casas_aceptadas().lower()
    for c in ("omdia", "gartner", "mckinsey", "idc"):
        assert c in casas
        assert tr._tier_de_la_fuente(c) is not None


# --- qué se acepta como cifra --------------------------------------------

def test_years_in_the_place_of_dollars_are_caught():
    """Medido con JPM: el modelo devolvió `[2024, 2025]` como serie de TAM.
    Nada aritmético lo delataba, y la participación habría salido de dividir
    los ingresos de JPM entre 2024."""
    datos = dict(BUENO, tam_history=[2024, 2025])
    fuera, error = tr._validar(datos, "Banks")
    assert fuera is not None, "el nivel sigue siendo bueno; lo malo es la serie"
    assert "tam_history" not in fuera
    assert "_sin_historia" in fuera


def test_the_series_must_close_on_the_current_tam():
    """`_share_automatico` divide el año anterior entre `historia[-2]` y el
    actual entre `tam`. Si el último punto de la serie fuera otra cifra, las
    dos mitades hablarían de mercados distintos."""
    datos = dict(BUENO, tam_history=[785_100_000_000, 700_000_000_000])
    fuera, _ = tr._validar(datos, "Software")
    assert "tam_history" not in fuera


def test_a_missing_figure_is_a_gap_with_a_reason():
    for valor in (None, 0, -5, "no encontrado"):
        fuera, error = tr._validar(dict(BUENO, tam=valor), "X")
        assert fuera is None and error


def test_a_figure_without_a_layer_is_rejected():
    """El error de Gartner/NVDA: la cifra medía gasto del usuario final y NVDA
    factura en componentes. Ningún chequeo aritmético lo caza, así que declarar
    la capa es obligatorio."""
    fuera, error = tr._validar(dict(BUENO, capa=""), "X")
    assert fuera is None and "capa" in error


def test_a_figure_without_a_url_is_rejected():
    fuera, error = tr._validar(dict(BUENO, cita="ver el informe"), "X")
    assert fuera is None and "URL" in error


def test_a_grounding_redirect_needs_the_literal_quote():
    """Gemini cita con un redirect que caduca y no dice de quién es la página.
    Con el nombre de la casa y la frase textual, la cifra se reencuentra
    aunque el enlace muera."""
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZ"
    fuera, error = tr._validar(dict(BUENO, cita=redirect, cita_textual=""), "X")
    assert fuera is None and "caduca" in error

    fuera, _ = tr._validar(dict(BUENO, cita=redirect), "X")
    assert fuera is not None
    assert "_cita_es_redirect" in fuera, "el enlace frágil queda marcado"


# --- el archivo de industria ---------------------------------------------

class _S:
    def __init__(self, root):
        self.inputs_dir = str(root)
        self.gemini_api_key = None
        self.openai_api_key = None


def test_a_human_written_file_is_never_touched(tmp_path, monkeypatch):
    """`Entradas/_industrias/semiconductors.json` lo escribió un analista que
    leyó el comunicado de Omdia. Sabe algo que ninguna búsqueda sabe."""
    d = tmp_path / "_industrias"
    d.mkdir()
    original = {"tam": 207_000_000_000, "tam_source": "Omdia", "tam_source_tier": 3}
    (d / "semiconductors.json").write_text(json.dumps(original), encoding="utf-8")

    def _explota(*a, **k):
        raise AssertionError("no debio investigar un archivo de analista")

    monkeypatch.setattr(tr, "_investigar", _explota)
    msg = tr.asegurar_tam_industria(_S(tmp_path), "Semiconductors", "NVDA")
    assert "analista" in msg
    assert json.loads((d / "semiconductors.json").read_text(encoding="utf-8")) == original


def test_a_fresh_answer_is_not_asked_again(tmp_path, monkeypatch):
    """Trimestral, como los filings: las casas revisan sus pronósticos por
    trimestre y preguntar más gasta cuota para recibir lo mismo."""
    from datetime import date

    d = tmp_path / "_industrias"
    d.mkdir()
    (d / "semiconductors.json").write_text(json.dumps({
        "_generado_por": "vertex/tam_research",
        "_resuelto_en": "2026-07-01", "tam": 1}), encoding="utf-8")

    monkeypatch.setattr(tr, "_investigar", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no debio volver a preguntar")))
    msg = tr.asegurar_tam_industria(_S(tmp_path), "Semiconductors", "NVDA",
                                    hoy=date(2026, 8, 5))
    assert "vigente" in msg


def test_a_stale_answer_is_asked_again(tmp_path, monkeypatch):
    from datetime import date

    d = tmp_path / "_industrias"
    d.mkdir()
    (d / "semiconductors.json").write_text(json.dumps({
        "_generado_por": "vertex/tam_research",
        "_resuelto_en": "2026-01-01", "tam": 1}), encoding="utf-8")

    monkeypatch.setattr(tr, "_investigar", lambda *a, **k: (dict(BUENO), []))
    tr.asegurar_tam_industria(_S(tmp_path), "Semiconductors", "NVDA",
                              hoy=date(2026, 8, 5))
    nuevo = json.loads((d / "semiconductors.json").read_text(encoding="utf-8"))
    assert nuevo["tam"] == BUENO["tam"] and nuevo["_resuelto_en"] == "2026-08-05"


def test_a_failed_search_does_not_erase_a_good_answer(tmp_path, monkeypatch):
    """Lo peor que podría hacer este módulo es borrar un TAM que funcionaba
    porque hoy se acabó la cuota."""
    from datetime import date

    d = tmp_path / "_industrias"
    d.mkdir()
    (d / "semiconductors.json").write_text(json.dumps({
        "_generado_por": "vertex/tam_research", "_resuelto_en": "2026-01-01",
        "tam": 207_000_000_000, "tam_source": "Omdia", "tam_source_tier": 3},
    ), encoding="utf-8")

    monkeypatch.setattr(tr, "_investigar", lambda *a, **k: (None, ["gemini: 429"]))
    msg = tr.asegurar_tam_industria(_S(tmp_path), "Semiconductors", "NVDA",
                                    hoy=date(2026, 8, 5))
    assert "429" in msg, "el motivo del fallo se nombra, no se traga"
    assert json.loads((d / "semiconductors.json").read_text(
        encoding="utf-8"))["tam"] == 207_000_000_000


def test_without_an_industry_there_is_nothing_to_research(tmp_path):
    for ind in (None, "", "   "):
        assert "no hay TAM" in tr.asegurar_tam_industria(_S(tmp_path), ind, "X")


def test_the_failure_reasons_are_all_reported(tmp_path, monkeypatch):
    """Un TAM ausente por cuota agotada y uno ausente porque la industria no
    tiene estudios publicados son problemas distintos."""
    monkeypatch.setattr(tr, "_preguntar_gemini", lambda s, p: ("", "gemini: 429"))
    monkeypatch.setattr(tr, "_preguntar_openai", lambda s, p: ("", "openai: sin clave"))
    datos, fallos = tr._investigar(_S(tmp_path), "Semiconductors", "NVDA")
    assert datos is None
    assert fallos == ["gemini: 429", "openai: sin clave"]
