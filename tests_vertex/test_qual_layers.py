"""Las dos capas de inputs cualitativos y su precedencia.

MISSING_DATA_POLICY.md define un arbol de 5 pasos. El paso 4 (proxy registrado)
existe y no puede saltarse: hacerlo manda a NOT_SCORABLE datos que si se pueden
obtener. Estas pruebas fijan que:
  - la capa determinista corre siempre y gana sobre el LLM;
  - un proxy se declara COMO proxy, nunca como dato reportado;
  - sin key de LLM el analisis sigue, con lo determinista intacto.
"""
import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def api():
    spec = importlib.util.spec_from_file_location("vapi_layers", _ROOT / "vertex_api.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vapi_layers"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload
    def json(self):
        return self._payload


def _facts(tag_cur, tag_non, cur, non):
    def _u(v):
        return {"units": {"USD": [{"val": v, "form": "10-K", "end": "2026-01-25"}]}}
    return {"facts": {"us-gaap": {tag_cur: _u(cur), tag_non: _u(non)}}}


# ── proxy registrado de ingreso recurrente ────────────────────────────────────

def test_recurring_revenue_proxy_from_asc606(api, monkeypatch):
    monkeypatch.setattr(api, "_edgar_companyfacts_for", lambda cik, s: _facts(
        "ContractWithCustomerLiabilityCurrent", "ContractWithCustomerLiabilityNoncurrent",
        30.0, 20.0), raising=False)
    got = api._xbrl_recurring_revenue(1045810, revenue_hint=100.0, settings=None)
    assert got is not None
    total, tag = got
    assert total == 50.0
    assert "ContractWithCustomerLiability" in tag


def test_recurring_revenue_falls_back_to_pre_asc606_tags(api, monkeypatch):
    monkeypatch.setattr(api, "_edgar_companyfacts_for", lambda cik, s: _facts(
        "DeferredRevenueCurrent", "DeferredRevenueNoncurrent", 10.0, 5.0), raising=False)
    total, tag = api._xbrl_recurring_revenue(1045810, revenue_hint=100.0, settings=None)
    assert total == 15.0
    assert "DeferredRevenue" in tag


def test_proxy_rejected_when_it_exceeds_revenue(api, monkeypatch):
    """Saldo diferido > ingreso anual: el supuesto economico no se sostiene."""
    monkeypatch.setattr(api, "_edgar_companyfacts_for", lambda cik, s: _facts(
        "ContractWithCustomerLiabilityCurrent", "ContractWithCustomerLiabilityNoncurrent",
        900.0, 300.0), raising=False)
    assert api._xbrl_recurring_revenue(1045810, revenue_hint=100.0, settings=None) is None


def test_proxy_needs_cik_and_revenue(api):
    assert api._xbrl_recurring_revenue(None, 100.0, None) is None
    assert api._xbrl_recurring_revenue(1045810, None, None) is None
    assert api._xbrl_recurring_revenue(1045810, 0.0, None) is None


# ── precedencia entre capas ───────────────────────────────────────────────────

def test_deterministic_layer_wins_over_llm(api, monkeypatch):
    """Un numero del filing por codigo NO puede ser pisado por uno de un LLM."""
    monkeypatch.setattr(api, "_fmp_segment_shares", lambda t, s=None, axis="product": ([0.7, 0.3] if axis == "product" else None))
    monkeypatch.setattr(api, "_xbrl_recurring_revenue", lambda c, r, s=None: (40.0, "ContractWithCustomerLiability"))
    monkeypatch.setattr(api, "_wbj_qual_from_10k_llm",
                        lambda t, c, s, revenue_hint=None, skip=(): {
                            "recurring_revenue": 999.0,      # el LLM intenta pisar
                            "segment_shares": [0.5, 0.5],    # y aqui tambien
                            "churn": {"lost": 5, "begin_customers": 100}})
    ov = api._wbj_extract_business_qual("NVDA", 1045810, None, revenue_hint=100.0)
    assert ov["recurring_revenue"] == 40.0, "gano el LLM sobre el dato deterministico"
    assert ov["segment_shares"] == [0.7, 0.3]
    assert ov["churn"] == {"lost": 5, "begin_customers": 100}, "el residuo del LLM si entra"


def test_llm_layer_is_told_what_to_skip(api, monkeypatch):
    seen = {}
    monkeypatch.setattr(api, "_fmp_segment_shares", lambda t, s=None, axis="product": ([1.0] if axis == "product" else None))
    monkeypatch.setattr(api, "_xbrl_recurring_revenue", lambda c, r, s=None: (10.0, "X"))
    def _spy(t, c, s, revenue_hint=None, skip=()):
        seen["skip"] = set(skip)
        return {}
    monkeypatch.setattr(api, "_wbj_qual_from_10k_llm", _spy)
    api._wbj_extract_business_qual("NVDA", 1, None, revenue_hint=100.0)
    assert {"segment_shares", "recurring_revenue"} <= seen["skip"]


def test_llm_failure_keeps_deterministic_results(api, monkeypatch):
    """Sin credito/key el analisis NO pierde lo que ya saco por codigo."""
    monkeypatch.setattr(api, "_fmp_segment_shares", lambda t, s=None, axis="product": ([0.6, 0.4] if axis == "product" else None))
    monkeypatch.setattr(api, "_xbrl_recurring_revenue", lambda c, r, s=None: None)
    def _boom(*a, **k):
        raise RuntimeError("credit balance is too low")
    monkeypatch.setattr(api, "_wbj_qual_from_10k_llm", _boom)
    ov = api._wbj_extract_business_qual("NVDA", 1, None, revenue_hint=100.0)
    assert ov["segment_shares"] == [0.6, 0.4]


# ── linaje ────────────────────────────────────────────────────────────────────

def test_proxy_is_declared_as_a_proxy(api, monkeypatch):
    """DATA_POLICY: un proxy no puede leerse igual que un dato reportado."""
    monkeypatch.setattr(api, "_fmp_segment_shares", lambda t, s=None, axis="product": ([1.0] if axis == "product" else None))
    monkeypatch.setattr(api, "_xbrl_recurring_revenue", lambda c, r, s=None: (10.0, "ContractWithCustomerLiability"))
    monkeypatch.setattr(api, "_wbj_qual_from_10k_llm", lambda *a, **k: {})
    prov = api._wbj_extract_business_qual("NVDA", 1, None, revenue_hint=100.0)["__provenance__"]
    assert prov["recurring_revenue"]["proxy"] is True
    assert prov["recurring_revenue"]["evidence_class"] == "A"
    assert prov["segment_shares"]["proxy"] is False
    assert prov["segment_shares"]["evidence_class"] == "R"


def test_provenance_never_leaks_into_the_overlay(api, monkeypatch):
    """__provenance__ es metadato del reporte, no una metrica para Victor."""
    monkeypatch.setattr(api, "_fmp_segment_shares", lambda t, s=None, axis="product": ([1.0] if axis == "product" else None))
    monkeypatch.setattr(api, "_xbrl_recurring_revenue", lambda c, r, s=None: None)
    monkeypatch.setattr(api, "_wbj_qual_from_10k_llm", lambda *a, **k: {})
    ov = api._wbj_extract_business_qual("NVDA", 1, None, revenue_hint=100.0)
    victor_keys = ("recurring_revenue", "largest_customer_share", "customer_shares",
                   "segment_shares", "product_shares", "geographic_shares",
                   "guidance_history", "retention", "churn", "customer_economics")
    assert "__provenance__" not in victor_keys
    assert set(ov) - {"__provenance__"} <= set(victor_keys)


# ── RPO / backlog desde XBRL (MKT-BACK-015) ───────────────────────────────────
#
# ASC 606 obliga a divulgar las obligaciones de desempeno pendientes y la
# taxonomia las etiqueta como `RevenueRemainingPerformanceObligation`. Es
# exactamente la magnitud que pide la formula, publicada por la empresa: capa 1
# determinista, no una pregunta para el juez. Sin ella, la dimension de
# catalizadores seguia NOT_SCORABLE aunque el juez respondiera perfecto.

def _rpo_facts(tag, values):
    return {"facts": {"us-gaap": {tag: {"units": {"USD": [
        {"val": v, "form": "10-K", "end": end, "accn": f"a-{end}"}
        for end, v in values]}}}}}


def test_backlog_series_from_asc606_rpo(api, monkeypatch):
    monkeypatch.setattr(api, "_edgar_companyfacts_for", lambda cik, s: _rpo_facts(
        "RevenueRemainingPerformanceObligation",
        [("2024-01-28", 28.0e9), ("2025-01-26", 34.5e9), ("2026-01-25", 41.2e9)]), raising=False)
    assert api._xbrl_backlog_rpo(1045810, None) == [28.0e9, 34.5e9, 41.2e9]


def test_backlog_falls_back_to_the_alternate_tag(api, monkeypatch):
    monkeypatch.setattr(api, "_edgar_companyfacts_for", lambda cik, s: _rpo_facts(
        "RevenueFromRemainingPerformanceObligation",
        [("2025-01-26", 10.0e9), ("2026-01-25", 12.0e9)]), raising=False)
    assert api._xbrl_backlog_rpo(1045810, None) == [10.0e9, 12.0e9]


def test_backlog_needs_two_closes_to_measure_growth(api, monkeypatch):
    monkeypatch.setattr(api, "_edgar_companyfacts_for", lambda cik, s: _rpo_facts(
        "RevenueRemainingPerformanceObligation", [("2026-01-25", 41.2e9)]), raising=False)
    assert api._xbrl_backlog_rpo(1045810, None) is None


def test_backlog_is_none_when_the_company_does_not_report_rpo(api, monkeypatch):
    """Muchas empresas no divulgan RPO. Esa ausencia es un hecho, no un hueco."""
    monkeypatch.setattr(api, "_edgar_companyfacts_for", lambda cik, s: {"facts": {"us-gaap": {}}},
                        raising=False)
    assert api._xbrl_backlog_rpo(1045810, None) is None


def test_backlog_deduplicates_repeated_closes(api, monkeypatch):
    """Un 10-K puede repetir el mismo cierre; se toma la presentacion mas reciente."""
    monkeypatch.setattr(api, "_edgar_companyfacts_for", lambda cik, s: _rpo_facts(
        "RevenueRemainingPerformanceObligation",
        [("2025-01-26", 34.0e9), ("2025-01-26", 34.5e9), ("2026-01-25", 41.2e9)]), raising=False)
    assert api._xbrl_backlog_rpo(1045810, None) == [34.5e9, 41.2e9]


def test_backlog_needs_a_cik(api):
    assert api._xbrl_backlog_rpo(None, None) is None
