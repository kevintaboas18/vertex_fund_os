"""Tests for wbj.providers.fmp.FMPProvider."""

import json
from datetime import date
from pathlib import Path

import httpx

from wbj.config import Settings
from wbj.providers.base import _qualified_cache_key
from wbj.providers.cache import Cache
from wbj.providers.fmp import FMPProvider

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "fmp"


def _load_fixture(name: str):
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


def _make_provider(tmp_path, handler, fmp_api_key="testkey"):
    """Build an FMPProvider wired to a MockTransport-backed httpx.Client."""
    settings = Settings(fmp_api_key=fmp_api_key)
    cache = Cache(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FMPProvider(settings, cache, client=client)


def _capturing_handler(fixture_name, captured):
    """Return a handler that records the request and replies with a fixture."""

    def handler(request):
        # `ohlcv_daily` hace DOS peticiones desde que fusiona el cierre
        # ajustado por dividendos: la cruda y la ajustada. Guardar solo la
        # ultima hacia que los asserts miraran la segunda.
        captured["request"] = request
        captured.setdefault("requests", []).append(request)
        return httpx.Response(200, json=_load_fixture(fixture_name))

    return handler


# --- availability -----------------------------------------------------------



def _key(label, **params):
    """The cache key the provider will build for these request params."""
    return _qualified_cache_key(label, params)


def test_available_true_when_api_key_set(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=[]))
    assert p.available is True


def test_available_false_when_api_key_missing(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=[]), fmp_api_key=None)
    assert p.available is False


def test_all_methods_return_none_and_skip_network_when_unavailable(tmp_path):
    def handler(request):
        raise AssertionError("transport should not be called when unavailable")

    p = _make_provider(tmp_path, handler, fmp_api_key=None)

    assert p.profile("NVDA") is None
    assert p.income_annual("NVDA") is None
    assert p.income_quarterly("NVDA") is None
    assert p.balance_annual("NVDA") is None
    assert p.balance_quarterly("NVDA") is None
    assert p.cashflow_annual("NVDA") is None
    assert p.cashflow_quarterly("NVDA") is None
    assert p.ohlcv_daily("NVDA", today=date(2026, 7, 16)) is None
    assert p.peers("NVDA") is None
    assert p.analyst_estimates("NVDA") is None
    assert p.insider_trades("NVDA") is None
    assert p.institutional_holders("NVDA") is None
    assert p.earnings_calendar("NVDA") is None


# --- profile ------------------------------------------------------------


