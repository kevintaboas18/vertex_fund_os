"""Port de `web/lib/risk.test.ts` (22 casos)."""

from __future__ import annotations

import math
from typing import Any

import pytest

from wbj.tito.flow import (UNUSUAL_TRADE_THRESHOLD, FlowFlags, FlowRow,
                           TradeScores)
from wbj.tito.risk import (
    IDEA_UNUSUAL_THRESHOLD,
    MAX_THETA_PCT_DAILY,
    MIN_DTE,
    MONEYNESS_CAP,
    THETA_BUDGET_PCT,
    RiskProfile,
    budgets_of,
    passes_quality_filter,
    size_flow,
    within_moneyness,
)

PROFILE = RiskProfile(account_size=10_000, tolerance_pct=4)
#: Tolerancia por encima de la banda de theta — ahí es donde el theta puede frenar.
AGRESIVO = RiskProfile(account_size=10_000, tolerance_pct=10)


def row(**p: Any) -> FlowRow:
    base: dict[str, Any] = dict(
        id=1, symbol="NVDA260919C00220000", underlying="NVDA", type="call",
        strike=220, expiration="2026-09-19", dte=57, price=2.1, size=100,
        side="ask", aggression="ask", asset_price=210, bid=2.0, ask=2.2,
        premium=210_000, delta=0.55, gamma=0.02, theta=-0.08, vega=0.1,
        theta_pct_daily=3.81, iv=0.5, open_interest=1000, volume=500, score=80,
        sentiment="bullish", timestamp="2026-07-24T14:00:00Z",
        condition_code=None, condition_name=None,
        flags=FlowFlags(), scores=TradeScores(),
        unusual=True, interesting=True, expiry_status="vigente",
    )
    base.update(p)
    return FlowRow(**base)


class TestBudgetsOf:
    def test_prima_del_slider_y_theta_de_la_banda(self):
        b = budgets_of(PROFILE)
        assert b.premium == 400  # 4% de $10,000
        assert b.theta == 500  # 5% fijo (banda de Inusualidad)

    def test_el_presupuesto_de_theta_no_depende_del_slider(self):
        assert budgets_of(PROFILE).theta == budgets_of(AGRESIVO).theta

    def test_nunca_negativo_ni_nan_con_entradas_basura(self):
        for p in (
            RiskProfile(account_size=-5, tolerance_pct=4),
            RiskProfile(account_size=float("nan"), tolerance_pct=4),
            RiskProfile(account_size=10_000, tolerance_pct=-2),
        ):
            b = budgets_of(p)
            assert b.premium >= 0
            assert math.isfinite(b.premium)
            assert math.isfinite(b.theta)


class TestPassesQualityFilter:
    def test_acepta_un_contrato_sano(self):
        assert passes_quality_filter(row()).ok is True

    def test_descarta_la_loteria(self):
        r = passes_quality_filter(row(theta_pct_daily=7.8))
        assert r.ok is False
        assert r.reason == "theta_alto"

    def test_acepta_justo_en_el_limite_y_rechaza_apenas_encima(self):
        assert passes_quality_filter(row(theta_pct_daily=MAX_THETA_PCT_DAILY)).ok is True
        assert passes_quality_filter(row(theta_pct_daily=MAX_THETA_PCT_DAILY + 0.01)).ok is False

    def test_descarta_si_no_hay_theta_no_se_estima(self):
        r = passes_quality_filter(row(theta_pct_daily=None))
        assert r.ok is False
        assert r.reason == "sin_theta"

    def test_descarta_vencidos_o_que_expiran_hoy(self):
        assert passes_quality_filter(row(expiry_status="expirado")).reason == "vencido"
        assert passes_quality_filter(row(expiry_status="expira_hoy")).reason == "vencido"

    def test_descarta_lo_que_expira_demasiado_pronto(self):
        assert passes_quality_filter(row(dte=MIN_DTE - 1)).reason == "vencido"
        assert passes_quality_filter(row(dte=MIN_DTE)).ok is True


