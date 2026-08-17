"""La watchlist de Víctor y sus doce componentes, dentro de Vertex.

Cubre lo que el diferencial NO puede cubrir: `diff_watchlist.sh` mide que las
19 funciones puras dan lo mismo que su `.ts`; esto mide el CABLEADO — que las
rutas existen, que el buzón persiste, que el payload trae lo que los
componentes leen, y que la watchlist de Vertex quedó fuera de verdad.

    python -m pytest tests_vertex/test_watchlist_y_componentes.py -q
"""

from __future__ import annotations

import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

fastapi = pytest.importorskip("fastapi", reason="requiere las deps de vertex_api")

NOW = datetime.now(timezone.utc)
SPOT = 100.0
HTML = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")


API = (ROOT / "vertex_api.py").read_text(encoding="utf-8")


def _sin_comentarios(js: str) -> str:
    """Quita comentarios de JS. Regla del proyecto: un test mide lo que el
    código HACE, no lo que un comentario dice — si no, documentar un arreglo
    hace fallar el test que lo comprueba. Pasó ya dos veces."""
    js = re.sub(r"/\*[\s\S]*?\*/", "", js)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    import vertex_api as V

    return TestClient(V.app)


@pytest.fixture(autouse=True)
def disco(monkeypatch, tmp_path):
    """Cada test estrena buzón: si compartieran archivo, el orden mandaría."""
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))
    monkeypatch.setenv("MASSIVE_API_KEY", "x" * 32)


CONTRATO = {"symbol": "WULF270115C00020000", "ticker": "wulf", "type": "call",
            "strike": 20.0, "expiration": "2027-01-15"}


