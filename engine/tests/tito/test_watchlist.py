"""Port de `web/lib/watchlist.test.ts` — sus 56 casos, uno a uno.

Mismo criterio que el resto de la suite: se traducen SUS tests, no se escriben
otros. Si un caso suyo falla aquí, el port divergió.

El diferencial (`engine/scripts/diff_watchlist.sh`) ejecuta además SU archivo en
Node sobre 734 casos generados y compara campo a campo. Los dos hacen falta: sus
tests dicen qué comportamiento le importa a ÉL; el diferencial caza lo que sus
tests no cubren.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wbj.tito.watchlist import (BROKERS, BrokerAdapter, BrokerSync, ContractRef,
                                EntrySource, OutboxItem, OutboxTarget,
                                add_to_outbox, broker_by_id, build_entry,
                                contract_query, contract_ref_label,
                                failed_outbox, mark_outbox_failed,
                                mark_outbox_synced, mark_synced, outbox_key,
                                outbox_label, payload_for, pending_outbox,
                                quote_link, remove, remove_from_outbox,
                                sort_entries, ticker_list, underlyings, upsert)

NOW = datetime(2026, 7, 24, 17, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 25, 17, 0, 0, tzinfo=timezone.utc)
ISO_NOW = "2026-07-24T17:00:00.000Z"
ISO_LATER = "2026-07-25T17:00:00.000Z"

PROFILE = {"accountSize": 10_000, "tolerancePct": 4}
SIZING = {"maxContracts": 2, "binding": "prima"}


def source(**p) -> EntrySource:
    base = dict(symbol="WULF270115C00020000", ticker="WULF", type="call",
                strike=20, expiration="2027-01-15", dte=175,
                price=5.25, assetPrice=19.27, premium=5_250_000,
                thetaPctDaily=0.3)
    base.update(p)
    return EntrySource(**base)


def target(**p) -> OutboxTarget:
    base = dict(symbol="WULF270115C00020000", ticker="WULF", type="call",
                strike=20, expiration="2027-01-15")
    base.update(p)
    return OutboxTarget(**base)


C25 = target(symbol="WULF270115C00025000", strike=25)
ZETA = target(symbol="ZETA260918C00020000", ticker="ZETA", expiration="2026-09-18")
RH = broker_by_id("robinhood")
SCHWAB = broker_by_id("schwab")


class TestBuildEntry:
    def test_congela_la_foto_del_momento(self):
        e = build_entry(source(), SIZING, PROFILE, NOW)
        assert e.symbol == "WULF270115C00020000"
        assert e.ticker == "WULF"
        assert e.entrySpot == 19.27
        assert e.entryPrice == 5.25
        assert e.maxContracts == 2
        assert e.binding == "prima"
        assert e.accountSizeAtEntry == 10_000
        assert e.tolerancePctAtEntry == 4
        assert e.addedAt == ISO_NOW
        assert e.brokerSync is None


class TestUpsert:
    def test_anade_al_principio(self):
        a = build_entry(source(), SIZING, PROFILE, NOW)
        assert len(upsert([], a)) == 1

    def test_un_contrato_entra_una_sola_vez(self):
        a = build_entry(source(), SIZING, PROFILE, NOW)
        assert len(upsert(upsert([], a), a)) == 1

    def test_volver_a_marcarlo_NO_pisa_la_foto_original(self):
        original = build_entry(source(), SIZING, PROFILE, NOW)
        despues = build_entry(
            source(assetPrice=25, price=8),
            {"maxContracts": 9, "binding": "theta"},
            {"accountSize": 50_000, "tolerancePct": 10},
            datetime(2026, 8, 1, 17, 0, 0, tzinfo=timezone.utc),
        )
        only = upsert(upsert([], original), despues)[0]
        assert only.entrySpot == 19.27
        assert only.entryPrice == 5.25
        assert only.maxContracts == 2
        assert only.addedAt == ISO_NOW

    def test_re_marcar_conserva_el_estado_de_sincronizacion(self):
        a = build_entry(source(), SIZING, PROFILE, NOW)
        sincro = mark_synced([a], a.symbol, BrokerSync(
            broker="robinhood", status="sincronizado", sent="underlying", at=ISO_NOW))
        only = upsert(sincro, a)[0]
        assert only.brokerSync.status == "sincronizado"

    def test_contratos_distintos_del_mismo_ticker_conviven(self):
        c20 = build_entry(source(), SIZING, PROFILE, NOW)
        c25 = build_entry(source(symbol="WULF270115C00025000", strike=25),
                          SIZING, PROFILE, NOW)
        assert len(upsert(upsert([], c20), c25)) == 2


class TestRemove:
    def test_quita_por_simbolo_y_deja_el_resto(self):
        a = build_entry(source(), SIZING, PROFILE, NOW)
        b = build_entry(source(symbol="ZETA260918C00020000", ticker="ZETA"),
                        SIZING, PROFILE, NOW)
        lista = remove(upsert(upsert([], a), b), a.symbol)
        assert len(lista) == 1
        assert lista[0].ticker == "ZETA"

    def test_quitar_algo_que_no_esta_no_rompe_nada(self):
        assert remove([], "NADA") == []


class TestPayloadFor:
    """Qué se le puede mandar a cada broker."""

    ENTRY = build_entry(source(), SIZING, PROFILE, NOW)

    def test_un_broker_que_acepta_contratos_recibe_el_OCC_completo(self):
        p = payload_for(self.ENTRY, BrokerAdapter(
            id="x", name="X", kind="mcp", granularity="contracts"))
        assert p == {"sent": "contract", "value": "WULF270115C00020000"}

    def test_robinhood_acepta_el_contrato(self):
        """Tiene watchlist de opciones por MCP, verificado contra la API real."""
        assert RH.granularity == "contracts"
        assert payload_for(self.ENTRY, RH) == {
            "sent": "contract", "value": "WULF270115C00020000"}

    def test_un_broker_de_solo_subyacente_recibe_el_ticker_pelado(self):
        assert payload_for(self.ENTRY, SCHWAB) == {
            "sent": "underlying", "value": "WULF"}

    def test_un_broker_que_no_acepta_nada_devuelve_None(self):
        """La UI no finge que sincronizó."""
        assert payload_for(self.ENTRY, BrokerAdapter(
            id="none", name="N", kind="none", granularity="none")) is None


class TestUnderlyings:
    def test_deduplica_y_ordena_los_tickers_a_sincronizar(self):
        lista = [
            build_entry(source(), SIZING, PROFILE, NOW),
            build_entry(source(symbol="WULF270115C00025000", strike=25),
                        SIZING, PROFILE, NOW),
            build_entry(source(symbol="ZETA260918C00020000", ticker="ZETA"),
                        SIZING, PROFILE, NOW),
        ]
        assert underlyings(lista) == ["WULF", "ZETA"]


class TestSortEntries:
    def test_las_mas_recientes_primero(self):
        viejo = build_entry(source(), SIZING, PROFILE,
                            datetime(2026, 7, 1, tzinfo=timezone.utc))
        nuevo = build_entry(source(symbol="ZETA260918C00020000", ticker="ZETA"),
                            SIZING, PROFILE,
                            datetime(2026, 7, 20, tzinfo=timezone.utc))
        assert sort_entries([viejo, nuevo])[0].ticker == "ZETA"


class TestBrokers:
    def test_robinhood_declara_la_limitacion_que_le_queda(self):
        """Nada de estrategias de varias patas por API."""
        import re

        assert re.search(r"patas|spread", RH.caveat, re.I)

    def test_robinhood_es_el_unico_que_escribe_de_verdad(self):
        assert [b.id for b in BROKERS if b.kind == "mcp"] == ["robinhood"]

    def test_todos_los_brokers_tienen_id_unico(self):
        assert len({b.id for b in BROKERS}) == len(BROKERS)

    def test_un_id_desconocido_devuelve_None(self):
        assert broker_by_id("etrade") is None

    def test_todo_broker_link_trae_su_plantilla_de_URL(self):
        for b in BROKERS:
            if b.kind == "link":
                assert callable(b.quote_url), f"{b.id} es link pero no tiene quote_url"

    def test_los_brokers_de_copiar_NO_traen_URL(self):
        """Mandar a un 404 es peor que copiar el ticker."""
        for b in BROKERS:
            if b.kind == "copy":
                assert b.quote_url is None, f"{b.id} es copy y no debería tener URL"
                assert b.caveat, f"{b.id} debe explicar por qué no hay enlace"


class TestQuoteLink:
    def test_construye_la_URL_del_ticker_en_el_broker(self):
        assert quote_link("WULF", SCHWAB) == \
            "https://www.schwab.com/research/stocks/quotes/summary/WULF"
        assert "symbol=WULF" in quote_link("WULF", broker_by_id("fidelity"))

    def test_un_broker_sin_ruta_por_simbolo_devuelve_None(self):
        """No una URL inventada."""
        assert quote_link("WULF", broker_by_id("webull")) is None
        assert quote_link("WULF", broker_by_id("ibkr")) is None

    def test_escapa_el_ticker_en_vez_de_pegarlo_crudo(self):
        assert quote_link("A B", RH) == "https://robinhood.com/stocks/A%20B"


class TestTickerList:
    def test_da_los_subyacentes_listos_para_pegar(self):
        lista = [
            build_entry(source(), SIZING, PROFILE, NOW),
            build_entry(source(symbol="WULF270115C00025000", strike=25),
                        SIZING, PROFILE, NOW),
            build_entry(source(symbol="ZETA260918C00020000", ticker="ZETA"),
                        SIZING, PROFILE, NOW),
        ]
        assert ticker_list(lista) == "WULF, ZETA"

    def test_un_watchlist_vacio_da_cadena_vacia(self):
        assert ticker_list([]) == ""


class TestContractQuery:
    """El puente OCC → id del broker."""

    def test_arma_la_busqueda_que_resuelve_el_contrato(self):
        assert contract_query(ContractRef("wulf", "call", 20, "2027-01-15")) == {
            "chain_symbol": "WULF", "type": "call",
            "strike_price": "20.0000", "expiration_dates": "2027-01-15"}

    def test_el_strike_va_con_4_decimales(self):
        """Es un filtro exacto: '20' no casa con '20.0000'."""
        q = contract_query(ContractRef("SPY", "put", 612.5, "2026-09-18"))
        assert q["strike_price"] == "612.5000"

    def test_sin_strike_o_sin_vencimiento_devuelve_None(self):
        """En vez de adivinar el contrato entre decenas."""
        assert contract_query(ContractRef("WULF", "call", None, "2027-01-15")) is None
        assert contract_query(ContractRef("WULF", "call", 20, None)) is None


class TestBuzonDeSalida:
    def test_con_granularidad_de_contratos_encola_el_contrato_entero(self):
        only = add_to_outbox([], target(), RH, NOW)[0]
        assert only.ticker == "WULF"
        assert only.symbol == "WULF270115C00020000"
        assert only.type == "call"
        assert only.strike == 20
        assert only.expiration == "2027-01-15"
        assert only.broker == "robinhood"
        assert only.syncedAt is None

    def test_un_broker_de_solo_subyacente_NO_recibe_strike_ni_vencimiento(self):
        only = add_to_outbox([], target(), SCHWAB, NOW)[0]
        assert only.ticker == "WULF"
        assert only.symbol is None
        assert only.strike is None
        assert only.expiration is None

    def test_dos_strikes_del_mismo_ticker_son_dos_trabajos_con_contratos(self):
        caja = add_to_outbox(add_to_outbox([], target(), RH, NOW), C25, RH, LATER)
        assert len(caja) == 2

    def test_pero_uno_solo_cuando_el_broker_solo_entiende_el_subyacente(self):
        caja = add_to_outbox(add_to_outbox([], target(), SCHWAB, NOW), C25,
                             SCHWAB, LATER)
        assert len(caja) == 1

    def test_marcar_dos_veces_no_genera_trabajo_doble(self):
        caja = add_to_outbox(add_to_outbox([], target(), RH, NOW), target(), RH, LATER)
        assert len(caja) == 1

    def test_no_reencola_algo_que_ya_se_sincronizo(self):
        sincro = mark_outbox_synced(add_to_outbox([], target(), RH, NOW),
                                    ["WULF270115C00020000"], "robinhood", NOW)
        otra = add_to_outbox(sincro, target(), RH, LATER)
        assert len(otra) == 1
        assert pending_outbox(otra, "robinhood") == []

    def test_cada_broker_tiene_su_propia_cola(self):
        caja = add_to_outbox([], target(), RH, NOW)
        caja = add_to_outbox(caja, target(), SCHWAB, NOW)
        assert [i.symbol for i in pending_outbox(caja, "robinhood")] == \
            ["WULF270115C00020000"]
        caja = mark_outbox_synced(caja, ["WULF270115C00020000"], "robinhood", NOW)
        assert pending_outbox(caja, "robinhood") == []
        assert [i.ticker for i in pending_outbox(caja, "schwab")] == ["WULF"]

    def test_lo_pendiente_sale_deduplicado_y_ordenado(self):
        caja = []
        for t in (ZETA, C25, target()):
            caja = add_to_outbox(caja, t, RH, NOW)
        assert [i.symbol for i in pending_outbox(caja, "robinhood")] == [
            "WULF270115C00020000", "WULF270115C00025000", "ZETA260918C00020000"]

    def test_lo_pendiente_trae_strike_y_vencimiento(self):
        """Sin ellos el agente no resuelve el id en el broker."""
        item = pending_outbox(add_to_outbox([], target(), RH, NOW), "robinhood")[0]
        assert contract_query(ContractRef(item.ticker, item.type, item.strike,
                                          item.expiration)) is not None

    def test_confirmar_no_toca_la_fecha_de_los_ya_confirmados(self):
        caja = add_to_outbox(add_to_outbox([], target(), RH, NOW), ZETA, RH, NOW)
        caja = mark_outbox_synced(caja, ["WULF270115C00020000"], "robinhood", NOW)
        caja = mark_outbox_synced(caja, ["ZETA260918C00020000"], "robinhood", LATER)
        assert next(i for i in caja if i.ticker == "WULF").syncedAt == ISO_NOW
        assert next(i for i in caja if i.ticker == "ZETA").syncedAt == ISO_LATER

    def test_la_cola_vieja_de_solo_tickers_sigue_leyendose(self):
        legado = [OutboxItem(ticker="WULF", broker="robinhood",
                             addedAt=ISO_NOW, syncedAt=None)]
        assert len(pending_outbox(legado, "robinhood")) == 1
        assert outbox_label(legado[0]) == "WULF"

    def test_etiqueta_el_contrato_entero_cuando_lo_hay(self):
        item = add_to_outbox([], target(), RH, NOW)[0]
        assert outbox_label(item) == "WULF $20 CALL 2027-01-15"

    def test_un_broker_que_no_sincroniza_nada_encola_solo_el_ticker(self):
        only = add_to_outbox([], target(), broker_by_id("none"), NOW)[0]
        assert only.symbol is None


class TestOutboxKey:
    """La clave que decide qué es el mismo trabajo."""

    def test_con_contrato_manda_el_simbolo_OCC(self):
        assert outbox_key(OutboxItem(
            ticker="WULF", broker="robinhood", addedAt=ISO_NOW, syncedAt=None,
            symbol="WULF270115C00020000", type="call", strike=20,
            expiration="2027-01-15")) == "WULF270115C00020000"

    def test_sin_contrato_cae_al_ticker(self):
        """Es como sobrevive la cola vieja."""
        assert outbox_key(OutboxItem(ticker="WULF", broker="robinhood",
                                     addedAt=ISO_NOW, syncedAt=None)) == "WULF"


class TestContractRefLabel:
    def test_nombra_el_contrato_entero(self):
        assert contract_ref_label(ContractRef("WULF", "call", 20, "2027-01-15")) \
            == "WULF $20 CALL 2027-01-15"

    def test_sin_strike_o_sin_vencimiento_se_queda_en_el_ticker(self):
        """No inventa."""
        assert contract_ref_label(ContractRef("WULF", "call", None, "2027-01-15")) == "WULF"
        assert contract_ref_label(ContractRef("WULF", "put", 20, None)) == "WULF"


class TestColaMixta:
    """La cola de `outbox.json` es anterior a la granularidad por contrato, así
    que conviven entradas de solo-ticker con entradas completas. Verificado en
    vivo por él: el panel mostraba "SPX $8000 CALL 2028-12-15 · SPXW · SPY ·
    WULF" sin romperse."""

    LEGADO = OutboxItem(ticker="WULF", broker="robinhood",
                        addedAt="2026-07-24T17:55:45.721Z", syncedAt=None)

    def test_las_viejas_se_listan_y_se_etiquetan_con_el_ticker_pelado(self):
        pend = pending_outbox([self.LEGADO], "robinhood")
        assert len(pend) == 1
        assert outbox_label(pend[0]) == "WULF"

    def test_una_vieja_no_bloquea_encolar_el_contrato_completo(self):
        caja = add_to_outbox([self.LEGADO], target(), RH, NOW)
        # Claves distintas ("WULF" vs el OCC): son dos trabajos, no un duplicado.
        assert len(caja) == 2

    def test_confirmar_la_vieja_por_ticker_no_toca_el_contrato_nuevo(self):
        caja = mark_outbox_synced(add_to_outbox([self.LEGADO], target(), RH, NOW),
                                  ["WULF"], "robinhood", NOW)
        assert next(i for i in caja if not i.symbol).syncedAt == ISO_NOW
        assert next(i for i in caja if i.symbol).syncedAt is None


