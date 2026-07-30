"""Port de `web/lib/validation.test.ts` (29 casos)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from wbj.tito.validation import (
    FlowLite,
    ValBar,
    evaluate_flow,
    flow_direction,
    median,
    speed_points,
    validation_points,
    validation_score,
)

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def flow(**p: Any) -> FlowLite:
    base: dict[str, Any] = dict(
        id=1, timestamp="2026-07-01T15:00:00Z", type="call", strike=100,
        expiration="2026-12-18", asset_price=100, premium=1_000_000, aggression="ask",
    )
    base.update(p)
    return FlowLite(**base)


def bars(spec: dict[str, tuple[float, float, float]]) -> list[ValBar]:
    """Barras planas en 100 salvo las que se pisen."""
    return [ValBar(time=t, high=h, low=l, close=c) for t, (h, l, c) in spec.items()]


class TestFlowDirection:
    def test_comprar_call_es_alcista(self):
        assert flow_direction("call", "ask") == "alcista"

    def test_vender_call_es_bajista(self):
        assert flow_direction("call", "bid") == "bajista"

    def test_comprar_put_es_bajista(self):
        assert flow_direction("put", "ask") == "bajista"

    def test_vender_put_es_alcista(self):
        assert flow_direction("put", "bid") == "alcista"

    def test_medio_o_desconocido_es_neutral(self):
        assert flow_direction("call", "mid") == "neutral"
        assert flow_direction("unknown", "ask") == "neutral"


class TestEvaluateFlow:
    def test_ignora_el_dia_del_propio_flow(self):
        o = evaluate_flow(flow(), bars({"2026-07-01": (130, 100, 130)}), NOW)
        assert o.sessions_observed == 0
        assert o.mfe_pct == 0

    def test_un_call_comprado_que_sube_se_valida(self):
        o = evaluate_flow(
            flow(),
            bars({"2026-07-02": (101, 99.5, 100.5), "2026-07-03": (103, 100, 102.5)}),
            NOW,
        )
        assert o.direction == "alcista"
        assert o.days_to_validate == 2
        assert o.validated is True
        assert o.mfe_pct == pytest.approx(3, abs=1e-6)
        assert o.days_to_mfe == 2

    def test_mide_la_excursion_adversa_aunque_termine_validando(self):
        o = evaluate_flow(
            flow(),
            bars({"2026-07-02": (100.5, 96, 97), "2026-07-03": (104, 100, 103)}),
            NOW,
        )
        assert o.mae_pct == pytest.approx(4, abs=1e-6)
        assert o.days_to_invalidate == 1
        assert o.days_to_validate == 2
        assert o.validated is False  # se fue en contra ANTES

    def test_un_put_comprado_se_valida_cuando_el_precio_baja(self):
        o = evaluate_flow(
            flow(type="put", aggression="ask"), bars({"2026-07-02": (100.5, 97, 97.5)}), NOW
        )
        assert o.direction == "bajista"
        assert o.validated is True
        assert o.mfe_pct == pytest.approx(3, abs=1e-6)

    def test_no_juzga_un_flow_demasiado_reciente(self):
        o = evaluate_flow(
            flow(timestamp="2026-07-23T15:00:00Z"),
            bars({"2026-07-24": (100.5, 99.8, 100.2)}),
            NOW,
        )
        assert o.resolved is False
        assert o.validated is False

    def test_no_mira_mas_alla_del_vencimiento(self):
        o = evaluate_flow(
            flow(expiration="2026-07-02"),
            bars({"2026-07-02": (100.5, 99.9, 100.1), "2026-07-03": (140, 100, 139)}),
            NOW,
        )
        assert o.sessions_observed == 1
        assert o.mfe_pct < 1

    def test_respeta_el_horizonte_de_sesiones(self):
        many = {f"2026-07-{i:02d}": (100.1, 99.9, 100.0) for i in range(2, 29)}
        o = evaluate_flow(flow(), bars(many), NOW, 2, 5)
        assert o.sessions_observed == 5
        assert o.resolved is True  # horizonte agotado

    def test_un_flow_neutral_no_se_evalua(self):
        o = evaluate_flow(flow(aggression="mid"), bars({"2026-07-02": (110, 100, 109)}), NOW)
        assert o.direction == "neutral"
        assert o.mfe_pct == 0

    def test_reporta_los_dias_naturales_transcurridos(self):
        o = evaluate_flow(
            flow(timestamp="2026-07-04T12:00:00Z"), bars({"2026-07-06": (101, 100, 101)}), NOW
        )
        assert o.days_elapsed == 20


class TestValidationPoints:
    def test_respeta_las_bandas(self):
        assert validation_points(75).points == 10
        assert validation_points(70).points == 10
        assert validation_points(65).points == 8
        assert validation_points(55).points == 6
        assert validation_points(45).points == 4
        assert validation_points(35).points == 2
        assert validation_points(10).points == 0

    def test_sin_datos_es_cero(self):
        assert validation_points(None).points == 0


class TestSpeedPoints:
    def test_premia_la_reaccion_rapida(self):
        assert speed_points(1).points == 10
        assert speed_points(4).points == 8
        assert speed_points(8).points == 6
        assert speed_points(13).points == 4
        assert speed_points(19).points == 2

    def test_decrece_de_forma_monotona(self):
        t = [speed_points(d).points for d in (2, 5, 10, 15, 20)]
        for prev, cur in zip(t, t[1:]):
            assert cur < prev


class TestMedian:
    def test_impares_y_pares(self):
        assert median([3, 1, 2]) == 2
        assert median([4, 1, 2, 3]) == 2.5

    def test_vacio_es_none(self):
        assert median([]) is None


UP_BARS = bars(
    {
        "2026-07-02": (101, 99.5, 100.8),
        "2026-07-03": (104, 100.5, 103.5),
        "2026-07-06": (105, 102, 104),
    }
)


class TestValidationScore:
    def test_sin_flows_reporte_vacio(self):
        assert validation_score([], UP_BARS, NOW).score == 0

    def test_calcula_la_tasa_de_validacion(self):
        s = validation_score(
            [flow(id=1, type="call", aggression="ask"), flow(id=2, type="put", aggression="ask")],
            UP_BARS,
            NOW,
        )
        assert s.hit_rate["resolved"] == 2
        assert s.hit_rate["validated"] == 1
        assert s.hit_rate["value"] == pytest.approx(50, abs=1e-6)

    def test_pondera_por_premium(self):
        s = validation_score(
            [
                flow(id=1, type="call", aggression="ask", premium=10_000_000),
                flow(id=2, type="put", aggression="ask", premium=100_000),
            ],
            UP_BARS,
            NOW,
        )
        assert s.hit_rate["value"] == pytest.approx(50, abs=1e-6)
        assert s.weighted_hit_rate > 95  # el grande acerto

    def test_separa_el_acierto_por_direccion(self):
        s = validation_score(
            [
                flow(id=1, type="call", aggression="ask"),
                flow(id=2, type="call", aggression="ask"),
                flow(id=3, type="put", aggression="ask"),
            ],
            UP_BARS,
            NOW,
        )
        alcista = next(d for d in s.by_direction if d["direction"] == "alcista")
        bajista = next(d for d in s.by_direction if d["direction"] == "bajista")
        assert alcista["hit_rate"] == pytest.approx(100, abs=1e-6)
        assert bajista["hit_rate"] == pytest.approx(0, abs=1e-6)

    def test_marca_cuando_el_historial_no_llega_a_60_dias(self):
        s = validation_score([flow()], UP_BARS, NOW)
        assert s.coverage["below_target"] is True
        assert s.coverage["flows"] == 1

    def test_no_marca_below_target_con_60_dias_o_mas(self):
        s = validation_score(
            [
                flow(id=1, timestamp="2026-05-01T15:00:00Z"),
                flow(id=2, timestamp="2026-07-01T15:00:00Z"),
            ],
            UP_BARS,
            NOW,
        )
        assert s.coverage["days"] >= 60
        assert s.coverage["below_target"] is False

    def test_cuenta_como_pendientes_los_flows_recientes(self):
        s = validation_score([flow(id=9, timestamp="2026-07-06T15:00:00Z")], UP_BARS, NOW)
        assert s.coverage["pending"] == 1
        assert s.hit_rate["resolved"] == 0

    def test_el_score_es_el_promedio_de_los_dos_parametros(self):
        s = validation_score([flow(id=1), flow(id=2), flow(id=3)], UP_BARS, NOW)
        assert s.score == round((s.hit_rate["points"] + s.speed["points"]) / 2)
        assert 0 <= s.score <= 10

    def test_los_flows_neutrales_quedan_fuera(self):
        s = validation_score(
            [flow(id=1, aggression="mid"), flow(id=2, aggression="ask")], UP_BARS, NOW
        )
        assert s.coverage["flows"] == 1
