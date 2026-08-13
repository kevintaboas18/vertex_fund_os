"""Un sello de "sin TAM" no puede firmarse con una tirada que la cuota corto.

Medido sobre los 43 archivos de industria: 27 llevaban un motivo MIXTO
--cuota mas alguna respuesta-- y el numero de respuestas reales era 1, 2 o 3
de los 8 intentos posibles (`INTENTOS`=4 por dos proveedores).
`aerospace-defense` quedaba marcada "sin fuente" hasta noviembre con UNA
respuesta de ocho; las otras siete murieron en 429.

Lo que eso contradice es el razonamiento del propio modulo:

    "Un `null` no era prueba. El modelo lleva busqueda web y no es
     determinista: preguntando cuatro veces por Consumer Electronics --una
     industria que llevaba semanas marcada 'ninguna asociacion publica este
     mercado'-- el segundo intento devolvio $783.000M de Gartner y valido.
     Los otros tres fueron `null`."

Si cuatro intentos existen porque uno no basta, un sello de 90 dias no puede
firmarse con uno.
"""
import re

import pytest

from wbj.overlay.tam_mundial import INTENTOS, _es_falta_de_cuota


def _respuestas(motivo: str) -> int:
    m = re.search(r"(\d+) respuestas sin cifra", motivo)
    return int(m.group(1)) if m else 0


_CORTADA_POR_CUOTA = (
    "openai: RateLimitError Error code: 429 - no credits remaining; "
    "gemini: ClientError 429 RESOURCE_EXHAUSTED; 1 respuestas sin cifra atribuible"
)
_INTENTO_SERIO = (
    "gemini: ClientError 429 RESOURCE_EXHAUSTED; "
    "4 respuestas sin cifra atribuible"
)
_SIN_CUOTA = "ninguna asociacion publica este mercado; 4 respuestas sin cifra atribuible"


def test_una_tirada_cortada_por_cuota_no_sella():
    """1 respuesta de 8 no prueba que la industria no tenga fuente."""
    assert _es_falta_de_cuota(_CORTADA_POR_CUOTA)
    assert _respuestas(_CORTADA_POR_CUOTA) < INTENTOS


def test_una_tirada_completa_SI_sella():
    """Si corrieron los intentos que el modulo considera suficientes, el
    veredicto vale -- si no, ninguna industria se cerraria nunca."""
    assert _respuestas(_INTENTO_SERIO) >= INTENTOS


def test_un_fallo_que_no_es_de_cuota_sella_igual():
    """"Ninguna asociacion publica este mercado" es un hallazgo, no un
    accidente: no depende del umbral."""
    assert not _es_falta_de_cuota(_SIN_CUOTA)


def test_el_umbral_es_el_mismo_numero_de_intentos_del_modulo():
    """Que no se separen: si `INTENTOS` sube, el listón sube con él."""
    import inspect

    from wbj.overlay import tam_mundial

    src = inspect.getsource(tam_mundial)
    assert "_n_respuestas >= INTENTOS" in src, (
        "el listón tiene que colgar de INTENTOS, no de un número suelto")
