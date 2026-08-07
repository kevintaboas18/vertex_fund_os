"""Los archivos de `Entradas/_industrias/` llevan lógica, no sólo cifras.

Tres campos deciden cómo se usa un TAM heredado, y equivocarse en uno no
levanta ningún error: produce un número calculado contra el denominador de
otra empresa, que es peor que un hueco porque el hueco se ve.

  - `_aplica_a` — a qué tickers cubre. Una industria de GICS es más ancha que
    un mercado: `Software - Infrastructure` mete en la misma bolsa a Palantir,
    cuyo negocio entero es software de datos y analítica, y a Microsoft, que
    factura casi el doble del TAM vendiendo sistemas operativos, ofimática,
    nube y videojuegos. Sin la lista, MSFT heredaba ese denominador y puntuaba
    MKT-TAM-001 y MKT-CAGR-004 sobre él.
  - `_segmento_patrones` — cómo reconocer el segmento que compite.
  - `_ingreso_relevante` — o que compite la empresa entera.

Los dos últimos son excluyentes por construcción: o compite un segmento o
compite todo. Declarar ambos deja sin definir cuál gana.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[2] / "Entradas" / "_industrias"
_ARCHIVOS = sorted(_DIR.glob("*.json")) if _DIR.is_dir() else []


@pytest.mark.skipif(not _ARCHIVOS, reason="sin archivos de industria")
@pytest.mark.parametrize("path", _ARCHIVOS, ids=lambda p: p.stem)
def test_every_industry_file_is_coherent(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(d, dict)

    tam = d.get("tam")
    if tam is not None:
        # La regla que ya existía: sin fuente atribuida y tier 1-4, el TAM se
        # descarta al leerlo. Que llegue aquí un archivo así significa que
        # alguien lo escribió a mano sin la atribución.
        assert d.get("tam_source"), f"{path.stem}: tam sin tam_source"
        assert d.get("tam_source_tier") in (1, 2, 3, 4), (
            f"{path.stem}: tam_source_tier fuera de 1-4")
        assert float(tam) > 0

    rel = d.get("_ingreso_relevante")
    if rel is not None:
        assert rel == "total" or (isinstance(rel, list) and rel), (
            f"{path.stem}: _ingreso_relevante es 'total' o una lista de tickers")
        assert tam is not None, (
            f"{path.stem}: declara como se usa un TAM que no tiene")
        assert d.get("_ingreso_relevante_porque"), (
            f"{path.stem}: decir que la empresa compite entera es un juicio "
            "sobre la capa del TAM y tiene que venir razonado")

    aplica = d.get("_aplica_a")
    if aplica is not None:
        assert isinstance(aplica, list) and aplica, (
            f"{path.stem}: _aplica_a vacio deja el archivo sin cubrir a nadie")
        assert all(isinstance(t, str) and t.strip() for t in aplica)


@pytest.mark.skipif(not _ARCHIVOS, reason="sin archivos de industria")
@pytest.mark.parametrize("path", _ARCHIVOS, ids=lambda p: p.stem)
def test_a_segment_and_the_whole_company_are_exclusive(path: Path):
    """O compite un segmento o compite la empresa entera. Con los dos
    declarados no está definido cuál manda, y el numerador es justo lo que no
    puede quedar ambiguo."""
    d = json.loads(path.read_text(encoding="utf-8"))
    if not d.get("tam"):
        return
    patrones = d.get("_segmento_patrones")
    rel = d.get("_ingreso_relevante")
    if rel == "total" and patrones:
        pytest.fail(
            f"{path.stem}: declara patrones de segmento Y que compite entera. "
            "Si la empresa entera compite, los patrones no eligen nada; si "
            "eligen, no compite entera.")


@pytest.mark.skipif(not _ARCHIVOS, reason="sin archivos de industria")
def test_an_unresolved_industry_says_why():
    """Un archivo sin TAM tiene que explicar la ausencia. `oil-gas-integrated`,
    `consumer-electronics` y `reit-retail` están así hoy: el resolutor corrió,
    no encontró fuente que atestiguara la cifra, y dejó dicho qué pasó y qué
    hacer. Un archivo vacío y mudo se lee como un error del sistema."""
    for path in _ARCHIVOS:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("tam") is not None:
            continue
        assert d.get("_sin_tam") or d.get("_que_hacer"), (
            f"{path.stem}: sin TAM y sin explicar por que")