class TestRemoveFromOutbox:
    """Desencolar al desmarcar ⭐."""

    LEGADO = OutboxItem(ticker="WULF", broker="robinhood",
                        addedAt="2026-07-24T17:55:45.721Z", syncedAt=None)
    CONTRATO = OutboxItem(ticker="WULF", broker="robinhood",
                          addedAt="2026-07-24T18:00:00.000Z", syncedAt=None,
                          symbol="WULF270115C00020000", type="call", strike=20,
                          expiration="2027-01-15")
    HERMANO = OutboxItem(ticker="WULF", broker="robinhood",
                         addedAt="2026-07-24T18:01:00.000Z", syncedAt=None,
                         symbol="WULF270115C00025000", type="call", strike=25,
                         expiration="2027-01-15")
    OBJ = {"symbol": "WULF270115C00020000", "ticker": "WULF"}

    def test_quita_el_contrato_por_su_simbolo(self):
        caja = remove_from_outbox([self.CONTRATO, self.HERMANO], self.OBJ, "robinhood")
        assert [i.symbol for i in caja] == ["WULF270115C00025000"]

    def test_tambien_barre_la_fila_vieja_de_solo_ticker(self):
        """La regresión real: `outbox_key(legado)` vale "WULF", no el OCC, así
        que el filtro por símbolo nunca casaba y SPXW/SPY se quedaban encoladas
        para siempre."""
        caja = remove_from_outbox([self.LEGADO, self.HERMANO], self.OBJ, "robinhood")
        assert [i.symbol for i in caja] == ["WULF270115C00025000"]

    def test_no_se_arrastra_los_strikes_hermanos(self):
        caja = remove_from_outbox([self.CONTRATO, self.HERMANO], self.OBJ, "robinhood")
        assert len(caja) == 1

    def test_sin_simbolo_quita_todo_lo_de_la_empresa(self):
        """Broker `underlying_only`: ahí sí es un solo trabajo."""
        caja = remove_from_outbox([self.CONTRATO, self.HERMANO],
                                  {"ticker": "WULF"}, "robinhood")
        assert caja == []

    def test_no_toca_la_cola_de_otro_broker(self):
        from dataclasses import replace

        otro = replace(self.CONTRATO, broker="schwab")
        caja = remove_from_outbox([self.CONTRATO, otro], self.OBJ, "robinhood")
        assert caja == [otro]


