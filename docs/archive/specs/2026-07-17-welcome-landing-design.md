# Pantalla de bienvenida centrada — WBJ web app

**Fecha:** 2026-07-17
**Archivo afectado:** `engine/scripts/webapp.py` (la constante `PAGE`)
**Alcance:** solo frontend (HTML/CSS/JS embebido). No cambian los endpoints Python ni el motor.

## Objetivo

Cuando alguien abre la web app por primera vez debe ver una pantalla de
bienvenida centrada, no la barra de búsqueda pegada arriba a la izquierda.

- Título: **"Bienvenido a Warren Buffett Jr"**
- Subtítulo: **"Tu Especialista Financiero"**
- Barra de búsqueda (con autocompletado existente) centrada debajo.
- Botón "✨ Descubrir empresas" se mantiene.
- Tema claro actual, acento morado — sin cambios de paleta.

## Comportamiento (collapse-to-top)

Dos estados en un contenedor `.app`:

| Estado | Clase | Layout |
|---|---|---|
| Landing (inicial) | `.app.landing` | Bloque bienvenida+búsqueda centrado vertical y horizontalmente en la ventana. |
| Resultados | `.app.results` | El bloque se encoge a una barra superior compacta: "Warren Buffett Jr" pequeño a la izquierda + barra de búsqueda al lado. Debajo, el scorecard/gráficas como hoy. |

Transiciones:
- Al ejecutar `run(ticker)` o el "Descubrir empresas" → se agrega la clase
  `results` (y se quita `landing`) sobre `.app`.
- Con la búsqueda vacía y sin resultados visibles, o al hacer clic en el
  título "Warren Buffett Jr", se vuelve al estado `landing`.
- La animación es CSS puro (`transition` sobre tamaño de fuente, padding y
  posición). Sin librerías nuevas.

## Implementación

1. Envolver el encabezado (kicker, título, subtítulo, topbar de búsqueda) y el
   área de resultados en un contenedor `<div class="app landing">`.
2. Añadir el subtítulo "Tu Especialista Financiero" y cambiar el título a
   "Bienvenido a Warren Buffett Jr". El kicker actual puede quedar como línea
   superior pequeña.
3. CSS: en `.app.landing` centrar con flexbox (min-height 100vh, justify/align
   center) y agrandar título; en `.app.results` colapsar a barra superior
   (título pequeño en fila con la barra de búsqueda, alineado a la izquierda).
4. JS: función `setMode('landing'|'results')` que hace toggle de clases sobre
   `.app`. Llamarla al inicio de `run()` y del handler de "Descubrir", y volver
   a `landing` al limpiar/clic en el título.
5. Reutilizar sin cambios: `search()` autocompletado, `run()`, `renderChart()`,
   los 5 cards, endpoints Python.

## Qué NO cambia

Los 5 cards de resultados, la gráfica SVG, los targets, el disclaimer, la lógica
de scoring, y todos los endpoints (`/api/search`, `/api/analyze`, `/api/screen`).

## Criterio de éxito

- Al abrir `http://localhost:8765` se ve el bloque de bienvenida centrado.
- Al buscar y analizar un ticker, el encabezado colapsa arriba y aparecen los
  resultados debajo — sin errores en consola.
- Textos en español; ortografía correcta ("Especialista").
