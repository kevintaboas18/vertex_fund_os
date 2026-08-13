"""Quien no personaliza hereda Kevin.md — el archivo, no una copia suya.

El mecanismo de modos ya existia y funcionaba: `personalizado` usa lo que la
persona contesto, `default` usa el perfil de referencia. Lo que estaba mal era
de donde salia ese "perfil de referencia".

`perfil_por_defecto()` lo construia desde los `defecto` de `PREGUNTAS`, escritos
a mano en `vertex_cuentas.py`. Coincidian con `Perfil Inversionista/Kevin.md`
--capital $1.000, horizonte 1-3 anos, 20-30% por posicion-- pero eran una
SEGUNDA COPIA. CLAUDE.md promete que editar ese archivo cambia el perfil "en
todo el sistema", y para todo el que estuviera en modo `default` esa promesa era
falsa: el archivo se movia y el default se quedaba quieto.

Nadie se habria enterado. Los dos numeros se ven igual de plausibles en
pantalla, y el unico sintoma seria un `profile_fit` calculado contra un capital
que ya nadie tiene.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "engine"))

import vertex_cuentas as CU  # noqa: E402
from wbj.specialists import risk  # noqa: E402


@pytest.fixture
def kevin_md(monkeypatch):
    """Edita `Kevin.md` en memoria. El default tiene que moverse con el."""
    def _poner(**campos):
        for k, v in campos.items():
            monkeypatch.setitem(risk.PROFILE, k, v)
    return _poner


def test_el_default_de_hoy_es_el_de_kevin_md():
    d = CU.perfil_por_defecto()
    assert d["capital"] == risk.PROFILE["capital_usd"]
    lo, hi = risk.PROFILE["max_position_pct"]
    assert d["max_posicion_pct"] == [round(lo * 100), round(hi * 100)]


def test_editar_kevin_md_mueve_el_default(kevin_md):
    """La promesa de CLAUDE.md, comprobada: se edita el archivo y cambia el
    perfil de quien no personalizo."""
    kevin_md(capital_usd=25_000.0, max_position_pct=(0.05, 0.10),
             horizon_years=(5, 10))
    d = CU.perfil_por_defecto()
    assert d["capital"] == 25_000.0
    assert d["max_posicion_pct"] == [5, 10]
    assert d["horizonte"] == "5+ años"


def test_el_modo_default_aplica_el_archivo(kevin_md):
    kevin_md(capital_usd=25_000.0)
    assert CU.perfil_efectivo({"modo": "default", "capital": 999})["capital"] == 25_000.0


def test_el_perfil_personalizado_manda_sobre_kevin_md(kevin_md):
    """Lo que pidio Victor: si contestaste, es TU perfil el que se usa."""
    kevin_md(capital_usd=25_000.0, max_position_pct=(0.05, 0.10))
    mio = {"modo": "personalizado", "capital": 4_200, "max_posicion_pct": [60, 70]}
    salida = CU.perfil_efectivo(mio)
    assert salida["capital"] == 4_200
    assert salida["max_posicion_pct"] == [60, 70]


def test_solo_se_hereda_lo_que_el_archivo_declara(kevin_md):
    """`fields_parsed` es la autoridad. La tolerancia NO se parsea de Kevin.md
    --risk.py la trae como constante-- asi que esa respuesta se queda con el
    default de la pregunta, que es donde de verdad vive."""
    kevin_md(fields_parsed=["capital_usd"], capital_usd=7_000.0,
             max_position_pct=(0.90, 0.99))
    d = CU.perfil_por_defecto()
    assert d["capital"] == 7_000.0
    assert d["max_posicion_pct"] == [20, 30], "heredo un campo que el .md no declara"
    assert d["tolerancia"] == "agresivo"


def test_un_horizonte_que_el_cuestionario_no_ofrece_no_se_inventa(kevin_md):
    """Inventar una opcion seria peor que no tenerla: el desajuste entre el
    archivo y el cuestionario tiene que quedar visible, no maquillado."""
    kevin_md(horizon_years=(2, 7))
    assert CU.perfil_por_defecto()["horizonte"] == "1-3 años"


def test_sin_engine_el_cuestionario_se_queda_con_los_suyos(monkeypatch):
    """Un perfil no puede tumbar la app. Sin `risk.py` importable, defaults."""
    monkeypatch.setattr(CU, "_campos_de_kevin_md", lambda: {})
    d = CU.perfil_por_defecto()
    assert d["capital"] == 1000 and d["max_posicion_pct"] == [20, 30]
