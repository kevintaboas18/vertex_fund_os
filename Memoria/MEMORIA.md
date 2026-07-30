# Memoria del Agente — Warren Buffett Jr

Índice de la memoria persistente. El orquestador la lee ANTES de cada
análisis y la actualiza DESPUÉS. Nunca borres una entrada: los errores
son la señal de aprendizaje.

## Estructura

| Archivo | Qué guarda | Quién escribe |
|---|---|---|
| `tesis/<TICKER>.md` | Tesis por empresa: qué se dijo, cuándo, qué pasó | El agente (Claude) |
| `errores.md` | Lecciones: sesgos detectados, supuestos fallidos, correcciones | El agente (Claude) |
| `calibracion.md` | Track record numérico: aciertos, sesgo vs targets | `wbj track` (automático) |
| `Reportes/*/*/prediccion.json` | La semilla: cada predicción con fecha | `wbj analyze` / web app (automático) |

## Ciclo de aprendizaje

1. **Analizar** → cada análisis guarda su `prediccion.json` automáticamente.
2. **Cosechar** → correr `wbj track` (ideal: mensual) actualiza `calibracion.md`.
3. **Recalibrar** → si el sesgo medio supera ±10% con ≥10 predicciones,
   ajustar `_SCENARIOS` en `engine/wbj/targets.py` y anotar el cambio en
   `errores.md` con fecha y justificación.

## Tesis activas

*(el agente agrega una línea por ticker analizado: `- [TICKER](tesis/TICKER.md) — resumen de una línea`)*
