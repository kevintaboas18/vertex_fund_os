"""Los tenedores 13F: FMP primero, EDGAR sólo si FMP no los trae.

`CLAUDE.md` punto 4 exige los fondos reconocidos con posición en la empresa
(13F). Con el plan actual, **las seis rutas** de `institutional-ownership`
de FMP devuelven 402 — verificado una por una contra la clave:

    Restricted Endpoint: This endpoint is not available under your
    current subscription

Victor recibe el mismo 402. Su `packet/builder.py` hace
`fmp.institutional_holders(ticker) or []` y su docstring lo dice:
*"may be plan-restricted -> None"*. Su sistema deja ese requisito de su
propio CLAUDE.md sin cubrir; el nuestro no.

El respaldo por EDGAR se retiró un rato por latencia y se devolvió al medir
bien el costo: el conjunto trimestral es **UN zip por trimestre, cacheado y
compartido por todos los tickers** — 19.1 s la primera vez, 8.3 s el
siguiente ticker, 0.3 s el tercero. Se paga una vez por trimestre, no una
vez por acción.

Estos tests fijan el orden (FMP primero), que el camino caro viva DENTRO de
`if not holders:`, y que un hueco sin sustituto se declare en vez de
anunciarse como tapado.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MOTOR = Path(__file__).resolve().parents[1] / "wbj"


def test_the_expensive_path_runs_only_when_fmp_came_back_empty():
    """El invariante de latencia: EDGAR es RESPALDO, no primera opción.

    La descarga del conjunto trimestral tiene que vivir DENTRO de
    `if not holders:`. Si alguien la saca de ahí, se paga el zip aunque FMP
    haya respondido — que es lo contrario de lo que se pidió.

    Se comprueba por AST y no por texto: el docstring que explica el diseño
    nombra los métodos, y esa mención no es una regresión.
    """
    fuente = (_MOTOR / "report" / "__init__.py").read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    objetivo = next(n for n in ast.walk(arbol)
                    if isinstance(n, ast.FunctionDef) and n.name == "_ownership")

    pesados = {"holders_13f_dataset", "institutional_holders_13f",
               "major_holders_13d_g"}

    # Los `if` cuyo test es exactamente `not holders`.
    guardas = [n for n in ast.walk(objetivo)
               if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp)
               and isinstance(n.test.op, ast.Not)
               and getattr(n.test.operand, "id", None) == "holders"]
    assert guardas, "`_ownership` perdió la guarda `if not holders:`"

    dentro = {n.func.attr for g in guardas for n in ast.walk(g)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr in pesados}
    fuera = {n.func.attr for n in ast.walk(objetivo)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in pesados} - dentro

    assert dentro, "el respaldo de EDGAR desapareció; el 13F queda sin cubrir"
    assert not fuera, (
        f"estos bajan de EDGAR SIEMPRE, no sólo cuando FMP falla: {sorted(fuera)}")


def test_fmp_is_asked_first():
    """FMP va primero — es la vía de Victor y no cuesta latencia. EDGAR sólo
    entra si esa lista vino vacía."""
    fuente = (_MOTOR / "report" / "__init__.py").read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    objetivo = next(n for n in ast.walk(arbol)
                    if isinstance(n, ast.FunctionDef) and n.name == "_ownership")
    llamadas = {n.func.attr for n in ast.walk(objetivo)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "institutional_holders" in llamadas, \
        "`_ownership` dejó de pedirle los tenedores a FMP"


def test_an_absent_list_is_declared_not_hidden():
    """Con el plan actual FMP responde 402 y la lista queda vacía. Eso tiene
    que VERSE: `holders_available` en False es lo que separa "no hay datos"
    de "no hay tenedores", que son cosas distintas."""
    fuente = (_MOTOR / "report" / "__init__.py").read_text(encoding="utf-8")
    assert '"holders_available": bool(holders)' in fuente, (
        "el reporte dejó de declarar si la lista de tenedores existe")


def test_the_gap_is_announced_as_covered_because_it_is():
    """El 402 de FMP está TAPADO por el respaldo de EDGAR, y el reporte
    tiene que decirlo: un hueco tapado y uno abierto piden acciones
    distintas (no hacer nada / subir de plan).

    El emparejamiento importa en los dos sentidos. Anunciar un sustituto que
    no existe hace que el lector deje de buscar el dato; callar uno que sí
    existe le hace pagar un plan que no necesita."""
    from wbj.report import _ENTITLEMENT_SUBSTITUTES

    assert _ENTITLEMENT_SUBSTITUTES.get("institutional_holders"), (
        "el respaldo de EDGAR volvió pero el reporte sigue anunciando el "
        "13F como un hueco sin cubrir")
    assert _ENTITLEMENT_SUBSTITUTES.get("estimates"), (
        "el sustituto de las estimaciones (FMP analyst-estimates) desapareció")


def test_the_edgar_provider_keeps_the_methods_for_whoever_wants_them():
    """Los tres escalones del respaldo siguen disponibles: el conjunto
    trimestral, el 13F por CUSIP y el 13D/G de los >5%. `_ownership` los usa
    de mejor a peor y se queda con el primero que devuelva nombres."""
    from wbj.providers.edgar import EdgarProvider

    for m in ("holders_13f_dataset", "institutional_holders_13f",
              "major_holders_13d_g"):
        assert callable(getattr(EdgarProvider, m, None)), \
            f"se perdió la capacidad `{m}`, no sólo su uso"