class TestSizeFlow:
    def test_el_caso_trabajado_del_plan(self):
        s = size_flow(row(), PROFILE, 20)
        # prima: $400/$210 = 1 · theta: $500/$160 = 3 -> gana el menor
        assert s.max_contracts == 1
        assert s.binding == "prima"
        assert s.cost_per_contract == 210
        assert s.theta_burn_per_contract == pytest.approx(160, abs=1e-5)
        assert s.total_cost == 210
        assert s.cost_pct_of_account == pytest.approx(2.1, abs=1e-5)

    def test_con_tolerancia_alta_el_theta_si_frena(self):
        s = size_flow(row(), AGRESIVO, 20)
        # prima: $1,000/$210 = 4 · theta: $500/$160 = 3 -> frena el theta
        assert s.max_contracts == 3
        assert s.binding == "theta"
        assert s.total_burn == pytest.approx(480, abs=1e-5)
        assert s.burn_pct_of_account == pytest.approx(4.8, abs=1e-5)

    def test_la_quema_nunca_supera_el_costo_y_marca_fully_decays(self):
        s = size_flow(row(price=1, theta=-0.05, theta_pct_daily=5), PROFILE, 30)
        # sin tope serian 0.05*100*30 = $150 por un contrato que cuesta $100
        assert s.theta_burn_per_contract == 100
        assert s.fully_decays is True

    def test_los_dias_de_quema_se_recortan_al_vencimiento(self):
        corto = size_flow(row(dte=8), PROFILE, 20)
        largo = size_flow(row(dte=57), PROFILE, 20)
        assert corto.burn_days == 8
        assert largo.burn_days == 20
        assert corto.theta_burn_per_contract < largo.theta_burn_per_contract

    def test_un_horizonte_mas_largo_aprieta_el_limite(self):
        barato = dict(price=0.4, theta=-0.01, theta_pct_daily=2.5)
        a = size_flow(row(**barato), AGRESIVO, 10)
        b = size_flow(row(**barato), AGRESIVO, 30)
        assert b.max_contracts < a.max_contracts

    def test_cuenta_que_no_alcanza_da_cero_sin_binding_falso(self):
        s = size_flow(row(price=50), RiskProfile(account_size=1_000, tolerance_pct=4), 20)
        assert s.max_contracts == 0
        assert s.total_cost == 0

    def test_entradas_degeneradas_no_producen_nan(self):
        for p in (
            RiskProfile(account_size=0, tolerance_pct=4),
            RiskProfile(account_size=10_000, tolerance_pct=0),
            RiskProfile(account_size=float("nan"), tolerance_pct=float("nan")),
        ):
            s = size_flow(row(), p, 20)
            assert s.max_contracts == 0
            assert math.isfinite(s.total_cost)
            assert math.isfinite(s.burn_pct_of_account)

    def test_precio_cero_no_revienta_la_division(self):
        s = size_flow(row(price=0), PROFILE, 20)
        assert s.max_contracts == 0
        assert math.isfinite(s.cost_per_contract)


class TestSalvaguardas:
    def test_cadena_iliquida_bloquea_el_sizing_por_completo(self):
        s = size_flow(row(), PROFILE, 20, low_liquidity=True)
        assert s.blocked["reason"] == "iliquidez"
        assert s.max_contracts == 0

    def test_la_iliquidez_manda_aunque_el_contrato_sea_impecable(self):
        bueno = dict(price=0.2, theta=-0.001, theta_pct_daily=0.5)
        assert size_flow(row(**bueno), PROFILE, 20).max_contracts > 0
        assert size_flow(row(**bueno), PROFILE, 20, low_liquidity=True).max_contracts == 0

    def test_lo_que_no_pasa_capa_uno_llega_bloqueado(self):
        s = size_flow(row(theta_pct_daily=9), PROFILE, 20)
        assert s.blocked["reason"] == "theta_alto"
        assert s.max_contracts == 0

    def test_sin_theta_real_no_se_dimensiona(self):
        s = size_flow(row(theta_pct_daily=None, theta=0), PROFILE, 20)
        assert s.blocked["reason"] == "sin_theta"
        assert s.max_contracts == 0


class TestWithinMoneyness:
    """`withinMoneyness — contratos más cercanos` (6 casos suyos)."""

    def test_acepta_un_strike_pegado_al_precio_atm(self):
        assert within_moneyness(row(strike=210, asset_price=210)) is True

    def test_acepta_dentro_de_la_banda_y_rechaza_justo_fuera(self):
        # banda por defecto ±25% sobre spot 200 → hasta 250 dentro, 251 fuera
        assert within_moneyness(row(strike=250, asset_price=200)) is True
        assert within_moneyness(row(strike=251, asset_price=200)) is False

    def test_rechaza_lo_muy_otm_loteria_barata_y_lo_muy_itm_caro(self):
        assert within_moneyness(row(strike=400, asset_price=200)) is False  # +100%
        assert within_moneyness(row(strike=80, asset_price=200)) is False  # −60%

    def test_respeta_una_banda_personalizada(self):
        assert within_moneyness(row(strike=220, asset_price=200), 0.05) is False
        assert within_moneyness(row(strike=220, asset_price=200), 0.15) is True

    def test_con_datos_faltantes_no_filtra_la_cercania_es_preferencia(self):
        assert within_moneyness(row(strike=None)) is True
        assert within_moneyness(row(asset_price=0)) is True

    def test_moneyness_cap_es_una_fraccion_razonable(self):
        assert MONEYNESS_CAP > 0
        assert MONEYNESS_CAP <= 1


def test_theta_budget_esta_dentro_de_la_banda_del_documento():
    assert 3 <= THETA_BUDGET_PCT <= 5


def test_el_umbral_del_screener_no_toca_el_institucional():
    """Su commit lo dice explícito: el 5 es del screener, el 7 sigue en `flow`."""
    assert IDEA_UNUSUAL_THRESHOLD == 5
    assert UNUSUAL_TRADE_THRESHOLD == 7
    assert IDEA_UNUSUAL_THRESHOLD < UNUSUAL_TRADE_THRESHOLD


def test_min_dte_deja_pasar_semanales_pero_no_el_0dte():
    """`MIN_DTE = 2`: el near-term entra; el que vence hoy lo tumba `expiry_status`."""
    assert MIN_DTE == 2
    assert passes_quality_filter(row(dte=2)).ok is True
    assert passes_quality_filter(row(dte=1)).reason == "vencido"
    assert passes_quality_filter(row(dte=0, expiry_status="expira_hoy")).reason == "vencido"
