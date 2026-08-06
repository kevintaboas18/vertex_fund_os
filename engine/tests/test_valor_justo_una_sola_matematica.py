"""El valor justo de un adaptador se calcula UNA vez, no dos.

`specialists/valuation.py` lo computa para puntuar, y `overlay/from_packet.py`
lo necesita otra vez para derivar el margen de seguridad que consume el agente
de riesgo — `SCORING.md` de riesgo dice "usa el packet del agente de
valuación; no dupliques su score".

Duplicar la MATEMÁTICA para no duplicar el score es lo que hacía que
divergieran. Medido antes de este arreglo, reconstruyendo el cálculo a mano:

    Realty Income   overlay  -81,2%   agente  -2,2%    40 veces

Con eso, riesgo habría dicho que la empresa está carísima mientras valuación
decía que está en precio. Una contradicción entre dos partes del sistema no se
ve como se ve un hueco.

Ahora hay una función en el motor y dos llamadores.
"""

from __future__ import annotations

import inspect

import pytest

from wbj.engines import valuation_engine as ve


def test_the_shared_function_exists_and_both_callers_use_it():
    """Si alguien vuelve a escribir la aritmética a mano en cualquiera de los
    dos sitios, esto falla."""
    import wbj.overlay.from_packet as fp
    import wbj.specialists.valuation as val

    assert callable(ve.valor_justo_por_adaptador)
    for modulo, nombre in ((val, "valuation.py"), (fp, "from_packet.py")):
        assert "valor_justo_por_adaptador" in inspect.getsource(modulo), (
            f"{nombre} ya no usa la funcion compartida: los dos volveran a "
            "divergir en cuanto uno de los dos cambie")


def test_a_bank_averages_residual_income_and_the_dividend_models():
    """La matriz le asigna renta residual / exceso de retorno / DDM."""
    justo, modelos = ve.valor_justo_por_adaptador(
        adapter="banks", price=100.0, shares=1000.0,
        net_income=150.0, equity_now=1000.0, equity_begin=950.0,
        cost_of_equity_value=0.09, dividend_per_share=2.0,
        dividend_growth=0.05)
    assert justo is not None and justo > 0
    assert "residual income" in modelos
    assert any("DDM" in m or "H-model" in m for m in modelos)


def test_a_reit_does_not_run_residual_income():
    """Su matriz nombra NAV, AFFO y cap rates, y ninguno esta registrado en
    FORMULAS.md. Le queda el modelo de dividendos, que si lo esta."""
    _, modelos = ve.valor_justo_por_adaptador(
        adapter="reits", price=50.0, shares=1000.0,
        net_income=150.0, equity_now=1000.0, equity_begin=950.0,
        cost_of_equity_value=0.09, dividend_per_share=3.0,
        dividend_growth=0.03)
    assert "residual income" not in modelos, (
        "aplicar renta residual a un REIT es lo que daba -81,2% contra -2,2%")


def test_the_residual_income_horizon_is_not_a_single_period():
    """Con un solo periodo el valor se trunca al libro y el margen sale
    absurdo: medido, JPM -158,9% y SPG -705,4%."""
    uno, _ = ve.valor_justo_por_adaptador(
        adapter="banks", price=100.0, shares=1000.0, net_income=150.0,
        equity_now=1000.0, equity_begin=950.0, cost_of_equity_value=0.09,
        dividend_per_share=None, dividend_growth=None, forecast_years=1)
    cinco, _ = ve.valor_justo_por_adaptador(
        adapter="banks", price=100.0, shares=1000.0, net_income=150.0,
        equity_now=1000.0, equity_begin=950.0, cost_of_equity_value=0.09,
        dividend_per_share=None, dividend_growth=None, forecast_years=5)
    assert cinco > uno, "cinco años de renta residual valen mas que uno"


def test_without_inputs_there_is_no_estimate():
    justo, modelos = ve.valor_justo_por_adaptador(
        adapter="banks", price=100.0, shares=None, net_income=None,
        equity_now=None, equity_begin=None, cost_of_equity_value=None,
        dividend_per_share=None, dividend_growth=None)
    assert justo is None and modelos == []
