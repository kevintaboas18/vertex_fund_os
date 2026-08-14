"""El barrido de TAM programado: que no se rompa en silencio a las 2 de la mañana.

Un workflow no se prueba corriéndolo — se prueba comprobando las tres cosas
que lo dejan mudo: que el Python embebido compile, que el terminador del
heredoc quede donde el shell lo espera, y que la rotación cubra todos los
sectores en vez de repetir el mismo.
"""
import ast
import re
from pathlib import Path

import pytest

_YML = Path(__file__).parent.parent / ".github" / "workflows" / "tam-industrias.yml"


@pytest.fixture(scope="module")
def texto() -> str:
    assert _YML.is_file(), "el workflow del TAM tiene que existir"
    return _YML.read_text(encoding="utf-8")


def test_el_python_embebido_compila(texto):
    """Un `SyntaxError` aquí sale a las 06:20 UTC, en un log que nadie lee."""
    m = re.search(r"python - <<'PY'\n(.*?)\n\s*PY\n", texto, re.S)
    assert m, "no se encontró el heredoc de Python"
    cuerpo = "\n".join(l[10:] if l.startswith(" " * 10) else l
                       for l in m.group(1).split("\n"))
    ast.parse(cuerpo)          # levanta SyntaxError si está mal


def test_el_terminador_del_heredoc_queda_en_columna_cero(texto):
    """YAML quita la indentación del bloque `run: |`. Si `PY` no queda
    exactamente a esa altura, el shell nunca cierra el heredoc y el paso
    cuelga hasta el timeout."""
    sangria_run = 10
    terminadores = [l for l in texto.split("\n") if l.strip() == "PY"]
    assert terminadores, "falta el terminador del heredoc"
    for l in terminadores:
        assert len(l) - len(l.lstrip()) == sangria_run


def test_la_rotacion_cubre_los_diez_sectores(texto):
    """Con 10 sectores y módulo 10, cada uno vuelve cada 10 días. Si la lista
    y el módulo se separan, unos sectores no se intentan nunca."""
    m = re.search(r"SECTORES=\((.*?)\)\n", texto, re.S)
    assert m, "no se encontró la lista de sectores"
    sectores = re.findall(r'"([^"]+)"', m.group(1))
    assert len(sectores) == 10, f"son {len(sectores)}, no 10"
    assert len(set(sectores)) == len(sectores), "hay un sector repetido"
    # el módulo tiene que ser el tamaño de la lista, no un número suelto
    assert "% ${#SECTORES[@]}" in texto


def test_pide_las_claves_que_el_barrido_necesita(texto):
    secrets = set(re.findall(r"secrets\.([A-Z_]+)", texto))
    # FMP enumera el universo; Gemini resuelve. Sin una de las dos no hay nada.
    assert {"FMP_API_KEY", "GEMINI_API_KEY"} <= secrets
    assert "if [ -z \"${FMP_API_KEY:-}\" ]" in texto, (
        "tiene que fallar en rojo si falta una clave, no seguir en verde")


def test_no_redespliega_Render_por_un_TAM(texto):
    """Corre a diario y cada push despierta un redespliegue. Un TAM nuevo no
    cambia el código que Render sirve."""
    assert "[skip render]" in texto


def test_solo_commitea_si_hay_algo(texto):
    """Un commit vacío por día ensucia el historial sin decir nada."""
    assert 'git status --porcelain Entradas/_industrias/' in texto
