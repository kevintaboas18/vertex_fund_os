"""Port de `web/lib/structure.test.ts` (12 casos)."""

from __future__ import annotations

from typing import Any

from wbj.tito.structure import (
    ChainRow,
    dominant_strikes_score,
    notional_score,
    structure_score,
    volume_over_oi_score,
)


def row(**over: Any) -> ChainRow:
    strike = over.get("strike", 100)
    oi = over.get("open_interest", 1000)
    return ChainRow(
        contract_type=over.get("contract_type", "call"),
        expiration=over.get("expiration", "2026-09-18"),
        strike=strike,
        open_interest=oi,
        volume=over.get("volume", 0),
        notional_value=over.get("notional_value", oi * 100 * strike),
    )


class TestTablasDePuntuacion:
    def test_valor_nocional_promedio(self):
        assert notional_score(2_000_000_000) == 10
        assert notional_score(700_000_000) == 10
        assert notional_score(200_000_000) == 8
        assert notional_score(70_000_000) == 6
        assert notional_score(30_000_000) == 4
        assert notional_score(10_000_000) == 2

    def test_cantidad_de_strikes_dominantes(self):
        assert dominant_strikes_score(6) == 10
        assert dominant_strikes_score(5) == 10
        assert dominant_strikes_score(3) == 8
        assert dominant_strikes_score(1) == 5
        assert dominant_strikes_score(0) == 0

    def test_volumen_sobre_open_interest(self):
        assert volume_over_oi_score(100) == 10
        assert volume_over_oi_score(80) == 8
        assert volume_over_oi_score(50) == 8
        assert volume_over_oi_score(40) == 5
        assert volume_over_oi_score(10) == 2


class TestStructureScore:
    def test_agrupa_el_nocional_por_strike_y_promedia(self):
        # 2 strikes: 100 (OI 1000 -> $10M) y 200 (OI 1000 -> $20M) -> promedio $15M
        s = structure_score(
            [row(strike=100, open_interest=1000), row(strike=200, open_interest=1000)]
        )
        assert s.notional["strike_count"] == 2
        assert s.notional["total"] == 30_000_000
        assert s.notional["avg_per_strike"] == 15_000_000
        assert s.notional["points"] == 2  # <= $25M
        assert s.notional["low_liquidity"] is True

    def test_cuenta_en_cuantos_top_strikes_domina_un_lado(self):
        rows = []
        for strike in (100, 110, 120, 130, 140):
            rows.append(row(strike=strike, contract_type="call", open_interest=1000))
            rows.append(row(strike=strike, contract_type="put", open_interest=50))
        s = structure_score(rows)
        assert s.strikes["considered_count"] == 5
        assert s.strikes["dominant_count"] == 5
        assert s.strikes["points"] == 10
        assert s.strikes["dominant_side"] == "calls"

    def test_un_strike_repartido_mitad_no_cuenta_como_dominado(self):
        rows = [
            row(strike=100, contract_type="call", open_interest=1000),
            row(strike=100, contract_type="put", open_interest=10),
            row(strike=110, contract_type="call", open_interest=500),
            row(strike=110, contract_type="put", open_interest=500),
            row(strike=120, contract_type="call", open_interest=500),
            row(strike=120, contract_type="put", open_interest=500),
        ]
        s = structure_score(rows)
        assert s.strikes["dominant_count"] == 1
        assert s.strikes["points"] == 5

    def test_sin_dominancia_en_ningun_strike_es_cero(self):
        rows = []
        for strike in (100, 110, 120):
            rows.append(row(strike=strike, contract_type="call", open_interest=500))
            rows.append(row(strike=strike, contract_type="put", open_interest=500))
        assert structure_score(rows).strikes["points"] == 0

    def test_identifica_si_dominan_calls_o_puts(self):
        s = structure_score(
            [
                row(strike=100, contract_type="call", open_interest=3000),
                row(strike=110, contract_type="put", open_interest=1000),
            ]
        )
        assert s.strikes["dominant_side"] == "calls"
        assert s.strikes["call_pct"] > s.strikes["put_pct"]

    def test_cuenta_pct_de_contratos_con_volumen_mayor_a_oi(self):
        s = structure_score(
            [
                row(strike=100, open_interest=10, volume=500),  # supera
                row(strike=110, open_interest=10, volume=500),  # supera
                row(strike=120, open_interest=1000, volume=5),  # no
                row(strike=130, open_interest=1000, volume=5),  # no
            ]
        )
        assert s.vol_oi["considered"] == 4
        assert s.vol_oi["exceeded"] == 2
        assert s.vol_oi["pct"] == 50
        assert s.vol_oi["points"] == 8

    def test_ignora_contratos_sin_actividad(self):
        s = structure_score(
            [
                row(strike=100, open_interest=0, volume=0),
                row(strike=110, open_interest=10, volume=500),
            ]
        )
        assert s.vol_oi["considered"] == 1
        assert s.vol_oi["pct"] == 100
        assert s.vol_oi["points"] == 10

    def test_resume_los_vencimientos_mas_relevantes(self):
        s = structure_score(
            [
                row(strike=100, expiration="2026-09-18", open_interest=10_000),
                row(strike=110, expiration="2026-12-18", open_interest=100),
            ]
        )
        assert s.expirations[0].expiration == "2026-09-18"
        assert s.expirations[0].pct_of_total > 90

    def test_cadena_vacia_devuelve_cero(self):
        assert structure_score([]).score == 0
