"""Cableado end-to-end del motor de Víctor dentro de Vertex.

La suite de `engine/tests/tito/` prueba la lógica pura. Esto prueba lo otro: que
los endpoints existen, que llaman al motor, que traducen bien a JSON y que
degradan con el motivo exacto cuando una fuente falla.

Sin red: Massive, MarketSnack y los feeds se sustituyen por dobles. Lo que se
verifica es el CABLEADO, no los datos.

    python -m pytest tests_vertex/test_tito_wiring.py -q
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

fastapi = pytest.importorskip("fastapi", reason="requiere las deps de vertex_api")

NOW = datetime.now(timezone.utc)
SPOT = 100.0


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import vertex_api as V

    return TestClient(V.app)


@pytest.fixture(autouse=True)
def fuentes(monkeypatch, tmp_path):
    """Sustituye Massive, MarketSnack y los feeds por dobles deterministas."""
    import wbj.tito.marketsnack as MS
    import wbj.tito.massive as MASS
    import wbj.tito.news as N
    from wbj.tito.levels import LvlBar
    from wbj.tito.marketsnack import FlowResult
    from wbj.tito.structure import ChainRow

    def fake_chain(ticker, **k):
        rows = []
        for exp in ("2026-09-18", "2027-01-15"):
            for s in range(70, 135, 5):
                for ct in ("call", "put"):
                    wall = (ct == "call" and s in (105, 110)) or (ct == "put" and s == 90)
                    oi = int(1500 * math.exp(-((s - SPOT) ** 2) / 400) * (8 if wall else 1)) + 60
                    rows.append(ChainRow(ct, exp, float(s), oi,
                                         int(oi * (1.4 if wall else 0.25)), oi * 100 * s))
        return MASS.ChainResult(rows=rows, underlying_price=SPOT, pages=1, truncated=False)

    def fake_bars(ticker, days=365, **k):
        # `days` posicional: la firma real es `fetch_daily_bars(ticker, days=365,
        # timeout=...)` y tanto `cached_daily_bars` como `daily_bars_for_panel`
        # lo pasan por posición. Un doble que solo acepte kwargs convierte un
        # cambio de firma en un `TypeError` disfrazado de "Massive está caído".
        out, seed = [], 7
        for i in range(200):
            seed = (seed * 1103515245 + 12345) % 2147483648
            c = 92 + i * 0.04 + 9 * math.sin(i / 17) + (seed / 2147483648 - 0.5) * 1.5
            out.append(LvlBar((NOW - timedelta(days=200 - i)).date().isoformat(),
                              c + 1.5, c - 1.5, c))
        out[-1] = LvlBar(out[-1].time, SPOT + 1, SPOT - 1, SPOT)
        return out

    def fake_flow(ticker, **k):
        tr = []
        for i, (strike, side, mins) in enumerate(
            [(105, "AT_ASK", 3), (105, "ABOVE_ASK", 2), (105, "AT_ASK", 1),
             (110, "ASKSIDE", 6), (90, "AT_BID", 40)]
        ):
            cp = "C" if strike >= 100 else "P"
            tr.append({
                "id": i + 1, "symbol": f"DEMO270115{cp}{strike * 1000:08d}",
                "price": 9.2, "size": 800, "side": side,
                "bid_price": 9.14, "ask_price": 9.26, "premium": 9.2 * 800 * 100,
                "delta": 0.62 if cp == "C" else -0.28, "gamma": 0.03,
                "theta": -0.04, "vega": 0.3, "implied_volatility": 0.44,
                "open_interest": 4000, "volume": 5200, "score": 8,
                "sentiment": "bullish",
                "timestamp": (NOW - timedelta(minutes=mins)).isoformat(),
                "asset_price": SPOT, "trade_condition_id": 231,
            })
        return FlowResult(trades=tr, pages=1, truncated=False)

    monkeypatch.setattr(MASS, "fetch_option_chain", fake_chain)
    monkeypatch.setattr(MASS, "fetch_daily_bars", fake_bars)
    monkeypatch.setattr(MASS, "fetch_ticker_name", lambda t, **k: "Demo Corporation")
    monkeypatch.setattr(MS, "fetch_flow", fake_flow)
    monkeypatch.setattr(N, "fetch_ticker_news", lambda t, **k: [
        N.NewsItem(id="1", title="Demo misses targets", url="u", publisher="Reuters",
                   published_utc=NOW.isoformat(), sentiment="negative", layer="company")])
    monkeypatch.setattr(N, "fetch_macro_feeds", lambda **k: [
        N.NewsItem(id="2", title="Fed holds rates steady", url="v", publisher="CNBC",
                   published_utc=NOW.isoformat(), layer="macro"),
        N.NewsItem(id="3", title="Demo Corporation recalls units", url="w",
                   publisher="CNBC", published_utc=NOW.isoformat(), layer="macro")])
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))
    monkeypatch.setenv("MASSIVE_API_KEY", "x" * 32)
    monkeypatch.setenv("MARKETSNACK_COOKIE", "y" * 32)


class TestRutas:
    def test_las_tres_rutas_del_motor_existen(self, client):
        import vertex_api as V
        rutas = {r.path for r in V.app.routes}
        assert {"/api/projection-targets", "/api/tito-scorecard", "/api/tito-news"} <= rutas

    def test_el_motor_carga_desde_vertex_api(self):
        import vertex_api as V
        assert V._tito_mod() is not None


class TestProjectionTargets:
    def test_devuelve_el_scorecard_del_motor_de_victor(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["ok"] is True
        assert d["engine"] == "victor/tito"
        assert d["chain_source"] == "massive"
        assert 0 <= d["score"] <= 100
        assert d["verdict"] in ("Oportunidad Fuerte", "Oportunidad Moderada", "Oportunidad Débil")

    def test_sirve_los_tres_horizontes_de_victor(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert sorted(int(h) for h in d["predictions"]) == [10, 20, 30]

    def test_el_orden_estricto_llega_hasta_el_json(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        for p in d["predictions"].values():
            assert p["bear"]["target"] < p["base"]["target"] < p["bull"]["target"]

    def test_lleva_lo_que_la_grafica_necesita(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert len(d["history"]) == 70  # las 70 velas de SimpleChart
        assert d["levels_for_chart"] is not None
        assert d["call_pct"] is not None
        assert d["chart_geometry"] is not None


class TestGeometriaDeLaGrafica:
    """`cone_points` y `prediction_path` — sus dos funciones de `expectedMove.ts`
    que su `SimpleChart` llama y aquí estaban portadas SIN llamador.

    La fórmula estaba escrita dos veces: en `expected_move.py` (muerta) y a mano
    dentro de `renderVictorProjChart`. `diff_cono.sh` medía la del navegador
    contra su archivo, pero nada garantizaba que las dos copias de este repo
    coincidieran entre sí. Ahora el cono y las rutas los calcula el motor con
    SUS funciones y la gráfica los dibuja.
    """

    def test_hay_cono_y_rutas_para_cada_horizonte(self, client):
        g = client.get("/api/projection-targets?ticker=DEMO").json()["chart_geometry"]
        assert set(g) == {"10", "20", "30"}
        for h, geo in g.items():
            assert len(geo["cone"]) == 25          # `conePoints(…, 24)` → 24+1
            assert set(geo["paths"]) == {"bull", "base", "bear"}
            for ruta in geo["paths"].values():
                assert len(ruta["points"]) == 31   # `wigglePath(…, steps = 30)`

    def test_el_cono_se_abre_con_la_raiz_del_tiempo(self, client):
        geo = client.get("/api/projection-targets?ticker=DEMO").json()["chart_geometry"]["30"]
        c = geo["cone"]
        assert c[0]["t"] == 0 and c[0]["upper1"] == c[0]["lower1"]   # arranca cerrado
        anchos = [p["upper1"] - p["lower1"] for p in c]
        assert anchos == sorted(anchos)                              # solo se abre
        # 2σ envuelve a 1σ en TODOS los pasos: si no, el polígono se cruza.
        assert all(p["upper2"] >= p["upper1"] and p["lower2"] <= p["lower1"] for p in c)

    def test_ninguna_ruta_se_sale_del_cono_de_2_sigma(self, client):
        """Es la regla de su `predictionPath`: *el precio no puede llegar a donde
        la volatilidad no da*. Sin esto, la línea del escenario se dibuja fuera
        de la banda que la propia gráfica pinta."""
        geo = client.get("/api/projection-targets?ticker=DEMO").json()["chart_geometry"]["30"]
        techo = geo["cone"][-1]["upper2"]
        suelo = geo["cone"][-1]["lower2"]
        for ruta in geo["paths"].values():
            assert suelo - 1e-6 <= ruta["target"] <= techo + 1e-6

    def test_la_ruta_empieza_en_el_spot_y_acaba_en_el_target(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        geo = d["chart_geometry"]["20"]
        for ruta in geo["paths"].values():
            assert abs(ruta["points"][0]["price"] - d["spot"]) < 1e-6
            assert abs(ruta["points"][-1]["price"] - ruta["target"]) < 1e-6

    def test_es_JSON_estricto(self, client):
        import json

        crudo = json.dumps(
            client.get("/api/projection-targets?ticker=DEMO").json()["chart_geometry"])
        assert "NaN" not in crudo and "Infinity" not in crudo


class TestRacimosDelTape:
    """`detect_clusters` de su `flow.ts` — lo que su `FlowPriceChart` dibuja.

    Estaba portado y sin llamador. Aporta lo que el scorecard no tiene: la
    **apuesta neta** de cada burst (comprar puts es bajista, no alcista) y su
    ventana temporal.
    """

    def test_el_endpoint_sirve_los_racimos(self, client):
        cl = client.get("/api/projection-targets?ticker=DEMO").json()["flow_clusters"]
        assert cl, "el tape del doble tiene un burst de 3 trades en 105"
        c = cl[0]
        assert c["count"] >= 3 and c["premium"] > 0
        assert c["direction"] in ("ask", "bid")
        assert c["bet"] in ("alcista", "bajista")
        assert 0.0 <= c["unidirectionality"] <= 1.0
        assert c["strikes"]

    def test_la_apuesta_neta_no_es_el_lado_de_ejecucion(self, client):
        # Su regla: comprar CALLs al ask es alcista. El burst del doble es
        # 3× call 105 AT_ASK/ABOVE_ASK, así que tiene que salir alcista.
        cl = client.get("/api/projection-targets?ticker=DEMO").json()["flow_clusters"]
        burst = next(c for c in cl if 105.0 in c["strikes"])
        assert burst["direction"] == "ask" and burst["bet"] == "alcista"
        assert burst["call_premium"] > burst["put_premium"]

    def test_sin_tape_no_se_inventan_racimos(self, client, monkeypatch):
        import wbj.tito.marketsnack as MS
        from wbj.tito.marketsnack import MarketSnackError

        def sin_tape(*a, **k):
            raise MarketSnackError("cookie caducada")

        monkeypatch.setattr(MS, "fetch_flow", sin_tape)
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["flow_clusters"] is None

    def test_un_fallo_al_agrupar_no_tumba_los_targets(self, client, monkeypatch):
        # Ilustra, no decide — mismo criterio que el heatmap.
        import wbj.tito.flow as F

        monkeypatch.setattr(F, "detect_clusters", lambda *a, **k: 1 / 0)
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["ok"] is True and d["flow_clusters"] is None

    def test_expone_el_estado_de_la_memoria(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        m = d["memory"]
        assert m["available"] is True
        assert m["flows_guardados"] > 0
        assert "iv_rank_real_en" in m

    def test_la_memoria_de_trades_se_acumula_entre_llamadas(self, client):
        # El round-trip por el envoltorio `StoredTrades` de store.ts: la segunda
        # consulta no puede perder ni duplicar lo de la primera, que es de lo
        # único que vive el sub-agente 6.
        import wbj.tito.stores as st

        primera = client.get("/api/projection-targets?ticker=DEMO").json()
        guardado = st.load_trades("DEMO")
        assert guardado is not None and guardado.ticker == "DEMO"
        n1 = len(guardado.trades)
        assert n1 == primera["memory"]["flows_guardados"]

        segunda = client.get("/api/projection-targets?ticker=DEMO").json()
        assert segunda["memory"]["flows_guardados"] == n1     # mismo tape → no duplica
        ids = [t["id"] for t in st.load_trades("DEMO").trades]
        assert len(ids) == len(set(ids))

    def test_distingue_lo_guardado_de_lo_utilizable(self, client, tmp_path):
        # Un solo contador mentía: si el tape pierde `asset_price`, el archivo
        # sigue creciendo y el sub-agente 6 se queda sin nada, pero `stats`
        # decía "0 guardados" — que se lee como "el disco no funciona".
        import json
        import wbj.tito.stores as st

        import vertex_api as V
        from datetime import datetime, timezone

        client.get("/api/projection-targets?ticker=DEMO")
        p = st.data_dir() / "trades" / "DEMO.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw["trades"], "el endpoint debería haber guardado algo"
        for t in raw["trades"]:
            t["asset_price"] = 0          # el esquema del tape cambió
        p.write_text(json.dumps(raw), encoding="utf-8")

        # Sin tape nuevo, para que no se auto-repare y se vea el estado degradado.
        m = V._tito_memory("DEMO", [], [], [], datetime(2026, 7, 30, 16, tzinfo=timezone.utc))["stats"]
        assert m["flows_guardados"] > 0          # el disco SÍ tiene datos
        assert m["flows_utilizables"] == 0       # pero ninguno sirve
        assert m["flows_descartados"] == m["flows_guardados"]
        assert m["available"] is True            # el disco no es el problema

    def test_el_tape_bueno_repara_los_trades_degradados(self, client):
        # El "análisis más reciente gana" de Víctor no es solo cosmético: una
        # corrida con tape sano vuelve a poner en pie lo que se guardó mal.
        import json
        import wbj.tito.stores as st

        client.get("/api/projection-targets?ticker=DEMO")
        p = st.data_dir() / "trades" / "DEMO.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        for t in raw["trades"]:
            t["asset_price"] = 0
        p.write_text(json.dumps(raw), encoding="utf-8")

        m = client.get("/api/projection-targets?ticker=DEMO").json()["memory"]
        assert m["flows_descartados"] == 0

    def test_si_la_memoria_se_apaga_dice_por_que(self, client, monkeypatch):
        # Degradar en silencio es peor que fallar: sin memoria el scorecard sale
        # igual de bonito con menos evidencia detrás.
        import wbj.tito.stores as st

        def boom(*a, **k):
            raise OSError("disco de solo lectura")

        monkeypatch.setattr(st, "load_iv_history", boom)
        m = client.get("/api/projection-targets?ticker=DEMO").json()["memory"]
        assert m["available"] is False
        assert "disco de solo lectura" in m["motivo"]

    def test_peticiones_simultaneas_no_se_comen_la_memoria(self, client):
        """La prueba de que está CONECTADO, no solo de que el store funciona.

        Es el caso real: el panel se auto-refresca mientras el usuario consulta,
        y FastAPI atiende las rutas síncronas en un pool de hilos. Sin cerrojo
        cada petición leía el mismo archivo y escribía encima de la anterior.
        """
        import threading

        import wbj.tito.stores as st

        errores = []

        def consulta():
            try:
                r = client.get("/api/projection-targets?ticker=DEMO")
                assert r.status_code == 200 and r.json()["ok"] is True
            except Exception as e:                     # noqa: BLE001
                errores.append(repr(e))

        hilos = [threading.Thread(target=consulta) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=60)

        assert not [h for h in hilos if h.is_alive()], "alguna petición se colgó"
        assert not errores, errores

        # El tape del doble es el mismo, así que el total no depende de cuántas
        # peticiones entraron — pero sí tiene que estar completo, no truncado.
        stored = st.load_trades("DEMO")
        assert stored is not None
        ids = [t["id"] for t in stored.trades]
        assert len(ids) == len(set(ids)), "la concurrencia duplicó trades"
        una = client.get("/api/projection-targets?ticker=DEMO").json()
        assert una["memory"]["flows_guardados"] == len(ids)

    def test_la_memoria_en_disco_enciende_el_subagente_6_de_verdad(self, client):
        """La prueba definitiva del punto del store: sin él, `validation` sale
        `None` aunque haya tape, porque un flow de hoy no tiene recorrido que
        juzgar. Aquí se comprueba de punta a punta —disco → endpoint— y no con
        `past_flows` inyectado a mano en `run_scorecard`.
        """
        import wbj.tito.stores as st

        # Sin memoria previa: el tape de hoy no basta.
        d0 = client.get("/api/projection-targets?ticker=FRESCO").json()
        assert d0["scores"]["validation"] is None
        assert d0["active"] < 6

        # Se siembra el archivo con flows de sesiones pasadas, por la MISMA vía
        # que usa el motor: save_trades. Nada de tocar el JSON a mano.
        from wbj.tito.flow import classify_flow
        import wbj.tito.marketsnack as MS

        viejos = []
        for dias in (30, 45, 60, 75, 90):
            for t in MS.fetch_flow("FRESCO").trades:
                t = dict(t)
                t["id"] = t["id"] * 1000 + dias
                t["timestamp"] = (NOW - timedelta(days=dias)).isoformat()
                viejos.append(t)
        guardado = st.save_trades("FRESCO", classify_flow(viejos, NOW).interesting)
        assert guardado.total >= 5, "la siembra no llegó al disco"

        d1 = client.get("/api/projection-targets?ticker=FRESCO").json()
        assert d1["scores"]["validation"] is not None, \
            "el sub-agente 6 sigue apagado con memoria acumulada en disco"
        assert d1["active"] == 6, f"solo {d1['active']}/6 categorías activas"
        assert d1["memory"]["flows_utilizables"] > 0
        # Y el score total cambia: la memoria no es decorativa.
        assert d1["score"] != d0["score"]

    def test_sobrevive_un_cambio_de_esquema_en_massive(self, client, monkeypatch):
        """Si Massive pasa a mandar los números como texto, el motor no puede
        llenarse de ceros en silencio.

        Es el mismo fallo que apareció en `store.ts`: la fuente cambia de tipo,
        nada lanza, y el scorecard sale igual de bonito con la cadena a cero.
        """
        import wbj.tito.massive as MASS
        from wbj.tito.compute import to_row

        def crudo(texto: bool):
            out = []
            for s in range(90, 115, 5):
                for ct in ("call", "put"):
                    oi, strike, vol = 9000, float(s), 400
                    if texto:
                        oi, strike, vol = str(oi), str(strike), str(vol)
                    out.append({
                        "details": {"contract_type": ct, "strike_price": strike,
                                    "expiration_date": "2026-09-18T00:00:00Z",
                                    "shares_per_contract": 100},
                        "day": {"volume": vol, "close": 2.5},
                        "open_interest": oi,
                    })
            return [to_row(c) for c in out]

        def chain(texto):
            return lambda t, **k: MASS.ChainResult(
                rows=crudo(texto), underlying_price=SPOT, pages=1, truncated=False)

        monkeypatch.setattr(MASS, "fetch_option_chain", chain(False))
        normal = client.get("/api/projection-targets?ticker=ESQ").json()
        monkeypatch.setattr(MASS, "fetch_option_chain", chain(True))
        texto = client.get("/api/projection-targets?ticker=ESQ").json()

        assert normal["ok"] is True and texto["ok"] is True
        assert texto["scores"]["structure"] == normal["scores"]["structure"] is not None
        assert texto["scores"]["structure"] > 0, "la cadena se fue a cero en silencio"
        # Y la fecha con hora no parte el vencimiento en dos.
        assert texto["score"] == normal["score"]

    def test_una_fila_malformada_no_se_lleva_la_cadena_entera(self, client, monkeypatch):
        """`to_row` corre dentro del bucle de descarga: si lanza con un
        contrato, se pierden los otros 4999 y el panel dice "sin cadena"."""
        import wbj.tito.massive as MASS
        from wbj.tito.compute import to_row

        def con_basura(t, **k):
            crudos = []
            for s in range(90, 115, 5):
                for ct in ("CALL", "PUT"):        # además, en MAYÚSCULAS
                    crudos.append({"details": {"contract_type": ct, "strike_price": float(s),
                                               "expiration_date": "2026-09-18",
                                               "shares_per_contract": 100},
                                   "day": {"volume": 400, "close": 2.5},
                                   "open_interest": 9000})
            crudos += [{"details": "esquema cambiado"}, {"open_interest": "NaN"}, None, "x"]
            filas = [r for r in map(to_row, crudos) if r.strike > 0 and r.expiration]
            return MASS.ChainResult(rows=filas, underlying_price=SPOT, pages=1, truncated=False)

        monkeypatch.setattr(MASS, "fetch_option_chain", con_basura)
        d = client.get("/api/projection-targets?ticker=BASURA").json()
        assert d["ok"] is True
        assert d["scores"]["structure"] is not None and d["scores"]["structure"] > 0
        # Y los PUT en mayúsculas siguen siendo puts: si no, el GEX se invierte.
        assert d["gex"]["regime"] in ("positive", "negative")

    def test_arranca_de_disco_vacio(self, client, tmp_path, monkeypatch):
        # Primer despliegue: no hay nada guardado. El motor tiene que responder
        # igual, con las tres piezas de memoria declaradas como apagadas.
        vacio = tmp_path / "sin-nada"
        monkeypatch.setenv("WBJ_TITO_DATA", str(vacio))
        d = client.get("/api/projection-targets?ticker=NUEVO").json()
        assert d["ok"] is True
        assert d["memory"]["available"] is True      # el disco va, solo está vacío
        assert d["memory"]["motivo"] is None
        assert d["memory"]["iv_rank_real_en"] > 0    # aún faltan sesiones

    def test_lo_guardado_lleva_el_analisis_completo(self, client):
        # Víctor guarda el FlowRow entero, no 8 campos: si mañana hace falta
        # preguntar por el score o los greeks del pasado, ya están.
        import wbj.tito.stores as st

        client.get("/api/projection-targets?ticker=DEMO")
        t = st.load_trades("DEMO").trades[0]
        assert {"id", "timestamp", "asset_price", "aggression",
                "score", "flags", "scores", "gamma", "expiry_status"} <= set(t)

    def test_las_noticias_no_viajan_dentro(self, client):
        # Van en su ruta propia: 4 feeds RSS lentos no pueden retrasar los targets.
        assert "news" not in client.get("/api/projection-targets?ticker=DEMO").json()

    def test_ticker_vacio_no_revienta(self, client):
        assert client.get("/api/projection-targets?ticker=").json()["ok"] is False


class TestElBordeDeVertex:
    """Las guardas que se quitaron de sus archivos, comprobadas desde el HTTP.

    Sus tres módulos (`store.ts`, `compute.ts`, `barsStore.ts`) son literales:
    no llevan una sola comprobación que él no escriba. Lo que impide que sus
    fallos conocidos lleguen al panel es este borde, que es exactamente donde
    su pipeline de Next lo tiene. Si algo de esto se rompe, el port sigue
    siendo fiel pero Vertex deja de estar protegido — y eso hay que verlo.
    """

    @pytest.mark.parametrize("malo", ["!!!", "@@@", "   ", "ñ", "...", "../..",
                                      "A" * 300])
    def test_un_ticker_que_no_deja_nada_se_rechaza_en_las_tres_rutas(self, client, malo):
        # Sin esto los tres van al MISMO `.json` sin dueño y la memoria de una
        # consulta basura contamina la del siguiente.
        for ruta in ("/api/projection-targets", "/api/tito-scorecard", "/api/tito-news"):
            r = client.get(f"{ruta}?ticker={malo}").json()
            assert r["ok"] is False, f"{ruta} aceptó {malo!r}"
            assert "ticker" in r["error"].lower()

    def test_un_ticker_bueno_pasa_ya_saneado(self, client):
        import wbj.tito.stores as st

        assert client.get("/api/projection-targets?ticker=+demo+").json()["ok"] is True
        assert (st.data_dir() / "trades" / "DEMO.json").exists()

    def test_el_health_reporta_el_ticker_invalido_antes_que_nada(self, client):
        r = client.get("/api/tito-health?ticker=!!!").json()
        assert r["ok"] is False and r["ticker"] is None
        assert r["checks"][0]["check"] == "ticker"

    def test_una_fila_corrupta_en_disco_no_tumba_la_peticion(self, client):
        """Su `saveTrades` SÍ se cae con ella (portado literal). Lo que no puede
        es llevarse la respuesta entera ni pasar mudo."""
        import json

        import wbj.tito.stores as st

        client.get("/api/projection-targets?ticker=DEMO")
        p = st.data_dir() / "trades" / "DEMO.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["trades"] = [raw["trades"][0], None, "basura", 42]
        p.write_text(json.dumps(raw), encoding="utf-8")

        m = client.get("/api/projection-targets?ticker=DEMO").json()["memory"]
        assert m["available"] is True                 # la LECTURA sobrevive
        assert m["flows_corruptos"] == 3              # y se dicen cuántas son
        # …y el fallo de ESCRITURA de su `saveTrades` queda declarado.
        assert any("trades" in e for e in (m["escrituras_fallidas"] or []))

    def test_el_health_levanta_la_bandera_del_archivo_corrupto(self, client):
        import json

        import wbj.tito.stores as st

        client.get("/api/projection-targets?ticker=DEMO")
        p = st.data_dir() / "trades" / "DEMO.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["trades"] = [raw["trades"][0], None]
        p.write_text(json.dumps(raw), encoding="utf-8")

        checks = {c["check"]: c for c in client.get(
            "/api/tito-health?ticker=DEMO").json()["checks"]}
        assert checks["memoria.flows.corrupto"]["ok"] is False
        assert "1 fila" in checks["memoria.flows.corrupto"]["detalle"]

    def test_los_trades_sin_id_se_cuentan_aunque_el_dedupe_siga_siendo_el_suyo(self, client):
        # Su clave de dedupe es `t.id` a secas: sin ese campo el `Map` conserva
        # UNO de la corrida entera, sin error. El contador es lo que hace que se
        # vea el mismo día en vez de descubrirse con un archivo de un trade.
        import json

        import wbj.tito.stores as st

        client.get("/api/projection-targets?ticker=DEMO")
        p = st.data_dir() / "trades" / "DEMO.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        for t in raw["trades"]:
            t.pop("id", None)
        p.write_text(json.dumps(raw), encoding="utf-8")

        checks = {c["check"]: c for c in client.get(
            "/api/tito-health?ticker=DEMO").json()["checks"]}
        assert checks["memoria.flows.sin_id"]["ok"] is False

    def test_un_cache_de_barras_envenenado_no_llega_al_motor(self, client):
        # BUG 2 de su `barsStore.ts`: `bars: "texto"` pasa su guarda de longitud
        # y el llamador recibe un string donde espera barras. `borde.barras_utiles`
        # lo trata como un cache que no está y se pide a la red.
        import json

        import wbj.tito.stores as st

        p = st.data_dir() / "bars"
        p.mkdir(parents=True, exist_ok=True)
        (p / "DEMO.json").write_text(json.dumps(
            {"ticker": "DEMO", "date": "2999-01-01", "bars": "texto"}), encoding="utf-8")
        r = client.get("/api/projection-targets?ticker=DEMO").json()
        assert r["ok"] is True

    def test_los_no_finitos_de_compute_no_salen_publicados(self, client, monkeypatch):
        """`to_row` SÍ produce NaN e Infinity: es su aritmética de JS, literal.

        Quien no los publica es `_json_safe`, que es el port de su
        `JSON.stringify` (NaN e Infinity → `null`). Es la misma y única línea de
        defensa que tiene él, y aquí se comprueba de punta a punta.
        """
        import json

        import wbj.tito.massive as MASS
        from wbj.tito.compute import to_row

        original = MASS.fetch_option_chain

        def con_basura(ticker, *a, **k):
            res = original(ticker, *a, **k)
            sucias = [
                to_row({"open_interest": "abc", "details": {"strike_price": 100,
                                                            "expiration_date": "2026-09-18"}}),
                to_row({"open_interest": 1e200, "last_trade": {"price": 1e200},
                        "details": {"strike_price": 1e200,
                                    "expiration_date": "2026-09-18"}}),
            ]
            return MASS.ChainResult(rows=list(res.rows) + sucias,
                                    underlying_price=res.underlying_price,
                                    pages=res.pages, truncated=res.truncated,
                                    expiration_count=res.expiration_count)

        monkeypatch.setattr(MASS, "fetch_option_chain", con_basura)
        crudo = client.get("/api/projection-targets?ticker=DEMO").text

        def estricto(c):
            raise ValueError(f"el endpoint publicó {c}")

        json.loads(crudo, parse_constant=estricto)
        assert "NaN" not in crudo and "Infinity" not in crudo


class TestFalloDeMassive:
    def test_reporta_el_motivo_y_no_publica_numero(self, client, monkeypatch):
        import wbj.tito.massive as MASS

        def boom(*a, **k):
            raise MASS.MassiveError("Falta MASSIVE_API_KEY en el entorno.")

        monkeypatch.setattr(MASS, "fetch_option_chain", boom)
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["ok"] is False
        assert d["source"] == "massive"
        assert "MASSIVE_API_KEY" in d["error"]
        assert "score" not in d  # no se publica un scorecard a medias

    def test_no_cae_a_yfinance(self, client, monkeypatch):
        import wbj.tito.massive as MASS
        monkeypatch.setattr(MASS, "fetch_daily_bars", lambda *a, **k: [])
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["ok"] is False  # sin barras corta, no busca otra fuente


class TestSinTape:
    def test_el_motor_sigue_con_la_cadena_y_lo_declara(self, client, monkeypatch):
        import wbj.tito.marketsnack as MS

        def boom(*a, **k):
            raise MS.MarketSnackError("Sesión de MarketSnack inválida o expirada.")

        monkeypatch.setattr(MS, "fetch_flow", boom)
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["ok"] is True                      # la cadena alcanza para seguir
        assert "MarketSnack" in d["flow_error"]     # pero se dice
        assert any("MarketSnack" in w for w in d["warnings"])
        assert d["scores"]["structure"] is not None  # el único que no depende del tape
        assert d["scores"]["aggression"] is None


class TestNoticias:
    def test_bandera_de_conflicto_con_flujo_alcista_y_noticias_malas(self, client):
        n = client.get("/api/tito-news?ticker=DEMO&call_pct=93").json()
        assert n["ok"] is True
        assert n["flow_bias"] == "bullish"
        assert n["bias"]["bias"] == "bearish"
        assert n["flag"]["kind"] == "conflict"

    def test_bandera_de_confirmacion_con_flujo_bajista(self, client):
        n = client.get("/api/tito-news?ticker=DEMO&call_pct=7").json()
        assert n["flag"]["kind"] == "confirm"

    def test_sin_call_pct_no_hay_bandera_inventada(self, client):
        assert client.get("/api/tito-news?ticker=DEMO").json()["flag"]["kind"] == "none"

    def test_promueve_el_titular_macro_que_nombra_a_la_empresa(self, client):
        n = client.get("/api/tito-news?ticker=DEMO&call_pct=93").json()
        assert len(n["promoted"]) == 1
        assert n["promoted"][0]["matched_by"]

    def test_declara_que_no_toca_los_cien_puntos(self, client):
        assert client.get("/api/tito-news?ticker=DEMO").json()["afecta_scorecard"] is False


class TestHealth:
    """El diagnóstico responde "¿por qué mi scorecard sale incompleto?"."""

    def test_con_todo_en_orden_no_reporta_pendientes(self, client):
        d = client.get("/api/tito-health?ticker=DEMO").json()
        por_check = {c["check"]: c for c in d["checks"]}
        assert por_check["motor"]["ok"] is True
        assert por_check["MASSIVE_API_KEY"]["ok"] is True
        assert por_check["massive.cadena"]["ok"] is True
        assert por_check["marketsnack.tape"]["ok"] is True

    def test_distingue_falta_la_key_de_la_key_no_sirve(self, client, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        d = client.get("/api/tito-health?ticker=DEMO").json()
        k = next(c for c in d["checks"] if c["check"] == "MASSIVE_API_KEY")
        assert k["ok"] is False
        assert "no está en el entorno" in k["detalle"]
        assert "Render" in k["arreglo"]
        # Sin key no se intenta la llamada: no tiene sentido preguntar dos veces.
        assert not any(c["check"] == "massive.cadena" for c in d["checks"])

    def test_cada_fallo_dice_su_impacto_y_su_arreglo(self, client, monkeypatch):
        monkeypatch.delenv("MARKETSNACK_COOKIE", raising=False)
        d = client.get("/api/tito-health?ticker=DEMO").json()
        for c in d["checks"]:
            if not c["ok"]:
                assert c["impacto"], c["check"]
                assert c["arreglo"], c["check"]

    def test_reconoce_la_cookie_caducada(self, client, monkeypatch):
        import wbj.tito.marketsnack as MS

        def caducada(*a, **k):
            raise MS.MarketSnackError("Sesión de MarketSnack inválida o expirada.")

        monkeypatch.setattr(MS, "fetch_flow", caducada)
        d = client.get("/api/tito-health?ticker=DEMO").json()
        c = next(x for x in d["checks"] if x["check"] == "marketsnack.tape")
        assert c["ok"] is False
        assert "DevTools" in c["arreglo"]  # el arreglo real, no "revisa la red"

    def test_avisa_del_disco_de_render(self, client, monkeypatch):
        import wbj.tito.stores as ST
        monkeypatch.setattr(ST, "data_dir", lambda: Path("/proc/imposible/tito"))
        d = client.get("/api/tito-health?ticker=DEMO").json()
        c = next(x for x in d["checks"] if x["check"] == "memoria.disco")
        assert c["ok"] is False
        assert "starter" in c["arreglo"]

    def test_nunca_devuelve_el_valor_de_una_credencial(self, client, monkeypatch):
        monkeypatch.setenv("MASSIVE_API_KEY", "SECRETO_NO_DEBE_SALIR_JAMAS")
        monkeypatch.setenv("MARKETSNACK_COOKIE", "COOKIE_SECRETA_TAMPOCO")
        body = client.get("/api/tito-health?ticker=DEMO").text
        assert "SECRETO_NO_DEBE_SALIR_JAMAS" not in body
        assert "COOKIE_SECRETA_TAMPOCO" not in body


class TestScorecardCompleto:
    def test_la_ruta_del_scorecard_tambien_responde(self, client):
        d = client.get("/api/tito-scorecard?ticker=DEMO").json()
        assert d["ok"] is True
        assert d["chain_source"] == "massive"
        assert set(d["scores"]) == {"aggression", "conviction", "unusuality",
                                    "structure", "iv_context", "validation"}


class TestHeatmapDeGex:
    """`gexHeatmap` estaba portado y sin endpoint: nadie lo llamaba.

    El GEX que ya sirve el scorecard es un agregado —un número por strike, con
    todos los vencimientos sumados— y eso esconde justo lo que el heatmap
    enseña: que un mismo strike puede ser muro esta semana y no serlo en enero.
    Es el `GexHeatmapCard` de su página.
    """

    def test_el_endpoint_sirve_el_heatmap(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        h = d["gex_heatmap"]
        assert h is not None, "el heatmap sigue sin llegar al panel"
        assert h["cells"], "sin celdas no hay mapa"
        assert h["strikes"] and h["expirations"]

    def test_las_celdas_son_strike_x_vencimiento(self, client):
        h = client.get("/api/projection-targets?ticker=DEMO").json()["gex_heatmap"]
        ejes = {(s["strike"]) for s in h["strikes"]}, {e["expiration"] for e in h["expirations"]}
        for c in h["cells"]:
            assert c["strike"] in ejes[0]
            assert c["expiration"] in ejes[1]
        # el mismo strike aparece en más de un vencimiento: es la dimensión que
        # el GEX agregado no tiene.
        por_strike = {}
        for c in h["cells"]:
            por_strike.setdefault(c["strike"], set()).add(c["expiration"])
        assert any(len(v) > 1 for v in por_strike.values())

    def test_la_intensidad_esta_normalizada(self, client):
        h = client.get("/api/projection-targets?ticker=DEMO").json()["gex_heatmap"]
        assert all(-1.0000001 <= c["intensity"] <= 1.0000001 for c in h["cells"])
        assert h["max_abs_cell"] > 0

    def test_el_spot_y_la_iv_son_los_del_scorecard(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["gex_heatmap"]["spot"] == d["spot"]
        assert d["gex_heatmap"]["iv"] == d["gex"]["iv"]

    def test_los_extremos_apuntan_a_una_celda_real(self, client):
        h = client.get("/api/projection-targets?ticker=DEMO").json()["gex_heatmap"]
        celdas = {(c["strike"], c["expiration"]) for c in h["cells"]}
        for k in ("hottest_positive", "hottest_negative"):
            if h[k] is not None:
                assert (h[k]["strike"], h[k]["expiration"]) in celdas

    def test_el_heatmap_no_puede_tumbar_los_targets(self, client, monkeypatch):
        # Ilustra, no decide: si revienta, el panel sigue dando escenarios.
        import wbj.tito.gex_heatmap as GH

        def boom(*a, **k):
            raise RuntimeError("heatmap roto")

        monkeypatch.setattr(GH, "gex_heatmap", boom)
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["ok"] is True
        assert d["gex_heatmap"] is None
        assert d["predictions"], "los targets tienen que seguir ahí"

    def test_es_JSON_estricto(self, client):
        import json

        h = client.get("/api/projection-targets?ticker=DEMO").json()["gex_heatmap"]
        crudo = json.dumps(h)

        def estricto(c):
            raise ValueError(c)

        json.loads(crudo, parse_constant=estricto)


class TestLaAdvertenciaDelSubagente6:
    """Sus bandas de puntos son una PROPUESTA: su PDF, a diferencia de los otros
    cinco, no trae tabla de puntuación.

    Bajo la regla innegociable del proyecto —"sin fórmula, no hay conclusión"—
    esa es la única parte del scorecard cuya escala no tiene respaldo
    documental, y **tiene que declararse en cada reporte que la use**. Hasta
    ahora eso vivía solo en un docstring y en un `warnings.append`; nada
    comprobaba que llegara al otro extremo del cable.
    """

    def _con_memoria(self, client):
        """Siembra flows VIEJOS y consulta.

        El sub-agente 6 solo puntúa sobre flows con recorrido: mide qué hizo el
        precio DESPUÉS. Con el tape de hoy —que es lo que da el doble— nunca
        hay nada resuelto y el test se saltaría, que es no probar nada. Se
        escriben directamente en el store, que es de donde los lee.
        """
        import wbj.tito.stores as st

        viejos = [{
            "id": 9000 + i,
            "timestamp": (NOW - timedelta(days=45 - i)).isoformat(),
            "symbol": f"DEMO270115C0010{i}000", "underlying": "DEMO",
            "type": "call", "strike": 100.0 + i, "expiration": "2027-01-15",
            "dte": 200, "price": 9.2, "size": 800, "side": "AT_ASK",
            "aggression": "ask", "asset_price": 92.0 + i, "bid": 9.14, "ask": 9.26,
            "premium": 736_000.0, "delta": 0.62, "gamma": 0.03, "theta": -0.04,
            "vega": 0.3, "theta_pct_daily": 0.43, "iv": 0.44,
            "open_interest": 4000, "volume": 5200, "score": 8,
            "sentiment": "bullish", "condition_code": None, "condition_name": None,
            "expiry_status": "vigente",
        } for i in range(8)]
        st.save_trades("MEM6", viejos)
        return client.get("/api/projection-targets?ticker=MEM6").json()

    def test_el_subagente_6_puntua_de_verdad_en_este_montaje(self, client):
        """Guarda de la guarda: si esto se salta, los dos de abajo no prueban nada."""
        d = self._con_memoria(client)
        assert d["scores"]["validation"] is not None, \
            "el montaje ya no ejercita el sub-agente 6; los tests de abajo son humo"

    def test_si_el_subagente_6_puntua_la_advertencia_viaja_en_el_json(self, client):
        d = self._con_memoria(client)
        assert any("PROPUESTA" in w for w in d["warnings"]), \
            "el score se publicó sin declarar que su escala no tiene respaldo"

    def test_y_lo_dice_con_el_motivo_no_solo_la_etiqueta(self, client):
        d = self._con_memoria(client)
        aviso = next(w for w in d["warnings"] if "PROPUESTA" in w)
        assert "no trae tabla" in aviso and "6" in aviso

    def test_sin_score_del_subagente_6_no_se_declara_nada(self, client, monkeypatch):
        # Sin tape no hay Confirmación de Precio: la advertencia sobraría, y una
        # advertencia que sale siempre deja de leerse.
        import wbj.tito.marketsnack as MS
        from wbj.tito.marketsnack import MarketSnackError

        monkeypatch.setattr(MS, "fetch_flow",
                            lambda *a, **k: (_ for _ in ()).throw(MarketSnackError("x")))
        d = client.get("/api/projection-targets?ticker=SINTAPE").json()
        assert d["scores"].get("validation") is None
        assert not any("PROPUESTA" in w for w in d["warnings"])

    def test_la_gráfica_pinta_las_advertencias(self):
        """El JSON no sirve de nada si el panel no las muestra."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "(d.warnings || [])" in html
        assert "⚠" in html


