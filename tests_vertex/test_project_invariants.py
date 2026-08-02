"""Las comprobaciones de la auditoría, convertidas en tests permanentes.

La auditoría del proyecto (AUDITORIA.md §17) corrió como scripts sueltos:
se ejecutaron una vez, dieron sus números y se perdieron. Un invariante que
sólo se comprueba a mano no es un invariante — es una foto. Aquí quedan
fijados, y cada uno recuerda qué defecto real cubre.

Tres de las comprobaciones originales dieron FALSOS POSITIVOS por estar mal
escritas. Cada versión de aquí arregla la trampa concreta:

  - buscar los documentos citados sólo dentro de `Cerebro/`, cuando
    `CLAUDE.md` y `Memoria/MEMORIA.md` viven fuera;
  - exigir que todo id del DOM esté en el HTML, cuando la plataforma crea
    varios en tiempo de ejecución;
  - probar `fcff` con el capex negativo, cuando la fórmula del Cerebro lo
    toma como magnitud positiva.
"""

from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[1]
_API = (_RAIZ / "vertex_api.py").read_text(encoding="utf-8")
_HTML = (_RAIZ / "vertex_fund_os_platform.html").read_text(encoding="utf-8")


# ===========================================================================
# Estructura: todo compila, todo importa
# ===========================================================================


def test_every_python_file_parses():
    """Un archivo que no compila tumba el arranque entero, y en un repo de
    204 archivos es fácil que pase inadvertido en una carpeta poco tocada."""
    malos = []
    for p in _RAIZ.rglob("*.py"):
        if "__pycache__" in p.parts or ".git" in p.parts:
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            malos.append(f"{p.relative_to(_RAIZ)}:{e.lineno} {e.msg}")
    assert not malos, "archivos que no compilan: " + "; ".join(malos)


def test_every_engine_module_imports():
    """Un import roto sólo aparece cuando alguien llama a esa ruta. El
    motor entero tiene que cargar."""
    import importlib
    import pkgutil
    import sys

    engine = _RAIZ / "engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    import wbj

    fallos = []
    for m in pkgutil.walk_packages(wbj.__path__, "wbj."):
        try:
            importlib.import_module(m.name)
        except Exception as e:                       # noqa: BLE001 - se reporta
            fallos.append(f"{m.name}: {type(e).__name__}: {e}")
    assert not fallos, "módulos que no importan: " + "; ".join(fallos)


# ===========================================================================
# Conexiones: la UI y la API tienen que hablar el mismo idioma
# ===========================================================================


def _rutas_declaradas() -> set[str]:
    return set(re.findall(r'@app\.(?:get|post|put|delete)\("(/api/[^"]+)"', _API))


def test_the_ui_never_calls_an_endpoint_the_api_does_not_expose():
    """Un `fetch` a una ruta inexistente da 404 y la pantalla se queda
    vacía sin decir por qué. Con 53 llamadas en la plataforma, esto no se
    revisa a ojo."""
    llamadas = set(re.findall(r"API_BASE\}(/api/[a-z0-9/_-]+)", _HTML))
    huerfanas = sorted(llamadas - _rutas_declaradas())
    assert not huerfanas, f"la UI llama rutas que no existen: {huerfanas}"


def test_the_ui_calls_something():
    """Guarda contra el propio test: si el patrón deja de encajar, el
    de arriba pasaría vacío para siempre."""
    assert len(re.findall(r"API_BASE\}(/api/[a-z0-9/_-]+)", _HTML)) > 30


def test_every_referenced_cerebro_document_exists():
    """Un módulo que cita `SCORING.md` para justificar una regla y apunta a
    un archivo inexistente deja la regla sin respaldo.

    Busca en TODO el repo, no sólo en `Cerebro/`: `CLAUDE.md` está en la
    raíz y `MEMORIA.md` en `Memoria/`. La primera versión de esta
    comprobación sólo miraba `Cerebro/` y los reportó como inexistentes.

    El `(?<![\\w-])` de delante NO es cosmético. Con `\\b` a secas, el guion
    cuenta como límite de palabra y `SCOREDCARD/Contexto-IV.md` —una cita real
    de `ivcontext.py`— se leía como un documento llamado `IV.md`, que no existe
    y nadie citó nunca. Un falso positivo en esta comprobación es caro: la
    manda a callar cambiando la cita en vez de creando el documento."""
    existentes = {p.name for p in _RAIZ.rglob("*.md")}
    citados: set[str] = set()
    for p in [*(_RAIZ / "engine" / "wbj").rglob("*.py"), _RAIZ / "vertex_api.py"]:
        citados |= set(re.findall(r"(?<![\w-])([A-Z][A-Z_0-9]+\.md)\b",
                                  p.read_text(encoding="utf-8")))
    faltan = sorted(citados - existentes)
    assert not faltan, f"documentos citados que no existen: {faltan}"


