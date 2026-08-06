"""El TAM se descarga de fuentes oficiales, no se busca en Google.

La primera versión de esto le preguntaba a Gemini con búsqueda web. Funcionaba
y estaba mal por dos motivos: Google no es una de las fuentes de este sistema,
y lo que devolvía no era el dato sino el *comunicado de prensa* sobre el dato,
porque IDC, Omdia y Gartner venden sus informes.

La cadena de ahora es oficial de punta a punta:

    ticker → CIK → EDGAR (SIC de la SEC) → NAICS → FRED (Census/BLS)

Estos tests fijan lo que puede salir mal en esa cadena. Casi todos nacieron de
algo medido contra tickers reales.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from wbj.overlay import tam_oficial as to


SERIE_Q = {"id": "REV5112TAXABL144QSA", "title": "Total Revenue for 5112: Software Publishers",
           "units_short": "Mil. of $", "frequency_short": "Q",
           "observation_end": "2026-01-01"}


class _S:
    def __init__(self, root, fred="k"):
        self.inputs_dir = str(root)
        self.fred_api_key = fred


class _Edgar:
    def __init__(self, sic=("7372", "Services-Prepackaged Software"), cik=1321655):
        self._sic, self._cik = sic, cik

    def cik_for(self, ticker):
        return self._cik

    def sic_for(self, cik):
        return self._sic


# --- la escala, que es donde se falla por mil -----------------------------

def test_the_units_come_from_fred_not_from_a_guess():
    """La serie de software publishers marca 169.800 y son $169.800 millones.
    Ignorar el metadato de escala es la forma más silenciosa de equivocarse
    por un factor de mil."""
    assert to._dolares("Mil. of $") == 1e6
    assert to._dolares("Bil. of U.S. $") == 1e9
    assert to._dolares("Thous. of $") == 1e3
    # Un porcentaje o un índice no es un tamaño de mercado.
    assert to._dolares("% Chg. from Yr. Ago") is None
    assert to._dolares("Index 2017=100") is None
    assert to._dolares("") is None


# --- elegir la serie ------------------------------------------------------

def test_the_naics_code_must_appear_in_the_series(monkeypatch):
    """Para un buscador de texto, "Total Revenue for Software Publishers" y
    "Total Revenue for Other Information Services" son igual de plausibles, y
    una de las dos es el mercado de otra empresa. Exigir el código NAICS en el
    identificador o el título es lo que quita el azar de en medio."""
    otra = dict(SERIE_Q, id="REV5191TAXABL144QSA",
                title="Total Revenue for 5191: Other Information Services")
    monkeypatch.setattr(to, "_pedir", lambda u, p: {"seriess": [otra]})
    assert to._serie_de_la_industria("k", "5112", "Software Publishers") is None

    monkeypatch.setattr(to, "_pedir", lambda u, p: {"seriess": [otra, SERIE_Q]})
    hallada = to._serie_de_la_industria("k", "5112", "Software Publishers")
    assert hallada["serie"]["id"] == "REV5112TAXABL144QSA"


def test_percentage_series_are_never_a_market_size(monkeypatch):
    """FRED publica la misma industria en dólares y en variación porcentual, y
    la de porcentaje suele salir antes por popularidad."""
    pct = dict(SERIE_Q, id="PQREV5112TUSY", units_short="% Chg.")
    monkeypatch.setattr(to, "_pedir", lambda u, p: {"seriess": [pct]})
    assert to._serie_de_la_industria("k", "5112", "Software Publishers") is None


# --- convertir la serie en un tamaño anual --------------------------------

def test_quarterly_series_are_summed_into_a_year(monkeypatch):
    """Un trimestre suelto contra los ingresos anuales de un emisor daría una
    participación cuatro veces mayor de la real, y el número resultante
    seguiría pareciendo razonable."""
    obs = [{"date": f"q{i}", "value": str(100_000 + i)} for i in range(8)]
    monkeypatch.setattr(to, "_pedir", lambda u, p: {"observations": obs})
    actual, previo, fecha = to._tamano("k", SERIE_Q, 1e6)
    assert actual == pytest.approx(sum(100_000 + i for i in range(4)) * 1e6)
    assert previo == pytest.approx(sum(100_000 + i for i in range(4, 8)) * 1e6)


def test_an_incomplete_year_is_no_answer(monkeypatch):
    """Tres trimestres no son un año. Sumarlos daría un mercado un 25% más
    pequeño y una participación un 33% más grande."""
    monkeypatch.setattr(to, "_pedir", lambda u, p: {"observations": [
        {"date": "q1", "value": "1"}, {"date": "q2", "value": "2"},
        {"date": "q3", "value": "3"}]})
    assert to._tamano("k", SERIE_Q, 1e6) is None


def test_missing_observations_are_skipped_not_read_as_zero():
    """FRED marca los huecos con un punto."""
    assert to._numero(".") is None
    assert to._numero("") is None
    assert to._numero(0) is None
    assert to._numero("1,234") == 1234.0


# --- la cadena entera -----------------------------------------------------

def test_the_whole_chain_is_written_down(tmp_path, monkeypatch):
    """Cada eslabón queda escrito en el archivo. Un TAM que no se puede
    rastrear hasta su fuente oficial no vale más que uno inventado."""
    (tmp_path / "_industrias").mkdir()
    monkeypatch.setattr(to, "_serie_de_la_industria",
                        lambda k, n, nom: {"serie": SERIE_Q, "escala": 1e6})
    monkeypatch.setattr(to, "_tamano", lambda k, s, e: (649e9, 560e9, "2026-01-01"))

    msg = to.asegurar_tam_industria(_S(tmp_path), "Software - Infrastructure",
                                    "PLTR", providers={"edgar": _Edgar()})
    d = json.loads((tmp_path / "_industrias" / "software-infrastructure.json"
                    ).read_text(encoding="utf-8"))
    assert d["tam"] == 649_000_000_000
    assert d["tam_source_tier"] == 1, "el Census es tier 1, la fuente mas alta"
    assert d["tam_history"] == [560_000_000_000, 649_000_000_000]
    assert "7372" in d["_cadena"] and "5112" in d["_cadena"] and "PLTR" in d["_cadena"]
    assert d["_serie_fred"] == "REV5112TAXABL144QSA"
    assert d["_ambito"] == "US", "el ambito decide que numerador se le pone encima"
    assert "FRED" in msg


def test_an_unmapped_sic_is_a_gap_with_its_code_written_down(tmp_path, monkeypatch):
    """El hueco tiene que llevar el SIC encima: es lo único que hace falta
    para añadirlo a la tabla."""
    (tmp_path / "_industrias").mkdir()
    monkeypatch.setattr(to, "_serie_de_la_industria", lambda *a: pytest.fail(
        "no debio llegar a FRED sin equivalencia NAICS"))
    edgar = _Edgar(sic=("9995", "Non-Operating Establishments"))
    to.asegurar_tam_industria(_S(tmp_path), "Shell Companies", "XYZ",
                              providers={"edgar": edgar})
    d = json.loads((tmp_path / "_industrias" / "shell-companies.json"
                    ).read_text(encoding="utf-8"))
    assert "9995" in d["_sic_visto"]
    assert "SIC_A_NAICS" in d["_que_hacer"]


def test_without_fred_there_is_no_official_tam(tmp_path):
    msg = to.asegurar_tam_industria(_S(tmp_path, fred=None), "Software", "PLTR",
                                    providers={"edgar": _Edgar()})
    assert "FRED_API_KEY" in msg


def test_without_edgar_there_is_no_sic(tmp_path):
    msg = to.asegurar_tam_industria(_S(tmp_path), "Software", "PLTR", providers=None)
    assert "SIC" in msg


# --- lo que no se toca ----------------------------------------------------

def test_an_analyst_file_is_never_touched(tmp_path, monkeypatch):
    """`semiconductors.json` lo escribió alguien que leyó el estudio de Omdia,
    y el mercado de aceleradores de datacenter no existe en las encuestas del
    Census. Quien leyó el estudio sabe algo que la descarga no sabe."""
    d = tmp_path / "_industrias"
    d.mkdir()
    original = {"tam": 207_000_000_000, "tam_source": "Omdia", "tam_source_tier": 3}
    (d / "semiconductors.json").write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(to, "_serie_de_la_industria", lambda *a: pytest.fail(
        "no debio tocar un archivo de analista"))
    msg = to.asegurar_tam_industria(_S(tmp_path), "Semiconductors", "NVDA",
                                    providers={"edgar": _Edgar()})
    assert "analista" in msg
    assert json.loads((d / "semiconductors.json").read_text(encoding="utf-8")) == original


def test_a_fresh_answer_is_not_downloaded_again(tmp_path, monkeypatch):
    d = tmp_path / "_industrias"
    d.mkdir()
    (d / "software-infrastructure.json").write_text(json.dumps({
        "_generado_por": "vertex/tam_oficial", "_resuelto_en": "2026-07-01",
        "tam": 1}), encoding="utf-8")
    monkeypatch.setattr(to, "_serie_de_la_industria", lambda *a: pytest.fail(
        "no debio volver a descargar"))
    msg = to.asegurar_tam_industria(_S(tmp_path), "Software - Infrastructure",
                                    "PLTR", hoy=date(2026, 8, 5),
                                    providers={"edgar": _Edgar()})
    assert "vigente" in msg


def test_a_failed_download_never_erases_a_good_tam(tmp_path, monkeypatch):
    """Lo peor que podría hacer este módulo es borrar un TAM que funcionaba
    porque hoy FRED no respondió."""
    d = tmp_path / "_industrias"
    d.mkdir()
    (d / "software-infrastructure.json").write_text(json.dumps({
        "_generado_por": "vertex/tam_oficial", "_resuelto_en": "2026-01-01",
        "tam": 649_000_000_000, "tam_source": "FRED", "tam_source_tier": 1}),
        encoding="utf-8")
    monkeypatch.setattr(to, "_pedir", lambda u, p: None)  # FRED caido
    to.asegurar_tam_industria(_S(tmp_path), "Software - Infrastructure", "PLTR",
                              hoy=date(2026, 8, 5), providers={"edgar": _Edgar()})
    d2 = json.loads((d / "software-infrastructure.json").read_text(encoding="utf-8"))
    assert d2["tam"] == 649_000_000_000
