"""Port de `web/lib/ivcontext.test.ts` (28 casos)."""

from __future__ import annotations

import math
from typing import Any

import pytest

from wbj.tito.flow import FlowFlags, FlowRow, TradeScores
from wbj.tito.ivcontext import (
    iv_context_score,
    iv_points,
    iv_rank_points,
    rank_within,
    realized_vol_series,
)

CLOSES = [100 + math.sin(i / 3) * 5 for i in range(300)]


def row(**p: Any) -> FlowRow:
    base: dict[str, Any] = dict(
        id=1, symbol="TSLA260724C00347500", underlying="TSLA", type="call",
        strike=347.5, expiration="2026-07-24", dte=0, price=1, size=1,
        side="ask", aggression="ask", asset_price=320, bid=0.9, ask=1.1,
        premium=1000, delta=0.5, gamma=0.02, theta=-0.1, vega=0.1,
        theta_pct_daily=None, iv=0.5, open_interest=100, volume=10, score=50,
        sentiment="neutral", timestamp="2026-07-24T14:00:00Z",
        condition_code=None, condition_name=None,
        flags=FlowFlags(), scores=TradeScores(),
        unusual=False, interesting=False, expiry_status="vigente",
    )
    base.update(p)
    return FlowRow(**base)


class TestIvPoints:
    def test_respeta_cada_banda(self):
        assert iv_points(120).points == 6
        assert iv_points(100).points == 6
        assert iv_points(99).points == 5
        assert iv_points(90).points == 5
        assert iv_points(89).points == 8
        assert iv_points(61).points == 8
        assert iv_points(60).points == 10
        assert iv_points(50).points == 10
        assert iv_points(40).points == 10
        assert iv_points(39).points == 5
        assert iv_points(30).points == 5
        assert iv_points(29).points == 2

    def test_el_pico_esta_en_la_iv_moderada(self):
        assert iv_points(50).points > iv_points(95).points
        assert iv_points(50).points > iv_points(120).points
        assert iv_points(50).points > iv_points(25).points

    def test_castiga_la_volatilidad_cara_mas_que_la_moderada(self):
        assert iv_points(95).points < iv_points(75).points

    def test_marca_mas_de_cien_como_categoria_especial(self):
        assert iv_points(101).special is True
        assert iv_points(99).special is False

    def test_cubre_el_hueco_entre_89_y_90(self):
        assert iv_points(89.5).points == 8

    def test_sin_datos_es_cero(self):
        assert iv_points(float("nan")).points == 0
        assert iv_points(-1).points == 0


class TestIvRankPoints:
    def test_respeta_cada_banda(self):
        assert iv_rank_points(0).points == 2
        assert iv_rank_points(15).points == 2
        assert iv_rank_points(16).points == 10
        assert iv_rank_points(30).points == 10
        assert iv_rank_points(31).points == 8
        assert iv_rank_points(50).points == 8
        assert iv_rank_points(51).points == 5
        assert iv_rank_points(70).points == 5
        assert iv_rank_points(71).points == 1
        assert iv_rank_points(99).points == 1
        assert iv_rank_points(100).points == 0

    def test_premia_la_compresion_sobre_la_expansion(self):
        assert iv_rank_points(20).points > iv_rank_points(80).points

    def test_la_accion_dormida_puntua_bajo_no_alto(self):
        assert iv_rank_points(10).points == 2
        assert iv_rank_points(10).points < iv_rank_points(20).points
        assert iv_rank_points(10).points < iv_rank_points(40).points

    def test_decrece_monotono_desde_el_pico(self):
        tramos = [iv_rank_points(r).points for r in (16, 31, 51, 71, 100)]
        for prev, cur in zip(tramos, tramos[1:]):
            assert cur < prev


class TestRealizedVolSeries:
    def test_vacio_si_no_hay_barras_suficientes(self):
        assert realized_vol_series([1, 2, 3]) == []

    def test_una_accion_plana_tiene_volatilidad_cero(self):
        s = realized_vol_series([100] * 60)
        assert len(s) > 0
        assert s[-1] == pytest.approx(0, abs=1e-6)

    def test_mas_zigzag_mas_volatilidad(self):
        calma = [100 + (0.1 if i % 2 else -0.1) for i in range(80)]
        loca = [100 + (8 if i % 2 else -8) for i in range(80)]
        a, b = realized_vol_series(calma), realized_vol_series(loca)
        assert b[-1] > a[-1]


