"""Tito Metralleta — motor de análisis de flujo de opciones (options flow).

Port fiel del motor TypeScript de "Agente Tito Metralleta" (`web/lib/*.ts`) a
Python, para vivir dentro de Vertex Fund OS en el área de **Proyecciones**.

Qué hace: toma el tape de opciones (MarketSnack) y la cadena completa, y produce
un **scorecard de 0 a 100** en 6 categorías más tres escenarios de precio con
probabilidad de toque.

    | # | Sub-agente             | Pregunta                              | Peso |
    |---|------------------------|---------------------------------------|------|
    | 1 | Agresividad            | ¿Compran al ask con fuerza?           | 20%  |
    | 2 | Convicción             | ¿Cuánto dinero real y qué ejecución?  | 20%  |
    | 3 | Inusualidad            | ¿Es flujo anormal? (griegos)          | 20%  |
    | 4 | Estructura             | ¿En qué strikes/vencimientos se acumula? | 15% |
    | 5 | Contexto IV            | ¿La IV está limpia o inflada?         | 10%  |
    | 6 | Confirmación de Precio | ¿El precio valida o absorbe?          | 15%  |

Reglas heredadas que NO se negocian:

- **El GEX confirma, no adivina.** Ningún escenario puede salirse del cono de 2σ;
  la volatilidad manda sobre el posicionamiento.
- **Salvaguarda de liquidez.** Si la cadena es ilíquida, los datos se marcan como
  no fiables y el sistema no recomienda operar. Aplica también al GEX.
- **Nunca un solo número.** Un target siempre viene en rango, con sus supuestos
  declarados — la misma regla de visualización del `CLAUDE.md` de Vertex.
- **Sin evidencia, no hay score.** Una categoría sin datos es `None` (no cero), y
  el ponderado se calcula solo sobre el peso activo, recortando la confianza.

Todos los módulos de este paquete son **puros**: reciben datos, devuelven
estructuras, no hacen I/O. El único que habla con la red es `marketsnack`.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
