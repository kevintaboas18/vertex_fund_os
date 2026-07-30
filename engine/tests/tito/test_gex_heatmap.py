"""Tests de `wbj.tito.gex_heatmap`.

`gexHeatmap.ts` NO tiene suite en el proyecto original — estos casos son nuevos,
escritos contra el comportamiento que el módulo declara, para que el port no
quede sin red.
"""

from __future__ import annotations

from datetime import datetime, timezone

from wbj.tito.gex_heatmap import HeatTrade, gex_heatmap
from wbj.tito.structure import ChainRow

NOW = datetime(2026, 7, 23, 15, 0, 0, tzinfo=timezone.utc)


def row(strike: float, type_: str, oi: int, exp: str) -> ChainRow:
    return ChainRow(
        contract_type=type_,  # type: ignore[arg-type]
        expiration=exp, strike=strike, open_interest=oi, volume=0,
        notional_value=strike * oi * 100,
    )


def test_vacio_sin_filas_o_sin_spot():
    assert gex_heatmap([], 100, 0.4, NOW).cells == []
    assert gex_heatmap([row(100, "call", 100, "2026-08-21")], 0, 0.4, NOW).cells == []


def test_una_celda_por_strike_y_vencimiento():
    rows = [
        row(100, "call", 1000, "2026-08-21"),
        row(100, "call", 1000, "2026-09-18"),
        row(105, "call", 1000, "2026-08-21"),
    ]
    h = gex_heatmap(rows, 100, 0.4, NOW)
    assert len(h.cells) == 3
    assert {c.expiration for c in h.cells} == {"2026-08-21", "2026-09-18"}
    assert {s.strike for s in h.strikes} == {100, 105}


def test_calls_suman_y_puts_restan_al_gex_neto():
    solo_calls = gex_heatmap([row(100, "call", 1000, "2026-08-21")], 100, 0.4, NOW)
    solo_puts = gex_heatmap([row(100, "put", 1000, "2026-08-21")], 100, 0.4, NOW)
    assert solo_calls.total_net_gex > 0
    assert solo_puts.total_net_gex < 0


def test_los_strikes_salen_de_mayor_a_menor_precio():
    rows = [row(s, "call", 1000, "2026-08-21") for s in (95, 100, 105)]
    h = gex_heatmap(rows, 100, 0.4, NOW)
    precios = [s.strike for s in h.strikes]
    assert precios == sorted(precios, reverse=True)


def test_los_vencimientos_salen_del_mas_cercano_al_mas_lejano():
    rows = [row(100, "call", 1000, e) for e in ("2026-12-18", "2026-08-21", "2026-09-18")]
    h = gex_heatmap(rows, 100, 0.4, NOW)
    assert [e.expiration for e in h.expirations] == [
        "2026-08-21", "2026-09-18", "2026-12-18",
    ]


def test_ignora_contratos_sin_open_interest():
    h = gex_heatmap(
        [row(100, "call", 0, "2026-08-21"), row(105, "call", 1000, "2026-08-21")],
        100, 0.4, NOW,
    )
    assert [c.strike for c in h.cells] == [105]


def test_la_intensidad_queda_entre_cero_y_uno_y_la_maxima_es_uno():
    rows = [row(100, "call", 9000, "2026-08-21"), row(115, "call", 10, "2026-08-21")]
    h = gex_heatmap(rows, 100, 0.4, NOW)
    assert all(0 <= c.intensity <= 1 for c in h.cells)
    assert max(c.intensity for c in h.cells) == 1


def test_recorta_al_numero_de_vencimientos_pedido():
    rows = [
        row(100, "call", 1000, f"2026-{m:02d}-18") for m in range(8, 13)
    ]
    h = gex_heatmap(rows, 100, 0.4, NOW, max_expirations=2)
    assert len(h.expirations) == 2


def test_la_gamma_real_del_tape_ancla_la_estimada():
    rows = [row(100, "call", 1000, "2026-08-21")]
    sin = gex_heatmap(rows, 100, 0.4, NOW)
    # Una gamma real muy superior a la teorica debe mover la celda hacia arriba.
    con = gex_heatmap(
        rows, 100, 0.4, NOW,
        trades=[HeatTrade(strike=100, expiration="2026-08-21", gamma=0.5, premium=1e6)],
    )
    assert con.total_net_gex > sin.total_net_gex


def test_expone_las_celdas_mas_calientes_de_cada_signo():
    rows = [row(100, "call", 9000, "2026-08-21"), row(95, "put", 9000, "2026-08-21")]
    h = gex_heatmap(rows, 100, 0.4, NOW)
    assert h.hottest_positive is not None
    assert h.hottest_negative is not None
    assert h.hottest_positive.net_gex > 0
    assert h.hottest_negative.net_gex < 0
