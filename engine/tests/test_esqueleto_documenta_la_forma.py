"""`sam_inputs: null` no le dice a nadie qué escribir dentro.

El propio `market.py` ya reconocía el problema en un comentario: *"the inner
keys were documented nowhere, so getting them wrong was the default outcome"*.
Y el esqueleto de `Entradas/` seguía emitiendo el `null` pelado, así que la
única forma de acertar era leer el código fuente.

Verificado en carne propia durante la auditoría: escribir
`eligible_geography_share` en vez de `geography_share` deja MKT-SAM-002 en
`MISSING` sin más aviso que el de la propia métrica, y el mismo error pasó dos
veces seguidas. Las claves de Market son la última milla entre el 0,59 de hoy y
el 1,000 que se midió como techo — no puede haber adivinanza en ellas.

Lo que estos tests fijan es que la forma emitida **sale del código que la
consume**, no de una copia tecleada en el esqueleto. Una copia se desincroniza
en el primer renombrado y entonces el esqueleto miente, que es peor que el
`null`: el `null` al menos no promete nada.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from wbj.entradas import render_skeleton


@pytest.fixture(scope="module")
def esqueleto() -> dict:
    return json.loads(render_skeleton("PRUEBA"))


@pytest.fixture(scope="module")
def notas(esqueleto) -> str:
    """Las lineas `_NN` juntas. Ahi vive la forma, y no en el valor: un
    diccionario de nulos sobrevive a `_manual_overlay` y pasa el
    `all(k in sam_inputs ...)` de market.py, que entonces llamaria a
    `sam(tam, None, None, None)`. El comentario documenta sin entrar."""
    return "\n".join(str(v) for k, v in esqueleto.items() if k.startswith("_"))


# --- la forma sale de quien la consume -------------------------------------

def test_the_sam_keys_come_from_the_specialist(notas):
    """Si alguien renombra `geography_share` en `market.py`, esto falla aquí
    en vez de dejar el esqueleto nombrando una clave que ya no se lee."""
    from wbj.specialists.market import _SAM_KEYS_PUBLICAS

    for clave in _SAM_KEYS_PUBLICAS:
        assert clave in notas, (
            f"{clave} no aparece en el archivo: quien lo rellene tiene que "
            "leer el codigo fuente para acertar, que es lo que fallaba")


def test_the_som_shape_names_its_only_key(notas):
    assert "target_share" in notas


# --- las llaves que de verdad hacen falta ----------------------------------

@pytest.mark.parametrize("clave", [
    "upward", "total",                        # MKT-REVBR-011
    "current_consensus", "prior_consensus",   # MKT-REVMAG-012
    "individual_estimates",                   # MKT-DISP-013
    "actual", "pre_release_consensus",        # MKT-SURP-014
])
def test_every_estimates_subkey_a_metric_reads_is_documented(notas, clave):
    """Cuatro métricas leen dentro de `estimates` y ninguna se nombraba. Son
    la dimensión de revisiones entera — 4 de los 20 puntos de Market."""
    assert clave in notas


def test_the_shape_is_named_but_never_emitted_as_a_value(esqueleto):
    """El equilibrio que costó dos intentos: la forma se nombra en el
    comentario y el valor sigue siendo `null`. Emitirla como valor documentaba
    igual de bien y rompía el contrato de "un esqueleto es como no tener
    archivo"."""
    for clave in ("sam_inputs", "som_inputs", "estimates",
                  "scenarios", "competitor_shares", "catalysts"):
        assert esqueleto[clave] is None, (
            f"{clave} salio con valor: un diccionario de nulos entra al "
            "overlay y pasa los chequeos de presencia de market.py")


# --- lo que el esqueleto sigue sin hacer -----------------------------------

def test_the_skeleton_never_invents_a_figure(esqueleto):
    """Documentar la forma no es rellenarla. Todo escalar sigue en `null`, y
    un archivo así se comporta igual que no tener archivo."""
    def _hojas(v):
        if isinstance(v, dict):
            for x in v.values():
                yield from _hojas(x)
        elif isinstance(v, list):
            for x in v:
                yield from _hojas(x)
        else:
            yield v

    for clave, valor in esqueleto.items():
        if clave.startswith("_") or clave == "judgments":
            continue
        for hoja in _hojas(valor):
            assert hoja is None, (
                f"{clave} trae el valor {hoja!r}: el esqueleto documenta "
                "llaves, no inventa cifras")


def test_an_existing_file_is_not_overwritten():
    """Lo que hay en `Entradas/` es captura hecha a mano que ningún proveedor
    puede devolver. Regenerar encima la destruiría en silencio."""
    from wbj.entradas import write_skeleton

    d = Path(tempfile.mkdtemp())
    assert write_skeleton(d, "PRUEBA")[0] is True
    (d / "PRUEBA.json").write_text('{"tam": 123}', encoding="utf-8")
    ok, mensaje = write_skeleton(d, "PRUEBA")
    assert ok is False and "ya existe" in mensaje
    assert json.loads((d / "PRUEBA.json").read_text(encoding="utf-8")) == {"tam": 123}
