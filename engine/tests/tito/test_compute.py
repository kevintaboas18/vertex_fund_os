"""Port de `web/lib/compute.test.ts` (12 casos) + los que el original no cubre."""

from __future__ import annotations

import math

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

    def test_la_basura_no_numerica_da_NaN_como_en_JS(self):
        # `Number("abc")` es NaN y `NaN * 100 * 100` es NaN. Se porta tal cual:
        # `?? fallback` solo rellena el AUSENTE, no el ilegible.
        r = to_row({"open_interest": "abc", "details": {"strike_price": 100}})
        assert math.isnan(r.open_interest)
        assert math.isnan(r.notional_value)

    def test_el_precio_conserva_la_regla_estricta(self):
        # Aquí NO se coacciona: un precio raro cae al siguiente de la cascada.
        assert to_row({"last_trade": {"price": "1.25"}, "day": {"close": 2}}).price == 2


class TestNoInventarElMultiplicador:
    """`shares_per_contract` ILEGIBLE no es lo mismo que AUSENTE.

    Su `?? 100` solo rellena cuando el campo falta; un `"abc"` NO cae al 100.
    Ese es el detalle que motivó portar este módulo entero —dar por hecho 100
    acciones por contrato infla el nocional de los ajustados por el factor del
    ajuste— y es literal en su archivo: la aritmética de JS convierte el
    ilegible en `NaN`, no en 100.
    """

    @pytest.mark.parametrize("basura", ["abc", {}, "NaN", "inf"])
    def test_shares_ilegible_no_cae_al_100(self, basura):
        r = to_row({"open_interest": 100,
                    "details": {"strike_price": 9000, "shares_per_contract": basura}})
        assert math.isnan(r.notional_value), "se fabricó un multiplicador de la nada"
        assert r.notional_value != 100 * 100 * 9000

    @pytest.mark.parametrize("vacio", ["", "   ", []])
    def test_shares_en_blanco_es_CERO_en_JS(self, vacio):
        # `Number("")`, `Number("   ")` y `Number([])` son 0, no NaN. Otra vez:
        # tampoco son 100.
        r = to_row({"open_interest": 100,
                    "details": {"strike_price": 9000, "shares_per_contract": vacio}})
        assert r.notional_value == 0

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


