"""La regla de la casa: los números nunca salen de un modelo.

> «los números ni los cálculos ni nada jamás ni nunca vendrán de un LLM.»
> — Kevin, 29/08/2026.

`CLAUDE.md` lo dice desde el primer día —«el LLM nunca puntúa: traduce a
palabras lo que el engine ya decidió»— y había **dos agujeros**, los dos
silenciosos:

1. **El tamaño de posición.** La probabilidad `p_positive_12m` la inventaba el
   modelo y alimentaba el Kelly. Había un anclaje al base-rate medido, pero
   pesado por muestra: «n alto → base-rate; n bajo → el LLM». Con el track
   record vacío —que es donde está hoy— el peso del base-rate es CERO y la
   probabilidad del modelo pasaba **entera** al cálculo de cuánto dinero poner.

2. **El camino sin motor.** `conviccion_score` y `recommendation` sólo se
   sobrescriben con los de Victor dentro de la rama del motor. Si el motor no
   cargaba, se quedaba lo que el modelo hubiera puesto — y en pantalla se veía
   igual que un análisis bueno.
"""

from __future__ import annotations

import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import vertex_api as V  # noqa: E402


class TestElDineroNoSeDimensionaConUnaAdivinanza:
    """Se mide el RESULTADO, no el código fuente.

    La decisión vive en dos funciones puras —`_ancla_probabilidad` y
    `_dimensiona`— justo para poder medirla así. Un caso que compare el texto
    del código se pone rojo por mover una línea y verde si la fabricación
    vuelve por otro sitio.
    """

    def test_SIN_MUESTRA_no_hay_probabilidad(self):
        """Es el caso de hoy: 28 predicciones y ninguna vencida."""
        p, detalle = V._ancla_probabilidad([], None)
        assert p is None and detalle is None

    def test_y_sin_probabilidad_NO_hay_tamano_de_posicion(self):
        """Kelly es `f* = p - q/b`. Sin `p` no hay fórmula, y rellenarla daría
        un número con pinta de cálculo y nada detrás."""
        d = V._dimensiona(None, reward=0.30, risk_dn=0.10)
        assert d["suggested_pct"] is None
        assert d["kelly_full_pct"] is None and d["kelly_half_pct"] is None
        assert "sin evidencia no hay número" in d["cap_reason"]
        assert "VENCIDA" in d["cap_reason"]
        # El reward/risk SÍ se publica: ése sale de los targets, no de una
        # probabilidad. No todo se cae por no poder dimensionar.
        assert d["reward_risk"] == 3.0

    def test_un_NULO_no_se_publica_como_un_CERO(self):
        """«No se puede dimensionar» y «no pongas nada» llevan a decisiones
        opuestas."""
        d = V._dimensiona(None, 0.30, 0.10)
        assert d["suggested_pct"] is not 0 and d["suggested_pct"] is None

    def test_CON_muestra_grande_manda_el_base_rate(self):
        p, det = V._ancla_probabilidad([("ticker NVDA", 70.0, 90)])
        assert det["weight_base"] >= 0.9, "con n=90 el base-rate tiene que mandar"
        assert 67 <= p <= 70

    def test_con_muestra_PEQUENA_manda_la_moneda_al_aire(self):
        """Y la moneda al aire es el 50 — no la opinión del modelo. Antes ese
        complemento era `p_del_modelo`, así que con n bajo el número inventado
        entraba casi entero."""
        p, det = V._ancla_probabilidad([("ticker NVDA", 90.0, 1)])
        assert det["prior_sin_opinion"] == 50.0
        assert det["weight_base"] < 0.1
        assert 50 <= p <= 55, f"con n=1 la p tendría que estar pegada al 50, y es {p}"

    def test_gana_el_ambito_MAS_ESPECIFICO_con_muestra_suficiente(self):
        _, det = V._ancla_probabilidad([
            ("ticker NVDA", 70.0, 8), ("setup Momentum", 60.0, 200)])
        assert det["scope"] == "ticker NVDA", "n>=5 en el más específico manda"

    def test_el_especifico_SIN_muestra_no_le_gana_al_general(self):
        """Un ticker con n=2 no dice nada. Gana el primero con n>=5, aunque
        sea menos específico — que es como estaba y como debe estar."""
        _, det = V._ancla_probabilidad([
            ("ticker NVDA", 70.0, 2), ("setup Momentum", 60.0, 200)])
        assert det["scope"] == "setup Momentum"

    def test_y_si_NINGUNO_llega_a_cinco_se_usa_el_mas_especifico(self):
        _, det = V._ancla_probabilidad([
            ("ticker NVDA", 70.0, 2), ("setup Momentum", 60.0, 3)])
        assert det["scope"] == "ticker NVDA"

    def test_y_el_GLOBAL_es_el_ultimo_recurso(self):
        _, det = V._ancla_probabilidad([], ("global", 55.0, 40))
        assert det["scope"] == "global"

    def test_el_tope_de_concentracion_sigue_acotando(self):
        """Lo que ya funcionaba no puede romperse: con una p alta el Kelly
        pediría más del 25% y el tope manda."""
        d = V._dimensiona(95, reward=1.0, risk_dn=0.10, held_w=10.0)
        assert d["suggested_pct"] <= 15.0
        assert "tope de concentración" in d["cap_reason"]

    def test_los_porcentajes_son_PORCENTAJES(self):
        """`_dimensiona` ya devuelve porcentajes. Multiplicarlos otra vez por
        100 daba una asignación sugerida del 2.330%."""
        d = V._dimensiona(60, 0.30, 0.10)
        assert 0 < d["suggested_pct"] <= 25.0, d["suggested_pct"]
        assert 0 < d["kelly_half_pct"] <= 100.0