def test_profile_url_params_and_payload(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("profile", captured))

    result = p.profile("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/profile"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("apikey") is not None
    assert result == _load_fixture("profile")


# --- income statements ----------------------------------------------------


def test_income_annual_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("income_annual", captured))

    result = p.income_annual("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/income-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "annual"
    assert req.url.params.get("limit") == "6"
    assert req.url.params.get("apikey") is not None
    assert result == _load_fixture("income_annual")


def test_income_annual_custom_limit(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("income_annual", captured))

    p.income_annual("NVDA", limit=3)

    assert captured["request"].url.params.get("limit") == "3"


def test_income_quarterly_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("income_quarterly", captured))

    result = p.income_quarterly("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/income-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "quarter"
    assert req.url.params.get("limit") == "21"
    assert result == _load_fixture("income_quarterly")


# --- balance sheet ----------------------------------------------------------


def test_balance_annual_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("balance_annual", captured))

    result = p.balance_annual("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/balance-sheet-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "annual"
    assert req.url.params.get("limit") == "6"
    assert result == _load_fixture("balance_annual")


def test_balance_quarterly_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("balance_quarterly", captured))

    result = p.balance_quarterly("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/balance-sheet-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "quarter"
    assert req.url.params.get("limit") == "21"
    assert result == _load_fixture("balance_quarterly")


# --- cash flow ---------------------------------------------------------------


def test_cashflow_annual_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("cashflow_annual", captured))

    result = p.cashflow_annual("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/cash-flow-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "annual"
    assert req.url.params.get("limit") == "6"
    assert result == _load_fixture("cashflow_annual")


def test_cashflow_quarterly_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("cashflow_quarterly", captured))

    result = p.cashflow_quarterly("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/cash-flow-statement"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "quarter"
    assert req.url.params.get("limit") == "21"
    assert result == _load_fixture("cashflow_quarterly")


# --- OHLCV -------------------------------------------------------------------


def test_ohlcv_daily_url_params_and_returns_flat_list(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("ohlcv_daily", captured))

    result = p.ohlcv_daily("NVDA", years=3, today=date(2026, 7, 16))

    reqs = captured["requests"]
    # Dos peticiones: la serie cruda (OHLC para gaps y ATR) y la ajustada
    # por dividendos, que es la unica que trae `adjClose`.
    assert [r.url.path for r in reqs] == [
        "/stable/historical-price-eod/full",
        "/stable/historical-price-eod/dividend-adjusted"]
    req = reqs[0]
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("from") == "2023-07-16"
    assert req.url.params.get("to") == "2026-07-16"
    # La ventana tiene que ser la MISMA en las dos, o la fusion por fecha
    # dejaria barras sin ajustar en un extremo.
    assert reqs[1].url.params.get("from") == reqs[0].url.params.get("from")
    assert reqs[1].url.params.get("to") == reqs[0].url.params.get("to")
    # /stable/historical-price-eod/full returns a top-level JSON array (not
    # a {"historical": [...]} wrapper); the provider returns it as-is.
    esperado = _load_fixture("ohlcv_daily")
    assert [r["date"] for r in result] == [r["date"] for r in esperado]


def test_ohlcv_daily_default_years_is_3(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("ohlcv_daily", captured))

    p.ohlcv_daily("NVDA", today=date(2026, 7, 16))

    assert captured["request"].url.params.get("from") == "2023-07-16"


def test_ohlcv_daily_windows_do_not_share_a_cache_entry(tmp_path):
    """A 1-year fetch must not satisfy a later 3-year one.

    They used to share the bare key "ohlcv_daily", so whichever call ran
    first won. When the MVP path (`cli.py`, years=1) won, the strict
    packet then read ~251 bars and was rejected for "fewer than 252
    daily sessions" even though FMP had served the full history.
    """
    calls = []

    def handler(request):
        # Solo la cruda: la ajustada usa su propia clave de cache y aqui lo
        # que se prueba es que las VENTANAS no se pisen entre si.
        if request.url.path.endswith("/full"):
            calls.append(request.url.params.get("from"))
        return httpx.Response(200, json=_load_fixture("ohlcv_daily"))

    p = _make_provider(tmp_path, handler)
    today = date(2026, 7, 16)

    p.ohlcv_daily("NVDA", years=1, today=today)
    p.ohlcv_daily("NVDA", years=3, today=today)

    # Both windows must reach the network; neither may serve the other.
    assert calls == ["2025-07-16", "2023-07-16"]


def test_ohlcv_daily_missing_historical_key_returns_none(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"symbol": "NVDA"})

    p = _make_provider(tmp_path, handler)

    assert p.ohlcv_daily("NVDA", today=date(2026, 7, 16)) is None


def test_ohlcv_daily_tolerates_dict_wrapped_historical_shape(tmp_path):
    """Some plans may still wrap the series in {"historical": [...]}; the
    provider falls back to that shape when the payload isn't a flat list."""

    def handler(request):
        return httpx.Response(200, json={"symbol": "NVDA", "historical": _load_fixture("ohlcv_daily")})

    p = _make_provider(tmp_path, handler)

    result = p.ohlcv_daily("NVDA", today=date(2026, 7, 16))

    assert result == _load_fixture("ohlcv_daily")


# --- peers ---------------------------------------------------------------


