"""Tests de la capa de persistencia (`wbj.tito.stores`).

Port del comportamiento de chainStore.ts / ivStore.ts / predictionStore.ts,
más los 5 casos de `predictionStore.test.ts`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from wbj.tito.levels import LvlBar
from wbj.tito.stores import (
    PredictionSnapshot,
    calibration_from_review,
    load_chain_history,
    load_iv_history,
    load_journal,
    load_trades,
    review_predictions,
    save_chain_snapshot,
    save_iv_snapshot,
    save_prediction,
    save_trades,
)
from wbj.tito.structure import ChainRow
from wbj.tito.flow import FlowFlags, FlowRow, TradeScores

NOW = datetime(2026, 7, 30, 16, 30, tzinfo=timezone.utc)  # 12:30 ET


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    """Cada test escribe en su propio directorio."""
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))


def _chain_rows():
    return [
        ChainRow(contract_type="call", expiration="2026-09-18", strike=100.0,
                 open_interest=500, volume=100, notional_value=5_000_000),
        ChainRow(contract_type="put", expiration="2026-09-18", strike=100.0,
                 open_interest=300, volume=50, notional_value=3_000_000),
    ]


def _flow_row(id_: int, ts: str) -> FlowRow:
    return FlowRow(
        id=id_, symbol="X", underlying="X", type="call", strike=100.0,
        expiration="2026-09-18", dte=50, price=2.0, size=10, side="AT_ASK",
        aggression="ask", asset_price=100.0, bid=1.9, ask=2.1, premium=200_000,
        delta=0.6, gamma=0.03, theta=-0.05, vega=0.1, theta_pct_daily=2.5,
        iv=0.45, open_interest=1000, volume=500, score=8, sentiment="bullish",
        timestamp=ts, condition_code=None, condition_name=None,
        flags=FlowFlags(), scores=TradeScores(),
    )


class TestChainStore:
    def test_guarda_y_recupera_una_foto(self):
        n = save_chain_snapshot("DEMO", _chain_rows(), NOW)
        assert n == 1
        hist = load_chain_history("DEMO")
        assert hist[0]["date"] == "2026-07-30"
        assert hist[0]["strikes"][0]["call_oi"] == 500
        assert hist[0]["strikes"][0]["put_oi"] == 300

    def test_dedupe_por_dia_de_mercado(self):
        save_chain_snapshot("DEMO", _chain_rows(), NOW)
        save_chain_snapshot("DEMO", _chain_rows(), NOW)
        assert len(load_chain_history("DEMO")) == 1

    def test_dias_distintos_se_acumulan(self):
        save_chain_snapshot("DEMO", _chain_rows(), NOW)
        save_chain_snapshot("DEMO", _chain_rows(), NOW - timedelta(days=1))
        assert len(load_chain_history("DEMO")) == 2

    def test_sin_historial_devuelve_vacio(self):
        assert load_chain_history("NUNCA") == []


class TestIvStore:
    def test_acumula_una_muestra_por_dia(self):
        for i in range(5):
            save_iv_snapshot("DEMO", 40 + i, NOW - timedelta(days=i))
        hist = load_iv_history("DEMO")
        assert len(hist) == 5
        assert hist[0]["date"] < hist[-1]["date"]  # ordenado

    def test_formato_compatible_con_iv_context_score(self):
        save_iv_snapshot("DEMO", 47.5, NOW)
        h = load_iv_history("DEMO")[0]
        assert set(h) == {"date", "avg_iv"}
        assert h["avg_iv"] == 47.5

    def test_ignora_iv_no_positiva(self):
        save_iv_snapshot("DEMO", 0, NOW)
        assert load_iv_history("DEMO") == []

    def test_a_los_60_dias_desplaza_al_proxy(self):
        # El umbral vive en ivcontext.MIN_IV_HISTORY_DAYS; aqui se comprueba que
        # el store puede alcanzarlo.
        from wbj.tito.ivcontext import MIN_IV_HISTORY_DAYS
        for i in range(MIN_IV_HISTORY_DAYS):
            save_iv_snapshot("DEMO", 40 + i * 0.1, NOW - timedelta(days=i))
        assert len(load_iv_history("DEMO")) >= MIN_IV_HISTORY_DAYS


class TestTradesStore:
    """Port de `store.ts`. La forma es la suya: envoltorio `StoredTrades`,
    `SaveResult` de tres campos, dedupe por id y tope por cantidad."""

    def test_acumula_y_deduplica_por_id(self):
        rows = [_flow_row(1, "2026-07-30T15:00:00Z"), _flow_row(2, "2026-07-30T15:01:00Z")]
        save_trades("DEMO", rows)
        save_trades("DEMO", rows)  # mismo lote otra vez
        assert len(load_trades("DEMO").trades) == 2

    def test_trades_nuevos_no_pisan_los_viejos(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-29T15:00:00Z")])
        save_trades("DEMO", [_flow_row(2, "2026-07-30T15:00:00Z")])
        assert {t["id"] for t in load_trades("DEMO").trades} == {1, 2}

    def test_guarda_el_analisis_completo_no_un_recorte(self):
        # "los trades vienen ya clasificados/puntuados, así que se guarda el
        # análisis completo" — el archivo no puede perder score/flags/greeks.
        save_trades("DEMO", [_flow_row(1, "2026-07-30T15:00:00Z")])
        t = load_trades("DEMO").trades[0]
        assert set(t) >= {"id", "timestamp", "type", "strike", "expiration",
                          "asset_price", "premium", "aggression",
                          "score", "sentiment", "delta", "gamma", "theta", "vega",
                          "iv", "open_interest", "volume", "flags", "scores",
                          "expiry_status", "condition_code", "unusual", "interesting"}

    def test_el_analisis_mas_reciente_gana(self):
        # expiry_status se recalcula cada corrida: un contrato vigente pasa a
        # expirado y el archivo tiene que reflejarlo, no conservar la foto vieja.
        from dataclasses import replace
        viejo = replace(_flow_row(1, "2026-07-30T15:00:00Z"), expiry_status="vigente")
        nuevo = replace(_flow_row(1, "2026-07-30T15:00:00Z"), expiry_status="expirado")
        save_trades("DEMO", [viejo])
        save_trades("DEMO", [nuevo])
        assert load_trades("DEMO").trades[0]["expiry_status"] == "expirado"

    def test_el_envoltorio_dice_de_quien_es_y_cuando(self):
        st = save_trades("demo", [_flow_row(1, "2026-07-30T15:00:00Z")])
        assert st.total == 1 and st.added == 1
        stored = load_trades("DEMO")
        assert stored.ticker == "DEMO"
        assert stored.updated_at.endswith("Z")

    def test_save_result_cuenta_solo_las_nuevas(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-30T15:00:00Z")])
        r = save_trades("DEMO", [_flow_row(1, "2026-07-30T15:00:00Z"),
                                 _flow_row(2, "2026-07-30T15:01:00Z")])
        assert r.added == 1
        assert r.total == 2

    def test_first_seen_es_el_trade_mas_antiguo(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-28T15:00:00Z"),
                             _flow_row(2, "2026-07-30T15:00:00Z")])
        r = save_trades("DEMO", [_flow_row(3, "2026-07-29T15:00:00Z")])
        assert r.first_seen == "2026-07-28T15:00:00Z"

    def test_ordena_de_lo_mas_nuevo_a_lo_mas_viejo(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-28T15:00:00Z"),
                             _flow_row(2, "2026-07-30T15:00:00Z"),
                             _flow_row(3, "2026-07-29T15:00:00Z")])
        assert [t["id"] for t in load_trades("DEMO").trades] == [2, 3, 1]

    def test_recorta_a_max_per_ticker_tirando_lo_mas_viejo(self):
        from wbj.tito.stores import MAX_PER_TICKER
        base = datetime(2020, 1, 1, tzinfo=timezone.utc)
        rows = [_flow_row(i, (base + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"))
                for i in range(MAX_PER_TICKER + 10)]
        r = save_trades("DEMO", rows)
        assert r.total == MAX_PER_TICKER
        assert r.added == MAX_PER_TICKER + 10        # `added` cuenta lo entrante, no lo que sobrevive
        ids = [t["id"] for t in load_trades("DEMO").trades]
        assert ids[0] == MAX_PER_TICKER + 9          # lo más nuevo arriba
        assert min(ids) == 10                        # los 10 más viejos se cayeron

    def test_sin_historial_devuelve_none_no_lista_vacia(self):
        assert load_trades("NUNCA") is None

    def test_el_ticker_no_puede_escaparse_del_directorio(self):
        # El ticker es entrada de usuario y acaba siendo una ruta. El saneado de
        # Víctor conserva el punto (`[^A-Z0-9._-]`), así que "../../ETC/PASSWD"
        # queda en "....ETCPASSWD": feo, pero sin barras no hay travesía.
        from wbj.tito.stores import _file_for, data_dir
        destino = _file_for("../../ETC/PASSWD")
        assert destino.name == "....ETCPASSWD.json"
        assert destino.resolve().parent == (data_dir() / "trades").resolve()

        save_trades("../../ETC/PASSWD", [_flow_row(1, "2026-07-30T15:00:00Z")])
        import os
        escritos = [os.path.join(r, f) for r, _, fs in os.walk(data_dir()) for f in fs
                    if not f.endswith(".lock")]
        assert len(escritos) == 1
        assert os.path.realpath(escritos[0]).startswith(str(data_dir().resolve()))

    def test_leer_no_deja_rastro_en_disco(self):
        # Preguntar "¿hay historial?" no puede crear carpetas.
        from wbj.tito.stores import data_dir
        assert load_trades("NUNCA") is None
        assert not (data_dir() / "trades").exists()

    def test_un_nan_no_deja_el_archivo_en_json_invalido(self):
        # `JSON.stringify` convierte NaN/Infinity en null y el archivo sigue
        # siendo JSON válido. Escribir `NaN` literal lo relee Python —su
        # json.loads acepta esas constantes— pero lo rechaza cualquier otra
        # cosa que lo abra: jq, un backup, un script de migración.
        import json
        from dataclasses import replace
        from wbj.tito.stores import data_dir

        save_trades("NAN", [
            replace(_flow_row(1, "2026-07-30T15:00:00Z"), iv=float("nan"),
                    delta=float("inf")),
            _flow_row(2, "2026-07-30T15:01:00Z"),
        ])
        crudo = (data_dir() / "trades" / "NAN.json").read_text(encoding="utf-8")
        assert "NaN" not in crudo and "Infinity" not in crudo

        def estricto(c):
            raise ValueError(c)

        json.loads(crudo, parse_constant=estricto)   # no debe lanzar

        trades = {t["id"]: t for t in load_trades("NAN").trades}
        assert len(trades) == 2                       # no se pierde ninguno
        assert trades[1]["iv"] is None                # el no-finito pasa a null
        assert trades[1]["delta"] is None
        assert trades[2]["iv"] == 0.45                # los sanos, intactos

    def test_el_mismo_id_dos_veces_en_la_misma_llamada(self):
        r = save_trades("DUP", [_flow_row(1, "2026-07-30T15:00:00Z"),
                                _flow_row(1, "2026-07-30T15:00:00Z"),
                                _flow_row(2, "2026-07-30T15:01:00Z")])
        assert (r.total, r.added) == (2, 2)

    def test_la_frontera_exacta_del_tope(self):
        from wbj.tito.stores import MAX_PER_TICKER
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def lote(n, off=0):
            return [_flow_row(off + i, (base + timedelta(minutes=off + i))
                              .isoformat().replace("+00:00", "Z")) for i in range(n)]

        assert save_trades("LIM", lote(MAX_PER_TICKER)).total == MAX_PER_TICKER
        assert save_trades("LIM", lote(1, off=99_999)).total == MAX_PER_TICKER
        ids = [t["id"] for t in load_trades("LIM").trades]
        assert 99_999 in ids and 0 not in ids     # se cae el más viejo

    def test_el_orden_es_estable_con_timestamps_identicos(self):
        mismo = "2026-07-30T15:00:00Z"
        save_trades("EST", [_flow_row(i, mismo) for i in (1, 2, 3)])
        primero = [t["id"] for t in load_trades("EST").trades]
        save_trades("EST", [])                    # reescribir no baraja
        assert [t["id"] for t in load_trades("EST").trades] == primero == [1, 2, 3]

    def test_el_cerrojo_anidado_no_cuelga_el_proceso(self):
        # threading.Lock no es reentrante, y flock tampoco entre dos descriptores
        # del mismo proceso: entrar dos veces colgaba el worker para siempre, sin
        # error ni timeout. Hoy ningún camino anida, pero la trampa era cara.
        import threading

        from wbj.tito.stores import _exclusive, _path

        p = _path("iv", "REENT")
        ok = []

        def anidado():
            with _exclusive(p):
                with _exclusive(p):
                    ok.append(True)

        t = threading.Thread(target=anidado, daemon=True)
        t.start()
        t.join(timeout=10)
        assert not t.is_alive(), "DEADLOCK: el cerrojo anidado colgó el hilo"
        assert ok == [True]

    def test_el_cerrojo_sigue_siendo_exclusivo_entre_hilos(self):
        # La reentrada no puede haber aflojado la exclusividad de verdad.
        import threading
        import time

        from wbj.tito.stores import _exclusive, _path

        p = _path("iv", "EXCL")
        dentro, solapes = [], []

        def entra():
            with _exclusive(p):
                dentro.append(1)
                if len(dentro) > 1:
                    solapes.append(1)
                time.sleep(0.02)
                dentro.pop()

        hilos = [threading.Thread(target=entra) for _ in range(6)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=10)
        assert not solapes, "dos hilos entraron a la vez"

    def test_dos_escrituras_a_la_vez_no_se_pisan(self):
        # Sin cerrojo, 8 hilos × 50 trades dejaban 100 de 400: cada uno leía el
        # mismo archivo y escribía encima de lo que acumuló el anterior.
        import threading
        from datetime import datetime as _dt
        base = _dt(2026, 7, 30, tzinfo=timezone.utc)

        def escribe(lote):
            save_trades("RACE", [
                _flow_row(i, (base + timedelta(seconds=i)).isoformat().replace("+00:00", "Z"))
                for i in lote])

        hilos = [threading.Thread(target=escribe, args=(range(k * 50, k * 50 + 50),))
                 for k in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        assert len(load_trades("RACE").trades) == 400

    def test_un_archivo_corrupto_se_lee_como_sin_historial(self):
        from wbj.tito.stores import data_dir
        p = data_dir() / "trades"
        p.mkdir(parents=True, exist_ok=True)
        (p / "DEMO.json").write_text("{no soy json", encoding="utf-8")
        assert load_trades("DEMO") is None

    def test_el_json_usa_su_clave_updatedAt(self):
        # Su payload es `{ticker, updatedAt, trades}` en camelCase. El atributo
        # de Python se llama `updated_at`, la CLAVE del archivo no.
        import json
        from wbj.tito.stores import data_dir

        save_trades("DEMO", [_flow_row(1, "2026-07-30T15:00:00Z")])
        crudo = json.loads((data_dir() / "trades" / "DEMO.json").read_text(encoding="utf-8"))
        assert sorted(crudo) == ["ticker", "trades", "updatedAt"]
        # `new Date().toISOString()` — con milisegundos y sufijo Z.
        assert __import__("re").match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$",
                                      crudo["updatedAt"])

    def test_el_orden_depende_de_la_zona_horaria_del_servidor_como_en_JS(self):
        """Su `Date.parse` no usa una zona uniforme, y se replica tal cual.

        Una fecha SOLA (`"2026-07-30"`) se lee en UTC; una fecha-hora SIN
        offset (`"2026-07-30T15:00:00"`) se lee en la zona LOCAL de la máquina.
        Es la regla de ES2015+, no un descuido suyo.

        La consecuencia es real: ese orden decide qué trade se cae por el tope
        de `MAX_PER_TICKER`, así que el mismo archivo se recorta distinto en UTC
        que en New York. En Render y en el contenedor la TZ es UTC, así que en
        la práctica coinciden — pero la dependencia existe y se fija aquí para
        que nadie la "arregle" sin querer y rompa el diferencial.
        """
        import os
        import time

        from wbj.tito.stores import _date_parse

        previo = os.environ.get("TZ")
        vistos = set()
        try:
            for tz in ("UTC", "America/New_York", "Asia/Tokyo"):
                os.environ["TZ"] = tz
                time.tzset()
                vistos.add(_date_parse("2026-07-30T15:00:00"))
            # La fecha sola SÍ es UTC en las tres, que es lo que dice el estándar.
            assert _date_parse("2026-07-30") == 1785369600000
        finally:
            if previo is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previo
            time.tzset()
        assert len(vistos) == 3, f"el naive dejó de leerse como local: {vistos}"

    def test_un_timestamp_ilegible_no_se_va_al_final(self):
        # `Date.parse("ayer")` es NaN, la resta da NaN y ECMA-262 manda tratar
        # ese comparador como 0 ("iguales"): con un sort estable la fila se
        # queda donde estaba. Llegué a mandarlas al final (-inf) y no es lo suyo.
        # El [1, 3, 2] está medido ejecutando su sort en Node: la ilegible se
        # queda arriba y las dos legibles sí se ordenan entre ellas.
        save_trades("ILEG", [_flow_row(1, "ayer"),
                             _flow_row(2, "2026-07-28T15:00:00Z"),
                             _flow_row(3, "2026-07-30T15:00:00Z")])
        assert [t["id"] for t in load_trades("ILEG").trades] == [1, 3, 2]

    def test_una_lista_pelada_del_formato_viejo_no_se_lee_como_historial(self):
        # Antes el archivo era una lista; ahora es {ticker, updated_at, trades}.
        # Un archivo del formato viejo NO debe colarse como si fuera válido.
        from wbj.tito.stores import data_dir
        p = data_dir() / "trades"
        p.mkdir(parents=True, exist_ok=True)
        (p / "DEMO.json").write_text('[{"id": 1}]', encoding="utf-8")
        assert load_trades("DEMO") is None


class TestLasTresQueSeQuedanComoEl:
    """Los tres puntos donde `store.ts` maneja mal una entrada malformada.

    El módulo es **literal**: los tres están portados tal cual, comprobados
    ejecutando su archivo en Node (`engine/scripts/diff_store.sh`, 47/47 sin
    una sola divergencia declarada). Los arreglos propuestos aguas arriba viven
    en `engine/scripts/upstream-tito-store.patch`.

    Lo que protege a Vertex de los tres es el BORDE, no una guarda dentro del
    store: `borde.ticker_valido` en el endpoint, `borde.trades_utiles` al leer
    y `borde.trades_sin_id` como contador visible. Es exactamente donde su
    pipeline de Next las tiene. Se fijan en `TestElBordeDeVertex`.
    """

    def _escribe(self, nombre, contenido):
        from wbj.tito.stores import data_dir

        p = data_dir() / "trades"
        p.mkdir(parents=True, exist_ok=True)
        (p / nombre).write_text(contenido, encoding="utf-8")

    def test_1_un_ticker_que_no_da_nombre_de_archivo_comparte_cubo(self):
        # `"!!!"`, `"@@@"` y `""` sanean todos a la cadena vacía y comparten un
        # `.json` sin dueño. Suyo, portado: el store no lo mira.
        from wbj.tito.stores import _file_for

        vacios = {_file_for(m) for m in ("!!!", "@@@", "", "   ", "ñ", "🙂")}
        assert len(vacios) == 1
        assert next(iter(vacios)).name == ".json"

    def test_1b_la_travesia_de_rutas_SI_la_cierra_su_regex(self):
        # Lo único que su saneado sí resuelve: borra las barras, así que no se
        # sale del directorio. Esto no necesitaba guarda y no la tiene.
        from wbj.tito.stores import _file_for, data_dir

        p = _file_for("../../etc/passwd")
        assert p.parent == data_dir() / "trades"
        assert p.name == "....ETCPASSWD.json"

    def test_2_trades_sin_id_se_funden_en_uno(self):
        # La clave del Map es `t.id`. `flow._base_row` hace
        # `int(_num(raw.get("id")))`, así que un tape sin ese campo devuelve 0
        # para TODOS: se conserva UNO, y `added` tampoco avisa porque a partir
        # del segundo la clave "ya existe". Su comportamiento, medido.
        r = save_trades("SINID", [_flow_row(0, "2026-07-30T15:00:00Z"),
                                  _flow_row(0, "2026-07-30T15:01:00Z"),
                                  _flow_row(0, "2026-07-30T15:02:00Z")])
        assert (r.total, r.added) == (1, 1)
        assert len(load_trades("SINID").trades) == 1

    def test_2b_con_id_real_el_dedupe_es_el_suyo(self):
        rows = [_flow_row(1, "2026-07-30T15:00:00Z"), _flow_row(2, "2026-07-30T15:01:00Z")]
        save_trades("CONID", rows)
        r = save_trades("CONID", rows)
        assert (r.total, r.added) == (2, 0)

    def test_3_una_fila_corrupta_tumba_el_guardado(self):
        # El suyo: `for (const t of existing.trades) byId.set(t.id, t)` sobre un
        # `null` lanza, el guardado entero se pierde, y vuelve a pasar en CADA
        # corrida porque la fila sigue en el archivo. Ese ticker deja de
        # acumular memoria hasta que alguien limpie el `.json`.
        #
        # En Vertex no pasa mudo: `_tito_memory` lo mete en
        # `stats.escrituras_fallidas` y `/api/tito-health` levanta
        # `memoria.flows.corrupto` con el arreglo.
        self._escribe("C.json",
                      '{"ticker":"C","updatedAt":"x",'
                      '"trades":[{"id":9,"timestamp":"2026-07-20T15:00:00Z"},null,'
                      '"basura",42]}')
        with pytest.raises(TypeError):
            save_trades("C", [_flow_row(1, "2026-07-30T15:00:00Z")])

    def test_3b_load_devuelve_la_basura_tal_cual(self):
        self._escribe("E.json",
                      '{"ticker":"E","updatedAt":"x","trades":[{"id":1},"basura",null,42]}')
        assert load_trades("E").trades == [{"id": 1}, "basura", None, 42]

    def test_load_no_inventa_los_campos_que_faltan(self):
        # `loadTrades` solo comprueba `Array.isArray(parsed.trades)`; el resto
        # del archivo entra tal cual.
        self._escribe("G.json", '{"trades":[]}')
        g = load_trades("G")
        assert g.ticker is None and g.updated_at is None
        self._escribe("H.json", '{"ticker":7,"updatedAt":9,"trades":[]}')
        h = load_trades("H")
        assert h.ticker == 7 and h.updated_at == 9


class TestElBordeDeVertex:
    """Las guardas que se quitaron del store, en el sitio donde él las tiene.

    En su repo las librerías nunca ven datos crudos: el ticker llega
    normalizado desde las rutas de Next y las filas pasan por el filtro de
    `/api/validation`, donde `"basura".assetPrice` es `undefined` y la fila se
    cae sola. Vertex no es Next, así que ese borde se escribe — en `borde.py`,
    una sola vez, y sin tocar un número.
    """

    def _escribe(self, nombre, contenido):
        from wbj.tito.stores import data_dir

        p = data_dir() / "trades"
        p.mkdir(parents=True, exist_ok=True)
        (p / nombre).write_text(contenido, encoding="utf-8")

    def test_el_ticker_que_no_deja_nada_se_rechaza_ANTES_del_store(self):
        from wbj.tito.borde import TickerInvalido, ticker_valido

        for malo in ("!!!", "@@@", "", "   ", "../..", "ñ"):
            with pytest.raises(TickerInvalido):
                ticker_valido(malo)

    def test_el_ticker_bueno_pasa_ya_saneado(self):
        from wbj.tito.borde import ticker_valido

        assert ticker_valido("  aapl ") == "AAPL"
        assert ticker_valido("brk.b") == "BRK.B"
        assert ticker_valido("A@A!P#L") == "AAPL"    # su regex, tal cual

    def test_el_ticker_absurdamente_largo_tambien(self):
        # Sin tope llega al sistema de archivos y revienta con ENAMETOOLONG,
        # que depende del FS. Mejor un error determinista y con motivo.
        from wbj.tito.borde import MAX_TICKER_LEN, TickerInvalido, ticker_valido

        ticker_valido("A" * MAX_TICKER_LEN)          # el límite justo pasa
        with pytest.raises(TickerInvalido):
            ticker_valido("A" * (MAX_TICKER_LEN + 1))

    def test_el_consumidor_no_revienta_con_un_archivo_sucio(self):
        # Es el filtro de su /api/validation: `t.assetPrice > 0 && t.timestamp`.
        from wbj.tito.borde import trades_utiles

        bueno = {"id": 1, "timestamp": "2026-07-30T15:00:00Z", "asset_price": 95.0}
        self._escribe("F.json", json.dumps(
            {"ticker": "F", "updatedAt": "x", "trades": [bueno, "basura", None, 42]}))
        usables = [t for t in trades_utiles(load_trades("F").trades)
                   if (t.get("asset_price") or 0) > 0 and t.get("timestamp")]
        assert usables == [bueno]

    def test_los_trades_sin_id_se_cuentan_aunque_no_se_arreglen(self):
        # No es un filtro: el dedupe sigue siendo el suyo. Es lo que hace que el
        # fallo se vea el mismo día en vez de descubrirse dentro de un mes con
        # un archivo de un solo trade.
        from wbj.tito.borde import trades_sin_id

        assert trades_sin_id([{"id": 1}, {"id": None}, {}, "basura", None]) == 4
        assert trades_sin_id([{"id": 1}, {"id": 2}]) == 0

    def test_load_no_inventa_los_campos_que_faltan(self):
        # Sin `str(... or "")`: un `ticker` ausente llega como None (su
        # `undefined`) y uno numérico llega como número. Esto SÍ es literal.
        self._escribe("G.json", '{"trades":[]}')
        g = load_trades("G")
        assert g.ticker is None and g.updated_at is None
        self._escribe("H.json", '{"ticker":7,"updatedAt":9,"trades":[]}')
        h = load_trades("H")
        assert h.ticker == 7 and h.updated_at == 9


class TestDateParseSigueElEstandar:
    """El subconjunto ISO que la ECMA-262 sí define, con sus formatos cortos.

    Salió del diferencial de primitivas: `"2026"` y `"2026-07"` son formatos
    VÁLIDOS del Date Time String Format y el port los daba por ilegibles, así
    que un timestamp truncado se ordenaba al azar en vez de por su fecha.
    """

    def test_los_formatos_cortos_del_estandar(self):
        from wbj.tito.stores import _date_parse

        # Año solo y año-mes: el mes y el día que faltan valen 1, en UTC.
        assert _date_parse("2026") == _date_parse("2026-01-01T00:00:00Z")
        assert _date_parse("2026-07") == _date_parse("2026-07-01T00:00:00Z")
        assert _date_parse("+002026-07-30T00:00:00Z") == _date_parse("2026-07-30")

    def test_una_fecha_imposible_en_formato_ISO_es_NaN(self):
        # La especificación pide una fecha válida. (V8 cae a su parseo legacy y
        # las desborda al mes siguiente; eso es cosa suya, no del estándar.)
        import math

        from wbj.tito.stores import _date_parse

        for v in ("2026-13-45T00:00:00Z", "2026-02-30", "2026-00-10", "2026-07-00"):
            assert math.isnan(_date_parse(v)), v

    def test_el_legacy_de_V8_SI_se_replica(self):
        """El número suelto sigue la regla de V8, medida contra Node 22.

        Estuvo sin replicar a propósito —ECMA-262 la declara
        *implementation-defined*, así que copiarla es copiar una peculiaridad
        del motor, no lógica de Víctor—. Se replica porque la instrucción es
        que lo único distinto de su código sea el perfil y la Wheel.

        La regla es arbitraria hasta el absurdo, y por eso está fijada aquí:
        `"1".."12"` se leen como MES del año 2001, `"13".."31"` dan NaN.
        """
        import math
        from datetime import datetime, timezone

        from wbj.tito.stores import _date_parse

        def dia(v):
            ms = _date_parse(v)
            if isinstance(ms, float) and math.isnan(ms):
                return None
            return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")

        assert dia("0") == "2000-01-01"
        assert dia("5") == "2001-05-01"      # mes 5 del 2001, no el año 5
        assert dia("12") == "2001-12-01"
        assert dia("13") is None             # ni mes ni año de dos cifras
        assert dia("31") is None
        assert dia("32") == "2032-01-01"
        assert dia("49") == "2049-01-01"
        assert dia("50") == "1950-01-01"     # a partir de 50, siglo XX
        assert dia("99") == "1999-01-01"
        # `strftime` de Python no rellena el año con ceros como `toISOString`
        # de JavaScript; el instante es el mismo, la comparación es de texto.
        assert dia("500") in ("0500-01-01", "500-01-01")

        # Lo que NO es un número suelto sigue siendo NaN: la fuente manda ISO.
        for v in ("$5", "Jul 30 2026", "30/07/2026", "ayer", "1e3"):
            assert math.isnan(_date_parse(v)), v

    def test_las_variantes_ISO_que_si_se_parsean(self):
        from wbj.tito.stores import _date_parse

        base = _date_parse("2026-07-30T15:00:00Z")
        assert _date_parse("2026-07-30T15:00:00z") == base
        assert _date_parse("2026-07-30T15:00") == base
        assert _date_parse("2026-07-30T10:00:00-05:00") == base
        assert _date_parse("2026-07-30T20:00:00+0500") == base
        assert _date_parse("2026-07-30T15:00:00.250Z") == base + 250


def _snap(d: str, base: float, spot: float = 100.0, direction: str = "up") -> PredictionSnapshot:
    return PredictionSnapshot(date=d, horizon_days=10, spot=spot,
                              bear=spot * 0.9, base=base, bull=spot * 1.1,
                              direction=direction)  # type: ignore[arg-type]


def _bars(start: str, n: int, close: float) -> list[LvlBar]:
    d0 = date.fromisoformat(start)
    return [LvlBar(time=(d0 + timedelta(days=i)).isoformat(),
                   high=close + 1, low=close - 1, close=close) for i in range(n)]


class TestPredictionStore:
    def test_guarda_y_deduplica_por_fecha_y_horizonte(self):
        save_prediction("DEMO", _snap("2026-07-30", 105))
        save_prediction("DEMO", _snap("2026-07-30", 107))  # pisa
        j = load_journal("DEMO")
        assert len(j) == 1
        assert j[0]["base"] == 107

    def test_sin_predicciones_vencidas_no_hay_sesgo(self):
        r = review_predictions([], [], NOW)
        assert r["matured_count"] == 0
        assert r["bias_pct"] is None

    def test_una_prediccion_a_medio_camino_no_se_juzga(self):
        s = _snap("2026-07-28", 105)
        r = review_predictions([s.__dict__], _bars("2026-07-29", 2, 101), NOW)
        assert r["evals"][0]["matured"] is False
        assert r["matured_count"] == 0

    def test_calcula_el_sesgo_firmado_del_target_base(self):
        # base 105, cerro en 100 -> error = (100-105)/100 = -5%
        s = _snap("2026-07-01", 105)
        r = review_predictions([s.__dict__], _bars("2026-07-02", 12, 100), NOW)
        assert r["matured_count"] == 1
        assert r["bias_pct"] == pytest.approx(-5.0, abs=1e-6)
        assert r["mean_abs_error_pct"] == pytest.approx(5.0, abs=1e-6)

    def test_detecta_si_el_precio_toco_el_target_base(self):
        s = _snap("2026-07-01", 105)
        tocado = review_predictions([s.__dict__], _bars("2026-07-02", 12, 106), NOW)
        no = review_predictions([s.__dict__], _bars("2026-07-02", 12, 99), NOW)
        assert tocado["base_touch_rate"] == 100
        assert no["base_touch_rate"] == 0

    def test_acierto_de_direccion(self):
        sube = _snap("2026-07-01", 105, direction="up")
        r = review_predictions([sube.__dict__], _bars("2026-07-02", 12, 104), NOW)
        assert r["direction_hit_rate"] == 100
        r2 = review_predictions([sube.__dict__], _bars("2026-07-02", 12, 96), NOW)
        assert r2["direction_hit_rate"] == 0

    def test_dice_que_escenario_quedo_mas_cerca(self):
        s = _snap("2026-07-01", 105)  # bear 90 / base 105 / bull 110
        r = review_predictions([s.__dict__], _bars("2026-07-02", 12, 109), NOW)
        assert r["best_counts"]["bull"] == 1

    def test_el_review_alimenta_la_calibracion(self):
        snaps = [_snap(f"2026-07-{d:02d}", 105).__dict__ for d in range(1, 7)]
        r = review_predictions(snaps, _bars("2026-07-02", 25, 100), NOW)
        cal = calibration_from_review(r)
        assert cal["samples"] == r["matured_count"]
        assert cal["bias_pct"] == r["bias_pct"]

    def test_con_5_vencidas_la_calibracion_ya_actua(self):
        from wbj.tito.prediction import calibration_shift_pct
        snaps = [_snap(f"2026-07-{d:02d}", 105).__dict__ for d in range(1, 7)]
        cal = calibration_from_review(review_predictions(snaps, _bars("2026-07-02", 25, 100), NOW))
        assert cal["samples"] >= 5
        assert calibration_shift_pct(cal["bias_pct"], cal["samples"]) != 0


def test_escritura_atomica_no_deja_json_truncado(tmp_path, monkeypatch):
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))
    save_iv_snapshot("DEMO", 45, NOW)
    # No debe quedar ningun .tmp suelto tras una escritura correcta.
    assert not list((tmp_path / "iv").glob("*.tmp"))
    assert load_iv_history("DEMO")[0]["avg_iv"] == 45


class TestUnArchivoQueNoEsElNuestroNoRevienta:
    """Su `loadIvHistory` devuelve `null` ante un archivo que no reconoce.

    El port lanzaba `AttributeError` con el MISMO archivo — el formato de su
    app, que lleva el sobre `{ticker, updatedAt, snapshots}` y no una lista
    pelada. El motor acababa degradando igual porque `_tito_memory` atrapa
    todo, pero por el camino equivocado: una excepción que sube tres capas en
    vez de un archivo descartado donde se lee.
    """

    @pytest.fixture(autouse=True)
    def disco(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))
        self.dir = tmp_path

    def _escribe(self, sub, contenido):
        import json as _json

        d = self.dir / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "WULF.json").write_text(_json.dumps(contenido), encoding="utf-8")

    def test_el_formato_de_SU_app_no_lanza(self):
        from wbj.tito import stores as st

        self._escribe("iv", {"ticker": "WULF", "updatedAt": "2026-08-07T15:30:00.000Z",
                             "snapshots": [{"date": "2026-08-07", "avgIv": 0.55}]})
        assert st.load_iv_history("WULF") == []

    def test_una_lista_de_textos_no_lanza(self):
        from wbj.tito import stores as st

        self._escribe("iv", ["esto", "no", "son", "filas"])
        assert st.load_iv_history("WULF") == []

    def test_las_filas_buenas_sobreviven_a_las_malas(self):
        """Descartar la basura no puede llevarse por delante lo que sí vale."""
        from datetime import date

        from wbj.tito import stores as st

        hoy = date.today().isoformat()
        self._escribe("iv", ["basura", {"date": hoy, "avg_iv": 0.55}, 42, None])
        assert st.load_iv_history("WULF") == [{"date": hoy, "avg_iv": 0.55}]

    def test_el_diario_de_predicciones_tambien(self):
        from wbj.tito import stores as st

        self._escribe("predictions", {"ticker": "WULF", "snapshots": [{"date": "2026-08-07"}]})
        assert st.load_journal("WULF") == []

    def test_una_fila_sin_fecha_no_rompe_el_orden(self):
        """El `sorted` ordenaba por `r[key]` y reventaba con `KeyError` en una
        fila sin `date`, aunque el filtro anterior ya la hubiera dejado pasar."""
        from datetime import date

        from wbj.tito import stores as st

        hoy = date.today().isoformat()
        self._escribe("iv", [{"avg_iv": 0.4}, {"date": hoy, "avg_iv": 0.55}])
        assert st.load_iv_history("WULF") == [{"date": hoy, "avg_iv": 0.55}]
