"""El JavaScript de la plataforma tiene que compilar.

Un solo error de sintaxis en un bloque `<script>` impide que se ejecute el
bloque ENTERO — no la línea rota, todo. Y el navegador no lo grita: la
página carga, se ve bien, y las funciones simplemente no existen.

Fue exactamente lo que pasó. Al quitar los paneles de opciones se borraron
líneas por su CONTENIDO (`getElementById('qtTradePlanBody')`) sin mirar si
además abrían un bloque. Una de ellas era:

    document.getElementById('qtTradePlanBody').innerHTML = `

que abría una plantilla de 30 líneas. Sin ella, el HTML quedó suelto en
medio del código y rompió el bloque de 250.000 caracteres donde vive casi
toda la aplicación.

El efecto en producción: `authSubmit` no existía, así que el botón "Sign In"
no hacía nada — ni entrar ni crear cuenta. Tampoco `renderDashboard` ni
`buildTVChart`. Las de antes y después del bloque roto sí funcionaban, y por
eso la comprobación superficial ("¿cargan las vistas?") pasó: `switchView`
está ANTES del corte.

Los tests que ya existían no podían verlo: comprueban ids del DOM y rutas de
la API, no si el código llega a ejecutarse.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[1]
_HTML = _RAIZ / "vertex_fund_os_platform.html"

#: Bloques `<script>` sin `src`, es decir, el código propio.
_INLINE = re.compile(r"<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>", re.S)


def _bloques() -> list[tuple[int, str]]:
    """(línea en la que empieza, código) por cada script propio."""
    texto = _HTML.read_text(encoding="utf-8")
    return [(texto[: m.start(1)].count("\n") + 1, m.group(1))
            for m in _INLINE.finditer(texto)]


_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node no está instalado")
def test_every_inline_script_parses():
    """`node --check` sobre cada bloque. Es el mismo analizador que usa el
    navegador, así que lo que él acepta, la página lo ejecuta."""
    fallos = []
    for linea, codigo in _bloques():
        if not codigo.strip():
            continue
        tmp = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                          encoding="utf-8")
        tmp.write(codigo)
        tmp.close()
        try:
            r = subprocess.run([_NODE, "--check", tmp.name],
                               capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(tmp.name)
        if r.returncode:
            detalle = (r.stderr or "").splitlines()
            # `node` numera desde el inicio del bloque; se traduce al archivo.
            m = re.search(r"\.js:(\d+)", r.stderr or "")
            real = linea + int(m.group(1)) - 1 if m else linea
            mensaje = next((x for x in detalle if "Error" in x), detalle[:1])
            fallos.append(f"{_HTML.name}:{real} — {mensaje}")
    assert not fallos, "JavaScript que no compila:\n  " + "\n  ".join(fallos)


@pytest.mark.skipif(_NODE is None, reason="node no está instalado")
def test_the_main_block_is_the_one_that_carries_the_app():
    """Guarda contra el propio test: si el patrón deja de encajar con el
    bloque grande, el de arriba pasaría revisando sólo migajas."""
    tamanos = [len(c) for _, c in _bloques()]
    assert tamanos, "no se encontró ningún script propio"
    assert max(tamanos) > 100_000, f"el bloque principal no aparece: {tamanos}"


def test_no_handler_points_at_a_function_that_does_not_exist():
    """Un `onclick="algo()"` cuya función nadie define es un botón muerto:
    el navegador lanza ReferenceError y no pasa nada visible.

    Es la otra mitad del mismo fallo — `authSubmit` seguía en el `onclick`
    del botón mientras su definición no llegaba a ejecutarse.
    """
    texto = _HTML.read_text(encoding="utf-8")
    js = "\n".join(c for _, c in _bloques())

    definidas = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", js))
    definidas |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
                                r"(?:async\s*)?(?:function|\()", js))
    # Nombres del navegador que aparecen en manejadores. No los declara
    # nadie porque los pone el propio entorno.
    definidas |= {"alert", "confirm", "print", "open", "close", "history",
                  "location", "event", "this", "window", "document",
                  "setTimeout", "setInterval", "clearTimeout", "clearInterval",
                  "requestAnimationFrame", "fetch", "Number", "String",
                  "Boolean", "Array", "Object", "JSON", "Math", "Date",
                  "parseInt", "parseFloat", "encodeURIComponent", "isNaN"}

    # Palabras clave que van seguidas de `(` sin ser una llamada.
    claves = {"if", "for", "while", "switch", "catch", "return", "typeof",
              "new", "await", "function", "delete", "void", "in", "of"}

    llamadas = set()
    for m in re.finditer(r'\bon\w+\s*=\s*"([^"]*)"', texto):
        # El `(?<![.\w$])` descarta los MÉTODOS: `event.stopPropagation()`
        # no es una función global que alguien tenga que declarar.
        llamadas |= set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(",
                                   m.group(1)))

    rotos = sorted(f for f in llamadas - definidas - claves if not f.isupper())
    assert not rotos, f"manejadores que apuntan a funciones inexistentes: {rotos}"
