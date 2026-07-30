"""Port de `web/lib/prediction.test.ts` (32 casos)."""

from __future__ import annotations

import math
from typing import Any

import pytest

from wbj.tito.expected_move import LevelInput, level_probabilities
from wbj.tito.prediction import (
    SubScores,
    calibration_shift_pct,
    confidence_of,
    predict_pro,
    weighted_score,
)

FULL = SubScores(
    aggression=5, conviction=6, unusuality=5, structure=8, iv_context=4, validation=6
)

NODES = [
    LevelInput(strike=110, concentration=1.0, side="call", net_gex=5e6),
    LevelInput(strike=105, concentration=0.5, side="call", net_gex=2e6),
    LevelInput(strike=92, concentration=0.7, side="put", net_gex=-3e6),
]


def call(**p: Any):
    kwargs: dict[str, Any] = dict(
        spot=100, iv=0.5, horizon_days=20, nodes=NODES, scores=FULL,
        regime="positive", callvpct=70, hit_rate=55, low_liquidity=False,
    )
    kwargs.update(p)
    return predict_pro(**kwargs)


class TestWeightedScore:
    def test_con_las_seis_categorias_pondera_por_los_pesos(self):
        score, active, weight = weighted_score(FULL)
        assert active == 6
        assert weight == 100
        # (5*20 + 6*20 + 5*20 + 8*15 + 4*10 + 6*15)/10 = 57
        assert score == 57

    def test_ignora_las_categorias_sin_dato_y_renormaliza(self):
        score, active, weight = weighted_score(
            SubScores(aggression=5, conviction=6, unusuality=5, structure=8)
        )
        assert active == 4
        assert weight == 75
        assert score > 0

    def test_sin_datos_es_cero(self):
        score, active, _ = weighted_score(SubScores())
        assert score == 0
        assert active == 0


LEVELS = level_probabilities(
    100, 0.5, 20,
    [
        LevelInput(strike=110, concentration=1, side="call", net_gex=5e6),
        LevelInput(strike=95, concentration=0.2, side="put", net_gex=-1e6),
    ],
)


class TestConfidenceOf:
    def test_la_baja_liquidez_anula_la_confianza(self):
        assert confidence_of(LEVELS, 6, 80, True) == 0

    def test_sin_niveles_no_hay_confianza(self):
        assert confidence_of([], 6, 80, False) == 0

    def test_mas_categorias_y_mejor_historico_dan_mas_confianza(self):
        assert confidence_of(LEVELS, 6, 80, False) > confidence_of(LEVELS, 2, 20, False)

    def test_queda_dentro_de_cero_a_cien(self):
        for hr in (0, 50, 100):
            assert 0 <= confidence_of(LEVELS, 6, hr, False) <= 100


class TestTresEscenarios:
    def test_ordena_siempre_bear_menor_base_menor_bull(self):
        casos = [
            call(),
            call(nodes=[
                LevelInput(strike=92, concentration=1.0, side="put", net_gex=-5e6),
                LevelInput(strike=95, concentration=0.8, side="put", net_gex=-3e6),
            ]),
            call(nodes=[]),
            call(nodes=[LevelInput(strike=100, concentration=1, side="call", net_gex=1e6)]),
        ]
        for p in casos:
            assert p.bear.target < p.base.target
            assert p.base.target < p.bull.target

    def test_si_el_unico_muro_de_abajo_es_el_iman_el_bear_lo_marca_la_sigma(self):
        p = call(nodes=[
            LevelInput(strike=95, concentration=1.0, side="put", net_gex=-5e6),
            LevelInput(strike=112, concentration=0.4, side="call", net_gex=2e6),
        ])
        assert p.base.target == 95
        assert p.bear.target < 95
        assert "1σ" in p.bear.driver

    def test_el_iman_lo_decide_la_probabilidad_de_toque(self):
        # 92 tiene mas concentracion, pero 95 esta mas cerca: gana 95.
        p = call(nodes=[
            LevelInput(strike=92, concentration=1.0, side="put", net_gex=-5e6),
            LevelInput(strike=95, concentration=0.9, side="put", net_gex=-4e6),
        ])
        assert p.base.target == 95
        assert p.bear.target == 92

    def test_el_escenario_base_es_el_nivel_iman(self):
        p = call()
        assert p.base.target == p.levels[0].strike
        assert "imán" in p.base.driver

    def test_ningun_escenario_se_sale_del_cono_de_dos_sigma(self):
        p = call(
            nodes=[LevelInput(strike=5000, concentration=1, side="call", net_gex=9e9)],
            horizon_days=10,
        )
        em2 = 100 * math.exp(2 * 0.5 * math.sqrt(10 / 365))
        assert p.bull.target <= em2 + 1e-6

    def test_sin_muros_usa_las_bandas_de_una_sigma(self):
        p = call(nodes=[])
        assert "1σ" in p.bull.driver
        assert "1σ" in p.bear.driver
        assert p.bull.target > 100
        assert p.bear.target < 100

    def test_las_probabilidades_estan_en_cero_a_uno(self):
        p = call()
        for s in (p.bear, p.base, p.bull):
            assert 0 <= s.probability <= 1

    def test_un_horizonte_mas_largo_abre_los_escenarios(self):
        corto = call(nodes=[], horizon_days=10)
        largo = call(nodes=[], horizon_days=30)
        assert largo.bull.target > corto.bull.target
        assert largo.bear.target < corto.bear.target

    def test_los_tres_escenarios_son_siempre_precios_distintos(self):
        abajo = call(nodes=[
            LevelInput(strike=92, concentration=1.0, side="put", net_gex=-5e6),
            LevelInput(strike=88, concentration=0.6, side="put", net_gex=-3e6),
            LevelInput(strike=110, concentration=0.4, side="call", net_gex=2e6),
        ])
        assert len({abajo.bear.target, abajo.base.target, abajo.bull.target}) == 3

        arriba = call()
        assert len({arriba.bear.target, arriba.base.target, arriba.bull.target}) == 3

        uno = call(nodes=[LevelInput(strike=95, concentration=1, side="put", net_gex=-1e6)])
        assert len({uno.bear.target, uno.base.target, uno.bull.target}) == 3

    def test_el_driver_describe_el_significado(self):
        p = call()
        assert "Gamma concentrada" in p.bull.driver
        assert "Muro de puts" not in p.bull.driver

    def test_respeta_el_horizonte_pedido(self):
        assert call(horizon_days=10).horizon_days == 10
        assert call(horizon_days=30).horizon_days == 30