class TestElBuzonDeLaWatchlist:
    """`/api/tito-watchlist` — su `/api/watchlist`, GET/POST/DELETE."""

    def test_las_tres_rutas_existen(self):
        import vertex_api as V

        metodos = {m for r in V.app.routes
                   if getattr(r, "path", "") == "/api/tito-watchlist"
                   for m in (getattr(r, "methods", None) or ())}
        assert {"GET", "POST", "DELETE"} <= metodos, metodos

    def test_get_vacio_no_revienta(self, client):
        d = client.get("/api/tito-watchlist").json()
        assert d["pending"] == [] and d["failed"] == []
        assert d["broker"] == "robinhood"          # su default
        assert d["granularity"] == "contracts"

    def test_el_selector_de_brokers_sale_del_motor(self, client):
        """Añadir un broker es tocar `watchlist.py`, no el HTML."""
        ids = [b["id"] for b in client.get("/api/tito-watchlist").json()["brokers"]]
        assert ids == ["robinhood", "schwab", "fidelity", "tastytrade",
                       "webull", "ibkr", "none"]

    def test_la_plantilla_del_enlace_lleva_hueco_no_un_ticker(self, client):
        b = {x["id"]: x for x in client.get("/api/tito-watchlist").json()["brokers"]}
        assert "__T__" in b["robinhood"]["quoteUrl"]
        # Los `copy` NO tienen enlace: la UI enseña "copiar" en vez de un
        # enlace roto. Es su contrato y aquí se comprueba.
        assert b["webull"]["quoteUrl"] is None
        assert b["ibkr"]["quoteUrl"] is None
        assert b["none"]["quoteUrl"] is None

    def test_encolar_y_persistir(self, client):
        d = client.post("/api/tito-watchlist",
                        json={"broker": "robinhood", "contract": CONTRATO}).json()
        assert [x["symbol"] for x in d["pending"]] == ["WULF270115C00020000"]
        assert d["pending"][0]["ticker"] == "WULF"       # mayúsculas suyas
        # Y sigue ahí en la siguiente petición: el buzón vive en disco.
        assert client.get("/api/tito-watchlist").json()["pending"] == d["pending"]

    def test_encolar_dos_veces_no_duplica(self, client):
        for _ in range(3):
            d = client.post("/api/tito-watchlist",
                            json={"broker": "robinhood", "contract": CONTRATO}).json()
        assert len(d["pending"]) == 1

    def test_la_cola_trae_la_CONSULTA_con_la_que_el_agente_resuelve(self, client):
        """El strike va a CUATRO decimales: "20" no casa con "20.0000" en el
        broker y la búsqueda vuelve vacía sin decir por qué."""
        client.post("/api/tito-watchlist",
                    json={"broker": "robinhood", "contract": CONTRATO})
        q = client.get("/api/tito-watchlist").json()["pending"][0]["query"]
        assert q == {"chain_symbol": "WULF", "type": "call",
                     "strike_price": "20.0000", "expiration_dates": "2027-01-15"}

    def test_la_etiqueta_es_legible(self, client):
        client.post("/api/tito-watchlist",
                    json={"broker": "robinhood", "contract": CONTRATO})
        assert client.get("/api/tito-watchlist").json()["pending"][0]["label"] \
            == "WULF $20 CALL 2027-01-15"

    def test_sin_strike_se_rechaza_EN_LA_PUERTA(self, client):
        """Aceptarlo solo aplaza el fallo hasta el drenador, donde ya no hay a
        quién avisar. Es su regla, con su código y su motivo."""
        r = client.post("/api/tito-watchlist", json={
            "broker": "robinhood", "contract": dict(CONTRATO, strike=None)})
        assert r.status_code == 400
        assert "strike" in r.json()["error"].lower()
        assert client.get("/api/tito-watchlist").json()["pending"] == []

    def test_sin_vencimiento_tambien(self, client):
        r = client.post("/api/tito-watchlist", json={
            "broker": "robinhood", "contract": dict(CONTRATO, expiration=None)})
        assert r.status_code == 400

    def test_un_broker_de_subyacentes_SI_acepta_lo_incompleto(self, client):
        """Con `underlying_only` solo viaja el ticker, así que el strike no
        hace falta — y ahí sí se encola."""
        d = client.post("/api/tito-watchlist", json={
            "broker": "schwab", "contract": dict(CONTRATO, strike=None)}).json()
        assert len(d["pending"]) == 1
        # Y viaja SOLO el ticker: ni tipo, ni strike, ni vencimiento.
        assert "symbol" not in d["pending"][0]
        assert d["pending"][0]["ticker"] == "WULF"

    def test_un_broker_desconocido_se_rechaza(self, client):
        r = client.post("/api/tito-watchlist",
                        json={"broker": "mi-broker", "contract": CONTRATO})
        assert r.status_code == 400

    def test_cuerpo_invalido_no_es_un_500(self, client):
        r = client.post("/api/tito-watchlist", content=b"{no json")
        assert r.status_code == 400

    def test_contrato_malformado_se_rechaza(self, client):
        for malo in ({}, {"symbol": 1, "ticker": "X", "type": "call"},
                     {"symbol": "S", "ticker": "X", "type": "spread"},
                     "una cadena", None):
            r = client.post("/api/tito-watchlist",
                            json={"broker": "robinhood", "contract": malo})
            assert r.status_code == 400, malo

    def test_el_agente_confirma_lo_sincronizado(self, client):
        client.post("/api/tito-watchlist",
                    json={"broker": "robinhood", "contract": CONTRATO})
        d = client.post("/api/tito-watchlist", json={
            "broker": "robinhood", "synced": ["WULF270115C00020000"]}).json()
        assert d["pending"] == []
        assert d["lastSyncedAt"]

    def test_lo_sincronizado_no_se_reencola(self, client):
        client.post("/api/tito-watchlist",
                    json={"broker": "robinhood", "contract": CONTRATO})
        client.post("/api/tito-watchlist",
                    json={"broker": "robinhood", "synced": ["WULF270115C00020000"]})
        d = client.post("/api/tito-watchlist",
                        json={"broker": "robinhood", "contract": CONTRATO}).json()
        assert d["pending"] == [], "marcar dos veces lo mismo no genera trabajo"

    def test_lo_irresoluble_se_aparca_y_NO_se_reintenta(self, client):
        """Sin esto el drenador reintentaría lo mismo cada 15 minutos para
        siempre. Es su comentario y su diseño."""
        client.post("/api/tito-watchlist",
                    json={"broker": "robinhood", "contract": CONTRATO})
        d = client.post("/api/tito-watchlist", json={
            "broker": "robinhood", "failed": ["WULF270115C00020000"],
            "reason": "el broker no encuentra el instrumento"}).json()
        assert d["pending"] == []
        assert d["failed"][0]["failReason"] == "el broker no encuentra el instrumento"

    def test_el_motivo_del_fallo_tiene_respaldo(self, client):
        client.post("/api/tito-watchlist",
                    json={"broker": "robinhood", "contract": CONTRATO})
        d = client.post("/api/tito-watchlist", json={
            "broker": "robinhood", "failed": ["WULF270115C00020000"],
            "reason": "   "}).json()
        assert d["failed"][0]["failReason"], "un motivo en blanco no puede quedar vacío"

    def test_desencolar_por_simbolo(self, client):
        client.post("/api/tito-watchlist",
                    json={"broker": "robinhood", "contract": CONTRATO})
        d = client.delete("/api/tito-watchlist"
                          "?symbol=WULF270115C00020000&ticker=WULF").json()
        assert d["pending"] == []

    def test_desencolar_alcanza_A_LAS_FILAS_VIEJAS_de_solo_tickers(self, client):
        """El bug real: `"WULF270115C00020000" != "WULF"` dejaba las filas
        legado imborrables para siempre. Pasó con SPXW y SPY."""
        client.post("/api/tito-watchlist",
                    json={"broker": "schwab", "contract": CONTRATO})   # solo ticker
        assert len(client.get("/api/tito-watchlist?broker=schwab").json()["pending"]) == 1
        d = client.delete("/api/tito-watchlist"
                          "?symbol=WULF270115C00020000&ticker=WULF&broker=schwab").json()
        assert d["pending"] == [], "el ticker es lo único que alcanza a la fila legado"

    def test_desencolar_por_simbolo_NO_arrastra_otros_strikes(self, client):
        otro = dict(CONTRATO, symbol="WULF270115C00025000", strike=25.0)
        client.post("/api/tito-watchlist", json={"broker": "robinhood", "contract": CONTRATO})
        client.post("/api/tito-watchlist", json={"broker": "robinhood", "contract": otro})
        d = client.delete("/api/tito-watchlist"
                          "?symbol=WULF270115C00020000&ticker=WULF").json()
        assert [x["symbol"] for x in d["pending"]] == ["WULF270115C00025000"], \
            "con granularidad `contracts` dos strikes son dos trabajos distintos"

    def test_desencolar_sin_nada_es_400(self, client):
        assert client.delete("/api/tito-watchlist").status_code == 400

    def test_la_cola_de_un_broker_no_toca_la_de_otro(self, client):
        client.post("/api/tito-watchlist", json={"broker": "robinhood", "contract": CONTRATO})
        client.post("/api/tito-watchlist", json={"broker": "schwab", "contract": CONTRATO})
        client.delete("/api/tito-watchlist?symbol=WULF270115C00020000&ticker=WULF")
        assert len(client.get("/api/tito-watchlist?broker=schwab").json()["pending"]) == 1

    def test_lo_privado_NUNCA_cruza_el_puente(self, client):
        """Los griegos, tu sizing y tu saldo se quedan en el navegador. Es la
        promesa que hace la propia tarjeta en pantalla."""
        client.post("/api/tito-watchlist", json={"broker": "robinhood", "contract": dict(
            CONTRATO, entrySpot=18.42, maxContracts=3, accountSizeAtEntry=1000,
            tolerancePctAtEntry=15, entryThetaPctDaily=-0.9)})
        fila = client.get("/api/tito-watchlist").json()["pending"][0]
        for prohibido in ("entrySpot", "maxContracts", "accountSizeAtEntry",
                          "tolerancePctAtEntry", "entryThetaPctDaily", "binding"):
            assert prohibido not in fila, prohibido

    def test_el_archivo_del_disco_no_inventa_campos(self, client, tmp_path):
        """Una fila de solo-tickers NO debe salir con `symbol: null`: quien lea
        ese archivo con su código vería `"symbol" in item` cierto y perdería la
        distinción de `removeFromOutbox`."""
        import json

        client.post("/api/tito-watchlist",
                    json={"broker": "schwab", "contract": CONTRATO})
        crudo = json.loads((tmp_path / "outbox.json").read_text())
        assert "symbol" not in crudo["items"][0]
        assert "failedAt" not in crudo["items"][0]


