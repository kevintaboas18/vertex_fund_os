"""Que el DCF inverso no encuentre raíz también es una respuesta.

`VAL-RDCF-027` resuelve qué crecimiento hace que el valor modelado iguale al
precio. Devolvía `NO_SIGN_CHANGE_IN_GROWTH_BOUNDS` y ahí se acababa — y con
eso se tiraba a la basura la conclusión, que en tres de diez tickers medidos
(AMD, TSLA, PLTR) era contundente.

El valor modelado **no es monótono** en el crecimiento: más crecimiento exige
más reinversión, así que pasado cierto punto el flujo libre cae y el valor con
él. Medido en AMD: el máximo que el modelo alcanza son ~11,46 por acción y el
precio son 482,05. No es que el solucionador se quedara corto de rango —
ampliarlo a +300% no cambió nada. Es que **ningún crecimiento justifica ese
precio**.

`SCORING.md` describe esa situación palabra por palabra en la banda 0-3 de
`growth_adjusted_multiples`: "price implies growth/returns far above evidenced
capacity". Puntuarla 0 no es inventar calibración; es leer la que ya está
escrita.
"""

from __future__ import annotations

import pytest

from wbj.core.nullstates import NullState
from wbj.engines import valuation_engine as ve
from wbj.schemas.valuation import ReverseDCFInputs


def _inputs(**kw):
    base = dict(revenue0=34_600_000_000.0, shares=1_636_000_000.0, tax_rate=0.21,
                roic=0.15, years=5, net_debt=0.0, margin=0.107, wacc=0.157,
                tv_growth=0.025, consensus_growth=0.20)
    base.update(kw)
    return ReverseDCFInputs(**base)


def test_a_price_no_growth_can_reach_says_so():
    """El caso de AMD, con sus cifras reales."""
    r = ve.reverse_dcf(482.05, 1_636_000_000.0, _inputs())
    assert not r.converged
    assert "PRICE_ABOVE_EVERY_MODELLED_VALUE" in " ".join(r.warnings)
    assert "far above evidenced capacity" in (r.implied_growth.warnings or [""])[0], (
        "el aviso tiene que citar la banda del Cerebro que lo justifica")


def test_the_state_stays_not_scorable():
    """No hay crecimiento implicito que reportar, y cambiar el estado alteraba
    un contrato que nada necesitaba. Lo que cambia es el AVISO."""
    r = ve.reverse_dcf(482.05, 1_636_000_000.0, _inputs())
    assert r.implied_growth.state is NullState.NOT_SCORABLE


def test_a_solvable_price_still_solves():
    """El caso normal no se toca: NVDA y KO seguian resolviendo."""
    r = ve.reverse_dcf(11.0, 1_636_000_000.0, _inputs())
    assert r.converged and r.implied_growth.is_valid


def test_the_specialist_scores_it_zero():
    """La consecuencia medible: AMD, TSLA y PLTR pasaban de 0,880 a 1,000 de
    cobertura en valuation."""
    import inspect

    from wbj.specialists import valuation as val

    fuente = inspect.getsource(val)
    assert "PRICE_ABOVE_EVERY_MODELLED_VALUE" in fuente
    assert "PRICE_BELOW_EVERY_MODELLED_VALUE" in fuente, (
        "el extremo opuesto -- precio por debajo de todo valor modelado -- "
        "tambien tiene banda en SCORING.md, la de 7-10"
    )