class TestDireccionYResumen:
    def test_marca_direccion_al_alza(self):
        p = call()
        assert p.direction == "up"
        assert "alza" in p.summary

    def test_marca_direccion_a_la_baja(self):
        p = call(
            nodes=[LevelInput(strike=85, concentration=1, side="put", net_gex=-5e6)],
            callvpct=20,
        )
        assert p.direction == "down"
        assert "puts" in p.summary

    def test_el_resumen_menciona_horizonte_y_rango(self):
        p = call(horizon_days=30)
        assert "30 días" in p.summary
        assert "1σ" in p.summary

    def test_el_resumen_incluye_el_historico_cuando_existe(self):
        assert "62%" in call(hit_rate=62).summary
        assert "Históricamente" not in call(hit_rate=None).summary

    def test_avisa_cuando_la_cadena_es_iliquida(self):
        p = call(low_liquidity=True)
        assert "NO FIABLE" in p.caveat
        assert p.confidence == 0

    def test_avisa_cuando_faltan_subagentes(self):
        p = call(scores=SubScores(aggression=5, conviction=6, unusuality=5, structure=8))
        assert "4 de 6" in p.caveat

    def test_sin_avisos_cuando_estan_los_seis_y_hay_liquidez(self):
        assert call().caveat is None


class TestCalibrationShiftPct:
    def test_no_corrige_sin_historial_suficiente(self):
        assert calibration_shift_pct(3, 4) == 0
        assert calibration_shift_pct(None, 20) == 0

    def test_corrige_el_sesenta_por_ciento_del_sesgo(self):
        assert calibration_shift_pct(2, 8) == pytest.approx(1.2, abs=1e-5)

    def test_acota_a_mas_menos_tres_por_ciento(self):
        assert calibration_shift_pct(10, 10) == 3
        assert calibration_shift_pct(-10, 10) == -3


class TestAutoCorreccionPorMemoria:
    def test_sin_calibracion_deja_el_target_crudo(self):
        r = call()
        assert r.base.target == pytest.approx(110, abs=1e-5)
        assert r.calibration["applied"] is False
        assert r.calibration["shift_pct"] == 0

    def test_con_sesgo_positivo_sube_el_target_base(self):
        r = call(calibration={"bias_pct": 2, "samples": 8})
        # shift = spot(100) * 1.2% = 1.2 -> 110 + 1.2
        assert r.base.target == pytest.approx(111.2, abs=1e-5)
        assert r.calibration["applied"] is True
        assert r.calibration["shift_pct"] == pytest.approx(1.2, abs=1e-5)
        assert "ajustó" in r.summary

    def test_con_sesgo_negativo_baja_el_target_y_respeta_el_orden(self):
        r = call(calibration={"bias_pct": -5, "samples": 6})
        assert r.base.target < 110
        assert r.bear.target < r.base.target
        assert r.bull.target > r.base.target

    def test_historial_insuficiente_no_toca_el_target(self):
        r = call(calibration={"bias_pct": 5, "samples": 3})
        assert r.base.target == pytest.approx(110, abs=1e-5)
        assert r.calibration["applied"] is False
