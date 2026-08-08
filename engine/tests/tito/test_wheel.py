"""Port de `wheel.test.ts`, `wheelAfford.test.ts` y `wheelUniverse.test.ts`.

Sus 41 casos, uno a uno. `wheel.py` (421 líneas), `wheel_universe.py` y
`earnings.py` estaban portados y **sin un solo test propio**: los 13 que
respondían a `-k "wheel or earnings"` eran todos del agente de ACCIONES
(`test_brief`, `test_technical`…), nada que ver con la Wheel.

Lo que eso significaba en la práctica: la estrategia que decide qué put vender,
con cuánto colateral y en qué orden se listan los candidatos no tenía ni un
test ni un diferencial. Ahora tiene las dos cosas — `diff_wheel.sh` la compara
contra su TypeScript sobre 1.072 casos, y esto fija su INTENCIÓN, que es lo que
un corpus aleatorio no expresa: qué caso es "el perfecto", por qué el 90%
anualizado se castiga en vez de premiarse, y que el IV Rank va al revés que en
`ivcontext`.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from wbj.tito.earnings import earnings_flag, estimate_next_earnings
from wbj.tito.levels import Level, LevelSource
from wbj.tito.wheel import (
    HAIRCUT, MIN_OI, WHEEL_PRESETS, CandidatesInput, ChainQuote, ScoreInput,
    WheelCandidate, WheelMetrics, WheelScore, ScorePart, atm_iv, liquidity_block,
    pick_premium, score_candidate, spread_pct_of, wheel_candidates, wheel_metrics)
from wbj.tito.wheel_universe import (
    WHEEL_UNIVERSE, afford_of, sort_by_afford_then_score)


# ── WHEEL_PRESETS ───────────────────────────────────────────────────────────

class TestPresets:
    def test_los_tres_presets_van_de_menos_a_mas_delta(self):
        assert WHEEL_PRESETS["conservador"].delta_max <= WHEEL_PRESETS["balanceado"].delta_min
        assert WHEEL_PRESETS["balanceado"].delta_max <= WHEEL_PRESETS["agresivo"].delta_min

    def test_todos_cierran_al_50_por_ciento_de_la_prima(self):
        for p in WHEEL_PRESETS.values():
            assert p.take_profit_pct == 50


# ── pickPremium ─────────────────────────────────────────────────────────────

class TestPickPremium:
    def test_prefiere_el_bid_real_y_no_le_aplica_recorte(self):
        p = pick_premium(bid=0.32, ask=0.36, last_trade=0.5, model=0.6)
        assert (p.price, p.source, p.raw) == (0.32, "bid", 0.32)

    def test_cae_al_ultimo_precio_con_recorte_del_10_cuando_no_hay_bid(self):
        p = pick_premium(bid=0, ask=0.36, last_trade=0.5, model=0.6)
        assert p.source == "ultimo"
        assert p.price == pytest.approx(0.5 * (1 - HAIRCUT["ultimo"]), abs=1e-10)
        assert p.raw == 0.5

    def test_cae_al_modelo_con_recorte_del_15_cuando_no_hay_bid_ni_ultimo(self):
        p = pick_premium(model=0.6)
        assert p.source == "modelo"
        assert p.price == pytest.approx(0.6 * (1 - HAIRCUT["modelo"]), abs=1e-10)

    def test_devuelve_none_si_no_hay_ninguna_fuente(self):
        assert pick_premium() is None
        assert pick_premium(bid=0, last_trade=0, model=0) is None


# ── spreadPctOf ─────────────────────────────────────────────────────────────

class TestSpreadPct:
    def test_mide_el_spread_contra_el_mid(self):
        assert spread_pct_of(0.9, 1.1) == pytest.approx(20, abs=1e-10)

    def test_devuelve_none_si_falta_un_lado(self):
        assert spread_pct_of(0, 1.1) is None


# ── liquidityBlock ──────────────────────────────────────────────────────────

class TestLiquidityBlock:
    """La salvaguarda del proyecto: qué contrato NO se enseña."""

    def test_bloquea_si_no_hay_bid(self):
        assert liquidity_block(0, 1.1, 900) == "sin_bid"

    def test_bloquea_si_el_spread_pasa_del_25(self):
        assert liquidity_block(0.5, 0.9, 900) == "spread_ancho"

    def test_bloquea_si_el_oi_es_menor_a_100(self):
        assert liquidity_block(1, 1.05, MIN_OI - 1) == "oi_bajo"

    def test_deja_pasar_un_contrato_liquido(self):
        assert liquidity_block(1, 1.05, 900) is None


# ── wheelMetrics ────────────────────────────────────────────────────────────

class TestWheelMetrics:
    """Put de F a $11, prima $0.32, spot $11.60, 21 días — su caso."""

    def _m(self, **kw):
        base = dict(strike=11, price=0.32, spot=11.6, dte=21, iv=0.45)
        return wheel_metrics(**{**base, **kw})

    def test_credito_colateral_retorno_anualizado_y_breakeven(self):
        m = self._m()
        assert m.credit == pytest.approx(32, abs=1e-10)
        assert m.collateral == pytest.approx(1100, abs=1e-10)
        assert m.return_pct == pytest.approx((32 / 1100) * 100, abs=1e-10)
        assert m.annualized_pct == pytest.approx((32 / 1100) * 100 * (365 / 21), abs=1e-10)
        assert m.breakeven == pytest.approx(10.68, abs=1e-10)

    def test_el_colchon_se_mide_desde_el_breakeven_no_desde_el_strike(self):
        m = self._m()
        assert m.cushion_pct == pytest.approx(((11.6 - 10.68) / 11.6) * 100, abs=1e-10)

    def test_la_probabilidad_de_expirar_sin_valor_va_en_0_100(self):
        m = self._m()
        assert m.prob_expire_worthless > 50
        assert m.prob_expire_worthless <= 100

    def test_un_dte_de_0_no_revienta_el_anualizado(self):
        assert math.isfinite(self._m(dte=0).annualized_pct)


# ── scoreCandidate ──────────────────────────────────────────────────────────

def _soporte(price: float, strength: int) -> Level:
    return Level(price=price, kind="soporte", strength=strength, distance_pct=0,
                 flipped=False, why="test",
                 sources=LevelSource(touches=2, last_touch="2026-07-01",
                                     open_interest=0, notional=0,
                                     flow_premium=0, net_gex=0))


def _base(**kw) -> ScoreInput:
    d = dict(annualized_pct=25, iv_rank=80, strike=11, spot=11.6, cushion_pct=8,
             supports=[_soporte(11.5, 60)], open_interest=900, spread_pct=5,
             earnings="fuera")
    return ScoreInput(**{**d, **kw})


class TestScoreCandidate:
    def test_suma_100_en_el_caso_perfecto(self):
        assert score_candidate(_base()).total == 100

    def test_castiga_el_rendimiento_por_encima_del_60_anualizado(self):
        """Prima así de gorda significa que el mercado sabe algo que tú no."""
        bueno = score_candidate(_base(annualized_pct=25)).annualized.points
        sospechoso = score_candidate(_base(annualized_pct=90)).annualized.points
        assert sospechoso < bueno
        assert sospechoso == 10

    def test_premia_el_iv_rank_ALTO_invertido_respecto_a_ivcontext(self):
        alto = score_candidate(_base(iv_rank=80)).iv_rank.points
        bajo = score_candidate(_base(iv_rank=20)).iv_rank.points
        assert alto > bajo
        assert alto == 20
        assert bajo == 4

    def test_premia_el_strike_por_debajo_de_un_soporte_fuerte(self):
        bajo_fuerte = score_candidate(_base(supports=[_soporte(11.5, 60)])).cushion.points
        por_encima = score_candidate(
            _base(strike=12, supports=[_soporte(11.5, 60)])).cushion.points
        assert bajo_fuerte == 25
        assert por_encima == 5

    def test_sin_soportes_pero_con_buen_colchon_da_puntuacion_intermedia(self):
        s = score_candidate(_base(supports=[], cushion_pct=12))
        assert s.cushion.points == 12

    def test_las_bandas_de_liquidez_se_evaluan_en_orden_y_gana_la_primera(self):
        """OI alto pero spread del 20%: NO cobra los 15, cae a la tercera banda."""
        s = score_candidate(_base(open_interest=800, spread_pct=20))
        assert s.liquidity.points == 5

    def test_penaliza_el_reporte_dentro_y_lo_anula_si_el_skew_lo_confirma(self):
        assert score_candidate(_base(earnings="fuera")).earnings.points == 10
        assert score_candidate(_base(earnings="dentro")).earnings.points == 3
        assert score_candidate(_base(earnings="dentro_confirmado")).earnings.points == 0

    def test_un_etf_sin_financials_cobra_los_10_puntos(self):
        assert score_candidate(_base(earnings="no_aplica")).earnings.points == 10

    def test_sin_iv_rank_puntua_la_banda_baja_pero_no_rompe(self):
        s = score_candidate(_base(iv_rank=None))
        assert s.iv_rank.points == 4
        assert math.isfinite(s.total)

    def test_cada_componente_explica_su_porque_en_llano(self):
        s = score_candidate(_base())
        for part in (s.annualized, s.iv_rank, s.cushion, s.liquidity, s.earnings):
            assert len(part.why) > 10
            assert part.points <= part.max

    def test_el_porque_del_soporte_lleva_el_numero_como_lo_escribe_JS(self):
        """`${Math.round(83)}` pinta "83". Una f-string de Python pintaba "83.0"
        y el mismo soporte salía descrito con dos números distintos según quién
        lo pintara. Lo cazó `diff_wheel.sh` en 60 de 200 casos."""
        s = score_candidate(_base(supports=[_soporte(11.5, 83)]))
        assert "fuerza 83:" in s.cushion.why


# ── wheelCandidates ─────────────────────────────────────────────────────────

def _quote(**kw) -> ChainQuote:
    d = dict(strike=11, expiration="2026-08-21", dte=35, bid=0.30, ask=0.34,
             last_trade=0.32, open_interest=900)
    return ChainQuote(**{**d, **kw})


def _cand_input(quotes, **kw) -> CandidatesInput:
    # 11,7 y no 11,6 por lo que él anota: a 11,6 el delta implícito del strike
    # 11 cae en ~0,31, justo FUERA de la banda 0,20-0,30 del preset balanceado.
    d = dict(ticker="F", spot=11.7, preset=WHEEL_PRESETS["balanceado"],
             iv_rank=60, supports=[], earnings="fuera", fallback_iv=0.45)
    return CandidatesInput(quotes=quotes, **{**d, **kw})


class TestWheelCandidates:
    def test_descarta_los_strikes_cuyo_delta_cae_fuera_del_preset(self):
        out = wheel_candidates(_cand_input([_quote(strike=5, bid=0.01, ask=0.02)]))
        assert out == []

    def test_descarta_los_vencimientos_fuera_de_la_ventana_de_dte(self):
        assert wheel_candidates(_cand_input([_quote(dte=3)])) == []

    def test_marca_como_blocked_y_sin_prima_un_contrato_iliquido(self):
        out = wheel_candidates(_cand_input([_quote(bid=0, ask=0.34, open_interest=5)]))
        assert len(out) == 1
        assert out[0].blocked is True
        assert out[0].block_reason == "sin_bid"
        assert out[0].premium is None
        assert out[0].metrics is None

    def test_un_candidato_valido_trae_delta_negativo_metricas_y_score(self):
        out = wheel_candidates(_cand_input([_quote()]))
        assert len(out) == 1
        c = out[0]
        assert c.blocked is False
        assert c.delta < 0
        assert c.premium.source == "bid"
        assert c.metrics.collateral == pytest.approx(1100, abs=1e-10)
        assert c.score.total > 0

    def test_marca_la_fila_cuando_la_iv_no_converge_y_cae_al_fallback(self):
        """Quote cruzada e imposible: la bisección no puede converger."""
        out = wheel_candidates(_cand_input([_quote(bid=20, ask=21, last_trade=20.5)]))
        assert out[0].iv_source == "estimada"

    def test_ordena_por_score_y_deja_los_bloqueados_al_final(self):
        out = wheel_candidates(_cand_input([
            _quote(strike=11, bid=0, ask=0.34, open_interest=5),
            _quote(strike=10.5, bid=0.28, ask=0.30, open_interest=2000),
        ]))
        assert out[0].blocked is False
        assert out[-1].blocked is True


# ── atmIv ───────────────────────────────────────────────────────────────────

class _Fila:
    def __init__(self, strike, iv):
        self.strike, self.iv = strike, iv


class TestAtmIv:
    def test_devuelve_la_iv_del_strike_mas_cercano_al_spot(self):
        assert atm_iv([_Fila(8, 0.9), _Fila(11.5, 0.45), _Fila(20, 0.7)], 11.6) == 0.45

    def test_devuelve_none_sin_datos(self):
        assert atm_iv([], 11.6) is None


# ── affordOf / sortByAffordThenScore ────────────────────────────────────────

def _wc(collateral: float, score_total: int, blocked: bool = False,
        metrics: bool = True) -> WheelCandidate:
    p = ScorePart(points=0, max=0, band="", why="")
    return WheelCandidate(
        ticker="F", strike=collateral / 100, expiration="2026-08-21", dte=35,
        spot=12, delta=-0.25, iv=0.45, iv_source="implicita", open_interest=900,
        spread_pct=5,
        premium=None, blocked=blocked, block_reason=None,
        metrics=(WheelMetrics(credit=30, collateral=collateral, return_pct=2,
                              annualized_pct=25, breakeven=10.7, cushion_pct=8,
                              prob_expire_worthless=75) if metrics else None),
        score=WheelScore(total=score_total, annualized=p, iv_rank=p, cushion=p,
                         liquidity=p, earnings=p))


class TestAfford:
    def test_alcanza_cuando_el_colateral_cabe_en_el_efectivo(self):
        a = afford_of(_wc(1100, 80), 2000)
        assert a.affordable is True
        assert a.shortfall == 0

    def test_no_alcanza_y_reporta_cuanto_falta(self):
        a = afford_of(_wc(1100, 80), 800)
        assert a.affordable is False
        assert a.shortfall == 300

    def test_un_candidato_bloqueado_nunca_es_asequible(self):
        a = afford_of(_wc(1100, 0, blocked=True, metrics=False), 5000)
        assert a.affordable is False

    def test_pone_los_asequibles_arriba_y_ordena_por_score_dentro_de_cada_grupo(self):
        filas = sort_by_afford_then_score(
            [_wc(5000, 95),   # caro pero mejor score
             _wc(1000, 60)],  # asequible, peor score
            1500)
        assert filas[0][0].metrics.collateral == 1000
        assert filas[0][1].affordable is True
        assert filas[1][1].affordable is False


# ── WHEEL_UNIVERSE ──────────────────────────────────────────────────────────

class TestUniverso:
    def test_no_tiene_tickers_repetidos(self):
        vistos = {s.ticker for s in WHEEL_UNIVERSE}
        assert len(vistos) == len(WHEEL_UNIVERSE)

    def test_cubre_los_cuatro_tramos(self):
        """Para que una cuenta chica tenga algo que hacer."""
        assert {s.tier for s in WHEEL_UNIVERSE} == {"etf", "barato", "medio", "caro"}
        assert len([s for s in WHEEL_UNIVERSE if s.tier == "barato"]) >= 8

    def test_cada_entrada_declara_por_que_esta(self):
        import re
        for s in WHEEL_UNIVERSE:
            assert re.fullmatch(r"[A-Z.]{1,6}", s.ticker), s.ticker
            assert len(s.razon) > 8, s.ticker


# ── earnings ────────────────────────────────────────────────────────────────

def _ahora(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


class TestEstimateNextEarnings:
    def test_proyecta_el_siguiente_reporte_a_91_dias_del_ultimo_filing(self):
        # 2026-05-01 + 91 días ≈ 2026-07-31
        assert estimate_next_earnings(["2026-01-30", "2026-05-01"],
                                      _ahora("2026-07-24T12:00:00Z")) == "2026-07-31"

    def test_avanza_en_pasos_de_91_dias_hasta_caer_en_el_futuro(self):
        n = estimate_next_earnings(["2026-01-30"], _ahora("2026-07-24T12:00:00Z"))
        assert n is not None
        assert n > "2026-07-24"

    def test_devuelve_none_sin_fechas(self):
        assert estimate_next_earnings([], _ahora("2026-07-24T00:00:00Z")) is None


class TestEarningsFlag:
    def test_no_aplica_cuando_no_hay_estimacion(self):
        """ETF o ticker sin financials."""
        assert earnings_flag(None, "2026-08-21", None) == "no_aplica"

    def test_fuera_cuando_el_reporte_cae_despues_del_vencimiento(self):
        assert earnings_flag("2026-09-15", "2026-08-21", 2) == "fuera"

    def test_dentro_cuando_el_reporte_cae_antes_del_vencimiento(self):
        assert earnings_flag("2026-08-10", "2026-08-21", 2) == "dentro"

    def test_dentro_confirmado_si_el_skew_del_frente_lo_respalda(self):
        assert earnings_flag("2026-08-10", "2026-08-21", 14) == "dentro_confirmado"

    def test_una_fecha_ilegible_penaliza_en_vez_de_absolver(self):
        """Su `getTime()` da `NaN`, toda comparación con `NaN` es falsa y cae en
        "dentro". El port devolvía "no_aplica" —10 de 10 puntos— o sea que un
        dato corrupto pasaba de penalizar a absolver, en la única guarda que
        existe para que no te pille un reporte dentro del vencimiento."""
        assert earnings_flag("no-es-fecha", "2026-09-18", 0) == "dentro"
        assert earnings_flag("2026-09-01", "tampoco", 0) == "dentro"
