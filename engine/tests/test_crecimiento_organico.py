"""El crecimiento orgánico se transcribe del comunicado, no se deduce.

`FIN-GR-004` divide crecimiento orgánico entre crecimiento total, y
`DATASET.md` nombra su fuente: "issuer reconciliation" — que vive en el
comunicado de resultados, no en el 10-K. El motor no la leía.

La línea que no se puede cruzar: `organic_growth` está en
`PROHIBITED_IMPUTATION`. Lo vedado es DEDUCIRLO de otros números reportados,
por ejemplo restando adquisiciones del crecimiento total. Transcribir la cifra
que la propia empresa concilia y publica es otra cosa, y `financial.py` ya la
admitía como "explicitly disclosed assumption" — sólo que nadie se la daba.
"""

from __future__ import annotations

import pytest

from wbj.extract import filing as F


class _Cliente:
    """Un cliente de Anthropic de mentira, con la respuesta preparada."""

    def __init__(self, valor, cita):
        self._v, self._c = valor, cita
        self.messages = self

    def parse(self, **kw):
        # `_parsed` lee `parsed_output`, no `parsed`.
        return type("R", (), {"parsed_output": F._Organic(
            organic_growth_pct=self._v, quote=self._c)})()


_TEXTO = {"text": "Second quarter organic revenue (non-GAAP) grew 6% for the "
                  "quarter, driven by price/mix across all operating segments."}


def test_the_disclosed_figure_is_transcribed():
    v = F.extract_organic_growth(
        _TEXTO, None, _Cliente(0.06, "organic revenue (non-GAAP) grew 6%"))
    assert v == pytest.approx(0.06)


def test_a_quote_that_is_not_in_the_release_is_dropped():
    """La misma verificación que el resto de extracciones: si la frase no está
    en el documento, la cifra no entra. Es lo que separa transcribir de
    inventar."""
    v = F.extract_organic_growth(
        _TEXTO, None, _Cliente(0.06, "organic revenue grew 25% this quarter"))
    assert v is None


def test_an_absurd_figure_is_dropped():
    for absurdo in (-2.0, 5.0):
        assert F.extract_organic_growth(
            _TEXTO, None, _Cliente(absurdo, "organic revenue (non-GAAP) grew 6%")) is None


def test_a_release_that_never_mentions_it_is_not_even_asked():
    """WMT, PLTR y JPM no usan el concepto: cero menciones en sus comunicados.
    Preguntar igualmente gasta dinero para que el modelo diga que no hay nada.
    """
    def _explota(**kw):
        raise AssertionError("no debio preguntar por un comunicado sin anclas")

    cli = _Cliente(0.06, "x")
    cli.parse = _explota
    assert F.extract_organic_growth(
        {"text": "Net sales increased 4.5% and operating income rose 7%."},
        None, cli) is None


def test_an_empty_release_yields_nothing():
    assert F.extract_organic_growth({}, None, _Cliente(0.06, "x")) is None
    assert F.extract_organic_growth({"text": ""}, None, _Cliente(0.06, "x")) is None
