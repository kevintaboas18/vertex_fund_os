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
    """Port de `store.ts`. La forma es la suya: envoltorio `StoredTrades`,
    `SaveResult` de tres campos, dedupe por id y tope por cantidad."""

    def test_acumula_y_deduplica_por_id(self):
        rows = [_flow_row(1, "2026-07-30T15:00:00Z"), _flow_row(2, "2026-07-30T15:01:00Z")]
        save_trades("DEMO", rows)
        save_trades("DEMO", rows)  # mismo lote otra vez
        assert len(load_trades("DEMO").trades) == 2

    def test_trades_nuevos_no_pisan_los_viejos(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-29T15:00:00Z")])
        save_trades("DEMO", [_flow_row(2, "2026-07-30T15:00:00Z")])
        assert {t["id"] for t in load_trades("DEMO").trades} == {1, 2}

    def test_guarda_el_analisis_completo_no_un_recorte(self):
        # "los trades vienen ya clasificados/puntuados, así que se guarda el
        # análisis completo" — el archivo no puede perder score/flags/greeks.
        save_trades("DEMO", [_flow_row(1, "2026-07-30T15:00:00Z")])
        t = load_trades("DEMO").trades[0]
        assert set(t) >= {"id", "timestamp", "type", "strike", "expiration",
                          "asset_price", "premium", "aggression",
                          "score", "sentiment", "delta", "gamma", "theta", "vega",
                          "iv", "open_interest", "volume", "flags", "scores",
                          "expiry_status", "condition_code", "unusual", "interesting"}

    def test_el_analisis_mas_reciente_gana(self):
        # expiry_status se recalcula cada corrida: un contrato vigente pasa a
        # expirado y el archivo tiene que reflejarlo, no conservar la foto vieja.
        from dataclasses import replace
        viejo = replace(_flow_row(1, "2026-07-30T15:00:00Z"), expiry_status="vigente")
        nuevo = replace(_flow_row(1, "2026-07-30T15:00:00Z"), expiry_status="expirado")
        save_trades("DEMO", [viejo])
        save_trades("DEMO", [nuevo])
        assert load_trades("DEMO").trades[0]["expiry_status"] == "expirado"

    def test_el_envoltorio_dice_de_quien_es_y_cuando(self):
        st = save_trades("demo", [_flow_row(1, "2026-07-30T15:00:00Z")])
        assert st.total == 1 and st.added == 1
        stored = load_trades("DEMO")
        assert stored.ticker == "DEMO"
        assert stored.updated_at.endswith("Z")

    def test_save_result_cuenta_solo_las_nuevas(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-30T15:00:00Z")])
        r = save_trades("DEMO", [_flow_row(1, "2026-07-30T15:00:00Z"),
                                 _flow_row(2, "2026-07-30T15:01:00Z")])
        assert r.added == 1
        assert r.total == 2

    def test_first_seen_es_el_trade_mas_antiguo(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-28T15:00:00Z"),
                             _flow_row(2, "2026-07-30T15:00:00Z")])
        r = save_trades("DEMO", [_flow_row(3, "2026-07-29T15:00:00Z")])
        assert r.first_seen == "2026-07-28T15:00:00Z"

    def test_ordena_de_lo_mas_nuevo_a_lo_mas_viejo(self):
        save_trades("DEMO", [_flow_row(1, "2026-07-28T15:00:00Z"),
                             _flow_row(2, "2026-07-30T15:00:00Z"),
                             _flow_row(3, "2026-07-29T15:00:00Z")])
        assert [t["id"] for t in load_trades("DEMO").trades] == [2, 3, 1]

    def test_recorta_a_max_per_ticker_tirando_lo_mas_viejo(self):
        from wbj.tito.stores import MAX_PER_TICKER
        base = datetime(2020, 1, 1, tzinfo=timezone.utc)
        rows = [_flow_row(i, (base + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"))
                for i in range(MAX_PER_TICKER + 10)]
        r = save_trades("DEMO", rows)
        assert r.total == MAX_PER_TICKER
        assert r.added == MAX_PER_TICKER + 10        # `added` cuenta lo entrante, no lo que sobrevive
        ids = [t["id"] for t in load_trades("DEMO").trades]
        assert ids[0] == MAX_PER_TICKER + 9          # lo más nuevo arriba
        assert min(ids) == 10                        # los 10 más viejos se cayeron

    def test_sin_historial_devuelve_none_no_lista_vacia(self):
        assert load_trades("NUNCA") is None

    def test_el_ticker_no_puede_escaparse_del_directorio(self):
        # El ticker es entrada de usuario y acaba siendo una ruta. El saneado de
        # Víctor conserva el punto (`[^A-Z0-9._-]`), así que "../../ETC/PASSWD"
        # queda en "....ETCPASSWD": feo, pero sin barras no hay travesía.
        from wbj.tito.stores import _file_for, data_dir
        destino = _file_for("../../ETC/PASSWD")
        assert destino.name == "....ETCPASSWD.json"
        assert destino.resolve().parent == (data_dir() / "trades").resolve()

        save_trades("../../ETC/PASSWD", [_flow_row(1, "2026-07-30T15:00:00Z")])
        import os
        escritos = [os.path.join(r, f) for r, _, fs in os.walk(data_dir()) for f in fs]
        assert len(escritos) == 1
        assert os.path.realpath(escritos[0]).startswith(str(data_dir().resolve()))

    def test_un_archivo_corrupto_se_lee_como_sin_historial(self):
        from wbj.tito.stores import data_dir
        p = data_dir() / "trades"
        p.mkdir(parents=True, exist_ok=True)
        (p / "DEMO.json").write_text("{no soy json", encoding="utf-8")
        assert load_trades("DEMO") is None

    def test_una_lista_pelada_del_formato_viejo_no_se_lee_como_historial(self):
        # Antes el archivo era una lista; ahora es {ticker, updated_at, trades}.
        # Un archivo del formato viejo NO debe colarse como si fuera válido.
        from wbj.tito.stores import data_dir
        p = data_dir() / "trades"
        p.mkdir(parents=True, exist_ok=True)
        (p / "DEMO.json").write_text('[{"id": 1}]', encoding="utf-8")
        assert load_trades("DEMO") is None


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
