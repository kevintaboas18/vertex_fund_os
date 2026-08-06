"""Tests del orquestador `wbj.tito.scorecard` (end-to-end, sin red)."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

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


class TestLaVentanaAnchaDeConviccion:
    """Su `/api/flow` hace DOS descargas y de ahí salen dos universos.

    1. `period` (5d) · ≥$100K · 6 páginas          → Agresividad
    2. `period: "1m"` · ≥$1M · 15 pág · 30 días    → Convicción, Inusualidad,
                                                     Contexto IV, GEX y callPct

    El port corría los seis sub-agentes sobre la primera. Estos casos fijan que
    cada uno mira el universo que le toca.
    """

    @staticmethod
    def _tape(n, primer_id, iv, premium, tipo="call"):
        return [{
            "id": primer_id + i,
            "symbol": f"DEMO270115{'C' if tipo == 'call' else 'P'}00100000",
            "price": 2.0, "size": 800, "side": "AT_ASK",
            "bid_price": 1.9, "ask_price": 2.1, "premium": premium,
            "delta": 0.62, "gamma": 0.03, "theta": -0.04, "vega": 0.3,
            "implied_volatility": iv, "open_interest": 4000, "volume": 5200,
            "score": 8, "sentiment": "bullish",
            "timestamp": "2026-07-31T15:30:00Z",
        } for i in range(n)]

    def _corre(self, **kw):
        from datetime import datetime, timezone

        from wbj.tito.levels import LvlBar
        from wbj.tito.scorecard import run_scorecard
        from wbj.tito.structure import ChainRow

        now = datetime(2026, 7, 31, 18, tzinfo=timezone.utc)
        bars = [LvlBar(time=f"2026-0{4 + i // 30}-{i % 30 + 1:02d}",
                       high=101.0, low=99.0, close=100.0) for i in range(60)]
        chain = [ChainRow("call", "2026-09-18", float(s), 9000, 400, 9e7)
                 for s in range(90, 115, 5)]
        return run_scorecard("DEMO", self._tape(4, 1, 0.44, 200_000),
                             chain, bars, now=now, spot=100.0,
                             horizons=(20,), **kw)

    def test_sin_ventana_ancha_todo_sale_de_los_cinco_dias(self):
        r = self._corre()
        assert r.conviction_window == "5d"
        assert [x.id for x in r.conviction_flow] == [x.id for x in r.flow.interesting]

    def test_con_ventana_ancha_conviccion_puntua_sobre_ELLA(self):
        anchos = self._tape(6, 100, 2.9, 5_000_000)
        r = self._corre(conviction_trades=anchos)
        assert r.conviction_window == "30d"
        # Convicción/Inusualidad/Contexto IV miran los 6 anchos, no los 4 cortos.
        assert {x.id for x in r.conviction_flow} == {100, 101, 102, 103, 104, 105}
        assert r.conviction.n == 6
        assert r.unusuality.n == 6
        assert r.iv_context.iv["contracts"] == 6
        # …y la IV que sale es la de la ventana ancha (290%), no la corta (44%).
        assert r.iv_context.iv["current"] == pytest.approx(290.0)
        # Agresividad sigue mirando los 5 días.
        assert r.aggression.n == 4

    def test_el_gex_ancla_con_la_ventana_ancha(self):
        anchos = self._tape(6, 100, 2.9, 5_000_000)
        corto = self._corre()
        ancho = self._corre(conviction_trades=anchos)
        # Los mismos strikes, pero la gamma real que ancla viene de otro set.
        assert corto.gex.n == ancho.gex.n
        assert [n.strike for n in corto.gex.nodes] == [n.strike for n in ancho.gex.nodes]

    def test_los_niveles_usan_la_UNION_de_las_dos_ventanas(self):
        anchos = self._tape(6, 100, 2.9, 5_000_000)
        r = self._corre(conviction_trades=anchos)
        # `convRows ∪ notable` con dedupe por id: 6 + 4 = 10 flows distintos.
        from wbj.tito.scorecard import _unir
        assert len(_unir(r.conviction_flow, r.flow.interesting)) == 10

    def test_el_call_pct_sale_de_la_ventana_ancha(self):
        # Ventana ancha 100% puts contra una corta 100% calls: si el callPct
        # saliera de la corta daría 100, y de la ancha da 0.
        anchos = self._tape(6, 100, 2.9, 5_000_000, tipo="put")
        r = self._corre(conviction_trades=anchos)
        assert r.predictions[20].summary.startswith("A 20 días")
        assert "en puts" in r.predictions[20].summary
