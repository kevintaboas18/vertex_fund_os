"""Las semánticas numéricas de JavaScript que el port tiene que reproducir.

Existe por un hallazgo medido: `round()` de Python y `Math.round()` de JS **no
son la misma función**. Python usa redondeo bancario (mitad al par) y JS redondea
la mitad hacia arriba:

    round(10.5) = 10        Math.round(10.5) = 11
    round(2.5)  = 2         Math.round(2.5)  = 3
    round(11.5) = 12        Math.round(11.5) = 12      ← aquí sí coinciden

No es un caso raro. Los sub-agentes puntúan promediando enteros
(`round((iv.points + rank.points) / 2)`), y ahí el `.5` sale constantemente: de
las 121 combinaciones de dos puntuaciones de 0 a 10, **30 caen en un `.5` donde
las dos funciones dan resultados distintos**. En todas ellas el port devolvía un
punto MENOS que él, y ese punto entra directo al scorecard de 0-100.

Lo descubrió el diferencial de `levels.ts`: una fuerza de nivel salía 10 donde su
archivo daba 11.
"""

from __future__ import annotations

import math

__all__ = ["js_round"]


def js_round(x: float) -> int:
    """`Math.round(x)` de JS: la mitad SIEMPRE hacia arriba, no al par.

    ECMA-262 lo define como `floor(x + 0.5)`, incluidos los negativos —por eso
    `Math.round(-10.5)` es `-10` y no `-11`—, así que se implementa igual.

    Un `NaN` o un infinito no tienen redondeo entero: se devuelven 0 y el mayor
    entero representable respectivamente, en vez de dejar que `math.floor`
    lance. Ningún camino del motor llega aquí con esos valores —las guardas de
    `compute` los paran antes—, pero esta función es de uso general.
    """
    if not math.isfinite(x):
        return 0 if x != x else (2**53 if x > 0 else -(2**53))
    return math.floor(x + 0.5)