class TestElPanelNoTiraNadaDelPayload:
    """Cada campo que el motor sirve tiene que llegar a la pantalla.

    Un campo calculado, serializado y nunca pintado es peor que no calcularlo:
    cuesta lo mismo, da la sensación de que la funcionalidad está, y nadie se
    entera de que falta. Pasó con SEIS a la vez —las 6 puntuaciones de los
    sub-agentes, los niveles, el mapa de GEX, los racimos, la memoria y el
    significado del veredicto—: el motor los servía desde el primer día y el
    panel los leía en una variable que no usaba nadie.
    """

    #: Campos de infraestructura, no de pantalla. Cada uno con su motivo.
    _NO_SE_PINTAN = {
        "ok": "lo consume el `if (!d.ok)` del cargador",
        "ticker": "va en el título del panel, que lo pone `renderProjections`",
        "engine": "sello de procedencia para depurar, no dato de mercado",
        "chain_source": "idem: de qué proveedor salió la cadena",
        "history": "son las velas; las dibuja la gráfica, no el texto",
        "levels_for_chart": "el recorte de niveles para la gráfica",
        "chart_geometry": "el cono y las rutas que dibuja la gráfica",
        "flow_error": "se promueve a `warnings[0]`, que sí se pinta",
    }

    @staticmethod
    def _payload_keys():
        import re
        api = (ROOT / "vertex_api.py").read_text(encoding="utf-8")
        cuerpo = api[api.index("def _tito_json(r):"):api.index("def _tito_call_pct(r):")]
        ruta = api[api.index("def projection_targets("):api.index("def _tito_chart_geometry(")]
        return (set(re.findall(r'^\s{8}"(\w+)":', cuerpo, re.M))
                | set(re.findall(r'out\["(\w+)"\]', ruta)))

    def test_cada_campo_servido_tiene_consumidor_en_el_panel(self):
        import re
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        huerfanos = []
        for k in sorted(self._payload_keys()):
            if k in self._NO_SE_PINTAN:
                continue
            if not re.search(rf"\bd\.{k}\b|\['{k}'\]|\[\"{k}\"\]", html):
                huerfanos.append(k)
        assert not huerfanos, (
            f"el motor sirve {huerfanos} y el panel no los pinta. O se cablean, "
            f"o se declaran en `_NO_SE_PINTAN` con su motivo.")

    def test_el_registro_de_no_pintados_no_miente(self):
        sobran = sorted(set(self._NO_SE_PINTAN) - self._payload_keys())
        assert not sobran, f"declarados como no pintados pero ya no se sirven: {sobran}"

    def test_las_seis_categorias_del_scorecard_estan_en_el_panel(self):
        # Nombres, pesos y preguntas son literalmente los de su `ScorecardPanel`.
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        from wbj.tito.prediction import WEIGHTS
        for clave, peso in WEIGHTS.items():
            assert f"'{clave}'" in html, f"la categoría {clave} no aparece en el panel"
        for nombre in ("Agresividad", "Convicción", "Inusualidad", "Estructura",
                       "Contexto IV", "Confirmación de Precio"):
            assert nombre in html, f"falta el nombre {nombre!r} de su ScorecardPanel"
        for pregunta in ("¿Compran al ask con fuerza?", "¿Cuánto dinero real entró?",
                         "¿Es flujo anormal?", "¿Strike/DTE de convicción o lotería?",
                         "¿IV limpia o inflada?", "¿El precio valida o absorbe?"):
            assert pregunta in html, f"falta la pregunta {pregunta!r}"
        # Y los pesos, que son los que convierten el 0-10 en puntos del 0-100.
        assert "['aggression', 'Agresividad', 20," in html
        assert "['iv_context', 'Contexto IV', 10," in html

    def test_los_bloques_nuevos_se_llaman_desde_el_render(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        render = html[html.index("function renderVictorTargets(d) {"):]
        render = render[:render.index("\nfunction ")]
        for fn in ("vcScorecardHTML", "vcLevelsHTML", "vcHeatmapHTML",
                   "vcClustersHTML", "vcMemoryHTML"):
            assert f"function {fn}(" in html, f"{fn} no existe"
            assert f"{fn}(d)" in render, f"{fn} existe pero nadie lo llama"
