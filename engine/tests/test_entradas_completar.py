"""`top_up_skeleton`: la llave nueva llega sin borrar la vieja.

Un archivo de `Entradas/` escrito antes de que el motor creciera no vuelve a
mencionar la llave nueva jamas -- `write_skeleton` se niega a reabrirlo y
`--force` borraria la captura. Esa es la grieta por la que `affo_history` quedo
fuera de los cuatro archivos ya en disco el dia que arreglo la valuacion de un
REIT.

Lo que se prueba aqui es lo unico que hace peligrosa la operacion: que agregar
no sea, en el fondo, sobrescribir.
"""
import json

import pytest

from wbj.entradas import SKELETON_KEYS, render_skeleton, top_up_skeleton


@pytest.fixture()
def viejo(tmp_path):
    """Un archivo real al que le falta una llave y tiene dos capturadas."""
    datos = json.loads(render_skeleton("NVDA"))
    del datos["affo_history"]
    datos["tam"] = 450_000_000_000
    datos["tam_source"] = "Gartner 2025, p. 12"
    path = tmp_path / "NVDA.json"
    path.write_text(json.dumps(datos, indent=2) + "\n", encoding="utf-8")
    return path


def test_la_llave_que_faltaba_aparece_en_null(viejo):
    ok, mensaje = top_up_skeleton(viejo)
    assert ok, mensaje
    datos = json.loads(viejo.read_text(encoding="utf-8"))
    assert "affo_history" in datos
    # En null: un esqueleto no puntua nunca, ni siquiera completando.
    assert datos["affo_history"] is None
    assert "affo_history" in mensaje


def test_lo_capturado_sobrevive_intacto(viejo):
    top_up_skeleton(viejo)
    datos = json.loads(viejo.read_text(encoding="utf-8"))
    assert datos["tam"] == 450_000_000_000
    assert datos["tam_source"] == "Gartner 2025, p. 12"


def test_una_llave_que_el_motor_ya_no_nombra_no_se_borra(tmp_path):
    datos = json.loads(render_skeleton("NVDA"))
    datos["llave_de_otra_epoca"] = 12345
    path = tmp_path / "NVDA.json"
    path.write_text(json.dumps(datos, indent=2) + "\n", encoding="utf-8")

    ok, mensaje = top_up_skeleton(path)
    assert ok, mensaje
    salida = json.loads(path.read_text(encoding="utf-8"))
    # Alguien la escribio. Que el motor la ignore no la vuelve falsa.
    assert salida["llave_de_otra_epoca"] == 12345
    assert "huerfana" in mensaje


def test_un_archivo_al_dia_no_se_reescribe(tmp_path):
    path = tmp_path / "NVDA.json"
    path.write_text(render_skeleton("NVDA"), encoding="utf-8")
    antes = path.read_text(encoding="utf-8")

    ok, mensaje = top_up_skeleton(path)
    assert not ok
    assert "al dia" in mensaje
    assert path.read_text(encoding="utf-8") == antes


def test_completar_deja_el_contrato_entero(viejo):
    top_up_skeleton(viejo)
    datos = json.loads(viejo.read_text(encoding="utf-8"))
    faltan = [k for k in SKELETON_KEYS if k not in datos]
    assert not faltan, f"siguen sin documentarse: {faltan}"


def test_un_json_roto_no_se_toca(tmp_path):
    path = tmp_path / "NVDA.json"
    path.write_text("{ esto no es json", encoding="utf-8")
    ok, mensaje = top_up_skeleton(path)
    assert not ok
    assert "no se toca" in mensaje
    # Intacto: perder una captura por un error de sintaxis es el peor final.
    assert path.read_text(encoding="utf-8") == "{ esto no es json"


def test_un_archivo_de_configuracion_no_se_confunde_con_un_ticker(tmp_path):
    """`cik_overrides.json` vive en `Entradas/` y NO es de una empresa.

    Sin guard, completar lo leia como el esqueleto de "CIK_OVERRIDES" y le
    escribia el contrato entero encima. Paso de verdad una vez.
    """
    path = tmp_path / "cik_overrides.json"
    original = json.dumps({"_comment_1": "mapa ticker -> CIK", "XOM": "0000034088"},
                          indent=2)
    path.write_text(original, encoding="utf-8")

    ok, mensaje = top_up_skeleton(path)
    assert not ok
    assert "no se toca" in mensaje
    assert path.read_text(encoding="utf-8") == original


def test_un_json_ajeno_con_nombre_corto_tampoco(tmp_path):
    """El nombre solo no basta: `NOTAS.json` cabe en seis letras."""
    path = tmp_path / "NOTAS.json"
    original = json.dumps({"cualquier": 1, "cosa": 2}, indent=2)
    path.write_text(original, encoding="utf-8")

    ok, mensaje = top_up_skeleton(path)
    assert not ok
    assert "contrato" in mensaje
    assert path.read_text(encoding="utf-8") == original


def test_un_archivo_que_no_existe_no_se_crea(tmp_path):
    path = tmp_path / "ZZZZ.json"
    ok, mensaje = top_up_skeleton(path)
    assert not ok
    assert "no existe" in mensaje
    assert not path.exists()
