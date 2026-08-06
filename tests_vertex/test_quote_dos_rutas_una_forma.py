"""`/api/quote` tiene dos rutas y una sola forma de respuesta.

La vía rápida —una petición a FMP— se añadió para que pulsar un ticker no
costara 2,4 s bajando un año de histórico. Funcionó, y devolvió las claves con
otros nombres: `price`, `volume`, `day_high` donde la pantalla lee `precio`,
`volumen` y `high`.

El resultado fue que la tarjeta salió con **undefined en todo** salvo el VWAP,
que fue la única clave que coincidió por casualidad. El endpoint respondía 200
y los números eran correctos: sólo estaban etiquetados de otra forma.

Dos rutas que atienden el mismo endpoint tienen que devolver la misma forma.
Si no, la más rápida rompe a quien la consume — y lo hace en silencio, porque
nada falla del lado del servidor.
"""

from __future__ import annotations

import inspect
import re

import vertex_api


#: Lo que la pantalla lee de `/api/quote`. Sacado de la ruta larga, que es la
#: que llevaba funcionando desde el principio.
_CLAVES = ("ticker", "nombre_completo", "precio", "cambio_pct", "volumen",
           "vwap", "high", "low", "after_hours", "logo_url")


def _returns_del_endpoint() -> list[set[str]]:
    """Las claves de cada `return {...}` dentro de `get_quick_quote`."""
    fuente = inspect.getsource(vertex_api.get_quick_quote)
    bloques = re.findall(r"return \{(.*?)\n\s*\}", fuente, re.S)
    return [set(re.findall(r'"([a-z_]+)":', b)) for b in bloques]


def test_both_paths_answer_with_the_same_shape():
    devoluciones = _returns_del_endpoint()
    assert len(devoluciones) >= 2, (
        "se esperaban dos rutas de respuesta: la rapida de FMP y la larga")
    primera, *resto = devoluciones
    for i, otras in enumerate(resto, start=2):
        assert primera == otras, (
            f"la ruta {i} devuelve claves distintas a la primera. "
            f"Sobran: {sorted(otras - primera)}. Faltan: {sorted(primera - otras)}. "
            "La pantalla lee un solo juego de nombres y no sabe cual le toco.")


def test_every_key_the_card_shows_is_answered():
    """El contrato en positivo: si mañana se añade una tercera ruta, esto
    falla en cuanto se deje fuera cualquiera de los campos de la tarjeta."""
    for i, claves in enumerate(_returns_del_endpoint(), start=1):
        faltan = set(_CLAVES) - claves
        assert not faltan, f"la ruta {i} no devuelve {sorted(faltan)}"


def test_no_english_aliases_crept_back_in():
    """Los nombres exactos que se colaron y rompieron la tarjeta.

    Se mira sólo el NIVEL SUPERIOR de cada respuesta. `after_hours` es un
    sub-diccionario y usa `price` y `change_pct` como claves propias, que la
    pantalla lee como `after_hours.price`: ahí el nombre en inglés es correcto
    y prohibirlo habría sido cambiar código que funciona.
    """
    aliases = {"price", "volume", "day_high", "day_low",
               "change_pct", "name", "previous_close"}
    for i, claves in enumerate(_returns_del_endpoint(), start=1):
        colados = claves & aliases
        assert not colados, (
            f"la ruta {i} devuelve {sorted(colados)} en el nivel superior: la "
            "pantalla lee las claves en espanol y estas saldrian como undefined")
