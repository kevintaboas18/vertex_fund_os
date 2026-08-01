# Calibración del agente

> Este archivo lo genera `wbj track` a partir de `Reportes/*/*/prediccion.json`.
> **Nunca se edita a mano.**

## Estado: sin predicciones todavía

No hay track record que medir. El paso 2 del protocolo de memoria de
`CLAUDE.md` dice: *"si reporta sesgo medio > ±10%, decláralo en el reporte y
ajusta la confianza de los targets a la baja"*.

**Con este estado, ese paso no aplica:** el sesgo no es medible, así que **no**
se ajusta la confianza por este motivo. Ausencia de datos no es evidencia de
buena calibración — tampoco de mala.

## Cómo se llena

1. Cada análisis guarda su `prediccion.json` automáticamente.
2. Cuando haya predicciones con horizonte cumplido, correr `wbj track`.
3. El umbral de recalibración es **±10% de sesgo medio con ≥10 predicciones**.
   Por debajo de 10, la muestra no dice nada.