class TestLaWatchlistDeVertexSeFue:
    """«Del watchlist de vertex elimínalo» — y de verdad, no escondido."""

    def test_no_queda_vista_ni_boton(self):
        assert "watchlistView" not in HTML
        assert "wlGet" not in HTML
        assert "vertex_watchlist_v1" not in HTML

    def test_no_queda_su_ruta_de_cotizacion(self):
        import vertex_api as V

        assert "/api/watchlist-quote" not in {r.path for r in V.app.routes}

    def test_la_campana_sigue_viva_con_la_watchlist_NUEVA(self):
        """El radar y el escaneo NO se borraron: son la campana, que ahora
        vigila los subyacentes de la watchlist de contratos."""
        import vertex_api as V

        rutas = {r.path for r in V.app.routes}
        assert "/api/alerts/scan" in rutas
        assert "/api/watchlist-radar" in rutas
        assert "wlUnderlyings" in HTML
        assert re.search(r"const tks = \(typeof wlUnderlyings", HTML)

    def test_el_toast_no_lleva_a_una_vista_que_ya_no_existe(self):
        assert "switchView('watchlistView')" not in HTML


class TestElPayloadTraeLoQueSusComponentesLeen:
    """Un componente puede existir, estar llamado y pintar vacío porque el
    payload no trae su clave. Esto lo mide en el servidor."""

    @pytest.fixture(autouse=True)
    def fuentes(self, monkeypatch):
        import wbj.tito.marketsnack as MS
        import wbj.tito.massive as MASS
        from wbj.tito.levels import LvlBar
        from wbj.tito.marketsnack import FlowResult
        from wbj.tito.structure import ChainRow

        def chain(ticker, **k):
            filas = []
            for exp in ("2026-09-18", "2027-01-15"):
                for s in range(70, 135, 5):
                    for ct in ("call", "put"):
                        oi = int(1500 * math.exp(-((s - SPOT) ** 2) / 400)) + 60
                        filas.append(ChainRow(ct, exp, float(s), oi, int(oi * 0.3),
                                              oi * 100 * s, option_ticker=f"O:D{exp}{ct}{s}",
                                              price=1.5, open_premium=oi * 1.5))
            return MASS.ChainResult(rows=filas, underlying_price=SPOT, pages=1,
                                    truncated=False)

        def bars(ticker, days=365, **k):
            out, seed = [], 7
            for i in range(200):
                seed = (seed * 1103515245 + 12345) % 2147483648
                c = 92 + i * 0.04 + 9 * math.sin(i / 17) + (seed / 2147483648 - .5) * 1.5
                out.append(LvlBar((NOW - timedelta(days=200 - i)).date().isoformat(),
                                  c + 1.5, c - 1.5, c))
            out[-1] = LvlBar(out[-1].time, SPOT + 1, SPOT - 1, SPOT)
            return out

        def flow(ticker, **k):
            tr = []
            for i, (strike, side, mins) in enumerate(
                    [(105, "AT_ASK", 3), (105, "ABOVE_ASK", 2), (90, "AT_BID", 40)]):
                cp = "C" if strike >= 100 else "P"
                tr.append({"id": i + 1, "symbol": f"DEMO270115{cp}{strike * 1000:08d}",
                           "price": 9.2, "size": 800, "side": side,
                           "bid_price": 9.14, "ask_price": 9.26,
                           "premium": 9.2 * 800 * 100,
                           "delta": .62, "gamma": .03, "theta": -.04, "vega": .3,
                           "implied_volatility": .44, "open_interest": 4000,
                           "volume": 5200, "score": 8, "sentiment": "bullish",
                           "timestamp": (NOW - timedelta(minutes=mins)).isoformat(),
                           "asset_price": SPOT, "trade_condition_id": 231})
            return FlowResult(trades=tr, pages=1, truncated=False)

        monkeypatch.setattr(MASS, "fetch_option_chain", chain)
        monkeypatch.setattr(MASS, "fetch_daily_bars", bars)
        monkeypatch.setattr(MASS, "fetch_ticker_name", lambda t, **k: "Demo Corporation")
        monkeypatch.setattr(MASS, "fetch_company", lambda t, **k: {
            "ticker": t, "name": "Demo Corporation", "sector": "Software",
            "price": SPOT, "change": 1.2, "change_percent": 1.21,
            "market_cap": 7.2e9, "day_volume": 41_200_000, "day_open": 99.0,
            "day_high": 101.5, "day_low": 98.5, "prev_close": 98.8})
        monkeypatch.setattr(MS, "fetch_flow", flow)
        monkeypatch.setenv("MARKETSNACK_COOKIE", "y" * 32)

    @pytest.fixture
    def d(self, client):
        r = client.get("/api/projection-targets?ticker=DEMO").json()
        assert r.get("ok"), r.get("error")
        return r

    # ── CompanyHeader ────────────────────────────────────────────────────
    def test_la_ficha_de_la_empresa_viaja(self, d):
        c = d["company"]
        assert c["name"] == "Demo Corporation"
        assert c["sector"] == "Software"
        assert c["market_cap"] and c["day_volume"]
        assert c["day_low"] and c["day_high"] and c["prev_close"]

    def test_el_precio_de_la_ficha_ES_EL_DEL_MOTOR(self, d):
        """Que la cabecera diga un precio y la gráfica otro es el fallo que la
        cascada de spot de su `page.tsx` existe para evitar."""
        assert d["company"]["price"] == d["spot"]

    def test_sin_ficha_el_scorecard_sigue_saliendo(self, client, monkeypatch):
        """La ficha es contexto de cabecera: perderla no puede tumbar un
        scorecard que ya está calculado. El spot cae al de la cadena, que es el
        segundo eslabón de su cascada en `page.tsx`."""
        import wbj.tito.massive as MASS

        monkeypatch.setattr(MASS, "fetch_company", lambda t, **k: None)
        r = client.get("/api/projection-targets?ticker=DEMO").json()
        assert r["ok"] is True
        assert r["company"]["name"] is None
        assert r["company"]["has_logo"] is False, "no se promete un logo que no se sabe si hay"
        assert r["company"]["price"] == r["spot"], "la cabecera no se queda muda"

    # ── OptionChainTable ─────────────────────────────────────────────────
    def test_la_cadena_entera_viaja_con_sus_ocho_columnas(self, d):
        assert d["chain"], "sin filas no hay tabla que pintar"
        fila = d["chain"][0]
        for col in ("expiration", "contract_type", "strike", "open_interest",
                    "volume", "price", "open_premium", "notional_value"):
            assert col in fila, col

    def test_la_cadena_llega_ordenada_por_open_interest(self, d):
        ois = [f["open_interest"] for f in d["chain"]]
        assert ois == sorted(ois, reverse=True)

    def test_la_cabecera_de_la_cadena_dice_la_verdad(self, d):
        m = d["chain_meta"]
        assert m["contract_count"] == len(d["chain"])
        assert m["expiration_count"] == 2
        assert m["truncated"] is False and m["capped"] is False

    def test_el_recorte_de_la_cadena_se_DECLARA(self, client, monkeypatch):
        """Recortar en silencio es lo que este proyecto no hace."""
        import vertex_api as V

        monkeypatch.setattr(V, "TITO_CHAIN_MAX", 5)
        d = client.get("/api/projection-targets?ticker=DEMO").json()
        assert len(d["chain"]) == 5
        assert d["chain_meta"]["capped"] is True
        assert d["chain_meta"]["shown"] == 5
        assert d["chain_meta"]["contract_count"] > 5, "el total NO se falsea"

    # ── ChartPanel ───────────────────────────────────────────────────────
    def test_los_cinco_de_mas_nocional_viajan(self, d):
        top = d["top_contracts"]
        assert len(top) == 5
        vals = [t["notional_value"] for t in top]
        assert vals == sorted(vals, reverse=True)
        assert vals[0] == max(f["notional_value"] for f in d["chain"])

    # ── ActivityCard / MoneyFlowCard / RepeatBadge ───────────────────────
    def test_las_filas_que_alimentan_sus_dos_tarjetas(self, d):
        assert d["conviction_rows"], "ActivityCard y MoneyFlowCard salen de aquí"
        f = d["conviction_rows"][0]
        for col in ("type", "premium", "timestamp", "aggression", "strike",
                    "expiration", "underlying", "repeated"):
            assert col in f, col

    def test_el_spread_y_el_vol_oi_que_leen_los_azulejos(self, d):
        assert "avg_pct" in d["subagents"]["conviction"]["spread"]
        assert "pct" in d["structure"]["vol_oi"]

    # ── FlowPriceChart ───────────────────────────────────────────────────
    def test_los_racimos_traen_su_ventana_en_segundos(self, d):
        cl = d.get("flow_clusters")
        if cl:
            assert cl[0]["start_sec"] and cl[0]["end_sec"]
            # UNIX en segundos, no milisegundos: leerlos mal los manda a 1970 y
            # ningún día se marcaría como racimo.
            assert cl[0]["start_sec"] < 4e9

    # ── RiskProfileCard ──────────────────────────────────────────────────
    def test_los_dos_presupuestos_viajan_calculados(self, client, monkeypatch):
        """Los dos techos con los que se dimensionó, pedidos AL MOTOR.

        Nació por un fallo concreto: el de theta se calculaba como un % del
        riesgo por operación en vez de un % de la cuenta —$7,50 en vez de $50—
        y sobre ese número se descartaban contratos perfectamente operables.
        Comprobaba `capital × 5%`, que era la fórmula buena entonces.

        Ahora hay DOS modelos —el de Víctor y el de Kevin, y el perfil decide—
        así que fijar una fórmula aquí volvería a poner una segunda aritmética
        que puede separarse de la del motor. Lo que se exige es más fuerte y no
        caduca: que lo servido sea EXACTAMENTE lo que devuelve `budgets_of`.
        """
        import wbj.tito.marketsnack as MS
        from wbj.tito.marketsnack import FlowResult
        from wbj.tito.risk import RiskProfile, budgets_of

        monkeypatch.setattr(MS, "fetch_market_flow", lambda **k: FlowResult(
            trades=[], pages=1, truncated=False))
        P = client.get("/api/tito-ideas").json()["perfil"]
        assert P["theta_budget_pct"] == 5.0
        pos = P["max_posicion_pct"]
        b = budgets_of(RiskProfile(
            account_size=P["capital"], tolerance_pct=P["riesgo_pct"],
            max_position_pct=float(pos[1]), loss_pct_of_position=P["riesgo_pct"]))
        assert P["budget_premium"] == pytest.approx(b.premium)
        assert P["budget_theta"] == pytest.approx(b.theta)
        assert P["theta_budget"] == pytest.approx(b.theta), (
            "el campo viejo dejó de seguir al presupuesto real")
        # Y el fallo original, en su forma general: el techo de theta no puede
        # ser una miga. Con $1.000 eran $7,50 y casi nada pasaba el filtro.
        assert P["budget_theta"] > P["capital"] * 0.01


