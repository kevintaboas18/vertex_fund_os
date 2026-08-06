"""El arranque de la aplicación tiene que ejecutarse de verdad.

`@app.on_event("startup")` está obsoleto en FastAPI y va a desaparecer. Al
migrarlo a `lifespan` el riesgo no es un error ruidoso: es que el
planificador deje de arrancar EN SILENCIO — la aplicación levanta, responde
a todo, y simplemente nunca recoge snapshots. Nadie se entera hasta que
faltan días de historial.

Estos tests cubren esa migración por comportamiento observable.
"""

from __future__ import annotations

import warnings

from fastapi.testclient import TestClient

import vertex_api


def test_the_startup_hook_actually_runs():
    """El invariante: levantar la aplicación ejecuta el arranque, una vez."""
    llamadas: list[int] = []
    original = vertex_api._vertex_startup

    def espia() -> None:
        llamadas.append(1)
        original()

    vertex_api._vertex_startup = espia
    try:
        with TestClient(vertex_api.app):
            pass
    finally:
        vertex_api._vertex_startup = original

    assert llamadas == [1], f"el arranque corrió {len(llamadas)} veces, no una"


def test_the_app_serves_after_startup():
    """Lo que le importa al usuario: que la aplicación quede sirviendo.

    Antes esto comprobaba que el planificador quedara vivo. El planificador
    existía para capturar un snapshot diario de señales de Quant Data, y esa
    capa salió del proyecto — un hilo despertándose cada noche para no hacer
    nada no es una garantía que valga la pena tener."""
    with TestClient(vertex_api.app) as c:
        r = c.get("/api/auth/status")
    assert r.status_code == 200


def test_the_health_strip_only_lists_real_sources():
    """`/api/data-health` anunciaba Quant Data como fuente CRÍTICA y
    yfinance como airbag de precio. Las dos salieron: decir que el sistema
    se apoya en algo que ya no existe es peor que no decir nada."""
    # `/api/data-health` NO es pública: exige el token cuando hay uno
    # configurado. Y `vertex_api` carga `vertex.env` al importarse, así que
    # basta con que el desarrollador tenga su `VERTEX_API_TOKEN` puesto —
    # para desplegar en Render, por ejemplo— para que este test recibiera un
    # 401 y fallara con `KeyError: 'sources'`. Se autentica como lo haría
    # cualquier cliente en vez de asumir un entorno sin token.
    cabeceras = ({"x-vertex-token": vertex_api.VERTEX_API_TOKEN}
                 if vertex_api.VERTEX_API_TOKEN else {})
    with TestClient(vertex_api.app) as c:
        r = c.get("/api/data-health", headers=cabeceras)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:120]}"
    d = r.json()
    claves = {s["key"] for s in d["sources"]}
    assert "quantdata" not in claves and "yfinance" not in claves
    assert {"fmp", "edgar", "fred"} <= claves, claves


def test_the_app_declares_no_deprecated_event_handlers():
    """`on_event` volvería a colar el aviso y a dejar la app atada a una API
    que FastAPI ya anunció que retira."""
    import re
    from pathlib import Path

    fuente = Path(vertex_api.__file__).read_text(encoding="utf-8")
    # En columna 0: un decorador de verdad. El docstring del `lifespan`
    # nombra `on_event` para explicar de qué se migró, y esa mención
    # indentada no es una regresión.
    usos = re.findall(r"(?m)^@app\.on_event", fuente)
    assert not usos, f"volvieron {len(usos)} manejadores `on_event` obsoletos"
    assert "lifespan=_vertex_lifespan" in fuente, "la app dejó de declarar su `lifespan`"


def test_importing_the_api_emits_no_deprecation_of_our_own():
    """Un aviso propio es deuda que revienta en una actualización futura.
    Los de las librerías de terceros no son nuestros para arreglar."""
    import importlib

    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        importlib.reload(vertex_api)

    nuestros = [
        f"{w.filename}:{w.lineno}"
        for w in capturados
        if issubclass(w.category, DeprecationWarning)
        and ("vertex_api" in str(w.filename) or "fastapi" in str(w.filename))
    ]
    assert not nuestros, f"avisos de obsolescencia propios: {nuestros}"
