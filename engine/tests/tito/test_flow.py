"""Port de `web/lib/flow.test.ts` (55 casos)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from wbj.tito.flow import (
    aggression_of,
    aggression_score,
    classify_flow,
    conviction_score,
    delta_score,
    detect_clusters,
    dominance_score,
    execution_level,
    execution_score,
    expiry_score,
    gamma_score,
    leg_score,
    order_size_score,
    repetition_score,
    spread_pct,
    spread_score,
    theta_score,
    timing_score,
    unusual_trade_score,
    unusuality_score,
    volume_score,
)

NOW = datetime(2026, 7, 22, 21, 0, 0, tzinfo=timezone.utc)


def trade(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "symbol": "TSLA261120P00305000",
        "price": 11.7,
        "size": 1,
        "side": "ASKSIDE",
        "bid_price": 11.55,
        "ask_price": 11.75,
        "premium": 1170,
        "delta": -0.18,
        "implied_volatility": 0.48,
        "open_interest": 1556,
        "volume": 1046,
        "score": 50,
        "sentiment": "bearish",
        "timestamp": "2026-07-22T19:59:59.994Z",
    }
    base.update(overrides)
    return base


def _iso(base: str, secs: float) -> str:
    dt = datetime.fromisoformat(base.replace("Z", "+00:00")) + timedelta(seconds=secs)
    return dt.isoformat().replace("+00:00", "Z")


class TestAggressionOf:
    def test_mapea_los_lados_de_marketsnack(self):
        assert aggression_of("ASKSIDE") == "ask"
        assert aggression_of("ABOVE_ASK") == "ask"
        assert aggression_of("AT_ASK") == "ask"
        assert aggression_of("BELOW_BID") == "bid"
        assert aggression_of("AT_BID") == "bid"
        assert aggression_of("MIDMKT") == "mid"
        assert aggression_of("???") == "unknown"


class TestClassifyFlow:
    def test_parsea_el_contrato_y_arma_la_fila(self):
        r = classify_flow([trade(id=1)], NOW).rows[0]
        assert r.underlying == "TSLA"
        assert r.type == "put"
        assert r.strike == 305
        assert r.expiration == "2026-11-20"
        assert r.dte == 121
        assert r.aggression == "ask"

    def test_marca_big_e_interesante(self):
        r = classify_flow([trade(id=1, premium=2_000_000)], NOW).rows[0]
        assert r.flags.big is True
        assert r.interesting is True

    def test_marca_conv_delta(self):
        r = classify_flow([trade(id=1, premium=150_000, delta=-0.7)], NOW).rows[0]
        assert r.flags.conv_delta is True
        assert r.interesting is True

    def test_descarta_los_midmkt_del_set_interesante(self):
        r = classify_flow([trade(id=1, side="MIDMKT", premium=5_000_000)], NOW).rows[0]
        assert r.flags.mid is True
        assert r.interesting is False  # mid se descarta aunque sea grande

    def test_above_ask_bajo_el_piso_no_es_interesante(self):
        low = classify_flow([trade(id=1, premium=1_000)], NOW)
        assert low.rows[0].interesting is False
        high = classify_flow([trade(id=1, premium=60_000)], NOW)
        assert high.rows[0].interesting is True

    def test_marca_leap_por_dte_largo(self):
        r = classify_flow([trade(id=1)], NOW).rows[0]  # DTE 121
        assert r.flags.leap is True

    def test_detecta_repetidas(self):
        base = "2026-07-22T19:50:00.000Z"
        rows = classify_flow(
            [trade(id=i, timestamp=_iso(base, s)) for i, s in ((1, 0), (2, 30), (3, 60))],
            NOW,
        ).rows
        assert all(r.flags.repeated for r in rows)

    def test_no_marca_repetidas_fuera_de_la_ventana(self):
        base = "2026-07-22T19:00:00Z"
        rows = classify_flow(
            [trade(id=i, timestamp=_iso(base, s)) for i, s in ((1, 0), (2, 600), (3, 1200))],
            NOW,
        ).rows
        assert not any(r.flags.repeated for r in rows)

    def test_detecta_ejecuciones_simultaneas(self):
        ts = "2026-07-22T19:30:00.000Z"
        rows = classify_flow(
            [
                trade(id=1, symbol="TSLA261120P00305000", timestamp=ts),
                trade(id=2, symbol="TSLA261120C00320000", timestamp=ts),
            ],
            NOW,
        ).rows
        assert rows[0].flags.simultaneous is True
        assert rows[1].flags.simultaneous is True
        # simultaneo != multileg: eso lo decide la condicion OPRA
        assert rows[0].flags.multileg is False

    def test_multileg_sale_de_la_condicion_opra(self):
        mlet = classify_flow([trade(id=1, trade_condition_id=232)], NOW).rows[0]
        assert mlet.flags.multileg is True
        assert mlet.condition_code == "MLET"

        slan = classify_flow([trade(id=2, trade_condition_id=227)], NOW).rows[0]
        assert slan.flags.multileg is False
        assert slan.condition_code == "SLAN"

        # MESL/MFSL se ejecutan "against single leg(s)" -> se tratan como single
        mesl = classify_flow([trade(id=3, trade_condition_id=236)], NOW).rows[0]
        assert mesl.flags.multileg is False

    def test_descarta_transacciones_canceladas(self):
        rows = classify_flow(
            [
                trade(id=1, trade_condition_id=201),  # CANC
                trade(id=2, trade_condition_id=203),  # CNCL
                trade(id=3, trade_condition_id=205),  # CNCO
                trade(id=4, trade_condition_id=207),  # CNOL
                trade(id=5, trade_condition_id=231),  # SLFT -> valida
            ],
            NOW,
        ).rows
        assert len(rows) == 1
        assert rows[0].id == 5
        assert rows[0].condition_code == "SLFT"

    def test_single_leg_puntua_diez_y_multileg_cinco(self):
        single = classify_flow([trade(id=1, trade_condition_id=227)], NOW).rows[0]
        multi = classify_flow([trade(id=2, trade_condition_id=232)], NOW).rows[0]
        assert unusual_trade_score(single).leg == 10
        assert unusual_trade_score(multi).leg == 5

    def test_ordena_el_set_interesante_por_premium_desc(self):
        interesting = classify_flow(
            [
                trade(id=1, premium=60_000),
                trade(id=2, premium=2_000_000),
                trade(id=3, premium=500_000),
            ],
            NOW,
        ).interesting
        assert [r.premium for r in interesting] == [2_000_000, 500_000, 60_000]


def _mk(id: int, side: str, premium: float):
    return classify_flow([trade(id=id, side=side, premium=premium)], NOW).rows[0]


class TestAggressionScore:
    def test_todo_al_ask_es_diez(self):
        s = aggression_score([_mk(1, "ASKSIDE", 1_000_000), _mk(2, "AT_ASK", 500_000)])
        assert s.score == 10
        assert s.premium_ask == 1_500_000
        assert s.premium_bid == 0

    def test_todo_al_bid_es_cero(self):
        s = aggression_score([_mk(1, "BIDSIDE", 800_000)])
        assert s.score == 0
        assert s.ratio == 0

    def test_mitad_y_mitad_es_cinco_e_ignora_mid(self):
        s = aggression_score(
            [_mk(1, "ASKSIDE", 500_000), _mk(2, "BIDSIDE", 500_000), _mk(3, "MIDMKT", 9_000_000)]
        )
        assert s.score == 5
        assert s.premium_mid == 9_000_000

    def test_sin_flujo_ask_bid_es_cero(self):
        assert aggression_score([_mk(1, "MIDMKT", 100_000)]).score == 0


class TestVolumeScore:
    def test_puntua_por_numero_de_contratos(self):
        assert volume_score(200, 0) == 10
        assert volume_score(150, 0) == 10
        assert volume_score(120, 0) == 8
        assert volume_score(60, 0) == 6
        assert volume_score(25, 0) == 4
        assert volume_score(10, 600_000) == 1  # <20 pero >$500k
        assert volume_score(10, 100_000) == 0  # <20 y <=$500k


class TestTimingScore:
    @staticmethod
    def _at(et_hour: int, et_min: int) -> str:
        # 2026-07-22 es horario de verano (EDT, UTC-4)
        return f"2026-07-22T{et_hour + 4:02d}:{et_min:02d}:00Z"

    def test_mediodia_es_diez(self):
        assert timing_score(self._at(12, 0)) == 10

    def test_apertura_es_siete(self):
        assert timing_score(self._at(10, 0)) == 7

    def test_cierre_es_seis(self):
        assert timing_score(self._at(15, 30)) == 6

    def test_otros_horarios_es_tres(self):
        assert timing_score(self._at(14, 0)) == 3


class TestRepetitionScore:
    def test_puntua_por_repeticiones(self):
        assert repetition_score(5) == 10
        assert repetition_score(3) == 10
        assert repetition_score(2) == 7
        assert repetition_score(1) == 4
        assert repetition_score(0) == 1


class TestScoreRows:
    def test_suma_los_tres_subscores_y_marca_inusual_y_exceeded_oi(self):
        def t(id: int) -> dict[str, Any]:
            return trade(
                id=id,
                symbol="TSLA260918C00350000",
                size=200,
                volume=5000,
                open_interest=100,
                timestamp="2026-07-22T16:00:00Z",  # 12:00 ET -> timing 10
            )

        rows = classify_flow([t(1), t(2), t(3)], NOW).rows
        s = rows[0].scores
        assert (s.volume, s.timing, s.repetition, s.total) == (10, 10, 10, 30)
        assert rows[0].unusual is True
        assert rows[0].flags.exceeded_oi is True  # volume 5000 > OI 100


class TestExpiryStatus:
    @staticmethod
    def _with_exp(symbol: str):
        return classify_flow([trade(symbol=symbol)], NOW).rows[0]

    def test_marca_expirado(self):
        assert self._with_exp("TSLA260710P00305000").expiry_status == "expirado"

    def test_marca_expira_hoy(self):
        assert self._with_exp("TSLA260722P00305000").expiry_status == "expira_hoy"

    def test_marca_vigente(self):
        assert self._with_exp("TSLA261120P00305000").expiry_status == "vigente"

    def test_desconocido_si_no_es_occ_valido(self):
        assert self._with_exp("RARO").expiry_status == "desconocido"


class TestConviccionSpread:
    def test_calcula_el_spread_relativo_sobre_el_mid(self):
        assert spread_pct(9.9, 10.1) == pytest.approx(2, abs=1e-2)
        assert spread_pct(0, 5) is None

    def test_puntua_segun_la_tabla(self):
        assert spread_score(1.5) == 10
        assert spread_score(2) == 7
        assert spread_score(5) == 7
        assert spread_score(8) == 4
        assert spread_score(10) == 4
        assert spread_score(15) == 0  # >10% se aparta
        assert spread_score(None) == 0


class TestConviccionDominancia:
    def test_puntua_segun_la_escala(self):
        assert dominance_score(85) == 10
        assert dominance_score(80) == 10
        assert dominance_score(75) == 8
        assert dominance_score(65) == 6
        assert dominance_score(57) == 4
        assert dominance_score(52) == 2
        assert dominance_score(49) == 0


class TestConviccionEjecucion:
    def test_clasifica_el_nivel(self):
        assert execution_level(10.5, 9, 10, "ASKSIDE") == "above_ask"
        assert execution_level(8.5, 9, 10, "BIDSIDE") == "below_bid"
        assert execution_level(10, 9, 10, "AT_ASK") == "at_ask"
        assert execution_level(9, 9, 10, "AT_BID") == "at_bid"
        assert execution_level(9.9, 9, 10, "ASKSIDE") == "near"
        assert execution_level(9.5, 9, 10, "MIDMKT") == "mid"
        assert execution_level(5, 0, 0, "MIDMKT") == "unclear"

    def test_puntua_de_mas_a_menos_agresivo(self):
        assert execution_score("above_ask") == 10
        assert execution_score("below_bid") == 10
        assert execution_score("at_ask") == 8
        assert execution_score("near") == 6
        assert execution_score("mid") == 3
        assert execution_score("unclear") == 0


class TestConvictionScore:
    @staticmethod
    def _mk(**over: Any):
        payload = {"bid_price": 9.9, "ask_price": 10.1, "price": 10.2}
        payload.update(over)
        return classify_flow([trade(**payload)], NOW).rows[0]

    def test_flujo_decidido_da_diez(self):
        rows = [
            self._mk(id=1, side="ASKSIDE", premium=1_000_000),
            self._mk(id=2, side="ASKSIDE", premium=1_000_000),
        ]
        s = conviction_score(rows)
        assert s.spread["points"] == 10
        assert s.dominance["dominant_pct"] == 100
        assert s.dominance["points"] == 10
        assert s.execution["points"] == 10  # above_ask
        assert s.score == 10

    def test_flujo_dividido_y_en_el_medio_baja_el_score(self):
        rows = [
            self._mk(id=1, side="ASKSIDE", premium=500_000, price=10.0),
            self._mk(id=2, side="BIDSIDE", premium=500_000, price=10.0),
        ]
        s = conviction_score(rows)
        assert s.dominance["dominant_pct"] == 50
        assert s.dominance["points"] == 2
        assert s.execution["counts"]["mid"] == 2
        assert s.score < 7

    def test_separa_spreads_anchos_y_alerta_si_superan_un_millon(self):
        rows = [
            self._mk(id=1, side="ASKSIDE", premium=2_000_000, bid_price=1, ask_price=3, price=3.5),
            self._mk(id=2, side="ASKSIDE", premium=100_000, bid_price=1, ask_price=3, price=3.5),
        ]
        s = conviction_score(rows)
        assert s.spread["wide_count"] == 2
        assert len(s.spread["wide_alert"]) == 1  # solo el de $2M alerta
        assert s.spread["wide_alert"][0].premium == 2_000_000

    def test_sin_trades_todo_en_cero(self):
        s = conviction_score([])
        assert s.score == 0
        assert s.dominance["points"] == 0


class TestInusualidadTabla:
    def test_tamano_de_ordenes(self):
        assert order_size_score(6_000_000) == 10
        assert order_size_score(2_000_000) == 8
        assert order_size_score(700_000) == 7
        assert order_size_score(300_000) == 5
        assert order_size_score(150_000) == 3
        assert order_size_score(50_000) == 0

    def test_delta_usa_el_valor_absoluto(self):
        assert delta_score(0.9) == 10
        assert delta_score(-0.9) == 10
        assert delta_score(0.75) == 8
        assert delta_score(-0.65) == 7
        assert delta_score(0.55) == 5
        assert delta_score(0.3) == 0

    def test_theta_como_pct_de_decaimiento_diario(self):
        assert theta_score(0.5) == 10
        assert theta_score(2) == 8
        assert theta_score(4) == 5
        assert theta_score(9) == 0
        assert theta_score(None) == 0

    def test_gamma_zona_institucional(self):
        assert gamma_score(0.02) == 10
        assert gamma_score(0.05) == 10
        assert gamma_score(0.1) == 8
        assert gamma_score(0.3) == 4
        assert gamma_score(0.005) == 2

    def test_single_leg_vale_mas_que_multileg(self):
        assert leg_score(False) == 10
        assert leg_score(True) == 5

    def test_vencimiento_los_leaps_puntuan_alto(self):
        assert expiry_score(320) == 10
        assert expiry_score(120) == 10
        assert expiry_score(95) == 8
        assert expiry_score(60) == 7
        assert expiry_score(30) == 5
        assert expiry_score(7) == 2
        assert expiry_score(None) == 0


class TestUnusualityScore:
    @staticmethod
    def _institucional(**over: Any):
        payload = {
            "symbol": "TSLA271217C00300000",  # vence 2027-12-17 -> LEAP
            "premium": 6_000_000,
            "delta": 0.85,
            "gamma": 0.03,
            "theta": -0.05,
            "price": 100,
        }
        payload.update(over)
        return classify_flow([trade(**payload)], NOW).rows[0]

    def test_un_trade_institucional_puntua_diez_en_todo(self):
        s = unusual_trade_score(self._institucional())
        assert (s.size, s.delta, s.theta, s.gamma, s.leg, s.expiry) == (10, 10, 10, 10, 10, 10)
        assert s.total == 10

    def test_un_trade_chico_y_de_corto_plazo_puntua_bajo(self):
        row = classify_flow(
            [
                trade(
                    symbol="TSLA260724C00300000",  # vence en 2 dias
                    premium=120_000, delta=0.2, gamma=0.005, theta=-8, price=100,
                )
            ],
            NOW,
        ).rows[0]
        s = unusual_trade_score(row)
        assert s.size == 3
        assert s.delta == 0
        assert s.expiry == 2
        assert s.total < 5

    def test_la_categoria_pondera_por_premium_y_cuenta_los_inusuales(self):
        grande = self._institucional(id=1)
        chico = classify_flow(
            [
                trade(
                    id=2, symbol="TSLA260724C00300000", premium=100_000,
                    delta=0.1, gamma=0.005, theta=-9, price=100,
                )
            ],
            NOW,
        ).rows[0]
        s = unusuality_score([grande, chico])
        assert s.n == 2
        assert s.unusual_count == 1  # solo el institucional pasa el umbral
        assert s.score >= 9  # el de $6M domina la ponderacion
        assert s.top[0][0].id == 1

    def test_sin_trades_devuelve_cero(self):
        assert unusuality_score([]).score == 0


class TestDetectClusters:
    BASE = "2026-07-22T15:00:00Z"

    def _row(self, id: int, side: str, premium: float, sec_offset: float):
        return classify_flow(
            [trade(id=id, side=side, premium=premium, timestamp=_iso(self.BASE, sec_offset))],
            NOW,
        ).rows[0]

    def test_agrupa_un_burst_de_tres_con_premium_suficiente(self):
        rows = [
            self._row(1, "ASKSIDE", 300_000, 0),
            self._row(2, "ASKSIDE", 300_000, 60),
            self._row(3, "ASKSIDE", 300_000, 120),
        ]
        cl = detect_clusters(rows)
        assert len(cl) == 1
        assert cl[0].count == 3
        assert cl[0].direction == "ask"
        assert cl[0].premium == 900_000

    def test_no_forma_racimo_si_el_premium_es_bajo(self):
        rows = [
            self._row(1, "ASKSIDE", 50_000, 0),
            self._row(2, "ASKSIDE", 50_000, 60),
            self._row(3, "ASKSIDE", 50_000, 120),
        ]
        assert detect_clusters(rows) == []

    def test_no_forma_racimo_con_menos_de_tres(self):
        rows = [self._row(1, "ASKSIDE", 400_000, 0), self._row(2, "ASKSIDE", 400_000, 60)]
        assert detect_clusters(rows) == []

    def test_separa_en_dos_racimos_si_hay_gap_mayor_a_cinco_min(self):
        rows = [
            self._row(1, "ASKSIDE", 300_000, 0),
            self._row(2, "ASKSIDE", 300_000, 60),
            self._row(3, "ASKSIDE", 300_000, 120),
            self._row(4, "BIDSIDE", 300_000, 600),
            self._row(5, "BIDSIDE", 300_000, 660),
            self._row(6, "BIDSIDE", 300_000, 720),
        ]
        cl = detect_clusters(rows)
        assert len(cl) == 2
        assert cl[0].direction == "ask"
        assert cl[1].direction == "bid"

    def test_direccion_es_el_lado_con_mas_premium(self):
        rows = [
            self._row(1, "ASKSIDE", 800_000, 0),
            self._row(2, "BIDSIDE", 200_000, 60),
            self._row(3, "ASKSIDE", 200_000, 120),
        ]
        cl = detect_clusters(rows)
        assert cl[0].direction == "ask"
        assert cl[0].unidirectionality == pytest.approx(1_000_000 / 1_200_000)

    def test_comprar_puts_al_ask_es_apuesta_bajista(self):
        # el trade base es un put (TSLA...P00305000) comprado al ask
        rows = [
            self._row(1, "ASKSIDE", 300_000, 0),
            self._row(2, "ASKSIDE", 300_000, 60),
            self._row(3, "ASKSIDE", 300_000, 120),
        ]
        cl = detect_clusters(rows)
        assert cl[0].direction == "ask"
        assert cl[0].bet == "bajista"
        assert cl[0].bet_label == "Compraron PUTS"
        assert cl[0].put_premium == 900_000

    def test_comprar_calls_al_ask_es_apuesta_alcista(self):
        def call(id: int, secs: float):
            return classify_flow(
                [
                    trade(
                        id=id, side="ASKSIDE", premium=300_000,
                        symbol="TSLA261120C00400000", timestamp=_iso(self.BASE, secs),
                    )
                ],
                NOW,
            ).rows[0]

        cl = detect_clusters([call(1, 0), call(2, 60), call(3, 120)])
        assert cl[0].bet == "alcista"
        assert cl[0].bet_label == "Compraron CALLS"
