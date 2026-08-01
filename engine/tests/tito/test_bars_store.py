"""Port de `barsStore.ts`.

El original no trae test propio (su consumidor es Wheel), así que estos cubren
su contrato completo más la divergencia de la sesión abierta.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from zoneinfo import ZoneInfo

from wbj.tito.bars_store import (
    MARKET_CLOSE_HOUR,
    mercado_abierto,
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


ET = ZoneInfo("America/New_York")


class TestCuandoElCacheEsValido:
    """La regla es una: con la sesión en curso no se cachea, punto.

    Dos versiones anteriores estaban mal y las encontró la auditoría.
    """

    @pytest.mark.parametrize("lbl,cuando,abierto", [
        ("lunes pre-market 08:00",   datetime(2026, 7, 27, 8, tzinfo=ET), False),
        ("lunes apertura 09:30",     datetime(2026, 7, 27, 9, 30, tzinfo=ET), True),
        ("lunes 11:00",              datetime(2026, 7, 27, 11, tzinfo=ET), True),
        ("lunes 15:59",              datetime(2026, 7, 27, 15, 59, tzinfo=ET), True),
        ("lunes cierre 16:00",       datetime(2026, 7, 27, 16, tzinfo=ET), False),
        ("lunes 18:00",              datetime(2026, 7, 27, 18, tzinfo=ET), False),
        ("SABADO 11:00",             datetime(2026, 8, 1, 11, tzinfo=ET), False),
        ("DOMINGO 14:00",            datetime(2026, 8, 2, 14, tzinfo=ET), False),
    ])
    def test_el_horario_de_sesion(self, lbl, cuando, abierto):
        assert mercado_abierto(cuando) is abierto, lbl

    def test_el_fin_de_semana_SI_cachea(self):
        # La 1a version miraba solo `hora >= 16` y dejaba el fin de semana
        # entero sin cache, con cero posibilidad de que el dato cambiara.
        sabado = datetime(2026, 8, 1, 11, tzinfo=ET)
        n = []

        def fetch(t, days):
            n.append(t)
            return _barras()

        for _ in range(5):
            cached_daily_bars("D", now=sabado, fetch=fetch)
        assert len(n) == 1

    def test_no_se_congela_si_massive_aun_no_publico_la_barra_de_hoy(self):
        """La 2a version cacheaba cuando la ultima barra era anterior a hoy,
        creyendo que era un festivo. Pero a las 9:31 Massive puede no haber
        publicado el agregado del dia — y entonces la sesion entera se servia
        del cache, con la grafica sin el dia en curso."""
        hoy = "2026-07-27"
        estado = {"bars": [LvlBar("2026-07-24", 101, 99, 100)]}

        def fetch(t, days):
            return estado["bars"]

        cached_daily_bars("D", now=datetime(2026, 7, 27, 9, 31, tzinfo=ET), fetch=fetch)
        estado["bars"] = estado["bars"] + [LvlBar(hoy, 105, 99, 104)]
        r = cached_daily_bars("D", now=datetime(2026, 7, 27, 15, 0, tzinfo=ET), fetch=fetch)
        assert r[-1].time == hoy, "la grafica se quedo sin el dia en curso"

    def test_el_cache_se_recorta_por_FECHA_no_por_numero_de_barras(self):
        # `days` son dias de CALENDARIO —fetch_daily_bars hace
        # end - timedelta(days=days)— y en 30 dias de calendario caben ~21
        # barras, no 30. Una version anterior hacia bars[-days:] y devolvia 30
        # barras, o sea 41 dias de calendario: mas historico del pedido.
        cerrada = datetime(2026, 7, 27, 18, tzinfo=ET)

        def habiles(days):
            fin = datetime(2026, 7, 27).date()
            d, out = fin - timedelta(days=days), []
            while d <= fin:
                if d.weekday() < 5:
                    out.append(LvlBar(d.isoformat(), 101, 99, 100))
                d += timedelta(days=1)
            return out

        def fetch(t, days):
            return habiles(days)

        largo = cached_daily_bars("E", 365, now=cerrada, fetch=fetch)
        corto = cached_daily_bars("E", 30, now=cerrada, fetch=fetch)   # del cache
        assert len(largo) == len(habiles(365))
        assert len(corto) == len(habiles(30)), "recorto por numero de barras"
        assert corto[0].time >= "2026-06-27"

    def test_un_cache_CORTO_no_se_sirve_a_quien_pide_LARGO(self):
        """El peor de los tres: el analisis correria sobre 21 barras creyendo
        tener 261, sin un solo aviso."""
        cerrada = datetime(2026, 7, 27, 18, tzinfo=ET)
        n = []

        def fetch(t, days):
            n.append(days)
            return _barras(21 if days <= 30 else 261)

        corto = cached_daily_bars("F", 30, now=cerrada, fetch=fetch)
        largo = cached_daily_bars("F", 365, now=cerrada, fetch=fetch)
        assert len(corto) == 21
        assert len(largo) == 261, "se sirvio el cache corto y trunco el historico"
        assert n == [30, 365], "no refresco"

    def test_pero_el_cache_LARGO_si_sirve_al_corto(self):
        cerrada = datetime(2026, 7, 27, 18, tzinfo=ET)
        n = []

        def fetch(t, days):
            n.append(days)
            return _barras(60)

        cached_daily_bars("G", 365, now=cerrada, fetch=fetch)
        cached_daily_bars("G", 30, now=cerrada, fetch=fetch)
        assert n == [365], "pidio a la red teniendo un cache que cubria la ventana"
