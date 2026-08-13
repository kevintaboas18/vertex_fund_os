"""Cada cuenta tiene SU `.md`. `Kevin.md` no se toca nunca.

El mecanismo ya existia --`guardar_perfil()` escribe
`Perfil Inversionista/usuarios/<nombre>-<id>.md` y nadie abre `Kevin.md` en
modo escritura-- pero no habia nada que lo sujetara. Un `open(..., "w")` mal
puesto sobre `_PERFIL_MD_DEFECTO`, o un "regenerar el perfil" que resolviera la
ruta por su cuenta, pisaria el perfil de referencia de TODOS los usuarios y el
sintoma seria un reporte hablando del capital de otra persona.

Esa clase de fallo ya ocurrio una vez en este archivo --dos funciones
resolviendo el mismo directorio por separado, el editor escribiendo en uno y el
agente leyendo del otro-- asi que la garantia se escribe, no se supone.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "engine"))

import vertex_cuentas as CU  # noqa: E402

KEVIN_REAL = RAIZ / "Perfil Inversionista" / "Kevin.md"


@pytest.fixture
def perfiles(tmp_path):
    """Un directorio de perfiles con una copia del `Kevin.md` de verdad."""
    d = tmp_path / "Perfil Inversionista"
    d.mkdir()
    (d / "Kevin.md").write_bytes(KEVIN_REAL.read_bytes())
    return d


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    CU.crear_tablas(c)
    yield c
    c.close()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_guardar_un_perfil_crea_el_md_de_esa_cuenta(conn, perfiles):
    u = CU.crear_usuario(conn, "ana@ejemplo.com", "Ana Perez", "unaClaveLarga123")
    CU.guardar_perfil(conn, str(perfiles), u, {"modo": "personalizado", "capital": 50_000})
    propio = Path(CU.ruta_md_de(str(perfiles), u))
    assert propio.is_file(), "no se creo el .md de la cuenta"
    assert propio.parent.name == "usuarios", "se escribio fuera de usuarios/"
    assert "$50,000" in propio.read_text(encoding="utf-8")


def test_kevin_md_no_se_toca_nunca(conn, perfiles):
    """La peticion de Victor, literal: su archivo de referencia no se edita."""
    antes = _sha(perfiles / "Kevin.md")
    for i in range(3):
        u = CU.crear_usuario(conn, f"u{i}@ejemplo.com", f"Usuario {i}", "unaClaveLarga123")
        CU.guardar_perfil(conn, str(perfiles), u,
                          {"modo": "personalizado", "capital": 1_000 * (i + 1),
                           "max_posicion_pct": [5 + i, 10 + i]})
    assert _sha(perfiles / "Kevin.md") == antes, "alguien escribio en Kevin.md"


def test_dos_personas_con_el_mismo_nombre_no_se_pisan(conn, perfiles):
    """Por eso el id va en el nombre del archivo y no solo el nombre."""
    a = CU.crear_usuario(conn, "kevin1@ejemplo.com", "Kevin", "unaClaveLarga123")
    b = CU.crear_usuario(conn, "kevin2@ejemplo.com", "Kevin", "unaClaveLarga123")
    CU.guardar_perfil(conn, str(perfiles), a, {"modo": "personalizado", "capital": 1_000})
    CU.guardar_perfil(conn, str(perfiles), b, {"modo": "personalizado", "capital": 90_000})
    ra, rb = Path(CU.ruta_md_de(str(perfiles), a)), Path(CU.ruta_md_de(str(perfiles), b))
    assert ra != rb
    assert "$1,000" in ra.read_text(encoding="utf-8")
    assert "$90,000" in rb.read_text(encoding="utf-8")


def test_el_perfil_guardado_es_el_suyo_no_el_de_kevin(conn, perfiles):
    u = CU.crear_usuario(conn, "ana@ejemplo.com", "Ana Perez", "unaClaveLarga123")
    CU.guardar_perfil(conn, str(perfiles), u,
                      {"modo": "personalizado", "capital": 50_000,
                       "max_posicion_pct": [10, 15], "tolerancia": "conservador"})
    suyo = CU.leer_perfil(conn, u["id"])
    assert suyo["capital"] == 50_000
    assert suyo["max_posicion_pct"] == [10, 15]
    assert suyo["tolerancia"] == "conservador"


def test_en_modo_default_su_md_refleja_el_de_referencia(conn, perfiles):
    """Quien NO personaliza hereda el perfil de referencia -- pero en su propio
    archivo. Heredar no puede significar compartir el archivo de otro."""
    u = CU.crear_usuario(conn, "bea@ejemplo.com", "Bea Ruiz", "unaClaveLarga123")
    antes = _sha(perfiles / "Kevin.md")
    CU.guardar_perfil(conn, str(perfiles), u, {"modo": "default"})
    propio = Path(CU.ruta_md_de(str(perfiles), u))
    assert propio.is_file()
    assert _sha(perfiles / "Kevin.md") == antes
    assert CU.leer_perfil(conn, u["id"])["capital"] == CU.perfil_por_defecto()["capital"]
