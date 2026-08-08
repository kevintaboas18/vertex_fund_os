"""Un test que se salta a sí mismo no protege nada, y no se nota.

`pytest` cuenta los saltos en una línea de resumen que nadie lee y sale con
código 0. Eso ya costó caro dos veces en este repo:

- `test_the_dimension_lights_once_four_of_five_are_valid` llenaba el hueco del
  panel de pares con una clave del overlay (`peer_revenue_growth`) **que no lee
  nadie** — FIN-GR-003 lee `packet.estimates["peer_panel"]`. Como la dimensión
  nunca llegaba a 4 de 5, el test se saltaba solo. Verde durante meses sin
  comprobar la única cosa que existía para comprobar.
- En la capa web, dos tests esperaban 60 s a que FMP cargara el índice de
  tickers y se saltaban si no llegaba: o sea SIEMPRE en integración continua.
  Al quitarles la red, uno falló a la primera y destapó que el autocompletado
  seguía haciendo dos peticiones HTTP por tecla.

Así que aquí un salto es un FALLO, salvo los que dicen literalmente que falta
una herramienta del entorno. Esa distinción es la que importa: "no tengo `node`
instalado" es una limitación de la máquina, se lee y se decide; "el fixture no
llega a 4 de 5" es un test que dejó de medir, y eso se arregla, no se tolera.

Si algún día hace falta un salto nuevo y legítimo, se añade su motivo a
`ENTORNO` — a mano y con nombre, para que quede escrito quién lo permitió.

Vive fuera de los dos `conftest.py` porque los dos lo usan y `conftest` es un
nombre que pytest ya ocupa: importarlo por su nombre desde el otro conftest
resuelve al propio archivo (import circular). Se carga por ruta.
"""

from __future__ import annotations

import re

#: Motivos que SÍ pueden saltar: falta una herramienta externa, no un dato.
#: Se comparan como subcadena, en minúsculas y sin acentos.
ENTORNO = (
    "node no esta instalado",
    "hace falta node",
    "hace falta git",
    "este node no quita tipos",
)


def _sin_acentos(s: str) -> str:
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        s = s.replace(a, b)
    return s


def es_del_entorno(motivo: str) -> bool:
    m = _sin_acentos((motivo or "").lower())
    return any(p in m for p in ENTORNO)


def motivo_de(report) -> str:
    """El texto del `skip`, sin el prefijo que le pone pytest."""
    if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
        return re.sub(r"^Skipped: ", "", str(report.longrepr[2]))
    return ""


def instala(modulo) -> None:
    """Cuelga los dos hooks del `conftest.py` que llame a esto.

    pytest busca `pytest_runtest_logreport` y `pytest_sessionfinish` como
    atributos del módulo conftest, así que se los ponemos ahí en vez de pedir
    que cada conftest los copie: copiados, uno de los dos se quedaría atrás.
    """
    saltados: list[tuple[str, str]] = []

    def pytest_runtest_logreport(report):
        if report.skipped and report.when in ("setup", "call"):
            saltados.append((report.nodeid, motivo_de(report)))

    def pytest_sessionfinish(session, exitstatus):
        malos = [(n, m) for n, m in saltados if not es_del_entorno(m)]
        if not malos:
            return
        print("\n" + "=" * 70)
        print(f"  {len(malos)} test(s) se saltaron sin ser una falta del ENTORNO.")
        print("  Un test que no corre no protege nada — arreglalo o hazlo fallar.")
        for nodeid, motivo in malos:
            print(f"    · {nodeid}\n        motivo: {motivo or '(sin motivo)'}")
        print("=" * 70)
        session.exitstatus = 1

    modulo.pytest_runtest_logreport = pytest_runtest_logreport
    modulo.pytest_sessionfinish = pytest_sessionfinish
