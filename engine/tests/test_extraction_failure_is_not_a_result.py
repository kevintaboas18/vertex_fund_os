"""Un fallo de extracción no es una respuesta, y no se puede cachear como tal.

`_cached_extract` guarda lo que devuelve `extract_disclosures` contra la
accession del filing, y un filing es inmutable: lo que se guarde ahí vale para
siempre. Eso es correcto para una respuesta —«este 10-K no divulga
concentración de clientes» no cambia— y es un desastre para un fallo.

Y el fallo era el caso habitual: la cuenta de Anthropic está sin saldo, así que
`extract_disclosures` devolvía `{}` en cada ticker. Ese `{}` se guardaba, y el
día que Victor recargue el saldo esos tickers seguirían devolviendo vacío,
porque la accession no cambia nunca. Un fallo transitorio convertido en
permanente, en silencio.

Ahora son dos cosas distintas: `{}` y `None` son respuestas
(«no divulga nada») y se guardan; el centinela `FALLO` es «no se pudo
preguntar» y no se guarda.
"""

from __future__ import annotations

from wbj.extract.filing import FALLO
from wbj.overlay.from_packet import _cached_extract


class _Cache:
    def __init__(self):
        self.datos = {}
        self.escrituras = 0

    def get(self, ticker, key):
        return self.datos.get((ticker, key))

    def put(self, ticker, key, payload):
        self.escrituras += 1
        self.datos[(ticker, key)] = payload


_FILING = {"accession": "0001045810-26-000021"}


def test_a_failure_is_not_stored():
    cache = _Cache()
    fuera = _cached_extract(cache, "NVDA", _FILING, "disclosures", lambda: FALLO)
    assert fuera is None
    assert cache.escrituras == 0, (
        "guardar el fallo dejaria a este ticker vacio para siempre: la "
        "accession no cambia nunca")


def test_a_failure_is_retried_and_the_later_answer_wins():
    """El dia que haya saldo, la respuesta tiene que poder entrar."""
    cache = _Cache()
    intentos = []

    def _extraer(resultado):
        intentos.append(resultado)
        return resultado

    assert _cached_extract(cache, "NVDA", _FILING, "disclosures",
                           lambda: _extraer(FALLO)) is None
    bueno = {"largest_customer_share": 0.22}
    assert _cached_extract(cache, "NVDA", _FILING, "disclosures",
                           lambda: _extraer(bueno)) == bueno
    assert len(intentos) == 2, "el segundo intento no llego a ejecutarse"
    assert _cached_extract(cache, "NVDA", _FILING, "disclosures",
                           lambda: _extraer("no deberia correr")) == bueno
    assert len(intentos) == 2, "volvio a preguntar teniendo ya la respuesta"


def test_an_empty_answer_is_a_real_answer_and_is_stored():
    """«Este 10-K no divulga concentración de clientes» es una respuesta, y no
    cambia nunca. Volver a preguntarla cuesta dinero sin cambiar nada."""
    cache = _Cache()
    llamadas = []
    for _ in range(3):
        fuera = _cached_extract(cache, "KO", _FILING, "disclosures",
                                lambda: llamadas.append(1) or {})
        assert fuera == {}
    assert len(llamadas) == 1, "volvio a preguntar una respuesta que ya tenia"
    assert cache.escrituras == 1
