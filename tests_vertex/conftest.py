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


# ═══════════════════════════════════════════════════════════════════════════
#  ¿ESTÁ LA BATERÍA MIDIENDO EL ÁRBOL QUE CREEMOS?
# ═══════════════════════════════════════════════════════════════════════════
#
# Una batería verde sobre el árbol equivocado es peor que una roja: la roja se
# investiga y la verde se cree. Pasó el 22/08/2026 — el contenedor remoto
# rebobinó el disco a un commit de dos días antes, la suite corrió cuarenta y
# cinco minutos sobre ese árbol y dio 1.078 en verde. Ese verde no medía nada
# de lo que se había escrito.
#
# Se avisa al EMPEZAR, no al acabar: enterarse a los cuarenta y cinco minutos
# de que la corrida no valía es tirar los cuarenta y cinco minutos.
#
# Y avisa, no bloquea. Una red caída no es una reversión, y dejar sin batería
# a quien trabaja sin conexión cambiaría un problema por otro. Lo que no puede
# pasar —y es lo único que este bloque garantiza— es que el verde salga en
# SILENCIO cuando el árbol no es el que se cree.


def _rama_actual() -> str:
    import subprocess

    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=10,
                           cwd=str(pathlib.Path(__file__).resolve().parents[1]))
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:                            # noqa: BLE001
        return ""


def _estado_del_arbol() -> tuple[str, str | None]:
    """`(linea, alarma)`. `alarma` solo se llena si el árbol se rebobinó."""
    import subprocess

    raiz = pathlib.Path(__file__).resolve().parents[1]

    def _git(*args, tiempo=10):
        try:
            r = subprocess.run(["git", *args], capture_output=True, text=True,
                               timeout=tiempo, cwd=str(raiz))
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:                        # noqa: BLE001
            return None

    aqui = _git("rev-parse", "HEAD")
    if not aqui:
        return "árbol: no es un repositorio git · no se puede comprobar", None
    rama = _rama_actual() or "?"
    linea = f"árbol: {aqui[:8]} ({rama})"

    # El remoto es lo ÚNICO que sobrevive a una reversión del contenedor: el
    # disco se rebobina entero, `.git` incluido —comprobado con el reflog, que
    # tras una reversión no tenía ni una entrada del día—. Por eso se pregunta
    # ahí y no a un archivo local, que se habría rebobinado con todo lo demás.
    if _git("fetch", "origin", rama, "--quiet", tiempo=45) is None:
        return linea + " · sin remoto: NO se pudo comprobar que esté al día", None
    alla = _git("rev-parse", f"origin/{rama}")
    if not alla:
        return linea + " · la rama no está en el remoto", None
    if alla == aqui:
        return linea + " · al día con el remoto", None
    if _git("merge-base", "--is-ancestor", aqui, alla) is not None:
        detras = _git("rev-list", "--count", f"{aqui}..{alla}") or "?"
        return linea, (
            f"El árbol está {detras} commit(s) POR DETRÁS del remoto "
            f"({alla[:8]}). Se rebobinó: lo que mida esta corrida NO es el "
            "trabajo que se cree.\n"
            f"  Párala y arréglalo:  git fetch origin {rama} && "
            f"git reset --hard origin/{rama}")
    return linea + " · con trabajo sin subir", None


def pytest_report_header(config):               # noqa: ARG001
    """Sobre qué árbol se está midiendo, en la cabecera de pytest."""
    linea, alarma = _estado_del_arbol()
    return linea if not alarma else f"{linea}\n  ¡ATENCIÓN! {alarma}"


def pytest_sessionstart(session):               # noqa: ARG001
    """La alarma, por un canal que NO se puede silenciar.

    `pytest_report_header` se apaga con `--no-header`, y `--no-header` es
    exactamente lo que lleva cada batería de este proyecto. Un aviso que el
    hábito de la casa silencia no avisa de nada.

    Así que la alarma —solo la alarma, no la línea informativa— se escribe
    también en `stderr`, que ninguna bandera de pytest apaga. Se paga una
    llamada al remoto por corrida; enterarse a los cuarenta y cinco minutos de
    que el verde no medía nada cuesta bastante más.
    """
    _, alarma = _estado_del_arbol()
    if alarma:
        sys.stderr.write(
            "\n" + "=" * 72 + "\n"
            "  PARA: ESTA BATERÍA NO ESTÁ MIDIENDO LO QUE CREES\n"
            "  " + alarma.replace("\n", "\n  ") + "\n"
            + "=" * 72 + "\n\n")
        sys.stderr.flush()
