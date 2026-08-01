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
