"""Port de `barsStore.ts`, en sus dos capas.

El original no trae test propio (su consumidor es Wheel), así que estos cubren:

- `TestCachedDailyBars` — SU contrato tal cual, cache por día de mercado y sin
  más reglas. Es lo que verifica `diff_bars.sh` contra su archivo en Node.
- `TestLasDosDeLoadBarsSeQuedanComoEl` — los dos bugs de su `as BarsFile`,
  portados tal cual: el cache sin campo `bars` lanza y el `bars` de texto cuela.
- `TestElBordeDeLasBarras` — `borde.barras_utiles`, la guarda de Vertex, en el
  sitio donde su pipeline la tiene (sus barras vienen de `fetchDailyBars`, no
  de un JSON ajeno leído del disco).
- `TestPoliticaDelPanel` — `daily_bars_for_panel`, que es política de Vertex y
  no suya: los tres agujeros que su regla de cache deja en un panel en vivo.

Las reglas del panel viven en su propia función a propósito. Llegué a meterlas
dentro de `cached_daily_bars` y eso rompía la única forma de comprobar que el
port es fiel — ejecutar su archivo y comparar. Separadas, su función sigue
midiéndose contra la suya y la política se prueba aparte.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from wbj.tito.bars_store import (
    cached_daily_bars,
    daily_bars_for_panel,
    load_bars,
    save_bars,
)
from wbj.tito.levels import LvlBar
from wbj.tito.massive import DailyBar

AYER = datetime(2026, 7, 30, 21, 0, tzinfo=timezone.utc)
HOY = datetime(2026, 7, 31, 21, 0, tzinfo=timezone.utc)   # 17:00 ET


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))


def _barras_hasta(ultimo: str, n: int = 30, base: float = 100.0):
    """`n` barras diarias consecutivas que terminan en `ultimo` (inclusive)."""
    fin = datetime.fromisoformat(ultimo).date()
    return [DailyBar(time=(fin - timedelta(days=n - 1 - i)).isoformat(),
                     open=base + i - 0.5, high=base + i + 1,
                     low=base + i - 1, close=base + i)
            for i in range(n)]


def _barras(n=5, base=100.0):
    return [DailyBar(time=(datetime(2026, 7, 27) + timedelta(days=i)).date().isoformat(),
                     open=base + i - 0.5, high=base + i + 1,
                     low=base + i - 1, close=base + i)
            for i in range(n)]


class TestGuardarYLeer:
    def test_round_trip(self):
        save_bars("DEMO", _barras(), HOY)
        f = load_bars("DEMO")
        assert f is not None
        assert f.ticker == "DEMO"
        assert f.date == "2026-07-31"          # día de mercado (ET)
        assert f.bars == _barras()

    def test_sin_cache_devuelve_none(self):
        assert load_bars("NUNCA") is None

    def test_el_ticker_se_normaliza(self):
        save_bars("demo", _barras(), HOY)
        assert load_bars("DEMO").ticker == "DEMO"

    def test_el_saneado_es_el_MISMO_que_el_de_stores(self):
        """Un solo saneado para las dos rutas de disco.

        Su `fileFor` es el mismo regex en `barsStore.ts` y en `store.ts`, así
        que se comparte `_sanea_ticker` en vez de copiarlo: dos saneados
        distintos para la misma entrada, en el mismo repo, es una trampa por sí
        sola. Literal en los dos, y el rechazo del ticker vacío vive en
        `borde.ticker_valido`.
        """
        from wbj.tito.bars_store import _file_for
        from wbj.tito.stores import _file_for as _trades_file_for

        assert _file_for("demo").name == "DEMO.json"
        assert _file_for(" demo ").name == "DEMO.json"
        assert _file_for("BRK.B").name == "BRK.B.json"
        assert _file_for("brk/b").name == "BRKB.json"
        # …y el mismo saneado que en la memoria de trades, carácter por carácter
        for t in ("!!!", "@@@", "", "   ", "...", "A" * 200, "brk/b", " demo "):
            assert _file_for(t).name == _trades_file_for(t).name
        assert load_bars("!!!") is None            # leer nunca lanza

    def test_el_regex_ya_cierra_la_travesia_de_rutas(self):
        # Esto si es suyo y se conserva: su propio regex borra las barras.
        from wbj.tito.bars_store import _file_for
        from wbj.tito.stores import data_dir

        d = _file_for("../../ETC/X")
        assert d.name == "....ETCX.json"
        assert d.resolve().parent == (data_dir() / "bars").resolve()

    def test_el_ticker_guardado_NO_se_recorta(self):
        # `ticker.toUpperCase()` sin trim: el nombre de archivo si se recorta
        # (fileFor hace .trim()), pero el campo del payload no.
        save_bars(" demo ", _barras(), HOY)
        assert load_bars("DEMO").ticker == " DEMO "

    def test_una_barra_corrupta_se_devuelve_tal_cual(self):
        # Literal: en TS los objetos del JSON ya SON `DailyBar` por su forma y
        # uno malformado se queda en la lista con los campos en `undefined`.
        # Filtrarlo cambiaría `bars.length`, que es lo único que mira su
        # `cachedDailyBars`. Quien decide sobre una serie ilegible es
        # `borde.barras_utiles`, no `load_bars`.
        import json

        from wbj.tito.borde import barras_utiles
        from wbj.tito.stores import data_dir

        p = data_dir() / "bars"
        p.mkdir(parents=True, exist_ok=True)
        (p / "DEMO.json").write_text(json.dumps({
            "ticker": "DEMO", "date": "2026-07-31",
            "bars": [{"time": "2026-07-30", "high": 2, "low": 1, "close": 1.5},
                     "basura", None, {"time": "x"}, 42],
        }), encoding="utf-8")
        crudo = load_bars("DEMO")
        assert len(crudo.bars) == 5
        assert crudo.bars[1] == "basura" and crudo.bars[2] is None
        assert barras_utiles(crudo) is None   # el borde la trata como sin cache

    def test_un_archivo_roto_se_lee_como_sin_cache(self):
        from wbj.tito.stores import data_dir

        p = data_dir() / "bars"
        p.mkdir(parents=True, exist_ok=True)
        (p / "DEMO.json").write_text("{no soy json", encoding="utf-8")
        assert load_bars("DEMO") is None

    def test_el_json_del_cache_es_estricto(self):
        # Un NaN en una barra no puede dejar el archivo ilegible para el resto
        # del mundo (el mismo hallazgo que en store.ts).
        import json

        from wbj.tito.stores import data_dir

        save_bars("NAN", [DailyBar("2026-07-30", 1, float("nan"), 1, float("inf"))], HOY)
        crudo = (data_dir() / "bars" / "NAN.json").read_text(encoding="utf-8")

        def estricto(c):
            raise ValueError(c)

        json.loads(crudo, parse_constant=estricto)
        assert "NaN" not in crudo


class TestCachedDailyBars:
    """El contrato de Víctor, entero y sin añadidos:

        const today = marketDateStr(now);
        const cached = await loadBars(ticker);
        if (cached && cached.date === today && cached.bars.length > 0)
            return cached.bars;
        const bars = await fetchDailyBars(ticker, days).catch(() => []);
        if (bars.length > 0) await saveBars(ticker, bars, now);
        return bars;
    """

    def test_la_primera_llamada_pide_y_guarda(self):
        llamadas = []

        def fetch(t, days):
            llamadas.append((t, days))
            return _barras()

        out = cached_daily_bars("DEMO", now=HOY, fetch=fetch)
        assert out == _barras()
        assert llamadas == [("DEMO", 365)]
        assert load_bars("DEMO").bars == _barras()

    def test_la_segunda_sale_del_cache(self):
        llamadas = []

        def fetch(t, days):
            llamadas.append(t)
            return _barras()

        for _ in range(4):
            cached_daily_bars("DEMO", now=HOY, fetch=fetch)
        assert llamadas == ["DEMO"], "el cache no ahorró la llamada"

    def test_el_cache_vale_para_TODO_el_dia_de_mercado(self):
        """Sin regla de horario: es el contrato de Víctor.

        Llegué a añadir una —"solo con la sesión cerrada"— porque la barra del
        día en curso es parcial. Trajo dos fallos seguidos y no estaba en el
        original. Su cabecera ya decía que este store no se usa fuera de Wheel,
        que es donde eso no importa.
        """
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        llamadas = []

        def fetch(t, days):
            llamadas.append(t)
            return _barras()

        for hora in (8, 11, 15, 18):        # pre-market, sesión, sesión, cerrada
            cached_daily_bars("DEMO", now=datetime(2026, 7, 31, hora, tzinfo=et),
                              fetch=fetch)
        assert len(llamadas) == 1

    def test_un_cache_de_ayer_no_vale_para_hoy(self):
        llamadas = []

        def fetch(t, days):
            llamadas.append(t)
            return _barras()

        cached_daily_bars("DEMO", now=AYER, fetch=fetch)
        cached_daily_bars("DEMO", now=HOY, fetch=fetch)
        assert len(llamadas) == 2

    def test_un_cache_vacio_no_cuenta_como_cache(self):
        # `cached.bars.length > 0` del original.
        llamadas = []

        def fetch(t, days):
            llamadas.append(t)
            return []

        cached_daily_bars("DEMO", now=HOY, fetch=fetch)
        cached_daily_bars("DEMO", now=HOY, fetch=fetch)
        assert len(llamadas) == 2

    def test_si_falla_la_red_devuelve_vacio(self):
        # `.catch(() => [])` — literal, sin usar cache viejo. Llegué a
        # devolverlo ("mejor viejo que nada") y tampoco estaba en el original.
        def boom(t, days):
            raise RuntimeError("Massive caído")

        assert cached_daily_bars("DEMO", now=HOY, fetch=boom) == []

    def test_si_falla_la_red_no_borra_el_cache_que_ya_habia(self):
        save_bars("DEMO", _barras(), AYER)

        def boom(t, days):
            raise RuntimeError("Massive caído")

        cached_daily_bars("DEMO", now=HOY, fetch=boom)
        assert load_bars("DEMO").bars == _barras()   # sigue ahí, intacto

    def test_el_days_se_pasa_tal_cual_a_la_red(self):
        vistos = []

        def fetch(t, days):
            vistos.append(days)
            return _barras()

        cached_daily_bars("A", 30, now=HOY, fetch=fetch)
        cached_daily_bars("B", now=HOY, fetch=fetch)
        assert vistos == [30, 365]


class TestLasDosDeLoadBarsSeQuedanComoEl:
    """Los dos bugs de su `barsStore.ts`, portados tal cual.

    `loadBars` hace `JSON.parse(raw) as BarsFile` a secas, y ese `as` es una
    afirmacion para el compilador, no una comprobacion: lo que haya en el
    archivo entra tal cual y `cachedDailyBars` lo usa sin mirar.

    El arreglo propuesto para el upstream esta en
    engine/scripts/upstream-tito-barsstore.patch. Quien no se los come es
    `daily_bars_for_panel`, que lee por `borde.barras_utiles` — ver
    `TestElBordeDeLasBarras`.
    """

    def _escribe(self, nombre, contenido):
        from wbj.tito.stores import data_dir

        p = data_dir() / "bars"
        p.mkdir(parents=True, exist_ok=True)
        (p / nombre).write_text(contenido, encoding="utf-8")

    def test_BUG1_un_cache_del_dia_sin_campo_bars_lanza(self):
        # `cached.bars.length` sobre undefined → TypeError, y el `catch` de
        # loadBars ya quedo atras, asi que sube al llamador.
        self._escribe("A.json", '{"ticker":"A","date":"2026-07-31"}')
        assert load_bars("A").bars is None
        with pytest.raises(TypeError):
            cached_daily_bars("A", now=HOY, fetch=lambda t, d: _barras(4))

    def test_BUG2_un_bars_de_texto_se_devuelve_como_barras(self):
        # `"texto".length > 0` es cierto, la guarda pasa y el llamador recibe un
        # string donde espera DailyBar[]. Sin excepcion: el analisis sigue,
        # sobre basura y en silencio. Es el feo de los dos.
        self._escribe("B.json", '{"ticker":"B","date":"2026-07-31","bars":"texto"}')
        assert cached_daily_bars("B", now=HOY, fetch=lambda t, d: _barras(4)) == "texto"

    def test_BUG2_un_objeto_con_length_tambien_cuela(self):
        self._escribe("C.json",
                      '{"ticker":"C","date":"2026-07-31","bars":{"length":9}}')
        # En JS `({length:9}).length > 0` pasa la guarda. En Python `len()` de
        # un dict es su numero de claves: 1 > 0, tambien pasa. Mismo desenlace.
        assert cached_daily_bars("C", now=HOY, fetch=lambda t, d: _barras(4)) \
            == {"length": 9}

    def test_el_None_sale_donde_sale_el_suyo(self):
        # Su `catch` cubre exactamente tres cosas: archivo que no existe, JSON
        # roto y `JSON.parse("null")`. Ni una mas.
        for nombre, crudo in (("G.json", 'null'), ("H.json", '{no soy json')):
            self._escribe(nombre, crudo)
            assert load_bars(nombre[0]) is None, nombre
        assert load_bars("NOEXISTE") is None
        # Un array pelado NO da None: da un BarsFile con los campos en None,
        # igual que su `cached.date` seria `undefined`.
        self._escribe("F.json", '[1,2]')
        f = load_bars("F")
        assert f is not None and f.date is None and f.bars is None

    def test_el_archivo_corrupto_NO_se_repara_solo(self):
        # `bars: "texto"` pasa la guarda, asi que nunca se pide a la red y
        # nunca se reescribe. El cache se queda envenenado para ese dia.
        self._escribe("D.json", '{"ticker":"D","date":"2026-07-31","bars":"texto"}')
        cached_daily_bars("D", now=HOY, fetch=lambda t, d: _barras(4))
        assert load_bars("D").bars == "texto"


class TestElBordeDeLasBarras:
    """`borde.barras_utiles` — la guarda, en el sitio donde su pipeline la tiene.

    Sus barras salen de `fetchDailyBars`, que devuelve `DailyBar[]` construidos
    por el, no JSON ajeno. Vertex sí lee JSON ajeno (el cache en disco), asi que
    el borde se escribe.
    """

    def _escribe(self, nombre, contenido):
        from wbj.tito.stores import data_dir

        p = data_dir() / "bars"
        p.mkdir(parents=True, exist_ok=True)
        (p / nombre).write_text(contenido, encoding="utf-8")

    @pytest.mark.parametrize("crudo", [
        '{"ticker":"X","date":"2026-07-31"}',                      # BUG 1
        '{"ticker":"X","date":"2026-07-31","bars":"texto"}',       # BUG 2
        '{"ticker":"X","date":"2026-07-31","bars":{"length":9}}',
        '{"ticker":"X","date":"2026-07-31","bars":[]}',
        '{"ticker":"X","date":"2026-07-31","bars":[{"time":"x"},1]}',
        '[1,2]',
        'null',
        '{no soy json',
    ])
    def test_un_cache_ilegible_es_un_cache_que_no_esta(self, crudo):
        from wbj.tito.borde import barras_utiles

        self._escribe("X.json", crudo)
        assert barras_utiles(load_bars("X")) is None

    def test_un_cache_bueno_pasa_entero(self):
        from wbj.tito.borde import barras_utiles

        save_bars("OK", _barras(5), HOY)
        assert barras_utiles(load_bars("OK")) == _barras(5)

    def test_una_sola_barra_rota_invalida_la_serie(self):
        # Un cache a medias no es un ahorro: el historico se usa entero para
        # maximos, minimos y toques. Mejor una peticion de mas.
        import json

        from wbj.tito.borde import barras_utiles

        self._escribe("Y.json", json.dumps({
            "ticker": "Y", "date": "2026-07-31",
            "bars": [{"time": "2026-07-30", "high": 2, "low": 1, "close": 1.5},
                     {"time": "2026-07-31", "high": "no"}],
        }))
        assert barras_utiles(load_bars("Y")) is None

    def test_el_panel_no_se_come_ninguno_de_los_dos_bugs(self):
        from wbj.tito.bars_store import daily_bars_for_panel

        for nombre, crudo in (("P.json", '{"ticker":"P","date":"2026-07-31"}'),
                              ("Q.json",
                               '{"ticker":"Q","date":"2026-07-31","bars":"texto"}')):
            self._escribe(nombre, crudo)
            r = daily_bars_for_panel(nombre[0], now=HOY, fetch=lambda t, d: _barras(4))
            assert r == _barras(4)


ET = ZoneInfo("America/New_York")


class TestPoliticaDelPanel:
    """`daily_bars_for_panel` — politica de Vertex, no de Victor.

    Su `cachedDailyBars` cachea por dia de mercado y punto. Los cuatro casos de
    abajo son los agujeros que eso deja en un panel en vivo, y son los mismos
    que aparecieron la primera vez que intente enchufar el store.
    """

    def _fetch(self, bars, llamadas):
        def f(t, d):
            llamadas.append(t)
            return bars
        return f

    def test_1_a_media_sesion_no_se_cachea_la_vela_parcial(self):
        # 11:00 ET del viernes: la barra de hoy esta a medio hacer. La ultima
        # sesion cerrada es la de ayer, asi que la serie de ayer sirve y la
        # parcial no se sella.
        llamadas = []
        ayer = _barras_hasta("2026-07-30")
        out = daily_bars_for_panel("X", now=datetime(2026, 7, 31, 11, tzinfo=ET),
                                   fetch=self._fetch(ayer, llamadas))
        assert out == ayer and len(llamadas) == 1
        # …y la segunda consulta ya sale del cache
        daily_bars_for_panel("X", now=datetime(2026, 7, 31, 15, tzinfo=ET),
                             fetch=self._fetch(ayer, llamadas))
        assert len(llamadas) == 1

    def test_2_si_Massive_publica_tarde_se_vuelve_a_pedir(self):
        # 18:00 ET del viernes y la serie NO trae la barra del viernes: no se
        # cachea, porque sellarla dejaria el dia fuera para siempre.
        llamadas = []
        vieja = _barras_hasta("2026-07-30")
        noche = datetime(2026, 7, 31, 18, tzinfo=ET)
        for _ in range(3):
            daily_bars_for_panel("X", now=noche, fetch=self._fetch(vieja, llamadas))
        assert len(llamadas) == 3, "sello una serie sin la barra de hoy"
        assert load_bars("X") is None

    def test_2b_y_en_cuanto_la_publica_se_cachea(self):
        llamadas = []
        noche = datetime(2026, 7, 31, 18, tzinfo=ET)
        completa = _barras_hasta("2026-07-31")
        for _ in range(3):
            daily_bars_for_panel("X", now=noche, fetch=self._fetch(completa, llamadas))
        assert len(llamadas) == 1

    def test_3_el_fin_de_semana_el_cache_SI_sirve(self):
        # `market_date` devuelve sabado, que nunca coincide con la fecha del
        # cache: con su regla se perdia el cache justo los dos dias en que las
        # barras no pueden cambiar.
        llamadas = []
        viernes = _barras_hasta("2026-07-31")
        daily_bars_for_panel("X", now=datetime(2026, 7, 31, 18, tzinfo=ET),
                             fetch=self._fetch(viernes, llamadas))
        for cuando in (datetime(2026, 8, 1, 12, tzinfo=ET),    # sabado
                       datetime(2026, 8, 2, 20, tzinfo=ET),    # domingo
                       datetime(2026, 8, 3, 9, tzinfo=ET)):    # lunes pre-market
            out = daily_bars_for_panel("X", now=cuando, fetch=self._fetch(viernes, llamadas))
            assert out == viernes
        assert len(llamadas) == 1, "el cache se perdio el fin de semana"

    def test_4_una_respuesta_truncada_no_acorta_el_historico(self):
        # Una pagina truncada o un rate limit devuelven menos barras. Sellarlas
        # recortaba el historico en silencio, con el ano de barras que necesitan
        # `levels` y el sub-agente 6 dependiendo de el.
        noche = datetime(2026, 7, 31, 18, tzinfo=ET)
        larga = _barras_hasta("2026-07-31", n=250)
        daily_bars_for_panel("X", now=noche, fetch=lambda t, d: larga)
        assert len(load_bars("X").bars) == 250

        corta = _barras_hasta("2026-07-31", n=5)
        from wbj.tito.stores import data_dir
        (data_dir() / "bars" / "X.json").unlink()      # forzar el refetch
        daily_bars_for_panel("X", now=noche, fetch=lambda t, d: larga)
        daily_bars_for_panel("X", now=noche + timedelta(days=3), fetch=lambda t, d: corta)
        assert len(load_bars("X").bars) == 250, "el historico se acorto"

    def test_4b_dos_peticiones_a_la_vez_no_acortan_el_historico(self):
        """La guarda de "no acortar" tiene que decidir DENTRO del cerrojo.

        Comparando contra el `load_bars` que hizo el llamador, dos peticiones
        simultaneas leen el cache vacio, las dos deciden que pueden escribir y
        gana la ultima: una respuesta truncada de 5 barras pisaba una serie
        buena de 250. Es el mismo patron leer-decidir-escribir sin cerrojo que
        costo ocho hallazgos en `store.ts`.
        """
        import threading

        noche = datetime(2026, 7, 31, 18, tzinfo=ET)
        leyeron = threading.Barrier(2)
        larga_ok = threading.Event()

        def larga():
            daily_bars_for_panel(
                "RACE", now=noche,
                fetch=lambda t, d: (leyeron.wait(), _barras_hasta("2026-07-31", 250))[1])
            larga_ok.set()

        def corta():
            def f(t, d):
                leyeron.wait()
                larga_ok.wait(10)          # la truncada escribe DESPUES
                return _barras_hasta("2026-07-31", 5)

            daily_bars_for_panel("RACE", now=noche, fetch=f)

        hs = [threading.Thread(target=larga), threading.Thread(target=corta)]
        for h in hs:
            h.start()
        for h in hs:
            h.join(timeout=15)
        assert len(load_bars("RACE").bars) == 250, "la truncada piso el historico"

    def test_el_corte_no_depende_de_la_TZ_del_servidor(self):
        # Un `now` sin zona se lee como UTC, igual que en `occ.market_date`. Si
        # no, la validez del cache dependeria de la TZ de la maquina.
        import os
        import time

        from wbj.tito.bars_store import _ultima_sesion_cerrada

        previo = os.environ.get("TZ")
        vistos = set()
        try:
            for tz in ("UTC", "America/New_York", "Asia/Tokyo"):
                os.environ["TZ"] = tz
                time.tzset()
                vistos.add(_ultima_sesion_cerrada(datetime(2026, 7, 31, 22, 0)))
        finally:
            if previo is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previo
            time.tzset()
        assert len(vistos) == 1, f"el corte cambio con la TZ: {vistos}"

    def test_si_falla_la_red_el_error_sube(self):
        # A diferencia de `cached_daily_bars` (que hace `.catch(() => [])`), aqui
        # el fallo tiene que llegar al llamador: `_tito_chain_and_bars` convierte
        # un fallo de Massive en un error del endpoint, no en un score sin datos.
        def boom(t, d):
            raise RuntimeError("Massive caido")

        with pytest.raises(RuntimeError):
            daily_bars_for_panel("X", now=HOY, fetch=boom)


class TestCableadoEnProyecciones:
    """Lo contrario de lo que decia el test anterior: ahora SI esta cableado."""

    def test_la_ruta_de_proyecciones_usa_el_cache(self):
        from pathlib import Path

        api = (Path(__file__).resolve().parents[3] / "vertex_api.py").read_text()
        bloque = api[api.index("def _tito_chain_and_bars"):api.index("def _tito_memory")]
        assert "daily_bars_for_panel(ticker)" in bloque
        assert "fetch_daily_bars(ticker)" not in bloque

    def test_el_health_check_sigue_pidiendo_en_directo(self):
        # Su trabajo es probar que Massive responde; una respuesta del cache
        # taparia justo la caida que busca.
        from pathlib import Path

        api = (Path(__file__).resolve().parents[3] / "vertex_api.py").read_text()
        bloque = api[api.index('add("massive.cadena"'):api.index("# 3. MarketSnack")]
        assert "fetch_daily_bars(tk)" in bloque
        assert "massive.barras.cache" in bloque
