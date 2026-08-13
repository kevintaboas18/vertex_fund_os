"""Un TAM no es un hecho permanente: hay que volver a leerlo.

WSTS revisa sus ventas mundiales cada trimestre y la IEA su demanda cada mes.
Un número correcto en agosto puede estar viejo en noviembre, y el archivo no se
entera solo.

Esto es lo que permite que la cifra la escriba **el agente** en vez del
analista sin volver al problema de origen. El problema era que el modelo
*recordaba* un número y nadie podía comprobarlo — nueve de diez citas eran
redirects caducados. Aquí la cifra no se recuerda: se vuelve a leer en la
página de su fuente cada 90 días, y si ya no está, se dice.

Tres cosas que esta revisión NO hace, y las tres importan:

- **No borra un TAM porque la página fallara hoy.** Un timeout no es una
  corrección. Se anota el intento y se conserva el número.
- **No toca lo que escribió un analista.** Sin `_verificado_por`, el archivo
  es de su autor.
- **No inventa el número nuevo.** Si la cifra cambió, no adivina cuál la
  sustituye: marca `_revisar_a_mano` y deja el anterior, que al menos se sabe
  de dónde salió. Adivinar el reemplazo sería repetir exactamente el error que
  toda esta verificación existe para impedir.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from wbj.overlay import tam_mundial as tm


class _S:
    def __init__(self, raiz):
        self.inputs_dir = str(raiz)


_HOY = date(2026, 11, 20)


def _archivo(tmp_path, **campos):
    d = tmp_path / "_industrias"
    d.mkdir(exist_ok=True)
    base = {
        "_generado_por": "vertex/tam_mundial",
        "_verificado_por": "vertex/tam_revision",
        "_verificado_en": "2026-08-08",
        "tam": 1_510_000_000_000,
        "tam_source": "WSTS Spring 2026 forecast",
        "tam_source_tier": 2,
        "_cita_verificada": "https://www.wsts.org/76/Recent-News-Release",
    }
    base.update(campos)
    (d / "semiconductors.json").write_text(json.dumps(base), encoding="utf-8")
    return _S(tmp_path)


def _leer(tmp_path) -> dict:
    return json.loads(
        (tmp_path / "_industrias" / "semiconductors.json").read_text(encoding="utf-8"))


def _verificador(monkeypatch, resultado: tuple[bool, str]):
    monkeypatch.setattr(tm, "_verificar_en_la_fuente",
                        lambda url, tam, fuente: resultado)


# --- la cifra sigue ahi ----------------------------------------------------

def test_a_figure_still_on_its_page_is_confirmed(tmp_path, monkeypatch):
    _verificador(monkeypatch, (True, "https://www.wsts.org/76/Recent-News-Release"))
    s = _archivo(tmp_path)
    filas = tm.revisar_tam_industrias(s, hoy=_HOY, forzar=True)
    assert filas[0]["estado"] == "confirmado"
    assert _leer(tmp_path)["_verificado_en"] == _HOY.isoformat()


def test_the_figure_survives_a_confirmation(tmp_path, monkeypatch):
    """Confirmar no reescribe: el número tiene que salir igual."""
    _verificador(monkeypatch, (True, "https://www.wsts.org/x"))
    s = _archivo(tmp_path)
    tm.revisar_tam_industrias(s, hoy=_HOY, forzar=True)
    assert _leer(tmp_path)["tam"] == 1_510_000_000_000


# --- la cifra cambio -------------------------------------------------------

def test_a_changed_figure_is_flagged_not_guessed(tmp_path, monkeypatch):
    """Si la cifra ya no aparece, el organismo la revisó. Adivinar cuál la
    sustituye sería repetir el error que toda esta verificación impide."""
    _verificador(monkeypatch, (False, "la cifra no aparece en https://www.wsts.org/x"))
    s = _archivo(tmp_path)
    filas = tm.revisar_tam_industrias(s, hoy=_HOY, forzar=True)
    assert filas[0]["estado"] == "CAMBIO"
    d = _leer(tmp_path)
    assert d["tam"] == 1_510_000_000_000, "borró un número que nadie corrigió"
    assert "_revisar_a_mano" in d
    assert _HOY.isoformat() in d["_revisar_a_mano"]


# --- la fuente no respondio ------------------------------------------------

def test_an_unreachable_source_is_not_a_correction(tmp_path, monkeypatch):
    """Un timeout no dice nada sobre la cifra. Tratarlo como un cambio
    borraría TAM buenos cada vez que una web tuviera un mal día."""
    _verificador(monkeypatch, (False, "la cita no se pudo abrir: TimeoutError"))
    s = _archivo(tmp_path)
    filas = tm.revisar_tam_industrias(s, hoy=_HOY, forzar=True)
    assert filas[0]["estado"] == "fuente inaccesible"
    d = _leer(tmp_path)
    assert d["tam"] == 1_510_000_000_000
    assert "_revisar_a_mano" not in d, "una web caida no marca la cifra como mala"
    assert "_ultimo_intento" in d


def test_a_recovered_source_clears_the_flag(tmp_path, monkeypatch):
    """Lo que se marcó para revisar tiene que poder desmarcarse solo cuando la
    fuente vuelve a publicar la cifra."""
    _verificador(monkeypatch, (True, "https://www.wsts.org/x"))
    s = _archivo(tmp_path, _revisar_a_mano="2026-09-01: la cifra no aparece")
    tm.revisar_tam_industrias(s, hoy=_HOY, forzar=True)
    assert "_revisar_a_mano" not in _leer(tmp_path)


# --- de quien es cada archivo ----------------------------------------------

def test_an_analyst_file_is_never_touched(tmp_path, monkeypatch):
    """Sin `_verificado_por` el archivo es de su autor. Kevin escribió el TAM
    de NVDA a mano y ése es el único que sobrevivió a la auditoría."""
    _verificador(monkeypatch, (False, "la cifra no aparece"))
    s = _archivo(tmp_path, _verificado_por=None)
    filas = tm.revisar_tam_industrias(s, hoy=_HOY, forzar=True)
    assert "analista" in filas[0]["estado"]
    assert "_revisar_a_mano" not in _leer(tmp_path)


# --- cada cuanto -----------------------------------------------------------

def test_it_waits_its_interval_before_re_reading(tmp_path, monkeypatch):
    """Revisar en cada análisis sería una descarga por ticker y por corrida
    contra la web de una asociación que no la pidió."""
    _verificador(monkeypatch, (True, "https://www.wsts.org/x"))
    s = _archivo(tmp_path, _verificado_en="2026-11-18")
    filas = tm.revisar_tam_industrias(s, hoy=_HOY)
    assert filas[0]["estado"] == "vigente"


def test_the_interval_is_per_file(tmp_path, monkeypatch):
    """La IEA publica cada mes y WSTS cada trimestre: el archivo dice cada
    cuánto quiere que lo relean."""
    _verificador(monkeypatch, (True, "https://www.iea.org/x"))
    s = _archivo(tmp_path, _verificado_en="2026-10-15", _revisar_cada_dias=30)
    assert tm.revisar_tam_industrias(s, hoy=_HOY)[0]["estado"] == "confirmado"


def test_an_unresolved_industry_is_skipped(tmp_path, monkeypatch):
    """Sin TAM no hay nada que releer, y una fila por cada industria vacía
    convertiría el reporte en ruido."""
    _verificador(monkeypatch, (True, "x"))
    s = _archivo(tmp_path, tam=None)
    assert tm.revisar_tam_industrias(s, hoy=_HOY, forzar=True) == []
