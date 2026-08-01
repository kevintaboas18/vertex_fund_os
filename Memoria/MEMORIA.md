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
- NVDA · 2026-08-01 11:23 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 11:28 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 11:30 · Avoid / Wait · raw 37.1/100 · FV $281.05
- AAPL · 2026-08-01 11:32 · Avoid / Wait · raw 31.6/100 · FV $322.17
- NVDA · 2026-08-01 11:34 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 11:37 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 11:41 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 11:49 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 11:51 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 11:55 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 11:56 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 11:58 · Avoid / Wait · raw 37.1/100 · FV $281.05
- AAPL · 2026-08-01 11:59 · Avoid / Wait · raw 31.6/100 · FV $322.17
- NVDA · 2026-08-01 12:00 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 13:50 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 13:51 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 13:52 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 13:53 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 13:54 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 13:55 · Avoid / Wait · raw 37.1/100 · FV $281.05
- NVDA · 2026-08-01 14:10 · Avoid / Wait · raw 37.1/100 · FV $281.05
