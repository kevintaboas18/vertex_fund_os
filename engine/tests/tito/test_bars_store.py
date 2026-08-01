"""Port de `barsStore.ts`.

El original no trae test propio (su consumidor es Wheel), así que estos cubren
su contrato completo más la divergencia de la sesión abierta.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wbj.tito.bars_store import (
    MARKET_CLOSE_HOUR,
    cached_daily_bars,
    load_bars,
    save_bars,
)
from wbj.tito.levels import LvlBar

# 14:00 UTC = 10:00 ET → sesión ABIERTA · 21:00 UTC = 17:00 ET → cerrada
ABIERTA = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
CERRADA = datetime(2026, 7, 31, 21, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))


def _barras(n=5, base=100.0):
    return [LvlBar(time=(datetime(2026, 7, 27) + timedelta(days=i)).date().isoformat(),
                   high=base + i + 1, low=base + i - 1, close=base + i)
            for i in range(n)]


class TestGuardarYLeer:
    def test_round_trip(self):
        save_bars("DEMO", _barras(), CERRADA)
        f = load_bars("DEMO")
        assert f is not None
        assert f.ticker == "DEMO"
        assert f.date == "2026-07-31"          # día de mercado (ET)
        assert f.bars == _barras()

    def test_sin_cache_devuelve_none(self):
        assert load_bars("NUNCA") is None

    def test_el_ticker_se_normaliza(self):
        save_bars("demo", _barras(), CERRADA)
        assert load_bars("DEMO").ticker == "DEMO"

    def test_un_ticker_inservible_no_lanza_al_leer(self):
        assert load_bars("!!!") is None
        with pytest.raises(ValueError):
            save_bars("!!!", _barras(), CERRADA)

    def test_una_barra_corrupta_no_tumba_el_cache(self):
        import json

        from wbj.tito.stores import data_dir

        p = data_dir() / "bars"
        p.mkdir(parents=True, exist_ok=True)
        (p / "DEMO.json").write_text(json.dumps({
            "ticker": "DEMO", "date": "2026-07-31",
            "bars": [{"time": "2026-07-30", "high": 2, "low": 1, "close": 1.5},
                     "basura", None, {"time": "x"}, 42],
        }), encoding="utf-8")
        assert len(load_bars("DEMO").bars) == 1

    def test_un_archivo_roto_se_lee_como_sin_cache(self):
        from wbj.tito.stores import data_dir

        p = data_dir() / "bars"
        p.mkdir(parents=True, exist_ok=True)
        (p / "DEMO.json").write_text("{no soy json", encoding="utf-8")
        assert load_bars("DEMO") is None


class TestCachedDailyBars:
    def test_la_primera_llamada_pide_y_guarda(self):
        llamadas = []

        def fetch(t, days):
            llamadas.append(t)
            return _barras()

        out = cached_daily_bars("DEMO", now=CERRADA, fetch=fetch)
        assert out == _barras()
        assert llamadas == ["DEMO"]
        assert load_bars("DEMO").bars == _barras()

    def test_la_segunda_no_toca_la_red_con_la_sesion_cerrada(self):
        llamadas = []

        def fetch(t, days):
            llamadas.append(t)
            return _barras()

        cached_daily_bars("DEMO", now=CERRADA, fetch=fetch)
        cached_daily_bars("DEMO", now=CERRADA, fetch=fetch)
        cached_daily_bars("DEMO", now=CERRADA, fetch=fetch)
        assert llamadas == ["DEMO"], "el cache no ahorró la llamada"

    def test_con_la_sesion_ABIERTA_siempre_refresca(self):
        """La divergencia declarada: la barra de hoy es parcial mientras la
        sesión está abierta, y congelarla dejaría la gráfica quieta."""
        llamadas = []

        def fetch(t, days):
            llamadas.append(t)
            return _barras(base=100.0 + len(llamadas))

        a = cached_daily_bars("DEMO", now=ABIERTA, fetch=fetch)
        b = cached_daily_bars("DEMO", now=ABIERTA, fetch=fetch)
        assert len(llamadas) == 2
        assert a[-1].close != b[-1].close, "el precio se quedó congelado"

    def test_un_cache_de_ayer_no_vale_para_hoy(self):
        llamadas = []

        def fetch(t, days):
            llamadas.append(t)
            return _barras()

        ayer = CERRADA - timedelta(days=1)
        cached_daily_bars("DEMO", now=ayer, fetch=fetch)
        cached_daily_bars("DEMO", now=CERRADA, fetch=fetch)
        assert len(llamadas) == 2

    def test_un_cache_vacio_no_cuenta_como_cache(self):
        llamadas = []

        def fetch(t, days):
            llamadas.append(t)
            return []

        cached_daily_bars("DEMO", now=CERRADA, fetch=fetch)
        cached_daily_bars("DEMO", now=CERRADA, fetch=fetch)
        assert len(llamadas) == 2       # no se guardó nada, así que se repite

    def test_si_la_red_falla_y_no_hay_cache_devuelve_vacio(self):
        from wbj.tito.massive import MassiveError

        def boom(t, days):
            raise MassiveError("Massive caído")

        assert cached_daily_bars("DEMO", now=CERRADA, fetch=boom) == []

    def test_un_error_de_PROGRAMACION_no_se_disfraza_de_red_caida(self):
        # `except Exception` a secas convertía una firma cambiada en "Massive
        # está caído": el peor diagnóstico posible, manda a mirar la red cuando
        # el problema está en el código.
        def firma_mala(t):
            return []

        with pytest.raises(TypeError):
            cached_daily_bars("DEMO", now=CERRADA, fetch=firma_mala)

    def test_si_la_red_falla_pero_hay_cache_viejo_lo_usa(self):
        # DIVERGENCIA: el original devuelve []. Unas barras de ayer dan un
        # análisis viejo pero honesto; ninguna barra no da nada.
        save_bars("DEMO", _barras(), CERRADA - timedelta(days=3))

        from wbj.tito.massive import MassiveError

        def boom(t, days):
            raise MassiveError("Massive caído")

        assert cached_daily_bars("DEMO", now=CERRADA, fetch=boom) == _barras()

    def test_el_cierre_esta_donde_dice_estar(self):
        assert MARKET_CLOSE_HOUR == 16
