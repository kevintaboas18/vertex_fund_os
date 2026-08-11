"""El correo pre-market nunca se envio. Tres cosas lo impedian a la vez.

Los tres se confirmaron leyendo los logs reales del workflow, no razonando
sobre el codigo:

1. **La puerta horaria no se abria nunca.** El guion exigia `now.hour == 8` ET
   porque un comentario decia que el workflow corria a las 12:00 y 13:00 UTC.
   No existia tal cosa: hay un solo cron, y GitHub lo dispara tarde y con
   deriva -- se le vio a las 12:08 y a las 13:52 UTC con el mismo `30 11`. El
   log del 7 de agosto lo dice entero: "Son las 09:52 ET, no las 8 - skip".
   Y como saltarse el envio devuelve 0, el workflow salia **en verde**. Meses
   de palomitas verdes sin un solo correo.

2. **La fuente devolvia 403 al runner.** stockanalysis.com responde a la IP de
   casa y bloquea las de GitHub Actions. Ademas no es una de las cuatro
   fuentes del proyecto. Ahora los movers salen de FMP.

3. **El destinatario llegaba vacio.** `EMAIL_TO: ${{ vars.EMAIL_TO }}` con la
   variable sin definir inyecta cadena VACIA, y `os.environ.get(k, default)`
   devuelve "" -- no el default -- cuando la clave existe vacia. Resend
   recibia `"to": [""]`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))


def _modulo(monkeypatch, **entorno):
    """Recarga el modulo: EMAIL_TO y compania se leen al importar."""
    for k in ("EMAIL_TO", "EMAIL_FROM", "FMP_API_KEY", "RESEND_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in entorno.items():
        monkeypatch.setenv(k, v)
    import premarket_email
    return importlib.reload(premarket_email)


def test_una_variable_vacia_no_borra_el_destinatario(monkeypatch):
    """Lo que hacia que Resend contestara 422."""
    pe = _modulo(monkeypatch, EMAIL_TO="")
    assert pe.EMAIL_TO == "kevintaboas02@gmail.com"


def test_el_destinatario_configurado_manda(monkeypatch):
    pe = _modulo(monkeypatch, EMAIL_TO="otro@ejemplo.com")
    assert pe.EMAIL_TO == "otro@ejemplo.com"


def test_la_ventana_absorbe_la_deriva_del_cron(monkeypatch):
    """8:00 ET en punto era la unica hora aceptada y GitHub no la da nunca.
    Las horas de las corridas reales que se saltaron el envio -- 9:52 y 8:08
    ET -- tienen que entrar ahora."""
    pe = _modulo(monkeypatch)
    for hora in (6, 7, 8, 9, 10):
        assert hora in pe.VENTANA_ET, f"{hora} ET quedaria fuera"
    for hora in (5, 11, 15, 23):
        assert hora not in pe.VENTANA_ET, f"{hora} ET no es pre-market"


def test_los_movers_salen_de_fmp_no_de_una_web_raspada(monkeypatch):
    """El raspado de stockanalysis.com daba 403 desde el runner. Si alguien lo
    devuelve, este test cae."""
    pe = _modulo(monkeypatch, FMP_API_KEY="x")
    assert "financialmodelingprep.com" in pe.FMP_BASE
    # La URL, no la palabra: el comentario que explica por que se abandono la
    # fuente la nombra, y esa prosa es justamente lo que hay que conservar.
    fuente = Path(pe.__file__).read_text(encoding="utf-8")
    assert "https://stockanalysis.com" not in fuente


def test_una_fila_rota_no_tumba_el_correo(monkeypatch):
    """FMP mete ETFs y tickers raros; una fila sin precio no puede costar el
    envio entero de las otras nueve."""
    pe = _modulo(monkeypatch, FMP_API_KEY="x")
    monkeypatch.setattr(pe, "fetch_json", lambda p: [
        {"symbol": "AAA", "name": "Buena", "price": 10.0, "changesPercentage": 5.0},
        {"symbol": "BBB", "name": "Rota", "price": None, "changesPercentage": None},
    ])
    monkeypatch.setattr(pe, "market_cap", lambda t: 2e10)
    filas = pe.movers("biggest-gainers")
    assert [f["ticker"] for f in filas] == ["AAA"]


def test_sin_clave_de_resend_el_error_dice_donde_ponerla(monkeypatch):
    """Antes era un KeyError pelado en el log del runner."""
    pe = _modulo(monkeypatch)
    with pytest.raises(RuntimeError, match="Secrets"):
        pe.send_resend("s", "t", "<p>h</p>")
