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

- [AAPL](tesis/AAPL.md) · 2026-08-03 11:50 · Avoid / Wait · raw 37.8/100 · FV $322.17
- [JPM](tesis/JPM.md) · 2026-08-03 11:47 · Avoid / Wait · raw 35.6/100 · FV $366.24
- [KO](tesis/KO.md) · 2026-08-03 11:48 · Avoid / Wait · raw 47.5/100 · FV $94.18
- [NVDA](tesis/NVDA.md) · 2026-08-03 15:14 · Avoid / Wait · raw 48.6/100 · FV $281.05
- [PLTR](tesis/PLTR.md) · 2026-08-03 11:49 · Avoid / Wait · raw 32.7/100 · FV $160.62
