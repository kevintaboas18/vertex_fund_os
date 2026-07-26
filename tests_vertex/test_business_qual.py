"""Inputs cualitativos del negocio SIN LLM.

Victor lee el filing con Python (XBRL companyfacts). Estas pruebas fijan que la
ruta determinista no inventa nada: lo que no venga de una fuente reportada queda
NOT_SCORABLE, como manda Cerebro/shared/MISSING_DATA_POLICY.md.
"""
import importlib.util
import sys
import pathlib

import httpx
import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def api():
    spec = importlib.util.spec_from_file_location("vapi", _ROOT / "vertex_api.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vapi"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload
    def json(self):
        return self._payload


def _patch_fmp(api, monkeypatch, resp):
    # `_fmp_segment_shares` hace `import httpx` dentro de la funcion, asi que el
    # parche va sobre el modulo httpx (mismo objeto que resuelve ese import).
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: resp)


# ── _norm_shares ──────────────────────────────────────────────────────────────

def test_norm_shares_accepts_fractions(api):
    assert api._norm_shares([0.5, 0.3, 0.2]) == [0.5, 0.3, 0.2]

def test_norm_shares_converts_percentages(api):
    # "40%, 35%, 25%" llega como 40/35/25 → se divide toda la lista entre 100
    assert api._norm_shares([40, 35, 25]) == [0.4, 0.35, 0.25]

def test_norm_shares_drops_impossible_values(api):
    # >100 no es ni fracción ni porcentaje válido → fuera, no se recorta
    assert api._norm_shares([150.0]) is None

def test_norm_shares_empty_is_none(api):
    assert api._norm_shares([]) is None
    assert api._norm_shares(None) is None
    assert api._norm_shares(["x", None]) is None


# ── segment_shares desde FMP ──────────────────────────────────────────────────

def test_segment_shares_from_nested_data(api, monkeypatch):
    _patch_fmp(api, monkeypatch, _Resp(200, [
        {"date": "2026-01-25", "symbol": "NVDA",
         "data": {"Data Center": 750.0, "Gaming": 200.0, "Auto": 50.0}},
        {"date": "2025-01-26", "symbol": "NVDA", "data": {"Data Center": 400.0}},
    ]))
    assert api._fmp_segment_shares("NVDA") == [0.75, 0.20, 0.05]

def test_segment_shares_from_flat_shape(api, monkeypatch):
    _patch_fmp(api, monkeypatch, _Resp(200, [
        {"date": "2026-01-25", "symbol": "NVDA", "reportedCurrency": "USD",
         "Data Center": 900.0, "Gaming": 100.0},
    ]))
    assert api._fmp_segment_shares("NVDA") == [0.9, 0.1]

def test_segment_shares_plan_limited_is_not_scorable(api, monkeypatch):
    """402/403 = fuera del plan. N/S, nunca una estimación."""
    for code in (402, 403):
        _patch_fmp(api, monkeypatch, _Resp(code))
        assert api._fmp_segment_shares("NVDA") is None

def test_segment_shares_without_key_is_not_scorable(api, monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    assert api._fmp_segment_shares("NVDA") is None

def test_segment_shares_zero_total_is_not_scorable(api, monkeypatch):
    _patch_fmp(api, monkeypatch, _Resp(200, [{"date": "2026-01-25", "data": {"A": 0.0}}]))
    assert api._fmp_segment_shares("NVDA") is None

def test_segment_shares_network_error_is_not_scorable(api, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    def _boom(*a, **k):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(httpx, "get", _boom)
    assert api._fmp_segment_shares("NVDA") is None


# ── el overlay completo ───────────────────────────────────────────────────────

def test_deterministic_layer_never_calls_an_llm(api, monkeypatch):
    """La CAPA 1 sale de FMP/XBRL por codigo. Que exista una capa LLM aparte no
    la autoriza a llamar a Anthropic para resolver `segment_shares`."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-used")
    anthropic = pytest.importorskip("anthropic")
    def _boom(*a, **k):
        raise AssertionError("la capa determinista llamo a Anthropic")
    monkeypatch.setattr(anthropic, "Anthropic", _boom)
    _patch_fmp(api, monkeypatch, _Resp(200, [{"date": "2026-01-25", "data": {"A": 60.0, "B": 40.0}}]))
    assert api._fmp_segment_shares("NVDA") == [0.6, 0.4]


def test_orchestrator_returns_deterministic_result(api, monkeypatch):
    """Con la capa LLM apagada, queda exactamente lo que saco el codigo."""
    _patch_fmp(api, monkeypatch, _Resp(200, [{"date": "2026-01-25", "data": {"A": 60.0, "B": 40.0}}]))
    monkeypatch.setattr(api, "_xbrl_recurring_revenue", lambda *a, **k: None)
    monkeypatch.setattr(api, "_wbj_qual_from_10k_llm", lambda *a, **k: {})
    ov = api._wbj_extract_business_qual("NVDA", 1045810, None, revenue_hint=1e9)
    ov.pop("__provenance__", None)
    assert ov == {"segment_shares": [0.6, 0.4]}

def test_overlay_is_empty_when_nothing_is_disclosed(api, monkeypatch):
    """Sin NINGUNA fuente (ni codigo ni LLM) → cero metricas. No se inventa nada."""
    _patch_fmp(api, monkeypatch, _Resp(403))
    monkeypatch.setattr(api, "_xbrl_recurring_revenue", lambda *a, **k: None)
    monkeypatch.setattr(api, "_wbj_qual_from_10k_llm", lambda *a, **k: {})
    ov = api._wbj_extract_business_qual("NVDA", 1045810, None)
    ov.pop("__provenance__", None)          # metadato de linaje, no una metrica
    assert ov == {}

def test_overlay_never_emits_customer_concentration(api, monkeypatch):
    """BUS-CONC-003 / MISSING_DATA_POLICY: prohibido inferir concentracion."""
    _patch_fmp(api, monkeypatch, _Resp(200, [{"date": "2026-01-25", "data": {"A": 90.0, "B": 10.0}}]))
    monkeypatch.setattr(api, "_xbrl_recurring_revenue", lambda *a, **k: None)
    monkeypatch.setattr(api, "_wbj_qual_from_10k_llm", lambda *a, **k: {})
    ov = api._wbj_extract_business_qual("NVDA", 1045810, None)
    # La CAPA DETERMINISTA nunca la deriva: companyfacts no trae el eje de cliente.
    # Solo el LLM puede traerla, y unicamente si el filing da un % especifico.
    assert "largest_customer_share" not in ov
    assert "customer_shares" not in ov
