"""Port de `web/lib/blackScholes.test.ts` (9 casos)."""

from __future__ import annotations

import math

import pytest

from wbj.tito.black_scholes import RISK_FREE, bs_delta, bs_gamma, bs_price, implied_vol


class TestBsPrice:
    def test_cumple_la_paridad_put_call(self):
        S, K, T, iv = 100, 95, 0.25, 0.4
        call = bs_price(S, K, T, iv, "call")
        put = bs_price(S, K, T, iv, "put")
        assert call - put == pytest.approx(S - K * math.exp(-RISK_FREE * T), abs=1e-6)

    def test_devuelve_cero_con_insumos_invalidos(self):
        assert bs_price(0, 95, 0.25, 0.4, "put") == 0
        assert bs_price(100, 95, 0, 0.4, "put") == 0
        assert bs_price(100, 95, 0.25, 0, "put") == 0


class TestBsDelta:
    def test_el_delta_de_un_put_esta_entre_menos_uno_y_cero(self):
        d = bs_delta(100, 95, 0.1, 0.4, "put")
        assert -1 < d < 0

    def test_put_muy_otm_cerca_de_cero_y_muy_itm_cerca_de_menos_uno(self):
        assert abs(bs_delta(100, 50, 0.1, 0.4, "put")) < 0.02
        assert bs_delta(100, 200, 0.1, 0.4, "put") < -0.95

    def test_delta_call_menos_delta_put_es_uno(self):
        c = bs_delta(100, 95, 0.25, 0.4, "call")
        p = bs_delta(100, 95, 0.25, 0.4, "put")
        assert c - p == pytest.approx(1, abs=1e-6)


class TestBsGamma:
    def test_gamma_es_positiva_y_maxima_en_el_dinero(self):
        atm = bs_gamma(100, 100, 0.25, 0.4)
        otm = bs_gamma(100, 140, 0.25, 0.4)
        assert atm > 0
        assert atm > otm

    def test_devuelve_cero_con_insumos_invalidos(self):
        assert bs_gamma(100, 100, 0, 0.4) == 0
        assert bs_gamma(0, 100, 0.25, 0.4) == 0


class TestImpliedVol:
    def test_ida_y_vuelta_precio_sigma_precio(self):
        S, K, T, iv = 100, 92, 30 / 365, 0.55
        price = bs_price(S, K, T, iv, "put")
        back = implied_vol(price, S, K, T, "put")
        assert back is not None
        assert back == pytest.approx(iv, abs=1e-4)

    def test_none_si_viola_el_limite_superior_de_no_arbitraje(self):
        # Un put nunca puede valer mas que el strike descontado.
        assert implied_vol(200, 100, 92, 30 / 365, "put") is None

    def test_none_si_el_precio_esta_bajo_el_intrinseco(self):
        assert implied_vol(1, 100, 120, 30 / 365, "put") is None

    def test_none_con_precio_no_positivo_o_t_cero(self):
        assert implied_vol(0, 100, 92, 0.1, "put") is None
        assert implied_vol(2, 100, 92, 0, "put") is None
