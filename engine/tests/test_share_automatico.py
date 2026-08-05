"""La participación de mercado se calcula sola, para cualquier ticker.

Hasta ahora `share` y `share_history` sólo existían si alguien los escribía a
mano en `Entradas/<TICKER>.json`. Sólo NVDA los tenía, así que sólo NVDA
puntuaba MKT-SHARE-006 y MKT-SHDELTA-007 — y la única forma de extender la
cobertura era sentarse a teclear un archivo por empresa.

Los dos números ya estaban en casa: el denominador lo comparte
`Entradas/_industrias/<slug>.json` y el numerador es el segmento del 10-K que
FMP publica para todo emisor. Lo que faltaba era casarlos.

Lo que estos tests fijan es el CUIDADO, porque una participación inventada es
peor que un hueco:

  - el nombre del segmento lo pone cada emisor (NVDA "Data Center", Intel
    "Data Center Group"), así que se casa por patrón;
  - si encajan DOS segmentos no se elige ninguno: significa que el patrón está
    mal escrito, y sumarlos o quedarse con el mayor sería inventar;
  - lo que el analista declaró gana siempre — esto sólo rellena huecos.
"""

from __future__ import annotations

from wbj.overlay.from_packet import _segmento_del_mercado, _share_automatico


class _FMP:
    """No hace falta red: el historial de segmentos ya viene resuelto."""

    def __init__(self, historial=None):
        self._historial = historial or []

    def revenue_product_segmentation(self, ticker, period="annual"):
        return self._historial


# --- elegir el segmento ---------------------------------------------------

def test_the_segment_is_matched_by_pattern_not_by_exact_name():
    """Cada emisor bautiza su segmento como quiere. Exigir igualdad exacta
    dejaba fuera a Intel por escribir "Group" al final."""
    for nombre in ("Data Center", "Data Center Group", "DATA CENTER"):
        segmentos = {nombre: 16_635_000_000.0, "Gaming": 2_600_000_000.0}
        assert _segmento_del_mercado(segmentos, ["data center"]) == (
            nombre, 16_635_000_000.0)


def test_two_matching_segments_pick_nothing():
    """Dos segmentos que parecen el mercado significan que el patrón está mal
    escrito. Sumarlos inflaría la participación; quedarse con el mayor la
    encogería. Las dos serían una respuesta inventada."""
    segmentos = {"Data Center": 16_000_000_000.0,
                 "Data Center Solutions": 4_000_000_000.0}
    assert _segmento_del_mercado(segmentos, ["data center"]) is None


def test_no_match_is_simply_no_answer():
    """AVGO reporta "Semiconductor Solutions", que mezcla aceleradores con
    conectividad y almacenamiento. No encaja, y eso es lo correcto: un hueco
    se ve, una participación inflada no."""
    segmentos = {"Semiconductor Solutions": 36_900_000_000.0,
                 "Infrastructure Software": 21_000_000_000.0}
    assert _segmento_del_mercado(segmentos, ["data center"]) is None
    assert _segmento_del_mercado(segmentos, None) is None
    assert _segmento_del_mercado(None, ["data center"]) is None


def test_a_segment_without_a_positive_figure_is_not_a_segment():
    for valor in (0, -1, None, "16B"):
        assert _segmento_del_mercado({"Data Center": valor}, ["data center"]) is None


# --- calcular la participación -------------------------------------------

def test_the_share_is_the_segment_over_the_industry_tam():
    """El caso de AMD: $16.635B de datacenter sobre el TAM de $207B de Omdia."""
    fuera = _share_automatico(_FMP(), "AMD", {"Data Center": 16_635_000_000.0},
                              207_000_000_000, None, ["data center"])
    assert fuera["share"] == {"company_sales": 16_635_000_000.0,
                              "total_market_sales": 207_000_000_000.0}


def test_without_a_tam_there_is_no_share():
    """Sin denominador no hay cociente. Un ticker cuya industria no declara
    TAM se queda sin participación, no con una participación a medias."""
    seg = {"Data Center": 16_635_000_000.0}
    assert _share_automatico(_FMP(), "AMD", seg, None, None, ["data center"]) == {}
    assert _share_automatico(_FMP(), "AMD", seg, 0, None, ["data center"]) == {}


def test_without_a_pattern_nothing_is_guessed():
    """Una industria que no declara `_segmento_patrones` no obtiene
    participación. Adivinar cuál de los segmentos compite en el mercado es
    exactamente lo que este código no debe hacer."""
    assert _share_automatico(_FMP(), "AMD", {"Data Center": 1.0},
                             207_000_000_000, None, None) == {}


def test_the_history_needs_the_previous_year_on_both_sides():
    """La variación sólo significa algo si numerador y denominador se mueven
    en el mismo periodo. Con el TAM de un año y el segmento de dos, la serie
    no se publica."""
    # FMP entrega las filas de más reciente a más antigua, así que el año
    # anterior es el índice 1 — el mismo orden que tiene `tam_history` al
    # revés, y por eso el TAM anterior se lee con `[-2]`.
    historial = [{"date": "2025", "data": {"Data Center": 16_635_000_000.0}},
                 {"date": "2024", "data": {"Data Center": 12_580_000_000.0}}]
    fuera = _share_automatico(_FMP(historial), "AMD",
                              {"Data Center": 16_635_000_000.0},
                              207_000_000_000, [123_000_000_000, 207_000_000_000],
                              ["data center"])
    assert fuera["share_history"] == [
        round(12_580_000_000.0 / 123_000_000_000, 6),
        round(16_635_000_000.0 / 207_000_000_000, 6)]

    # Mismo segmento, pero el TAM sólo trae un año: no hay serie.
    fuera = _share_automatico(_FMP(historial), "AMD",
                              {"Data Center": 16_635_000_000.0},
                              207_000_000_000, [207_000_000_000], ["data center"])
    assert "share_history" not in fuera
    assert "share" in fuera, "el nivel sigue siendo válido sin la serie"
