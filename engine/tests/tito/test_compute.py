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

    def test_la_basura_no_numerica_da_NaN(self):
        # `"abc" * 100 * 100` es NaN en JS y aquí también. Llegué a caer al
        # fallback (0) y está quitado: no es lo que hace su archivo.
        import math

        r = to_row({"open_interest": "abc", "details": {"strike_price": 100}})
        assert math.isnan(r.open_interest)
        assert math.isnan(r.notional_value)

    def test_el_precio_conserva_la_regla_estricta(self):
        # Aquí NO se coacciona: un precio raro cae al siguiente de la cascada.
        assert to_row({"last_trade": {"price": "1.25"}, "day": {"close": 2}}).price == 2


class TestNoInventarElMultiplicador:
    """`shares_per_contract`: solo el AUSENTE cae al 100 (`?? 100`).

    Llegué a distinguir además el ILEGIBLE —`"abc"`, `[]`, `""`— para que no
    fabricara un multiplicador donde no hay evidencia de cuál es. Está quitado:
    su `?? 100` no lo distingue y el nocional le sale `NaN`, que al serializar
    es `null`. Un `""` o un `[]` sí caen a 0 porque `Number("") === 0`.
    """

    @pytest.mark.parametrize("basura", ["abc", {}, "NaN", "inf"])
    def test_shares_ilegible_da_NaN_como_el_suyo(self, basura):
        import math

        r = to_row({"open_interest": 100,
                    "details": {"strike_price": 9000, "shares_per_contract": basura}})
        assert math.isnan(r.notional_value)

    @pytest.mark.parametrize("cero", ["", "   ", []])
    def test_lo_que_Number_convierte_en_cero_da_cero(self, cero):
        r = to_row({"open_interest": 100,
                    "details": {"strike_price": 9000, "shares_per_contract": cero}})
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


