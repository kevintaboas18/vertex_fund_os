"""Port de `web/lib/compute.test.ts` (12 casos) + los que el original no cubre."""

from __future__ import annotations

import pytest

from wbj.tito.compute import (
    contract_price,
    count_expirations,
    notional_value,
    open_premium,
    sort_by_open_interest_desc,
    to_row,
)
from wbj.tito.structure import ChainRow


class TestContractPrice:
    def test_prefiere_last_trade(self):
        raw = {"last_trade": {"price": 1.25}, "day": {"close": 1.1, "vwap": 1.05}}
        assert contract_price(raw) == (1.25, "last_trade")

    def test_cae_a_day_close_cuando_no_hay_last_trade(self):
        assert contract_price({"day": {"close": 1.1, "vwap": 1.05}}) == (1.1, "day_close")

    def test_cae_a_day_vwap_cuando_no_hay_last_trade_ni_close(self):
        assert contract_price({"day": {"vwap": 1.05}}) == (1.05, "day_vwap")

    def test_devuelve_none_cuando_no_hay_ningun_precio(self):
        assert contract_price({}) == (None, "none")

    def test_ignora_precios_no_positivos(self):
        raw = {"last_trade": {"price": 0}, "day": {"close": 2}}
        assert contract_price(raw) == (2, "day_close")

    def test_un_precio_en_texto_no_cuenta(self):
        # `typeof x === "number"` del original: "1.25" no es un número.
        assert contract_price({"last_trade": {"price": "1.25"}, "day": {"close": 2}}) \
            == (2, "day_close")

    def test_un_precio_negativo_tampoco(self):
        assert contract_price({"last_trade": {"price": -3}, "day": {"vwap": 1.0}}) \
            == (1.0, "day_vwap")


class TestOpenPremium:
    def test_oi_por_precio(self):
        assert open_premium(60, 1.25) == 75

    def test_none_si_no_hay_precio(self):
        assert open_premium(60, None) is None

    def test_none_y_cero_no_son_lo_mismo(self):
        # "no se pudo calcular" vs "vale cero".
        assert open_premium(0, 1.25) == 0
        assert open_premium(60, None) is None


class TestNotionalValue:
    def test_oi_por_100_por_strike(self):
        assert notional_value(60, 205) == 60 * 100 * 205

    def test_respeta_shares_per_contract_distinto_de_100(self):
        assert notional_value(10, 50, 10) == 10 * 10 * 50


class TestToRow:
    def test_mapea_un_contrato_crudo_de_massive(self):
        raw = {
            "details": {
                "contract_type": "call",
                "expiration_date": "2026-07-22",
                "strike_price": 205,
                "shares_per_contract": 100,
                "ticker": "O:AAPL260722C00205000",
            },
            "day": {"volume": 81, "close": 119.28},
            "last_trade": {"price": 119.28},
            "open_interest": 60,
        }
        row = to_row(raw)
        assert row.contract_type == "call"
        assert row.strike == 205
        assert row.open_interest == 60
        assert row.volume == 81
        assert row.price == 119.28
        assert row.price_source == "last_trade"
        assert row.open_premium == pytest.approx(60 * 119.28)
        assert row.notional_value == 60 * 100 * 205
        assert row.option_ticker == "O:AAPL260722C00205000"

    def test_contract_type_ausente_es_call_y_los_campos_faltantes_cero(self):
        row = to_row({})
        assert row.contract_type == "call"
        assert row.open_interest == 0
        assert row.notional_value == 0
        assert row.open_premium is None

    def test_un_contrato_ajustado_no_se_valora_como_uno_normal(self):
        # El bug que destapó este port: dar por hecho 100 acciones por contrato
        # inflaba el nocional de los ajustados por el factor del ajuste (10× en
        # este caso), y el nocional es la entrada principal del sub-agente 4.
        raw = {"details": {"strike_price": 250, "shares_per_contract": 10,
                           "expiration_date": "2026-09-18", "contract_type": "call"},
               "open_interest": 30_000}
        assert to_row(raw).notional_value == 30_000 * 10 * 250
        assert to_row(raw).notional_value != 30_000 * 100 * 250


