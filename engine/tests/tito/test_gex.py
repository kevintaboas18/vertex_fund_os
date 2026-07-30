"""Port de `web/lib/gex.test.ts` (13 casos)."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from wbj.tito.black_scholes import bs_gamma
from wbj.tito.gex import FALLBACK_IV, TradeLite, estimate_iv, gex_analysis
from wbj.tito.structure import ChainRow

# now fijo para DTE determinista.
NOW = datetime(2026, 7, 23, 15, 0, 0, tzinfo=timezone.utc)

CLOSES = [100 + math.sin(i) * 2 for i in range(22)]


def row(strike: float, type_: str, oi: int, exp: str = "2026-08-21") -> ChainRow:
    return ChainRow(
        contract_type=type_,  # type: ignore[arg-type]
        expiration=exp,
        strike=strike,
        open_interest=oi,
        volume=0,
        notional_value=strike * oi * 100,
    )


def analyze(rows: list[ChainRow], **extra: Any):
    kwargs: dict[str, Any] = {"closes": CLOSES, "spot": 100, "now": NOW}
    kwargs.update(extra)
    return gex_analysis(rows, **kwargs)


class TestBsGamma:
    def test_es_cero_con_insumos_invalidos(self):
        assert bs_gamma(0, 100, 0.1, 0.4) == 0
        assert bs_gamma(100, 100, 0, 0.4) == 0
        assert bs_gamma(100, 100, 0.1, 0) == 0

    def test_pico_cerca_del_dinero(self):
        atm = bs_gamma(100, 100, 0.1, 0.4)
        otm = bs_gamma(100, 130, 0.1, 0.4)
        assert atm > 0
        assert atm > otm


class TestEstimateIv:
    def test_cae_al_fallback_con_pocas_barras(self):
        assert estimate_iv([100, 101]) == FALLBACK_IV
        assert estimate_iv([]) == FALLBACK_IV

    def test_una_serie_mas_volatil_da_iv_mas_alta(self):
        calm = [100 + i * 0.05 for i in range(22)]
        wild = [100 * (1 + (0.04 if i % 2 == 0 else -0.04)) for i in range(22)]
        assert estimate_iv(wild) > estimate_iv(calm)


class TestGexAnalysis:
    def test_devuelve_vacio_sin_filas_o_sin_spot(self):
        assert analyze([]).nodes == []
        assert gex_analysis([row(100, "call", 1000)], CLOSES, 0, NOW).nodes == []

    def test_calls_dominan_regimen_positivo(self):
        a = analyze([row(100, "call", 5000), row(100, "put", 500)])
        assert a.total_net_gex > 0
        assert a.regime == "positive"
        assert a.nodes[0].side == "call"

    def test_puts_dominan_regimen_negativo(self):
        a = analyze([row(100, "call", 500), row(100, "put", 5000)])
        assert a.total_net_gex < 0
        assert a.regime == "negative"

    def test_el_nodo_principal_es_el_de_mayor_concentracion(self):
        a = analyze(
            [row(95, "call", 800), row(100, "call", 9000), row(105, "call", 600)]
        )
        assert a.king_strike == 100
        assert a.direction == "flat"

    def test_ignora_strikes_lejanos_oi_cero_y_expirados(self):
        a = analyze(
            [
                row(100, "call", 1000),
                row(200, "call", 100_000),  # lejano
                row(101, "call", 0),  # sin OI
                row(99, "put", 1000, "2020-01-01"),  # expirado
            ]
        )
        assert sorted(n.strike for n in a.nodes) == [100]

    def test_detecta_la_zona_de_inversion_gamma(self):
        a = analyze([row(95, "put", 8000), row(105, "call", 8000)])
        assert a.flip_strike is not None
        assert 95 < a.flip_strike < 105

    def test_el_premium_de_trades_sube_la_concentracion_de_su_strike(self):
        rows = [row(98, "call", 5000), row(102, "call", 5000)]
        with_flow = analyze(
            rows,
            trades=[TradeLite(strike=102, type="call", premium=5_000_000, gamma=0.05)],
        )
        c98 = next(n for n in with_flow.nodes if n.strike == 98).concentration
        c102 = next(n for n in with_flow.nodes if n.strike == 102).concentration
        assert c102 > c98
        # con la actividad real concentrada en 102, ese es el nodo principal
        assert with_flow.king_strike == 102

    def test_propaga_la_bandera_de_baja_liquidez(self):
        assert analyze([row(100, "call", 1000)], low_liquidity=True).low_liquidity is True

    def test_confianza_en_cero_a_cien(self):
        a = analyze(
            [row(100, "call", 5000), row(105, "put", 2000)],
            conviction_score=8,
            structure_score=7,
        )
        assert 0 <= a.confidence <= 100