def test_peers_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("peers", captured))

    result = p.peers("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/stock-peers"
    assert req.url.params.get("symbol") == "NVDA"
    assert result == _load_fixture("peers")


# --- analyst estimates ----------------------------------------------------


def test_analyst_estimates_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("analyst_estimates", captured))

    result = p.analyst_estimates("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/analyst-estimates"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("period") == "annual"
    assert req.url.params.get("limit") == "10"
    assert result == _load_fixture("analyst_estimates")


# --- insider trades --------------------------------------------------------


def test_insider_trades_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("insider_trades", captured))

    result = p.insider_trades("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/insider-trading/search"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("limit") == "200"
    assert result == _load_fixture("insider_trades")


# --- institutional holders (13F) --------------------------------------------


def test_institutional_holders_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("institutional_holders", captured))

    result = p.institutional_holders("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/institutional-ownership/extract-analytics/holder"
    assert req.url.params.get("symbol") == "NVDA"
    assert result == _load_fixture("institutional_holders")


# --- earnings calendar -------------------------------------------------------


def test_earnings_calendar_url_and_params(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("earnings_calendar", captured))

    result = p.earnings_calendar("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/earnings"
    assert req.url.params.get("symbol") == "NVDA"
    assert req.url.params.get("limit") == "40"
    assert result == _load_fixture("earnings_calendar")


# --- caching: distinct cache keys per method --------------------------------


def test_the_same_endpoint_with_different_params_does_not_collide(tmp_path):
    """The cache key used to be the caller's label alone, so every request
    to one endpoint shared a single entry whatever it asked for. Raising
    the annual-history limit from 6 to 11 changed nothing: the six-row
    response came back from cache while the API served eleven."""
    calls = []

    def handler(request):
        calls.append(request.url.params.get("limit"))
        return httpx.Response(200, json=_load_fixture("income_annual"))

    cache = Cache(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = FMPProvider(Settings(fmp_api_key="testkey"), cache, client=client)

    p.income_annual("NVDA", limit=6)
    p.income_annual("NVDA", limit=11)

    assert calls == ["6", "11"], "the second request must reach the network"


def test_the_api_key_never_reaches_a_cache_key(tmp_path):
    """Keys are written to disk as filenames, and a rotated key must not
    invalidate every cached response."""
    a = _key("income_annual", symbol="NVDA", limit=6, apikey="secret-one")
    b = _key("income_annual", symbol="NVDA", limit=6, apikey="secret-two")
    assert a == b
    assert "secret" not in a


def test_methods_use_distinct_cache_keys(tmp_path):
    """Each data type must cache under its own key so refetching one type
    doesn't clobber or shadow another."""
    cache = Cache(tmp_path)

    def handler(request):
        path = request.url.path
        if "profile" in path:
            return httpx.Response(200, json=_load_fixture("profile"))
        if "income-statement" in path:
            return httpx.Response(200, json=_load_fixture("income_annual"))
        return httpx.Response(200, json=[])

    settings = Settings(fmp_api_key="testkey")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = FMPProvider(settings, cache, client=client)

    p.profile("NVDA")
    p.income_annual("NVDA")

    # Keys are qualified by the request parameters, so two calls to the
    # same endpoint with different params no longer collide. Read them
    # back through the same qualifier the provider used.
    profile_key = _key("profile", symbol="NVDA")
    income_key = _key("income_annual", symbol="NVDA", period="annual", limit=6)
    assert cache.get("NVDA", profile_key) == _load_fixture("profile")
    assert cache.get("NVDA", income_key) == _load_fixture("income_annual")
    assert profile_key != income_key


def test_get_json_serves_from_cache_without_hitting_transport(tmp_path):
    cache = Cache(tmp_path)
    # Seed under the qualified key the provider will look up.
    cache.put("NVDA", _key("profile", symbol="NVDA"), _load_fixture("profile"))

    def handler(request):
        raise AssertionError("transport should not be called on cache hit")

    settings = Settings(fmp_api_key="testkey")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = FMPProvider(settings, cache, client=client)

    result = p.profile("NVDA")
    assert result == _load_fixture("profile")


def test_key_executives_url_and_params(tmp_path):
    """Backs CLAUDE.md's mandatory report item 4 (who runs the company)."""
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(200, json=[{"name": "Jane Doe", "title": "CEO", "pay": 1000000}])

    p = _make_provider(tmp_path, handler)
    result = p.key_executives("NVDA")

    req = captured["request"]
    assert req.url.path == "/stable/key-executives"
    assert req.url.params.get("symbol") == "NVDA"
    assert result[0]["title"] == "CEO"


def test_key_executives_none_without_api_key(tmp_path):
    def handler(request):
        raise AssertionError("transport should not be called when unavailable")

    p = _make_provider(tmp_path, handler, fmp_api_key=None)
    assert p.key_executives("NVDA") is None


def test_el_cierre_ajustado_se_fusiona_por_FECHA(tmp_path):
    """`historical-price-eod/full` no devuelve `adjClose` -- verificado
    contra la respuesta real de FMP-- asi que `adj_close` caia SIEMPRE al
    cierre crudo.

    Los splits no eran el problema: el crudo ya viene ajustado (NVDA el
    2024-06-07, tres dias antes de su split 10:1, cotiza a 120,89 y no a los
    ~1.208 que valia). Los dividendos si: KO a un ano daba 70,46 crudo
    contra 68,53 ajustado, un 2,7% que sesgaba momentum y fuerza relativa en
    contra de quien paga dividendo.
    """
    def handler(request):
        if request.url.path.endswith("/dividend-adjusted"):
            return httpx.Response(200, json=[
                {"date": "2026-07-16", "adjClose": 98.0},
                {"date": "2026-07-15", "adjClose": 97.0},
            ])
        return httpx.Response(200, json=[
            {"date": "2026-07-16", "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.0, "volume": 1},
            {"date": "2026-07-15", "open": 99.0, "high": 100.0, "low": 98.0,
             "close": 99.0, "volume": 1},
        ])

    p = _make_provider(tmp_path, handler)
    barras = p.ohlcv_daily("KO", years=1, today=date(2026, 7, 16))

    por_fecha = {b["date"]: b for b in barras}
    assert por_fecha["2026-07-16"]["adjClose"] == 98.0
    assert por_fecha["2026-07-15"]["adjClose"] == 97.0
    # El OHLC crudo NO se toca: gaps y ATR se miden sobre el precio al que
    # la accion cotizo de verdad.
    assert por_fecha["2026-07-16"]["close"] == 100.0
    assert por_fecha["2026-07-16"]["high"] == 101.0


def test_una_fecha_sin_ajustado_se_queda_sin_adjClose_y_no_se_inventa(tmp_path):
    def handler(request):
        if request.url.path.endswith("/dividend-adjusted"):
            return httpx.Response(200, json=[{"date": "2026-07-16", "adjClose": 98.0}])
        return httpx.Response(200, json=[
            {"date": "2026-07-16", "open": 1.0, "high": 1.0, "low": 1.0, "close": 100.0, "volume": 1},
            {"date": "2026-07-15", "open": 1.0, "high": 1.0, "low": 1.0, "close": 99.0, "volume": 1},
        ])

    p = _make_provider(tmp_path, handler)
    por_fecha = {b["date"]: b for b in p.ohlcv_daily("KO", years=1, today=date(2026, 7, 16))}
    assert por_fecha["2026-07-16"]["adjClose"] == 98.0
    assert "adjClose" not in por_fecha["2026-07-15"]


def test_si_la_serie_ajustada_falla_se_devuelven_las_crudas(tmp_path):
    """Una serie que no llega no puede costar las barras crudas: el consumidor
    cae al cierre crudo, que es peor pero no falso."""
    def handler(request):
        if request.url.path.endswith("/dividend-adjusted"):
            return httpx.Response(500)
        return httpx.Response(200, json=[
            {"date": "2026-07-16", "open": 1.0, "high": 1.0, "low": 1.0, "close": 100.0, "volume": 1}])

    p = _make_provider(tmp_path, handler)
    barras = p.ohlcv_daily("KO", years=1, today=date(2026, 7, 16))
    assert len(barras) == 1
    assert barras[0]["close"] == 100.0