class TestElProxyDelLogo:
    """`/api/tito-logo` — su `/api/logo`."""

    def test_la_ruta_existe(self):
        import vertex_api as V

        assert "/api/tito-logo" in {r.path for r in V.app.routes}

    def test_sin_logo_es_404_no_500(self, client, monkeypatch):
        import wbj.tito.massive as MASS

        monkeypatch.setattr(MASS, "fetch_logo_image", lambda t, **k: None)
        assert client.get("/api/tito-logo?ticker=AAPL").status_code == 404

    def test_sirve_el_binario_con_su_cache(self, client, monkeypatch):
        import wbj.tito.massive as MASS

        monkeypatch.setattr(MASS, "fetch_logo_image",
                            lambda t, **k: (b"\x89PNG\r\n\x1a\n", "image/png"))
        r = client.get("/api/tito-logo?ticker=AAPL")
        assert r.status_code == 200
        assert r.content.startswith(b"\x89PNG")
        assert r.headers["content-type"] == "image/png"
        assert "max-age=86400" in r.headers["cache-control"]

    def test_un_ticker_invalido_es_400(self, client):
        assert client.get("/api/tito-logo?ticker=!!!").status_code == 400
        assert client.get("/api/tito-logo?ticker=").status_code == 400

    def test_una_travesia_de_rutas_no_sale_del_borde(self, client, monkeypatch):
        """No se rechaza con un 400: se SANEA, que es lo que hace el mismo
        `_tito_ticker` de las otras rutas. Lo que se comprueba es que lo que
        llega a Massive ya no lleve barras ni puntos dobles."""
        import vertex_api as V

        visto = []
        monkeypatch.setattr("wbj.tito.massive.fetch_logo_image",
                            lambda t, **k: (visto.append(t), None)[1])
        client.get("/api/tito-logo?ticker=../../etc/passwd")
        # Queda `....ETCPASSWD`: sin barras, sin nada que salga del segmento de
        # la URL. Los puntos sobreviven porque son legítimos en un ticker
        # (BRK.B), y sin barra no forman una travesía.
        assert visto, "la ruta ni siquiera llegó a pedir el logo"
        assert re.fullmatch(r"[A-Z0-9._-]+", visto[0]), visto
        assert "/" not in visto[0] and "\\" not in visto[0], visto

    def test_un_fallo_de_massive_no_filtra_la_clave(self, client, monkeypatch):
        import wbj.tito.massive as MASS

        def revienta(t, **k):
            raise RuntimeError("401 con Bearer supersecreto123")

        monkeypatch.setattr(MASS, "fetch_logo_image", revienta)
        r = client.get("/api/tito-logo?ticker=AAPL")
        assert r.status_code == 502
        assert "supersecreto" not in r.text

    def test_la_clave_solo_viaja_al_dominio_de_massive(self):
        """El binario lo sirve Massive desde su propio dominio. Si la respuesta
        trajera una URL torcida, la Authorization no puede acompañarla."""
        fuente = (ROOT / "engine" / "wbj" / "tito" / "massive.py").read_text(encoding="utf-8")
        i = fuente.index("def fetch_logo_image")
        cuerpo = fuente[i:fuente.index("\n@dataclass", i)]
        assert "netloc" in cuerpo and "if mismo else {}" in cuerpo


