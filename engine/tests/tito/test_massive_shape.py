"""La forma de la respuesta de Massive, en el bucle que llama a `compute.to_row`.

`fetch_option_chain` es donde el port de `compute.ts` se encuentra con la red.
Todo lo que llegue mal formado tiene que salir como `MassiveError` —que
`_tito_memory` sabe reportar con su motivo— y nunca como un `AttributeError` o
un `TypeError` crudo desde dentro del cliente.
"""

from __future__ import annotations

import pytest

import wbj.tito.massive as MASS
from wbj.tito.massive import MassiveError, fetch_option_chain


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "x" * 32)


def _responde(monkeypatch, payload):
    monkeypatch.setattr(MASS, "_get", lambda *a, **k: payload)


@pytest.mark.parametrize("payload,que", [
    ([{"details": {}}], "un array suelto en vez de un objeto"),
    (None, "null"),
    ("texto", "una cadena"),
    ({"results": "texto"}, "`results` como cadena"),
    ({"results": {"a": 1}}, "`results` como objeto"),
    ({"results": 5}, "`results` como número"),
])
def test_una_respuesta_con_forma_rara_da_massive_error(monkeypatch, payload, que):
    _responde(monkeypatch, payload)
    with pytest.raises(MassiveError):
        fetch_option_chain("DEMO")


def test_results_ausente_es_una_cadena_vacia_no_un_error(monkeypatch):
    # No es una respuesta mal formada: es un ticker sin contratos.
    _responde(monkeypatch, {"status": "OK"})
    assert fetch_option_chain("DEMO").rows == []


def test_contratos_no_dict_mezclados_no_tumban_la_pagina(monkeypatch):
    _responde(monkeypatch, {"results": [
        {"details": {"contract_type": "call", "strike_price": 100.0,
                     "expiration_date": "2026-09-18"},
         "open_interest": 500, "underlying_asset": {"price": 99.0}},
        "no soy un contrato", None, 42, ["x"],
        {"details": {"contract_type": "PUT", "strike_price": 95.0,
                     "expiration_date": "2026-09-18T00:00:00Z"},
         "open_interest": 700},
    ]})
    r = fetch_option_chain("DEMO")
    assert len(r.rows) == 2
    assert r.underlying_price == 99.0
    assert [x.open_interest for x in r.rows] == [700, 500]   # ordenadas por OI
    # Los dos comportamientos de su `compute.ts`, portados literales y fijados
    # aquí desde el cliente: el `T00:00:00Z` NO agrupa con la fecha pelada, y
    # `"PUT"` NO es `put` (su comparación es `t === "put"` exacto). Los dos
    # están propuestos aguas arriba en `upstream-tito-compute.patch`.
    assert r.expiration_count == 2
    assert [x.contract_type for x in r.rows] == ["call", "call"]


# ─────────────────────────────────────────────────────────────────────────────
# Barras diarias — `fetchDailyBars` de su `massive.ts`
# ─────────────────────────────────────────────────────────────────────────────


class TestBarrasDiarias:
    """Su `DailyBar` es `{time, open, high, low, close}` y el día sale en UTC.

    El port devolvía `LvlBar` —sin la apertura— y fechaba con
    `date.fromtimestamp`, que usa la zona LOCAL del servidor. Las dos cosas se
    veían solo al dibujar: velas doji y un eje temporal corrido un día.
    """

    #: 2026-07-31 20:00:00 UTC. En cualquier zona al oeste de Greenwich la
    #: fecha LOCAL de ese instante sigue siendo el 31; a las 23:30 UTC ya no.
    CIERRE_MS = 1_785_528_000_000       # 2026-07-31T20:00:00Z

    def test_la_apertura_viaja(self, monkeypatch):
        from wbj.tito.massive import fetch_daily_bars
        _responde(monkeypatch, {"results": [
            {"t": self.CIERRE_MS, "o": 99.5, "h": 101.0, "l": 98.0, "c": 100.0}]})
        b = fetch_daily_bars("DEMO")[0]
        assert (b.open, b.high, b.low, b.close) == (99.5, 101.0, 98.0, 100.0)

    def test_el_dia_es_UTC_no_la_zona_del_servidor(self, monkeypatch):
        import os
        import time

        from wbj.tito.massive import fetch_daily_bars
        # 2026-08-01T02:30:00Z — en Nueva York todavía es el 31 de julio.
        ms = 1_785_551_400_000
        _responde(monkeypatch, {"results": [
            {"t": ms, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}]})
        antes = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/New_York"
            time.tzset()
            assert fetch_daily_bars("DEMO")[0].time == "2026-08-01"
        finally:
            if antes is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = antes
            time.tzset()

    def test_no_filtra_barras_como_no_las_filtra_el(self, monkeypatch):
        # Su `bars.map(...)` no descarta nada; el port se saltaba las barras con
        # algún valor <= 0, así que la serie tenía huecos que él no tiene.
        from wbj.tito.massive import fetch_daily_bars
        _responde(monkeypatch, {"results": [
            {"t": self.CIERRE_MS, "o": 0, "h": 0, "l": 0, "c": 0},
            {"t": self.CIERRE_MS, "o": 1, "h": 2, "l": 0.5, "c": 1.5}]})
        assert len(fetch_daily_bars("DEMO")) == 2

    def test_el_cache_conserva_la_apertura(self, tmp_path, monkeypatch):
        from wbj.tito.bars_store import load_bars, save_bars
        from wbj.tito.massive import DailyBar
        monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))
        from datetime import datetime, timezone
        ahora = datetime(2026, 7, 31, 21, tzinfo=timezone.utc)
        save_bars("DEMO", [DailyBar("2026-07-31", 99.5, 101.0, 98.0, 100.0)], ahora)
        assert load_bars("DEMO").bars[0].open == 99.5


