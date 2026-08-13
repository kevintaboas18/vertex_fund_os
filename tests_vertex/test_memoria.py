"""La memoria entre sesiones: que exista, que se lea y que cambie algo.

`CLAUDE.md` describe el protocolo —leer la tesis antes de analizar, escribirla
después, apuntar la lección al contradecirse— y decía que NO era automático. No
lo era: `Memoria/` ni siquiera existía en la rama de datos. Estos casos son lo
que impide que vuelva a no serlo.

    python -m pytest tests_vertex/test_memoria.py -q
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if not __import__("shutil").which("git"):        # pragma: no cover
    pytest.skip("hace falta git", allow_module_level=True)


@pytest.fixture
def alm(tmp_path):
    from vertex_almacen import Almacen

    a = Almacen(raiz=tmp_path / "almacen")
    a.restaura()
    return a


def _texto(alm, ruta):
    v = alm.lee(ruta)
    return v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else (v or "")


def _acciones(veredicto, puntaje, precio, tesis="Una tesis."):
    return {"analisis": {"recommendation": veredicto, "total_score": puntaje,
                         "in_simple_terms": tesis},
            "targets": {"12m": {"bear": 100.0, "base": 150.0, "bull": 200.0}},
            "precio_actual": precio}


class TestSeEscribeSola:
    """Guardar un reporte tiene que dejar memoria. Sin llamar a nada más."""

    def test_archivar_un_reporte_escribe_la_tesis_y_el_indice(self, alm):
        import vertex_archivo as VA
        import vertex_memoria as M

        VA.guarda_reporte_acciones("NVDA", _acciones("FAVORABLE", 78, 150.0), alm=alm)

        tesis = _texto(alm, M.ruta_tesis("NVDA"))
        assert "FAVORABLE" in tesis and "78" in tesis and "$150.00" in tesis
        indice = _texto(alm, M.RUTA_INDICE)
        assert "[NVDA](tesis/NVDA.md)" in indice and "FAVORABLE" in indice

    def test_el_agente_de_opciones_tambien(self, alm):
        import vertex_archivo as VA
        import vertex_memoria as M

        VA.guarda_reporte_opciones("WULF", {"score": 71, "verdict": "ALCISTA",
                                            "company": {"price": 12.5}}, alm=alm)
        t = _texto(alm, M.ruta_tesis("WULF"))
        assert "ALCISTA" in t and "71" in t and "opciones" in t

    def test_los_dos_agentes_comparten_el_archivo_del_ticker(self, alm):
        """Es el mismo ticker y el mismo lector. Dos archivos obligarían a abrir
        los dos para saber qué se piensa de NVDA."""
        import vertex_archivo as VA
        import vertex_memoria as M

        VA.guarda_reporte_acciones("NVDA", _acciones("FAVORABLE", 78, 150.0),
                                   alm=alm, cuando="2026-08-01")
        VA.guarda_reporte_opciones("NVDA", {"score": 64, "verdict": "NEUTRAL",
                                            "company": {"price": 152.0}},
                                   alm=alm, cuando="2026-08-02")
        t = _texto(alm, M.ruta_tesis("NVDA"))
        assert "agente de acciones" in t and "agente de opciones" in t


class TestLaTesisVIEJANoSeBorra:
    """`CLAUDE.md`: «nunca borres la tesis vieja — corrígela encima».

    El motivo es el mismo por el que existe el track record: si se borra lo que
    se dijo, no hay forma de saber si se acertó.
    """

    def test_la_revision_nueva_va_arriba_y_la_vieja_se_queda(self, alm):
        import vertex_archivo as VA
        import vertex_memoria as M

        VA.guarda_reporte_acciones("AMD", _acciones("FAVORABLE", 80, 100.0, "Lo viejo."),
                                   alm=alm, cuando="2026-08-01")
        VA.guarda_reporte_acciones("AMD", _acciones("FAVORABLE", 72, 110.0, "Lo nuevo."),
                                   alm=alm, cuando="2026-08-10")
        t = _texto(alm, M.ruta_tesis("AMD"))
        assert "Lo viejo." in t, "se borró la tesis anterior"
        assert t.index("Lo nuevo.") < t.index("Lo viejo."), "la nueva no quedó arriba"

    def test_no_crece_sin_freno(self, alm):
        import vertex_archivo as VA
        import vertex_memoria as M

        for i in range(M.MAX_REVISIONES + 4):
            VA.guarda_reporte_acciones(
                "AMD", _acciones("FAVORABLE", 70 + i, 100.0 + i, f"Rev {i}."),
                alm=alm, cuando=f"2026-08-{(i % 28) + 1:02d}")
        t = _texto(alm, M.ruta_tesis("AMD"))
        assert t.count("### ") == M.MAX_REVISIONES


class TestCuandoElAgenteSeContradice:
    """Dar la vuelta al veredicto se apunta. No es castigo: es lo único que
    permite revisar después si el cambio estuvo justificado."""

    def test_el_cambio_de_veredicto_queda_en_errores(self, alm):
        import vertex_archivo as VA
        import vertex_memoria as M

        VA.guarda_reporte_acciones("META", _acciones("FAVORABLE", 78, 150.0),
                                   alm=alm, cuando="2026-08-01")
        VA.guarda_reporte_acciones("META", _acciones("DESFAVORABLE", 41, 120.0),
                                   alm=alm, cuando="2026-08-13")
        e = _texto(alm, M.RUTA_ERRORES)
        assert "META" in e and "FAVORABLE" in e and "DESFAVORABLE" in e
        assert "-20.00%" in e, "no dice qué hizo el precio entre las dos"

    def test_confirmar_la_tesis_NO_es_un_error(self, alm):
        import vertex_archivo as VA
        import vertex_memoria as M

        VA.guarda_reporte_acciones("GOOGL", _acciones("FAVORABLE", 78, 150.0),
                                   alm=alm, cuando="2026-08-01")
        VA.guarda_reporte_acciones("GOOGL", _acciones("FAVORABLE", 81, 160.0),
                                   alm=alm, cuando="2026-08-13")
        assert not _texto(alm, M.RUTA_ERRORES).strip(), (
            "mantener el veredicto se apuntó como contradicción")


class TestLaMemoriaLLEGAalAgente:
    """Lo que decide si esto es memoria o solo un archivo bonito."""

    def test_el_contexto_trae_la_tesis_y_lo_que_hizo_el_precio(self, alm):
        import vertex_archivo as VA
        import vertex_memoria as M

        VA.guarda_reporte_acciones("NVDA", _acciones("FAVORABLE", 78, 120.0,
                                                     "El margen aguanta."), alm=alm)
        ctx = M.contexto_para_el_agente("NVDA", alm, precio_hoy=132.0)
        assert "MEMORIA DE NVDA" in ctx
        assert "El margen aguanta." in ctx
        assert "+10.00%" in ctx, "no dice qué hizo el precio desde entonces"
        assert "contradice" in ctx, "no le pide al modelo que se compare"

    def test_sin_memoria_previa_no_inventa_nada(self, alm):
        import vertex_memoria as M

        assert M.contexto_para_el_agente("ZZZZ", alm) == ""
        assert M.lee_tesis("ZZZZ", alm) is None

    def test_un_ticker_invalido_no_escribe_ni_rompe(self, alm):
        import vertex_memoria as M

        assert M.lee_tesis("../../etc/passwd", alm) is None
        assert M.contexto_para_el_agente("no-es-ticker", alm) == ""

    def test_el_prompt_del_agente_recibe_la_memoria(self):
        """El cableado: si `_wbj_contexto` deja de leerla, la memoria se escribe
        y no la mira nadie."""
        fuente = (ROOT / "vertex_api.py").read_text(encoding="utf-8")
        assert "contexto_para_el_agente" in fuente, (
            "nadie mete la memoria en el prompt")
        # `rsplit`, no `split`: la primera aparición de «MI PERFIL» está en un
        # docstring que EXPLICA el bloque, no en el bloque.
        assert "_memoria_ctx" in fuente.rsplit("=== MI PERFIL", 1)[0][-2500:], (
            "la memoria no entra en el contexto que se le pasa al modelo")


class TestLaMemoriaViajaAGitHub:
    """De nada sirve recordar si el recuerdo se borra en el próximo deploy."""

    def test_la_memoria_sobrevive_al_borrado_del_disco(self, tmp_path):
        from vertex_almacen import Almacen
        import vertex_archivo as VA
        import vertex_memoria as M

        remoto = tmp_path / "remoto.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remoto)], check=True)
        url = f"file://{remoto}"

        a = Almacen(raiz=tmp_path / "a", remoto=url, token="x")
        a.restaura()
        VA.guarda_reporte_acciones("NVDA", _acciones("FAVORABLE", 78, 150.0,
                                                     "Sobrevive."), alm=a)
        a.sincroniza()

        # Otro contenedor, disco vacío.
        b = Almacen(raiz=tmp_path / "b", remoto=url, token="x")
        b.restaura()
        assert "Sobrevive." in _texto(b, M.ruta_tesis("NVDA")), (
            "la memoria no llegó a la rama de datos")
        assert "[NVDA](tesis/NVDA.md)" in _texto(b, M.RUTA_INDICE)
