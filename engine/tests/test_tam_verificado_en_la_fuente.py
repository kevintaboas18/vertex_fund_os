"""La cifra se LEE de la fuente. Es la regla de Victor, y faltaba aquí.

Su `judge.py` usa el modelo para el **tier** del TAM —clasificar la calidad de
una fuente que ya existe— y su system prompt lo dice sin rodeos: *«Nunca
inventes cifras. Un juicio cualitativo puede citar contexto»*. El propio
docstring lo enumera: *«moat classification, catalyst probability, thesis
killers, TAM tier, customer concentration»*. **Tier**, no cifra.

Este módulo cruzaba esa línea: le pedía la cifra al modelo. Auditado contra lo
que había guardado, no aguantó una sola comprobación:

  - Nueve de diez citas eran redirects de grounding de Google. Ninguna apuntaba
    a la página de la fuente.
  - `consumer-electronics` — $1,06 billones «de Omdia». El enlace daba **404 a
    un día** de escrito.
  - `credit-services` — la URL traía caracteres de control. Inservible.
  - `discount-stores` y `drug-manufacturers-general` — el enlace abría, y la
    cifra **no aparecía** en la página.
  - Las tres que sobrevivían apuntaban a `icaew.com`, `beveragedaily.com` y
    `bestmediainfo.com`: prensa que *cuenta* el dato, no el organismo que lo
    *mide*. Que es exactamente lo que el encabezado de `tam_mundial.py` dice
    que vino a evitar — «devolvía el comunicado de prensa sobre el dato en vez
    del dato».

Resultado tras aplicar esto: **cero de diez** TAM sobrevivieron. Todos bajados
a sugerencia. Un número que nadie puede abrir no es evidencia; es un recuerdo
del modelo con aspecto de dato.

Lo que el modelo sigue haciendo es lo que hace en el motor de Victor:
**encontrar** la fuente. La cifra se lee del documento.
"""

from __future__ import annotations

import io
import pytest

from wbj.overlay import tam_mundial as tm


class _Respuesta(io.BytesIO):
    def __init__(self, cuerpo: str, url: str):
        super().__init__(cuerpo.encode("utf-8"))
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _pagina(monkeypatch, cuerpo: str, url: str):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=0: _Respuesta(cuerpo, url))


def _rota(monkeypatch, excepcion: Exception):
    import urllib.request

    def _boom(req, timeout=0):
        raise excepcion

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


_WSTS = "https://www.wsts.org/informe/2025"


# --- la cifra tiene que estar en la pagina ---------------------------------

def test_the_figure_must_appear_in_the_cited_page(monkeypatch):
    _pagina(monkeypatch,
            "<p>WSTS forecasts worldwide semiconductor sales of $630.5 billion</p>",
            _WSTS)
    ok, detalle = tm._verificar_en_la_fuente(_WSTS, 630_500_000_000, "WSTS")
    assert ok, detalle


def test_a_page_without_the_figure_is_rejected(monkeypatch):
    """Pasó de verdad: `discount-stores` y `drug-manufacturers-general` tenían
    enlaces que abrían y no contenían su cifra."""
    _pagina(monkeypatch, "<p>Un video sobre tendencias del sector</p>", _WSTS)
    ok, detalle = tm._verificar_en_la_fuente(_WSTS, 18_200_000_000_000, "WSTS")
    assert not ok and "no aparece" in detalle


@pytest.mark.parametrize("escrito", [
    "$630.5 billion", "630,500 million", "0.63 trillion", "630,500,000,000",
])
def test_the_usual_ways_of_writing_the_number_all_count(monkeypatch, escrito):
    """Una fuente escribe "$630.5 billion" y otra "630,500". Exigir una sola
    forma habría rechazado cifras que sí estaban."""
    _pagina(monkeypatch, f"<p>WSTS: {escrito}</p>", _WSTS)
    ok, _ = tm._verificar_en_la_fuente(_WSTS, 630_500_000_000, "WSTS")
    assert ok, f"la forma {escrito!r} no se reconocio"


# --- y la pagina tiene que ser del organismo -------------------------------

