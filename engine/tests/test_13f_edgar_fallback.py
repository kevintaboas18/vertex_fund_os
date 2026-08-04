"""Los tenedores 13F salen de FMP, y si el plan no los cubre, no salen.

Es exactamente lo que hace Victor. Su `packet/builder.py` (líneas 308-309):

    insider_trades = fmp.insider_trades(ticker) or []
    institutional_holders = fmp.institutional_holders(ticker) or []

y el docstring de su método lo dice sin rodeos: *"13F institutional holders
(may be plan-restricted -> None)"*. Cuenta con el 402 y devuelve vacío.

Aquí había un respaldo de tres escalones sobre EDGAR cuyo primer paso
descargaba el conjunto trimestral 13F de la SEC. **Encontraba tenedores
reales donde FMP da 402** — pero costaba **19 segundos en cada ticker
nuevo**, casi una cuarta parte de un análisis en frío, y Victor no lo hace.

Este archivo cubría aquel respaldo. Ahora cubre lo contrario: que el hueco
se declare en vez de taparse con una fuente que ya no existe. `CLAUDE.md`
pide los inversionistas 13F y ese requisito queda SIN CUBRIR con el plan
actual de FMP — en este repo y en el de Victor por igual. Se resuelve
subiendo de plan, no con más código.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MOTOR = Path(__file__).resolve().parents[1] / "wbj"


def test_the_report_does_not_download_the_quarterly_dataset():
    """El invariante de latencia: nadie vuelve a bajar el zip trimestral.

    Se comprueba por AST y no por texto: el docstring que explica por qué se
    retiró nombra el método, y esa mención no es una regresión.
    """
    fuente = (_MOTOR / "report" / "__init__.py").read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    pesados = {"holders_13f_dataset", "institutional_holders_13f",
               "major_holders_13d_g"}
    usados = {n.func.attr for n in ast.walk(arbol)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr in pesados}
    assert not usados, (
        f"el reporte volvió a llamar a EDGAR para los 13F: {sorted(usados)}. "
        "Son 19 s por ticker nuevo y Victor no lo hace.")


def test_the_holders_come_from_fmp_like_victor():
    """La ÚNICA fuente, igual que en su `packet/builder.py`."""
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


def test_no_substitute_is_announced_for_a_gap_that_has_none():
    """Anunciar un sustituto retirado es peor que no anunciar nada: el
    lector deja de buscar el dato."""
    from wbj.report import _ENTITLEMENT_SUBSTITUTES

    assert "institutional_holders" not in _ENTITLEMENT_SUBSTITUTES, (
        "se sigue anunciando el conjunto 13F de la SEC como sustituto, y ya "
        "no se descarga")
    assert _ENTITLEMENT_SUBSTITUTES.get("estimates"), (
        "el sustituto que SÍ existe (FMP analyst-estimates) desapareció")


def test_the_edgar_provider_keeps_the_methods_for_whoever_wants_them():
    """Retirar la LLAMADA no es borrar la capacidad. Los métodos siguen en
    el proveedor: si algún día hay presupuesto de latencia (un trabajo
    nocturno, por ejemplo), están listos y probados."""
    from wbj.providers.edgar import EdgarProvider

    for m in ("holders_13f_dataset", "institutional_holders_13f",
              "major_holders_13d_g"):
        assert callable(getattr(EdgarProvider, m, None)), \
            f"se perdió la capacidad `{m}`, no sólo su uso"
