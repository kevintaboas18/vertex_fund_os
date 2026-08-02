"""Las primitivas de JavaScript, y los 6 sitios del motor que las necesitaban.

Estos casos salieron de dos diferenciales —`diff_frescura.sh` y `diff_reloj.sh`—
que ejecutan sus archivos en Node. Se fijan también aquí porque los diferenciales
necesitan node y red, y estas reglas tienen que sostenerse igual sin ninguna de
las dos cosas.

El patrón de todos: Python tiene una función que hace *casi* lo mismo que la de
JS, y el port usaba la de Python. `round` vs `Math.round`, `fromisoformat` vs
`Date.parse`, `f"{x:,}"` vs `toLocaleString`, días de calendario vs milisegundos.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from wbj.tito.jsmath import (
    MS_POR_DIA,
    js_date_parse,
    js_days_since,
    js_locale_string,
    js_round,
    js_time,
)


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestJsRound:
    def test_la_mitad_va_hacia_arriba_no_al_par(self):
        # `round()` de Python es bancario: round(10.5) = 10, round(2.5) = 2.
        assert js_round(10.5) == 11
        assert js_round(2.5) == 3
        assert js_round(11.5) == 12          # aquí las dos coinciden

    def test_los_negativos_siguen_floor_de_la_especificacion(self):
        # ECMA-262 lo define como `floor(x + 0.5)`, así que -10.5 → -10.
        assert js_round(-10.5) == -10
        assert js_round(-2.5) == -2
        assert js_round(-2.6) == -3

    def test_el_NaN_pasa_TAL_CUAL(self):
        """Devolver 0 convertía un dato ilegible en un número creíble.

        Es el caso caro: `_dte_of` daba 0 —"vence hoy", la banda más caliente del
        heatmap— donde `Math.round(NaN)` es `NaN` y no entra en ninguna banda.
        """
        assert math.isnan(js_round(math.nan))

    def test_los_infinitos_tambien(self):
        assert js_round(math.inf) == math.inf
        assert js_round(-math.inf) == -math.inf


class TestJsLocaleString:
    def test_un_entero_no_lleva_decimales(self):
        # `f"{73886.0:,}"` de Python da "73,886.0"; su `toLocaleString` da
        # "73,886". Ese texto va al campo `why` de cada nivel.
        assert js_locale_string(73886.0) == "73,886"
        assert js_locale_string(0.0) == "0"
        assert js_locale_string(-4563.0) == "-4,563"

    def test_hasta_3_decimales_sin_rellenar(self):
        assert js_locale_string(1234.5) == "1,234.5"
        assert js_locale_string(1234.25) == "1,234.25"
        assert js_locale_string(1234.5678) == "1,234.568"

    def test_los_no_finitos_usan_SU_formato(self):
        assert js_locale_string(math.nan) == "NaN"
        assert js_locale_string(math.inf) == "∞"
        assert js_locale_string(-math.inf) == "-∞"


class TestJsDaysSince:
    """La cuenta de días de su `recencyFactor`: fraccionaria y anclada a 21:00Z."""

    def test_son_dias_FRACCIONARIOS_no_de_calendario(self):
        # 2026-07-01T21:00Z → 2026-07-31T22:00Z son 30 días y 1 hora.
        d = js_days_since("2026-07-01", _utc("2026-07-31T22:00:00Z"))
        assert 30 < d < 30.1
        # El calendario diría 30 justos, y 30 <= 30 cae en la banda de arriba.
        assert (_utc("2026-07-31T22:00:00Z").date()
                - datetime(2026, 7, 1).date()).days == 30

    def test_el_ancla_es_el_cierre_de_la_sesion(self):
        # A las 21:00Z exactas la cuenta es entera: es la hora del ancla.
        assert js_days_since("2026-07-01", _utc("2026-07-31T21:00:00Z")) == 30.0

    def test_una_fecha_ilegible_da_NaN_y_decide_el_llamador(self):
        assert math.isnan(js_days_since("ayer", _utc("2026-07-31T21:00:00Z")))
        # Concatenar la hora a algo que YA la trae también es inválido.
        assert math.isnan(
            js_days_since("2026-07-01T00:00:00Z", _utc("2026-07-31T21:00:00Z")))


class TestJsTime:
    def test_un_naive_se_lee_como_UTC(self):
        # Mismo criterio que `occ.market_date`. Si no, el corte —y con él la
        # frescura de los niveles— dependería de la TZ del servidor.
        assert js_time(datetime(2026, 7, 31)) == js_time(
            datetime(2026, 7, 31, tzinfo=timezone.utc))

    def test_son_milisegundos(self):
        assert js_time(_utc("1970-01-02T00:00:00Z")) == MS_POR_DIA


class TestDateParseEsLaDeJS:
    """Lo que `Date.parse` acepta y `fromisoformat` no, y al revés."""

    def test_los_formatos_cortos_del_estandar_son_validos(self):
        assert js_date_parse("2026") == js_date_parse("2026-01-01T00:00:00Z")
        assert js_date_parse("2026-07") == js_date_parse("2026-07-01T00:00:00Z")

    def test_una_fecha_imposible_es_NaN_no_una_excepcion(self):
        for malo in ("2026-13-45", "2026-02-30", "ayer", "", "no soy fecha"):
            assert math.isnan(js_date_parse(malo)), malo

    def test_lo_que_no_es_texto_tambien(self):
        for malo in (None, 12345, [], {}):
            assert math.isnan(js_date_parse(malo))


class TestLosSeisSitiosDelMotor:
    """Cada hallazgo del barrido del reloj, fijado en su función."""

    AHORA = _utc("2026-07-31T18:30:00Z")

    def test_1_recency_factor_cuenta_como_el(self):
        from wbj.tito.levels import recency_factor

        # Justo en el umbral de 30 días, a partir de las 22:00Z el factor baja.
        assert recency_factor("2026-07-01", _utc("2026-07-31T20:00:00Z")) == 1.0
        assert recency_factor("2026-07-01", _utc("2026-07-31T22:00:00Z")) == 0.75

    def test_2_days_to_expiration_no_recorta_el_vencimiento(self):
        from wbj.tito.occ import days_to_expiration

        assert days_to_expiration("2026-09-18", self.AHORA) == 49
        # Con hora, la concatenación con `T00:00:00Z` es inválida → NaN.
        assert math.isnan(days_to_expiration("2026-09-18T00:00:00Z", self.AHORA))

    def test_3_dte_of_no_cae_a_cero(self):
        from wbj.tito.gex_heatmap import _dte_of

        assert _dte_of("2026-09-18", self.AHORA) == 49
        # `Math.max(0, NaN)` en JS es NaN, no 0 — y 0 se lee como "vence hoy".
        assert math.isnan(_dte_of("basura", self.AHORA))
        assert math.isnan(_dte_of("2026-09-18T00:00:00Z", self.AHORA))
        # Un vencimiento pasado SÍ se corta en 0, que eso sí es suyo.
        assert _dte_of("2025-01-01", self.AHORA) == 0

    def test_4_days_between_no_cae_a_cero(self):
        from wbj.tito.validation import _days_between

        assert _days_between("2026-07-01T00:00:00Z", "2026-07-31T00:00:00Z") == 30
        # Un 0 aquí se leería como "el flow es de hoy" y el backtest lo trataría
        # como recién abierto en vez de dejarlo sin resolver.
        assert math.isnan(_days_between("2026-07-01T00:00:00Z", "ayer"))
        assert math.isnan(_days_between("basura", "2026-07-31T00:00:00Z"))

    def test_5_recency_weight_entiende_los_formatos_cortos(self):
        from wbj.tito.news import recency_weight

        assert recency_weight("2026-07-31T15:00:00Z", self.AHORA) == 1.0
        # `"2026"` es el 1 de enero para él: una noticia de hace 7 meses.
        assert recency_weight("2026", self.AHORA) == 0.1
        assert recency_weight("ayer", self.AHORA) == 1.0     # ilegible → 1

    def test_6_epoch_acepta_el_timestamp_sin_zona(self):
        from wbj.tito.flow import _epoch

        # ES2015+ lo lee en la zona LOCAL. El port lo descartaba, y con él el
        # trade entero salía del conteo de repetición y de racimos.
        assert not math.isnan(_epoch("2026-07-31T15:00:00"))
        assert not math.isnan(_epoch("2026-07-31"))
        assert math.isnan(_epoch("ayer"))


class TestLaRepeticionNoFiltraLosIlegibles:
    """`markRepeated` de su `flow.ts` NO descarta un timestamp ilegible.

    Su `sort` compara `Date.parse(a) - Date.parse(b)`, y ECMA-262 manda tratar
    un comparador que devuelve `NaN` como 0 —"iguales"—, así que con un sort
    estable la fila se queda donde estaba. Y en la ventana deslizante
    `NaN > REPEAT_WINDOW_MS` es falso, así que `start` no avanza. El port las
    filtraba, o sea quitaba trades del conteo.
    """

    def _fila(self, ts, i):
        from wbj.tito.flow import FlowFlags, FlowRow, TradeScores

        return FlowRow(
            id=i, symbol="X260918C00100000", underlying="X", type="call",
            strike=100.0, expiration="2026-09-18", dte=49, price=2.0, size=10,
            side="AT_ASK", aggression="ask", asset_price=95.0, bid=1.9, ask=2.1,
            premium=500_000.0, delta=0.6, gamma=0.03, theta=-0.05, vega=0.1,
            theta_pct_daily=2.5, iv=0.45, open_interest=1000, volume=500,
            score=8, sentiment="bullish", timestamp=ts,
            condition_code=None, condition_name=None,
            flags=FlowFlags(), scores=TradeScores(),
        )

    def test_una_fila_ilegible_no_saca_al_grupo_de_la_ventana(self):
        from wbj.tito.flow import _mark_repeated

        filas = [self._fila("2026-07-31T15:00:00Z", 1),
                 self._fila("ayer", 2),
                 self._fila("2026-07-31T15:01:00Z", 3)]
        _mark_repeated(filas)
        # Los 3 caen en la misma ventana de 5 min: los 3 quedan marcados.
        assert [f.flags.repeated for f in filas] == [True, True, True]

    def test_con_menos_de_tres_no_marca_nada(self):
        from wbj.tito.flow import _mark_repeated

        filas = [self._fila("2026-07-31T15:00:00Z", 1), self._fila("ayer", 2)]
        _mark_repeated(filas)
        assert not any(f.flags.repeated for f in filas)


class TestElGexNoDescartaElVencimientoIlegible:
    """`if (dte <= 0) continue;` — con `NaN` la comparación es falsa.

    El port hacía `if dte is None or dte <= 0: continue` y la fila desaparecía.
    Él la deja entrar: `bsGamma` la corta a 0 por su propia guarda `invalid`
    —`NaN > 0` es falso—, así que el GEX sale 0 igual. Lo que NO es igual es que
    en su versión el strike **existe** como nodo con GEX 0, y en el port no
    existía. Un muro que vale cero y un muro que no está no son lo mismo: el
    primero se ve en el mapa y el segundo se pierde en silencio.
    """

    def _cadena(self, exp):
        from wbj.tito.structure import ChainRow

        return [ChainRow("call", exp, float(s), 9000, 400, 9000.0 * 100 * s)
                for s in range(90, 115, 5)]

    def test_el_strike_sigue_apareciendo_como_nodo(self):
        from wbj.tito.gex import gex_analysis

        now = _utc("2026-07-31T18:30:00Z")
        rota = gex_analysis(self._cadena("2026-09-18T00:00:00Z"), [100.0] * 60, 100.0, now)
        assert rota.n == 5
        assert [nodo.strike for nodo in rota.nodes] == [90.0, 95.0, 100.0, 105.0, 110.0]
        assert all(nodo.net_gex == 0.0 for nodo in rota.nodes)

    def test_con_el_vencimiento_bueno_el_gex_es_real(self):
        from wbj.tito.gex import gex_analysis

        now = _utc("2026-07-31T18:30:00Z")
        buena = gex_analysis(self._cadena("2026-09-18"), [100.0] * 60, 100.0, now)
        assert buena.total_net_gex > 0
        assert math.isfinite(buena.total_net_gex)
