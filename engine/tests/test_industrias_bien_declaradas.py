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
        # La declaracion puede quedar DORMIDA: dice como se usara el TAM en
        # cuanto haya uno. Es lo que pasa cuando la cifra bajo a
        # `_sugerencia_sin_verificar` por no poderse comprobar contra la
        # pagina de su fuente -- el juicio sobre la capa sigue siendo valido y
        # borrarlo obligaria a rehacerlo cuando el analista confirme el
        # numero. Lo que no vale es declararlo sin TAM y sin nada esperando.
        assert tam is not None or d.get("_sugerencia_sin_verificar"), (
            f"{path.stem}: declara como se usa un TAM que no tiene ni espera")
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
    """Un archivo sin TAM tiene que explicar la ausencia. Un archivo vacío y
    mudo se lee como un error del sistema.

    Hay dos formas de no tener TAM y las dos explican:

    - `_sin_tam` — el resolutor corrió y no encontró fuente que lo publicara.
    - `_sugerencia_sin_verificar` — encontró una cifra y NO se pudo comprobar
      contra la página de su propia fuente. La investigación se guarda entera
      (cifra, fuente, cita y el motivo del rechazo) para que un analista pueda
      confirmarla a mano, pero no puntúa. Es la regla del `judge.py` de Victor
      —"Nunca inventes cifras"— aplicada al TAM: el modelo encuentra la
      fuente, la cifra se lee del documento.
    """
    for path in _ARCHIVOS:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("tam") is not None:
            continue
        assert (d.get("_sin_tam") or d.get("_que_hacer")
                or d.get("_sugerencia_sin_verificar")), (
            f"{path.stem}: sin TAM y sin explicar por que")


# ── La contradiccion no se arregla a mano: se hace imposible ────────────────

def test_al_escribir_se_quitan_los_patrones_si_compite_entera():
    """`drug-manufacturers-general` salio del generador con las dos cosas.

    Los juicios de la capa se CONSERVAN de la resolucion anterior —para que
    revisar la cifra no borre el numerador— mientras `_validar` anade los
    patrones nuevos que devuelve el modelo. Cada mitad es correcta y juntas se
    contradicen, asi que arreglar el archivo a mano no basta: el barrido
    volveria a escribirlo igual dentro de 90 dias.
    """
    from wbj.overlay.tam_mundial import _sin_contradiccion

    salida = _sin_contradiccion({
        "tam": 1_700_000_000_000, "_ingreso_relevante": "total",
        "_ingreso_relevante_porque": "compite entera",
        "_segmento_patrones": ["pharmaceuticals", "drugs"]})
    assert "_segmento_patrones" not in salida
    assert salida["_ingreso_relevante"] == "total", "manda el juicio explicito"


def test_pero_si_NO_compite_entera_los_patrones_se_quedan():
    """La invariante no puede llevarse por delante el caso normal: un archivo
    de segmento necesita sus patrones para elegir el numerador."""
    from wbj.overlay.tam_mundial import _sin_contradiccion

    salida = _sin_contradiccion({
        "tam": 5_000_000_000, "_ingreso_relevante": "segmento",
        "_segmento_patrones": ["gaming", "datacenter"]})
    assert salida["_segmento_patrones"] == ["gaming", "datacenter"]


def test_y_no_muta_lo_que_le_pasan():
    """Escribe una copia. Mutar el diccionario del llamador haria que el
    archivo en memoria y el del disco dejaran de coincidir."""
    from wbj.overlay.tam_mundial import _sin_contradiccion

    entrada = {"_ingreso_relevante": "total", "_segmento_patrones": ["x"]}
    _sin_contradiccion(entrada)
    assert entrada["_segmento_patrones"] == ["x"]
