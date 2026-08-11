"""La suite web no puede depender de un archivo que no está en git.

`vertex_api.py` lee `vertex.env` al importarse y guarda el token en la
constante de módulo `VERTEX_API_TOKEN` (línea ~102). Con token configurado la
API exige autenticación, y los tests que llaman a endpoints protegidos sin
cabecera reciben 401.

`vertex.env` está en `.gitignore`. O sea que la suite pasaba en CI y en
cualquier clon limpio —donde el archivo no existe y la autenticación queda
desactivada— y fallaba en la máquina de quien sí tiene sus claves puestas.
Medido el 2026-08-06 sobre `origin/main`, sin ningún cambio propio:

    sin vertex.env     1 fallo,  196 pasan
    con vertex.env    64 fallos, 133 pasan

Un test que cambia de resultado según un archivo ignorado no está midiendo el
código: está midiendo la máquina. Y el modo en que fallaba es el peor de los
dos, porque el desarrollador que TIENE credenciales es justo el que va a
ejecutar la suite antes de subir.

Se neutraliza sobre el módulo ya importado y no sobre el entorno, porque el
entorno lo repuebla `load_dotenv` en el import y el orden dejaría de estar
garantizado. Los tests que quieran probar la autenticación pueden fijar
`vertex_api.VERTEX_API_TOKEN` ellos mismos: esto sólo pone el punto de partida.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _sin_token_de_api():
    """Cada test arranca con la API en modo local, como en un clon limpio."""
    import vertex_api

    previo = vertex_api.VERTEX_API_TOKEN
    vertex_api.VERTEX_API_TOKEN = ""
    try:
        yield
    finally:
        vertex_api.VERTEX_API_TOKEN = previo


# ── Un salto es un fallo, salvo que falte una herramienta del entorno ────────
#
# La regla, y los dos casos que costó, están en `engine/tests/_saltos.py`. Aquí
# se paga en los tests del buscador: esperaban 60 s a que FMP cargara el índice
# y se saltaban si no llegaba —o sea SIEMPRE sin red—, y al quitarles la
# dependencia uno falló a la primera y destapó que el autocompletado seguía
# haciendo dos peticiones HTTP por tecla.
#
# Se carga por RUTA y no por nombre: `conftest` es un nombre que pytest ya
# ocupa, e importarlo desde el otro conftest resuelve al propio archivo.

import importlib.util  # noqa: E402
import pathlib  # noqa: E402

_RUTA = pathlib.Path(__file__).resolve().parent.parent / "engine" / "tests" / "_saltos.py"
_spec = importlib.util.spec_from_file_location("wbj_tests_saltos", _RUTA)
_saltos = importlib.util.module_from_spec(_spec)
sys.modules["wbj_tests_saltos"] = _saltos
_spec.loader.exec_module(_saltos)

_saltos.instala(sys.modules[__name__])
