"""La forma de la respuesta de Massive, en el bucle que llama a `compute.to_row`.

`fetch_option_chain` es donde el port de `compute.ts` se encuentra con la red.
Todo lo que llegue mal formado tiene que salir como `MassiveError` —que
`_tito_memory` sabe reportar con su motivo— y nunca como un `AttributeError` o
un `TypeError` crudo desde dentro del cliente.
"""

from __future__ import annotations

import pytest

import wbj.tito.massive as MASS
from wbj.tito.massive import MassiveError, fetch_option_chain


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "x" * 32)


def _responde(monkeypatch, payload):
    monkeypatch.setattr(MASS, "_get", lambda *a, **k: payload)


@pytest.mark.parametrize("payload,que", [
    ([{"details": {}}], "un array suelto en vez de un objeto"),
    (None, "null"),
    ("texto", "una cadena"),
    ({"results": "texto"}, "`results` como cadena"),
    ({"results": {"a": 1}}, "`results` como objeto"),
    ({"results": 5}, "`results` como número"),
])
def test_una_respuesta_con_forma_rara_da_massive_error(monkeypatch, payload, que):
    _responde(monkeypatch, payload)
    with pytest.raises(MassiveError):
        fetch_option_chain("DEMO")


def test_results_ausente_es_una_cadena_vacia_no_un_error(monkeypatch):
    # No es una respuesta mal formada: es un ticker sin contratos.
    _responde(monkeypatch, {"status": "OK"})
    assert fetch_option_chain("DEMO").rows == []


def test_contratos_no_dict_mezclados_no_tumban_la_pagina(monkeypatch):
    _responde(monkeypatch, {"results": [
        {"details": {"contract_type": "call", "strike_price": 100.0,
                     "expiration_date": "2026-09-18"},
         "open_interest": 500, "underlying_asset": {"price": 99.0}},
        "no soy un contrato", None, 42, ["x"],
        {"details": {"contract_type": "PUT", "strike_price": 95.0,
                     "expiration_date": "2026-09-18T00:00:00Z"},
         "open_interest": 700},
    ]})
    r = fetch_option_chain("DEMO")
    assert len(r.rows) == 2
    assert r.underlying_price == 99.0
    assert [x.open_interest for x in r.rows] == [700, 500]   # ordenadas por OI
    assert r.expiration_count == 1                       # el 'T00:00:00Z' agrupa igual
    assert [x.contract_type for x in r.rows] == ["put", "call"]   # 'PUT' sigue siendo put
