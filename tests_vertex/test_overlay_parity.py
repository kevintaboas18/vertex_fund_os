"""La capa web y el motor tienen que alimentar a los especialistas igual.

`CLAUDE.md`: *"Dos capas, una sola matemática. `engine/wbj/` calcula;
`vertex_api.py` presenta."* No se cumplía, y el fallo no era una regla
distinta: era hambre de datos.

`_engine_scorecard` arrancaba su overlay en `{}` y construía 16 claves a
mano, mientras `build_overlay` —el que usan `run_aggregate`, `wbj report` y
la CLI— construye 42. Entre las 26 que faltaban estaban TODAS las de
`Entradas/<TICKER>.json`: el TAM declarado con su fuente y su tier, la
clasificación de moat, la concentración de clientes. El analista las
escribía en disco y la ruta web ni las miraba.

Medido sobre NVDA: Risk −3.94 y Business −0.92, y el mismo ticker daba dos
números el mismo día (motor 44.70, web 37.0).

Estos tests no leen el código fuente: comparan las claves que cada capa
entrega de verdad.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[1]

# El motor va al path AL IMPORTAR el módulo, no dentro del primer helper:
# los tests hacen `from wbj...` en su propio cuerpo, y para entonces ya tiene
# que estar resuelto.
for _ruta in (_RAIZ / "engine", _RAIZ / "engine" / "tests" / "fixtures" / "packet"):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))


def _settings_y_packet():
    """El packet golden, sin red — el mismo que usa el resto de la suite."""
    from make_packet_fixture import FIXED_NOW, make_default_providers
    from wbj.config import load_settings
    from wbj.packet.builder import build_packet

    return load_settings(), build_packet("NVDA", make_default_providers(FIXED_NOW), FIXED_NOW)


def test_the_web_layer_seeds_its_overlay_from_the_engine():
    """El invariante: la ruta parte del overlay canónico, no de `{}`.

    Se comprueba por AST y no por búsqueda de texto: un comentario que
    mencione `build_overlay` no es lo mismo que llamarlo.
    """
    import ast

    fuente = (_RAIZ / "vertex_api.py").read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    objetivo = next(
        n for n in ast.walk(arbol)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_engine_scorecard")

    llamadas = {
        n.func.id for n in ast.walk(objetivo)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    importados = {
        (a.asname or a.name) for n in ast.walk(objetivo)
        if isinstance(n, ast.ImportFrom) for a in n.names}

    assert "build_overlay" in importados or "_build_overlay" in importados, \
        "la ruta dejó de importar el overlay canónico del motor"
    assert llamadas & {"build_overlay", "_build_overlay"}, \
        "la ruta importa el overlay canónico pero no lo llama"


def test_the_analyst_input_file_reaches_the_web_layer():
    """Lo que de verdad se perdía: `Entradas/<TICKER>.json`.

    El TAM declarado, su fuente y su tier son trabajo humano que el Cerebro
    exige y que ningún proveedor sirve. Si no llegan a los especialistas, se
    investigaron para nada.
    """
    from wbj.overlay.from_packet import build_overlay

    settings, packet = _settings_y_packet()
    overlay = build_overlay(packet, settings)

    entradas = _RAIZ / "Entradas" / "NVDA.json"
    if not entradas.exists():
        pytest.skip("sin Entradas/NVDA.json en este entorno")

    import json
    declarado = {k for k in json.loads(entradas.read_text(encoding="utf-8"))
                 if not k.startswith("_")}
    llegan = declarado & set(overlay)
    assert llegan, (
        f"ninguna de las {len(declarado)} claves declaradas por el analista "
        f"llega al overlay: {sorted(declarado)}")
    # El TAM es el caso testigo: es el que motivó todo el canal `Entradas/`.
    if "tam" in declarado:
        assert overlay.get("tam"), "el TAM declarado no llega a los especialistas"
        assert overlay.get("tam_source_tier") is not None, \
            "el TAM llega sin su tier de fuente, y sin tier se RECHAZA"


def test_the_canonical_overlay_is_richer_than_the_hand_built_one():
    """Guarda contra la regresión concreta: volver a construir el overlay a
    mano en la ruta. Las 16 claves que tenía son un subconjunto pobre."""
    from wbj.overlay.from_packet import build_overlay

    settings, packet = _settings_y_packet()
    overlay = build_overlay(packet, settings)

    # Las que la ruta NO construía y que costaban puntos en Risk y Business.
    perdidas = {"largest_customer_share", "customer_shares", "ppe", "depreciation",
                "lease_commitments", "debt_due", "guidance_history", "judgments"}
    assert perdidas & set(overlay), (
        "el overlay canónico dejó de aportar las claves que la ruta no tenía; "
        f"presentes: {sorted(perdidas & set(overlay))}")


def test_both_layers_read_the_same_wacc():
    """El WACC es el handoff que define la metodología: Valuation lo computa
    y Business/Financial lo consumen. Dos capas con dos WACC distintos serían
    dos análisis distintos con el mismo nombre."""
    from wbj.overlay.from_packet import build_overlay

    settings, packet = _settings_y_packet()
    overlay = build_overlay(packet, settings)
    assert overlay.get("wacc") is not None, "el overlay canónico perdió el WACC"
    assert 0.0 < float(overlay["wacc"]) < 1.0, \
        f"WACC fuera de rango: {overlay['wacc']}"