class TestRankWithin:
    def test_situa_el_valor_dentro_del_rango(self):
        assert rank_within([10, 20, 30], 10) == 0
        assert rank_within([10, 20, 30], 30) == 100
        assert rank_within([10, 20, 30], 20) == 50

    def test_serie_plana_devuelve_none(self):
        assert rank_within([5, 5, 5], 5) is None

    def test_recorta_fuera_de_rango(self):
        assert rank_within([10, 30], 99) == 100


class TestIvContextScore:
    def test_sin_trades_con_iv_devuelve_reporte_vacio(self):
        s = iv_context_score([row(iv=0)], CLOSES)
        assert s.score == 0
        assert s.regime == "desconocido"

    def test_convierte_la_iv_decimal_a_porcentaje(self):
        s = iv_context_score([row(iv=0.477)], CLOSES)
        assert s.iv["current"] == pytest.approx(47.7, abs=0.1)
        assert s.iv["points"] == 10  # 40-60% - la zona buena

    def test_pondera_por_premium(self):
        s = iv_context_score(
            [
                row(id=1, iv=0.90, premium=1_000_000),
                row(id=2, iv=0.30, premium=1_000),
                row(id=3, iv=0.30, premium=1_000),
            ],
            CLOSES,
        )
        assert s.iv["current"] > 85
        assert s.iv["simple_avg"] < s.iv["current"]

    def test_agrupa_promedio_de_iv_por_vencimiento_ordenado_por_cercania(self):
        s = iv_context_score(
            [
                row(id=1, expiration="2026-07-24", dte=0, iv=0.80),
                row(id=2, expiration="2026-07-24", dte=0, iv=0.60),
                row(id=3, expiration="2026-12-18", dte=147, iv=0.50),
            ],
            CLOSES,
        )
        assert len(s.by_expiration) == 2
        assert s.by_expiration[0].expiration == "2026-07-24"
        assert s.by_expiration[0].avg_iv == pytest.approx(70, abs=1e-6)
        assert s.by_expiration[0].trades == 2
        assert s.by_expiration[0].max_iv == pytest.approx(80, abs=1e-6)

    def test_detecta_el_skew_del_frente(self):
        s = iv_context_score(
            [
                row(id=1, expiration="2026-07-24", dte=0, iv=0.90),
                row(id=2, expiration="2026-09-18", dte=56, iv=0.45),
                row(id=3, expiration="2026-12-18", dte=147, iv=0.45),
            ],
            CLOSES,
        )
        assert s.front_skew == pytest.approx(45, abs=1e-6)

    def test_lista_los_contratos_de_mayor_iv_primero(self):
        s = iv_context_score(
            [row(id=1, iv=0.40), row(id=2, iv=0.95), row(id=3, iv=0.60)], CLOSES
        )
        assert [c.id for c in s.top_contracts] == [2, 3, 1]
        assert s.top_contracts[0].iv == pytest.approx(95, abs=1e-6)

    def test_usa_el_proxy_cuando_no_hay_historia_de_iv(self):
        s = iv_context_score([row(iv=0.5)], CLOSES)
        assert s.rank["source"] == "realized-proxy"
        assert s.rank["value"] is not None

    def test_la_historia_propia_desplaza_al_proxy_a_los_60_dias(self):
        iv_history = [
            {"date": f"2026-05-{(i % 28) + 1:02d}", "avg_iv": 30 + i} for i in range(60)
        ]
        s = iv_context_score([row(iv=0.60)], CLOSES, iv_history)
        assert s.rank["source"] == "iv-history"
        assert s.rank["days"] == 60
        # 60 dentro de [30, 89] -> ~50.8
        assert s.rank["value"] == pytest.approx(50.8, abs=0.1)

    def test_con_poca_historia_se_queda_con_el_proxy(self):
        iv_history = [{"date": f"d{i}", "avg_iv": 40 + i} for i in range(10)]
        s = iv_context_score([row(iv=0.5)], CLOSES, iv_history)
        assert s.rank["source"] == "realized-proxy"

    def test_iv_sobre_cien_marca_regimen_inflado(self):
        s = iv_context_score([row(iv=1.4)], CLOSES)
        assert s.iv["special"] is True
        assert s.regime == "inflada"
        assert "IV crush" in s.note

    def test_el_score_es_el_promedio_de_los_dos_parametros(self):
        iv_history = [{"date": f"d{i}", "avg_iv": 40 + i} for i in range(60)]
        s = iv_context_score([row(iv=0.85)], CLOSES, iv_history)
        assert s.iv["points"] == 8  # 85% -> 61-89%
        # `js_round`, no `round`: su `Math.round((iv.points + rank.points) / 2)`
        # redondea la mitad hacia ARRIBA y el `round` de Python al par. Con 8 y 1
        # la media es 4.5 → él da 5 y Python daba 4. Esta línea afirmaba el bug:
        # comparaba el port contra la semántica de Python en vez de contra la
        # suya, y por eso la suite portada nunca lo vio. Lo destapó el
        # diferencial de `levels.ts` (ver `wbj/tito/jsmath.py`).
        from wbj.tito.jsmath import js_round

        assert s.score == js_round((s.iv["points"] + s.rank["points"]) / 2)
        assert s.score == 5

    def test_el_score_nunca_se_sale_de_cero_a_diez(self):
        for iv in (0.05, 0.35, 0.45, 0.65, 0.85, 1.5):
            s = iv_context_score([row(iv=iv)], CLOSES)
            assert 0 <= s.score <= 10