def test_the_press_that_reports_it_is_not_the_body_that_measures_it(monkeypatch):
    """El caso real: `bestmediainfo.com` traía la cifra correcta de PwC. La
    cifra estaba; la atribución, no. Y una atribución que no se puede abrir es
    exactamente lo que este archivo entero existe para impedir."""
    _pagina(monkeypatch,
            "<p>According to PwC, global internet advertising will hit $755.6bn</p>",
            "https://bestmediainfo.com/insights/global-advertising-revenue")
    ok, detalle = tm._verificar_en_la_fuente(
        "https://bestmediainfo.com/x", 755_600_000_000,
        "PwC Global Entertainment & Media Outlook 2026-30")
    assert not ok
    assert "no es el dominio" in detalle


def test_only_the_organisation_name_counts_not_the_report_title(monkeypatch):
    """«PwC Global Entertainment & **Media** Outlook» partido en palabras deja
    `media`, que encaja con `bestmediainfo.com`. Con eso, la nota de prensa
    volvía a colarse. Sólo cuenta la primera palabra: el organismo."""
    _pagina(monkeypatch, "<p>755.6</p>", "https://bestmediainfo.com/x")
    ok, _ = tm._verificar_en_la_fuente(
        "https://bestmediainfo.com/x", 755_600_000_000,
        "PwC Global Entertainment & Media Outlook")
    assert not ok, "'media' encajo con el dominio y dejo pasar la nota de prensa"


def test_the_body_own_page_passes(monkeypatch):
    _pagina(monkeypatch, "<p>755.6 billion</p>", "https://www.pwc.com/outlook")
    ok, _ = tm._verificar_en_la_fuente(
        "https://www.pwc.com/outlook", 755_600_000_000, "PwC Global Outlook")
    assert ok


# --- lo que no se puede abrir, no cuenta -----------------------------------

@pytest.mark.parametrize("fallo", [
    Exception("HTTP Error 404: Not Found"),      # consumer-electronics, a 1 dia
    TimeoutError("timed out"),                   # banks-diversified
    ValueError("URL can't contain control characters"),  # credit-services
])
def test_a_citation_that_cannot_be_opened_is_not_evidence(monkeypatch, fallo):
    _rota(monkeypatch, fallo)
    ok, detalle = tm._verificar_en_la_fuente(_WSTS, 630_500_000_000, "WSTS")
    assert not ok and "no se pudo abrir" in detalle


def test_a_citation_that_is_not_a_url_is_not_evidence():
    """`semiconductors` guardaba una frase textual en el campo del enlace."""
    ok, detalle = tm._verificar_en_la_fuente(
        "$123 billion in GPUs shipped in 2024", 207_000_000_000, "Omdia")
    assert not ok and "no es una URL" in detalle


# --- el validador la exige -------------------------------------------------

def test_validation_refuses_a_figure_it_could_not_check(monkeypatch):
    """No es un aviso: es un rechazo. Una cifra sin comprobar no llega al
    overlay y por tanto no puntúa."""
    monkeypatch.setattr(tm, "_verificar_en_la_fuente",
                        lambda c, t, f: (False, "la cita no se pudo abrir: HTTPError"))
    salida, motivo = tm._validar({
        "tam": 207_000_000_000, "tam_anio": 2026, "tam_source": "Omdia", "ambito": "mundial",
        "cita": "https://omdia.tech.informa.com/x",
        "capa": "ingresos anuales de chips"}, "Semiconductors")
    assert salida is None
    assert "recuerdo del modelo" in motivo


def test_a_verified_figure_records_where_it_was_read(monkeypatch):
    """Para que la próxima auditoría no tenga que volver a seguir el redirect:
    se guarda el destino final, no el enlace que caduca."""
    monkeypatch.setattr(tm, "_verificar_en_la_fuente",
                        lambda c, t, f: (True, "https://www.wsts.org/informe"))
    salida, _ = tm._validar({
        "tam": 630_500_000_000, "tam_anio": 2026, "tam_source": "WSTS", "ambito": "mundial",
        "cita": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x",
        "cita_textual": "worldwide semiconductor sales of $630.5 billion",
        "capa": "ventas mundiales de chips"}, "Semiconductors")
    assert salida is not None
    assert salida["_cita_verificada"] == "https://www.wsts.org/informe"