class TestLasCincoQueSeQuedanComoEl:
    """Los cinco puntos donde `compute.ts` maneja mal una entrada malformada.

    Este módulo es **literal**: los cinco están portados tal cual, comprobados
    ejecutando su archivo en Node (`diff_compute.sh`, 604/604 sin una sola
    divergencia declarada). Los arreglos propuestos aguas arriba viven en
    `engine/scripts/upstream-tito-compute.patch`; lo que aquí se fija es que el
    port hace exactamente lo que hace el suyo, ni más ni menos.

    Lo que protege a Vertex de estos cinco no es una guarda dentro del módulo,
    es el borde: `_json_safe` en el endpoint (que es su `JSON.stringify`) y los
    filtros de `borde.py`. Los dos últimos tests de la clase lo comprueban.
    """

    def test_1_un_precio_infinito_se_devuelve_como_precio(self):
        # `Infinity > 0` es true, así que lo devuelve y el `day.close` bueno que
        # viene detrás no se llega a mirar. Suyo, portado.
        assert contract_price({"last_trade": {"price": float("inf")},
                               "day": {"close": 2}}) == (float("inf"), "last_trade")
        # El negativo sí cae al siguiente nivel: su cascada ya lo trataba.
        assert contract_price({"day": {"close": float("-inf"), "vwap": 1.5}}) \
            == (1.5, "day_vwap")

    @pytest.mark.parametrize("crudo,esperado", [
        ({"open_interest": -500, "details": {"strike_price": 100}}, -500 * 100 * 100),
        ({"open_interest": 500, "details": {"strike_price": -100}}, 500 * 100 * -100),
        ({"open_interest": 10,
          "details": {"strike_price": 100, "shares_per_contract": -100}},
         10 * -100 * 100),
    ])
    def test_2_un_negativo_produce_un_nocional_negativo(self, crudo, esperado):
        assert to_row(crudo).notional_value == esperado

    def test_2b_y_una_fila_corrupta_invierte_la_cadena_entera(self):
        """Lo que cuesta portarlo literal, medido: UNA fila de OI -900k invierte
        el nocional de la cadena y la declara ilíquida. Es su comportamiento.

        Se fija a propósito. Si algún día deja de pasar, o alguien metió una
        guarda que él no tiene, o su archivo cambió — las dos cosas hay que
        enterarse de ellas.
        """
        from wbj.tito.structure import structure_score

        def cadena(con_corrupta):
            filas = [to_row({"details": {"contract_type": ct, "strike_price": float(s),
                                         "expiration_date": "2026-09-18",
                                         "shares_per_contract": 100},
                             "day": {"volume": 400}, "open_interest": 9000})
                     for s in range(90, 115, 5) for ct in ("call", "put")]
            if con_corrupta:
                filas.append(to_row({"details": {"contract_type": "call",
                                                 "strike_price": 100.0,
                                                 "expiration_date": "2026-09-18",
                                                 "shares_per_contract": 100},
                                     "day": {"volume": 400},
                                     "open_interest": -900_000}))
            return filas

        sana, sucia = structure_score(cadena(False)), structure_score(cadena(True))
        assert sucia.notional["total"] < 0 < sana.notional["total"]
        assert sucia.notional["low_liquidity"]

    def test_2c_el_precio_negativo_sigue_con_su_propia_regla(self):
        # La cascada ya lo trataba: cae al siguiente nivel, no al fallback.
        assert contract_price({"last_trade": {"price": -3}, "day": {"vwap": 1.0}}) \
            == (1.0, "day_vwap")

    @pytest.mark.parametrize("oi,strike,shares", [
        (1e200, 1e200, 100), (1e308, 10, 100), (1e160, 1e160, 1e160),
    ])
    def test_3_el_producto_desbordado_llega_a_la_fila_como_infinito(self, oi, strike, shares):
        # `1e200 * 100 * 1e200` es `Infinity` con las dos entradas finitas.
        r = to_row({"open_interest": oi,
                    "details": {"strike_price": strike, "shares_per_contract": shares}})
        assert math.isinf(r.notional_value)

    def test_3b_un_open_premium_desbordado_tambien(self):
        r = to_row({"open_interest": 1e200, "last_trade": {"price": 1e200},
                    "details": {"strike_price": 1}})
        assert math.isinf(r.open_premium)

    def test_4_el_ticker_usa_la_semantica_de_Victor(self):
        # `?? ""`, no `or ""`: solo el ausente cae a vacío. `String(0)` es "0",
        # que es lo que produce la interpolación de su plantilla.
        assert to_row({"details": {"ticker": None}}).option_ticker == ""
        assert to_row({"details": {}}).option_ticker == ""
        assert to_row({"details": {"ticker": 0}}).option_ticker == "0"
        assert to_row({"details": {"ticker": "O:X"}}).option_ticker == "O:X"

    def test_5_el_vencimiento_entra_TAL_CUAL(self):
        # Sin recorte a 10 caracteres: es la clave con la que agrupan el
        # sub-agente 4 y el heatmap, y él la usa cruda.
        for crudo in ("2026-09-18", "2026-09-18T00:00:00", "2026-09-18T00:00:00Z"):
            assert to_row({"details": {"expiration_date": crudo}}).expiration == crudo

    def test_5b_dos_formatos_del_mismo_dia_NO_agrupan_juntos(self):
        # Su consecuencia directa, fijada: el mismo vencimiento escrito de dos
        # formas cuenta como dos. Ver upstream-tito-compute.patch.
        from wbj.tito.structure import structure_score

        filas = [to_row({"details": {"contract_type": "call", "strike_price": 100,
                                     "expiration_date": e},
                         "open_interest": 1000, "day": {"volume": 100}})
                 for e in ("2026-09-18", "2026-09-18T00:00:00")]
        assert len(structure_score(filas).expirations) == 2
        assert count_expirations(filas) == 2

    def test_la_fila_SI_puede_llevar_no_finitos(self):
        """El módulo no limpia nada: NaN e Infinity salen en la fila.

        Quien los convierte en `null` es `_json_safe` del endpoint, que es el
        port de su `JSON.stringify` — la misma y única línea de defensa que
        tiene él. Fijado desde el otro lado en
        `tests_vertex/test_tito_wiring.py`.
        """
        filas = [to_row({"open_interest": "abc", "details": {"strike_price": 100}}),
                 to_row({"open_interest": 1e200, "last_trade": {"price": 1e200},
                         "details": {"strike_price": 1e200}}),
                 to_row({"last_trade": {"price": float("inf")}, "open_interest": 100,
                         "details": {"strike_price": 50}})]
        assert math.isnan(filas[0].notional_value)
        assert math.isinf(filas[1].notional_value)
        assert math.isinf(filas[2].price)


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


