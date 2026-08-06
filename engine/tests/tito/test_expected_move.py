"""Port de `web/lib/expectedMove.test.ts` (28 casos)."""

from __future__ import annotations

import pytest

from wbj.tito.expected_move import (
    LevelInput,
    cone_points,
    expected_move,
    level_probabilities,
    norm_cdf,
    prediction_path,
    prob_above,
    prob_in_band,
    prob_touch,
)


class TestNormCdf:
    def test_es_medio_en_el_centro_y_simetrica(self):
        assert norm_cdf(0) == pytest.approx(0.5, abs=1e-6)
        assert norm_cdf(1) + norm_cdf(-1) == pytest.approx(1.0, abs=1e-6)

    def test_reproduce_los_valores_de_tabla(self):
        assert norm_cdf(1) == pytest.approx(0.8413, abs=1e-3)
        assert norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
        assert norm_cdf(-2) == pytest.approx(0.0228, abs=1e-3)


class TestExpectedMove:
    def test_sigma_es_spot_por_iv_por_raiz_de_t(self):
        em = expected_move(100, 0.5, 365)
        assert em.sigma == pytest.approx(50, abs=1e-6)
        assert em.sigma_pct == pytest.approx(50, abs=1e-6)

    def test_escala_con_la_raiz_del_tiempo_no_lineal(self):
        a = expected_move(100, 0.5, 30)
        b = expected_move(100, 0.5, 120)  # 4x el tiempo
        assert b.sigma / a.sigma == pytest.approx(2, abs=1e-3)  # -> 2x el movimiento

    def test_dos_sigma_esta_mas_lejos_y_el_suelo_no_cruza_cero(self):
        em = expected_move(100, 1.5, 365)
        assert em.upper2 > em.upper1
        assert em.lower2 < em.lower1
        assert em.lower2 > 0

    def test_a_cero_dias_no_hay_movimiento(self):
        em = expected_move(100, 0.5, 0)
        assert em.sigma == pytest.approx(0, abs=1e-9)
        assert em.upper1 == pytest.approx(100, abs=1e-9)


class TestConePoints:
    def test_empieza_pegado_al_spot_y_se_abre(self):
        c = cone_points(100, 0.5, 30, 10)
        assert len(c) == 11
        assert c[0].upper1 == pytest.approx(100, abs=1e-6)
        assert c[10].upper1 > c[5].upper1
        assert c[10].lower1 < c[5].lower1

    def test_se_abre_en_raiz_de_t(self):
        c = cone_points(100, 0.5, 100, 100)
        half = c[50].upper1 - 100
        full = c[100].upper1 - 100
        assert half / full > 0.6  # ~0.707, no 0.5


class TestProbAbove:
    def test_atm_cerca_del_cincuenta_por_ciento(self):
        assert prob_above(100, 100, 0.4, 30) > 0.42
        assert prob_above(100, 100, 0.4, 30) < 0.5

    def test_mas_lejos_menos_probable(self):
        cerca = prob_above(100, 105, 0.4, 30)
        lejos = prob_above(100, 130, 0.4, 30)
        assert lejos < cerca

    def test_mas_volatilidad_hace_mas_probable_el_strike_lejano(self):
        assert prob_above(100, 120, 0.9, 30) > prob_above(100, 120, 0.2, 30)


class TestProbInBand:
    def test_la_banda_central_concentra_mas_que_una_lejana(self):
        centro = prob_in_band(100, 98, 102, 0.4, 30)
        lejos = prob_in_band(100, 138, 142, 0.4, 30)
        assert centro > lejos

    def test_una_banda_enorme_se_acerca_a_uno(self):
        assert prob_in_band(100, 1, 10_000, 0.4, 30) > 0.98

    def test_no_depende_del_orden_de_los_limites(self):
        assert prob_in_band(100, 95, 105, 0.4, 30) == pytest.approx(
            prob_in_band(100, 105, 95, 0.4, 30), abs=1e-9
        )


class TestProbTouch:
    def test_tocar_es_mas_probable_que_cerrar_mas_alla(self):
        cierra = prob_above(100, 110, 0.5, 30)
        assert prob_touch(100, 110, 0.5, 30) > cierra

    def test_el_nivel_del_precio_actual_se_toca_seguro(self):
        assert prob_touch(100, 100, 0.5, 30) == 1

    def test_nunca_pasa_de_uno(self):
        assert prob_touch(100, 100.01, 0.9, 90) <= 1

    def test_funciona_hacia_abajo_igual_que_hacia_arriba(self):
        assert prob_touch(100, 90, 0.5, 30) > 0
        assert prob_touch(100, 110, 0.5, 30) > 0

    def test_la_caida_al_nivel_espejo_es_algo_mas_probable(self):
        # 90 y 111.11 son espejo en log (90/100 = 100/111.11), pero el termino
        # -0.5*sigma^2*T de d2 inclina la distribucion: el suelo se toca antes.
        abajo = prob_touch(100, 90, 0.5, 30)
        arriba = prob_touch(100, 111.11, 0.5, 30)
        assert abajo > arriba
        assert abajo - arriba < 0.15  # pero sin dispararse


LEVELS = [
    LevelInput(strike=95, concentration=0.2, side="put", net_gex=-1e6),
    LevelInput(strike=100, concentration=0.5, side="call", net_gex=2e6),
    LevelInput(strike=105, concentration=1.0, side="call", net_gex=5e6),
]


class TestLevelProbabilities:
    def test_los_pesos_suman_cien_por_ciento(self):
        p = level_probabilities(100, 0.5, 30, LEVELS)
        assert sum(l.magnet for l in p) == pytest.approx(1.0, abs=1e-6)

    def test_un_muro_grande_gana_a_un_nivel_cercano_vacio(self):
        p = level_probabilities(100, 0.5, 30, LEVELS)
        assert p[0].strike == 105  # el de mayor concentracion manda

    def test_devuelve_toque_y_banda_ademas_del_iman(self):
        p = level_probabilities(100, 0.5, 30, LEVELS)
        l = next(x for x in p if x.strike == 105)
        assert l.touch > 0
        assert l.band > 0
        assert l.concentration == 1

    def test_sin_niveles_devuelve_vacio(self):
        assert level_probabilities(100, 0.5, 30, []) == []

    def test_ordena_de_mayor_a_menor_peso(self):
        p = level_probabilities(100, 0.5, 30, LEVELS)
        for prev, cur in zip(p, p[1:]):
            assert cur.magnet <= prev.magnet


class TestPredictionPath:
    def test_va_del_spot_al_objetivo(self):
        p = prediction_path(100, 110, 0.5, 30, 10)
        assert p.points[0][1] == pytest.approx(100, abs=1e-6)
        assert p.points[-1][1] == pytest.approx(110, abs=1e-6)
        assert p.clamped is False

    def test_avanza_rapido_al_principio_y_se_aplana(self):
        p = prediction_path(100, 110, 0.5, 30, 100)
        mitad_tiempo = p.points[50][1] - 100
        total = 10
        assert mitad_tiempo / total > 0.6  # ~0.707

    def test_recorta_un_objetivo_que_la_volatilidad_no_alcanza(self):
        p = prediction_path(100, 500, 0.2, 5, 5)  # 500 esta fuera de 2 sigma
        assert p.clamped is True
        assert p.target < 500
        assert p.target == pytest.approx(expected_move(100, 0.2, 5).upper2, abs=1e-6)

    def test_recorta_tambien_hacia_abajo(self):
        p = prediction_path(100, 1, 0.2, 5, 5)
        assert p.clamped is True
        assert p.target > 1