class TestMarkOutboxFailed:
    """Aparcar lo irresoluble."""

    LEGADO = OutboxItem(ticker="SPXW", broker="robinhood",
                        addedAt="2026-07-24T17:56:59.727Z", syncedAt=None)

    def test_lo_saca_de_pendientes(self):
        """Para que el drenador no lo reintente cada 15 min para siempre."""
        caja = mark_outbox_failed([self.LEGADO], ["SPXW"], "robinhood",
                                  "Sin strike.", NOW)
        assert pending_outbox(caja, "robinhood") == []
        assert failed_outbox(caja, "robinhood")[0].failReason == "Sin strike."

    def test_no_pisa_lo_ya_sincronizado(self):
        """Si entró, entró."""
        from dataclasses import replace

        hecho = replace(self.LEGADO, syncedAt=ISO_NOW)
        caja = mark_outbox_failed([hecho], ["SPXW"], "robinhood", "Sin strike.", NOW)
        assert caja[0].failedAt is None
        assert caja[0].syncedAt == ISO_NOW

    def test_el_motivo_del_primer_fallo_no_se_sobrescribe(self):
        una = mark_outbox_failed([self.LEGADO], ["SPXW"], "robinhood", "Sin strike.", NOW)
        dos = mark_outbox_failed(una, ["SPXW"], "robinhood", "Otro motivo.", NOW)
        assert dos[0].failReason == "Sin strike."


