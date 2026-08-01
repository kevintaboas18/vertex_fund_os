# Perfil del Inversionista — Kevin

> Este es el perfil que el sistema usa SIEMPRE para el filtro de "fit" (reemplaza
> al de Victor Gonzalez). El agente lo lee antes de explicar cualquier
> recomendación. No cambia la matemática del scoring — solo contextualiza la
> explicación al perfil de Kevin.
>
> NOTA: este contenido es un borrador armado a partir de respuestas rápidas.
> Reemplázalo con tu perfil detallado cuando lo tengas (mismo archivo).

## Objetivos
- **Crecimiento de capital** con horizonte principal de **1 a 3 años**.
- **Trades de opciones** de corto plazo (semanas a meses) para aprovechar timing.
- **Generación de ingresos** en el largo plazo (**5+ años**).

## Tolerancia al riesgo
- **Agresivo y especulativo.** Acepta volatilidad alta y posibilidad de pérdidas
  grandes a cambio de mayor potencial de crecimiento.

## Instrumentos
- **Acciones** individuales (EE.UU.)
- **ETFs**
- **Opciones** (calls/puts y estrategias)
- Sin forex. Sin cripto.

## Universo
- Estados Unidos.

## Capital
- Aproximadamente **$1,000 USD**.

## Reglas de dimensionamiento (guía, no altera el scoring)
- **Máximo por posición individual: 20% – 30% del capital.** Elegido por mí.
  Con ~$1,000 son **$200–$300 por posición**, es decir entre 3 y 5 posiciones
  simultáneas como máximo. El engine lee este rango de aquí (`risk.py`), así
  que cambiarlo en esta línea lo cambia en todo el sistema.
- Capital pequeño + perfil agresivo: el **sizing** debe cuidar el riesgo de
  ruina. Con $1,000 y opciones, una sola posición mal dimensionada puede borrar
  una fracción grande de la cuenta. Con un tope de 30%, **tres pérdidas totales
  seguidas se llevan casi la cuenta entera** — por eso el reporte debe declarar
  siempre el nivel de invalidación antes que el objetivo.
- Prioriza **probabilidad de éxito** y **puntos de entrada/salida (timing)**.
- El sistema entrega **clasificación de research** con niveles de confirmación e
  invalidación — nunca una orden automática de compra/venta. La ejecución es
  siempre manual y del inversionista.

## Qué espero del sistema
- Que **la matemática y el scoring se calculen exactamente con la metodología de
  Victor** (framework WBJ, Cerebro).
- Que el **LLM solo explique en palabras simples y detalladas** qué significa
  cada número, gate, override, nivel y su ajuste con este perfil — **sin cambiar
  ni reducir ningún cálculo**.