class TestComportamientoLiteralDeVictor:
    """Cinco guardas que llegué a añadir aquí, y que están QUITADAS.

    Cada una parecía —y sigue pareciendo— una mejora, pero ninguna está en su
    `compute.ts`, y la instrucción es que el motor sea exactamente el suyo.
    Auditado antes de quitarlas: en su repo, `massive.ts` mete `json.results`
    sin validar y `app/api/chain/route.ts` pasa las filas directo a
    `structureScore`, así que este archivo era el único sitio donde podían
    estar. El arreglo propuesto para el upstream está en
    `engine/scripts/upstream-tito-compute.patch`.

    Estos tests fijan SU comportamiento exacto, medido ejecutando su
    `compute.ts` en Node (`engine/scripts/diff_compute.sh`:
    3004/3004 filas con el mismo valor en los 10 campos).
    """

    def test_1_un_precio_infinito_SI_se_acepta(self):
        # `typeof Infinity === "number" && Infinity > 0` → lo devuelve como
        # precio. Llegué a rechazarlo para que el Open Premium no saliera `inf`.
        assert contract_price({"last_trade": {"price": float("inf")},
                               "day": {"close": 2}}) == (float("inf"), "last_trade")
        r = to_row({"last_trade": {"price": float("inf")}, "open_interest": 100,
                    "details": {"strike_price": 50}})
        assert r.open_premium == float("inf")

    @pytest.mark.parametrize("crudo,campo,esperado", [
        ({"open_interest": -500, "details": {"strike_price": 100}}, "open_interest", -500),
        ({"open_interest": 500, "details": {"strike_price": -100}}, "strike", -100),
        ({"day": {"volume": -9}}, "volume", -9),
    ])
    def test_2_los_negativos_pasan_tal_cual(self, crudo, campo, esperado):
        assert getattr(to_row(crudo), campo) == esperado

    def test_2b_un_OI_negativo_RESTA_del_nocional_de_la_cadena(self):
        """Lo que cuesta la guarda 2, medido: una fila corrupta y la cadena
        entera cambia de signo y se declara ilíquida."""
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
        assert sucia.notional["low_liquidity"]      # sobre una cadena líquida

    @pytest.mark.parametrize("oi,strike,shares", [
        (1e200, 1e200, 100), (1e308, 10, 100), (1e160, 1e160, 1e160),
    ])
    def test_3_el_producto_desbordado_SI_llega_a_la_fila(self, oi, strike, shares):
        # `1e200 * 100 * 1e200` es `Infinity` en los dos lenguajes; él lo deja.
        import math

        r = to_row({"open_interest": oi,
                    "details": {"strike_price": strike, "shares_per_contract": shares}})
        assert math.isinf(r.notional_value)

    def test_4_el_ticker_crudo_pasa_sin_convertir(self):
        # `?? ""`: solo el ausente cae a vacío, el resto pasa TAL CUAL — sin
        # `str()`, así que un ticker `0` sigue siendo el número 0.
        assert to_row({"details": {"ticker": None}}).option_ticker == ""
        assert to_row({"details": {}}).option_ticker == ""
        assert to_row({"details": {"ticker": 0}}).option_ticker == 0
        assert to_row({"details": {"ticker": False}}).option_ticker is False
        assert to_row({"details": {"ticker": "O:X"}}).option_ticker == "O:X"

    def test_5_el_vencimiento_NO_se_recorta(self):
        # Llegué a recortarlo a YYYY-MM-DD porque es la clave de agrupación del
        # sub-agente 4 y del heatmap. `?? ""` y nada más.
        for crudo in ("2026-09-18T00:00:00Z", "2026-09-18T00:00:00"):
            assert to_row({"details": {"expiration_date": crudo}}).expiration == crudo
        assert to_row({"details": {"expiration_date": 20260918}}).expiration == 20260918
        assert to_row({"details": {}}).expiration == ""

    def test_5b_dos_formatos_del_mismo_dia_cuentan_como_dos_vencimientos(self):
        """Lo que cuesta la guarda 5. Hoy no se dispara: Massive manda la fecha
        sola. Si algún día manda la hora, el heatmap parte el vencimiento."""
        from wbj.tito.structure import structure_score

        filas = [to_row({"details": {"contract_type": "call", "strike_price": 100,
                                     "expiration_date": e},
                         "open_interest": 1000, "day": {"volume": 100}})
                 for e in ("2026-09-18", "2026-09-18T00:00:00")]
        assert count_expirations(filas) == 2
        assert len(structure_score(filas).expirations) == 2

    def test_la_respuesta_del_endpoint_sigue_siendo_JSON_valido(self):
        """`NaN`/`Infinity` en la fila son de él; lo que NO puede pasar es que
        `json.dumps` los escriba a pelo, porque eso no es JSON y el navegador lo
        rechaza. Su `JSON.stringify` los convierte en `null`, y `_json_safe` de
        `vertex_api` hace lo mismo antes de devolver."""
        import json
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from vertex_api import _json_safe

        filas = [to_row({"open_interest": "abc", "details": {"strike_price": 100}}),
                 to_row({"open_interest": 1e200, "last_trade": {"price": 1e200},
                         "details": {"strike_price": 1e200}})]
        crudo = json.dumps(_json_safe([
            {"oi": f.open_interest, "nv": f.notional_value,
             "op": f.open_premium, "p": f.price} for f in filas]))

        def estricto(c):
            raise ValueError(c)

        json.loads(crudo, parse_constant=estricto)   # no debe lanzar
        assert "NaN" not in crudo and "Infinity" not in crudo


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
    """La guarda más cara de las cinco quitadas, y la que más vigilancia pide.

    `normalizeType` compara `t === "put"` exacto, así que un `"PUT"` de la
    fuente se convierte en **call**. Llegué a comparar en minúsculas; está
    quitado porque no es lo que hace su archivo.

    Massive manda `"call"`/`"put"` en minúsculas, así que hoy no se dispara. El
    día que cambie el `case`, esto es lo que pasa — y no avisa, miente.
    """

    def test_solo_el_put_exacto_en_minusculas_es_put(self):
        assert to_row({"details": {"contract_type": "put"}}).contract_type == "put"
        for t in ("PUT", "Put", " put ", "call", "CALL", "P", "", None, 5):
            assert to_row({"details": {"contract_type": t}}).contract_type == "call"

    def test_el_GEX_CAMBIA_DE_SIGNO_con_la_cadena_en_mayusculas(self):
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
        # Todos los puts pasan a contarse como calls: el GEX neto se invierte y
        # con él el régimen. Es la señal central del motor decidida por el case
        # de un string de la fuente.
        assert bajo.total_net_gex < 0 < alto.total_net_gex
        assert bajo.regime != alto.regime


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
        # `Number("NaN")`, `Number("nan")` y `Number("inf")` son NaN en JS —
        # `float()` de Python parsearía los tres, así que `_js_number` lo cierra.
        # `Number("-Infinity")` sí es -inf, y también en él.
        import math

        for v in ("NaN", "nan", "inf"):
            r = to_row({"open_interest": v, "details": {"strike_price": 100}})
            assert math.isnan(r.open_interest) and math.isnan(r.notional_value)
        r = to_row({"open_interest": "-Infinity", "details": {"strike_price": 100}})
        assert r.open_interest == float("-inf")
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
