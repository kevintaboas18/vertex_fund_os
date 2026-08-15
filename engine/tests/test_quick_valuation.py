"""La valuación del puntaje rápido salía 0,0 o casi, y por seis motivos.

«¿Por qué en el puntaje rápido el de valuation sale 0.0 o bien bajito?»

La categoría era **byte a byte la de Victor** — mismo `_valuation_category`,
mismas anclas — así que los seis fallos son suyos, no una divergencia. Cada
uno se midió sobre el motor antes de tocarlo, y cada caso de aquí fija lo
medido:

1. Una empresa que PIERDE dinero puntuaba MÁS que la misma ganándolo: 5,7
   contra 4,5. El P/E negativo se caía de la lista en silencio y la nota
   quedaba colgando del múltiplo de caja que sobrevivía.
2. `coverage` seguía diciendo 1,00 con la mitad de la evidencia fuera. La
   misma laguna en `financial` y en `risk` sí bajaba las suyas a 0,50:
   valuación era la ÚNICA que filtraba la métrica en vez de pasarla nula.
3. Con precio en pantalla y sin base positiva, el motivo decía «sin precio de
   mercado». El precio estaba.
4. No había NINGÚN ajuste por crecimiento, cuando la dimensión más grande de
   `Cerebro/06_valuation_analysis/SCORING.md` es «Growth-adjusted multiples …
   VAL-PEG-028 … reverse DCF», cuya banda 7-10 es literalmente «price embeds
   conservative growth relative to quality».
5. El flujo de caja era el de un solo ejercicio, contra el «Use normalized,
   not peak-cycle, cash flow» del mismo documento.
6. El suelo de las anclas (P/E 70x, P/FCF 90x) hacía indistinguibles una
   empresa a 75x y otra a 400x: las dos 0,0.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from wbj.quick import _A_PE, _A_PEG, quick_scorecard


def _serie(v, ends=("2024-12-31",)):
    return [{"end": e, "val": v} for e in ends]


_TRES = ("2022-12-31", "2023-12-31", "2024-12-31")


def _packet(ni=93.7e9, ocf=118.3e9, capex=9.4e9, price=230.0, mcap=3.5e12,
            shares=15.41e9, estimates=None, ocf_serie=None, capex_serie=None):
    return {
        "ticker": "TEST", "as_of": "2026-08-15",
        "annual": {
            "revenue": _serie(1e11), "net_income": _serie(ni),
            "operating_cash_flow": ocf_serie or _serie(ocf, _TRES),
            "capex": capex_serie or _serie(capex, _TRES),
            "long_term_debt": _serie(1e10), "equity": _serie(5e10),
            "operating_income": _serie(3e10), "gross_profit": _serie(5e10),
            "interest_expense": _serie(1e9), "diluted_shares": _serie(shares),
        },
        "market_data": {"price": price, "market_cap": mcap,
                        "estimates": estimates or []},
        "industry_adapter": "default_nonfinancial",
    }


def _val(packet, lang="es"):
    return next(r for r in quick_scorecard(packet, lang)["categories"]
                if r["key"] == "valuation")


# ---------------------------------------------------------------------------
# 1 y 2 — perder dinero no puede MEJORAR la nota, y la cobertura no puede mentir
# ---------------------------------------------------------------------------


def test_perder_dinero_ya_no_SUBE_el_puntaje():
    """Medido antes del arreglo: 4,5 ganando y **5,7** perdiendo 10.000 M.

    Ese es el fallo entero en una línea. El P/E negativo desaparecía de la
    lista y la media se hacía sobre el único múltiplo que quedaba, así que la
    pérdida no restaba: quitaba al testigo que la habría delatado.
    """
    gana = _val(_packet(ni=93.7e9))
    pierde = _val(_packet(ni=-10.0e9))

    assert gana["status"] == "scored" and gana["score10"] is not None
    assert pierde["score10"] is None, (
        f"perdiendo dinero sigue saliendo {pierde['score10']}")
    assert pierde["status"] == "not_scorable"


def test_la_cobertura_BAJA_cuando_se_cae_un_multiplo():
    """La misma laguna, en las tres categorías que la sufren.

    `financial` y `risk` ya bajaban de 1,00 a 0,50. Valuación se quedaba en
    1,00 — «cobertura completa» con media evidencia — porque construía la
    dimensión con la lista ya filtrada y el motor nunca se enteraba del hueco.
    """
    completo = _val(_packet())
    medio = _val(_packet(ni=-10.0e9))
    assert completo["coverage"] == pytest.approx(1.0)
    assert medio["coverage"] < 0.7, (
        f"con medio múltiplo la cobertura dice {medio['coverage']}")


def test_y_el_hueco_lo_ve_el_MOTOR_no_una_regla_escrita_aparte():
    """El corte es el 70% de `score10_value`, el mismo de todas las
    categorías. Fijarlo aquí a mano sería una segunda copia del umbral."""
    from wbj.core.scoring import COVERAGE_USABLE

    assert COVERAGE_USABLE > 0.5, (
        "con el umbral por debajo del 50% media evidencia volvería a puntuar")


# ---------------------------------------------------------------------------
# 3 — el motivo dice QUÉ falta, y no siempre lo mismo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("caso,packet_kw,debe_decir,no_debe_decir", [
    ("sin precio", {"price": None}, "precio", None),
    ("sin beneficio", {"ni": -10.0e9}, "flujo de caja", "sin precio"),
    ("sin caja", {"ocf": 5.0e9, "capex": 20.0e9}, "beneficios", "sin precio"),
    ("sin nada", {"ni": -10.0e9, "ocf": 5.0e9, "capex": 20.0e9},
     "múltiplo que calcular", "sin precio"),
])
def test_el_motivo_dice_la_verdad(caso, packet_kw, debe_decir, no_debe_decir):
    """Decir «sin precio de mercado» con el precio en pantalla manda a mirar
    donde no es. Un motivo falso es peor que no tener motivo."""
    r = _val(_packet(**packet_kw))
    assert r["score10"] is None, caso
    razon = r["reason"]
    assert debe_decir in razon, f"{caso}: {razon}"
    if no_debe_decir:
        assert no_debe_decir not in razon, f"{caso} miente: {razon}"


def test_el_motivo_tambien_esta_en_INGLES():
    """El panel es bilingüe: un motivo solo en español se ve en español al
    elegir inglés, que es exactamente lo que el guardián del idioma persigue.
    """
    r = _val(_packet(ni=-10.0e9), lang="en")
    assert r["reason"] and not re.search(r"[áéíóúñ¿¡]", r["reason"]), r["reason"]


# ---------------------------------------------------------------------------
# 4 — el ajuste por crecimiento que el Cerebro exige
# ---------------------------------------------------------------------------


def _con_crecimiento(g):
    """El mismo packet, mismo P/E, cambiando SOLO el consenso de crecimiento."""
    eps = 72.9e9 / 24.80e9
    est = [] if g is None else [{"date": "2026-12-31", "epsAvg": eps * (1 + g)}]
    return _packet(ni=72.9e9, ocf=64.1e9, capex=3.2e9, price=140.0,
                   mcap=3.43e12, shares=24.80e9, estimates=est)


def test_el_mismo_multiplo_NO_vale_lo_mismo_creciendo_al_10_que_al_60():
    """«Price embeds conservative growth relative to quality» — SCORING.md,
    banda 7-10 de la dimensión MÁS GRANDE de valuación.

    A 47,6x el motor daba 1,9 y punto, creciera lo que creciera. Pagar eso por
    un 10% y pagarlo por un 60% no es la misma decisión, y ahora no es el
    mismo número.
    """
    lento = _val(_con_crecimiento(0.10))["score10"]
    rapido = _val(_con_crecimiento(0.60))["score10"]
    assert rapido > lento + 2.0, (
        f"el crecimiento apenas mueve la nota: {lento} contra {rapido}")


def test_sin_consenso_se_cae_al_P_E_crudo_y_NO_a_un_hueco():
    """Una empresa sin cobertura de analistas no puede quedarse sin valuación
    por eso: el múltiplo crudo sigue siendo evidencia."""
    r = _val(_con_crecimiento(None))
    assert r["status"] == "scored" and r["score10"] is not None
    assert r["coverage"] == pytest.approx(1.0)


def test_un_crecimiento_NEGATIVO_no_se_usa_como_si_fuera_bueno():
    """Dividir un P/E entre un crecimiento negativo da un PEG negativo, que
    interpolado contra anclas descendentes saldría 10 — «baratísima» por
    encoger. Con base no positiva se usa el P/E crudo."""
    conserva = _val(_con_crecimiento(-0.30))["score10"]
    crudo = _val(_con_crecimiento(None))["score10"]
    assert conserva == crudo, (
        f"un crecimiento negativo cambió la nota a {conserva}")


def test_las_anclas_del_PEG_son_las_del_ESPECIALISTA_PROFUNDO():
    """Copiadas, no importadas —el quick no arrastra el módulo profundo—, así
    que hace falta algo que impida que se separen. Es la misma vigilancia que
    ya tienen las catorce casillas del panel contra el motor."""
    src = (pathlib.Path(__file__).parent.parent / "wbj" / "specialists"
           / "valuation.py").read_text(encoding="utf-8")
    i = src.index("VAL-PEG-028: el P/E contra el crecimiento")
    trozo = src[i:i + 2000]
    m = re.search(r"_score_from_anchor\(_peg,\s*(\[[^\]]*\])", trozo)
    assert m, "cambió la forma de la línea del PEG en el especialista profundo"
    profundas = [(float(a), float(b))
                 for a, b in re.findall(r"\(([\d.]+),\s*([\d.]+)\)", m.group(1))]
    assert profundas == _A_PEG, (
        f"el quick usa {_A_PEG} y el profundo {profundas}: se separaron")


# ---------------------------------------------------------------------------
# 5 — el flujo de caja normalizado
# ---------------------------------------------------------------------------


def test_un_ANO_de_capex_fuerte_ya_no_hunde_la_categoria():
    """«Use normalized, not peak-cycle, cash flow» — SCORING.md, fila de
    cash-flow yield. Un ciclo de inversión (los centros de datos de ahora es
    el caso vivo) parte el flujo del año y con él la nota.

    Tres años iguales salvo el último, que se lleva un capex cuatro veces
    mayor. La mediana lo absorbe; el último año solo, no.
    """
    ocf = _serie(118.3e9, _TRES)
    capex = [{"end": "2022-12-31", "val": 9.4e9},
             {"end": "2023-12-31", "val": 9.4e9},
             {"end": "2024-12-31", "val": 40.0e9}]
    picudo = _val(_packet(ocf_serie=ocf, capex_serie=capex))
    normal = _val(_packet())
    assert picudo["score10"] == normal["score10"], (
        f"el año de capex fuerte movió la nota a {picudo['score10']}")


def test_los_dos_lados_se_emparejan_POR_EJERCICIO():
    """Flujo operativo de un año contra el capex de otro no es el flujo libre
    de ninguno. Es el mismo peligro que `_at_period` ya vigila en la cobertura
    de intereses, aplicado aquí.
    """
    from wbj.quick import _fcf_normalizado

    a = {"operating_cash_flow": [{"end": "2023-12-31", "val": 100.0},
                                 {"end": "2024-12-31", "val": 120.0}],
         # El capex de 2024 NO está: no se puede tomar prestado el de 2023.
         "capex": [{"end": "2023-12-31", "val": 20.0}]}
    assert _fcf_normalizado(a) == 80.0, (
        "se emparejó el flujo de 2024 con el capex de 2023")


def test_sin_ningun_ejercicio_completo_el_flujo_es_None_y_NO_cero():
    from wbj.quick import _fcf_normalizado

    assert _fcf_normalizado({"operating_cash_flow": [], "capex": []}) is None


# ---------------------------------------------------------------------------
# 6 — el suelo, y lo que NO cambió
# ---------------------------------------------------------------------------


def test_lo_caro_de_verdad_SIGUE_saliendo_bajo():
    """Arreglar el suelo no puede convertirse en repartir aprobados. Una
    empresa a 400x pagando por un crecimiento que no la justifica sigue
    saliendo abajo — eso nunca fue el fallo."""
    r = _val(_packet(ni=0.46e9, ocf=1.15e9, capex=0.01e9, price=80.0,
                     mcap=180e9, shares=2.40e9))
    assert r["score10"] is not None and r["score10"] < 1.0, r["score10"]


def test_las_anclas_del_P_E_no_se_tocaron():
    """El encargo era arreglar los errores, no reescalar la generosidad de la
    categoría. Las anclas siguen siendo las de Victor."""
    assert _A_PE == [(10.0, 10.0), (18.0, 8.0), (28.0, 5.0), (45.0, 2.0),
                     (70.0, 0.0)]
