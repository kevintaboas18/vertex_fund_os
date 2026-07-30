"""Tests del orquestador `wbj.tito.scorecard` (end-to-end, sin red)."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from wbj.tito.levels import LvlBar
from wbj.tito.scorecard import run_scorecard, verdict_of
from wbj.tito.structure import ChainRow

NOW = datetime(2026, 7, 30, 16, 30, 0, tzinfo=timezone.utc)  # 12:30 ET
SPOT = 100.0


def bars(n: int = 250) -> list[LvlBar]:
    seed = 42
    out = []
    start = date(2025, 11, 1)
    for i in range(n):
        seed = (seed * 1103515245 + 12345) % 2147483648
        rnd = seed / 2147483648
        close = 92 + i * 0.05 + 9 * math.sin(i / 17) + 4 * math.sin(i / 6.5) + (rnd - 0.5) * 1.6
        out.append(LvlBar(time=(start + timedelta(days=i)).isoformat(),
                          high=close + 1.2, low=close - 1.2, close=close))
    out[-1] = LvlBar(time=out[-1].time, high=SPOT + 1, low=SPOT - 1, close=SPOT)
    return out


def chain() -> list[ChainRow]:
    rows = []
    for exp in ("2026-09-18", "2027-01-15"):
        for strike in range(70, 135, 5):
            for ct in ("call", "put"):
                wall = (ct == "call" and strike in (105, 110)) or (ct == "put" and strike == 90)
                base = 1500 * math.exp(-((strike - SPOT) ** 2) / 400)
                oi = int(base * (8 if wall else 1)) + 60
                rows.append(ChainRow(
                    contract_type=ct, expiration=exp, strike=float(strike),
                    open_interest=oi, volume=int(oi * (1.4 if wall else 0.25)),
                    notional_value=oi * 100 * strike,
                ))
    return rows


def trades() -> list[dict[str, Any]]:
    out = []
    tid = 1
    for strike, size, price, side, minute in (
        (105, 900, 9.20, "AT_ASK", 3),
        (105, 750, 9.25, "ABOVE_ASK", 2),
        (105, 640, 9.30, "AT_ASK", 1),
        (110, 520, 7.10, "ASKSIDE", 6),
        (90, 300, 2.40, "AT_BID", 40),
    ):
        cp = "C" if strike >= 100 else "P"
        ts = (NOW - timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")
        out.append({
            "id": tid, "symbol": f"DEMO270115{cp}{strike * 1000:08d}",
            "price": price, "size": size, "side": side,
            "bid_price": price - 0.06, "ask_price": price + 0.06,
            "premium": price * size * 100, "delta": 0.62 if cp == "C" else -0.28,
            "gamma": 0.03, "theta": -0.04, "vega": 0.3,
            "implied_volatility": 0.44, "open_interest": 4000, "volume": 5200,
            "score": 8, "sentiment": "bullish", "timestamp": ts,
            "asset_price": SPOT, "trade_condition_id": 231,
        })
        tid += 1
    return out


def test_verdict_of_respeta_las_bandas():
    assert verdict_of(80)[0] == "Oportunidad Fuerte"
    assert verdict_of(75)[0] == "Oportunidad Fuerte"
    assert verdict_of(60)[0] == "Oportunidad Moderada"
    assert verdict_of(50)[0] == "Oportunidad Moderada"
    assert verdict_of(20)[0] == "Oportunidad Débil"


def test_pipeline_completo_produce_scorecard_y_escenarios():
    r = run_scorecard("DEMO", trades(), chain(), bars(), NOW, spot=SPOT)
    assert r.ticker == "DEMO"
    assert 0 <= r.score <= 100
    assert r.verdict in ("Oportunidad Fuerte", "Oportunidad Moderada", "Oportunidad Débil")
    assert set(r.predictions) == {10, 20, 30}
    for p in r.predictions.values():
        assert p.bear.target < p.base.target < p.bull.target


def test_los_seis_scores_salen_en_el_reporte():
    r = run_scorecard("DEMO", trades(), chain(), bars(), NOW, spot=SPOT)
    assert set(r.scores) == {
        "aggression", "conviction", "unusuality", "structure", "iv_context", "validation",
    }
    for k in ("aggression", "conviction", "unusuality", "structure"):
        assert r.scores[k] is not None, k


def test_una_categoria_sin_evidencia_va_como_none_no_como_cero():
    # Sin tape: las 4 categorias que dependen del flujo quedan en None.
    r = run_scorecard("DEMO", [], chain(), bars(), NOW, spot=SPOT)
    assert r.scores["aggression"] is None
    assert r.scores["conviction"] is None
    assert r.scores["unusuality"] is None
    assert r.scores["structure"] is not None  # la cadena si esta
    assert r.active < 6


def test_avisa_cuando_faltan_subagentes():
    r = run_scorecard("DEMO", [], chain(), bars(), NOW, spot=SPOT)
    assert any("de 6 sub-agentes" in w for w in r.warnings)
    assert any("NO es comparable" in w for w in r.warnings)


def test_la_salvaguarda_de_liquidez_aparece_en_las_advertencias():
    flaca = [
        ChainRow(contract_type="call", expiration="2026-09-18", strike=100.0,
                 open_interest=5, volume=1, notional_value=5 * 100 * 100)
    ]
    r = run_scorecard("DEMO", [], flaca, bars(), NOW, spot=SPOT)
    assert any("SALVAGUARDA DE LIQUIDEZ" in w for w in r.warnings)
    for p in r.predictions.values():
        assert p.confidence == 0
        assert "NO FIABLE" in p.caveat


def test_declara_que_las_bandas_del_subagente_seis_son_propuesta():
    # Solo aplica cuando la categoria 6 tiene dato; si no, no debe mentir.
    r = run_scorecard("DEMO", trades(), chain(), bars(), NOW, spot=SPOT)
    if r.scores["validation"] is not None:
        assert any("PROPUESTA" in w for w in r.warnings)


def test_el_spot_cae_al_ultimo_cierre_si_no_se_pasa():
    b = bars()
    r = run_scorecard("DEMO", [], chain(), b, NOW)
    assert r.spot == b[-1].close


def test_la_calibracion_por_memoria_mueve_el_target_base():
    sin = run_scorecard("DEMO", trades(), chain(), bars(), NOW, spot=SPOT)
    con = run_scorecard(
        "DEMO", trades(), chain(), bars(), NOW, spot=SPOT,
        calibration={"bias_pct": 2, "samples": 8},
    )
    assert con.predictions[20].calibration["applied"] is True
    assert con.predictions[20].base.target != sin.predictions[20].base.target
