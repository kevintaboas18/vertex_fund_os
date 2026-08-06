"""El navegador tiene que enterarse de que la app cambió.

El HTML es el ESQUELETO: todo el JavaScript va dentro. Se servía **sin una
sola cabecera de caché**, así que el navegador decidía por su cuenta cuánto
guardarlo — y tras un despliegue seguía ejecutando el bundle viejo contra la
API nueva.

Eso fue exactamente lo que le pasó a Victor: la API ya tenía el arreglo, su
navegador seguía con el JavaScript roto, y al analizar NVDA salía
`integrityStripHTML is not defined`. El código estaba bien; lo que llegaba a
su pantalla, no.

`no-cache` NO es "no lo guardes": es "guárdalo, pero pregúntame antes de
usarlo". Con `ETag` derivado del contenido, si nada cambió el servidor
responde 304 y no se transfiere el cuerpo — misma velocidad en el teléfono,
pero un despliegue llega siempre.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import vertex_api

_RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cliente():
    with TestClient(vertex_api.app) as c:
        yield c


def test_the_app_shell_is_always_revalidated(cliente):
    """El invariante: el HTML nunca se sirve como si fuera inmutable."""
    r = cliente.get("/")
    assert r.status_code == 200
    control = r.headers.get("cache-control", "")
    assert "no-cache" in control, (
        f"el esqueleto de la app se sirve sin revalidar: {control!r}. "
        "Un despliegue no llegaría al navegador hasta que el usuario "
        "vaciara la caché a mano.")


def test_an_unchanged_shell_costs_nothing(cliente):
    """`no-cache` sin `ETag` significaría medio mega en CADA carga, y esto se
    usa desde el teléfono. Con `ETag`, la revalidación es gratis."""
    r = cliente.get("/")
    etag = r.headers.get("etag")
    assert etag, "sin ETag, revalidar obliga a retransmitir el HTML entero"

    r2 = cliente.get("/", headers={"If-None-Match": etag})
    assert r2.status_code == 304, (
        f"se esperaba 304 y llegó {r2.status_code}: el navegador vuelve a "
        "descargar el HTML aunque no haya cambiado")
    assert not r2.content, "un 304 no puede llevar cuerpo"


def test_a_new_deploy_reaches_the_browser(cliente):
    """La otra mitad: si el contenido cambió, el ETag NO coincide y el
    navegador se lleva la versión nueva sin intervención del usuario."""
    r = cliente.get("/", headers={"If-None-Match": 'W/"de-otro-despliegue"'})
    assert r.status_code == 200
    assert r.content, "el HTML nuevo llegó vacío"


def test_the_etag_follows_the_file_not_the_clock(cliente):
    """Derivado del CONTENIDO: dos peticiones seguidas dan el mismo ETag, y
    sólo cambia cuando cambia el archivo. Un ETag por marca de tiempo
    invalidaría la caché en cada reinicio del servidor sin motivo."""
    a = cliente.get("/").headers.get("etag")
    b = cliente.get("/").headers.get("etag")
    assert a == b == cliente.get("/").headers.get("etag")

    import hashlib
    html = (_RAIZ / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
    esperado = hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]
    assert esperado in (a or ""), (
        "el ETag no sale del contenido del archivo que se sirve")
