"""Port de `barsStore.ts`.

El original no trae test propio (su consumidor es Wheel), así que estos cubren
su contrato tal cual: cache por día de mercado, sin más reglas.

Cuatro reglas que llegué a añadir aquí —horario de sesión, campo `days`,
recorte de la ventana, usar cache viejo si falla la red— están **quitadas**.
Cada una parecía una mejora y cada una trajo su propio fallo; ninguna estaba en
el original. Su cabecera ya avisaba de que este store no se enchufa fuera de
Wheel, y ahí estaba la respuesta.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wbj.tito.bars_store import cached_daily_bars, load_bars, save_bars
from wbj.tito.levels import LvlBar

AYER = datetime(2026, 7, 30, 21, 0, tzinfo=timezone.utc)
HOY = datetime(2026, 7, 31, 21, 0, tzinfo=timezone.utc)   # 17:00 ET


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))


def _barras(n=5, base=100.0):
    return [LvlBar(time=(datetime(2026, 7, 27) + timedelta(days=i)).date().isoformat(),
                   high=base + i + 1, low=base + i - 1, close=base + i)
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

    def test_el_saneado_del_ticker_es_el_suyo(self):
        # `ticker.trim().toUpperCase().replace(/[^A-Z0-9._-]/g, "")` — el mismo
        # saneado literal que `stores._file_for`, sin guardas anadidas.
        from wbj.tito.bars_store import _file_for

        assert _file_for("demo").name == "DEMO.json"
        assert _file_for(" demo ").name == "DEMO.json"
        assert _file_for("BRK.B").name == "BRK.B.json"
        assert _file_for("brk/b").name == "BRKB.json"
        assert _file_for("!!!").name == ".json"       # como el suyo
        assert _file_for("").name == ".json"

    def test_el_regex_ya_cierra_la_travesia_de_rutas(self):
        # Sin guarda extra: su propio regex borra las barras.
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

    def test_el_json_del_cache_es_estricto(self):
        # Un NaN en una barra no puede dejar el archivo ilegible para el resto
        # del mundo (el mismo hallazgo que en store.ts).
        import json

        from wbj.tito.stores import data_dir

        save_bars("NAN", [LvlBar("2026-07-30", float("nan"), 1, float("inf"))], HOY)
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


class TestBugsDeVictorReplicados:
    """Dos bugs de su `barsStore.ts`, replicados A PROPOSITO.

    `loadBars` hace `JSON.parse(raw) as BarsFile` a secas, y ese `as` es una
    afirmacion para el compilador, no una comprobacion. Estos tests fijan SU
    comportamiento exacto, medido ejecutando su archivo en Node — para que el
    port sea identico y para que, si alguien lo cambia, se vea que se esta
    separando del original a sabiendas.

    El arreglo propuesto para el upstream esta en
    engine/scripts/upstream-tito-barsstore.patch. Aqui no se aplica: el modulo
    no lo llama nadie (su cabecera dice que solo lo usa Wheel), asi que el
    riesgo esta acotado y la fidelidad pesa mas.
    """

    def _escribe(self, nombre, contenido):
        from wbj.tito.stores import data_dir

        p = data_dir() / "bars"
        p.mkdir(parents=True, exist_ok=True)
        (p / nombre).write_text(contenido, encoding="utf-8")

    def test_BUG1_un_cache_del_dia_sin_campo_bars_lanza(self):
        # El suyo: `cached.bars.length` sobre undefined → TypeError, y el
        # `catch` de loadBars ya quedo atras, asi que sube al llamador.
        self._escribe("A.json", '{"ticker":"A","date":"2026-07-31"}')
        with pytest.raises(TypeError):
            cached_daily_bars("A", now=HOY, fetch=lambda t, d: _barras(4))

    def test_BUG2_un_bars_de_texto_se_devuelve_como_si_fueran_barras(self):
        # El suyo: `"texto".length > 0` es cierto, la guarda pasa y el llamador
        # recibe un string donde espera DailyBar[]. Sin excepcion.
        self._escribe("B.json", '{"ticker":"B","date":"2026-07-31","bars":"texto"}')
        r = cached_daily_bars("B", now=HOY, fetch=lambda t, d: _barras(4))
        assert r == "texto"

    def test_BUG2_con_un_objeto_devuelve_el_objeto(self):
        self._escribe("C.json",
                      '{"ticker":"C","date":"2026-07-31","bars":{"length":9}}')
        r = cached_daily_bars("C", now=HOY, fetch=lambda t, d: _barras(4))
        assert r == {"length": 9}

    def test_load_bars_no_valida_nada(self):
        # `JSON.parse(raw) as BarsFile`: lo que haya en el disco pasa tal cual.
        self._escribe("E.json", '{"ticker":"E","date":"x","bars":"txt"}')
        assert load_bars("E").bars == "txt"
        self._escribe("F.json", '[1,2]')
        f = load_bars("F")
        assert f is not None and f.ticker is None and f.bars is None
        self._escribe("G.json", 'null')
        assert load_bars("G") is None      # `catch` → null


class TestNoEstaCableadoFueraDeWheel:
    """La instrucción está en su cabecera: *"`fetchDailyBars` sigue sin cache
    para el resto de rutas: este store es nuevo y en v1 solo lo usa Wheel"*."""

    def test_la_ruta_de_proyecciones_pide_en_directo(self):
        from pathlib import Path

        api = (Path(__file__).resolve().parents[3] / "vertex_api.py").read_text()
        bloque = api[api.index("def _tito_chain_and_bars"):api.index("def _tito_memory")]
        assert "cached_daily_bars" not in bloque
        assert "fetch_daily_bars(ticker)" in bloque
