"""A un banco se le prohíbe el *enterprise value*, no la aritmética.

`INDUSTRY_ADAPTERS.md` es concreto sobre qué veda: "do not use
enterprise-value/EBITDA, net-debt/EBITDA, or conventional FCFF". La razón es
que la deuda de un banco es su materia prima, no su financiamiento, así que
sumarla al valor de mercado no significa nada.

Dos filas quedaron apagadas por aplicar esa veda más ancha de lo que dice:

  - **VAL-ZHIST-035**, el múltiplo actual contra su propia serie histórica. El
    P/E de JPM tiene historia igual que el de cualquiera, y el overlay ya la
    publicaba: se verificó que `historical_multiples` llegaba con datos para
    JPM y para O. La ruta de adaptador la ignoraba con un `NOT_SCORABLE`
    escrito a mano.
  - **VAL-PEG-028**, P/E entre crecimiento esperado. No toca *enterprise
    value* por ningún lado. Su denominador también estaba en casa: medido,
    JPM 22,0% y BAC 21,4% de crecimiento de consenso.

Medido con las dos conectadas: JPM y BAC pasan de 0,609 a 1,000 de cobertura.

Lo que NO se hizo, y por eso hay un test que lo fija: darle P/E a un REIT.
`INDUSTRY_ADAPTERS.md` manda "replace EPS with FFO/AFFO" para ellos, así que
su hueco sigue siendo la falta de AFFO — un hueco de verdad, que cuenta en
contra — y no algo que se tape con el múltiplo equivocado.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

#: Lo mínimo para que la ruta de banco produzca valor: dividendos y coste de
#: capital propio. Copiado de `test_valuation_financial_adapter.py`.
_BASE = {"dividend_per_share": 5.80,
         "dividends_per_share_history": [3.4, 3.6, 3.8, 4.0, 4.1, 4.8, 5.8],
         "dividend_tag": "CommonStockDividendsPerShareDeclared",
         "dividend_period_end": "2025-12-31",
         "risk_free_rate": 0.042, "beta": 1.1}

#: La serie histórica de P/E y el crecimiento del consenso: los dos insumos que
#: la ruta de adaptador tenía delante y no miraba.
_CON_INSUMOS = {**_BASE,
                "historical_multiples": [22.0, 24.5, 19.8, 26.1, 21.3],
                "eps_growth_pct": 0.22}


def _paquete(adapter: str) -> Packet:
    data = json.loads(_FIXTURE.read_text())
    data["analysis"]["industry_adapter"] = adapter
    return Packet.model_validate(data)


def _filas(salida):
    return {m.metric_id: m for m in salida.metrics}


def _dim(salida, nombre):
    return {d.name: d for d in salida.dimensions}[nombre]


# --- las dos filas que estaban apagadas ------------------------------------

@pytest.mark.parametrize("adapter", ["banks", "insurers"])
def test_the_historical_multiple_is_scored_when_the_series_arrives(adapter):
    fila = _filas(val.run(_paquete(adapter), _CON_INSUMOS)).get("VAL-ZHIST-035")
    assert fila is not None, "la ruta de adaptador no emitio VAL-ZHIST-035"
    assert fila.value is not None, (
        "el overlay traia la serie historica y la fila siguio sin puntuar")


@pytest.mark.parametrize("adapter", ["banks", "insurers"])
def test_peg_is_not_an_enterprise_value_metric(adapter):
    """P/E entre crecimiento: ni deuda ni EBITDA aparecen en la fórmula."""
    fila = _filas(val.run(_paquete(adapter), _CON_INSUMOS)).get("VAL-PEG-028")
    assert fila is not None, "la ruta de adaptador no emitio VAL-PEG-028"
    assert fila.value is not None, (
        "el crecimiento del consenso estaba en el overlay y el PEG no se calculo")


def test_the_two_rows_reach_their_dimensions():
    """Calcular la métrica no basta: el hueco estaba en que su puntaje no
    llegara al slot de la dimensión, que es lo que cuenta para la cobertura."""
    salida = val.run(_paquete("banks"), _CON_INSUMOS)
    for nombre in (val.DIM_HIST_PEER, val.DIM_MULTIPLES):
        assert any(v.is_valid for _, v in _dim(salida, nombre).metric_scores), (
            f"{nombre} sigue sin un solo puntaje valido")


def test_the_bank_category_becomes_complete():
    """Medido sobre datos reales: JPM y BAC 0,609 -> 1,000. Con el fixture, lo
    que se fija es que cruza el umbral del 70% de `SCORING_ENGINE.md`."""
    salida = val.run(_paquete("banks"), _CON_INSUMOS)
    assert salida.coverage >= 0.70
    assert salida.status != "INCOMPLETE"


# --- lo que sigue prohibido -------------------------------------------------

def test_the_enterprise_value_inputs_stay_barred():
    """Conectar dos filas no reabre la puerta a las que sí veda el adaptador."""
    filas = _filas(val.run(_paquete("banks"), _CON_INSUMOS))
    for prohibido in ("VAL-FCFF-005", "VAL-JEVS-033", "VAL-RDCF-027"):
        assert prohibido not in filas
    assert NullState.NOT_APPLICABLE in [
        v.state for _, v in _dim(val.run(_paquete("banks"), _CON_INSUMOS),
                                val.DIM_MULTIPLES).metric_scores], (
        "el DCF inverso tiene que seguir saliendo del denominador")


def test_a_reit_does_not_get_a_price_earnings_multiple():
    """"Replace EPS with FFO/AFFO" — INDUSTRY_ADAPTERS.md. Un REIT deprecia
    edificios que se aprecian, así que su utilidad contable no es la base de
    su múltiplo. Darle P/E aquí subiría la cobertura tapando el hueco real."""
    salida = val.run(_paquete("reits"), _CON_INSUMOS)
    filas = _filas(salida)
    for fila in ("VAL-ZHIST-035", "VAL-PEG-028"):
        assert filas.get(fila) is None or filas[fila].value is None, (
            f"{fila} se calculo sobre EPS para un REIT")
    assert salida.coverage < 0.70, (
        "sin AFFO transcrito la categoria de un REIT sigue INCOMPLETE")


# --- la ausencia se sigue viendo -------------------------------------------

def test_without_the_inputs_the_gap_still_counts_against():
    """El arreglo es "úsalo cuando esté", no "asúmelo cuando falte": sin serie
    y sin crecimiento, las dos filas cuentan en contra igual que antes."""
    salida = val.run(_paquete("banks"), _BASE)

    # El histórico entero depende de la serie: sin ella, ni un solo
    # NOT_APPLICABLE, porque el P/E de un banco sí tiene historia.
    assert NullState.NOT_APPLICABLE not in [
        v.state for _, v in _dim(salida, val.DIM_HIST_PEER).metric_scores], (
        "un insumo ausente no convierte al historico en inaplicable")

    # En múltiplos hay UN inaplicable legítimo —el DCF inverso, que el
    # adaptador sí veda— así que aquí se mira la fila del PEG por su nombre.
    peg = _filas(salida).get("VAL-PEG-028")
    assert peg is not None and peg.value is None
    assert peg.state is NullState.MISSING, (
        "sin crecimiento declarado el PEG es un hueco, no una metrica que "
        "no aplique: MISSING cuenta en contra y NOT_APPLICABLE no")

    assert salida.coverage < 0.70
