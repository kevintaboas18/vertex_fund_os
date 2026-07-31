"""Port de `web/lib/compute.test.ts` (12 casos) + los que el original no cubre."""

from __future__ import annotations

import pytest

from wbj.tito.compute import (
    contract_price,
    count_expirations,
    sort_by_open_interest_desc,
    notional_value,
    open_premium,
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


class TestNoInventarElMultiplicador:
    """`shares_per_contract` ILEGIBLE no es lo mismo que AUSENTE.

    Caer al 100 cuando el campo viene y no se entiende es inventar el
    multiplicador estándar justo donde no hay evidencia de cuál es — el mismo
    bug que motivó portar este módulo, por la puerta de atrás.
    """

    @pytest.mark.parametrize("basura", ["abc", "", "   ", [], {}, "NaN"])
    def test_shares_ilegible_no_cae_al_100(self, basura):
        r = to_row({"open_interest": 100,
                    "details": {"strike_price": 9000, "shares_per_contract": basura}})
        assert r.notional_value == 0, "se fabricó un multiplicador de la nada"

    def test_shares_ausente_si_cae_al_100(self):
        r = to_row({"open_interest": 100, "details": {"strike_price": 9000}})
        assert r.notional_value == 100 * 100 * 9000

    def test_shares_null_tambien_cae_al_100(self):
        r = to_row({"open_interest": 100,
                    "details": {"strike_price": 9000, "shares_per_contract": None}})
        assert r.notional_value == 100 * 100 * 9000

    def test_shares_cero_explicito_sigue_siendo_cero(self):
        r = to_row({"open_interest": 100,
                    "details": {"strike_price": 9000, "shares_per_contract": 0}})
        assert r.notional_value == 0


class TestBooleanos:
    """Las dos reglas de Víctor tratan un booleano al revés, a propósito.

    `typeof true === "boolean"` lo rechaza en el precio; `true * 5 === 5` lo
    convierte en la aritmética. No es un descuido suyo: son dos reglas.
    """

    def test_la_regla_laxa_los_convierte(self):
        assert to_row({"open_interest": True, "details": {"strike_price": 100}}) \
            .notional_value == 1 * 100 * 100
        assert to_row({"open_interest": False, "details": {"strike_price": 100}}) \
            .notional_value == 0

    def test_la_regla_estricta_los_rechaza(self):
        assert contract_price({"last_trade": {"price": True}, "day": {"close": 2}}) \
            == (2, "day_close")


class TestUnPrecioInfinitoNoContaminaElJSON:
    """`Infinity > 0` es `true` en JS, así que Víctor lo acepta como precio y el
    Open Premium sale `Infinity` — que su `JSON.stringify` convierte a `null`.
    `json.dumps` escribiría `Infinity`, que no es JSON, y estos dos campos
    existen precisamente para servir la tabla de la cadena."""

    def test_un_precio_infinito_cae_al_siguiente_de_la_cascada(self):
        assert contract_price({"last_trade": {"price": float("inf")},
                               "day": {"close": 2}}) == (2, "day_close")
        assert contract_price({"day": {"close": float("-inf"), "vwap": 1.5}}) \
            == (1.5, "day_vwap")
        assert contract_price({"last_trade": {"price": float("inf")}}) == (None, "none")

    def test_la_fila_siempre_serializa_a_json_estricto(self):
        import json

        r = to_row({"last_trade": {"price": float("inf")}, "open_interest": 100,
                    "details": {"strike_price": 50}})
        crudo = json.dumps({"price": r.price, "open_premium": r.open_premium,
                            "notional_value": r.notional_value})

        def estricto(c):
            raise ValueError(c)

        json.loads(crudo, parse_constant=estricto)   # no debe lanzar


class TestElProductoTambienPuedeDesbordar:
    """Filtrar solo lo que ENTRA dejaba el agujero abierto por el otro lado:
    `1e200 * 100 * 1e200` es `inf` con las dos entradas finitas."""

    @pytest.mark.parametrize("oi,strike,shares", [
        (1e200, 1e200, 100), (1e308, 10, 100), (1e160, 1e160, 1e160),
    ])
    def test_un_nocional_desbordado_no_llega_a_la_fila(self, oi, strike, shares):
        r = to_row({"open_interest": oi,
                    "details": {"strike_price": strike, "shares_per_contract": shares}})
        assert r.notional_value == 0

    def test_un_open_premium_desbordado_tampoco(self):
        r = to_row({"open_interest": 1e200, "last_trade": {"price": 1e200},
                    "details": {"strike_price": 1}})
        assert r.open_premium is None

    def test_la_fila_desbordada_serializa_a_json_estricto(self):
        import json

        r = to_row({"open_interest": 1e200, "last_trade": {"price": 1e200},
                    "details": {"strike_price": 1e200}})
        crudo = json.dumps({"op": r.open_premium, "nv": r.notional_value,
                            "p": r.price})

        def estricto(c):
            raise ValueError(c)

        json.loads(crudo, parse_constant=estricto)


class TestElTickerUsaLaSemanticaDeVictor:
    def test_solo_el_ausente_cae_a_vacio(self):
        # `?? ""`, no `or ""`: con `or`, un ticker 0 o False se borraba y
        # quedaba indistinguible de "no vino".
        assert to_row({"details": {"ticker": None}}).option_ticker == ""
        assert to_row({"details": {}}).option_ticker == ""
        assert to_row({"details": {"ticker": 0}}).option_ticker == "0"
        assert to_row({"details": {"ticker": False}}).option_ticker == "False"
        assert to_row({"details": {"ticker": "O:X"}}).option_ticker == "O:X"


class TestLasDosFuncionesQueFaltabaCablear:
    """`sortByOpenInterestDesc` y `countExpirations` estaban portadas pero sin
    llamar desde ningún sitio. Su `/api/chain` las usa antes de puntuar."""

    def test_la_cadena_sale_ordenada_por_open_interest(self):
        import wbj.tito.massive as MASS

        filas = [to_row({"details": {"contract_type": "call", "strike_price": float(s),
                                     "expiration_date": "2026-09-18"},
                         "open_interest": oi})
                 for s, oi in ((100, 5), (105, 900), (110, 27))]
        res = MASS.ChainResult(rows=sort_by_open_interest_desc(filas),
                               expiration_count=count_expirations(filas))
        assert [r.open_interest for r in res.rows] == [900, 27, 5]
        assert res.expiration_count == 1

    def test_ordenar_no_cambia_ningun_score(self):
        import random
        from datetime import datetime, timezone

        from wbj.tito.gex import gex_analysis
        from wbj.tito.structure import structure_score

        random.seed(7)
        now = datetime(2026, 7, 31, tzinfo=timezone.utc)
        filas = [to_row({"details": {"contract_type": ct, "expiration_date": e,
                                     "strike_price": float(s), "shares_per_contract": 100},
                         "day": {"volume": random.randint(10, 900)},
                         "open_interest": random.randint(50, 9000)})
                 for s in range(80, 125, 5) for ct in ("call", "put")
                 for e in ("2026-09-18", "2026-12-18")]
        ordenadas = sort_by_open_interest_desc(filas)
        assert structure_score(filas).score == structure_score(ordenadas).score
        a, b = (gex_analysis(f, [100.0] * 60, 100.0, now) for f in (filas, ordenadas))
        assert (a.regime, a.king_strike, a.flip_strike) == (b.regime, b.king_strike, b.flip_strike)


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


class TestElTipoDeContratoNoPuedeDependerDelCase:
    """El fallo más caro de los encontrados: no avisa, miente.

    Víctor compara `t === "put"` exacto, así que un `"PUT"` de la fuente se
    convierte en **call**. Con la cadena entera en mayúsculas el GEX neto
    cambia de signo (-13.6M → +27.2M) y el régimen se invierte.
    """

    def test_put_en_cualquier_case_sigue_siendo_put(self):
        for t in ("put", "PUT", "Put", " put "):
            assert to_row({"details": {"contract_type": t}}).contract_type == "put"

    def test_lo_que_no_es_put_es_call(self):
        for t in ("call", "CALL", "P", "", None, 5):
            assert to_row({"details": {"contract_type": t}}).contract_type == "call"

    def test_el_gex_no_cambia_de_signo_con_la_cadena_en_mayusculas(self):
        from datetime import datetime, timezone

        from wbj.tito.gex import gex_analysis

        now = datetime(2026, 7, 31, tzinfo=timezone.utc)

        def cadena(mayus):
            return [to_row({"details": {
                        "contract_type": ct.upper() if mayus else ct,
                        "expiration_date": "2026-09-18", "strike_price": float(s),
                        "shares_per_contract": 100},
                    "day": {"volume": 400},
                    "open_interest": 9000 if ct == "put" else 3000})
                    for s in range(90, 115, 5) for ct in ("call", "put")]

        bajo = gex_analysis(cadena(False), [100.0] * 60, 100.0, now)
        alto = gex_analysis(cadena(True), [100.0] * 60, 100.0, now)
        assert alto.total_net_gex == bajo.total_net_gex
        assert alto.regime == bajo.regime


class TestUnaFilaMalaNoTumbaLaPagina:
    """`to_row` corre dentro del bucle de `fetch_option_chain`: si lanza, se
    pierde la cadena entera, no una fila."""

    def test_details_con_tipo_raro_no_lanza(self):
        # El `?.` de Víctor devuelve undefined; `or {}` no cubre un string.
        for d in ([], "texto", 5, None, True):
            r = to_row({"details": d, "open_interest": 10})
            assert r.strike == 0 and r.contract_type == "call"

    def test_day_y_last_trade_con_tipo_raro_tampoco(self):
        assert contract_price({"last_trade": "x", "day": {"close": 2}}) == (2, "day_close")
        assert contract_price({"day": "x"}) == (None, "none")

    def test_un_contrato_que_no_es_dict_tampoco(self):
        for raw in (None, [], "x", 5):
            r = to_row(raw)
            assert r.strike == 0 and r.open_interest == 0

    def test_un_string_no_finito_no_revienta_la_conversion(self):
        # float("NaN") PARSEA: sin filtro llegaba a int(oi) y lanzaba ValueError
        # dentro del cliente HTTP, tumbando la cadena completa.
        for v in ("NaN", "nan", "inf", "-Infinity"):
            r = to_row({"open_interest": v, "details": {"strike_price": 100}})
            assert r.open_interest == 0 and r.notional_value == 0
        r = to_row({"open_interest": 10, "details": {"strike_price": float("nan")}})
        assert r.strike == 0 and r.notional_value == 0

    def test_una_fila_mala_en_medio_de_la_pagina_no_se_lleva_el_resto(self):
        crudos = [
            {"details": {"contract_type": "call", "strike_price": 100.0,
                         "expiration_date": "2026-09-18"}, "open_interest": 500},
            {"details": "esquema cambiado"},          # antes: AttributeError
            {"open_interest": "NaN"},                  # antes: ValueError
            {"details": {"contract_type": "put", "strike_price": 95.0,
                         "expiration_date": "2026-09-18"}, "open_interest": 700},
        ]
        filas = [to_row(c) for c in crudos]
        buenas = [f for f in filas if f.strike > 0 and f.expiration]
        assert len(buenas) == 2
        assert {f.contract_type for f in buenas} == {"call", "put"}


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
