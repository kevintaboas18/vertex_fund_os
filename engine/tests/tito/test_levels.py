"""Port de `web/lib/levels.test.ts` (26 casos)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from wbj.tito.levels import (
    ChainLevel,
    FlowLevel,
    GexLevel,
    LvlBar,
    Pivot,
    cluster_pivots,
    find_levels,
    find_pivots,
    recency_factor,
)

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def bar(time: str, high: float, low: float, close: float | None = None) -> LvlBar:
    return LvlBar(time=time, high=high, low=low, close=close if close is not None else (high + low) / 2)


def series(spec: dict[int, tuple[float, float]], n: int = 40) -> list[LvlBar]:
    """Serie plana con picos/valles inyectados en las posiciones que se pidan."""
    start = date(2026, 5, 1)
    out = []
    for i in range(n):
        h, l = spec.get(i, (101, 99))
        out.append(bar((start + timedelta(days=i)).isoformat(), h, l))
    return out


class TestFindPivots:
    def test_detecta_un_swing_high_aislado(self):
        p = find_pivots(series({10: (120, 110)}))
        highs = [x for x in p if x.kind == "high"]
        assert len(highs) == 1
        assert highs[0].price == 120

    def test_detecta_un_swing_low_aislado(self):
        p = find_pivots(series({12: (90, 80)}))
        lows = [x for x in p if x.kind == "low"]
        assert len(lows) == 1
        assert lows[0].price == 80

    def test_no_inventa_pivotes_en_una_serie_plana(self):
        assert find_pivots(series({})) == []

    def test_vacio_si_no_hay_barras_suficientes(self):
        assert find_pivots([bar("2026-07-01", 10, 9)]) == []

    def test_respeta_la_ventana_k(self):
        s = series({10: (120, 110), 12: (125, 115)})
        assert len([p for p in find_pivots(s, 1) if p.kind == "high"]) >= 2
        assert len([p for p in find_pivots(s, 5) if p.kind == "high"]) == 1


class TestClusterPivots:
    def test_junta_pivotes_casi_al_mismo_precio(self):
        c = cluster_pivots(
            [
                Pivot(price=299, time="2026-06-01", kind="high"),
                Pivot(price=301, time="2026-07-01", kind="high"),
            ],
            1,
        )
        assert len(c) == 1
        assert c[0].touches == 2
        assert c[0].price == pytest.approx(300, abs=1e-6)
        assert c[0].last_touch == "2026-07-01"

    def test_no_junta_precios_lejanos(self):
        c = cluster_pivots(
            [
                Pivot(price=100, time="2026-06-01", kind="high"),
                Pivot(price=200, time="2026-07-01", kind="high"),
            ],
            1,
        )
        assert len(c) == 2

    def test_cuenta_techos_y_suelos_por_separado(self):
        c = cluster_pivots(
            [
                Pivot(price=100, time="2026-06-01", kind="high"),
                Pivot(price=100.2, time="2026-06-10", kind="low"),
            ],
            1,
        )
        assert c[0].highs == 1
        assert c[0].lows == 1

    def test_sin_pivotes_devuelve_vacio(self):
        assert cluster_pivots([], 1) == []


class TestRecencyFactor:
    def test_lo_reciente_pesa_completo_y_lo_viejo_se_degrada(self):
        assert recency_factor("2026-07-20", NOW) == 1
        assert recency_factor("2026-06-01", NOW) == 0.75
        assert recency_factor("2026-03-01", NOW) == 0.5
        assert recency_factor("2025-01-01", NOW) == 0.3


BARS = series({8: (120, 118), 14: (121, 119), 20: (82, 80), 26: (81, 79)}, 40)


class TestFindLevels:
    def test_clasifica_resistencia_arriba_y_soporte_abajo(self):
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40)
        assert all(l.price > 100 for l in r.resistances)
        assert all(l.price < 100 for l in r.supports)

    def test_un_nivel_tocado_dos_veces_es_mas_fuerte(self):
        dos = series({8: (120, 118), 14: (120.5, 118.5)}, 40)
        una = series({8: (120, 118)}, 40)
        a = find_levels(dos, spot=100, now=NOW, rango_pct=40)
        b = find_levels(una, spot=100, now=NOW, rango_pct=40)
        assert a.resistances[0].strength > b.resistances[0].strength

    def test_la_venta_de_calls_refuerza_una_resistencia(self):
        flows = [FlowLevel(strike=120, type="call", aggression="bid", premium=50_000_000)]
        sin = find_levels(BARS, spot=100, now=NOW, rango_pct=40)
        con = find_levels(BARS, spot=100, now=NOW, rango_pct=40, flows=flows)
        r_sin = next(l for l in sin.resistances if abs(l.price - 120) < 2)
        r_con = next(l for l in con.resistances if abs(l.price - 120) < 2)
        assert r_con.strength > r_sin.strength
        assert r_con.sources.flow_premium == 50_000_000

    def test_la_venta_de_puts_refuerza_un_soporte(self):
        flows = [FlowLevel(strike=80, type="put", aggression="bid", premium=50_000_000)]
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40, flows=flows)
        s = next(l for l in r.supports if abs(l.price - 80) < 2)
        assert s.sources.flow_premium == 50_000_000

    def test_la_compra_no_construye_muro(self):
        comprado = [FlowLevel(strike=120, type="call", aggression="ask", premium=50_000_000)]
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40, flows=comprado)
        l = next(x for x in r.resistances if abs(x.price - 120) < 2)
        assert l.sources.flow_premium == 0

    def test_solo_calls_para_resistencia_y_puts_para_soporte(self):
        chain = [ChainLevel(strike=120, contract_type="put", open_interest=90_000, notional_value=1e9)]
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40, chain=chain)
        l = next(x for x in r.resistances if abs(x.price - 120) < 2)
        assert l.sources.open_interest == 0  # puts no sostienen una resistencia

    def test_un_strike_con_dinero_entra_aunque_no_haya_rebote(self):
        chain = [ChainLevel(strike=110, contract_type="call", open_interest=80_000, notional_value=8e8)]
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40, chain=chain)
        l = next((x for x in r.resistances if abs(x.price - 110) < 1), None)
        assert l is not None
        assert l.sources.touches == 0

    def test_la_confluencia_puntua_mas_que_cada_fuente_por_separado(self):
        chain = [ChainLevel(strike=120, contract_type="call", open_interest=50_000, notional_value=5e8)]
        solo_precio = find_levels(BARS, spot=100, now=NOW, rango_pct=40)
        ambos = find_levels(BARS, spot=100, now=NOW, rango_pct=40, chain=chain)
        a = next(l for l in solo_precio.resistances if abs(l.price - 120) < 2)
        b = next(l for l in ambos.resistances if abs(l.price - 120) < 2)
        assert b.strength > a.strength
        assert "confluencia" in b.why

    def test_descarta_strikes_con_oi_residual_y_sin_rebote(self):
        chain = [
            ChainLevel(strike=110, contract_type="call", open_interest=90_000, notional_value=9e8),
            ChainLevel(strike=111, contract_type="call", open_interest=40, notional_value=1e5),
            ChainLevel(strike=112, contract_type="call", open_interest=30, notional_value=1e5),
            ChainLevel(strike=113, contract_type="call", open_interest=20, notional_value=1e5),
            ChainLevel(strike=114, contract_type="call", open_interest=10, notional_value=1e5),
        ]
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40, chain=chain, tolerance_pct=0.4)
        precios = [round(l.price) for l in r.resistances]
        assert 110 in precios  # el grande entra
        assert 113 not in precios  # el ruido no

    def test_un_strike_pequeno_si_entra_si_el_precio_reacciono(self):
        chain = [
            ChainLevel(strike=120, contract_type="call", open_interest=5, notional_value=1e4),
            ChainLevel(strike=108, contract_type="call", open_interest=90_000, notional_value=9e8),
        ]
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40, chain=chain, tolerance_pct=1)
        assert any(abs(l.price - 120) < 2 for l in r.resistances)

    def test_descarta_niveles_fuera_del_rango_operativo(self):
        lejos = series({8: (500, 490)}, 40)
        r = find_levels(lejos, spot=100, now=NOW, rango_pct=25)
        assert all(abs(l.distance_pct) <= 25 for l in r.resistances)

    def test_marca_flipeado_un_techo_que_ahora_queda_debajo(self):
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40)
        s = next((l for l in r.supports if abs(l.price - 81) < 3), None)
        assert s is not None

    def test_expone_el_soporte_y_la_resistencia_mas_fuertes(self):
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40)
        assert r.key_support is not None
        assert r.key_resistance is not None
        assert r.key_support.strength >= max(l.strength for l in r.supports) - 0.001

    def test_la_fuerza_nunca_se_sale_de_cero_a_cien(self):
        chain = [ChainLevel(strike=120, contract_type="call", open_interest=5_000_000, notional_value=9e12)]
        flows = [FlowLevel(strike=120, type="call", aggression="bid", premium=9e12)]
        r = find_levels(
            BARS, spot=100, now=NOW, rango_pct=40, chain=chain, flows=flows,
            gex=[GexLevel(strike=120, net_gex=9e12)],
        )
        for l in [*r.supports, *r.resistances]:
            assert 0 <= l.strength <= 100

    def test_sin_barras_devuelve_reporte_vacio(self):
        r = find_levels([], spot=100, now=NOW)
        assert r.supports == []
        assert r.resistances == []

    def test_los_soportes_salen_del_mas_cercano_al_mas_lejano(self):
        r = find_levels(BARS, spot=100, now=NOW, rango_pct=40)
        for prev, cur in zip(r.supports, r.supports[1:]):
            assert cur.price < prev.price
        for prev, cur in zip(r.resistances, r.resistances[1:]):
            assert cur.price > prev.price