class TestSusDoceComponentesEstanEnElPanel:
    """Que la función exista Y que alguien la llame. Lo que además EJECUTA el
    JS es `engine/scripts/_smoke_componentes.mjs`."""

    COMPONENTES = {
        "CompanyHeader":    ("vcCompanyHTML", "${vcCompanyHTML(d)}"),
        "AnalysisLoader":   ("vcLoaderHTML", "vcLoaderArranca(ticker)"),
        "ActivityCard":     ("vcActivityHTML", "vcActivityHTML(d)"),
        "MoneyFlowCard":    ("vcMoneyFlowHTML", "vcMoneyFlowHTML(d)"),
        "OptionChainTable": ("vcCadenaHTML", "vcPintaCadena(d)"),
        "ChartPanel":       ("renderProjTop5", "renderProjTop5(d)"),
        "FlowPriceChart":   ("renderProjFlowMoney", "renderProjFlowMoney(d)"),
        "WatchlistCard":    ("renderProjWatchlist", "renderProjWatchlist()"),
        "RiskProfileCard":  ("vcRiesgoHTML", "vcRiesgoHTML(d.perfil)"),
        "RepeatBadge":      ("vcRepeatBadge", "vcRepeatCounts"),
        "ChartCrosshair":   ("vcCrosshairCablea", "vcCrosshairCablea(cont,"),
        "HeaderBar":        ("vcSyncCabecera", "projHbRight"),
    }

    @pytest.mark.parametrize("nombre", sorted(COMPONENTES))
    def test_existe_y_alguien_lo_llama(self, nombre):
        fn, uso = self.COMPONENTES[nombre]
        assert f"function {fn}(" in HTML, f"{nombre}: falta {fn}"
        assert uso in HTML, f"{nombre}: {fn} existe pero nadie lo usa"

    def test_sus_contenedores_estan_en_el_markup(self):
        for cid in ("projActivity", "projMoneyFlow", "projChain", "projTop5",
                    "projFlowMoney", "projWatchlist", "projRiesgo", "projHbRight"):
            assert f'id="{cid}"' in HTML, cid

    def test_el_logo_va_por_el_proxy_y_no_a_massive(self):
        assert "/api/tito-logo?ticker=" in HTML
        assert "api.massive.com" not in HTML

    def test_la_marca_de_Tito_NO_se_PINTA(self):
        """El logo de la EMPRESA sí; su wordmark no — esta pantalla es Vertex.

        Se mira el código que se EJECUTA, no los comentarios: los comentarios
        citan su repo por su nombre a propósito, y prohibirlo ahí borraría la
        trazabilidad del port sin quitar un solo píxel de la pantalla."""
        js = "\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", HTML))
        cuerpo = re.sub(r"<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?</script>", "", HTML)
        vivo = _sin_comentarios(js) + "\n" + re.sub(r"<!--[\s\S]*?-->", "", cuerpo)
        assert "Tito Metralleta" not in vivo

    def test_los_formateadores_son_LOS_SUYOS(self):
        """Su `money` es compacto con 2 decimales; el `fmtAbbr` de Vertex tiene
        1. Las dos cifras se pintan juntas, así que no pueden discrepar."""
        assert "notation: 'compact', maximumFractionDigits: 2" in HTML
        assert "signDisplay: 'always'" in HTML          # su `pct`
        assert "timeZone: VC_ET" in HTML or "America/New_York" in HTML


