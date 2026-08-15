"""Cableado end-to-end del motor de Víctor dentro de Vertex.

La suite de `engine/tests/tito/` prueba la lógica pura. Esto prueba lo otro: que
los endpoints existen, que llaman al motor, que traducen bien a JSON y que
degradan con el motivo exacto cuando una fuente falla.

Sin red: Massive, MarketSnack y los feeds se sustituyen por dobles. Lo que se
verifica es el CABLEADO, no los datos.

    python -m pytest tests_vertex/test_tito_wiring.py -q
"""

from __future__ import annotations

import json
import math
import re
import inspect
import os
import sys
import tempfile
import time
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


@pytest.fixture
def wheel_dobles(monkeypatch):
    """Cadena de puts + barras + filings, sin tocar red."""
    import wbj.tito.bars_store as BS
    import wbj.tito.earnings as EA
    import wbj.tito.massive as MASS
    from wbj.tito.massive import DailyBar, WheelChainQuote, WheelChainResult

    def chain(t, dmin, dmax, now=None, **k):
        spot, q = 100.0, []
        for s in range(70, 101, 5):
            # Bid/ask coherentes con una IV del 35%: si no, la bisección da
            # deltas fuera de la banda del preset y no sale ningún candidato.
            from wbj.tito.black_scholes import bs_price
            px = bs_price(spot, float(s), 38 / 365, 0.35, "put")
            # `last_trade` va poblado a propósito: en la cadena real sale de
            # SU cascada `last_trade → day.close → day.vwap`, y es el precio
            # que sostiene la prima cuando no hay horquilla.
            q.append(WheelChainQuote(strike=float(s), expiration="2026-09-18", dte=38,
                                     bid=round(px * 0.97, 2), ask=round(px * 1.03, 2),
                                     last_trade=round(px, 2), open_interest=600))
        return WheelChainResult(spot=spot,
                                quotes=[x for x in q if dmin <= x.dte <= dmax])

    def barras(t, days=365, now=None, **k):
        """Camino aleatorio con volatilidad REALISTA (~28% anualizada).

        No es un detalle del doble: sin horquilla, la IV implícita no se puede
        despejar y el delta se calcula con la volatilidad **realizada** de
        estas barras. Con una serie demasiado plana los deltas salen casi
        cero, ningún strike cae en la banda del preset y el escenario que se
        quiere probar se convierte en "fuera de banda".
        """
        out, seed, c = [], 7, 100.0
        for i in range(300):
            seed = (seed * 1103515245 + 12345) % 2147483648
            c *= 1 + ((seed / 2147483648) - 0.5) * 0.035
            out.append(DailyBar((NOW - timedelta(days=300 - i)).date().isoformat(),
                                c, c * 1.01, c * 0.99, c))
        # El último cierre ES el spot de la cadena doblada. Sin anclarlo, el
        # respaldo por barras devuelve un precio distinto al de la cadena y la
        # banda de delta se desplaza.
        out[-1] = DailyBar(out[-1].time, 100.0, 101.5, 98.5, 100.0)
        return out

    monkeypatch.setattr(MASS, "fetch_wheel_chain", chain)
    monkeypatch.setattr(BS, "cached_daily_bars", barras)
    monkeypatch.setattr(EA, "fetch_filing_dates",
                        lambda t, **k: ["2026-05-01", "2026-02-01", "2025-11-01"])


@pytest.fixture
def mercado(monkeypatch):
    """El barrido SIN filtro de símbolo — su `fetchMarketFlow`."""
    import wbj.tito.marketsnack as MS
    from wbj.tito.marketsnack import FlowResult

    def fake(**k):
        tr = []
        for i, (sym, strike, side) in enumerate(
            [("NVDA270115C00180000", 180, "AT_ASK"),
             ("TSLA260918C00420000", 420, "ABOVE_ASK"),
             ("AAPL270115P00200000", 200, "AT_BID")]
        ):
            cp = "C" if "C00" in sym else "P"
            tr.append({
                "id": i + 1, "symbol": sym, "price": 12.5, "size": 900, "side": side,
                "bid_price": 12.4, "ask_price": 12.6, "premium": 12.5 * 900 * 100,
                "delta": 0.61 if cp == "C" else -0.31, "gamma": 0.02, "theta": -0.05,
                "vega": 0.3, "implied_volatility": 0.42, "open_interest": 5000,
                "volume": 6000, "score": 9, "sentiment": "bullish",
                "timestamp": (NOW - timedelta(minutes=i * 3 + 1)).isoformat(),
                "asset_price": strike * 0.98, "trade_condition_id": 231,
            })
        return FlowResult(trades=tr, pages=1, truncated=False)

    monkeypatch.setattr(MS, "fetch_market_flow", fake)
    return fake


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
    # El snapshot del subyacente. Es el PRIMER eslabón del spot en su `page.tsx`,
    # así que sin doble la suite salía a la red de verdad y esperaba el timeout
    # en cada caso: 22 s se convirtieron en 55 s.
    monkeypatch.setattr(MASS, "fetch_company",
                        lambda t, **k: {"ticker": t, "name": "Demo Corporation",
                                        "price": SPOT, "prev_close": SPOT - 1})
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


class TestElSpotSaleDeSuFuente:
    """`company?.price ?? chainMeta?.underlyingPrice ?? bars[last].close`.

    El orden es suyo y no es un detalle: el spot ancla los nodos del GEX, la
    ventana de ±20% que decide qué strikes entran, los niveles, el cono y los
    tres targets. El port se saltaba el primer eslabón —el snapshot del
    subyacente— y usaba el precio con el que Massive calculó la CADENA, que no
    es el mismo cuando la cadena viene de caché o el papel se movió después.
    """

    def test_manda_el_snapshot_de_la_empresa(self, client, monkeypatch):
        import wbj.tito.massive as MASS
        monkeypatch.setattr(MASS, "fetch_company", lambda t, **k: {"price": 123.45})
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["spot"] == 123.45, "el precio de la cadena ganó al snapshot"

    def test_sin_snapshot_cae_al_precio_de_la_cadena(self, client, monkeypatch):
        import wbj.tito.massive as MASS
        monkeypatch.setattr(MASS, "fetch_company", lambda t, **k: None)
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["spot"] == SPOT   # `underlying_price` del doble de cadena

    def test_el_nulo_baja_pero_el_cero_NO_baja(self, client, monkeypatch):
        """Su `??` solo salta el nulo. Un `price: 0` se queda en 0 y entonces su
        guarda `if (!spot || spot <= 0) return null` corta la lectura.

        Es deliberado: si el snapshot dice 0, el feed está mal, y bajar en
        silencio a otro precio es el fallback callado que este proyecto trata
        como peor que un error. Aquí se corta con el motivo escrito."""
        import wbj.tito.massive as MASS
        # `null`/ausente → baja al precio de la cadena.
        for nulo in ({"price": None}, {}):
            monkeypatch.setattr(MASS, "fetch_company", lambda t, _v=nulo, **k: _v)
            assert client.get("/api/projection-targets?ticker=DEMO").json()["spot"] == SPOT
        # 0, negativo o basura → NO baja: no se publica lectura.
        for malo in (0, -5, "abc", True):
            monkeypatch.setattr(MASS, "fetch_company", lambda t, _m=malo, **k: {"price": _m})
            d = client.get("/api/projection-targets?ticker=DEMO").json()
            assert d["ok"] is False, f"un price={malo!r} produjo un scorecard"
            assert "score" not in d


