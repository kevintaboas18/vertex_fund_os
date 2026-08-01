"""A paywalled endpoint is not the same answer as an empty one.

FMP's institutional-ownership returns 402 and FinnHub's eps- and
revenue-estimate return 403 on the plans in use. `get_json` logged the
status and returned `None` — which is exactly what a company with no data
returns — so the analysis lost inputs and the report had no way to say
whether a figure was missing or merely unpaid for.
"""

from __future__ import annotations

import httpx
import pytest

from wbj.providers.base import Provider
from wbj.providers.cache import Cache
from wbj.report import _entitlement_gaps


class _Settings:
    def __init__(self, tmp_path):
        self.cache_dir = tmp_path


def _provider(tmp_path, status: int) -> Provider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Provider(_Settings(tmp_path), Cache(tmp_path), client=client)


@pytest.mark.parametrize("status", [401, 402, 403])
def test_an_entitlement_refusal_is_remembered(tmp_path, status):
    p = _provider(tmp_path, status)
    assert p.get_json("https://x.test/estimates", {}, "estimates", "NVDA") is None
    assert p.blocked_endpoints == {"estimates": status}


@pytest.mark.parametrize("status", [404, 400, 422])
def test_an_ordinary_client_error_is_not_an_entitlement_problem(tmp_path, status):
    """A 404 means this company has none, which is a different sentence and
    must not be reported as an unpaid endpoint."""
    p = _provider(tmp_path, status)
    assert p.get_json("https://x.test/estimates", {}, "estimates", "NVDA") is None
    assert p.blocked_endpoints == {}


def test_a_successful_call_records_nothing(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"ok": True}])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = Provider(_Settings(tmp_path), Cache(tmp_path), client=client)
    assert p.get_json("https://x.test/ok", {}, "ok", "NVDA") == [{"ok": True}]
    assert p.blocked_endpoints == {}


# --- and it has to reach the reader ---------------------------------------


class _FakeProviders:
    def __init__(self, **blocked):
        for name in ("fmp", "edgar", "finnhub", "fred"):
            setattr(self, name, type("P", (), {
                "blocked_endpoints": blocked.get(name, {})})())


def test_the_gap_names_the_provider_the_endpoint_and_the_status():
    gaps = _entitlement_gaps(_FakeProviders(
        fmp={"institutional_holders": 402},
        finnhub={"estimates": 403, "revenue_estimates": 403}))

    assert len(gaps) == 3
    blob = " ".join(gaps)
    assert "fmp: ENDPOINT_NOT_IN_PLAN (institutional_holders, HTTP 402)" in blob
    assert "finnhub: ENDPOINT_NOT_IN_PLAN (estimates, HTTP 403)" in blob
    assert "finnhub: ENDPOINT_NOT_IN_PLAN (revenue_estimates, HTTP 403)" in blob
    # And it says which way the analysis erred, because "unscored" and
    # "estimated" are the distinction the whole engine rests on.
    assert "stay unscored rather than being estimated" in gaps[0]


def test_the_cache_key_hash_is_not_shown_to_the_reader():
    """Cache keys carry a parameter hash (`estimates_d07de7c492a6`). The
    reader wants the endpoint, not the cache bookkeeping."""
    gaps = _entitlement_gaps(_FakeProviders(finnhub={"estimates_d07de7c492a6": 403}))
    assert "(estimates, HTTP 403)" in gaps[0]
    assert "d07de7c492a6" not in gaps[0]


def test_one_endpoint_blocked_under_two_parameter_sets_is_reported_once():
    """The same endpoint hit for two tickers produces two cache keys and
    one fact about the plan."""
    gaps = _entitlement_gaps(_FakeProviders(
        finnhub={"estimates_aaaaaaaaaaaa": 403, "estimates_bbbbbbbbbbbb": 403}))
    assert len(gaps) == 1


def test_nothing_blocked_reports_nothing():
    assert _entitlement_gaps(_FakeProviders()) == []