class TestLaPersistenciaDelBuzon:
    """`outbox_store.py` — su `outboxStore.ts`. No está en sus tests porque en
    su repo es I/O puro; aquí se prueba porque el formato del archivo es lo que
    mantiene viva la distinción entre fila legado y fila de contrato."""

    @pytest.fixture(autouse=True)
    def disco(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))
        self.dir = tmp_path

    def test_sin_archivo_devuelve_la_cola_vacia(self):
        from wbj.tito.outbox_store import load_outbox

        assert load_outbox()["items"] == []

    def test_ida_y_vuelta_conserva_el_contrato(self):
        from wbj.tito.outbox_store import load_outbox, save_outbox

        items = add_to_outbox([], target(), RH, NOW)
        save_outbox(items)
        leidos = load_outbox()["items"]
        assert leidos == items

    def test_una_fila_legado_NO_gana_un_symbol_nulo_al_guardarse(self):
        """Quien lea ese archivo con su código vería `"symbol" in item` cierto
        para una fila que no lo tiene, y perdería la distinción de
        `removeFromOutbox`."""
        import json

        from wbj.tito.outbox_store import save_outbox

        save_outbox(add_to_outbox([], target(), SCHWAB, NOW))
        crudo = json.loads((self.dir / "outbox.json").read_text())
        assert "symbol" not in crudo["items"][0]

    def test_un_archivo_corrupto_no_tumba_la_cola(self):
        from wbj.tito.outbox_store import load_outbox

        (self.dir).mkdir(parents=True, exist_ok=True)
        (self.dir / "outbox.json").write_text("{no json")
        assert load_outbox()["items"] == []

    def test_una_fila_basura_dentro_del_archivo_se_ignora(self):
        import json

        from wbj.tito.outbox_store import load_outbox

        (self.dir).mkdir(parents=True, exist_ok=True)
        (self.dir / "outbox.json").write_text(json.dumps(
            {"updatedAt": "", "items": ["no soy un objeto", 42, None,
                                        {"ticker": "WULF", "broker": "robinhood",
                                         "addedAt": ISO_NOW}]}))
        items = load_outbox()["items"]
        assert len(items) == 1 and items[0].ticker == "WULF"

    def test_el_watchlist_legado_se_lee_pero_NO_se_escribe(self):
        """Ya nadie guarda ahí: la ausencia de un `save` es el punto."""
        import wbj.tito.watchlist_store as WS

        assert not hasattr(WS, "save_watchlist")
        assert WS.load_watchlist()["entries"] == []

    def test_el_watchlist_legado_se_convierte_a_entradas(self):
        import json

        from wbj.tito.watchlist_store import load_watchlist

        (self.dir).mkdir(parents=True, exist_ok=True)
        (self.dir / "watchlist.json").write_text(json.dumps({
            "updatedAt": ISO_NOW, "broker": "robinhood",
            "entries": [{"symbol": "W20", "ticker": "WULF", "type": "call",
                         "strike": 20, "expiration": "2027-01-15",
                         "addedAt": ISO_NOW, "entrySpot": 18.42,
                         "entryPrice": 4.15, "maxContracts": 1,
                         "accountSizeAtEntry": 1000, "tolerancePctAtEntry": 15}]}))
        d = load_watchlist()
        assert d["broker"] == "robinhood"
        assert d["entries"][0].ticker == "WULF"
        assert d["entries"][0].entrySpot == 18.42