class TestLaFichaDeEmpresaLlevaSusDieciochoCampos:
    """`CompanyInfo` de su `types.ts`, entera.

    `fetch_company` servía 12 de los 18. Los seis que faltaban no eran de
    adorno: `_tito_company` YA los declaraba en su dict base y `vcCompanyHTML`
    YA los leía —el subtítulo de la cabecera es `[exchange, sector]` y hay una
    casilla de empleados—, así que el panel pintaba el subtítulo con el sector
    solo y la casilla vacía. La cadena entera existía menos el eslabón que
    produce el dato, y por eso no fallaba nada.
    """

    #: Los 18 de su interfaz, en snake_case.
    SUYOS = ("ticker", "name", "exchange", "market_cap", "homepage_url",
             "employees", "list_date", "sector", "description", "has_logo",
             "price", "change", "change_percent", "day_open", "day_high",
             "day_low", "day_volume", "prev_close")

    def _ficha(self, monkeypatch, detalles=None, snap=None):
        from wbj.tito import massive as M

        monkeypatch.setattr(M, "_api_key", lambda: "k")

        def _get(url, key, ticker, timeout):
            if "/v3/reference/" in url:
                return {"results": detalles if detalles is not None else {}}
            return {"ticker": snap if snap is not None else {}}

        monkeypatch.setattr(M, "_get", _get)
        return M.fetch_company("DEMO")

    def test_estan_los_dieciocho(self, monkeypatch):
        f = self._ficha(monkeypatch)
        faltan = [c for c in self.SUYOS if c not in f]
        assert not faltan, f"faltan {faltan}"

    def test_el_codigo_de_bolsa_se_traduce_como_el_suyo(self, monkeypatch):
        """`EXCHANGE_NAMES[code] ?? code`: el conocido se traduce y el
        desconocido se muestra tal cual, no se esconde."""
        f = self._ficha(monkeypatch, {"primary_exchange": "XNAS"})
        assert f["exchange"] == "Nasdaq"
        assert self._ficha(monkeypatch, {"primary_exchange": "ZZZZ"})["exchange"] == "ZZZZ"
        assert self._ficha(monkeypatch, {})["exchange"] is None

    def test_has_logo_sale_de_la_marca_y_acepta_las_dos_claves(self, monkeypatch):
        """`Boolean(branding.logo_url || branding.icon_url)` — cualquiera de
        las dos vale, y sin marca es `False`."""
        assert self._ficha(monkeypatch, {"branding": {"logo_url": "u"}})["has_logo"] is True
        assert self._ficha(monkeypatch, {"branding": {"icon_url": "u"}})["has_logo"] is True
        assert self._ficha(monkeypatch, {"branding": {}})["has_logo"] is False
        assert self._ficha(monkeypatch, {})["has_logo"] is False

    def test_los_cuatro_de_la_ficha_larga_llegan(self, monkeypatch):
        f = self._ficha(monkeypatch, {"total_employees": 29600, "list_date": "1999-01-22",
                                      "homepage_url": "https://x", "description": "texto"})
        assert f["employees"] == 29600
        assert f["list_date"] == "1999-01-22"
        assert f["homepage_url"] == "https://x"
        assert f["description"] == "texto"