class TestSinMotorNoHayNumeros:
    """Si el motor determinista no calculó, no hay puntaje. Ni recomendación,
    ni valor justo. Lo que quede es contexto en palabras — y se mide llamando
    a la función, no leyendo el código."""

    def _con_numeros_del_modelo(self):
        """Lo que devolvería el modelo si nadie lo corrigiera."""
        return {"conviccion_score": 87, "recommendation": "FAVORABLE",
                "fair_value": 412.0, "upside_pct": 31.0, "confidence": 78,
                "p_bull_correct": 71, "wbj": {"raw_total": 87},
                "tesis_inversion_completa": "La prosa del modelo."}

    def test_NINGUN_numero_del_modelo_sobrevive(self):
        aj = V._sin_motor_no_hay_numeros(self._con_numeros_del_modelo())
        for campo in V._NUMEROS_QUE_SOLO_DA_EL_MOTOR:
            assert aj[campo] is None, f"{campo} se publicó con el valor del modelo"
        assert aj["wbj"] is None

    def test_pero_el_TEXTO_se_conserva(self):
        """Explicar en palabras sí es trabajo del modelo. Lo que no puede
        hacer es poner números."""
        aj = V._sin_motor_no_hay_numeros(self._con_numeros_del_modelo())
        assert aj["tesis_inversion_completa"] == "La prosa del modelo."

    def test_queda_MARCADO_para_que_el_archivo_lo_sepa(self):
        """Un reporte sin motor no puede confundirse después con uno bueno."""
        aj = V._sin_motor_no_hay_numeros({})
        assert aj["motor_ausente"] is True
        assert "SIN MOTOR" in aj["scores_source"]

    def test_y_lo_dice_en_palabras_llanas(self):
        aj = V._sin_motor_no_hay_numeros({})
        aviso = aj["aviso_sin_motor"]
        assert "NO hay puntaje" in aviso
        assert "Revisa el servidor" in aviso
        assert "ATR" not in aviso and "engine" not in aviso.lower()

    def test_lo_de_despues_aguanta_los_nulos(self):
        """La respuesta tiene que seguir saliendo: un panel caído no informa de
        nada, y lo que hace falta es que se VEA que no hay número."""
        r = V.compute_memory_comparison(None, 100.0, None, None, None)
        assert isinstance(r, dict) and r.get("has_prior") is False


class TestElSpreadSeRestaDelResultado:
    """El motor ya MEDÍA el spread —`spread_pct_of` en `wheel.py`— pero sólo
    para puntuar la calidad del contrato. Nadie lo restaba del resultado.

    En Robinhood la comisión de opciones es ~$0: lo que cuesta dinero es la
    horquilla. Se entra al ask y se sale al bid, así que el viaje completo
    cuesta aproximadamente el spread entero medido sobre el mid.
    """

    def test_el_spread_se_come_el_rendimiento(self):
        """Con $1.000 esto no es un detalle."""
        assert V._neto_de_spread(15.0, 8.0) == 7.0

    def test_un_rendimiento_pequeno_con_spread_ancho_es_CERO(self):
        """Un «+6%» con 6% de spread no es un +6% pequeño: es nada."""
        assert V._neto_de_spread(6.0, 6.0) == 0.0

    def test_y_puede_salir_NEGATIVO_sin_maquillarlo(self):
        """Acotarlo a cero escondería justo el caso que hay que ver."""
        assert V._neto_de_spread(3.0, 8.0) == -5.0

    @pytest.mark.parametrize("bruto,spread", [
        (15.0, None), (None, 8.0), (None, None), (15.0, "8"), (15.0, -1),
        (float("nan"), 8.0), (15.0, float("inf")), (15.0, True),
    ])
    def test_sin_los_DOS_datos_no_se_publica_neto(self, bruto, spread):
        """Un neto calculado sin saber el spread sería el bruto disfrazado —
        peor que no darlo, porque parece que ya lleva el coste dentro."""
        assert V._neto_de_spread(bruto, spread) is None

    def test_el_BRUTO_de_Victor_no_se_toca(self):
        """`engine/wbj/tito/wheel.py` es port literal y `diff_wheel.sh` lo
        compara número a número contra su código. El neto se añade AL LADO."""
        import subprocess
        import sys as _s

        r = subprocess.run(
            [_s.executable, "-c",
             "import sys; sys.path.insert(0,'engine');"
             "from wbj.tito.wheel import wheel_metrics;"
             "m = wheel_metrics(100.0, 2.0, 105.0, 30, 0.35);"
             "print(round(m.return_pct, 4))"],
            cwd=RAIZ, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        # 2.00 de prima sobre 100 de strike = 2% del colateral. Es la fórmula
        # de Víctor sin el spread dentro, y así se queda.
        assert float(r.stdout.strip()) == 2.0