def test_every_dom_id_the_script_touches_is_reachable():
    """`getElementById` sobre un id que no existe devuelve `null`, y la
    siguiente línea revienta con "cannot read properties of null".

    Un id vale si el HTML lo declara O si el script lo crea. La plataforma
    hace lo segundo con los contenedores de toast, modales y el gate — la
    primera versión de esta comprobación los reportó como rotos."""
    tocados = set(re.findall(r"getElementById\(['\"]([A-Za-z0-9_-]+)['\"]\)", _HTML))
    declarados = set(re.findall(r"\bid=['\"]([A-Za-z0-9_-]+)['\"]", _HTML))
    creados = set(re.findall(r"\.id\s*=\s*['\"]([A-Za-z0-9_-]+)['\"]", _HTML))
    # Un uso guardado (`if (el)`, `el || crear()`) tampoco puede reventar.
    guardados = set(re.findall(
        r"(?:if\s*\(\s*document\.getElementById\(|getElementById\(['\"][^'\"]+['\"]\)\s*\|\||"
        r"!\s*document\.getElementById\()['\"]?([A-Za-z0-9_-]+)?", _HTML))
    rotos = sorted(tocados - declarados - creados - guardados)
    assert not rotos, f"ids que el script toca y nadie define ni crea: {rotos}"


# ===========================================================================
# Datos: ningún número corrupto puede llegar al cliente
# ===========================================================================


def _reporte_offline():
    """Los seis especialistas sobre el packet golden, sin red."""
    import sys

    engine = _RAIZ / "engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    fixtures = engine / "tests" / "fixtures" / "packet"
    if str(fixtures) not in sys.path:
        sys.path.insert(0, str(fixtures))
    from make_packet_fixture import FIXED_NOW, make_default_providers
    from wbj.packet.builder import build_packet

    packet = build_packet("NVDA", make_default_providers(FIXED_NOW), FIXED_NOW)
    from wbj.specialists import business, financial, risk, technical, valuation

    overlay = {"wacc": 0.09}
    return {
        "business": business.run(packet, overlay),
        "financial": financial.run(packet, overlay),
        "technical": technical.run(packet, overlay),
        "risk": risk.run(packet, overlay),
        "valuation": valuation.run(packet, overlay),
    }


def test_no_specialist_publishes_nan_or_infinity():
    """`float('nan')` sobrevive a `json.dumps` por defecto y llega al
    navegador como el literal `NaN`, que **no es JSON válido**: el
    `JSON.parse` del cliente lanza y la pantalla queda en blanco. Un
    `inf` hace lo mismo."""
    malos: list[str] = []

    def revisar(o, ruta):
        if isinstance(o, float):
            if math.isnan(o) or math.isinf(o):
                malos.append(f"{ruta} = {o}")
        elif isinstance(o, dict):
            for k, v in o.items():
                revisar(v, f"{ruta}.{k}")
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                revisar(v, f"{ruta}[{i}]")

    for nombre, salida in _reporte_offline().items():
        revisar(salida.model_dump(mode="json"), nombre)
    assert not malos, "números no finitos publicados: " + "; ".join(malos[:8])


def test_every_specialist_output_survives_strict_json():
    """Lo que exige un cliente HTTP de verdad. `allow_nan=False` es la
    misma regla que aplica un `JSON.parse`."""
    for nombre, salida in _reporte_offline().items():
        json.dumps(salida.model_dump(mode="json"), allow_nan=False)


def test_awarded_points_never_leave_their_range():
    """Un score fuera de `[0, max]` rompe la suma de las seis categorías
    contra el `raw_total`, que es la cifra sobre la que actúan los gates."""
    for nombre, salida in _reporte_offline().items():
        cat = salida.category
        if cat.awarded_points is not None:
            assert 0.0 <= cat.awarded_points <= cat.max_points, nombre
        if cat.confidence is not None:
            assert 0.0 <= cat.confidence <= 100.0, nombre
        if salida.coverage is not None:
            assert 0.0 <= salida.coverage <= 1.0, nombre


# ===========================================================================
# Cálculo: la convención de signo que me hizo tropezar
# ===========================================================================


def test_fcff_takes_capex_as_a_positive_magnitude():
    """`FORMULAS.md` (VAL-FCFF-005): `EBIT*(1-tax) + D&A - Capex - ΔNWC`.
    El capex entra **restando**, así que se pasa como magnitud POSITIVA.

    Los estados de FMP lo traen negativo, y por eso los llamadores del
    especialista hacen `abs()`. Probar la función con el signo del estado
    financiero da un FCFF inflado — y hace ver un fallo donde no lo hay.
    """
    import sys

    engine = _RAIZ / "engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    from wbj.engines import valuation_engine as ve

    ebit, tasa, dna, capex, dnwc = 1000.0, 0.21, 100.0, 200.0, 50.0
    esperado = ebit * (1 - tasa) + dna - capex - dnwc
    assert ve.fcff(ebit, tasa, dna, capex, dnwc).value == pytest.approx(esperado)

    # Con el signo del estado financiero el capex SUMA, que es el error.
    invertido = ve.fcff(ebit, tasa, dna, -capex, dnwc).value
    assert invertido == pytest.approx(esperado + 2 * capex)
    assert invertido > esperado


def test_the_specialist_normalises_the_sign_before_calling():
    """La defensa real: da igual cómo venga del proveedor."""
    fuente = (_RAIZ / "engine" / "wbj" / "specialists" / "valuation.py").read_text(
        encoding="utf-8")
    assert "abs(_num(latest, \"capex\")" in fuente or "abs(float(capex_latest))" in fuente
