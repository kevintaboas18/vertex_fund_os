"""Un cliente httpx por proceso, como lo tiene Victor.

`engine/scripts/webapp.py` instancia `EdgarProvider(settings, Cache(...))`
UNA vez a nivel de módulo y lo reutiliza durante toda la vida del servidor.
Esta copia hacía lo contrario: `Provider.__init__` llamaba a
`httpx.Client()` cada vez, y `build_providers` se invoca siete veces entre
`deep`, `report` y la capa web.

Medido sobre un `run_report` real: **22 clientes**. Cada uno con su propio
pool, así que ninguna conexión se reutilizaba entre proveedores — handshake
TCP+TLS nuevo en cada llamada — y ninguno se cerraba.
"""

from __future__ import annotations

import httpx

from wbj.providers.base import Provider
from wbj.providers.cache import Cache


class _Settings:
    def __init__(self, tmp_path):
        self.cache_dir = tmp_path
        self.repo_root = tmp_path


def test_providers_without_an_explicit_client_share_one(tmp_path):
    """El caso que producía los 22: nadie pasa cliente."""
    a = Provider(_Settings(tmp_path), Cache(tmp_path))
    b = Provider(_Settings(tmp_path), Cache(tmp_path))
    assert a.client is b.client


def test_an_explicit_client_still_wins(tmp_path):
    """Los tests inyectan `MockTransport`; compartir no puede pisarlo."""
    propio = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={})))
    p = Provider(_Settings(tmp_path), Cache(tmp_path), client=propio)
    compartido = Provider(_Settings(tmp_path), Cache(tmp_path))
    assert p.client is propio
    assert p.client is not compartido.client


def test_build_providers_returns_the_same_set_and_one_client(tmp_path):
    """Memoizado por (cache_dir, repo_root), y los cuatro proveedores
    detrás del mismo pool."""
    from wbj.deep import build_providers

    s = _Settings(tmp_path)
    uno, dos = build_providers(s), build_providers(s)
    assert uno is dos
    clientes = {uno.fmp.client, uno.edgar.client, uno.finnhub.client, uno.fred.client}
    assert len(clientes) == 1


def test_the_shared_client_is_created_lazily():
    """Importar el módulo no puede abrir sockets."""
    import wbj.providers.base as base

    assert callable(base._cliente_compartido)
    assert base._cliente_compartido() is base._cliente_compartido()