class TestElTabEsSoloDeVictor:
    """Nada de la Vertex vieja puede quedar en la pantalla del tab."""

    @staticmethod
    def _dom():
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        dom = html[html.index('<main id="projectionsView"'):]
        return re.sub(r"<!--.*?-->", "", dom[:dom.index("</main>")], flags=re.S)

    def test_no_queda_copy_de_la_vertex_vieja(self):
        """La cabecera decía "Proyecciones GEX", el vacío pedía "presiona
        Proyectar" y el botón de refresco seguía prometiendo dark pool — de
        Quant Data, que salió del tab hace tres rondas."""
        for viejo in ("Proyecciones GEX", "presiona", "dark pool",
                      "Generar tesis AI completa", "Plan de operación"):
            assert viejo not in self._dom(), f"queda el copy viejo «{viejo}»"

    def test_esta_su_cabecera_y_el_tab_no_exige_ticker(self):
        """Su app tiene cuatro pestañas y solo una —el dashboard— pide símbolo.
        La de *Ideas* escanea el mercado entero sin que escribas nada, y el
        vacío del panel Ticker apunta a ella."""
        dom = self._dom()
        # Sin logo ni nombre propio: la marca de la pantalla es Vertex, que ya
        # está arriba. Queda la etiqueta de qué es esto.
        assert "AI Options Agent" in dom
        assert "Tito Metralleta" not in dom
        assert "Analiza un ticker" in dom          # su copy, para el panel Ticker
        assert "escanea el mercado entero y no pide nada" in dom
        assert 'id="projIdeas"' in dom

    def test_la_navegacion_va_en_la_cabecera_y_NUNCA_se_esconde(self):
        """El fallo: `projNav` y los tres paneles nuevos quedaron ANIDADOS
        dentro de `projPaneTicker`. Al abrir Ideas se ocultaba el padre y con
        él la navegación entera — la pantalla se quedaba en negro y sin forma
        de volver. En su app la `NavTabs` vive en el `HeaderBar`, arriba y
        siempre visible, junto al buscador.
        """
        import re as _re
        dom = self._dom()
        prof, nivel = 0, {}
        for ln in dom.split("\n"):
            m = _re.search(r'id="(projNav|projPane\w+|projBuscador)"', ln)
            if m:
                nivel[m.group(1)] = prof
            prof += len(_re.findall(r"<div\b", ln)) - len(_re.findall(r"</div>", ln))
        # Los cuatro paneles son HERMANOS, al mismo nivel.
        paneles = [nivel[k] for k in ("projPaneTicker", "projPaneIdeas",
                                      "projPaneWheel", "projPaneTape")]
        assert len(set(paneles)) == 1, f"los paneles no son hermanos: {nivel}"
        # …y ni la navegación ni el buscador cuelgan de ninguno de ellos.
        assert nivel["projNav"] > paneles[0], "la navegación tiene que ir en la cabecera"
        assert nivel["projBuscador"] > paneles[0]
        assert prof == 0, "los <div> del tab no cierran"

    def test_la_cabecera_lleva_SU_orden(self):
        """`HeaderBar.tsx`: marca → NavTabs → tickers rápidos → buscador."""
        dom = self._dom()
        orden = [dom.index("AI Options Agent"), dom.index('id="projNav"'),
                 dom.index('id="projQuick"'), dom.index('id="projTicker"')]
        assert orden == sorted(orden), "la cabecera no sigue el orden de su HeaderBar"

    def test_al_abrir_el_tab_arranca_el_screener_no_un_cartel(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        # Entra por Ideas, que no pide nada.
        assert "let vcTabActiva = 'ideas';" in html
        assert "vcAbreTab(vcTabActiva);" in html
        # …y en cuanto hay ticker, manda el análisis.
        assert "if (vcTabActiva !== 'tape') vcAbreTab('ticker');" in html

    def test_estan_sus_cuatro_pestanas_con_su_orden(self):
        """`NavTabs.tsx`: Ticker / Ideas / Wheel / Time & Sales, en ese orden.

        Las SUYAS van primero y en su orden. La quinta —Cobertura— es de
        Vertex y va al final: su app no la tiene porque su despliegue es local
        y él ve el log; aquí el agente corre en un servidor que nadie mira, y
        «¿con qué dato salió este número?» no se puede contestar de otra forma.
        """
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("const VC_TABS = [")
        bloque = html[i:html.index("];", i)]
        orden = re.findall(r"\['(\w+)',", bloque)
        assert orden[:4] == ["ticker", "ideas", "wheel", "tape"], orden
        assert orden[4:] == ["salud"], f"pestaña de más sin declarar: {orden[4:]}"
        for texto in ("Ticker", "Ideas", "Wheel", "Time &amp; Sales"):
            assert texto.replace("&amp;", "&") in bloque or texto in bloque
        dom = self._dom()
        for pane in ("projPaneTicker", "projPaneIdeas", "projPaneWheel", "projPaneTape"):
            assert f'id="{pane}"' in dom, f"falta el panel {pane}"

    def test_el_refresco_silencioso_no_te_saca_de_la_pestana(self):
        """El auto-refresco entra por `loadProjections(..., {silent:true})`.
        Sin guarda te arrancaría de Ideas o de Wheel cada minuto para
        plantarte en el panel de Ticker."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("async function loadProjections(tickerArg, opts) {")
        cuerpo = _sin_comentarios(html[i:i + 4000])
        # El salto de panel tiene que estar DENTRO de un `if (!silent)`.
        j = cuerpo.index("vcAbreTab('ticker')")
        guarda = cuerpo.rindex("if (!silent) {", 0, j)
        assert guarda > 0, "el salto de panel no está protegido"
        # …y el bloque no se cierra antes de llegar al salto.
        assert "}" not in cuerpo[guarda:j].split("vcTabActiva")[0]

    def test_cada_pestana_carga_BAJO_DEMANDA(self):
        """Entrar a Proyecciones no puede disparar cuatro escaneos: entre Wheel
        (40 tickers × 2 llamadas) e Ideas (el mercado entero) sería quemar la
        cuota de golpe."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function vcAbreTab(id) {")
        cuerpo = _sin_comentarios(html[i:html.index("/* ── WHEEL", i)])
        assert "if (vcTabCargada[id]) return;" in cuerpo
        for fn in ("loadProjIdeas()", "loadProjWheel()", "loadProjTape()"):
            assert fn in cuerpo, f"{fn} no se dispara al abrir su pestaña"

    def test_el_buscador_se_esconde_donde_no_va(self):
        """Ideas y Wheel escanean el mercado entero: un cuadro de ticker ahí
        promete un filtro que no existe."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert 'id="projBuscador"' in self._dom()
        assert "id !== 'ticker' && id !== 'tape'" in html

    def test_no_hay_que_teclear_estan_sus_cuatro_tickers(self):
        """Su `HeaderBar` lleva `QUICK = ["TSLA","NVDA","SPY","AAPL"]`: se hace
        clic, no se teclea. El tab exigía escribir el símbolo antes de enseñar
        nada."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "const VC_QUICK = ['TSLA', 'NVDA', 'SPY', 'AAPL'];" in html
        assert 'id="projQuick"' in self._dom()
        assert "vcPintaQuick(ticker);" in html   # se repinta al cargar

    def test_el_plan_de_operacion_ya_no_vive_aqui(self):
        """Era de `/api/analyze` —el agente de Vertex—, no suyo."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        for id_ in ("projOpPlan", "projOpPlanFor", "qtTradePlan", "qtTradePlanBody"):
            assert id_ not in html, f"{id_} sigue en la página"
        for fn in ("syncOpPlanVisibility", "projGenerarTesis"):
            assert f"function {fn}(" not in html and f"{fn}(" not in html


class TestElPanelDiceQueEstaApagadoYPorQue:
    """Con la cookie de MarketSnack caída, 4 de los 6 sub-agentes se apagan."""

    def test_el_payload_nombra_las_categorias_sin_dato_y_el_arreglo(self, client, monkeypatch):
        import wbj.tito.marketsnack as MS

        def boom(*a, **k):
            raise MS.MarketSnackError("La cookie de MarketSnack caducó.")

        monkeypatch.setattr(MS, "fetch_flow", boom)
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        ap = d["subagentes_apagados"]
        assert ap["de"] == 6 and ap["total"] >= 4
        cats = {c for g in ap["grupos"] for c in g["categorias"]}
        assert {"Agresividad", "Convicción", "Inusualidad", "Contexto IV"} <= cats
        arreglos = " ".join(g["arreglo"] for g in ap["grupos"])
        assert "MARKETSNACK_COOKIE" in arreglos and "CADUCA" in arreglos

    def test_con_las_seis_encendidas_no_hay_aviso(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        apagadas = [k for k, v in d["scores"].items() if v is None]
        if not apagadas:
            assert "subagentes_apagados" not in d

    def test_el_aviso_se_pinta_ARRIBA_no_en_9px_al_final(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "function vcApagadosHTML(d) {" in html
        bloque = html[html.index("    el.innerHTML = `\n      ${vcApagadosHTML(d)}"):][:400]
        # Antes que el veredicto, que antes que los targets.
        assert "vcApagadosHTML" in bloque

    def test_sin_cinta_el_screener_no_manda_a_escribir_un_ticker(self):
        """Era un consejo falso: con la cinta caída el análisis por ticker
        también sale cojo, así que escribir el símbolo no arregla nada."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("async function loadProjIdeas() {")
        cuerpo = html[i:html.index("function renderProjIdeas(d) {")]
        assert "Escribe un ticker arriba para el" not in cuerpo
        assert "MARKETSNACK_COOKIE no es una API key" in cuerpo
        assert "d.source === 'marketsnack'" in cuerpo


class TestElHorizonteSeVeYSeCambia:
    """Kevin: «no me dice a cuánto tiempo son los targets, ni me deja ver el
    horizonte ni cambiarlo»."""

    @staticmethod
    def _render():
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderVictorTargets(d) {")
        return _sin_comentarios(html[i:html.index("function renderProjections(d) {")])

    def test_el_selector_de_horizonte_esta_rotulado(self):
        r = self._render()
        assert "Horizonte" in r
        assert "'Esta semana'" in r and "'2 semanas'" in r and "'1 mes'" in r

    def test_cada_target_dice_su_plazo(self):
        r = self._render()
        assert "Targets a ${h} días" in r
        assert "a ${h} días" in r          # y también en cada card

    def test_la_cobertura_parcial_NO_esconde_los_targets(self):
        """Sus DOS `caveat` no son el mismo aviso: baja liquidez es "no
        operar"; `active < 6` dice que la confianza está recortada. La ronda 6
        los colapsó y escondía los targets también con cobertura parcial — que
        es el estado exacto en el que deja el panel una cookie caducada."""
        r = self._render()
        assert "const noFiable = !!((d.gex || {}).low_liquidity);" in r
        assert "${noFiable ? '' :" in r, "los targets solo se ocultan por baja liquidez"
        assert "${p.caveat ? '' :" not in r, "volvió a esconderlos por cobertura parcial"

    def test_los_targets_desaparecen_SI_la_cadena_es_ilíquida(self):
        r = self._render()
        assert "Datos no fiables — no operar" in r


class TestIdeasDelMercado:
    """`/api/tito-ideas` — su `/api/ideas`, el screener que no pide ticker."""

    def test_escanea_sin_ticker_y_devuelve_ideas(self, client, monkeypatch, mercado):
        d = client.get("/api/tito-ideas").json()
        assert d["ok"] is True and d["engine"] == "victor/tito"
        assert d["ideas"] and d["tickers"] >= 1

    def test_usa_SUS_parametros_de_escaneo(self, client, mercado):
        """`MIN_PREMIUM=100_000`, `PERIOD="1d"`, `MONEYNESS_CAP=0.25` — los de
        su `route.ts` después del commit 53d5a20."""
        d = client.get("/api/tito-ideas").json()
        assert d["min_premium"] == 100_000
        assert d["period"] == "1d"
        assert d["moneyness_cap"] == 0.25

    def test_declara_POR_QUE_se_cayo_cada_contrato(self, client, mercado):
        """Sin el desglose, "0 ideas" y "el mercado está tranquilo" se ven
        igual. Sus cinco motivos, incluido el `lejano` del commit nuevo."""
        d = client.get("/api/tito-ideas").json()
        assert set(d["rejected"]) == {"theta_alto", "vencido", "sin_theta",
                                      "no_inusual", "lejano"}

    def test_el_sizing_sale_del_perfil_del_servidor(self, client, mercado):
        """DIVERGENCIA DECLARADA: su ruta devuelve los griegos y nada más
        porque su app no tiene perfil de inversionista. Esta sí lo tiene, en el
        servidor, así que la capa 2 de su propio `risk.py` corre aquí.

        El motor es el suyo: se comprueba que el techo de contratos coincide
        con lo que `size_flow` devuelve para el mismo perfil. Si alguien cambia
        el perfil por defecto o la fórmula, esto se cae.
        """
        import vertex_api as V
        from wbj.tito.risk import RiskProfile, size_flow

        d = client.get("/api/tito-ideas").json()
        p = d["perfil"]
        assert p["riesgo_por_trade"] == pytest.approx(
            p["capital"] * p["riesgo_pct"] / 100, abs=0.01)
        assert p["caben"] == sum(1 for i in d["ideas"]
                                 if (i["sizing"] or {}).get("max_contracts"))
        assert any(i["sizing"] for i in d["ideas"]), "ninguna idea trae sizing"

    def test_lo_que_no_te_cabe_se_baja_pero_NO_se_esconde(self, client, mercado):
        """Que una operación esté fuera de tu presupuesto es información, no
        ruido. Se ordena —las que caben primero— y se marca; nunca se filtra."""
        d = client.get("/api/tito-ideas").json()
        caben = [bool((i["sizing"] or {}).get("max_contracts")) for i in d["ideas"]]
        assert caben == sorted(caben, reverse=True), "las que caben no van primero"

    def test_el_perfil_cambia_el_orden_y_el_techo(self, client, monkeypatch, mercado):
        """La prueba de que el perfil MANDA: con $1,000 caben menos ideas que
        con $250,000, sobre exactamente la misma cinta."""
        import vertex_api as V

        base = V._perfil_leer()
        # `_perfil_leer` recibe el `request` desde que el perfil es por usuario:
        # el doble tiene que aceptarlo o la ruta revienta al llamarlo.
        monkeypatch.setattr(V, "_perfil_leer",
                            lambda request=None: {**base, "capital": 1_000.0,
                                                  "riesgo_pct": 15.0,
                                                  "riesgo_por_trade": 150.0})
        pobre = client.get("/api/tito-ideas").json()
        monkeypatch.setattr(V, "_perfil_leer",
                            lambda request=None: {**base, "capital": 250_000.0,
                                                  "riesgo_pct": 15.0,
                                                  "riesgo_por_trade": 37_500.0})
        rico = client.get("/api/tito-ideas").json()

        assert pobre["perfil"]["capital"] == 1_000
        assert rico["perfil"]["caben"] >= pobre["perfil"]["caben"]
        assert len(rico["ideas"]) == len(pobre["ideas"]), \
            "el perfil ordena y marca; no puede borrar ideas de la lista"

    def test_el_umbral_es_el_del_SCREENER_no_el_institucional(self):
        from wbj.tito.risk import IDEA_UNUSUAL_THRESHOLD
        from wbj.tito.flow import UNUSUAL_TRADE_THRESHOLD
        assert IDEA_UNUSUAL_THRESHOLD == 5 < UNUSUAL_TRADE_THRESHOLD == 7

    def test_sin_cinta_lo_dice_y_no_inventa_ideas(self, client, monkeypatch):
        import wbj.tito.marketsnack as MS

        def boom(**k):
            raise MS.MarketSnackError("La cookie de MarketSnack caducó.")

        monkeypatch.setattr(MS, "fetch_market_flow", boom)
        d = client.get("/api/tito-ideas").json()
        assert d["ok"] is False and d["source"] == "marketsnack"
        assert "cadu" in d["error"]          # el motivo nuestro pasa entero
        assert "ideas" not in d

    def test_el_titular_cuenta_las_que_CABEN_no_todas_las_filas(self):
        """Su titular es `operables` = las que sí puedes operar, más «N
        descartadas». Aquí decía «N operables» con N = la tabla entera,
        incluidas las que el propio panel marca «no cabe» dos columnas más a la
        derecha. Con $1.000 de capital eso no es cosmético: casi todas las filas
        de una corrida caen del lado de «no cabe», así que el titular decía lo
        contrario de lo que decía la tabla."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjIdeas(d) {")
        cuerpo = html[i:html.index("\n/** La ÚNICA llamada de datos", i)]
        assert "${ideas.length} operables" not in cuerpo
        assert "d.perfil.caben" in cuerpo, "el titular tiene que salir del conteo del motor"
        assert "no te caben" in cuerpo, "y las descartadas tienen que decirse"

    def test_el_horizonte_del_sizing_se_declara(self, client, mercado):
        """`size_flow` quema theta HASTA una fecha, así que el techo de
        contratos cambia con el horizonte. Sin decir cuál se usó, «te caben 3»
        es un número sin unidad. Su app lo elige con un botón; aquí sale del
        perfil, que es la parte que sí es de Kevin — pero tiene que verse."""
        p = client.get("/api/tito-ideas").json()["perfil"]
        assert p["horizonte_dias"] > 0
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function vcCabeceraPerfil(")
        assert "P.horizonte_dias" in html[i:html.index("\nasync function", i)]

    def test_el_disclaimer_del_precio_de_ejecucion_esta(self):
        """Su disclaimer de `/ideas`, que faltaba entero: el tamaño NO sale de
        la cotización viva del contrato —el feed no la da— sino del precio al
        que se ejecutó el flow. Sin decirlo, el número parece de ahora."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjIdeas(d) {")
        cuerpo = html[i:html.index("\n/** La ÚNICA llamada de datos", i)]
        assert "precio al que se ejecut" in cuerpo
        assert "cotizaci&oacute;n viva" in cuerpo
        assert "no es consejo financiero" in cuerpo


class TestElTabSeArmaAlEntrarPorElMENU:
    """El fallo: la inicialización colgaba de la barra de comandos (Cmd+K).

    Entrando por el menú —que es como se entra— no se pintaba ni la navegación
    de pestañas ni los tickers rápidos: quedaba el DOM crudo y el único texto
    visible era "Analiza un ticker". Todo el trabajo estaba hecho y no lo
    llamaba nadie.
    """

    def test_switchView_arma_proyecciones(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function switchView(viewId) {")
        cuerpo = _sin_comentarios(html[i:html.index("\n}", i)])
        assert "viewId === 'projectionsView'" in cuerpo
        assert "vcArrancaProyecciones()" in cuerpo

    def test_el_arranque_pinta_las_cuatro_piezas(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function vcArrancaProyecciones() {")
        cuerpo = _sin_comentarios(html[i:html.index("\n}", i)])
        for pieza in ("vcPintaNav()", "vcPintaQuick(", "vcAbreTab(", "vcArrancaVivo()"):
            assert pieza in cuerpo, f"el arranque no llama a {pieza}"

    def test_TODAS_las_entradas_al_tab_pasan_por_switchView(self):
        """Si mañana alguien añade otro botón que enseñe el tab sin pasar por
        `switchView`, vuelve el DOM crudo. Aquí se fija que no hay atajos."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        sin_comentarios = _sin_comentarios(html)
        # Nadie puede quitarle el `hidden` al tab a mano.
        sospechosos = re.findall(
            r"getElementById\(['\"]projectionsView['\"]\)[^;\n]*classList\.remove",
            sin_comentarios)
        assert not sospechosos, f"alguien enseña el tab sin pasar por switchView: {sospechosos}"
        # Y el menú móvil también pasa por ahí.
        i = html.index("function mobileGo(viewId){")
        assert "switchView(viewId)" in html[i:i + 200]

    def test_el_arranque_es_idempotente(self):
        """Entrar diez veces al tab no puede relanzar diez veces el escaneo ni
        dejar diez temporizadores corriendo."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function vcArrancaProyecciones() {")
        cuerpo = _sin_comentarios(html[i:html.index("\n}", i)])
        assert "!vcVivoTimer" in cuerpo, "el temporizador en vivo se duplicaría"
        j = html.index("function vcAbreTab(id) {")
        assert "if (vcTabCargada[id]) return;" in html[j:j + 900]


class TestEnVivoSinBotones:
    """Fuera el botón de recargar y la casilla "auto": el panel se mantiene solo."""

    def test_no_queda_ningun_control_manual_de_refresco(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        dom = html[html.index('<main id="projectionsView"'):]
        dom = re.sub(r"<!--.*?-->", "", dom[:dom.index("</main>")], flags=re.S)
        for id_ in ("projRefreshBtn", "projAutoRefresh", "projAutoState", "projRefreshTs"):
            assert id_ not in dom, f"{id_} sigue en el tab"
        for fn in ("projToggleAuto", "projRefresh"):
            assert f"function {fn}(" not in html and f"{fn}(" not in html

    def test_el_indicador_dice_la_HORA_no_solo_un_punto_verde(self):
        """Ni Massive ni MarketSnack empujan nada: las dos son REST y esto es
        sondeo. Un punto verde sin hora prometería streaming."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function vcPintaVivo() {")
        cuerpo = html[i:html.index("function vcRefrescaActiva", i)]
        assert "hace ${seg}s" in cuerpo and "min" in cuerpo
        assert "mercado cerrado" in cuerpo
        assert "toLocaleTimeString()" in cuerpo   # la hora exacta, en el tooltip

    def test_solo_se_refresca_la_pestana_ACTIVA_y_visible(self):
        """Sondear las cuatro a la vez, o una pestaña de fondo, quema cuota
        para nadie."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function vcArrancaVivo() {")
        cuerpo = _sin_comentarios(html[i:i + 1800])
        assert "document.visibilityState !== 'visible'" in cuerpo
        assert "view.classList.contains('hidden')" in cuerpo
        assert "vcVivoUltimo[vcTabActiva]" in cuerpo
        # …y solo la activa vuelve a pedir.
        j = html.index("function vcRefrescaActiva() {")
        act = _sin_comentarios(html[j:html.index("function vcArrancaVivo", j)])
        assert act.count("vcTabActiva ===") == 4

    def test_la_cadencia_son_15_minutos_como_la_fuente(self):
        """Decisión de Kevin, y encaja con Massive: sus planes sirven la
        cotización con hasta 15 min de retraso, así que sondear más rápido
        devuelve el MISMO dato y gasta cuota."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "const VC_VIVO_MIN = 15;" in html
        i = html.index("const VC_VIVO_SEG = {")
        bloque = html[i:html.index("};", i)]
        assert set(re.findall(r"(\w+):\s*VC_VIVO_MIN", bloque)) == {
            "tape", "ticker", "ideas", "wheel"}
        # Con el mercado cerrado, más lento aún: el dato no cambia en 16 horas.
        assert "const VC_VIVO_CERRADO_SEG = 3600;" in html
        i2 = html.index("function vcArrancaVivo() {")
        assert "projIsMarketOpen()" in html[i2:i2 + 1200]

    def test_el_tooltip_explica_por_que_15_y_no_menos(self):
        """Sin el motivo, 15 minutos parece lentitud del panel y no un límite
        de la fuente."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function vcPintaVivo() {")
        cuerpo = html[i:html.index("function vcRefrescaActiva", i)]
        assert "15 min de retraso" in cuerpo and "Massive" in cuerpo


class TestWheel:
    """`/api/tito-wheel` — su `/api/wheel`, vender puts cash-secured."""

    def test_escanea_su_universo_curado(self, client, wheel_dobles):
        d = client.get("/api/tito-wheel").json()
        from wbj.tito.wheel_universe import WHEEL_UNIVERSE
        assert d["ok"] is True and d["scanned"] == len(WHEEL_UNIVERSE) == 40

    def test_sirve_sus_tres_presets_con_sus_bandas(self, client, wheel_dobles):
        d = client.get("/api/tito-wheel").json()
        bandas = {p["id"]: (p["delta_min"], p["delta_max"], p["dte_min"], p["dte_max"])
                  for p in d["presets"]}
        assert bandas["conservador"] == (0.10, 0.20, 30, 45)
        assert bandas["balanceado"] == (0.20, 0.30, 30, 45)
        assert bandas["agresivo"] == (0.30, 0.40, 7, 21)
        assert d["preset_id"] == "balanceado"      # su default

    def test_un_candidato_bloqueado_NO_lleva_prima(self, client, wheel_dobles):
        """Su regla crítica: ante la duda, no operar y avisar. No se enseña un
        número que no puedes cobrar."""
        d = client.get("/api/tito-wheel").json()
        for c in d["candidates"]:
            if c["blocked"]:
                assert c["premium"] is None and c["metrics"] is None and c["score"] is None
                assert c["block_reason"] in ("sin_bid", "spread_ancho", "oi_bajo")

    def test_la_asequibilidad_sale_del_perfil_del_servidor(self, client, wheel_dobles):
        """DIVERGENCIA DECLARADA: `wheelAfford.ts` corre en su CLIENTE porque
        allí el saldo vive en localStorage. Aquí el capital está en
        `Perfil Inversionista/perfil.json`, en el servidor, así que su misma
        función corre en la ruta.

        Lo que se vigila no es que el número viaje —tiene que viajar— sino que
        la fórmula siga siendo la suya: `affordable` es `colateral <= capital`,
        y `shortfall` lo que falta. Un signo al revés aquí diría "sí te cabe"
        sobre una operación que te deja la cuenta en negativo.
        """
        d = client.get("/api/tito-wheel").json()
        assert d["perfil"]["capital"] >= 0
        cap = d["perfil"]["capital"]
        vistos = 0
        for c in d["candidates"]:
            if c["blocked"]:
                continue
            a, col = c["afford"], c["metrics"]["collateral"]
            assert a is not None, "un candidato vivo sin veredicto de asequibilidad"
            assert a["affordable"] is (col <= cap), (
                f"{c['ticker']} {c['strike']}P: colateral {col} vs capital {cap} "
                f"→ affordable={a['affordable']}")
            assert a["shortfall"] == (0 if a["affordable"] else pytest.approx(col - cap))
            vistos += 1
        assert vistos, "el doble no dejó ningún candidato vivo que comprobar"

    def test_lo_que_no_te_cabe_se_baja_pero_NO_se_esconde(self, client, wheel_dobles):
        """Su `sortByAffordThenScore`: bloqueado → no asequible → score.

        Esconder lo que no te cabe te dejaría creyendo que el mercado no
        ofrecía nada. Se ordena, no se filtra.
        """
        d = client.get("/api/tito-wheel").json()
        vivos = [c for c in d["candidates"] if not c["blocked"]]
        orden = [(not c["afford"]["affordable"], -c["score"]["total"]) for c in vivos]
        assert orden == sorted(orden), "el orden no es asequible-primero-luego-score"

    @staticmethod
    def _cadena_sin_precio(monkeypatch):
        """La cadena llega sin `underlying_asset.price` — el escenario real."""
        import wbj.tito.massive as MASS
        from wbj.tito.massive import WheelChainResult
        original = MASS.fetch_wheel_chain
        monkeypatch.setattr(
            MASS, "fetch_wheel_chain",
            lambda t, a, b, now=None, **k: WheelChainResult(
                spot=None, quotes=original(t, a, b, now=now).quotes))

    def test_el_ultimo_CIERRE_rescata_el_escaneo(self, client, monkeypatch, wheel_dobles):
        """El fallo real que dejó a Kevin con «0 de 40 · sin precio del subyacente».

        Su `page.tsx` resuelve el spot con TRES eslabones:

            company?.price ?? chainMeta?.underlyingPrice ?? bars[last].close

        Wheel tenía los dos primeros y le faltaba el tercero — que es justo el
        que nunca falla, porque las barras ya están descargadas para los
        niveles y el IV Rank. Con la cadena sin precio y el snapshot del
        subyacente rechazado por el plan, los 40 símbolos caían de golpe.
        """
        self._cadena_sin_precio(monkeypatch)
        d = client.get("/api/tito-wheel").json()
        assert d["with_candidates"] > 0, f"el último cierre no rescató: {d['rejected']}"
        assert d["failed"] == 0

    def test_el_escaneo_NO_gasta_40_peticiones_en_el_snapshot(self, client, monkeypatch,
                                                              wheel_dobles):
        """`fetch_company` son 40 peticiones extra cada 15 minutos, y en esta
        cuenta ese endpoint no responde. Donde compensa —Ticker, UNA petición—
        se mantiene su precedencia entera; aquí no."""
        import wbj.tito.massive as MASS
        llamadas = []
        monkeypatch.setattr(MASS, "fetch_company",
                            lambda t, **k: llamadas.append(t) or {"price": 100.0})
        client.get("/api/tito-wheel")
        assert not llamadas, f"el escaneo pidió el snapshot {len(llamadas)} veces"

    def test_sin_NINGUNA_fuente_de_precio_lo_dice_por_su_nombre(self, client, monkeypatch,
                                                                wheel_dobles):
        import wbj.tito.bars_store as BS
        self._cadena_sin_precio(monkeypatch)
        monkeypatch.setattr(BS, "cached_daily_bars", lambda t, d=365, n=None, **k: [])
        d = client.get("/api/tito-wheel").json()
        assert {r["motivo"] for r in d["rejected"]} == {"sin_barras"}

    def test_los_TRES_desenlaces_se_reportan_por_separado(self, client, monkeypatch,
                                                          wheel_dobles):
        """«0 de 40 · 40 sin cadena» juntaba tres cosas muy distintas: un 403
        del plan, una cadena vacía de verdad, y una cadena llena cuyos strikes
        no caen en la banda de delta del preset. Con 40 de 40 cayendo, eso no
        dejaba forma de saber si el problema era la cuenta, el mercado o el
        filtro."""
        import wbj.tito.massive as MASS

        def rechaza(t, a, b, now=None, **k):
            raise MASS.MassiveError("el plan no lo cubre", 403)

        monkeypatch.setattr(MASS, "fetch_wheel_chain", rechaza)
        d = client.get("/api/tito-wheel").json()
        assert [r["motivo"] for r in d["rejected"]] == ["fuente"]
        assert d["rejected"][0]["tickers"] == 40
        # …y con un EJEMPLO real, que es lo que dice si es 401, 403 o el filtro.
        assert "el plan no lo cubre" in d["rejected"][0]["ejemplo"]

    def test_el_desglose_llega_al_panel(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjWheel(d) {")
        cuerpo = html[i:html.index("/* ── TIME & SALES", i)]
        assert "d.rejected" in cuerpo and "que_significa" in cuerpo and "ejemplo" in cuerpo
        # Y las dos pistas que convierten el desglose en algo accionable.
        assert "Prueba otro preset" in cuerpo
        assert "no es el mercado" in cuerpo
        # "sin cadena" ya no puede rotular un 403.
        assert "sin cadena" not in cuerpo

    def test_el_escaneo_NO_muta_estado_compartido_entre_hilos(self):
        """Corren 6 hilos. Un `contador += 1` desde varios pierde cuentas en
        silencio, y el contador es justo lo que se enseña en pantalla."""
        api = (ROOT / "vertex_api.py").read_text(encoding="utf-8")
        i = api.index("    def _uno(sym):")
        cuerpo = api[i:api.index("    # `mapLimit(WHEEL_UNIVERSE", i)]
        assert "nonlocal" not in cuerpo, "el worker vuelve a mutar estado compartido"
        assert "fallidos +=" not in cuerpo and "todos.extend" not in cuerpo
        # Devuelve su resultado; la suma se hace fuera, en un solo hilo.
        assert "return [], (" in cuerpo and "return cands, (" in cuerpo

    @staticmethod
    def _sin_horquilla(monkeypatch):
        import wbj.tito.massive as MASS
        from wbj.tito.massive import WheelChainQuote, WheelChainResult
        original = MASS.fetch_wheel_chain

        def sin_quote(t, a, b, now=None, **k):
            r = original(t, a, b, now=now)
            return WheelChainResult(spot=r.spot, quotes=[
                WheelChainQuote(strike=q.strike, expiration=q.expiration, dte=q.dte,
                                bid=None, ask=None, last_trade=q.last_trade,
                                open_interest=q.open_interest) for q in r.quotes])

        monkeypatch.setattr(MASS, "fetch_wheel_chain", sin_quote)

    def test_sin_horquilla_el_screener_SIGUE_dando_candidatos(self, client, monkeypatch,
                                                              wheel_dobles):
        """Su plan de Massive tampoco sirve quotes — lo dice su `compute.ts`:

            "La fórmula del agente pide BID, pero el plan actual de Massive NO
             devuelve quotes, así que cae a last_trade → day.close → day.vwap."

        Su `wheelCandidates` bloquea por `sin_bid` ANTES de llamar a
        `pickPremium`, así que las ramas `ultimo` y `modelo` de esa cascada
        —con sus recortes del 10% y 15%— son inalcanzables en su código. No se
        escriben dos recortes que nunca se aplican: existen para esto.
        """
        self._sin_horquilla(monkeypatch)
        d = client.get("/api/tito-wheel").json()
        assert d["with_candidates"] > 0, f"sigue vacío: {d['rejected']}"
        assert d["quotes_missing"] == d["scanned"]
        operables = [c for c in d["candidates"] if not c["blocked"]]
        assert operables, "ningún operable sin horquilla"
        assert all(c["premium"]["source"] != "bid" for c in operables)

    def test_sin_horquilla_el_SCORE_se_castiga_solo(self, client, monkeypatch, wheel_dobles):
        """La salvaguarda no se pierde al dejar pasar el contrato: el spread
        sigue sin poder medirse, y `_liquidity_part` ya trata ese `None` como
        `inf` → banda "insuficiente" → **0 de 15**. El propio score castiga no
        saber la liquidez, que es justo lo que el bloqueo protegía."""
        self._sin_horquilla(monkeypatch)
        d = client.get("/api/tito-wheel").json()
        for c in d["candidates"]:
            if c["blocked"]:
                continue
            assert c["spread_pct"] is None
            assert c["score"]["liquidity"]["points"] == 0
            assert c["score"]["liquidity"]["band"] == "insuficiente"

    def test_su_comportamiento_LITERAL_sigue_siendo_el_default(self):
        """La divergencia es opt-in: `allow_missing_quote=False` por defecto,
        así que lo que se prueba contra su repo es su código tal cual."""
        from wbj.tito.wheel import CandidatesInput
        assert CandidatesInput.__dataclass_fields__["allow_missing_quote"].default is False

    def test_la_cadena_de_precio_del_contrato_es_la_SUYA(self, monkeypatch):
        """`fetch_wheel_chain` leía solo `last_trade.price`. Su `contractPrice`
        —el que usa para la tabla de la cadena— tiene tres niveles, y el
        segundo es el que salva un contrato que hoy no negoció: fuera de sesión
        son todos."""
        import wbj.tito.massive as MASS

        def solo_close(url, key, ticker, timeout):
            return {"results": [{
                "details": {"strike_price": 90.0, "expiration_date": "2026-09-18"},
                "underlying_asset": {"price": 100.0},
                "day": {"close": 1.25},          # ← sin last_trade
                "open_interest": 800}]}

        monkeypatch.setattr(MASS, "_get", solo_close)
        r = MASS.fetch_wheel_chain("NVDA", 0, 400, now=NOW)
        assert r.quotes and r.quotes[0].last_trade == 1.25

    def test_el_panel_avisa_de_que_la_prima_es_ESTIMADA(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjWheel(d) {")
        cuerpo = html[i:html.index("/* ── TIME & SALES", i)]
        assert "d.quotes_missing" in cuerpo
        assert "no del bid" in cuerpo
        assert "0 de 15" in cuerpo               # el castigo, dicho
        assert "Verifica la prima en tu br" in cuerpo

    def test_el_429_se_reintenta_y_no_se_confunde_con_otro_error(self, monkeypatch):
        """40 tickers × 2 llamadas en 6 hilos es una ráfaga de ~80 peticiones, y
        los planes limitan por minuto. La firma es inconfundible: unos pocos
        pasan y el resto cae de golpe. Un 403, en cambio, no mejora esperando:
        reintentarlo solo retrasaría el diagnóstico."""
        import vertex_api as V
        from wbj.tito.massive import MassiveError

        monkeypatch.setattr(V, "_WHEEL_ESPERA_BASE", 0.001)
        intentos = {"n": 0}

        def falla_dos_veces():
            intentos["n"] += 1
            if intentos["n"] <= 2:
                raise MassiveError("límite de tasa", 429)
            return "ok"

        assert V._wheel_con_reintento(falla_dos_veces) == "ok"
        assert intentos["n"] == 3

        prohibido = {"n": 0}

        def prohibido_siempre():
            prohibido["n"] += 1
            raise MassiveError("el plan no lo cubre", 403)

        with pytest.raises(MassiveError):
            V._wheel_con_reintento(prohibido_siempre)
        assert prohibido["n"] == 1, "un 403 no se reintenta"

    def test_las_barras_reportan_su_motivo_en_vez_de_una_lista_vacia(self, monkeypatch):
        """`cached_daily_bars` hace `.catch(() => [])` —comportamiento suyo, y
        ahí está bien— pero eso convierte un 429, un 403 y un ticker sin datos
        en la misma lista vacía. En un escaneo de 40 símbolos esa es la
        diferencia entre "espera un momento" y "revisa tu plan".

        Se prueba `_wheel_barras` directo: el doble de `wheel_dobles` sustituye
        `cached_daily_bars` entero y taparía justo el camino que se mide.
        """
        import vertex_api as V
        import wbj.tito.bars_store as BS
        import wbj.tito.massive as MASS

        def revienta(t, d=365, **k):
            raise MASS.MassiveError("Massive no aceptó la credencial", 401)

        monkeypatch.setattr(MASS, "fetch_daily_bars", revienta)
        monkeypatch.setattr(BS, "load_bars", lambda t: None)      # sin cache
        bars, motivo = V._wheel_barras("NVDA", NOW)
        assert bars == []
        assert motivo is not None and motivo[0] == "fuente"
        assert "credencial" in motivo[1]

        # Y con un ticker que simplemente no tiene datos, el motivo es None:
        # la ruta lo traduce a "sin barras", que es otra cosa.
        monkeypatch.setattr(MASS, "fetch_daily_bars", lambda t, d=365, **k: [])
        bars, motivo = V._wheel_barras("NVDA", NOW)
        assert bars == [] and motivo is None

    def test_los_bloqueados_se_resumen_en_vez_de_llenar_la_tabla(self):
        """Una fila bloqueada no lleva NINGÚN número —esa es su regla—, así que
        cien seguidas son cien líneas vacías."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjWheel(d) {")
        cuerpo = html[i:html.index("/* ── TIME & SALES", i)]
        assert "_bloqMostrados <= 8" in cuerpo
        assert "d.blocked_summary" in cuerpo and "Contratos bloqueados" in cuerpo
        assert "a&ntilde;adido de plan" in cuerpo   # el porqué del sin_bid

    def test_el_aviso_de_cotizacion_retrasada_va_siempre(self):
        """Su `wheel-disclaimer` no está dentro de ningún `if`.

        Aquí el «confirma el precio en tu bróker» vivía SOLO dentro del bloque
        de `quotes_missing`, o sea justo en el caso en que el usuario ya sabía
        que la prima era estimada. Cuando Massive SÍ sirve horquilla la
        cotización sigue siendo retrasada, y ahí no se decía nada.
        """
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjWheel(d) {")
        cuerpo = html[i:html.index("/* ── TIME & SALES", i)]
        j = cuerpo.index("retrasadas")
        # El aviso está en la plantilla base, ANTES del primer `${d.quotes_missing`
        # y de cualquier otro condicional de degradación.
        assert j < cuerpo.index("${d.quotes_missing"), \
            "el aviso volvió a quedar colgando de que falte la horquilla"
        assert "confirma el precio en tu br&oacute;ker" in cuerpo

    def test_se_puede_volver_a_escanear_sin_cambiar_de_preset(self):
        """Su `↻ Volver a escanear`. Sin él, la única forma de repetir el
        escaneo es irse a otro preset y volver — que además tira el resultado
        bueno por el camino. Y un escaneo degradado se repite: las
        cotizaciones cambian."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjWheel(d) {")
        cuerpo = html[i:html.index("/* ── TIME & SALES", i)]
        assert "Volver a escanear" in cuerpo
        assert "loadProjWheel()" in cuerpo, \
            "el botón tiene que llamar SIN preset, para que conserve el elegido"
        # …y `loadProjWheel` sin argumento conserva el preset en curso.
        lp = html[html.index("async function loadProjWheel(preset)"):]
        assert "if (preset) vcWheelPreset = preset;" in lp[:400]

    def test_castiga_el_rendimiento_sospechosamente_alto(self):
        """Un screener que ordena por prima pone arriba justo las acciones a
        punto de desplomarse. Su banda >60% da 10/30, menos que la de 15-35%."""
        from wbj.tito.wheel import ScoreInput, score_candidate
        def _t(pct):
            return score_candidate(ScoreInput(
                annualized_pct=pct, iv_rank=60, strike=90, spot=100, cushion_pct=12,
                supports=[], open_interest=600, spread_pct=5, earnings="fuera")).annualized
        assert _t(80).points == 10 and _t(25).points == 30
        assert _t(80).points < _t(25).points

    def test_la_banda_de_IV_rank_va_INVERTIDA_respecto_al_subagente_5(self):
        """La Wheel VENDE prima: quiere la volatilidad cara. El resto del
        agente compra y quiere vega barata."""
        from wbj.tito.wheel import ScoreInput, score_candidate
        from wbj.tito.ivcontext import iv_rank_points
        def _w(rank):
            return score_candidate(ScoreInput(
                annualized_pct=20, iv_rank=rank, strike=90, spot=100, cushion_pct=12,
                supports=[], open_interest=600, spread_pct=5, earnings="fuera")).iv_rank.points
        assert _w(85) > _w(20), "la Wheel debe premiar el IV Rank ALTO"
        assert iv_rank_points(85).points < iv_rank_points(20).points, \
            "el sub-agente 5 debe premiar el IV Rank BAJO"


class TestTimeAndSales:
    """`/api/tito-tape` — su `/flow`, la cinta cruda de un ticker."""

    def test_devuelve_la_cinta_clasificada(self, client):
        d = client.get("/api/tito-tape?ticker=DEMO").json()
        assert d["ok"] is True and d["ticker"] == "DEMO"
        assert d["trades"] and d["notable"] >= 1
        assert 0 <= d["aggression"]["score"] <= 10

    def test_cada_operacion_lleva_su_evidencia(self, client):
        """A diferencia del scorecard, aquí NO se agrega nada: se ve el flujo
        tal como entró."""
        d = client.get("/api/tito-tape?ticker=DEMO").json()
        t = d["trades"][0]
        for campo in ("timestamp", "strike", "expiration", "dte", "size", "price",
                      "premium", "aggression", "delta", "iv", "unusual_score",
                      "repeated", "multileg", "above_ask", "below_bid", "exceeded_oi"):
            assert campo in t, f"falta {campo} en la fila del tape"

    def test_sin_cinta_lo_dice_y_no_inventa(self, client, monkeypatch):
        import wbj.tito.marketsnack as MS

        def boom(*a, **k):
            raise MS.MarketSnackError("La cookie de MarketSnack caducó.")

        monkeypatch.setattr(MS, "fetch_flow", boom)
        d = client.get("/api/tito-tape?ticker=DEMO").json()
        assert d["ok"] is False and d["source"] == "marketsnack"
        assert "trades" not in d

    def test_los_puntos_de_inusualidad_llevan_su_desglose(self, client):
        """Su `NotableTable` pone «Volumen X/10 · Horario Y/10 · Repetición
        Z/10» en el `title` de la columna de Puntos. Solo viajaba el total, y
        un 21/30 sin desglose no dice si vino del tamaño de la orden, de la
        hora a la que entró o de cuántas veces se repitió el contrato — que son
        tres señales distintas y se leen distinto."""
        d = client.get("/api/tito-tape?ticker=DEMO").json()
        p = d["trades"][0]["unusual_parts"]
        assert set(p) == {"volume", "timing", "repetition"}
        assert p["volume"] + p["timing"] + p["repetition"] == d["trades"][0]["unusual_score"], \
            "el desglose tiene que sumar el total, o uno de los dos miente"

    def test_la_tabla_de_inusualidad_llega_entera_como_la_suya(self, client):
        """Su `unusualRows`: los seis sub-puntajes pegados a CADA trade.

        Aquí solo viajaban los seis promedios. Un promedio no dice qué contrato
        disparó la señal, y es justo lo que se mira: «inusualidad 7,4» no se
        opera, «el call de $210 que vence en tres días puntúa 9,2 por tamaño y
        gamma» sí.
        """
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        filas = d.get("unusual_rows")
        assert filas, "el motor no sirve las filas de inusualidad"
        r = filas[0]
        for campo in ("id", "underlying", "strike", "expiration", "dte",
                      "timestamp", "premium", "delta", "gamma",
                      "theta_pct_daily", "condition_code", "condition_name",
                      "multileg", "confirmed_by_aggression", "unusual_scores"):
            assert campo in r, f"falta {campo}: su tabla no se puede montar"
        assert set(r["unusual_scores"]) == {"size", "delta", "theta", "gamma",
                                            "leg", "expiry", "total"}

    def test_el_cruce_con_agresividad_es_el_suyo(self, client):
        """`agresividadIds.has(row.id)` — y su nota: verifica, no puntúa."""
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        u = d["subagents"]["unusuality"]
        assert "confirmed_count" in u, "sin el recuento, la columna no se resume"
        marcadas = sum(1 for r in d["unusual_rows"]
                       if r["confirmed_by_aggression"])
        assert u["confirmed_count"] == marcadas, (
            f"el recuento ({u['confirmed_count']}) no cuadra con las filas "
            f"marcadas ({marcadas})")
        assert u["confirmed_count"] <= len(d["unusual_rows"])

    def test_las_filas_no_se_archivan_pero_la_evidencia_si(self, client):
        """150 trades con griegos por reporte llenarían el repositorio.

        Lo que sostiene el score —los promedios y los recuentos— sí se archiva;
        la materia prima de pantalla se vuelve a bajar cuando haga falta.
        """
        import vertex_api as V

        assert "unusual_rows" in V._OPCIONES_NO_SE_ARCHIVA
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        recortado = V._sin_derivado(d)
        assert "unusual_rows" not in recortado
        assert "unusual_rows" in recortado["_no_archivado"], (
            "se recorta en silencio: el archivo tiene que decir qué le falta")
        assert recortado["subagents"]["unusuality"]["avg_by_param"], (
            "la evidencia del score no puede irse con la materia prima")

    def test_el_panel_monta_SU_tabla_con_sus_columnas(self):
        import pathlib

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "vcUnusualTablaHTML" in html
        for col in ("Theta/día", "Condición", "Validación", "Inusual",
                    "Tam", "Venc"):
            assert col in html, f"falta la columna «{col}» de su UnusualityCard"
        assert "Solo inusuales" in html and "también en Agresividad" in html

    def test_el_recorte_al_cono_se_avisa_en_el_escenario(self, client):
        """Su «· recortado al cono 2σ», que el motor servía y nadie pintaba.

        `clamped` no es un detalle de dibujo: dice que el escenario pedía MÁS
        de lo que la volatilidad da en ese plazo, así que ese target es el
        techo del cono y no una previsión. Sin el aviso, un target recortado se
        lee igual que uno que salió solo — «puede llegar aquí» contra «aquí es
        donde le dejamos parar».
        """
        import pathlib

        d = client.get("/api/projection-targets?ticker=DEMO").json()
        geo = d.get("chart_geometry") or {}
        assert geo, "el motor dejó de servir la geometría"
        alguno = next(iter(geo.values()))
        for clave in ("bear", "base", "bull"):
            ruta = (alguno.get("paths") or {}).get(clave)
            if ruta is not None:
                assert "clamped" in ruta, f"{clave} sin el flag de recorte"

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "recortado al cono" in html, "el aviso no llega a la pantalla"
        assert "_recortado" in html and ".clamped" in html

    def test_cada_trade_lleva_los_SEIS_parametros_del_sub_agente_3(self, client):
        """Las dos escalas de inusualidad, y por qué hacen falta las dos.

        La de la columna (0-30) decide qué fila se tiñe. Ésta (0-10, seis
        parámetros) es la que puntúa al sub-agente 3 y la que se compara con el
        umbral institucional de 7 — confundirlas es lo que hacía que la leyenda
        dijera «≥7/30». El panel enseñaba el PROMEDIO de los seis y ninguno por
        trade, así que no había forma de ver por qué UN contrato puntúa alto.
        """
        d = client.get("/api/tito-tape?ticker=DEMO").json()
        s = d["trades"][0]["unusual_scores"]
        assert set(s) == {"size", "delta", "theta", "gamma", "leg", "expiry",
                          "total"}, s
        seis = [s[k] for k in ("size", "delta", "theta", "gamma", "leg", "expiry")]
        assert all(0 <= v <= 10 for v in seis), s
        assert abs(s["total"] - sum(seis) / 6) < 0.06, (
            f"el total no es el promedio de los seis: {s}")
        assert s["total"] != d["trades"][0]["unusual_score"] or s["total"] == 0, (
            "las dos escalas no son la misma; si coinciden siempre, una está mal")

    def test_el_modo_grafica_usa_SUS_presets_por_ventana(self, client, monkeypatch):
        """`?days=N` — su segundo modo, el que alimenta la gráfica del dinero.

        El piso de premium depende de la VENTANA y no al revés: cinco días con
        el piso de $1M salen casi vacíos, y por eso su preset lo baja a $250K.
        El panel reagrupaba siempre las mismas filas de convicción ($1M, 30
        días) al cambiar de marco, así que en las vistas cortas enseñaba menos
        barras que su app — y los racimos, que salen de esas filas, eran otros.
        """
        import wbj.tito.marketsnack as MS

        vistos = []
        original = MS.fetch_flow

        def espia(ticker, **opts):
            vistos.append(opts)
            return original(ticker, **opts)

        monkeypatch.setattr(MS, "fetch_flow", espia)
        for dias, piso, paginas in ((5, 250_000, 30), (10, 500_000, 20),
                                    (30, 1_000_000, 15)):
            vistos.clear()
            d = client.get(f"/api/tito-tape?ticker=DEMO&days={dias}").json()
            assert d["ok"] is True and d["days"] == dias, d
            assert d["min_premium"] == piso, (
                f"con {dias} días el piso debe ser {piso}, no {d['min_premium']}")
            assert "rows" in d and "trades" not in d, (
                "el modo gráfica devuelve `rows`, como el suyo")
            assert vistos and vistos[-1]["min_premium"] == piso, vistos
            assert vistos[-1]["max_pages"] == paginas, vistos
            assert vistos[-1]["target_days"] == dias, (
                "sin `target_days` se baja el mes entero en vez de la ventana")
            assert vistos[-1]["period"] == "1m", vistos

    def test_una_ventana_rara_cae_a_su_preset_por_defecto(self, client):
        """`CHART_PRESETS[days] ?? { minPremium: 500_000, maxPages: 20 }`."""
        d = client.get("/api/tito-tape?ticker=DEMO&days=7").json()
        assert d["ok"] is True and d["min_premium"] == 500_000, d

    def test_sin_days_la_cinta_sigue_siendo_la_cinta(self, client):
        """El modo nuevo no puede cambiar el viejo: es la misma ruta."""
        d = client.get("/api/tito-tape?ticker=DEMO").json()
        assert "trades" in d and "rows" not in d
        assert d["min_premium"] == 100_000, "el piso del tape es el suyo"

    def test_el_panel_pide_la_ventana_al_cambiar_de_marco(self):
        """Sin esto el arreglo del servidor no llega a la pantalla."""
        import pathlib

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "VC_FLOW_DIAS" in html and "'5m5d': 5" in html
        assert "_projFlowVentana" in html
        assert "/api/tito-tape?ticker=" in html and "&days=" in html

    def test_la_cinta_lleva_la_horquilla_del_momento(self, client):
        """Las flechas ↑/↓ dicen que el print salió FUERA de la horquilla. Sin
        la horquilla no se sabe por cuánto — un centavo sobre el ask y un dólar
        sobre el ask son la misma flecha."""
        d = client.get("/api/tito-tape?ticker=DEMO").json()
        t = d["trades"][0]
        assert t["bid"] is not None and t["ask"] is not None
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjTape(d) {")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        assert "t.bid" in cuerpo and "t.ask" in cuerpo

    def test_el_veredicto_de_agresividad_se_pinta_en_la_cinta(self):
        """Su `AggressionScoreCard` va con la cinta, no solo en el scorecard.
        La barra sola no separa un 55/45 de un 90/10: eso lo hace la frase."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjTape(d) {")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        for frase in ("Sin flujo agresivo", "Compra agresiva (al ask)",
                      "Presión al bid", "Mixto"):
            assert frase in cuerpo, f"falta el caso «{frase}»"
        assert "a.ratio >= 0.66" in cuerpo and "a.ratio <= 0.34" in cuerpo

    def test_el_recorte_de_la_TABLA_se_dice_no_el_de_la_bajada(self):
        """Su `meta.shown < meta.notableCount ? ", top N"`.

        Aquí colgaba de `d.truncated`, que es otra cosa: `truncated` dice que
        se cortó la BAJADA por el tope de páginas, y la tabla se recorta aparte
        en `TABLE_CAP`. Con 140 notables en una sola página la bajada no truncó
        nada y aun así solo se ven 100 — que es justo el caso en que callarlo
        hace creer que se están viendo todos.
        """
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjTape(d) {")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        assert "d.truncated" not in cuerpo, "volvió a colgar del recorte de la bajada"
        assert "_vistos < (d.notable" in cuerpo and "top ${_vistos}" in cuerpo

    def test_el_tope_de_la_tabla_es_el_suyo(self):
        """Y el tope que produce ese recorte es `TABLE_CAP`, importado de su
        módulo por nombre — no un número escrito a mano en la ruta."""
        from wbj.tito.scorecard import TABLE_CAP
        api = (ROOT / "vertex_api.py").read_text(encoding="utf-8")
        assert "TITO_TABLE_CAP]" in api
        assert TABLE_CAP == 100

    def test_con_agresividad_desconocida_no_dice_Mid(self):
        """Su `sideLabel` tiene CUATRO ramas: ask, bid, mid… y `unknown`, que
        cae a `r.side`. Aquí el `else` se comía el desconocido y lo rotulaba
        «Mid», que es afirmar una lectura de la horquilla que el motor declaró
        que no pudo hacer."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderProjTape(d) {")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        assert "t.aggression === 'mid' ?" in cuerpo, "el mid tiene que ser explícito"
        assert "t.side" in cuerpo, "el desconocido tiene que caer al lado crudo"


class TestCabeEnCualquierPantalla:
    """El tab tenía 16 tablas con ancho mínimo de hasta 720px. En un teléfono
    de 390px eso es scroll horizontal en todas, y el usuario no sabe que hay
    más a la derecha."""

    @staticmethod
    def _tab():
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        dom = html[html.index('<main id="projectionsView"'):]
        return html, dom[:dom.index("</main>")]

    def test_ninguna_tabla_con_ancho_fijo_se_queda_sin_modo_tarjeta(self):
        """Bajo 640px el CSS convierte `table.vc-t` en tarjetas. Una tabla con
        `min-w` y sin esa clase es scroll horizontal garantizado."""
        html, _ = self._tab()
        desde = html.index("const VC_TABS = [")
        sueltas = [m.group(1)[:70] for m in re.finditer(r'<table class="([^"]*)"', html[desde:])
                   if "min-w" in m.group(1) and "vc-t" not in m.group(1)]
        assert not sueltas, f"tablas con ancho fijo y sin modo tarjeta: {sueltas}"

    def test_el_css_de_tarjeta_existe_y_apaga_el_ancho_minimo(self):
        html, _ = self._tab()
        assert "@media (max-width: 639px)" in html
        i = html.index("@media (max-width: 639px)")
        bloque = html[i:i + 1400]
        assert "table.vc-t { min-width: 0 !important" in bloque
        assert "table.vc-t thead { display: none; }" in bloque
        assert "content: attr(data-th)" in bloque

    def test_las_etiquetas_se_leen_del_THEAD_no_se_escriben_a_mano(self):
        """Son ~150 celdas en 8 tablas generadas por plantilla: escritas a mano
        se desincronizan con el primer cambio de columna."""
        html, _ = self._tab()
        assert "function vcTablaResponsive(raiz) {" in html
        i = html.index("function vcTablaResponsive(raiz) {")
        cuerpo = _sin_comentarios(html[i:html.index("\n}", i)])
        assert "thead th" in cuerpo and "setAttribute('data-th'" in cuerpo
        # Y se llama tras CADA render que pinta tablas.
        for caja in ("projIdeas", "projWheel", "projTape", "projTargets", "projUnusual"):
            assert f"vcTablaResponsive('{caja}')" in html, f"falta en {caja}"
        # Un `colspan` (fila de aviso) no lleva etiqueta.
        assert "hasAttribute('colspan')" in cuerpo

    def test_no_quedan_alturas_fijas_en_pixeles(self):
        """440px de gráfica en un teléfono apaisado ocupan la pantalla entera."""
        _, dom = self._tab()
        assert not re.findall(r"height:\s*\d+px", dom)
        assert "h-[280px] sm:h-[360px] lg:h-[440px]" in dom

    def test_el_contenedor_crece_en_monitores_grandes(self):
        """`max-w-6xl` son 1152px: en un 27 pulgadas el tab usaba un tercio de
        la pantalla y las tablas seguían con scroll."""
        html, _ = self._tab()
        clase = re.search(r'<main id="projectionsView"[^>]*class="([^"]*)"', html).group(1)
        assert "xl:max-w-[1400px]" in clase and "2xl:max-w-[1760px]" in clase
        # …y el relleno se encoge en móvil.
        assert "p-3 sm:p-4" in clase

    def test_la_navegacion_no_se_parte_en_dos_filas_en_movil(self):
        html, dom = self._tab()
        nav = re.search(r'<div id="projNav" class="([^"]*)"', dom).group(1)
        assert "overflow-x-auto" in nav and "sm:flex-wrap" in nav
        assert "flex-shrink-0 whitespace-nowrap" in html   # los botones no se encogen


class TestElMotivoDeMassiveEsAccionable:
    """401 y 403 son problemas distintos y se arreglan distinto."""

    def test_el_401_habla_de_la_credencial_y_el_403_del_plan(self):
        from wbj.tito.massive import _describe
        uno = _describe(401, "NVDA", "", "/v3/snapshot/options/NVDA")
        tres = _describe(403, "NVDA", "", "/v2/snapshot/locale/us/markets/stocks/tickers/NVDA")
        assert "credencial" in uno and "MASSIVE_API_KEY" in uno
        assert "plan no lo cubre" in tres and "Cambiar la key no lo arregla" in tres
        assert uno != tres, "401 y 403 no pueden dar el mismo mensaje"

    def test_el_motivo_dice_QUE_ruta_falló(self):
        from wbj.tito.massive import _describe
        for code in (401, 403, 404):
            assert "/v2/aggs" in _describe(code, "NVDA", "", "/v2/aggs/ticker/NVDA")

    def test_el_motivo_nunca_lleva_la_credencial(self):
        """El centinela, también sobre la rama nueva: la key va en la cabecera,
        no en la URL, y la ruta se recorta antes de la query."""
        from wbj.tito.massive import _describe, _ruta
        centinela = "sk_" + "z" * 40
        url = f"https://api.massive.com/v2/aggs/ticker/NVDA?apiKey={centinela}"
        assert centinela not in _ruta(url)
        for code in (401, 403, 404, 429, 500):
            assert centinela not in _describe(code, "NVDA", "cuerpo", _ruta(url))


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


def _cuerpo_js(html: str, nombre: str) -> str:
    """El cuerpo EXACTO de `function nombre(…) { … }`, contando llaves.

    Una ventana de N caracteres se desborda a la función siguiente y produce
    falsos positivos: `projRefresh` parecía llamar a Quant Data solo porque el
    corte alcanzaba a `loadAnalysisGex`, que es de otra vista.
    """
    for pre in ("async function ", "function "):
        i = html.find(pre + nombre + "(")
        if i == -1:
            continue
        par = html.index("(", i)
        prof = 0
        for k in range(par, len(html)):
            if html[k] == "(":
                prof += 1
            elif html[k] == ")":
                prof -= 1
                if prof == 0:
                    par = k
                    break
        b = html.index("{", par)
        prof = 0
        for k in range(b, len(html)):
            if html[k] == "{":
                prof += 1
            elif html[k] == "}":
                prof -= 1
                if prof == 0:
                    return html[i:k + 1]
    return ""


def _sin_comentarios(js: str) -> str:
    """Quita comentarios de un fragmento de JS.

    Sin esto, un test que busca `Max Pain` o `api/options-gex` en el CÓDIGO pasa
    o falla por lo que digan los comentarios que explican precisamente que eso
    ya no está. El comentario documenta; el código es lo que se ejecuta.
    """
    import re
    js = re.sub(r"/\*[\s\S]*?\*/", "", js)          # /* … */ y /** … */
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)   # // …


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

    #: Hojas de `subagents` que el panel no lee, con su motivo. El registro es
    #: la única salida: o se pinta, o se declara aquí.
    _SUB_NO_SE_PINTAN = {
        "iv_context.iv.special": "marca de IV atípica; el aviso al usuario es "
                                 "`iv.band`, que sí se pinta",
        "validation.outcomes.id": "el id del flow, para la clave de la fila",
        "iv_context.iv.contracts": "sobre cuántos contratos se promedió la IV; "
                                   "la muestra ya la da `by_expiration`",
    }

    @staticmethod
    def _hojas(d, prefijo=""):
        """Las rutas hoja de un dict anidado, tipo `iv_context.rank.source`."""
        for k, v in d.items():
            ruta = f"{prefijo}{k}"
            if isinstance(v, dict):
                yield from TestElPanelNoTiraNadaDelPayload._hojas(v, ruta + ".")
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                yield from TestElPanelNoTiraNadaDelPayload._hojas(v[0], ruta + ".")
            else:
                yield ruta

    #: Hojas del payload ENTERO que el panel no lee, con su motivo. Mismo
    #: contrato que `_SUB_NO_SE_PINTAN`, pero para todo lo demás.
    _HOJAS_NO_SE_PINTAN = {
        "gex_heatmap.max_abs_cell": "el normalizador de la escala de color; lo "
                                    "consume el propio degradado, no es un dato de mercado",
    }

    @staticmethod
    def _payload_completo():
        """El payload REAL de la ruta, con lo que añade `projection_targets`."""
        import vertex_api as V
        sys.path.insert(0, str(ROOT / "engine" / "tests"))
        from tests.tito.test_scorecard import bars, chain, trades, NOW, SPOT
        from wbj.tito.scorecard import run_scorecard

        r = run_scorecard("DEMO", trades(), chain(), bars(), now=NOW, spot=SPOT)
        d = V._tito_json(r)
        d["memory"] = {"iv_days": 0, "flows": 0, "predictions": 0}
        d["gex_heatmap"] = V._tito_heatmap(chain(), r, trades(), NOW)
        d["chart_geometry"] = V._tito_chart_geometry(r)
        d["flow_clusters"] = V._tito_clusters(trades(), NOW)
        return d

    def test_cada_hoja_del_payload_ENTERO_se_pinta(self):
        """La versión de arriba mira claves raíz; ésta mira las 120 hojas.

        Con solo el test de raíces, `subagents` pasaba siendo UNA clave con 60
        hojas dentro — y por el mismo hueco se colaron siete columnas de la
        tabla de top strikes de su `StructureCard`, la unidireccionalidad de
        los racimos, el premium del tape por nodo de gamma y la muestra de la
        calibración. Todo servido, nada pintado.
        """
        d = self._payload_completo()
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        declaradas = dict(self._NO_SE_PINTAN)
        declaradas.update(self._HOJAS_NO_SE_PINTAN)
        huerfanas = []
        for ruta in sorted(set(self._hojas(d))):
            raiz = ruta.split(".")[0]
            if raiz in self._NO_SE_PINTAN or raiz == "subagents":
                continue          # infraestructura, o cubierto por el test de detalle
            if ruta in self._HOJAS_NO_SE_PINTAN:
                continue
            hoja = ruta.rsplit(".", 1)[-1]
            if not re.search(rf"\.{hoja}\b|'{hoja}'|\"{hoja}\"", html):
                huerfanas.append(ruta)
        assert not huerfanas, (
            f"el motor sirve estas hojas y el panel no las pinta: {huerfanas}. "
            f"O se cablean, o se declaran en `_HOJAS_NO_SE_PINTAN` con su motivo.")

    def test_el_registro_de_hojas_del_payload_no_miente(self):
        reales = set(self._hojas(self._payload_completo()))
        sobran = sorted(set(self._HOJAS_NO_SE_PINTAN) - reales)
        assert not sobran, f"declaradas como no pintadas pero ya no se sirven: {sobran}"

    def test_ninguna_declaracion_del_scorecard_miente_al_reves(self):
        """La otra cara: una entrada que dice «esto NO se pinta» de algo que el
        panel **sí** pinta.

        El barrido salta una hoja declarada **antes** de mirar el HTML, así que
        la declaración sobrevive a su propio motivo y se queda como
        documentación que afirma lo contrario del código. En Ideas y la Wheel
        pasó con catorce a la vez. Este es el mismo control para los tres
        registros del scorecard.

        Nota: las hojas de `_NO_SE_PINTAN` con nombre de infraestructura
        (`ok`, `ticker`, `history`…) SÍ aparecen en el HTML por otras vías —el
        `if (!d.ok)`, el título del panel— y por eso se comprueban solo las dos
        listas de hojas, que son las que hablan de datos de mercado.
        """
        import re

        #: Colisiones de NOMBRE, no declaraciones podridas: dos hojas distintas
        #: que se llaman igual son indistinguibles para el barrido.
        #: `iv_context.iv.contracts` es sobre cuántos contratos se promedió la
        #: IV, y no se pinta; `by_expiration[].contracts` es la muestra por
        #: vencimiento, y sí. Los dos nombres son los de su `IvContextScore`.
        COLISIONES = {
            "iv_context.iv.contracts",
            # `id` es la hoja más colisionada que hay: el panel accede a `.id`
            # de ideas, de trades, de racimos… La de `outcomes` no se pinta —
            # es la clave de la fila, como en su `key={o.id}`.
            "validation.outcomes.id",
        }

        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        mentiras = []
        for ruta in sorted(set(self._HOJAS_NO_SE_PINTAN) | set(self._SUB_NO_SE_PINTAN)):
            if ruta in COLISIONES:
                continue
            hoja = ruta.rsplit(".", 1)[-1]
            # Se busca el acceso por la hoja, igual que el barrido — con la
            # misma limitación: dos hojas homónimas son indistinguibles. Aquí
            # no hay ninguna, y si apareciera este test lo diría.
            if re.search(rf"\.{re.escape(hoja)}\b|\['{re.escape(hoja)}'\]", html):
                mentiras.append(ruta)
        assert not mentiras, (
            f"{mentiras} están declaradas como «no se pintan» y el panel SÍ las "
            "pinta. Bórralas del registro en vez de dejarlas mintiendo.")

    def test_cada_hoja_del_detalle_de_subagentes_se_pinta(self):
        """El fallo que ESTE test existe para impedir, ya cometido una vez.

        El motor calcula el desglose completo de los 6 sub-agentes —el spread
        medio, la dominancia, el promedio por parámetro, el IV Rank con su
        fuente, el MFE/MAE del backtest— y el payload servía solo el titular
        0-10. Seis cifras sin evidencia detrás, que es exactamente lo que la
        regla innegociable del proyecto prohíbe.

        El test de arriba no lo habría visto: mira las claves RAÍZ, y
        `subagents` es una sola clave con 60 hojas dentro.
        """
        import vertex_api as V
        sys.path.insert(0, str(ROOT / "engine" / "tests"))
        from tests.tito.test_scorecard import bars, chain, trades, NOW, SPOT
        from wbj.tito.scorecard import run_scorecard

        r = run_scorecard("DEMO", trades(), chain(), bars(), now=NOW, spot=SPOT)
        sub = V._tito_json(r)["subagents"]
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        # La ventana arranca en el primer helper, no en `vcSubagentesHTML`:
        # las tablas de etiquetas (`VC_EXEC_ORDER`, `VC_UNU_PARAMS`) viven fuera
        # de la función y son las que pintan esas hojas. Recortar antes daba
        # 13 huérfanas falsas.
        render = _sin_comentarios(
            html[html.index("function _vcCls(s) {"):
                 html.index("function vcMemoryHTML(d) {")])

        huerfanas = []
        for ruta in sorted(set(self._hojas(sub))):
            if ruta in self._SUB_NO_SE_PINTAN:
                continue
            hoja = ruta.rsplit(".", 1)[-1]
            # Se busca el acceso real (`.avg_pct`, `['avg_pct']`) en el
            # renderizador, o la clave suelta si va por tabla de constantes.
            if not re.search(rf"\.{hoja}\b|'{hoja}'|\"{hoja}\"", render):
                huerfanas.append(ruta)
        assert not huerfanas, (
            f"el motor sirve estas hojas del detalle y el panel no las pinta: "
            f"{huerfanas}. O se cablean, o se declaran en `_SUB_NO_SE_PINTAN`.")

    def test_el_registro_de_hojas_no_pintadas_no_miente(self):
        import vertex_api as V
        sys.path.insert(0, str(ROOT / "engine" / "tests"))
        from tests.tito.test_scorecard import bars, chain, trades, NOW, SPOT
        from wbj.tito.scorecard import run_scorecard

        r = run_scorecard("DEMO", trades(), chain(), bars(), now=NOW, spot=SPOT)
        reales = set(self._hojas(V._tito_json(r)["subagents"]))
        sobran = sorted(set(self._SUB_NO_SE_PINTAN) - reales)
        assert not sobran, f"declaradas como no pintadas pero ya no se sirven: {sobran}"

    def test_los_seis_subagentes_tienen_tarjeta_de_detalle(self):
        """Las seis, con el veredicto literal de sus componentes."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        render = _sin_comentarios(
            html[html.index("function _vcCls(s) {"):
                 html.index("function vcMemoryHTML(d) {")])
        for clave in ("aggression", "conviction", "unusuality", "structure",
                      "iv_context", "validation"):
            assert f"S.{clave}" in render, f"falta la tarjeta de {clave}"
        # Veredictos suyos, con sus cortes exactos.
        for v in ("Compra agresiva (al ask)", "Presión al bid", "Mixto",
                  "Convicción muy alta", "Convicción alta", "Convicción media",
                  "Convicción baja", "Flujo muy anormal", "Flujo anormal",
                  "Algo fuera de lo común", "Flujo normal",
                  "Posicionamiento muy claro", "Posicionamiento claro",
                  "Posicionamiento moderado", "Posicionamiento difuso"):
            assert v in render, f"falta el veredicto {v!r} de su tarjeta"
        # Y sus tres tablas de etiquetas, literales de su repo.
        for etiqueta in ("Sobre el ask", "Bajo el bid", "En el ask", "En el bid",
                         "Cerca del borde", "En el medio", "Sin claridad"):
            assert etiqueta in html, f"falta {etiqueta!r} de su EXECUTION_LABEL"
        for etiqueta in ("Acción dormida", "Volatilidad comprimida",
                         "Volatilidad estirada", "Prima inflada",
                         "Volatilidad normal", "Sin contexto"):
            assert etiqueta in html, f"falta {etiqueta!r} de su REGIME_LABEL"

    def test_las_tablas_de_filas_escapan_lo_que_viene_del_tape(self):
        """`strike` y `size` llegan CRUDOS de MarketSnack, no los calcula el motor.

        El port es literal: no valida tipos, así que un `strike` que el feed
        mande como texto viaja intacto hasta el `innerHTML` del panel. Los
        puntajes (0-10) y los contadores sí los produce el motor y son enteros
        por construcción; estos dos no.
        """
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        for bloque, campos in (
            ("function vcSubagentesHTML(d) {", ("r.strike", "r.size")),
            ("const ua = d.unusual || [];", ("u.strike", "u.size")),
        ):
            i = html.index(bloque)
            trozo = html[i:i + 4000]
            for campo in campos:
                crudo = re.findall(rf"\$\{{[^}}]*\b{re.escape(campo)}\b[^}}]*\}}", trozo)
                sin_escapar = [c for c in crudo if "_vcEsc" not in c and "Math." not in c]
                assert not sin_escapar, (
                    f"{campo} llega del tape y se interpola sin escapar: {sin_escapar}")

    def test_el_detalle_va_colapsado_como_el_suyo(self):
        """Es material de auditoría, no de primer vistazo: `<details>`, no
        seis tarjetas más empujando los targets fuera de pantalla."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        render = html[html.index("function vcSubagentesHTML(d) {"):
                      html.index("function vcMemoryHTML(d) {")]
        assert "<details" in render and "Detalle de sub-agentes" in render

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

    def test_el_tab_no_toca_quant_data(self):
        """UNA sola fuente para el tab: el motor de Víctor.

        El tab medía gamma DOS veces —`/api/options-gex` (Quant Data, respaldo
        yfinance+BSM) alimentaba las cards, los muros y el gráfico de gamma por
        strike— y las dos lecturas convivían en la misma pantalla sin forma de
        saber cuál mandaba. Ahora el cargador del tab llama a un solo endpoint.

        `/api/options-gex` sigue existiendo: lo usa la vista de Research, que es
        otra pantalla. Lo que este test prohíbe es que vuelva a ESTE tab.
        """
        import re
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        ini = html.index("async function loadProjections(")
        fin = html.index("\nasync function vcFetchTargets(", ini)
        cargador = _sin_comentarios(html[ini:fin])
        apis = set(re.findall(r"api/([a-z0-9-]+)", cargador))
        # `vcFetchTargets` es la única puerta de datos del tab.
        assert "vcFetchTargets(ticker)" in cargador
        prohibidos = {"options-gex", "net-flow", "options-ledger", "confluence",
                      "gex-strike"}
        assert not (apis & prohibidos), (
            f"el tab volvió a llamar a Quant Data: {sorted(apis & prohibidos)}")

    #: Rutas del servidor que usan Quant Data. Ninguna puede alimentar un panel
    #: del tab: da igual que el fetch no esté en el cargador si el endpoint
    #: consulta Quant Data por dentro. Este fue el hueco de la primera versión
    #: del test — miraba solo `loadProjections` y dejó pasar cuatro paneles
    #: (self-test, plan de operación, venta de prima y backtest de señales) que
    #: llamaban a rutas con `get_gex_cached` y `quantdata_*` dentro.
    _RUTAS_QD = ("options-gex", "net-flow", "options-ledger", "confluence",
                 "gex-strike", "self-test", "trade-plan", "backtest",
                 "collect-signals", "signal-history", "income-strategies",
                 "backfill")

    def test_ningun_handler_del_tab_llega_a_quant_data(self):
        """Ni por el cargador ni por un botón: el tab entero, no solo la carga."""
        import re
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        dom = html[html.index('<main id="projectionsView"'):]
        dom = dom[:dom.index("</main>")]
        handlers = {m.group(1) for m in
                    re.finditer(r'on(?:click|change|keydown)="([A-Za-z_$][\w$]*)\(', dom)}
        sucios = []
        for h in sorted(handlers):
            cuerpo = _sin_comentarios(_cuerpo_js(html, h))
            for r in self._RUTAS_QD:
                if f"api/{r}" in cuerpo:
                    sucios.append(f"{h} → /api/{r}")
        assert not sucios, (
            "handlers del tab que llegan a Quant Data: " + ", ".join(sucios))

    def test_los_paneles_de_quant_data_ya_no_estan_en_el_tab(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        dom = html[html.index('<main id="projectionsView"'):]
        dom = dom[:dom.index("</main>")]
        for nodo in ("projNetDriftCard", "projLedgerCard", "projDarkPool",
                     "projConfluence"):
            assert f'id="{nodo}"' not in dom, f"{nodo} sigue en el tab"
        # …y sus cargadores tampoco quedan colgando.
        for fn in ("loadNetDrift", "loadFlowLedger", "loadProjConfluence",
                   "loadGammaStrike", "renderProjDarkPool", "projActiveWalls"):
            assert f"function {fn}(" not in html, f"{fn} quedó definida"
            assert f"{fn}(" not in html, f"{fn} quedó referenciada"

    def test_no_queda_ni_el_rotulo_de_quant_data_en_el_tab(self):
        """El cascarón también miente.

        Al quitar `projDarkPool` sobrevivió la TARJETA que lo envolvía: marco
        morado, icono de radar y el título *Dark Pool & Flujo Institucional
        (Quant Data)* sobre un cuerpo vacío. Para quien mira la pantalla eso no
        es "un panel menos", es "el panel de Quant Data no está cargando".
        """
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        dom = html[html.index('<main id="projectionsView"'):]
        dom = dom[:dom.index("</main>")]
        # Se miran solo los textos VISIBLES: un comentario que explique por qué
        # se quitó Quant Data es documentación, no una etiqueta en pantalla.
        visible = re.sub(r"<!--.*?-->", "", dom, flags=re.S)
        for rotulo in ("Quant Data", "Dark Pool", "Max Pain"):
            assert rotulo not in visible, f"quedó el rótulo «{rotulo}» en el tab"

    def test_ninguna_tarjeta_del_tab_se_queda_sin_cuerpo(self):
        """Una tarjeta con título y sin contenedor de datos es un panel roto."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        dom = html[html.index('<main id="projectionsView"'):]
        dom = dom[:dom.index("</main>")]
        visible = re.sub(r"<!--.*?-->", "", dom, flags=re.S)
        vacias = [m.group(1).strip() for m in re.finditer(
            r'<div class="[^"]*rounded-2xl[^"]*">\s*<h3[^>]*>(.*?)</h3>\s*</div>',
            visible, flags=re.S)]
        assert not vacias, f"tarjetas con título y sin cuerpo: {vacias}"

    def test_el_plan_de_operacion_no_trae_gamma_ni_flujo_de_quant_data(self):
        """El único panel del tab que NO es de Víctor tampoco puede duplicarlo.

        `projOpPlan` se llena desde `/api/analyze` —el motor de Full Research,
        donde Quant Data sí manda— pero se PINTA dentro de Proyecciones. Traía
        put wall, call wall y gamma flip de `get_gex_cached` (Quant Data) y un
        checkpoint de flujo de `_qd_conv`: los mismos cuatro niveles que el
        bloque *Escenarios de Precio (GEX)* ya muestra dos tarjetas más arriba,
        calculados por el motor de Víctor sobre la cadena de Massive. Cuando
        discrepaban no había forma de saber cuál mirar.
        """
        api = (ROOT / "vertex_api.py").read_text(encoding="utf-8")
        ini = api.index('analisis_json["trade_plan"] = {')
        fin = api.index('analisis_json["trade_plan"]["thesis_invalidation"]', ini)
        bloque = api[ini:fin]
        # Se lee el CÓDIGO, no el comentario que explica la eliminación.
        codigo = "\n".join(l for l in bloque.splitlines()
                           if not l.lstrip().startswith("#"))
        for fuente in ("_gex_now", "_qd_conv", "_qd_np", "put_wall", "call_wall",
                       "gamma_flip", "flow_override"):
            assert fuente not in codigo, (
                f"el plan del tab volvió a leer «{fuente}», que es de Quant Data")

    def test_el_rotulo_de_actividad_inusual_describe_lo_que_pinta(self):
        """Decía «volumen > OI» —la definición de Quant Data— sobre los trades
        del sub-agente 3, que puntúa inusualidad /30 sobre la cinta."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        dom = html[html.index('<main id="projectionsView"'):]
        dom = dom[:dom.index("</main>")]
        titulo = re.search(r"Actividad Inusual de Opciones.*?</h3>", dom, flags=re.S)
        assert titulo, "desapareció el panel de actividad inusual"
        assert "volumen &gt; OI" not in titulo.group(0)
        assert "/30" in titulo.group(0), "el rótulo debe decir de dónde sale el número"

    def test_las_cards_del_tab_salen_del_motor_de_victor(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        render = html[html.index("function renderProjections(d) {"):]
        render = _sin_comentarios(render[:render.index("\n/** Gamma neto por strike")])
        # Muros = el nodo de mayor magnitud de cada lado, como su ProWallsCard.
        assert "g.nodes" in render and "n.side === lado" in render
        assert "g.flip_strike" in render and "g.king_strike" in render
        assert "g.total_net_gex" in render
        # Max Pain fuera: Víctor no lo calcula.
        assert "Max Pain" not in render, "Víctor no calcula max pain; no puede estar"
        assert "Nodo imán" in render, "el nodo imán es su equivalente y debe estar"
        # Nada del payload de Quant Data.
        for viejo in ("d.call_wall", "d.put_wall", "d.gamma_flip", "d.max_pain",
                      "d.net_gex", "d.unusual_activity"):
            assert viejo not in render, f"{viejo} es del payload de Quant Data"

    def test_la_cabecera_de_SU_grafica_sale_de_SU_motor(self):
        """El tab mide gamma DOS veces y con fuentes distintas.

        `/api/options-gex` (Quant Data, respaldo yfinance+BSM) alimenta las
        cards de arriba; el motor de Víctor (Massive + MarketSnack) alimenta la
        gráfica, los escenarios y el imán. El título y la insignia van ENCIMA de
        su gráfica, así que salían de la fuente equivocada: el encabezado podía
        decir "GEX+ anclado" mientras los escenarios de abajo venían de una
        lectura γ−, y nadie podía notarlo.
        """
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cargador = html[html.index("async function loadProjections("):]
        cargador = cargador[:cargador.index("\nasync function vcFetchTargets(")]
        assert "vcSyncCabecera(d)" in cargador, (
            "la cabecera de la gráfica de Víctor no se sincroniza con SU payload")
        sync = html[html.index("function vcSyncCabecera(d) {"):]
        sync = sync[:sync.index("\n}")]
        assert "d.gex.regime" in sync, "la insignia no usa el régimen de su motor"
        assert "d.spot" in sync, "el título no usa el spot de su motor"

    def test_la_lectura_de_gamma_de_victor_se_pinta(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "function vcGexHTML(" in html
        for campo in ("king_strike", "flip_strike", "total_net_gex", "regime",
                      "confidence", "low_liquidity"):
            assert f"g.{campo}" in html, f"`gex.{campo}` se sirve y no se pinta"

    def test_el_dashboard_lleva_su_disclaimer(self):
        """Su `page.tsx` cierra con «Las predicciones son estimaciones de IA, no
        consejo financiero» — DOS veces, una por vista.

        Aquí las dos vistas están fundidas en una, así que va una vez; pero
        faltaba entera. La Wheel y las Ideas ya tenían la suya, y la pantalla
        que enseña los TARGETS —la que más se parece a una promesa— era justo
        la que no decía nada.
        """
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function renderVictorTargets(d) {")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        assert "estimaciones de IA" in cuerpo
        assert "no consejo financiero" in cuerpo

    def test_los_bloques_nuevos_se_llaman_desde_el_render(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        render = html[html.index("function renderVictorTargets(d) {"):]
        render = render[:render.index("\nfunction ")]
        for fn in ("vcGexHTML", "vcScorecardHTML", "vcLevelsHTML", "vcHeatmapHTML",
                   "vcClustersHTML", "vcMemoryHTML"):
            assert f"function {fn}(" in html, f"{fn} no existe"
            assert f"{fn}(d)" in render, f"{fn} existe pero nadie lo llama"


class TestIdeasYWheelTampocoTiranNada:
    """La cobertura de hojas, extendida a las otras dos pestañas.

    `TestElPanelNoTiraNadaDelPayload` barre el scorecard entero: cada campo que
    el motor sirve tiene que tener consumidor en el panel, o estar declarado con
    su motivo. Ideas y Wheel no tenían ese barrido — sus campos nuevos estaban
    cubiertos por tests escritos a mano, que solo cazan lo que alguien se
    acordó de comprobar.

    Sin esto, añadir un campo al payload y olvidar pintarlo no lo caza nadie:
    el motor calcula algo que nunca llega a la pantalla y nada falla.
    """

    @staticmethod
    def _hojas(d, prefijo=""):
        for k, v in (d or {}).items():
            ruta = f"{prefijo}{k}"
            if isinstance(v, dict):
                yield from TestIdeasYWheelTampocoTiranNada._hojas(v, ruta + ".")
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                yield from TestIdeasYWheelTampocoTiranNada._hojas(v[0], ruta + ".")
            else:
                yield ruta

    #: Hojas que el panel no lee, con su motivo. Mismo contrato que el barrido
    #: del scorecard: o se pinta, o se declara aquí.
    _NO_SE_PINTAN = {
        # ── Ideas ──
        "ok": "bandera de control; su ausencia ya dispara el mensaje de error",
        "engine": "sello del motor, para depurar desde la API",
        "generated_at": "marca de tiempo; el panel enseña la del poller en vivo",
        "period": "la ventana del escaneo, fija en su `1d`",
        "saved_tickers": "cuántos tickers se persistieron para el sub-agente 6; "
                         "es contabilidad del store, no un dato de mercado",
        "ideas.delta": "no se pinta como número: el filtro de moneyness ya usó "
                       "la distancia al strike, que sí se explica en la nota",
        "ideas.iv": "la IV del contrato suelto no informa sin su rango; el rank "
                    "vive en el scorecard del ticker",
        "ideas.open_interest": "el OI se usa en el score de inusualidad, que sí "
                               "se pinta como /30",
        "ideas.asset_price": "el spot del subyacente; la fila lleva el strike",
        "ideas.size": "número de contratos del trade; el dinero es lo comparable",
        "ideas.theta": "theta absoluta; la columna es `theta_pct_daily`, que es "
                       "la que se puede comparar entre contratos",
        "ideas.sizing.blocked_reason": "el CÓDIGO del bloqueo (`theta_alto`, "
                                       "`vencido`…), para filtrar desde la API; "
                                       "en pantalla va `blocked`, que es la frase",
        "perfil.riesgo_pct": "se pinta dentro de la franja como el % del riesgo "
                             "por operación",
        # ── Wheel ──
        "preset_id": "el id del preset elegido; en pantalla va su etiqueta",
        "candidates.metrics.return_pct": "el retorno del periodo; la columna es el "
                                         "anualizado, que es lo comparable entre DTEs",
        "blocked_total": "el total de bloqueados; el desglose por motivo es lo que "
                         "se pinta, y ya lleva sus cuentas",
        "preset": "la etiqueta del preset activo; los botones ya la pintan desde "
                  "`presets[].label`, y el activo va resaltado",
        # ── Time & Sales ──
        "ticker": "el símbolo; va en el encabezado de la tarjeta, no en la tabla",
        # OJO: `truncated` es raíz y lo sirven Ideas y la cinta. Ideas SÍ lo
        # pinta («(truncado)» junto al conteo de páginas); la cinta no, a
        # propósito —
        "truncated": "en la CINTA no: dice que se cortó la bajada (tope de "
                     "páginas), y lo que el usuario necesita saber es si se "
                     "cortó la TABLA. Eso es `shown < notableCount`, su propia "
                     "condición, que sí se pinta como «top N». En Ideas sí se "
                     "pinta, porque allí el recorte de la bajada ES el recorte "
                     "de la lista",
        "trades.id": "identificador del trade, para deduplicar y para el ×N de "
                     "los repetidos",
        "trades.symbol": "el OCC completo; la columna de contrato enseña "
                         "subyacente + strike + tipo, que es lo legible",
        "trades.gamma": "su `NotableTable` pinta delta y ya; gamma, theta y vega "
                        "de un trade suelto no se comparan entre contratos",
        "trades.theta": "idem gamma",
        "trades.vega": "idem gamma",
        "trades.volume": "el volumen del contrato; lo que informa es si SUPERÓ el "
                         "open interest, y eso es la señal «Vol > OI»",
        "trades.open_interest": "idem volumen: la señal es la comparación, no el "
                                "número suelto",
        "trades.exceeded_oi": "es una de las siete señales; la pinta `vcSenalesHTML`",
        "trades.big": "señal, pintada por `vcSenalesHTML`",
        "trades.conv_delta": "señal, pintada por `vcSenalesHTML`",
        "trades.leap": "señal, pintada por `vcSenalesHTML`",
        "trades.multileg": "señal, pintada por `vcSenalesHTML`",
        "trades.simultaneous": "señal, pintada por `vcSenalesHTML`",
        "trades.expiry_status": "si el contrato ya venció; la cinta es del día y "
                                "la columna de vencimiento lleva los DTE",
    }

    #: Ayudantes que los renders llaman y que también pintan. Sin ellos, un
    #: campo pintado por `vcCabeceraPerfil` saldría como huérfano.
    _AYUDANTES = ("vcCabeceraPerfil", "vcRiesgoHTML", "vcSenalesHTML",
                  "vcVenceLabel", "vcHistoriaHTML", "vcFrenoHTML", "vcQuemaHTML")

    @staticmethod
    def _cuerpo(render):
        """El cuerpo de UNA función de render, no el archivo entero.

        Buscar la hoja en todo el HTML era un colador: un `d.iv` de otro panel
        daba por pintada la `iv` de la Wheel. Acotado a su render, la ausencia
        significa lo que dice.
        """
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index(f"function {render}(")
        prof, j = 0, html.index("{", i)
        k = j
        while k < len(html):
            if html[k] == "{":
                prof += 1
            elif html[k] == "}":
                prof -= 1
                if prof == 0:
                    break
            k += 1
        return html[j:k]

    def _barre(self, payload, nombre, render):
        import re

        cuerpo = self._cuerpo(render)
        for h in self._AYUDANTES:
            cuerpo += self._cuerpo(h)
        huerfanas = []
        for ruta in sorted(set(self._hojas(payload))):
            if ruta in self._NO_SE_PINTAN:
                continue
            hoja = ruta.split(".")[-1]
            # Tres formas de leerlo: `c.hoja`, `c['hoja']` y `sc[k].hoja` — esta
            # última es acceso por corchetes, y el `\w+\.` de antes no la veía:
            # daba por huérfano lo que el panel sí pinta en bucle.
            patron = (rf"\b\w+\.{re.escape(hoja)}\b"
                      rf"|\['{re.escape(hoja)}'\]"
                      rf"|\]\.{re.escape(hoja)}\b")
            if not re.search(patron, cuerpo):
                huerfanas.append(ruta)
        assert not huerfanas, (
            f"{nombre}: el motor sirve {huerfanas} y `{render}` no los pinta. "
            f"O se cablean, o se declaran en `_NO_SE_PINTAN` con su motivo.")

    def test_ideas_no_sirve_nada_que_el_panel_tire(self, client, mercado):
        self._barre(client.get("/api/tito-ideas").json(), "Ideas", "renderProjIdeas")

    def test_wheel_no_sirve_nada_que_el_panel_tire(self, client, wheel_dobles):
        self._barre(client.get("/api/tito-wheel").json(), "Wheel", "renderProjWheel")

    def test_la_cinta_no_sirve_nada_que_el_panel_tire(self, client):
        """La tercera pestaña, que era el agujero que quedaba.

        Ideas y Wheel tenían barrido; la cinta no. Y ahí es donde más duele:
        `_fila` sirve 27 campos por operación y la tabla enseñaba nueve
        columnas. `bid` y `ask` viajaban en cada fila desde el primer día y
        nadie los pintaba — su `NotableTable` tiene columna «Bid/Ask» justo
        para eso: las flechas ↑/↓ dicen que el print salió fuera de la
        horquilla, y sin la horquilla no se sabe por cuánto.
        """
        self._barre(client.get("/api/tito-tape?ticker=DEMO").json(),
                    "Time & Sales", "renderProjTape")

    def test_el_registro_no_miente(self, client, mercado, wheel_dobles):
        """Una declaración para una hoja que ya no existe es documentación
        podrida: dice que algo se decidió a propósito cuando ya no hay nada."""
        vivas = set()
        for r in ("/api/tito-ideas", "/api/tito-wheel", "/api/tito-tape?ticker=DEMO"):
            vivas |= set(self._hojas(client.get(r).json()))
        # `ideas.history.*` solo aparece cuando ese ticker YA tiene flows
        # guardados, y el doble arranca con el store vacío. Que no salga aquí no
        # significa que no se sirva — significa que no hay historial todavía.
        vivas |= {r for r in self._NO_SE_PINTAN if r.startswith("ideas.history.")}
        fantasmas = sorted(set(self._NO_SE_PINTAN) - vivas)
        assert not fantasmas, f"declaradas pero ya no se sirven: {fantasmas}"

    def test_el_registro_tampoco_miente_al_reves(self):
        """La otra cara de la podredumbre: una entrada que dice «esto NO se
        pinta» de algo que ahora **sí** se pinta.

        Pasó con cuatro de Ideas a la vez. `ideas.expiration` estaba declarada
        como «la fecha cruda; el panel pinta los DTE» — y su `expiryLabel` dice
        literalmente «fecha real + días restantes, **nunca solo 57d**». Igual
        con `price`, `timestamp` y `history.median_sessions`. El barrido de
        huérfanas nunca lo caza: una hoja declarada se salta antes de mirar
        nada, así que la declaración sobrevive a su propio motivo y queda como
        documentación que afirma lo contrario de lo que hace el código.
        """
        import re

        #: Colisiones de NOMBRE, no declaraciones podridas. El barrido busca la
        #: hoja por su última palabra, así que dos hojas distintas que se llamen
        #: igual son indistinguibles para él. `trades.volume` es el volumen del
        #: contrato, que no se pinta; `unusual_parts.volume` es el sub-score de
        #: tamaño de la orden, que sí — y los dos son «volume». Los nombres son
        #: los de su `FlowRow` y su `TradeScores`; renombrarlos para que el test
        #: no se confunda sería divergir de él por comodidad del test.
        #: `trades.gamma` y `trades.theta` son las GRIEGAS del contrato, que su
        #: `NotableTable` no pinta; `unusual_scores.gamma` y `.theta` son dos de
        #: los seis sub-puntajes 0-10 del sub-agente 3, que sí se pintan en el
        #: tooltip de la columna Inusual. Mismo nombre, cosas distintas — y los
        #: nombres son los de su `FlowRow` y su `UnusualScores`.
        COLISIONES = {"trades.volume", "trades.gamma", "trades.theta"}

        for render, prefijo in (("renderProjIdeas", "ideas."),
                                ("renderProjWheel", "candidates."),
                                ("renderProjTape", "trades.")):
            cuerpo = self._cuerpo(render)
            for h in self._AYUDANTES:
                cuerpo += self._cuerpo(h)
            mentiras = []
            for ruta in sorted(self._NO_SE_PINTAN):
                if not ruta.startswith(prefijo) or ruta in COLISIONES:
                    continue
                hoja = ruta.split(".")[-1]
                patron = (rf"\b\w+\.{re.escape(hoja)}\b"
                          rf"|\['{re.escape(hoja)}'\]"
                          rf"|\]\.{re.escape(hoja)}\b")
                if re.search(patron, cuerpo):
                    mentiras.append(ruta)
            assert not mentiras, (
                f"{render}: {mentiras} están declaradas como «no se pintan» y "
                "el panel SÍ las pinta. Bórralas del registro.")


class TestBarrasIntradia:
    """`/api/tito-bars` — su `/api/bars`, la última de sus once rutas que
    estaba a medias.

    El payload de proyecciones ya traía el diario (`history`), que es lo que
    piden dos de sus tres consumidores. El tercero, `FlowPriceChart`, ofrece
    INTRADÍA, y eso no existía: agregado por día se ve qué día entró el dinero
    grande; en velas de 5 minutos se ve si el precio se movió antes o después
    de que entrara, que es lo que mide el sub-agente 6.
    """

    def test_la_tabla_de_marcos_es_la_suya(self):
        import vertex_api as V

        assert V._TITO_TF == {"1y": (1, "day", 365),
                              "15m10d": (15, "minute", 10),
                              "5m5d": (5, "minute", 5)}

    def test_un_marco_desconocido_cae_al_de_por_defecto(self, monkeypatch):
        """`TF[tf] ?? TF["5m5d"]` suyo: no es un error, es el de por defecto."""
        import vertex_api as V

        pedido = {}

        def _fake(tk, mult, span, days, **kw):
            pedido.update(tk=tk, mult=mult, span=span, days=days)
            return []

        monkeypatch.setattr("wbj.tito.massive.fetch_bars", _fake)
        d = self._get("DEMO", tf="no-existe")
        assert pedido["mult"] == 5 and pedido["span"] == "minute" and pedido["days"] == 5
        assert d["tf"] == "5m5d", "el eco del tf tiene que ser el que se usó"

    def _get(self, ticker, tf=None):
        from fastapi.testclient import TestClient

        import vertex_api as V

        q = f"/api/tito-bars?ticker={ticker}" + (f"&tf={tf}" if tf else "")
        r = TestClient(V.app).get(q)
        assert r.status_code == 200, r.text
        return r.json()

    def test_sirve_las_velas_con_el_tiempo_en_segundos(self, monkeypatch):
        from wbj.tito.massive import TfBar

        monkeypatch.setattr("wbj.tito.massive.fetch_bars",
                            lambda *a, **k: [TfBar(1_770_000_000, 1.0, 2.0, 0.5, 1.5)])
        d = self._get("DEMO", tf="5m5d")
        assert d["bars"] == [{"time": 1_770_000_000, "open": 1.0, "high": 2.0,
                              "low": 0.5, "close": 1.5}]

    def test_un_ticker_invalido_es_400_no_500(self):
        from fastapi.testclient import TestClient

        import vertex_api as V

        assert TestClient(V.app).get("/api/tito-bars?ticker=").status_code == 400

    def test_un_fallo_de_la_fuente_no_filtra_el_mensaje(self, monkeypatch):
        """`_describe` de `massive.py` puede citar el cuerpo de la respuesta, y
        eso es superficie de fuga. Su ruta sí lo devuelve; aquí no."""
        from fastapi.testclient import TestClient

        import vertex_api as V

        def _revienta(*a, **k):
            raise RuntimeError("apikey=SECRETO-123 rechazada")

        monkeypatch.setattr("wbj.tito.massive.fetch_bars", _revienta)
        r = TestClient(V.app).get("/api/tito-bars?ticker=DEMO&tf=1y")
        assert r.status_code == 502
        assert "SECRETO" not in r.text


class TestLasConstantesDeSuRutaDeFlujo:
    """Sus siete `const` de `/api/flow`, con SU valor.

    Cuatro faltaban. Tres estaban aquí como números sueltos dentro de la
    llamada —el mismo valor, pero sin nombre, así que el cotejo de constantes
    de la auditoría no podía verlas— y la cuarta tenía otro valor.

    La cuarta importa de verdad: `CONVICTION_TABLE_CAP` decide cuántas filas de
    convicción viajan al panel, y de esas filas comen sus TRES tarjetas
    (`ConvictionTransactions`, `ActivityCard` y `MoneyFlowCard`). La última es
    la gráfica que dice "el dinero de CADA DÍA": con 25 filas no es el dinero
    del día, es el de los 25 trades más grandes, y un día entero de trades
    medianos no aparecía.
    """

    def test_las_siete_valen_lo_que_las_suyas(self):
        from wbj.tito import scorecard as S

        assert S.MIN_PREMIUM == 100_000
        assert S.LEAN_MAX_PAGES == 6
        assert S.TABLE_CAP == 100
        assert S.CONVICTION_DAYS == 30
        assert S.CONVICTION_MIN_PREMIUM == 1_000_000
        assert S.CONVICTION_MAX_PAGES == 15
        assert S.CONVICTION_TABLE_CAP == 150

    def test_la_capa_web_usa_las_del_motor_no_copias(self):
        """Un número escrito a mano en la llamada se queda atrás en silencio el
        día que él cambie el suyo."""
        from wbj.tito import scorecard as S

        import vertex_api as V

        assert V.TITO_MIN_PREMIUM is S.MIN_PREMIUM
        assert V.TITO_LEAN_MAX_PAGES is S.LEAN_MAX_PAGES
        assert V.TITO_TABLE_CAP is S.TABLE_CAP
        assert V.TITO_CONVICTION_TABLE_CAP is S.CONVICTION_TABLE_CAP

    def test_ningun_tope_suelto_quedo_en_las_dos_rutas(self):
        """El 120 de la cinta y el 25 de convicción, que eran los dos que no
        coincidían con los suyos."""
        import inspect

        import vertex_api as V

        cinta = inspect.getsource(V.tito_tape)
        assert "[:120]" not in cinta and "TITO_TABLE_CAP" in cinta
        assert "max_pages=6" not in cinta and "TITO_LEAN_MAX_PAGES" in cinta


class TestTopFlowsNotables:
    """`topFlows` de su `page.tsx` — las 3 mayores de `convRows ∪ notable`.

    Su `PredictionCard` las pinta debajo de los escenarios, y aquí faltaba el
    bloque entero. Es el único sitio del panel donde los tres targets van
    acompañados de las operaciones CONCRETAS que los sostienen: sin él los
    números salen sin que se pueda ver de qué dinero se dedujeron.
    """

    def test_son_tres_como_mucho_y_ordenadas_por_premium(self, client, mercado):
        tf = client.get("/api/projection-targets?ticker=DEMO").json()["top_flows"]
        assert len(tf) <= 3
        primas = [f["premium"] for f in tf]
        assert primas == sorted(primas, reverse=True)

    def test_la_union_es_conviccion_MAS_notable_no_solo_una(self):
        """La ventana corta trae operaciones recientes que la de 30 días
        todavía no tiene. Con una sola, el top 3 sale de otro universo."""
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V)
        i = fuente.index('"top_flows"')
        bloque = fuente[i:i + 900]
        assert "_tito_unir(" in bloque
        assert "conviction_flow" in bloque and "flow.interesting" in bloque

    def test_la_marca_alcista_es_su_regla(self):
        """`(call ∧ ask) ∨ (put ∧ bid)`: comprar calls y vender puts son la
        MISMA apuesta, y las dos salen en verde."""
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V)
        i = fuente.index('"alcista"')
        bloque = fuente[i:i + 260]
        assert '"call"' in bloque and '"ask"' in bloque
        assert '"put"' in bloque and '"bid"' in bloque

    def test_el_panel_lo_pinta(self):
        import pathlib

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "function vcTopFlowsHTML(" in html
        assert "${vcTopFlowsHTML(d)}" in html, "definida pero nadie la llama"
        assert "Top 3 flows notables" in html


class TestElPanelNoLeeCamposQueNadieManda:
    """Un campo que el frontend lee y el backend no envía **no rompe nada**:
    se pinta "—" o no se pinta, y nadie se entera.

    Pasó de verdad con la ficha de empresa: `vcCompanyHTML` leía `c.exchange` y
    `c.employees`, `_tito_company` los declaraba en su dict base valiendo
    `None`, y `fetch_company` no los pedía a Massive. El subtítulo de la
    cabecera salía con el sector solo y la casilla de empleados vacía, durante
    todas las rondas anteriores, sin un solo error.

    Esto lo generaliza: se recogen TODOS los campos de primer nivel que leen
    las funciones de render del panel y se cruzan con lo que sirve la ruta.
    """

    #: Lo que el panel lee y NO viene de `/api/projection-targets`, con su
    #: procedencia. Se declara por nombre para que la lista no crezca sola.
    DE_OTRAS_RUTAS = {
        # /api/tito-ideas
        "ideas", "scanned", "pages", "min_premium", "moneyness_cap", "rejected",
        "blocked_summary", "blocked_total", "with_history", "perfil",
        # /api/tito-wheel
        "candidates", "presets", "preset_id", "preset_explain", "quotes_missing",
        "with_candidates", "tickers", "failed",
        # /api/tito-tape — `renderProjTape`
        "trades", "notable", "total", "period", "aggression",
        # …y el corte con el que se tiñe una fila, servido en vez de repetido
        # en el panel: la leyenda decía «≥7/30» y el corte real es 24/30.
        "unusual_cut",
        # /api/tito-ideas — `renderProjIdeas` (el aviso de escaneo truncado)
        "truncated",
        # /api/tito-health — `renderProjSalud`, la pestaña de Cobertura
        "checks",
        # sobres de error / degradación, comunes a todas
        "ok", "error", "degraded",
    }

    def test_todo_lo_que_pinta_el_panel_llega_o_esta_declarado(self, client, mercado):
        import pathlib
        import re

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        fns = re.findall(r"function (vc[A-Za-z0-9_]+|renderProj[A-Za-z0-9_]+)\s*\(\s*d\b", html)
        assert len(fns) >= 20, "el escaneo no encontró las funciones de render"
        leidos = set()
        for fn in fns:
            i = html.index(f"function {fn}(")
            j = html.find("\nfunction ", i + 10)
            leidos |= set(re.findall(r"\bd\.([a-z_][a-z0-9_]*)",
                                     html[i:j if j > 0 else i + 4000]))

        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d.get("ok") is not False, f"la ruta degradó: {d.get('error')}"
        huerfanos = sorted(leidos - set(d) - self.DE_OTRAS_RUTAS)
        assert not huerfanos, (
            f"el panel lee campos que ninguna ruta manda: {huerfanos}. "
            "O el backend dejó de mandarlos, o el render quedó colgando.")


class TestElArranqueNoDependeDeQueFalteConfiguracion:
    """El despliegue caído: una variable BIEN configurada rompía el arranque.

    `engine/` llegaba a `sys.path` solo como EFECTO SECUNDARIO de
    `_sec_user_agent()`, que lo insertaba dentro de su rama de respaldo. Con
    `EDGAR_USER_AGENT` definida —el caso de Render, y el que la SEC exige— esa
    función devuelve el valor y retorna ANTES de insertar nada. El
    `from wbj.tito.scorecard import ...` de nivel de módulo moría entonces con
    `ModuleNotFoundError: No module named 'wbj'`, uvicorn salía con código 1, y
    Render lo reportaba como «Exited with status 1 while running your code».

    En local nunca se veía: nadie define `EDGAR_USER_AGENT` para desarrollar,
    así que la función caía al respaldo, insertaba el path de paso y todo lo
    demás importaba. La AUSENCIA de configuración salvaba el arranque.

    Los tests normales tampoco: corren dentro de un pytest que ya tiene el
    `engine/` en el path por su propio conftest. Hay que importar en un proceso
    NUEVO, con el entorno de Render, para ver lo que ve Render.
    """

    #: Las que Render tiene puestas y esta máquina no. Cada una es una rama del
    #: código que en local no se ejecuta nunca.
    ENTORNO_RENDER = {
        "EDGAR_USER_AGENT": "Vertex Fund OS research@ejemplo.com",
        "VERTEX_API_TOKEN": "un-token-largo-de-prueba",
        "VERTEX_ORIGIN": "https://ejemplo.onrender.com",
        "VERTEX_DB_KEY": "clave-de-prueba-para-fernet-0123456789",
        "MASSIVE_API_KEY": "falsa",
        "MARKETSNACK_COOKIE": "sesion=falsa",
        "MASSIVE_MAX_PAGES": "40",
        "PORT": "10000",
    }

    def _importa(self, extra: dict) -> tuple[int, str]:
        import subprocess

        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": os.environ.get("HOME", "/tmp")}
        env.update(extra)
        r = subprocess.run(
            [sys.executable, "-c", "import vertex_api; print('IMPORT OK')"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
        return r.returncode, (r.stdout + r.stderr)

    def test_importa_con_el_entorno_de_render(self):
        cod, salida = self._importa(self.ENTORNO_RENDER)
        assert cod == 0, (
            "`import vertex_api` falla con las variables de Render puestas — "
            f"eso es el «Exited with status 1» del panel:\n{salida[-1500:]}")
        assert "IMPORT OK" in salida

    def test_importa_tambien_sin_ninguna_variable(self):
        """La otra mitad: que arreglarlo para Render no lo rompa en local."""
        cod, salida = self._importa({})
        assert cod == 0, f"falla sin configuración:\n{salida[-1500:]}"

    def test_el_motor_esta_en_el_path_antes_del_primer_import(self):
        """El arreglo, en el código: `engine/` entra en `sys.path` donde se
        define la ruta, no donde a alguien le hizo falta. Un import de nivel de
        módulo no puede depender de qué función se ejecutó antes."""
        fuente = (ROOT / "vertex_api.py").read_text(encoding="utf-8")
        i_def = fuente.index("_WBJ_ENGINE_PATH = os.path.join(")
        i_ins = fuente.index("sys.path.insert(0, _WBJ_ENGINE_PATH)", i_def)
        # La inserción va pegada a la definición, y a nivel de módulo (sin
        # indentar dentro de una función).
        entre = fuente[i_def:i_ins]
        assert "def " not in entre, "la inserción volvió a quedar dentro de una función"
        # Y ocurre ANTES del primer `from wbj` de nivel de módulo.
        i_wbj = fuente.index("\nfrom wbj.")
        assert i_ins < i_wbj, "el path se inserta después del primer import de wbj"

    def test_sec_user_agent_usa_la_variable_cuando_esta(self):
        """Y el atajo de `_sec_user_agent` sigue ahí: con la variable puesta no
        toca el motor para nada. Lo que se arregló es que ya no sea ÉL quien
        pone el path."""
        import vertex_api as V

        fuente = inspect.getsource(V._sec_user_agent)
        assert 'os.environ.get("EDGAR_USER_AGENT")' in fuente
        assert fuente.index("return ua") < fuente.index("_WBJ_ENGINE_PATH")


class TestElTabSeVeEnRender:
    """Lo que decide si el tab se VE en el servidor, no solo si los tests pasan.

    Tres cosas hunden un despliegue sin que ningún test de lógica se entere:
    un acento grave mal puesto rompe el JS y el tab sale en blanco; una ruta que
    el panel pide y el servidor no sirve da 404 en silencio; y una ruta que
    revienta con 5xx en vez de degradar deja al usuario con un error de red en
    vez de con una explicación.
    """

    def test_el_javascript_del_panel_es_valido(self):
        """Un backtick dentro de un comentario HTML CIERRA la plantilla y se
        lleva el tab entero — no una tarjeta, el tab. Ya pasó dos veces."""
        import re
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("node"):
            pytest.skip("node no esta instalado")
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        bloques = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html)
        assert bloques, "el panel no tiene bloques de script"
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                         encoding="utf-8") as f:
            f.write(max(bloques, key=len))
            ruta = f.name
        r = subprocess.run(["node", "--check", ruta], capture_output=True, text=True)
        assert r.returncode == 0, f"el JS del panel no compila:\n{r.stderr[:800]}"

    def test_cada_ruta_que_el_panel_pide_existe(self, client):
        """Un 404 en una pestaña no rompe las demás: se ve como «cargando» para
        siempre. Se cruzan las 45 llamadas del panel con las rutas de la app."""
        import re

        import vertex_api as V

        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        pedidas = sorted(set(re.findall(r"\$\{API_BASE\}(/api/[a-z0-9\-_/]+)", html)))
        assert len(pedidas) >= 20, f"el escaneo solo vio {len(pedidas)} rutas"
        servidas = {getattr(x, "path", "") for x in V.app.routes}
        faltan = [p for p in pedidas if p not in servidas]
        assert not faltan, f"el panel pide rutas que el servidor no sirve: {faltan}"

    def test_el_html_llega_entero_por_la_raiz(self, client):
        """Render sirve `/`. Si el panel no viaja ahí, no hay tab."""
        r = client.get("/")
        assert r.status_code == 200
        for fn in ("renderProjTape", "renderProjWheel", "renderProjIdeas",
                   "renderVictorTargets", "vcOutcomesHTML"):
            assert fn in r.text, f"{fn} no llega al navegador"

    def test_sin_claves_las_rutas_del_tab_degradan_en_vez_de_reventar(self, client,
                                                                      monkeypatch):
        """En un Render recién creado no hay `MASSIVE_API_KEY` ni cookie. Con
        200 + `ok:false` el panel explica qué falta; con un 5xx el usuario ve un
        error de red y no sabe si es el agente o su internet."""
        for var in ("MASSIVE_API_KEY", "MARKETSNACK_COOKIE"):
            monkeypatch.delenv(var, raising=False)
        for ruta in ("/api/projection-targets?ticker=DEMO", "/api/tito-ideas",
                     "/api/tito-tape?ticker=DEMO", "/api/tito-wheel"):
            r = client.get(ruta)
            assert r.status_code == 200, f"{ruta} devolvió {r.status_code}"
            d = r.json()
            if d.get("ok") is False:
                assert d.get("error"), f"{ruta} degrada sin decir por qué"

    def test_si_faltan_las_velas_la_grafica_de_dinero_lo_dice(self):
        """`/api/tito-bars` responde 502 cuando Massive falla —como su ruta—, y
        el consumidor se lo tragaba con tres `return` mudos. En un gráfico que
        se llama «dinero contra precio», que falte el precio sin avisar se lee
        como que no hubo movimiento."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("async function _projFlowPrecio(")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        assert "_projFlowAviso(" in cuerpo, "los fallos vuelven a ser mudos"
        assert 'id="projFlowMoneyAviso"' in html, "el aviso no tiene dónde pintarse"
        assert "function _projFlowAviso(" in html


class TestSuDisenoNoSoloSuLogica:
    """Su `globals.css` no lo había mirado nadie en dieciséis rondas.

    Se portó su lógica, sus textos, sus umbrales y sus constantes. El diseño se
    dibujó con utilidades sueltas de Tailwind, y eso deja fuera decisiones suyas
    que no son adorno — la que más se nota es `tabular-nums`: sin ella cada
    dígito lleva su propio ancho, así que $1.111 y $8.888 no acaban en la misma
    columna y la vista no puede comparar dos precios sin leerlos. En una
    pantalla que es casi toda números, eso no es tipografía fina.
    """

    #: Sus tonos, de `:root` de su CSS. El `emerald-400` de Tailwind es #34d399
    #: y su verde es #12b76a: parecidos en la cabeza, distintos en pantalla.
    TONOS = {"--vc-accent": "#2f6bff", "--vc-green": "#12b76a",
             "--vc-red": "#f04438", "--vc-put": "#f97066",
             "--vc-amber": "#f79009"}

    @staticmethod
    def _css():
        return (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")

    def test_sus_tonos_exactos(self):
        # Por regex y no por cadena literal: el bloque alinea los valores con
        # espacios y el test debe medir el VALOR, no el formato.
        import re

        html = self._css()
        for var, hexa in self.TONOS.items():
            assert re.search(rf"{re.escape(var)}:\s*{hexa}\b", html, re.I), \
                f"{var} no es el suyo ({hexa})"

    def test_las_cuatro_reglas_de_tabla(self):
        html = self._css()
        for regla in ("font-variant-numeric: tabular-nums",
                      "tbody tr:nth-child(odd)", "tbody tr:hover",
                      "position: sticky"):
            assert regla in html, f"falta «{regla}» de sus tablas"

    def test_su_escala_de_espaciado_con_su_recorte_de_movil(self):
        """Su regla, escrita en su propio CSS: «el gap ENTRE cards (lg) tiene
        que superar al padding DENTRO (md), si no la proximidad se invierte y
        las cards se leen pegadas». Y en móvil las recorta, porque «ahí el
        scroll pesa más que el agrupamiento»."""
        html = self._css()
        for var, px in (("--vc-xs", 8), ("--vc-sm", 14), ("--vc-md", 24), ("--vc-lg", 32)):
            assert f"{var}: {px}px" in html, f"{var} no vale {px}px"
        assert "--vc-md: 16px; --vc-lg: 22px" in html, "falta su recorte de móvil"

    def test_la_fila_inusual_se_tine_ENTERA(self):
        """Su `tbody tr.unusual td { background }` tiñe la fila, no la celda del
        puntaje. Una fila inusual entre cien es lo que hay que poder encontrar
        de un vistazo."""
        html = self._css()
        assert "tr.vc-unusual td" in html
        i = html.index("function renderProjTape(d) {")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        assert "'vc-unusual'" in cuerpo, "la clase no llega a la fila"

    def test_el_lado_va_en_su_pill(self):
        """`.pill`: 999px, 700, MAYÚSCULAS y letter-spacing. Es lo que las
        separa del texto de al lado sin necesitar más color."""
        html = self._css()
        assert "text-transform: uppercase" in html and ".vc-pill" in html
        i = html.index("function renderProjTape(d) {")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        assert "vc-pill-call" in cuerpo and "vc-pill-put" in cuerpo

    def test_el_css_cuelga_del_contenedor_que_existe(self):
        """Un selector con el id equivocado es CSS muerto: no falla, no se ve,
        y parece que el diseño está puesto. Pasó al escribir esto — el
        contenedor se llama `projectionsView`, no `view-proyecciones`."""
        html = self._css()
        assert 'id="projectionsView"' in html
        assert "#projectionsView" in html
        assert "#view-proyecciones" not in html, "quedó un selector muerto"

    def test_la_divergencia_de_luminosidad_esta_declarada(self):
        """Su paleta es CLARA y este tab vive dentro de Vertex, que es oscuro.
        Lo que se porta son sus tonos y sus proporciones, no su fondo — y eso
        se dice en el sitio, no se deja a la interpretación."""
        html = self._css()
        i = html.index("EL SISTEMA DE DISEÑO DE VÍCTOR")
        bloque = html[i:i + 1400]
        assert "DIVERGENCIA DECLARADA" in bloque
        assert "#f3f4f6" in bloque, "no cita su fondo real"


class TestLaCoberturaDeLosSubAgentes:
    """`/api/tito-health` existía en el servidor y no lo llamaba NADIE.

    Es la única respuesta a la pregunta que el scorecard no contesta: «¿con qué
    dato salió este número?». Un 62/100 sostenido por dos categorías de seis se
    ve idéntico a uno sostenido por las seis — el veredicto lleva el mismo
    tamaño de letra. El diagnóstico estaba escrito, tocaba cada fuente una por
    una, decía el impacto y el arreglo… y vivía declarado como «se consulta a
    mano al desplegar», o sea nunca.
    """

    #: Lo que tiene que mirar. Las tres últimas faltaban: son las que deciden si
    #: el agente MEJORA con el tiempo o se estrena cada semana.
    OBLIGATORIOS = ("ticker", "motor", "MASSIVE_API_KEY", "MARKETSNACK_COOKIE",
                    "memoria.disco", "memoria.iv", "memoria.flows",
                    "memoria.predicciones", "memoria.cadenas", "memoria.respaldo")

    def test_diagnostica_las_tres_series_y_el_respaldo(self, client):
        d = client.get("/api/tito-health?ticker=DEMO").json()
        nombres = [c["check"] for c in d["checks"]]
        for k in self.OBLIGATORIOS:
            assert k in nombres, f"el diagnóstico no mira «{k}»"

    def test_cada_fallo_dice_el_impacto_y_el_arreglo(self, client):
        """Un diagnóstico que dice «falta X» y no dice qué se rompe ni cómo
        arreglarlo obliga a leer el código para usarlo."""
        d = client.get("/api/tito-health?ticker=DEMO").json()
        for c in d["checks"]:
            if c["ok"]:
                continue
            assert c.get("impacto"), f"«{c['check']}» falla y no dice qué se rompe"
            assert c.get("arreglo"), f"«{c['check']}» falla y no dice cómo arreglarlo"

    def test_la_calibracion_se_declara_con_sus_muestras(self, client):
        """El lazo que corrige los targets. Sin él el agente puede llevar seis
        meses apuntando un 8% de más y seguir apuntando lo mismo."""
        d = client.get("/api/tito-health?ticker=DEMO").json()
        pred = next(c for c in d["checks"] if c["check"] == "memoria.predicciones")
        assert "muestras" in pred["detalle"] and "vencidas" in pred["detalle"]

    def test_el_respaldo_dice_que_sin_el_todo_vuelve_a_cero(self, client):
        """En Render free el disco se borra en cada redeploy. Sin respaldo, los
        contadores de arriba vuelven a cero solos y el agente se estrena otra
        vez sin que nadie lo note: los números que enseña son correctos,
        simplemente empiezan de nuevo."""
        d = client.get("/api/tito-health?ticker=DEMO").json()
        r = next(c for c in d["checks"] if c["check"] == "memoria.respaldo")
        if not r["ok"]:
            assert "VERTEX_GIT_TOKEN" in (r["arreglo"] or "")
            assert "redeploy" in (r["impacto"] or "")

    def test_no_imprime_credenciales(self, client, monkeypatch):
        """Dice si la clave está y cuánto mide. Nunca lo que vale."""
        monkeypatch.setenv("MASSIVE_API_KEY", "clave-secreta-de-verdad-123")
        monkeypatch.setenv("MARKETSNACK_COOKIE", "cookie-secreta-de-verdad-456")
        cuerpo = client.get("/api/tito-health?ticker=DEMO").text
        assert "clave-secreta-de-verdad" not in cuerpo
        assert "cookie-secreta-de-verdad" not in cuerpo

    def test_la_pestana_existe_y_lo_pinta(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "'salud'" in html and "Cobertura" in html
        assert "function loadProjSalud(" in html and "function renderProjSalud(" in html
        assert "loadProjSalud();" in html, "la pestaña existe y nadie la carga"
        assert 'id="projPaneSalud"' in html and 'id="projSalud"' in html
        assert "/api/tito-health?ticker=" in html, "el panel no llama a la ruta"

    def test_cada_check_dice_a_que_sub_agente_afecta(self):
        """La tabla traduce el diagnóstico de sistema a la pregunta del
        usuario: no «falta MARKETSNACK_COOKIE», sino «Agresividad, Convicción,
        Inusualidad y Contexto IV se quedan sin dato»."""
        import re

        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("const VC_SALUD_QUIEN = {")
        mapa = html[i:html.index("};", i)]
        for k in self.OBLIGATORIOS:
            if k in ("ticker", "motor"):
                continue           # no son de ningún sub-agente: son el arranque
            assert f"'{k}'" in mapa, f"«{k}» no dice a quién afecta"

    def test_la_ruta_ya_no_esta_declarada_como_huerfana(self):
        """Tenía cliente cero y estaba declarada así. Ahora lo tiene, y dejar
        la declaración sería documentación que afirma lo contrario del código."""
        aud = (ROOT / "engine" / "scripts" / "auditar_tito.py").read_text(encoding="utf-8")
        i = aud.index("RUTAS_HUERFANAS = {")
        assert '"tito-health"' not in aud[i:aud.index("\n}", i)]


class TestLaEvidenciaDelSubAgente6:
    """«Sin evidencia, no hay número» — la regla del proyecto, aplicada al 6.

    `validation.outcomes` es la tabla de su `ValidationCard`: cada flow pasado
    con cuánto recorrió a favor, cuánto en contra, cuánto tardó y si acabó
    validado o absorbido. Se calculaba ENTERA en `validation.py` y moría en el
    servidor: al panel solo llegaba la tasa. Un 62% sin sus 25 filas no deja
    ver si vino de tres aciertos grandes o de veinte pequeños, que se leen muy
    distinto.
    """

    def test_los_outcomes_llegan_al_payload(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        v = d["subagents"]["validation"]
        assert "outcomes" in v, "la evidencia del sub-agente 6 no sale de la ruta"
        assert len(v["outcomes"]) <= 25, "su `outcomes.slice(0, 25)`"
        if v["outcomes"]:
            o = v["outcomes"][0]
            for campo in ("timestamp", "type", "strike", "expiration", "premium",
                          "direction", "mfe_pct", "mae_pct", "days_to_validate",
                          "days_elapsed", "validated", "resolved"):
                assert campo in o, f"falta {campo} en la fila de evidencia"

    def test_la_tasa_y_las_filas_cuentan_lo_mismo(self, client):
        """Si la tabla dice otra cosa que el titular, una de las dos miente."""
        v = client.get("/api/projection-targets?ticker=DEMO").json()["subagents"]["validation"]
        resueltos = [o for o in v["outcomes"] if o["resolved"]]
        if not resueltos or v["hit_rate"]["resolved"] > 25:
            return          # la tabla está recortada a 25: no es comparable
        assert sum(1 for o in resueltos if o["validated"]) == v["hit_rate"]["validated"]
        assert len(resueltos) == v["hit_rate"]["resolved"]

    def test_el_panel_pinta_la_tabla(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "function vcOutcomesHTML(" in html
        assert "${vcOutcomesHTML(val)}" in html, "definida pero nadie la llama"
        assert "Qu&eacute; pas&oacute; despu&eacute;s de cada flow" in html
        for etq in ("Validado", "Absorbido", "Muy reciente"):
            assert etq in html, f"falta el resultado «{etq}»"


class TestLaMemoriaFallaEnVozAlta:
    """La escritura de la que depende TODO el aprendizaje era la única muda.

    El panel ya contaba las escrituras fallidas de cadena, trades e IV en
    `memory.escrituras_fallidas`, y las pinta. La de PREDICCIONES —la que cierra
    el lazo de calibración, o sea lo único que hace que el agente mejore con el
    tiempo— vivía en un `except Exception: pass`. Un disco lleno o un permiso
    mal puesto dejaban el track record congelado durante meses mientras el panel
    seguía diciendo «0 predicciones vencidas», que es lo que se ve el primer día.
    """

    def test_si_no_se_puede_guardar_la_prediccion_se_dice(self, client, monkeypatch):
        import wbj.tito.stores as ST

        def boom(*a, **k):
            raise OSError("No space left on device")

        monkeypatch.setattr(ST, "save_prediction", boom)
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert d["ok"] is True, "un fallo de ESCRITURA no puede tumbar el análisis"
        fallidas = (d.get("memory") or {}).get("escrituras_fallidas") or []
        assert any("predicciones" in f for f in fallidas), \
            f"la escritura falló y el panel no se entera: {fallidas}"

    def test_cuando_se_guarda_bien_no_inventa_una_alerta(self, client):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        fallidas = (d.get("memory") or {}).get("escrituras_fallidas") or []
        assert not any("predicciones" in f for f in fallidas)

    def test_el_panel_pinta_esa_lista(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "m.escrituras_fallidas" in html, \
            "el campo viaja y nadie lo enseña: volvería a fallar en silencio"


class TestElTrackRecordSeVe:
    """Su `MemoriaCard` no enseña un resumen: enseña una TABLA con qué predijo,
    qué pasó de verdad y cuánto se equivocó.

    `review_predictions` ya se llamaba en el servidor y sus `evals` se
    tiraban — solo viajaban los agregados. O sea que el panel decía "6
    predicciones vencidas, sesgo +2%" y no había forma de ver ninguna. Para lo
    único que existe esa sección —saber si el agente acierta— el resumen es
    justo lo que no basta.
    """

    def test_el_payload_lleva_las_predicciones_una_a_una(self, client, mercado):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        m = (d.get("memory") or {})
        assert "evals" in m, "el track record no viaja"
        assert isinstance(m["evals"], list)
        for e in m["evals"]:
            for k in ("date", "horizon_days", "matured", "base",
                      "actual_close", "error_pct", "best"):
                assert k in e, k

    def test_el_panel_pinta_la_tabla_con_el_color_del_error(self):
        import pathlib

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        # La función ENTERA, no sus primeros 3.000 caracteres. Con el corte
        # fijo, añadir dos líneas arriba empujaba la tabla fuera de la ventana
        # y el test denunciaba como ausente algo que seguía ahí.
        i = html.index("function vcMemoryHTML(")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        assert "m.evals" in cuerpo, "la tabla no lee los evals"
        assert "vcErrColor(" in cuerpo, "el error no va coloreado"
        assert "acertó" in cuerpo, "no se dice qué escenario acertó"


class TestLaProbabilidadDeTocarElNivel:
    """`probTouch(spot, l.price, iv, horizonDays)` de su `NivelesSimples`.

    La tabla de niveles enseñaba precio, fuerza y distancia — y no cuán
    probable era llegar. Un soporte de fuerza 80 al 15% de distancia y otro de
    fuerza 50 al 2% se leían igual de "fuertes", que es justo lo que esta
    columna desambigua. El motor ya trae `prob_touch`; solo faltaba llamarlo.
    """

    def test_cada_nivel_viaja_con_su_probabilidad(self, client, mercado):
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        niveles = (d["levels"]["supports"] or []) + (d["levels"]["resistances"] or [])
        assert niveles, "sin niveles no se prueba nada"
        for l in niveles:
            assert "touch" in l, "el nivel no lleva P(toque)"
            if l["touch"] is not None:
                assert 0.0 <= l["touch"] <= 1.0, l["touch"]

    def test_mas_cerca_es_mas_probable(self):
        """La comprobación que le da sentido: si la probabilidad no cae con la
        distancia, la columna sería decorativa."""
        from wbj.tito.expected_move import prob_touch

        cerca = prob_touch(100.0, 98.0, 0.45, 20)
        lejos = prob_touch(100.0, 80.0, 0.45, 20)
        assert cerca > lejos

    def test_usa_la_IV_del_GEX_como_el(self):
        """Su `NivelesSimples` recibe `iv={gex?.iv ?? 0.4}`. Con otra IV la
        columna diría otra cosa sin que nada fallara."""
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V)
        i = fuente.index("_iv_niveles")
        assert "r.gex.iv" in fuente[i:i + 200]
        assert "0.4" in fuente[i:i + 200], "falta su respaldo de 0.4"

    def test_el_panel_pinta_la_columna(self):
        import pathlib

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        # La función ENTERA, no sus primeros 2.200 caracteres: con el corte fijo,
        # añadirle texto arriba empujaba la cabecera fuera de la ventana y el
        # test denunciaba como ausente algo que estaba tres líneas más abajo.
        i = html.index("function vcLevelsHTML(")
        cuerpo = html[i:html.index("\nfunction ", i + 10)]
        assert "l.touch" in cuerpo, "la columna no lee el dato"
        assert "P(toque)" in cuerpo, "la columna no tiene cabecera"


class TestLaTablaDeIndustriasVIVEENUNSOLOSITIO:
    """Estuvo duplicada —panel y motor— con un test vigilando las dos copias.

    La copia se borró: el servidor ya no decide qué industrias tiene un sector,
    cotiza los tickers que el panel le manda. Una copia vigilada sigue siendo
    una copia que alguien puede olvidar, y el vigilante era trabajo que no hacía
    falta hacer.

    Lo que queda por comprobar es lo contrario de antes: que la segunda copia
    NO haya vuelto.
    """

    def test_el_motor_no_guarda_industrias(self):
        import wbj.sectores as S

        assert not hasattr(S, "INDUSTRIAS"), (
            "volvió la segunda copia de la tabla al motor")
        assert not hasattr(S, "industrias_de")

    def test_el_panel_es_el_unico_que_las_tiene(self):
        import pathlib

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "const VX_INDUSTRIAS = {" in html
        assert "VX_SECTOR_NOMBRE" not in html, (
            "el nombre del sector ya viene en la fila de la parrilla; una tabla "
            "aparte volvería a ser una copia que puede quedarse atrás")

    def test_la_ruta_cotiza_lo_que_le_pidan_EN_SU_ORDEN(self, client,
                                                        monkeypatch):
        """El orden importa: la sección enseña las industrias en el orden en
        que están escritas, y devolverlas barajadas movería las filas de sitio.

        Se sustituye la bajada por una fila de mentira — aquí se mide el
        CONTRATO de la ruta, no que FMP conteste.
        """
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "_sector_fila", lambda t: {
            "ticker": t, "nombre": t, "precio": 1.0, "cambio_pct": 0.0,
            "rsi": 50.0, "sma200": 1.0, "sma200_dist": 0.0, "cambios": {},
            "cierres": [1.0], "volumenes": [1]})
        d = client.get("/api/sectores?tickers=SMH,IGV,CIBR").json()
        assert d["ok"] is True, d
        assert d["tickers"] == ["SMH", "IGV", "CIBR"], d
        assert [f["ticker"] for f in d["filas"]] == ["SMH", "IGV", "CIBR"], d
        assert all("cierres" not in f for f in d["filas"]), (
            "las series no se mandan a la pantalla: son casi un mega y ya "
            "cumplieron su función")

    def test_sin_clave_de_FMP_lo_dice_en_vez_de_callarse(self, client):
        d = client.get("/api/sectores?tickers=SMH").json()
        if d.get("ok") is False:
            assert "FMP_API_KEY" in d["error"], d
            assert d["filas"] == []

    def test_la_basura_no_se_convierte_en_una_fila_muerta(self):
        """`../etc/passwd` pasaba el saneador general como «ETCPASSWD»: un
        ticker que no existe, que FMP no conoce y que se quedaría en la pantalla
        con sus puntos suspensivos para siempre."""
        import vertex_api as V

        assert V._sectores_pedidos("../etc/passwd") == []
        assert V._sectores_pedidos("smh, igv ,SMH") == ["SMH", "IGV"], "sin repetir"
        assert V._sectores_pedidos("DEMASIADOLARGO") == []

    def test_pedir_doscientos_no_gasta_la_cuota_de_FMP(self):
        """Una ruta que cotiza lo que le manden necesita un borde."""
        import vertex_api as V

        muchos = ",".join(f"AA{chr(65 + i % 26)}" for i in range(200))
        assert len(V._sectores_pedidos(muchos)) <= V._SECTORES_MAX_PEDIDOS


class TestLaLecturaDelMercadoEXPLICAyNoDECIDE:
    """El modelo cuenta lo que el motor ya decidió, y nada más.

    Es la regla del proyecto y aquí importa el doble: un párrafo bien escrito
    suena a análisis aunque los números digan lo contrario. Por eso al prompt
    van los datos YA CLASIFICADOS —la matriz, el flujo, la amplitud— con la
    prohibición explícita de recalcularlos.
    """

    _ROT = {
        "disponible": True,
        "salud": {"clave": "estrecho", "frase": "Rally estrecho: pocas mega-caps."},
        "matriz": {"leading": ["XLE", "XLF"], "improving": ["XLI"],
                   "weakening": ["XLK"], "lagging": ["XLRE"]},
        "sectores": {"XLK": {"cuadrante": "weakening", "flujo": "salida",
                             "volumen_rel": 1.44},
                     "XLE": {"cuadrante": "leading", "flujo": "entrada",
                             "volumen_rel": 1.62}},
        "entrando": ["XLE"], "saliendo": ["XLK"], "dispersion": 1.18,
        "dia_rojo": [{"ticker": "XLE", "cambio_pct": 1.4}],
        "diagnostico": ["Rotación hacia la economía real."],
    }
    _FILAS = [{"ticker": "SPY", "nombre": "S&P 500", "cambio_pct": -1.35,
               "rsi": 44.0, "sma200_dist": 2.1},
              {"ticker": "XLK", "nombre": "Tecnología", "cambio_pct": -2.1,
               "rsi": 36.0, "sma200_dist": -3.4},
              {"ticker": "XLE", "nombre": "Energía", "cambio_pct": 1.4,
               "rsi": 63.0, "sma200_dist": 7.7}]

    def test_los_datos_llegan_al_modelo_ya_clasificados(self):
        import vertex_api as V

        txt = V._lectura_datos(self._ROT, self._FILAS)
        for esperado in ("Liderando: XLE, XLF", "Agotándose: XLK",
                         "ENTRA DINERO", "SALE DINERO",
                         "Rally estrecho", "AGUANTAN EL DÍA ROJO"):
            assert esperado in txt, f"falta «{esperado}» en:\n{txt}"
        assert "no la recalcules" in txt, (
            "sin esa instrucción el modelo puede rehacer la matriz a ojo")
        assert "media de 200" in txt and "RSI" in txt

    def test_el_prompt_le_PROHIBE_decidir(self):
        import vertex_api as V

        sistema = V._LECTURA_SYSTEM.lower()
        # Se mide el SENTIDO, no una frase exacta: la versión anterior fijaba
        # «no inventes números» y el prompt pasó a decir «no inventes NADA»,
        # que es más fuerte — el caso salió en rojo por una mejora. Un guardián
        # que ata la redacción convierte cada mejora en un falso positivo.
        assert "no recalcules" in sistema, "tiene que prohibir rehacer los números"
        assert "no inventes" in sistema, "tiene que prohibir inventar datos"
        assert "compra" in sistema and "venta" in sistema, (
            "tiene que prohibir explícitamente recomendar operar")
        assert "objetivos de precio" in sistema
        # Lo que de verdad separa una nota de mesa de una opinión: la evidencia
        # pegada a cada afirmación y la línea entre saber e inferir.
        assert "evidencia" in sistema
        assert "infieres" in sistema or "inferencia" in sistema
        for seccion in ("qué está pasando", "dónde entra el dinero",
                        "de dónde sale", "dónde dejó de entrar",
                        "por qué está pasando", "qué esperar y qué lo invalidaría",
                        "cómo se sabe"):
            assert seccion in sistema, f"el formato no pide «{seccion}»"

    def test_sin_rotacion_no_se_inventa_una_lectura(self, client, monkeypatch):
        import vertex_api as V

        monkeypatch.setattr(V, "_LECTURA_CACHE", {})
        monkeypatch.setattr(V, "api_sectores", lambda *a, **k: {
            "ok": True, "filas": [], "rotacion": {"disponible": False,
                                                  "motivo": "Sin serie del SPY."}})
        d = client.get("/api/sectores/lectura").json()
        assert d["ok"] is False and d["texto"] == ""
        assert "SPY" in d["error"], d

    def test_si_ningun_modelo_contesta_se_dice_POR_QUE(self, client, monkeypatch):
        """Un 429 de cuota reportado como «falta la clave» manda a mirar donde
        no es. El error de CADA proveedor, no el del último."""
        import vertex_api as V

        monkeypatch.setattr(V, "_LECTURA_CACHE", {})
        monkeypatch.setattr(V, "api_sectores", lambda *a, **k: {
            "ok": True, "filas": self._FILAS, "rotacion": self._ROT})
        monkeypatch.setattr(V, "_texto_llm",
                            lambda *a, **k: ("", "", "gemini: 429 cuota"))
        d = client.get("/api/sectores/lectura").json()
        assert d["ok"] is False
        assert "429" in d["error"], d

    def test_la_lectura_sale_de_la_MISMA_foto_que_la_pantalla(self, client,
                                                              monkeypatch):
        """Pedir los datos dos veces con dos minutos de diferencia haría que lo
        que se lee y lo que se ve se contradijeran."""
        import vertex_api as V

        vistos = []
        monkeypatch.setattr(V, "_LECTURA_CACHE", {})
        monkeypatch.setattr(V, "api_sectores", lambda *a, **k: (
            vistos.append(1) or {"ok": True, "filas": self._FILAS,
                                 "rotacion": self._ROT}))
        monkeypatch.setattr(V, "_texto_llm",
                            lambda *a, **k: ("**Qué está pasando** — texto.",
                                             "gemini", ""))
        d = client.get("/api/sectores/lectura").json()
        assert d["ok"] is True and d["texto"]
        assert len(vistos) == 1, "se pidieron los datos más de una vez"

    def test_el_panel_escapa_ANTES_de_marcar_las_negritas(self):
        """Al revés de como suele hacerse, y la única forma de que un
        `<img onerror>` en la respuesta del modelo no acabe ejecutándose.

        Se mira `vxLecturaHTML`, que es por donde pasan LAS DOS lecturas —la
        del mercado y la de un grupo—. Cuando cada una tenía su copia, este
        test solo cubría una.
        """
        import pathlib
        import re

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cuerpo = html.split("function vxLecturaHTML", 1)[1][:2600]
        i_esc = cuerpo.index("_vcEsc(d.texto)")
        i_neg = cuerpo.index("replace(/\\*\\*")
        assert i_esc < i_neg, (
            "se marcan las negritas antes de escapar: el HTML del modelo "
            "entraría vivo")
        assert not re.search(r"innerHTML\s*=\s*[`\'\"]?\s*\$\{d\.texto\}", html)
        assert html.count("_vcEsc(d.texto)") == 1, (
            "hay más de un sitio pintando la lectura: uno puede quedarse sin "
            "escapar")


class TestLaVentanaDeDatosDAParaLoQueSeANUNCIA:
    """«El cambio de 1 año no me sale.»

    Y no salía nunca, en los catorce de la parrilla y en cada industria, sin
    que nada lo dijera: se bajaba UN AÑO de calendario —unas 251 sesiones— y el
    cambio a «1A» compara contra el cierre de hace 252, así que necesita 253
    cierres. Faltaban tres. La casilla se pintaba «—» y eso se lee como «este
    ETF no tiene un año de historia», que era mentira.

    Es un fallo de aritmética entre dos sitios que no se hablan: la ventana que
    se pide y la ventana que se calcula. Este test los pone a hablar.
    """

    def test_se_baja_MAS_historia_de_la_que_pide_la_ventana_mas_larga(self):
        import vertex_api as V
        from wbj.sectores import VENTANAS_CAMBIO

        dias = V._PERIODO_DIAS[V._SECTORES_PERIODO]
        # 252 sesiones por cada 365 días naturales: es la cuenta de la bolsa
        # estadounidense, festivos incluidos.
        sesiones = int(dias * 252 / 365)
        mas_larga = max(n for _, n in VENTANAS_CAMBIO)
        assert sesiones >= mas_larga + 1, (
            f"se bajan ~{sesiones} sesiones y la ventana más larga ({mas_larga}) "
            f"necesita {mas_larga + 1} cierres: esa casilla saldrá vacía SIEMPRE")

    def test_y_tambien_para_la_media_de_200(self):
        import vertex_api as V
        from wbj.sectores import SMA_LARGA

        sesiones = int(V._PERIODO_DIAS[V._SECTORES_PERIODO] * 252 / 365)
        assert sesiones >= SMA_LARGA, (
            f"~{sesiones} sesiones no dan para una media de {SMA_LARGA}")

    def test_con_la_historia_que_se_baja_TODAS_las_ventanas_salen(self):
        """La prueba directa: se arma una serie del tamaño real y se comprueba
        que ninguna de las seis se queda en blanco."""
        import vertex_api as V
        from wbj.sectores import cambios_por_ventana, sma

        sesiones = int(V._PERIODO_DIAS[V._SECTORES_PERIODO] * 252 / 365)
        serie = [100.0 + i * 0.1 for i in range(sesiones)]
        c = cambios_por_ventana(serie)
        vacias = [k for k, v in c.items() if v is None]
        assert not vacias, f"estas ventanas salen vacías con la historia real: {vacias}"
        assert sma(serie) is not None

    def test_un_año_justo_NO_habria_bastado(self):
        """El caso que falló, congelado: si alguien vuelve a poner «1y», este
        test explica por qué no vale antes de que llegue a producción."""
        from wbj.sectores import cambios_por_ventana

        un_año = [100.0 + i * 0.1 for i in range(251)]
        assert cambios_por_ventana(un_año)["1A"] is None


class TestLasEmpresasDeUnaIndustriaESTANESCRITAS:
    """«Cuando entro a cada industria no me sale ninguna de las acciones.»

    Y no era un fallo del panel: las posiciones de un ETF se pedían a FMP y con
    este plan contesta **HTTP 402 · HTTP 403** —402 en el endpoint nuevo, que es
    de pago, y 403 en el de siempre, que es «exclusive endpoint»—. Los dos
    caminos cerrados, así que la lista no llegaba nunca.

    La ruta que los pedía se quitó: mantener un camino que estructuralmente no
    puede funcionar es una trampa para el siguiente que lo mire. Las empresas
    están escritas en el panel y solo se extraen sus números, igual que las
    industrias y que la parrilla.
    """

    @staticmethod
    def _tabla(nombre):
        import re

        h = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        bruto = h.split(f"const {nombre} = {{", 1)[1].split("\n};", 1)[0]
        salida = {}
        for linea in bruto.splitlines():
            m = re.match(r"\s*([A-Z]+):\s*\[(.*)\],\s*$", linea)
            if m:
                salida[m.group(1)] = re.findall(r"\['([^']+)',\s*'([^']*)'\]",
                                                m.group(2))
        return salida

    def test_cada_industria_de_cada_sector_tiene_sus_empresas(self):
        industrias = self._tabla("VX_INDUSTRIAS")
        acciones = self._tabla("VX_ACCIONES")
        vacias = [s for s, v in industrias.items() if not v]
        assert not vacias, f"sectores sin ni una industria: {vacias}"
        faltan = sorted({t for v in industrias.values() for t, _ in v}
                        - set(acciones))
        assert not faltan, (
            f"industrias sin empresas escritas: {faltan} — entrar ahí deja la "
            "pantalla vacía, que es el fallo que esto arregla")

    def test_los_once_sectores_estan_y_son_los_del_motor(self):
        from wbj.sectores import SECTORES

        industrias = self._tabla("VX_INDUSTRIAS")
        assert set(industrias) == {t for t, _ in SECTORES}, (
            f"la tabla de industrias no cubre los once sectores: "
            f"{set(industrias) ^ {t for t, _ in SECTORES}}")

    def test_ninguna_lista_pasa_el_TOPE_de_la_ruta(self):
        """La ruta que las cotiza corta en 25 tickers. Una lista más larga
        perdería empresas EN SILENCIO."""
        import vertex_api as V

        acciones = self._tabla("VX_ACCIONES")
        largas = {e: len(v) for e, v in acciones.items()
                  if len(v) > V._SECTORES_MAX_PEDIDOS}
        assert not largas, f"listas por encima del tope: {largas}"

    def test_y_ninguna_repite_un_ticker(self):
        acciones = self._tabla("VX_ACCIONES")
        for etf, v in acciones.items():
            ts = [t for t, _ in v]
            assert len(ts) == len(set(ts)), f"{etf} repite un ticker"

    def test_cada_empresa_tiene_NOMBRE(self):
        """«NVDA, NVDA» no informa de nada.

        Pero hay empresas que SE LLAMAN como su ticker —AMD, Meta, PNC, AIG,
        MSCI, Okta— y exigir que todos difieran era una regla falsa: el primer
        intento de este caso salió en rojo por datos correctos. Lo que sí es un
        error es rellenar la columna con tickers, y eso se ve en la proporción,
        no en el caso suelto.
        """
        acciones = self._tabla("VX_ACCIONES")
        vacios = [(e, t) for e, v in acciones.items() for t, n in v if not n]
        assert not vacios, f"empresas sin nombre: {vacios[:8]}"

        todos = [(t, n) for v in acciones.values() for t, n in v]
        iguales = [x for x in todos if x[1].upper() == x[0]]
        assert len(iguales) < len(todos) * 0.2, (
            f"{len(iguales)} de {len(todos)} nombres son el propio ticker: "
            "la columna se rellenó con tickers en vez de con nombres")

    def test_la_ruta_que_pedia_las_posiciones_YA_NO_esta(self):
        """Estructuralmente no podía funcionar con este plan. Dejarla ahí es
        una trampa: el siguiente que la vea creerá que hay un camino."""
        import vertex_api as V

        assert not hasattr(V, "_etf_holdings")
        rutas = {r.path for r in V.app.routes if hasattr(r, "path")}
        assert "/api/sectores/acciones" not in rutas

    def test_y_NADIE_quedo_llamandola(self):
        """Quitar la ruta y dejar la llamada es peor que no quitarla: en vez de
        una lista vacía sale un 500 con `NameError` cuando alguien pide la
        lectura de una industria. Aquí llegó así, y se cazó leyendo el flujo.
        """
        import vertex_api as V

        fuente = Path(V.__file__).read_text(encoding="utf-8")
        assert "api_sectores_acciones(" not in fuente, (
            "queda una llamada a la ruta que ya no existe")

    def test_sin_lista_de_miembros_la_lectura_lo_DICE(self, client):
        """Y no revienta. Los miembros los manda el panel; si no llegan, la
        respuesta lo cuenta en vez de romperse por dentro."""
        r = client.get("/api/sectores/lectura?ambito=SMH")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is False
        assert "miembros" in (d.get("error") or "").lower(), d

    def test_el_panel_MANDA_las_empresas_al_pedir_la_lectura(self):
        """El tercer piso pregunta «¿qué pasa aquí dentro?» con SU lista. Antes
        mandaba la cadena vacía y el servidor tenía que deducirla — que es lo
        que no se puede hacer con este plan."""
        h = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cuerpo = h.split("async function cargaLecturaGrupo", 1)[1].split(
            "\n}", 1)[0]
        assert "VX_ACCIONES[VX_DONDE.industria]" in cuerpo, (
            "la lectura de una industria no manda sus empresas")
        assert "VX_INDUSTRIAS[VX_DONDE.sector]" in cuerpo, (
            "la lectura de un sector no manda sus industrias")


class TestElVolumenLLEGAConElJUICIOHECHO:
    """«¿Está respaldado por volumen?» lo decide el motor, no el panel.

    Si el panel comparara el relativo contra 1,20 por su cuenta, el umbral
    viviría en dos sitios: el día que se mueva en el motor, la pantalla seguiría
    diciendo lo de antes y nadie se enteraría — un número correcto con una
    etiqueta vieja al lado es peor que no tener etiqueta.
    """

    def test_la_fila_trae_medio_relativo_y_veredicto_por_ventana(self, monkeypatch):
        import vertex_api as V
        from wbj.sectores import VENTANAS_CAMBIO

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_quote_rapido_fmp",
                            lambda t: {"price": 100.0, "previousClose": 99.0})
        # 300 sesiones: da para la ventana de 1A y para la media de 200.
        barras = [(0, 1, 1, 1, 100.0 + i * 0.1, 1_000_000) for i in range(300)]
        monkeypatch.setattr(V, "_fmp_daily_bars", lambda t, p=None: barras)

        f = V._sector_fila("XLK")
        assert set(f["volumen"]) == {e for e, _ in VENTANAS_CAMBIO}, f["volumen"]
        for etiqueta, datos in f["volumen"].items():
            assert set(datos) >= {"medio", "relativo", "respalda"}, (
                f"{etiqueta} llega sin el veredicto hecho")

    def test_volumen_plano_da_relativo_uno_y_NO_respaldado(self, monkeypatch):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_quote_rapido_fmp", lambda t: {"price": 100.0})
        barras = [(0, 1, 1, 1, 100.0, 1_000_000) for _ in range(300)]
        monkeypatch.setattr(V, "_fmp_daily_bars", lambda t, p=None: barras)

        v = V._sector_fila("XLK")["volumen"]["1M"]
        assert v["relativo"] == 1.0
        assert v["respalda"] is False, "1,00x no es respaldo, es lo de siempre"

    def test_sin_volumen_el_veredicto_es_None_y_no_False(self, monkeypatch):
        """`False` afirmaría «este movimiento NO va respaldado», que es una
        afirmación sobre algo que no se ha mirado."""
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_quote_rapido_fmp", lambda t: {"price": 100.0})
        monkeypatch.setattr(V, "_fmp_daily_bars", lambda t, p=None: [])

        v = V._sector_fila("XLK")["volumen"]
        assert v == {} or all(x.get("respalda") is None for x in v.values()), v

    def test_el_umbral_del_panel_es_el_del_MOTOR(self):
        """Un solo umbral. El panel ni lo compara: pinta el veredicto."""
        from wbj.sectores import VOLUMEN_RESPALDA

        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cuerpo = html.split("function vxVolumenHTML", 1)[1].split("\n}", 1)[0]
        assert "v.respalda" in cuerpo, "el panel tiene que leer el veredicto"
        assert str(VOLUMEN_RESPALDA) not in cuerpo, (
            "el panel está comparando el umbral por su cuenta: son dos copias "
            "que se van a separar")

    def test_y_el_que_CITA_la_nota_tambien(self):
        """El método que la nota cita lleva el umbral dentro. Se importa del
        motor con un `except` detrás por si el path no está montado — y ese
        `except` es justo el sitio donde una copia vieja se quedaría callada.
        """
        import vertex_api as V
        from wbj.sectores import UMBRAL_VOLUMEN

        assert V._UMBRAL_VOLUMEN_TXT == f"{UMBRAL_VOLUMEN:.2f}", (
            "la nota cita un umbral que ya no es el del motor")

    def test_la_lectura_del_modelo_recibe_el_volumen_YA_JUZGADO(self):
        import vertex_api as V

        f = {"ticker": "XLK", "nombre": "Tecnología",
             "volumen": {"1M": {"medio": 5_000_000, "relativo": 1.45,
                                "respalda": True}}}
        frase = V._volumen_frase(f, "1M")
        assert "1.45x" in frase and "respaldado por volumen" in frase, frase
        assert V._volumen_frase(f, "1A") is None, (
            "sin dato no se escribe una línea diciendo que no hay dato: el "
            "modelo acabaría hablando de la falta de dato y no del mercado")

    def test_una_ventana_inventada_cae_a_una_de_verdad(self):
        """`?ventana=../` no puede dejar la lectura sin volumen en silencio."""
        import vertex_api as V
        from wbj.sectores import VENTANAS_CAMBIO

        assert V._ventana_valida("../etc") == VENTANAS_CAMBIO[0][0]
        assert V._ventana_valida("1m") == "1M", "y se acepta en minúsculas"
        assert V._ventana_valida("3M") == "3M"


class TestElRelojNoSeSIRVEDeCACHE:
    """Un precio de las 16:00 de ayer y uno de ahora se ven idénticos.

    El reloj es el único dato de esta pantalla que caduca solo: guardado con
    los datos, a las 16:01 seguiría diciendo «abierto» durante dos minutos —
    que es justo el minuto en que importa.
    """

    def test_la_respuesta_trae_el_estado_del_mercado(self, monkeypatch, client):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "_sectores_filas", lambda ts: [])
        monkeypatch.setattr(V, "_amplitud_interna_al_dia", lambda: {})
        d = client.get("/api/sectores").json()
        assert d.get("reloj"), d
        assert set(d["reloj"]) >= {"clave", "frase", "abierto"}

    def test_al_servir_de_CACHE_el_reloj_se_recalcula(self, monkeypatch, client):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "_sectores_filas", lambda ts: [])
        monkeypatch.setattr(V, "_amplitud_interna_al_dia", lambda: {})
        client.get("/api/sectores")

        llamadas = []
        real = V._reloj_ahora
        monkeypatch.setattr(V, "_reloj_ahora",
                            lambda: (llamadas.append(1), real())[1])
        d = client.get("/api/sectores").json()
        assert d["cacheado"] is True, "esta segunda tenía que venir de caché"
        assert llamadas, "el reloj vino congelado dentro de la foto guardada"

    def test_el_reloj_va_en_el_MOTOR_y_no_en_el_navegador(self):
        """Calcularlo en el cliente lo ataría al reloj del teléfono, que en un
        viaje diría que Wall Street abre a las tres de la madrugada."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        franja = html.split("function vxFranjaHTML", 1)[1].split("\n}", 1)[0]
        assert "d.reloj" in franja, "el panel tiene que leer el reloj del servidor"
        assert "America/New_York" not in franja, (
            "el panel está deduciendo la sesión por su cuenta")


class TestLaAmplitudInternaSEGUARDAyNOBLOQUEA:
    """~110 peticiones a FMP. Ni se piden en cada visita ni se esperan."""

    def test_se_sirve_lo_guardado_sin_esperar_al_calculo(self, monkeypatch):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        ayer = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        monkeypatch.setattr(V, "_amplitud_interna_guardada",
                            lambda: {"generado": ayer, "sectores": {"XLK": {"pct": 70.0}}})
        # Si esto llegara a ejecutarse en el hilo de la petición, el test se
        # colgaría: la prueba es que devuelve YA, con lo de ayer.
        monkeypatch.setattr(V, "_amplitud_interna_calcula",
                            lambda: pytest.fail("no puede calcularse en línea"))
        hilos = []
        monkeypatch.setattr(V.threading, "Thread",
                            lambda **kw: hilos.append(kw) or type(
                                "H", (), {"start": lambda s: None})())
        d = V._amplitud_interna_al_dia()
        assert d["sectores"]["XLK"]["pct"] == 70.0, "no sirvió lo de ayer"
        assert hilos, "y tenía que haber disparado el recálculo en segundo plano"

    def test_lo_fresco_NO_dispara_nada(self, monkeypatch):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_amplitud_interna_guardada", lambda: {
            "generado": datetime.now(timezone.utc).isoformat(), "sectores": {}})
        hilos = []
        monkeypatch.setattr(V.threading, "Thread",
                            lambda **kw: hilos.append(kw) or type(
                                "H", (), {"start": lambda s: None})())
        V._amplitud_interna_al_dia()
        assert not hilos, "recalculó algo que ya estaba al día"

    def test_un_sector_que_falla_no_tumba_a_los_otros_diez(self, monkeypatch):
        import vertex_api as V

        def _barras(t, p=None):
            if t == "XOM":            # el primer componente de XLE
                raise RuntimeError("se cayó FMP")
            return [(0, 1, 1, 1, 100.0 + i, 1) for i in range(80)]

        monkeypatch.setattr(V, "_fmp_daily_bars", _barras)
        d = V._amplitud_interna_calcula()
        assert len(d["sectores"]) == 11, d["sectores"].keys()
        assert d["sectores"]["XLE"]["pct"] is None
        assert d["sectores"]["XLE"]["motivo"], "un None sin motivo no se explica"
        assert d["sectores"]["XLK"]["pct"] == 100.0

    def test_vive_en_el_ALMACEN_y_no_en_memoria(self):
        """Render borra el disco en cada redeploy y duerme el servicio: en
        memoria, el dato se perdería justo cuando el usuario vuelve."""
        import vertex_api as V

        assert V._AMPLITUD_INTERNA_RUTA.startswith("Series/")
        fuente = inspect.getsource(V._amplitud_interna_al_dia)
        assert "almacen" in inspect.getsource(V._amplitud_interna_guardada)
        assert "_amplitud_interna_guardada" in fuente


class TestElMAPAyElRRGRecibenLoQueNecesitan:
    def test_los_pesos_y_las_estelas_viajan_con_la_rotacion(self, monkeypatch):
        import vertex_api as V
        from wbj.sectores import SECTORES

        serie = [100.0 * (1.001 ** i) for i in range(300)]
        filas = {t: {"ticker": t, "nombre": t, "precio": 100.0, "cambio_pct": 1.0,
                     "cierres": list(serie), "volumenes": [1_000_000] * 300}
                 for t in ("SPY", "RSP") + tuple(t for t, _ in SECTORES)}
        rot = V._sectores_rotacion(filas)
        assert rot["disponible"] is True, rot
        assert rot["estelas"], "sin estela el RRG solo puede pintar un punto"
        for t, e in rot["estelas"].items():
            assert len(e) >= 2, f"{t} con media estela: se leería como un giro"
            assert set(e[0]) >= {"fuerza", "impulso", "cuadrante"}

    def test_el_VIX_se_baja_pero_NO_cuenta_como_sector(self, monkeypatch, client):
        """Se lee al revés que todo lo demás: en la cuenta de «cuántos al alza»,
        un día de pánico saldría como un día de fuerza."""
        import vertex_api as V
        from wbj.sectores import EXTRAS, universo

        assert "^VIX" in universo()
        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "_amplitud_interna_al_dia", lambda: {})
        monkeypatch.setattr(V, "_sectores_filas", lambda ts: [
            {"ticker": t, "nombre": t, "precio": 10.0, "cambio_pct": 9.0,
             "rsi": 50.0, "sma200": None, "sma200_dist": None, "cambios": {},
             "volumen": {}, "cierres": [], "volumenes": []} for t in ts])
        d = client.get("/api/sectores").json()
        assert "^VIX" not in d["sectores"] and "^VIX" not in d["referencias"], (
            "el VIX no es una casilla del mapa")
        amp = d["amplitud"]["7D"]
        assert amp["n"] == 11, (
            f"la amplitud contó {amp['n']} y los sectores son once: se coló "
            "algo que no es un sector")
        assert "^VIX" not in amp["fuertes"]
        assert [t for t, _ in EXTRAS] == ["^VIX"]


class TestLaAmplitudINTERNASoloSePintaDondeEXISTE:
    """«Internos —» en SPY o dentro de una industria es un hueco permanente.

    Y un hueco se lee como «este dato falta», no como «esta pregunta no aplica
    aquí». La amplitud interna solo existe para los once sectores: en un índice
    o en un ETF de industria no hay tal cosa.
    """

    def test_el_panel_se_calla_fuera_de_los_once(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cuerpo = html.split("function vxInternaHTML", 1)[1].split("\n}", 1)[0]
        assert "sectores" in cuerpo and "includes(ticker)" in cuerpo, (
            "vxInternaHTML no comprueba que el ticker sea uno de los once: "
            "SPY, QQQ y cada industria van a enseñar «Internos —» para siempre")

    def test_pero_un_sector_SIN_dato_sigue_diciendolo(self):
        """Callarse ahí sí sería tapar un dato que debería estar."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cuerpo = html.split("function vxInternaHTML", 1)[1].split("\n}", 1)[0]
        assert "Internos —" in cuerpo, (
            "un sector cuyo cálculo falló tiene que decir que no hay número")


class TestLasCATORCECasillasNoSeSeparanDeLasDelMOTOR:
    """La parrilla está escrita en el panel, y el motor tiene su propia copia.

    La del panel existe para que el mapa aparezca ENTERO al entrar —tickers y
    nombres— y que solo los números lleguen después. La del motor no se puede
    quitar: de ella salen el universo que se baja, las categorías y la rotación.

    Así que son dos copias de verdad, y una copia sin vigilar es una copia que
    un día dice otra cosa. Este caso es esa vigilancia: si alguien añade un
    sector en un sitio y no en el otro, la parrilla enseñaría una casilla que
    nadie cotiza, o cotizaría una que nadie enseña.
    """

    @staticmethod
    def _del_panel():
        import json
        import re

        h = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        bruto = h.split("const VX_SECTORES = {", 1)[1].split("\n};", 1)[0]
        salida = {}
        for grupo in ("referencias", "sectores"):
            # Se corta por el cierre INDENTADO del grupo, no por el primer
            # «],»: ese es el final de la primera fila y dejaba la lista vacía.
            trozo = bruto.split(f"{grupo}: [", 1)[1].split("\n    ],", 1)[0]
            salida[grupo] = [
                (m.group(1), m.group(2))
                for m in re.finditer(r"\['([^']+)',\s*'([^']*)'\]", trozo)]
        return salida

    def test_los_tickers_y_los_nombres_son_LOS_MISMOS(self):
        from wbj.sectores import REFERENCIAS, SECTORES

        panel = self._del_panel()
        assert panel["referencias"] == list(REFERENCIAS), (
            f"las referencias del panel y las del motor se separaron:\n"
            f"  panel: {panel['referencias']}\n  motor: {list(REFERENCIAS)}")
        assert panel["sectores"] == list(SECTORES), (
            f"los sectores del panel y los del motor se separaron:\n"
            f"  panel: {panel['sectores']}\n  motor: {list(SECTORES)}")

    def test_y_el_orden_tambien(self):
        """El orden es el que se pinta. Barajado, las casillas cambian de sitio
        entre el esqueleto y los datos, y eso se ve como un salto."""
        from wbj.sectores import SECTORES

        panel = self._del_panel()
        assert [t for t, _ in panel["sectores"]] == [t for t, _ in SECTORES]

    def test_cada_nombre_del_panel_tiene_traduccion(self):
        """La parrilla se pinta desde el panel, así que sus nombres pasan por
        el diccionario como cualquier otra cadena."""
        import json

        h = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        dic = h.split("const VX_ES2EN = {", 1)[1].split("\n};", 1)[0]
        faltan = [n for _, n in self._del_panel()["sectores"]
                  if json.dumps(n, ensure_ascii=False) + ":" not in dic]
        assert not faltan, f"nombres de sector sin traducción: {faltan}"


class TestElMAPAsePintaANTESdeQueLleguenLosNUMEROS:
    """«Ya todo tiene que estar escrito porque solo necesitamos extraer los
    datos. Cuando entre me salga ya todo y carguen los precios.»

    Lo que no depende de la red —qué sectores hay y cómo se llaman— no puede
    esperar a la red.
    """

    def test_carga_pinta_el_esqueleto_ANTES_de_pedir_nada(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        import re

        cuerpo = html.split("async function cargaSectores", 1)[1].split("\n}", 1)[0]
        antes = cuerpo.index("pintaSectores(null)")
        pide = cuerpo.index("fetch(")
        assert antes < pide, (
            "se pide al servidor antes de pintar la parrilla: la pantalla se "
            "queda esperando a saber algo que no cambia nunca")
        # Sin comentarios: el porqué del cambio MENCIONA la pantalla de espera
        # que se quitó, y buscarla en el texto crudo cazaba la explicación en
        # vez del código.
        codigo = re.sub(r"^\s*//.*$", "", cuerpo, flags=re.M)
        assert "Leyendo el mercado" not in codigo, (
            "sigue la pantalla de espera que sustituía al mapa")

    def test_un_fallo_de_red_NO_borra_el_mapa(self):
        """Los tickers no dependen de la red: se quedan, y el motivo va encima.
        Antes el aviso reemplazaba la parrilla entera."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cuerpo = html.split("async function cargaSectores", 1)[1].split("\n}", 1)[0]
        assert "parrilla.innerHTML" not in cuerpo, (
            "un fallo vuelve a borrar la parrilla para poder decir el motivo")
        assert cuerpo.count("vxSectoresAviso(") == 2, (
            "los dos caminos de fallo tienen que avisar SIN borrar el mapa")


class TestLaVALUACIONDelQuickNoSaleCEROporUnFALLO:
    """«¿Por qué en el puntaje rápido el de valuation sale 0.0 o bien bajito?»

    La causa está medida en `engine/tests/test_quick_valuation.py`, que es
    donde vive la matemática. Aquí se mide lo que le toca a la capa web: que
    el motivo llegue a la pantalla EN EL IDIOMA DE LA SESIÓN.

    Sin eso, el motivo nace en inglés dentro de un panel en español — y el
    barrido no lo salva: traduce ES→EN, no al revés, para el texto que nace
    en el servidor.
    """

    def test_el_quick_recibe_el_IDIOMA_de_la_sesion(self):
        import vertex_api as V

        fuente = Path(V.__file__).read_text(encoding="utf-8")
        for llamada in ("quick_scorecard(dict_packet, _idioma_actual())",
                        "_qsc(dict_packet, _idioma_actual())"):
            assert llamada in fuente, f"falta el idioma en: {llamada}"

    def test_y_el_motivo_sale_TRADUCIDO_de_verdad(self):
        """No basta con pasar el parámetro: se comprueba el texto que sale."""
        from wbj.quick import quick_scorecard

        def s(v, ends=("2024-12-31",)):
            return [{"end": e, "val": v} for e in ends]

        # Pierde dinero y quema caja: la valuación no se puede calcular.
        pk = {"ticker": "TEST", "as_of": "2026-08-15", "annual": {
                "revenue": s(1e11), "net_income": s(-1e10),
                "operating_cash_flow": s(5e9), "capex": s(2e10),
                "long_term_debt": s(1e10), "equity": s(5e10),
                "operating_income": s(3e10), "gross_profit": s(5e10),
                "interest_expense": s(1e9), "diluted_shares": s(1e10)},
              "market_data": {"price": 230.0, "market_cap": 3.5e12},
              "industry_adapter": "default_nonfinancial"}

        def motivo(lang):
            return next(r for r in quick_scorecard(pk, lang)["categories"]
                        if r["key"] == "valuation")["reason"]

        assert "múltiplo" in motivo("es"), motivo("es")
        assert "multiple" in motivo("en"), motivo("en")
        assert motivo("es") != motivo("en")

    def test_y_NO_dice_que_falta_el_precio_cuando_el_precio_ESTA(self):
        """El motivo que llegaba al panel era siempre el mismo, «sin precio de
        mercado (FMP)», incluso con el precio en pantalla. Un motivo falso
        manda a mirar donde no es."""
        from wbj.quick import quick_scorecard

        def s(v):
            return [{"end": "2024-12-31", "val": v}]

        pk = {"ticker": "TEST", "as_of": "2026-08-15", "annual": {
                "revenue": s(1e11), "net_income": s(-1e10),
                "operating_cash_flow": s(5e9), "capex": s(2e10),
                "long_term_debt": s(1e10), "equity": s(5e10),
                "operating_income": s(3e10), "gross_profit": s(5e10),
                "interest_expense": s(1e9), "diluted_shares": s(1e10)},
              "market_data": {"price": 230.0, "market_cap": 3.5e12},
              "industry_adapter": "default_nonfinancial"}
        fila = next(r for r in quick_scorecard(pk, "es")["categories"]
                    if r["key"] == "valuation")
        assert "precio" not in fila["reason"], fila["reason"]


class TestLaPARRILLASeVeAlINSTANTE:
    """«Cuando entro al panel se tarda en que se vean los precios.»

    Y el motivo era aritmético: una fila cuesta DOS peticiones a FMP —la
    cotización y quince meses de velas—, así que la parrilla son **28** con
    seis en paralelo. La caché duraba 120 s y vivía solo en memoria, de modo
    que entrar tras dos minutos parado —o después de cualquier redeploy, que
    en Render borra el disco— pagaba las veintiocho con la pantalla delante.

    Ahora es el mismo trato que la amplitud interna ya tenía: si hay foto se
    sirve YA y el refresco va por detrás. Solo se espera cuando no hay
    absolutamente nada que enseñar.
    """

    @staticmethod
    def _sin_red(monkeypatch, veces):
        """`_sectores_calcula` contado, para saber si la petición esperó."""
        import vertex_api as V

        def _finge(pedidos):
            veces.append(1)
            return {"ok": True, "filas": [{"ticker": "SPY"}], "motivo": None,
                    "generado": datetime.now(timezone.utc).isoformat()}

        monkeypatch.setattr(V, "_sectores_calcula", _finge)

    def test_una_foto_VIEJA_se_sirve_sin_esperar(self, monkeypatch, client):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "_SECTORES_REFRESCANDO", set())
        monkeypatch.setattr(V, "_sectores_del_almacen", lambda: None)
        monkeypatch.setattr(V, "_sectores_apunta", lambda *a: None)
        veces = []
        self._sin_red(monkeypatch, veces)
        # Una foto de hace media hora, ya guardada.
        vieja = {"ok": True, "filas": [{"ticker": "SPY", "precio": 1.0}],
                 "generado": "2026-08-15T10:00:00+00:00"}
        V._SECTORES_CACHE["_parrilla"] = (time.time() - 1800, vieja)

        lanzados = []
        monkeypatch.setattr(V, "_sectores_refresca_en_fondo",
                            lambda c, p: lanzados.append(c))
        d = client.get("/api/sectores").json()

        assert d["filas"] == vieja["filas"], "no se sirvió la foto que había"
        assert not veces, "la petición se puso a bajar en vez de servir lo que tenía"
        assert lanzados == ["_parrilla"], "no se disparó el refresco de fondo"

    def test_y_NO_miente_sobre_su_edad(self, monkeypatch, client):
        """Servir rápido no puede ser servir engañando: la foto viaja con su
        `generado`, que es de cuando se bajó, y la franja lo pinta."""
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "_SECTORES_REFRESCANDO", set())
        monkeypatch.setattr(V, "_sectores_del_almacen", lambda: None)
        monkeypatch.setattr(V, "_sectores_refresca_en_fondo", lambda c, p: None)
        vieja = {"ok": True, "filas": [{"ticker": "SPY"}],
                 "generado": "2026-08-15T10:00:00+00:00"}
        V._SECTORES_CACHE["_parrilla"] = (time.time() - 1800, vieja)
        d = client.get("/api/sectores").json()
        assert d["generado"] == "2026-08-15T10:00:00+00:00"
        assert d.get("refrescando") is True, "no se dice que se está refrescando"
        # El reloj SÍ es de ahora: es lo único que no puede venir congelado.
        assert d["reloj"]["frase"]

    def test_el_refresco_de_fondo_RECALCULA_de_verdad(self, monkeypatch):
        """Aquí llegó un fallo real: el hilo llamaba a `api_sectores`, que se
        encontraba su propia foto vieja, decidía servirla y volvía sin bajar
        nada. Un refresco que no refrescaba deja la pantalla congelada para
        siempre, y por fuera se ve igual que si funcionara."""
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "_SECTORES_REFRESCANDO", set())
        veces, guardadas = [], []
        self._sin_red(monkeypatch, veces)
        monkeypatch.setattr(V, "_sectores_apunta",
                            lambda c, t, s: guardadas.append(c))
        V._SECTORES_CACHE["_parrilla"] = (
            time.time() - 1800, {"ok": True, "filas": [], "generado": ""})

        V._sectores_refresca_en_fondo("_parrilla", [])
        for _ in range(100):                      # se espera al hilo, no al reloj
            if guardadas:
                break
            time.sleep(0.02)
        assert veces, "el refresco de fondo no bajó nada"
        assert guardadas == ["_parrilla"], "bajó pero no guardó la foto nueva"

    def test_dos_visitas_seguidas_NO_lanzan_dos_refrescos(self, monkeypatch):
        """Sin el cerrojo, diez visitas a una foto vieja serían diez tandas de
        veintiocho peticiones a la vez."""
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "_SECTORES_REFRESCANDO", {"_parrilla"})
        veces = []
        self._sin_red(monkeypatch, veces)
        V._sectores_refresca_en_fondo("_parrilla", [])
        time.sleep(0.1)
        assert not veces, "se lanzó un segundo refresco con uno ya en vuelo"

    def test_la_foto_SOBREVIVE_al_redeploy(self, monkeypatch):
        """En Render el disco se borra en cada despliegue y en cada despertar.
        Una foto que solo vive en memoria no existe después de eso, y la
        primera visita volvía a pagar las veintiocho."""
        import vertex_api as V

        assert V._SECTORES_RUTA.startswith("Series/"), (
            "la foto tiene que vivir en el almacén, que es lo que sobrevive")
        guardado = {}
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})

        class _Alm:
            def guarda(self, ruta, datos):
                guardado[ruta] = datos

            def lee_json(self, ruta):
                return guardado.get(ruta)

        import vertex_almacen as VA
        monkeypatch.setattr(VA, "almacen", _Alm())
        foto = {"ok": True, "filas": [{"ticker": "SPY"}],
                "generado": "2026-08-15T10:00:00+00:00"}
        V._sectores_apunta("_parrilla", time.time(), foto)
        assert V._SECTORES_RUTA in guardado, "no se guardó en el almacén"

        # Y se recupera con SU fecha, no con la de ahora: fingirla fresca la
        # dejaría sin refrescar dos minutos más y mentiría sobre su edad.
        ts, datos = V._sectores_del_almacen()
        assert datos["filas"] == foto["filas"]
        assert abs(ts - datetime.fromisoformat(
            foto["generado"]).timestamp()) < 1.0

    def test_un_ticker_suelto_NO_se_guarda_en_el_almacen(self, monkeypatch):
        """Solo la parrilla, que es la pantalla de entrada. Guardar cada
        industria y cada acción llenaría la rama de datos de fotos que nadie
        va a volver a mirar."""
        import vertex_api as V

        guardado = {}

        class _Alm:
            def guarda(self, ruta, datos):
                guardado[ruta] = datos

        import vertex_almacen as VA
        monkeypatch.setattr(VA, "almacen", _Alm())
        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        V._sectores_apunta("SMH,IGV", time.time(), {"ok": True, "filas": []})
        assert not guardado

    def test_al_arrancar_se_PRECALIENTA(self):
        """La primera visita después de un despliegue tampoco puede esperar."""
        import vertex_api as V

        fuente = Path(V.__file__).read_text(encoding="utf-8")
        arranque = fuente.split("async def _vertex_lifespan", 1)[1].split(
            "\n    yield", 1)[0]
        assert "_precalienta_parrilla" in arranque
        assert "daemon=True" in arranque, (
            "el precalentamiento no puede bloquear el arranque")