class TestLasDosReglasDeVictor:
    """`compute.ts` usa DOS reglas de tipo, no una.

    `typeof x === "number"` (estricta) solo para el precio; `?? fallback`
    (laxa, con la aritmética de JS detrás) para OI, strike, volumen y acciones
    por contrato. Aplicar la estricta a los cuatro se llenaba de ceros en
    silencio si la fuente cambiaba a números en texto.
    """

    def test_un_oi_en_texto_sigue_contando(self):
        # En JS: "500" * 100 * 100 = 5_000_000. Antes daba 0.
        r = to_row({"open_interest": "500", "details": {"strike_price": 100}})
        assert r.open_interest == 500
        assert r.notional_value == 500 * 100 * 100

    def test_un_strike_en_texto_sigue_contando(self):
        r = to_row({"open_interest": 10, "details": {"strike_price": "205"}})
        assert r.strike == 205
        assert r.notional_value == 10 * 100 * 205

    def test_el_volumen_en_texto_tambien(self):
        assert to_row({"day": {"volume": "81"}}).volume == 81

    def test_shares_cero_no_cae_al_default(self):
        # `?? 100` solo cambia null/undefined: un 0 explícito se respeta.
        r = to_row({"open_interest": 10,
                    "details": {"strike_price": 100, "shares_per_contract": 0}})
        assert r.notional_value == 0

    def test_shares_null_si_cae_al_default(self):
        r = to_row({"open_interest": 10,
                    "details": {"strike_price": 100, "shares_per_contract": None}})
        assert r.notional_value == 10 * 100 * 100

    def test_la_basura_no_numerica_cae_al_fallback(self):
        # En JS daría NaN y envenenaría el nocional en silencio; aquí cae a 0,
        # que además enciende la salvaguarda de baja liquidez del sub-agente 4.
        r = to_row({"open_interest": "abc", "details": {"strike_price": 100}})
        assert r.open_interest == 0
        assert r.notional_value == 0

    def test_el_precio_conserva_la_regla_estricta(self):
        # Aquí NO se coacciona: un precio raro cae al siguiente de la cascada.
        assert to_row({"last_trade": {"price": "1.25"}, "day": {"close": 2}}).price == 2


class TestCoherenciaInterna:
    def test_la_fila_y_sus_formulas_usan_el_mismo_oi(self):
        # Redondear para el campo y calcular con el crudo dejaba filas que se
        # contradicen: OI 60 con el nocional de 60.5.
        r = to_row({"open_interest": 60.5, "details": {"strike_price": 100}})
        assert r.notional_value == r.open_interest * 100 * 100
        assert r.open_premium is None or r.open_premium == r.open_interest * (r.price or 0)

    def test_el_vencimiento_queda_canonico(self):
        # Es la CLAVE con la que agrupan el sub-agente 4 y el heatmap: sin
        # canonizar, "2026-09-18" y "2026-09-18T00:00:00" serían dos distintos.
        for crudo in ("2026-09-18", "2026-09-18T00:00:00", "2026-09-18T00:00:00Z"):
            assert to_row({"details": {"expiration_date": crudo}}).expiration == "2026-09-18"

    def test_dos_formatos_del_mismo_dia_agrupan_juntos(self):
        from wbj.tito.structure import structure_score
        filas = [to_row({"details": {"contract_type": "call", "strike_price": 100,
                                     "expiration_date": e},
                         "open_interest": 1000, "day": {"volume": 100}})
                 for e in ("2026-09-18", "2026-09-18T00:00:00")]
        exps = structure_score(filas).expirations
        assert len(exps) == 1 and exps[0].expiration == "2026-09-18"
        assert exps[0].contracts == 2          # los dos cayeron en el mismo grupo
        assert count_expirations(filas) == 1


class TestSortByOpenInterestDesc:
    def test_ordena_de_mayor_a_menor_sin_mutar_el_original(self):
        def fila(oi):
            return ChainRow("call", "2026-09-18", 100.0, oi, 0, 0.0)

        rows = [fila(5), fila(100), fila(27)]
        ordenadas = sort_by_open_interest_desc(rows)
        assert [r.open_interest for r in ordenadas] == [100, 27, 5]
        assert rows[0].open_interest == 5   # original intacto


class TestCountExpirations:
    def test_cuenta_vencimientos_distintos(self):
        def fila(exp):
            return ChainRow("call", exp, 100.0, 0, 0, 0.0)

        rows = [fila("2026-07-22"), fila("2026-07-22"), fila("2026-08-21"), fila("")]
        assert count_expirations(rows) == 2

    def test_sin_filas_es_cero(self):
        assert count_expirations([]) == 0