class TestElPuenteNoTiraNadaDelPayload:
    """Cada campo que sirve la ruta del buzón lo lee alguien en el panel."""

    def test_los_campos_de_la_respuesta_se_usan(self):
        for campo in ("pending", "failed", "lastSyncedAt", "brokers", "legacy",
                      "granularity", "quoteUrl", "failReason"):
            assert campo in HTML, f"el buzón sirve `{campo}` y nadie lo lee"

    def test_la_importacion_del_watchlist_viejo_es_UNICA(self):
        assert "tito.watchlist.migrated" in HTML
        assert "wlMarcaMigrada()" in HTML

    def test_el_archivo_local_no_se_borra_al_cambiar_de_broker(self):
        js = HTML[HTML.index("async function wlCambiaBroker"):]
        js = js[:js.index("\nfunction ") if "\nfunction " in js else 400]
        assert "removeItem" not in js and "wlLocalGuarda([])" not in js


class TestLaCabeceraRecibeLoQuePinta:
    """El panel LEE `c.exchange`, `c.employees` y `c.has_logo`; el backend
    tiene que MANDARLOS.

    Un campo que el frontend lee y el backend no envía no rompe nada: se pinta
    "—" y nadie se entera. Aquí pasó con la bolsa y con los empleados, y el
    único síntoma era un subtítulo más corto de lo que él pone.
    """

    def test_el_panel_y_el_payload_hablan_de_los_mismos_campos(self):
        import pathlib
        import re

        import vertex_api as V

        html = pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function vcCompanyHTML(")
        lee = set(re.findall(r"\bc\.([a-z_]+)", html[i:i + 2600]))
        manda = set(V._tito_company("DEMO", 100.0, {
            "name": "X", "sector": "S", "exchange": "Nasdaq", "employees": 10,
            "has_logo": True, "market_cap": 1, "price": 1, "change": 0,
            "change_percent": 0, "day_volume": 1, "day_low": 1, "day_high": 1,
            "prev_close": 1}))
        faltan = lee - manda
        assert not faltan, f"la cabecera lee campos que el payload no manda: {sorted(faltan)}"

    def test_el_valor_LLEGA_no_solo_la_clave(self):
        """La clave estaba: el dict base la declaraba como `None`. Lo que no
        pasaba es el VALOR.

        Comprobar solo la presencia de la clave no sirve — ese test pasaba
        igual con el cableado borrado, porque `exchange` seguía existiendo
        valiendo `None`. Un campo que existe y siempre vale nulo se pinta como
        uno que no existe."""
        import vertex_api as V

        ficha = {"name": "X", "sector": "Semiconductores", "exchange": "Nasdaq",
                 "employees": 29600, "has_logo": True}
        out = V._tito_company("DEMO", 100.0, ficha)
        assert out["exchange"] == "Nasdaq", "la bolsa no llega a la cabecera"
        assert out["employees"] == 29600, "los empleados no llegan a la cabecera"
        assert out["sector"] == "Semiconductores"

    def test_sin_ficha_la_cabecera_sigue_teniendo_las_claves(self):
        """Sin Massive se devuelve lo mínimo, pero con TODAS las claves: una
        clave ausente y una clave nula se pintan igual, y solo una de las dos
        es un estado que el panel sabe manejar."""
        import vertex_api as V

        base = V._tito_company("DEMO", 100.0, None)
        for k in ("exchange", "employees", "has_logo", "name", "sector"):
            assert k in base, k

    def test_has_logo_deja_de_prometer_lo_que_no_hay(self):
        import vertex_api as V

        assert V._tito_company("D", 1.0, {"has_logo": False})["has_logo"] is False
        assert V._tito_company("D", 1.0, {"has_logo": True})["has_logo"] is True
        # Sin ficha se mantiene la promesa optimista: mejor pedirlo y caer a
        # las iniciales que esconderlo por no haber podido preguntar.
        assert V._tito_company("D", 1.0, {"name": "X"})["has_logo"] is True


class TestLosUmbralesQueVivenEnElComponente:
    """Sus componentes llevan umbrales PROPIOS, que no están en el motor.

    Siete rondas comparando que la función existiera y ni una mirando lo que
    decide por dentro. `strengthLabel` (70/50/30) pone "Muy fuerte" o "Débil"
    al lado de cada nivel, `ivColor` (90/61/40) tiñe la IV, el hit rate va
    verde ≥55 y rojo <45, la frase de sesgo tiene una banda muerta de ±1%, y
    `intensity > 0.12` decide si una celda del heatmap enseña su número.

    Nada de esto rompe si falta: la pantalla simplemente dice menos.
    """

    def _html(self):
        import pathlib

        return pathlib.Path("vertex_fund_os_platform.html").read_text(encoding="utf-8")

    def test_los_nueve_helpers_de_banda_existen_y_se_usan(self):
        import re

        html = self._html()
        for fn in ("vcFuerzaLabel", "vcIvColor", "vcHitRateColor", "vcSesgoFrase",
                   "vcConfLabel", "vcErrColor", "vcHaceCuanto", "vcDolares",
                   "vcScoreColor"):
            assert f"function {fn}(" in html, f"{fn} no existe"
        # Los que tienen sitio en el panel se llaman de verdad.
        for fn in ("vcFuerzaLabel", "vcIvColor", "vcHitRateColor", "vcSesgoFrase",
                   "vcErrColor", "vcHaceCuanto", "vcScoreColor"):
            assert len(re.findall(rf"\b{fn}\s*\(", html)) > 1, f"{fn} sin llamador"

    def test_las_bandas_son_las_suyas_valor_a_valor(self):
        html = self._html()
        # `strengthLabel`: 70 / 50 / 30
        fuerza = html.split("function vcFuerzaLabel(")[1][:400]
        for u, etq in ((70, "Muy fuerte"), (50, "Fuerte"), (30, "Moderado")):
            assert f"n >= {u}" in fuerza and etq in fuerza, (u, etq)
        assert "Débil" in fuerza
        # `ivColor`: 90 / 61 / 40
        ivc = html.split("function vcIvColor(")[1][:400]
        for u in (90, 61, 40):
            assert f"n >= {u}" in ivc, u
        # hit rate: ≥55 bueno, <45 malo
        hr = html.split("function vcHitRateColor(")[1][:400]
        assert "n >= 55" in hr and "n < 45" in hr
        # sesgo: banda muerta ±1
        sg = html.split("function vcSesgoFrase(")[1][:400]
        assert "n > 1 ?" in sg and "n < -1 ?" in sg
        assert "bien calibrado" in sg
        # heatmap: el número solo por encima de 0,12
        assert "a > 0.12 ?" in html

    def test_los_tres_minimos_de_fuerza_son_tres_y_distintos(self):
        """25 en la gráfica, 20 en la lista y 35 en los muros. Con uno solo, o
        la gráfica se llena de rayas o la lista se queda coja."""
        html = self._html()
        assert "VC_MIN_FUERZA_GRAFICA = 25" in html
        assert "VC_MIN_FUERZA_LISTA = 20" in html
        assert "VC_MIN_FUERZA_MUROS = 35" in html
        assert "VC_MIN_FUERZA_LISTA" in html.split("function vcLevelsHTML(")[1][:900], \
            "la lista de niveles no aplica su filtro de fuerza"