class TestElPRECIOSeMueveSOLOElPrecio:
    """«Quiero que los precios cambien en tiempo real.»

    Y solo el precio, que además de ser lo pedido es lo correcto: de lo que el
    panel enseña de cada ticker —precio, RSI, media de 200 y volumen—, los
    tres últimos son DIARIOS. Salen de barras diarias y no se mueven hasta el
    cierre, así que volver a pedirlos cada quince segundos sería bajar quince
    meses de historia para recibir el número que ya está puesto.

    No es streaming: este plan de FMP no lo tiene. Es un sondeo corto.
    """

    def test_la_ruta_devuelve_PRECIOS_y_nada_mas(self, monkeypatch, client):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_VIVO_CACHE", {})
        monkeypatch.setattr(V, "_quotes_lote", lambda ts: {
            "XLK": {"symbol": "XLK", "price": 250.5, "previousClose": 248.0},
            "XLF": {"symbol": "XLF", "price": 48.25, "previousClose": 48.0}})
        d = client.get("/api/sectores/vivo?tickers=XLK,XLF").json()
        assert d["ok"] is True
        assert d["precios"] == {"XLK": 250.5, "XLF": 48.25}, d["precios"]
        # Un número por ticker, no un diccionario con RSI y compañía dentro:
        # lo que no se mueve no viaja.
        assert all(isinstance(v, (int, float)) for v in d["precios"].values())

    def test_un_precio_IMPOSIBLE_no_se_pinta(self, monkeypatch, client):
        """Cero o negativo no es un precio barato: es un dato roto, y pintarlo
        borraría el bueno que ya está en pantalla."""
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_VIVO_CACHE", {})
        monkeypatch.setattr(V, "_quotes_lote", lambda ts: {
            "XLK": {"symbol": "XLK", "price": 0},
            "XLF": {"symbol": "XLF", "price": -3.0},
            "XLE": {"symbol": "XLE", "price": None},
            "XLV": {"symbol": "XLV", "price": 170.0}})
        d = client.get("/api/sectores/vivo?tickers=XLK,XLF,XLE,XLV").json()
        assert d["precios"] == {"XLV": 170.0}

    def test_el_lote_se_COMPLETA_uno_a_uno_si_el_proveedor_no_los_trae(
            self, monkeypatch):
        """FMP acepta la lista por comas, pero no en todos los planes ni
        devolviendo siempre lo que se pide. Sin la red de abajo, un plan que no
        soporte el lote dejaría la pantalla congelada sin decir por qué — y no
        se puede comprobar desde aquí, que es justo el motivo de no fiarse."""
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "requests", _RequestsFalso({"XLK": 250.0}))
        sueltos = []

        def _uno(tk):
            sueltos.append(tk)
            return {"symbol": tk, "price": 99.0}

        monkeypatch.setattr(V, "_quote_rapido_fmp", _uno)
        d = V._quotes_lote(["XLK", "XLF", "XLE"])
        assert sorted(sueltos) == ["XLE", "XLF"], (
            "no se pidieron uno a uno los que el lote no trajo")
        assert set(d) == {"XLK", "XLF", "XLE"}

    def test_se_cachea_para_que_DIEZ_pestañas_no_sean_diez_sondeos(
            self, monkeypatch, client):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_VIVO_CACHE", {})
        veces = []
        monkeypatch.setattr(V, "_quotes_lote", lambda ts: (
            veces.append(1), {"XLK": {"symbol": "XLK", "price": 1.0}})[1])
        client.get("/api/sectores/vivo?tickers=XLK")
        d = client.get("/api/sectores/vivo?tickers=XLK").json()
        assert len(veces) == 1, "la segunda petición volvió a salir a la red"
        assert d["cacheado"] is True

    def test_hay_TOPE_de_tickers(self, monkeypatch, client):
        """La pantalla más grande son catorce casillas. Un número mayor solo
        puede venir de alguien probando qué pasa."""
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V, "_VIVO_CACHE", {})
        pedidos = []
        monkeypatch.setattr(V, "_quotes_lote",
                            lambda ts: (pedidos.extend(ts), {})[1])
        muchos = ",".join(f"AA{i}" for i in range(90))
        client.get(f"/api/sectores/vivo?tickers={muchos}")
        assert len(pedidos) <= V._VIVO_MAX

    def test_sin_clave_lo_DICE_en_vez_de_callarse(self, monkeypatch, client):
        import vertex_api as V

        monkeypatch.delenv("FMP_API_KEY", raising=False)
        monkeypatch.setattr(V, "_VIVO_CACHE", {})
        d = client.get("/api/sectores/vivo?tickers=XLK").json()
        assert d["ok"] is False and "FMP_API_KEY" in d["error"]

    def test_el_panel_SONDEA_con_los_tres_frenos(self):
        """Un latido sin frenos se come la cuota: solo lo que se ve, solo con
        el mercado vivo y solo con la pestaña delante."""
        h = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cuerpo = h.split("async function vxVivoLatido", 1)[1].split("\n}", 1)[0]
        assert "visibilityState" in cuerpo, "sondea con la pestaña de fondo"
        assert "vxVivoMercadoVivo()" in cuerpo, "sondea con la bolsa cerrada"
        assert "vxVivoTickers()" in cuerpo, "no se limita a lo que está en pantalla"

    def test_el_latido_se_PARA_al_salir_del_panel(self):
        h = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cuerpo = h.split("function switchView(viewId)", 1)[1].split("\n}", 1)[0]
        assert "vxVivoArranca()" in cuerpo and "vxVivoPara()" in cuerpo, (
            "el latido no se enciende y apaga con la pantalla")

    def test_NO_se_repinta_la_parrilla_entera_para_cambiar_un_numero(self):
        """Repintar cerraría la rotación abierta, movería el foco y haría
        parpadear catorce casillas para cambiar un número."""
        h = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        cuerpo = h.split("function vxVivoPinta", 1)[1].split("\n}\n", 1)[0]
        for prohibido in ("pintaParrilla", "innerHTML", "cargaSectores"):
            assert prohibido not in cuerpo, f"el latido llama a {prohibido}"
        assert "textContent" in cuerpo, "tiene que tocar el nodo del precio"


class _RequestsFalso:
    """Un `requests` de mentira que solo contesta el lote que se le diga."""

    def __init__(self, precios):
        self._precios = precios

    def get(self, url, params=None, timeout=None):
        precios = self._precios

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return [{"symbol": k, "price": v} for k, v in precios.items()]

        return _R()