class TestElTipoDeContratoDependeDelCase:
    """`t === "put"` exacto. Un `"PUT"` de la fuente se convierte en **call**.

    Es lo más caro de su archivo y lo que menos avisa: no lanza, miente. Y el
    `case` de un campo es justo lo que cambia en una migración de proveedor sin
    que nadie lo note. Portado literal; el arreglo propuesto está en
    `upstream-tito-compute.patch`.

    Lo que hace que esto NO llegue a Proyecciones es la fuente: Massive envía
    `contract_type` en minúsculas, y el día que deje de hacerlo lo dirá este
    test al romperse contra su archivo en `diff_compute.sh`.
    """

    def test_solo_el_put_en_minusculas_es_put(self):
        assert to_row({"details": {"contract_type": "put"}}).contract_type == "put"

    def test_cualquier_otro_case_se_vuelve_call(self):
        for t in ("PUT", "Put", " put "):
            assert to_row({"details": {"contract_type": t}}).contract_type == "call"

    def test_lo_que_no_es_put_es_call(self):
        for t in ("call", "CALL", "P", "", None, 5):
            assert to_row({"details": {"contract_type": t}}).contract_type == "call"

    def test_el_gex_cambia_de_signo_con_la_cadena_en_mayusculas(self):
        """Lo que cuesta, medido: -13.6M → +27.2M y el régimen se invierte."""
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
        assert bajo.total_net_gex < 0 < alto.total_net_gex
        assert alto.regime != bajo.regime


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
        # `float("NaN")` y `float("inf")` PARSEAN en Python, `Number()` no
        # siempre: `Number("inf")` es NaN y `Number("Infinity")` sí es infinito
        # (distingue el case). Lo que se fija es que ninguno LANCE — el port no
        # llama a `int()` en ningún sitio, igual que él.
        for v in ("NaN", "nan", "inf"):
            r = to_row({"open_interest": v, "details": {"strike_price": 100}})
            assert math.isnan(r.open_interest) and math.isnan(r.notional_value)
        for v in ("Infinity", "-Infinity"):
            r = to_row({"open_interest": v, "details": {"strike_price": 100}})
            assert math.isinf(r.open_interest) and math.isinf(r.notional_value)
        r = to_row({"open_interest": 10, "details": {"strike_price": float("nan")}})
        assert math.isnan(r.strike) and math.isnan(r.notional_value)

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


class TestNumberDeJSNoEsFloatDePython:
    """Los cuatro sitios donde `float()` y `Number()` dejan de coincidir.

    Salieron del diferencial de PRIMITIVAS (`engine/scripts/diff_primitivas.sh`),
    que ejecuta `Number()` de V8 sobre un corpus de casos límite. El diferencial
    a nivel de fila no los alcanza: su generador nunca produce un `"1_000"`.
    """

    def test_el_separador_de_millar_de_Python_no_es_un_numero_en_JS(self):
        # `float("1_000")` es 1000 y `Number("1_000")` es NaN. Sin el filtro, un
        # texto que para él es basura entraba como número — y encima grande.
        from wbj.tito.compute import _js_number
        import math

        for v in ("1_0", "1_000", "12_3.4", "1_0.5"):
            assert math.isnan(_js_number(v)), v
        r = to_row({"open_interest": "1_000", "details": {"strike_price": 100}})
        assert math.isnan(r.open_interest)   # NaN, como él: NO 1000

    def test_las_bases_que_JS_entiende_y_float_no(self):
        from wbj.tito.compute import _js_number
        import math

        assert _js_number("0x1A") == 26
        assert _js_number("0X1a") == 26
        assert _js_number("0o17") == 15
        assert _js_number("0b101") == 5
        assert math.isnan(_js_number("0xZZ"))

    def test_Infinity_distingue_el_case_y_float_no(self):
        # `float()` acepta "inf", "infinity" y "NAN" en cualquier capitalización;
        # `Number()` solo el "Infinity" exacto.
        from wbj.tito.compute import _js_number
        import math

        assert _js_number("Infinity") == float("inf")
        assert _js_number("-Infinity") == float("-inf")
        for v in ("infinity", "INFINITY", "inf", "-inf", "nan", "NaN", "NAN"):
            assert math.isnan(_js_number(v)), v

    def test_lo_que_si_coincide(self):
        from wbj.tito.compute import _js_number

        assert _js_number("  500  ") == 500      # Number() recorta
        assert _js_number("") == 0
        assert _js_number("1e3") == 1000
        assert _js_number([7]) == 7              # Number([7]) === 7
        assert _js_number(True) == 1
