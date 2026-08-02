"""Prediction Pro — el cierre del sistema.

Port de `web/lib/prediction.ts`.

Junta lo que producen los 6 sub-agentes, el mapa de GEX y la matemática de
desviación estándar en tres escenarios legibles (bajista / base / alcista) y un
resumen en lenguaje llano.

**Regla de oro heredada de la Guía GEX: el GEX confirma, no adivina.** Por eso
ningún escenario puede salirse del cono de 2σ — la volatilidad manda sobre el
posicionamiento. Y por eso también se fuerza el orden estricto
``bear < base < bull``: tres escenarios que colapsan en el mismo precio no son
tres escenarios.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

from .jsmath import js_round, js_gt, js_number
from .expected_move import (
    LevelInput,
    LevelProb,
    expected_move,
    level_probabilities,
    prob_touch,
)

__all__ = [
    "HORIZONS",
    "WEIGHTS",
    "CALIBRATION",
    "ScenarioKind",
    "Scenario",
    "SubScores",
    "ProPrediction",
    "weighted_score",
    "calibration_shift_pct",
    "confidence_of",
    "predict_pro",
]

ScenarioKind = Literal["bear", "base", "bull"]

#: Horizontes que ofrece el panel.
HORIZONS: list[dict] = [
    {"days": 10, "label": "10 días"},
    {"days": 20, "label": "20 días"},
    {"days": 30, "label": "30 días"},
]

#: Pesos del scorecard (suman 100).
WEIGHTS: dict[str, int] = {
    "aggression": 20,
    "conviction": 20,
    "unusuality": 20,
    "structure": 15,
    "iv_context": 10,
    "validation": 15,
}

#: Calibración por memoria (lazo de control). Amortiguada y acotada a propósito:
#: solo actúa con historial suficiente, corrige una FRACCIÓN del sesgo y tiene
#: tope, así converge — al mejorar, el sesgo baja y la corrección se apaga sola.
CALIBRATION = {"min_samples": 5, "gain": 0.6, "cap_pct": 3.0}


@dataclass(frozen=True)
class Scenario:
    kind: ScenarioKind
    target: float
    change_pct: float
    #: Probabilidad de que el precio TOQUE ese nivel dentro del horizonte (0-1).
    probability: float
    #: Qué sostiene el escenario, en una línea.
    driver: str


@dataclass(frozen=True)
class SubScores:
    aggression: float | None = None
    conviction: float | None = None
    unusuality: float | None = None
    structure: float | None = None
    iv_context: float | None = None
    validation: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return {
            "aggression": self.aggression,
            "conviction": self.conviction,
            "unusuality": self.unusuality,
            "structure": self.structure,
            "iv_context": self.iv_context,
            "validation": self.validation,
        }


@dataclass
class ProPrediction:
    horizon_days: int
    spot: float
    iv: float
    bear: Scenario
    base: Scenario
    bull: Scenario
    #: Sentiment 0-100 ponderado por los pesos del scorecard.
    score: int
    #: Categorías con dato / total.
    active: int
    confidence: int
    levels: list[LevelProb]
    direction: Literal["up", "down", "flat"]
    summary: str
    caveat: str | None
    #: Auto-corrección aplicada al target base según la memoria del agente.
    calibration: dict


def weighted_score(scores: SubScores) -> tuple[int, int, int]:
    """Sentiment 0-100: promedio ponderado de las categorías que YA tienen dato.

    Devuelve ``(score, active, weight)``. Se divide por el **peso activo**, no
    por 100: una categoría sin datos no arrastra el score a cero, pero sí
    recorta la confianza y dispara un caveat. La consecuencia a tener presente
    es que dos tickers con distinto número de categorías activas **no son
    estrictamente comparables** por su score.
    """
    pts = weight = 0.0
    active = 0
    d = scores.as_dict()
    for key, w in WEIGHTS.items():
        s = d.get(key)
        if s is None:
            continue
        active += 1
        weight += w
        pts += (js_number(s) / 10) * w
    return (js_round(pts / weight * 100) if weight > 0 else 0, active, int(weight))


def calibration_shift_pct(bias_pct: float | None, samples: int) -> float:
    """Ajuste en % del spot (firmado; >0 sube el target)."""
    if bias_pct is None or samples < CALIBRATION["min_samples"] or not math.isfinite(bias_pct):
        return 0.0
    raw = bias_pct * CALIBRATION["gain"]
    cap = CALIBRATION["cap_pct"]
    return max(-cap, min(cap, raw))


def confidence_of(
    levels: Sequence[LevelProb],
    active: int,
    hit_rate: float | None,
    low_liquidity: bool,
) -> int:
    """Confianza 0-100.

    Mezcla cuánto domina el nivel imán, cuántas categorías respondieron y qué
    tan bien viene acertando el backtest. Con baja liquidez es **0**: la
    confianza nunca convierte un desconocido en un score favorable.
    """
    if low_liquidity or not levels:
        return 0
    # Un solo nivel con >=50% del peso ya es "nitidez máxima": por eso x2 y se acota.
    sharpness = min(1.0, levels[0].magnet * 2)
    coverage = min(1.0, active / 6)
    track = (0.5 if hit_rate is None
             else min(1.0, max(0.0, js_number(hit_rate) / 100)))
    raw = 0.45 * sharpness + 0.30 * coverage + 0.25 * track
    return js_round(100 * min(1.0, max(0.0, raw)))


def _pct_change(spot: float, target: float) -> float:
    target, spot = js_number(target), js_number(spot)
    return ((target - spot) / spot * 100) if spot > 0 else 0.0


def predict_pro(
    spot: float,
    iv: float,
    horizon_days: int,
    nodes: Sequence[LevelInput],
    scores: SubScores,
    regime: Literal["positive", "negative"],
    callvpct: float | None = None,
    hit_rate: float | None = None,
    low_liquidity: bool = False,
    calibration: dict | None = None,
) -> ProPrediction:
    """Tres escenarios con probabilidad de toque + resumen en español.

    `callvpct` es el % del premium notable que está en calls (dirección del
    dinero). `calibration` es ``{"bias_pct": float|None, "samples": int}``.
    """
    em = expected_move(spot, iv, horizon_days)
    levels = level_probabilities(spot, iv, horizon_days, nodes)[:10]

    score, active, _ = weighted_score(scores)

    # BASE = el nivel de mayor peso (probabilidad × dinero). Es el imán.
    magnet = levels[0] if levels else None
    raw_base = magnet.strike if magnet else spot

    # Auto-corrección por memoria: si el agente históricamente apunta alto/bajo,
    # se ajusta el target base (recortado al cono de 2σ). El imán CRUDO sigue
    # anclando la búsqueda de bull/bear para no arrastrar el sesgo a los extremos.
    def in_cone(x: float) -> float:
        return min(max(js_number(x), js_number(em.lower2)), js_number(em.upper2))

    # `calibration?.biasPct` de JS: si no es un objeto, `undefined`.
    cal = calibration if isinstance(calibration, dict) else {}
    shift_pct = calibration_shift_pct(cal.get("bias_pct"),
                                      int(js_number(cal.get("samples") or 0)))
    # El recorte al cono es INCONDICIONAL.
    #
    # DIVERGENCIA vs el TypeScript original (deliberada, es un arreglo): allí
    # era `shiftPct !== 0 ? inCone(...) : rawBase`, así que el target base solo
    # se acotaba CUANDO había calibración. Sin ella el imán salía crudo y podía
    # escaparse del cono de 2σ mientras bear y bull sí se recortaban — rompiendo
    # la regla de oro que este mismo módulo declara ("el GEX confirma, no
    # adivina: la volatilidad manda sobre el posicionamiento") y el orden
    # bear < base < bull.
    #
    # En el pipeline completo no se alcanzaba porque `gex_analysis` ya filtra
    # los nodos a ±20% del spot, pero `predict_pro` es público y su contrato
    # dice que NINGÚN escenario sale del cono. Ahora lo cumple siempre.
    #
    # El tope del base es el punto medio entre 1σ y 2σ, no el borde de 2σ: ese
    # borde pertenece a los escenarios EXTREMOS. Si el base pudiera sentarse
    # justo encima, no quedaría sitio para el bear (o el bull) y los tres
    # colapsarían en el mismo precio. Con un imán alcanzable —lo normal, porque
    # `gex_analysis` filtra a ±20% del spot— este tope no toca nada; solo actúa
    # cuando el imán está tan lejos que la volatilidad no lo alcanza, y ahí
    # decir "hasta aquí llega lo defendible" es más honesto que fingir el nivel.
    base_floor = (js_number(em.lower1) + js_number(em.lower2)) / 2
    base_ceil = (js_number(em.upper1) + js_number(em.upper2)) / 2
    base_target = min(max(js_number(raw_base) + js_number(spot) * shift_pct / 100,
                          base_floor), base_ceil)

    # Bull y bear se buscan EXCLUYENDO el nivel base: si no, cuando el imán ya
    # está arriba (o abajo) los tres escenarios colapsan en el mismo precio.
    others = [l for l in levels if l.strike != raw_base]
    above = max((l for l in others if js_number(l.strike) > spot),
                key=lambda l: js_number(l.magnet), default=None)
    below = max((l for l in others if js_number(l.strike) < spot),
                key=lambda l: js_number(l.magnet), default=None)

    bull_target = above.strike if above else em.upper1
    bear_target = below.strike if below else em.lower1

    # El bear tiene que ser MÁS bajista que el base y el bull más alcista: si el
    # muro más cercano se queda corto, el escenario lo marca la volatilidad (1σ).
    #
    # DIVERGENCIA vs el TypeScript original (deliberada, es un arreglo):
    # allí esto era `min(em.lower1, base_target)`, que devuelve `base_target`
    # cuando el suelo de 1σ queda POR ENCIMA de la base — pasa cuando el imán
    # está lejos por debajo del spot a plazo corto (p.ej. imán en 92 con spot
    # 100 a 10 días: lower1 ≈ 92.05 > 92). El resultado era bear == base: dos
    # escenarios en el mismo precio, justo lo que la propia función declara
    # impedir y lo que su test "ordena SIEMPRE bear < base < bull" exige.
    #
    # Cuando 1σ no alcanza, el escenario lo marca la SIGUIENTE banda de
    # volatilidad (2σ), que está por debajo de la base por construcción y sigue
    # siendo el límite duro que el motor ya respeta. La volatilidad manda, que
    # es la regla de oro — solo que aquí hay que bajar un peldaño más.
    bear_target, bull_target = js_number(bear_target), js_number(bull_target)
    if bear_target >= base_target:
        bear_target = em.lower1 if js_number(em.lower1) < base_target else em.lower2
    if bull_target <= base_target:
        bull_target = em.upper1 if js_number(em.upper1) > base_target else em.upper2

    # Y nada puede salirse del cono de 2σ.
    bear_target = max(bear_target, em.lower2)
    bull_target = min(bull_target, em.upper2)

    regime_word = (
        "el dealer estabiliza (γ+): el precio tiende a frenarse ahí"
        if regime == "positive"
        else "el dealer amplifica (γ−): si el precio llega, acelera"
    )

    def _fijo(x, n: int = 2) -> str:
        """`Number(x).toFixed(n)` de JS: coacciona y no lanza.

        Con `Infinity` escribe "Infinity" (no "1e+308" ni un `OverflowError`) y
        con `NaN` escribe "NaN". Lo destapó el corpus malformado.
        """
        v = js_number(x)
        if v != v:
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        return f"{v:.{n}f}"

    def wall_text(l: LevelProb) -> str:
        lado = "calls" if l.side == "call" else "puts"
        # `${l.strike.toFixed(2)}` — coacciona y nunca lanza. En Python un
        # `:.2f` sobre `None` es un `TypeError` y sobre `NaN` da "nan" donde JS
        # escribe "NaN"; `_fijo` reproduce su `toFixed`.
        return (f"Gamma concentrada en ${_fijo(l.strike)} (dominan {lado})"
                f" — {regime_word}")

    bull = Scenario(
        kind="bull",
        target=bull_target,
        change_pct=_pct_change(spot, bull_target),
        probability=prob_touch(spot, bull_target, iv, horizon_days),
        driver=(
            wall_text(above)
            if above
            else "Techo de 1σ: hasta aquí llega el movimiento esperado por volatilidad"
        ),
    )

    bear = Scenario(
        kind="bear",
        target=bear_target,
        change_pct=_pct_change(spot, bear_target),
        probability=prob_touch(spot, bear_target, iv, horizon_days),
        driver=(
            wall_text(below)
            if below
            else "Suelo de 1σ: hasta aquí llega la caída esperada por volatilidad"
        ),
    )

    base = Scenario(
        kind="base",
        target=base_target,
        change_pct=_pct_change(spot, base_target),
        probability=prob_touch(spot, base_target, iv, horizon_days) if magnet else 0.0,
        driver=(
            f"Nivel imán: {js_number(magnet.magnet) * 100:.0f}% del peso del mapa"
            f" está en ${js_number(magnet.strike):.2f}"
            if magnet
            else "Sin nodos de gamma suficientes para fijar un imán"
        ),
    )

    direction = "up" if base.change_pct > 1 else ("down" if base.change_pct < -1 else "flat")
    confidence = confidence_of(levels, active, hit_rate, low_liquidity)

    # ---- resumen en lenguaje llano ----
    if direction == "up":
        dir_text = f"hacia ${base_target:.2f} (+{base.change_pct:.1f}%)"
    elif direction == "down":
        dir_text = f"hacia ${base_target:.2f} ({base.change_pct:.1f}%)"
    else:
        dir_text = f"lateral, cerca de ${base_target:.2f}"

    cp = js_number(callvpct)
    if callvpct is None:
        money_text = "No hay lectura clara del flujo."
    elif cp >= 60:
        money_text = f"El dinero está {cp:.0f}% en calls: apuesta al alza."
    elif cp <= 40:
        money_text = f"El dinero está {100 - cp:.0f}% en puts: apuesta a la baja."
    else:
        money_text = f"El dinero está repartido ({cp:.0f}% calls): sin apuesta dominante."

    if score >= 70:
        score_text = "Las señales de los sub-agentes son fuertes."
    elif score >= 50:
        score_text = "Las señales de los sub-agentes son mixtas tirando a favorables."
    elif score >= 35:
        score_text = "Las señales de los sub-agentes son débiles."
    else:
        score_text = "Las señales de los sub-agentes son muy débiles."

    track_text = (
        ""
        if hit_rate is None
        else (" Históricamente, cuando aparece flujo así en este ticker el precio"
              f" lo confirmó el {js_number(hit_rate):.0f}% de las veces.")
    )

    samples = int(cal.get("samples") or 0)
    cal_text = (
        f" El target se ajustó {'+' if shift_pct >= 0 else ''}{shift_pct:.1f}% por el sesgo "
        f"histórico del agente ({samples} predicciones vencidas)."
        if shift_pct != 0
        else ""
    )

    summary = (
        f"A {horizon_days} días el escenario base apunta {dir_text}, dentro de un rango "
        f"esperado de ±{em.sigma_pct:.1f}% (1σ). {money_text} {score_text}{track_text}{cal_text}"
    )

    if low_liquidity:
        caveat = (
            "Cadena de baja liquidez: la predicción se marca como NO FIABLE y no debe "
            "usarse para operar."
        )
    elif active < 6:
        caveat = f"Solo {active} de 6 sub-agentes tienen dato; la confianza está recortada."
    else:
        caveat = None

    return ProPrediction(
        horizon_days=horizon_days, spot=spot, iv=iv,
        bear=bear, base=base, bull=bull,
        score=score, active=active, confidence=confidence, levels=levels,
        direction=direction, summary=summary, caveat=caveat,
        calibration={"applied": shift_pct != 0, "shift_pct": shift_pct, "samples": samples},
    )
