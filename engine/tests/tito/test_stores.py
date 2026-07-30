"""Tests de la capa de persistencia (`wbj.tito.stores`).

Port del comportamiento de chainStore.ts / ivStore.ts / predictionStore.ts,
más los 5 casos de `predictionStore.test.ts`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from wbj.tito.levels import LvlBar
from wbj.tito.stores import (
    PredictionSnapshot,
    calibration_from_review,
    load_chain_history,
    load_iv_history,
    load_journal,
    load_trades,
    review_predictions,
    save_chain_snapshot,
    save_iv_snapshot,
    save_prediction,
    save_trades,
)
from wbj.tito.structure import ChainRow
from wbj.tito.flow import FlowFlags, FlowRow, TradeScores

NOW = datetime(2026, 7, 30, 16, 30, tzinfo=timezone.utc)  # 12:30 ET


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    """Cada test escribe en su propio directorio."""
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))


def _chain_rows():
    return [
        ChainRow(contract_type="call", expiration="2026-09-18", strike=100.0,
                 open_interest=500, volume=100, notional_value=5_000_000),
        ChainRow(contract_type="put", expiration="2026-09-18", strike=100.0,
                 open_interest=300, volume=50, notional_value=3_000_000),
    ]


def _flow_row(id_: int, ts: str) -> FlowRow:
    return FlowRow(
        id=id_, symbol="X", underlying="X", type="call", strike=100.0,
        expiration="2026-09-18", dte=50, price=2.0, size=10, side="AT_ASK",
        aggression="ask", asset_price=100.0, bid=1.9, ask=2.1, premium=200_000,
        delta=0.6, gamma=0.03, theta=-0.05, vega=0.1, theta_pct_daily=2.5,
        iv=0.45, open_interest=1000, volume=500, score=8, sentiment="bullish",
        timestamp=ts, condition_code=None, condition_name=None,
        flags=FlowFlags(), scores=TradeScores(),
    )


class TestChainStore:
    def test_guarda_y_recupera_una_foto(self):
        n = save_chain_snapshot("DEMO", _chain_rows(), NOW)
        assert n == 1
        hist = load_chain_history("DEMO")
        assert hist[0]["date"] == "2026-07-30"
        assert hist[0]["strikes"][0]["call_oi"] == 500
        assert hist[0]["strikes"][0]["put_oi"] == 300

    def test_dedupe_por_dia_de_mercado(self):
        save_chain_snapshot("DEMO", _chain_rows(), NOW)
        save_chain_snapshot("DEMO", _chain_rows(), NOW)
        assert len(load_chain_history("DEMO")) == 1

    def test_dias_distintos_se_acumulan(self):
        save_chain_snapshot("DEMO", _chain_rows(), NOW)
        save_chain_snapshot("DEMO", _chain_rows(), NOW - timedelta(days=1))
        assert len(load_chain_history("DEMO")) == 2

    def test_sin_historial_devuelve_vacio(self):
        assert load_chain_history("NUNCA") == []


class TestIvStore:
    def test_acumula_una_muestra_por_dia(self):
        for i in range(5):
            save_iv_snapshot("DEMO", 40 + i, NOW - timedelta(days=i))
        hist = load_iv_history("DEMO")
        assert len(hist) == 5
        assert hist[0]["date"] < hist[-1]["date"]  # ordenado

    def test_formato_compatible_con_iv_context_score(self):
        save_iv_snapshot("DEMO", 47.5, NOW)
        h = load_iv_history("DEMO")[0]
        assert set(h) == {"date", "avg_iv"}
        assert h["avg_iv"] == 47.5

    def test_ignora_iv_no_positiva(self):
        save_iv_snapshot("DEMO", 0, NOW)
        assert load_iv_history("DEMO") == []

    def test_a_los_60_dias_desplaza_al_proxy(self):
        # El umbral vive en ivcontext.MIN_IV_HISTORY_DAYS; aqui se comprueba que
        # el store puede alcanzarlo.
        from wbj.tito.ivcontext import MIN_IV_HISTORY_DAYS
        for i in range(MIN_IV_HISTORY_DAYS):
            save_iv_snapshot("DEMO", 40 + i * 0.1, NOW - timedelta(days=i))
        assert len(load_iv_history("DEMO")) >= MIN_IV_HISTORY_DAYS


class TestTradesStore:
    def test_acumula_y_deduplica_por_id(self):
        rows = [_flow_row(1, "2026-07-30T15:00:00Z"), _flow_row(2, "2026-07-30T15:01:00Z")]
        save_trades("DEMO", rows, NOW)
        save_trades("DEMO", rows, NOW)  # mismo lote otra vez
        assert len(load_trades("DEMO")) == 2

    def test_trades_nuevos_no_pisan_los_viejos(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-29T15:00:00Z")], NOW)
        save_trades("DEMO", [_flow_row(2, "2026-07-30T15:00:00Z")], NOW)
        assert {t["id"] for t in load_trades("DEMO")} == {1, 2}

    def test_guarda_lo_que_necesita_el_subagente_6(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-30T15:00:00Z")], NOW)
        t = load_trades("DEMO")[0]
        assert set(t) >= {"id", "timestamp", "type", "strike", "expiration",
                          "asset_price", "premium", "aggression"}


def _snap(d: str, base: float, spot: float = 100.0, direction: str = "up") -> PredictionSnapshot:
    return PredictionSnapshot(date=d, horizon_days=10, spot=spot,
                              bear=spot * 0.9, base=base, bull=spot * 1.1,
                              direction=direction)  # type: ignore[arg-type]


def _bars(start: str, n: int, close: float) -> list[LvlBar]:
    d0 = date.fromisoformat(start)
    return [LvlBar(time=(d0 + timedelta(days=i)).isoformat(),
                   high=close + 1, low=close - 1, close=close) for i in range(n)]


class TestPredictionStore:
    def test_guarda_y_deduplica_por_fecha_y_horizonte(self):
        save_prediction("DEMO", _snap("2026-07-30", 105))
        save_prediction("DEMO", _snap("2026-07-30", 107))  # pisa
        j = load_journal("DEMO")
        assert len(j) == 1
        assert j[0]["base"] == 107

    def test_sin_predicciones_vencidas_no_hay_sesgo(self):
        r = review_predictions([], [], NOW)
        assert r["matured_count"] == 0
        assert r["bias_pct"] is None

    def test_una_prediccion_a_medio_camino_no_se_juzga(self):
        s = _snap("2026-07-28", 105)
        r = review_predictions([s.__dict__], _bars("2026-07-29", 2, 101), NOW)
        assert r["evals"][0]["matured"] is False
        assert r["matured_count"] == 0

    def test_calcula_el_sesgo_firmado_del_target_base(self):
        # base 105, cerro en 100 -> error = (100-105)/100 = -5%
        s = _snap("2026-07-01", 105)
        r = review_predictions([s.__dict__], _bars("2026-07-02", 12, 100), NOW)
        assert r["matured_count"] == 1
        assert r["bias_pct"] == pytest.approx(-5.0, abs=1e-6)
        assert r["mean_abs_error_pct"] == pytest.approx(5.0, abs=1e-6)

    def test_detecta_si_el_precio_toco_el_target_base(self):
        s = _snap("2026-07-01", 105)
        tocado = review_predictions([s.__dict__], _bars("2026-07-02", 12, 106), NOW)
        no = review_predictions([s.__dict__], _bars("2026-07-02", 12, 99), NOW)
        assert tocado["base_touch_rate"] == 100
        assert no["base_touch_rate"] == 0

    def test_acierto_de_direccion(self):
        sube = _snap("2026-07-01", 105, direction="up")
        r = review_predictions([sube.__dict__], _bars("2026-07-02", 12, 104), NOW)
        assert r["direction_hit_rate"] == 100
        r2 = review_predictions([sube.__dict__], _bars("2026-07-02", 12, 96), NOW)
        assert r2["direction_hit_rate"] == 0

    def test_dice_que_escenario_quedo_mas_cerca(self):
        s = _snap("2026-07-01", 105)  # bear 90 / base 105 / bull 110
        r = review_predictions([s.__dict__], _bars("2026-07-02", 12, 109), NOW)
        assert r["best_counts"]["bull"] == 1

    def test_el_review_alimenta_la_calibracion(self):
        snaps = [_snap(f"2026-07-{d:02d}", 105).__dict__ for d in range(1, 7)]
        r = review_predictions(snaps, _bars("2026-07-02", 25, 100), NOW)
        cal = calibration_from_review(r)
        assert cal["samples"] == r["matured_count"]
        assert cal["bias_pct"] == r["bias_pct"]

    def test_con_5_vencidas_la_calibracion_ya_actua(self):
        from wbj.tito.prediction import calibration_shift_pct
        snaps = [_snap(f"2026-07-{d:02d}", 105).__dict__ for d in range(1, 7)]
        cal = calibration_from_review(review_predictions(snaps, _bars("2026-07-02", 25, 100), NOW))
        assert cal["samples"] >= 5
        assert calibration_shift_pct(cal["bias_pct"], cal["samples"]) != 0


def test_escritura_atomica_no_deja_json_truncado(tmp_path, monkeypatch):
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path))
    save_iv_snapshot("DEMO", 45, NOW)
    # No debe quedar ningun .tmp suelto tras una escritura correcta.
    assert not list((tmp_path / "iv").glob("*.tmp"))
    assert load_iv_history("DEMO")[0]["avg_iv"] == 45
