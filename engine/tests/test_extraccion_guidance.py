"""`BUS-GUIDE-027`: el guidance sale del comunicado, y ninguna API lo tiene.

Se verifico contra las fuentes: FMP no tiene endpoint de guidance
(`stable/guidance` y `stable/financial-guidance` devuelven 404) y lo que si
tiene --`revenueEstimated`, `epsEstimated`-- es CONSENSO DE ANALISTAS.
FinnHub, igual.

Y la diferencia no es un matiz. BUS-GUIDE-027 mide si la gerencia cumple SUS
PROPIAS metas, que es una senal de calidad del management. El consenso mide
otra cosa: que tan bien la calle modela la empresa. Sustituir uno por otro
daria un numero verosimil de algo que nadie pidio -- el mismo error que
confundir cuentas por cobrar con ingresos, pero mas dificil de notar.

Lo que se prueba aqui son las defensas, porque el extractor usa un modelo y
lo unico que impide que invente es que la cita tiene que estar escrita.
"""
from types import SimpleNamespace

import pytest

from wbj.extract.filing import _GUIDANCE_ANCHORS, _Guidance, extract_guidance


_TEXTO = (
    "NVIDIA Corporation reported record revenue for the quarter. "
    "Outlook: For the first quarter of fiscal 2027, revenue is expected to be "
    "$43.0 billion, plus or minus 2%. "
    "Gross margins are expected to be 70.6% GAAP."
)
_CITA = ("Outlook: For the first quarter of fiscal 2027, revenue is expected "
         "to be $43.0 billion, plus or minus 2%.")


def _cliente(**campos):
    """Un cliente que devuelve exactamente lo que se le indique."""
    datos = {"metric": "revenue", "period_label": "first quarter of fiscal 2027",
             "low": 42.14, "high": 43.86, "midpoint": 43.0,
             "unit": "billions_usd", "quote": _CITA}
    datos.update(campos)

    class _Msgs:
        def parse(self, **_kw):
            # La API real deja la respuesta en `parsed_output`.
            return SimpleNamespace(parsed_output=_Guidance(**datos))

    return SimpleNamespace(messages=_Msgs())


def _release():
    return {"text": _TEXTO, "url": "https://sec.gov/x", "accession": "0001-26-1"}


def test_un_guidance_bien_citado_entra():
    g = extract_guidance(_release(), SimpleNamespace(), client=_cliente())
    assert g is not None
    assert g["guidance_midpoint"] == pytest.approx(43.0)
    assert g["unidad"] == "billions_usd"
    assert g["fuente_guidance"] == _CITA
    assert g["url"] == "https://sec.gov/x"


def test_una_cita_que_no_esta_en_el_comunicado_se_descarta():
    """La unica defensa que impide que un modelo invente una cifra: si la
    frase no esta escrita en el documento, el numero no existe."""
    g = extract_guidance(_release(), SimpleNamespace(),
                         client=_cliente(quote="Revenue will be $99.0 billion."))
    assert g is None


def test_sin_cita_no_hay_numero():
    g = extract_guidance(_release(), SimpleNamespace(), client=_cliente(quote=None))
    assert g is None


def test_un_punto_medio_fuera_de_su_rango_no_es_un_punto_medio():
    """`low <= midpoint <= high` no es una formalidad: un midpoint que no cae
    entre sus extremos es otro numero, y entraria al score como si fuera el
    guidance."""
    g = extract_guidance(_release(), SimpleNamespace(),
                         client=_cliente(midpoint=50.0))
    assert g is None


def test_un_rango_coherente_pero_asimetrico_si_entra():
    """No se exige que el midpoint sea la media exacta: un emisor puede guiar
    un rango sesgado y decir cual es su punto medio."""
    g = extract_guidance(_release(), SimpleNamespace(),
                         client=_cliente(low=40.0, high=44.0, midpoint=43.0))
    assert g is not None
    assert g["guidance_midpoint"] == pytest.approx(43.0)


def test_sin_comunicado_no_se_pregunta_nada():
    """NVIDIA, Lilly y Exxon publican en su propia sala de prensa: su 8-K es
    una caratula. None es la respuesta correcta, no un fallo."""
    assert extract_guidance({"text": ""}, SimpleNamespace(), client=_cliente()) is None
    assert extract_guidance(None, SimpleNamespace(), client=_cliente()) is None


def test_un_comunicado_sin_guidance_no_gasta_una_llamada():
    """Si ninguna ancla aparece, no se pregunta: el extracto vacio corta antes
    de llamar al modelo."""
    sin = {"text": "The company reported revenue of $10 billion for the quarter."}

    class _Explota:
        class messages:
            @staticmethod
            def parse(**_kw):
                raise AssertionError("no debio preguntarse")

    assert extract_guidance(sin, SimpleNamespace(), client=_Explota()) is None


def test_las_anclas_cubren_como_hablan_los_emisores():
    """Medido en comunicados reales: KO abre con "Outlook", WMT con "issues
    guidance for Q2", PLTR con "Raises FY 2026 Revenue Guidance"."""
    for frase in ("outlook", "guidance", "we expect", "expected to be"):
        assert frase in _GUIDANCE_ANCHORS