class TestElRedondeoEsElDeJS:
    """`Math.round` de JS y `round` de Python NO son la misma función.

    Python usa redondeo bancario (mitad al par) y JS la manda hacia arriba. Los
    sub-agentes puntúan promediando enteros, así que el `.5` sale constantemente:
    de las 121 combinaciones de dos puntuaciones 0-10, **30 caen en un `.5` donde
    las dos funciones difieren**, y en todas ellas el port devolvía un punto
    MENOS que él — directo al scorecard de 0-100.

    Lo destapó el diferencial de `levels.ts`: una fuerza de nivel salía 10 donde
    su archivo daba 11.
    """

    def test_la_mitad_va_hacia_arriba_como_en_JS(self):
        from wbj.tito.jsmath import js_round

        # Los casos donde Python discrepa de `Math.round`.
        assert js_round(0.5) == 1 and round(0.5) == 0
        assert js_round(2.5) == 3 and round(2.5) == 2
        assert js_round(4.5) == 5 and round(4.5) == 4
        assert js_round(10.5) == 11 and round(10.5) == 10
        # …y donde coinciden, coinciden.
        for v in (1.5, 3.5, 11.5, 1.4, 1.6, 0.0, 7.0):
            assert js_round(v) == round(v), v

    def test_los_negativos_siguen_su_regla_floor_x_mas_medio(self):
        # ECMA-262 define `Math.round` como `floor(x + 0.5)`, así que
        # `Math.round(-10.5)` es -10, no -11.
        from wbj.tito.jsmath import js_round

        assert js_round(-10.5) == -10
        assert js_round(-2.5) == -2
        assert js_round(-2.6) == -3

    def test_ningun_modulo_del_motor_usa_ya_el_round_de_Python_para_puntuar(self):
        """Barrido: todo `round(x)` de un argumento que espeje un `Math.round`
        suyo tiene que ser `js_round`."""
        import re
        from pathlib import Path

        tito = Path(__file__).resolve().parents[2] / "wbj" / "tito"
        sospechosos = []
        for f in sorted(tito.glob("*.py")):
            if f.name == "jsmath.py":
                continue
            for n, linea in enumerate(f.read_text().splitlines(), 1):
                # Las lineas que CITAN su TypeScript en un docstring no cuentan:
                # `Math.round(...)` es la fuente, no una llamada del port.
                if "Math.round(" in linea:
                    continue
                if re.search(r"(?<!js_)\bround\([^,()]*(\([^()]*\))?[^,()]*\)(?!\s*,)", linea) \
                        and "round(" in linea and not re.search(r"round\([^)]*,", linea):
                    sospechosos.append(f"{f.name}:{n}: {linea.strip()}")
        assert not sospechosos, "usan el round() de Python:\n  " + "\n  ".join(sospechosos)
