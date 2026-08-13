"""Campo por campo: qué DATO pinta cada componente suyo, y si el panel lo pinta.

Los diferenciales miden que la cuenta dé lo mismo. La auditoría mide que la
función exista y que sus umbrales estén. Nada medía lo de en medio: que el
número calculado LLEGUE A LA PANTALLA. Un campo que el motor calcula bien y el
panel nunca pinta no lo ve ningún check — se ve mirando el panel y echándolo de
menos, que es justo lo que pasó.

Se leen los campos que su `.tsx` saca de sus datos (`d.foo`, `x.barBaz`) y los
que saca la función del panel que lo sustituye, y se restan. Los nombres se
normalizan —él escribe `premiumTotal`, el motor sirve `premium_total`— porque
si no la mitad del listado serían falsos positivos de estilo.

    python engine/scripts/_diffcampos_extrae.py            # solo lo que falta
    python engine/scripts/_diffcampos_extrae.py --todo     # con lo que sí está

No decide nada por su cuenta: escupe el listado para mirarlo a mano. Un campo
puede faltar por una razón buena (su crosshair, su wordmark) y esa razón hay
que escribirla, no adivinarla.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
#: El panel entero, normalizado, para preguntar «¿está este campo?».
PANEL = re.sub(r"[^a-z0-9]", "", HTML.lower())

#: Nombres que no son datos: métodos, cosas del framework, del DOM.
RUIDO = {
    "map", "filter", "length", "slice", "join", "push", "sort", "reduce",
    "toFixed", "forEach", "includes", "split", "trim", "replace", "indexOf",
    "find", "some", "every", "concat", "keys", "values", "entries", "flat",
    "toUpperCase", "toLowerCase", "startsWith", "endsWith", "padStart",
    "toString", "charAt", "substring", "repeat", "match", "test", "then",
    "catch", "finally", "json", "ok", "status", "current", "target", "value",
    "props", "children", "style", "className", "key", "ref", "id", "type",
    "log", "warn", "error", "abs", "max", "min", "round", "floor", "ceil",
    "pow", "sqrt", "random", "now", "getTime", "toISOString", "innerHTML",
    "innerText", "textContent", "classList", "add", "remove", "contains",
    "getElementById", "querySelector", "querySelectorAll", "appendChild",
    "setAttribute", "getAttribute", "addEventListener", "preventDefault",
    "stopPropagation", "toLocaleString", "toLocaleDateString", "sign",
    "toSorted", "at", "flatMap", "from", "isArray", "parse", "stringify",
    "padEnd", "trimStart", "trimEnd", "localeCompare", "reverse", "indexOf",
}

CAMPO = re.compile(r"\.([a-z][A-Za-z0-9_]{2,})\b")


def norma(s: str) -> str:
    """`premiumTotal`, `premium_total` y `PREMIUM_TOTAL` son el mismo campo."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def campos(texto: str) -> set[str]:
    return {c for c in CAMPO.findall(texto) if c not in RUIDO}


def cuerpo_fn(nombre: str) -> str:
    """El cuerpo de una función del panel, hasta la siguiente declaración."""
    for arranque in (f"function {nombre}(", f"const {nombre} = ",
                     f"{nombre}: function("):
        i = HTML.find(arranque)
        if i >= 0:
            break
    else:
        return ""
    j = HTML.find("\nfunction ", i + 10)
    return HTML[i:j if j > 0 else i + 12000]


def _registro_de_componentes() -> dict[str, tuple[str, str]]:
    """`COMPONENTES_SUYOS` de la auditoría, sin correr la auditoría."""
    import ast                                                 # noqa: PLC0415

    fuente = (ROOT / "engine" / "scripts" / "auditar_tito.py").read_text(
        encoding="utf-8")
    for nodo in ast.walk(ast.parse(fuente)):
        if (isinstance(nodo, ast.Assign)
                and any(getattr(t, "id", "") == "COMPONENTES_SUYOS"
                        for t in nodo.targets)):
            return ast.literal_eval(nodo.value)
    raise SystemExit("la auditoría ya no declara COMPONENTES_SUYOS")


def main() -> int:
    tito = os.environ.get("TITO_ROOT", "")
    if not tito or not (Path(tito) / "web" / "app" / "components").is_dir():
        print("  · saltado: hace falta TITO_ROOT con su clon")
        return 0
    comp_dir = Path(tito) / "web" / "app" / "components"

    # El registro se LEE, no se importa: `auditar_tito` corre la auditoría
    # entera al importarse y aquí solo hace falta su tabla de componentes.
    registro = _registro_de_componentes()

    todo = "--todo" in sys.argv
    fn_citada = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
    faltan_total = 0
    for comp, (tipo, nota) in sorted(registro.items()):
        if tipo != "panel":
            continue
        f = comp_dir / f"{comp}.tsx"
        if not f.is_file():
            f = Path(tito) / "web" / "app" / f"{comp}.tsx"
        if not f.is_file():
            continue
        suyos = campos(f.read_text(encoding="utf-8"))
        fns = sorted(set(fn_citada.findall(nota)))
        mio = "".join(cuerpo_fn(x) for x in fns)
        if not mio.strip():
            print(f"  {comp}: sin función del panel localizada ({fns})")
            continue
        # Se busca en el panel ENTERO, no solo en la función que le toca. Un
        # campo puede pintarse en otra función —él parte en dos cards lo que
        # aquí es una— y eso no es que falte, es que está en otro sitio. La
        # pregunta que importa es si el número llega a la pantalla o no llega.
        faltan = sorted(c for c in suyos if norma(c) not in PANEL)
        if faltan:
            faltan_total += len(faltan)
            print(f"\n── {comp}  ({', '.join(fns)})")
            for c in faltan:
                print(f"     falta  {c}")
        elif todo:
            print(f"\n── {comp}: los {len(suyos)} campos están")
    print(f"\n  {faltan_total} campo(s) suyos sin rastro en el panel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
