# Estado del proyecto — Vertex Fund OS

**Actualizado:** 2026-08-17 · **Rama:** `main` · **Estado:** engine completo; los 26
hallazgos de la auditoría inicial, cerrados; el tab de Proyecciones portado del
repo de Víctor y verificado contra su archivo.

> Este archivo describía en qué punto quedó la construcción del engine en julio de
> 2026. Esa construcción **terminó**, así que el contenido anterior había quedado
> falso de arriba abajo (decía que `packet/builder.py` no existía —tiene 1139
> líneas—, que había 160 tests —hay 2006—, y apuntaba a un `.superpowers/sdd/`
> que no existe). Reescrito con el estado real.

## Qué es esto

Dos capas sobre la metodología de Victor (`Cerebro/`, v2.0.0, build 2026-07-14):

| Capa | Qué hace |
|---|---|
| `engine/wbj/` | Motor **determinista** en Python. Calcula las 6 categorías, gates, overrides y niveles. Sin LLM. |
| `vertex_api.py` + `vertex_fund_os_platform.html` | Web app (FastAPI + una sola página). Llama al engine y usa el LLM **solo para explicar en palabras**, nunca para puntuar. |

El `Cerebro/` está verificado íntegro: 83 archivos, SHA-256 coinciden con su
`MANIFEST.md`.

### El Dashboard (pestaña de arranque)

Lo primero que se ve al entrar. Es el mapa del mercado **antes** de elegir un
ticker, y baja por tres pisos: `Dashboard › XLK › SMH › NVDA`.

| Bloque | Qué contesta |
|---|---|
| Franja de estado (arriba del todo) | Una frase de alguien que se jugó dinero de verdad, cambiando sola; el VIX; y en la esquina derecha, fija: ¿está abierta la sesión y de cuándo es este dato? |
| Rotación sectorial (plegable) | Debajo de las casillas y cerrada por defecto. Dentro: la salud del mercado, **hacia dónde va cada sector** en cuatro capas —Liderando, Cogiendo fuerza, Agotándose, Rezagados—, el flujo de capital, el diagnóstico y la dispersión. |
| Parrilla | Precio, cambio en la ventana elegida, RSI, media de 200, **volumen relativo** y **amplitud interna**. El precio se refresca **en vivo cada 15 s** con la pestaña visible; RSI, media de 200 y volumen son diarios y no. |
| Resultados de ganancias (izquierda) | Las empresas **grandes** (más de $10.000 M de capitalización) que reportan en los próximos 14 días, agrupadas por día y de mayor a menor dentro de cada uno. El umbral y cuántas quedaron fuera se pintan en el pie; si el tamaño no se puede medir, salen todas y se dice por qué. La caja mide lo suyo: no se estira para igualar a la de al lado. |
| Macroeconómico (derecha) | Lo que **ya salió** con sus tres columnas —salió · esperado · anterior—, un botón «Explícame qué está pasando» que traduce la sorpresa a lo que significa para la economía, para la gente y para cada uno de los once sectores, y debajo **los próximos datos**. |
| Lectura | El modelo cuenta en palabras lo que el motor ya decidió. No puntúa nada. |

La franja va **encima del título** a propósito: es el estado del mercado, y
todo lo de abajo se lee en función de él. Un sector que sube un 2% no significa
lo mismo con el índice en verde que cayendo.

La ventana (7D, 1M, 3M, 6M, 1A) manda en los tres pisos a la vez: el porcentaje
que se lee, el reparto de fuerza que se cuenta y el volumen que se compara
hablan siempre del mismo periodo.

Todo lo que se calcula vive en `engine/wbj/sectores.py` — funciones puras, sin
I/O y sin LLM. `vertex_api.py` baja los datos y `vertex_fund_os_platform.html`
los pinta. La tabla de industrias vive **solo** en el panel (el servidor cotiza
los tickers que le pidan), y los umbrales viven **solo** en el motor: el panel
pinta veredictos, no los recalcula.

### Proyecciones: dos lecturas de la misma cadena

El motor de Víctor calcula **gamma de dealer** (`γ × OI × 100 × spot² × 0,01`)
sobre los strikes a ±20% del spot, y sus horizontes son 10/20/30 días. Encima
va **Drift** (`engine/wbj/tito/drift.py`, port de su `drift-sentiment-agent`),
que cuenta **interés abierto y nocional** por vencimiento mensual — hechos
publicados, sin modelo.

| Plazo | Muro de calls, muro de puts e imán |
|---|---|
| 10 / 20 / 30 días | **los dos**, lado a lado: `Muro de calls / Drift → $310 / $400` |
| 90 / 120 / 320 días | **solo Drift** — el motor no llega hasta ahí |

Apuntan a strikes distintos a propósito: la gamma se apaga lejos del dinero y
el recuento de contratos no. Drift **no puntúa**: el score de 6 sub-agentes se
calcula exactamente igual que antes de que existiera, y hay un test que lo
compara con Drift encendido y apagado.

## Cómo se corre

```bash
# Web app (local)
python -m uvicorn vertex_api:app --port 8000     # o ./start.sh / start.bat

# CLI del engine
cd engine && pip install -e ".[dev]"
wbj analyze NVDA      # análisis completo   ·  wbj quick / scorecard / report
wbj track             # actualiza Memoria/calibracion.md con el track record
```

Comandos disponibles: `entradas`, `fetch`, `packet`, `compute`, `analyze`,
`scorecard`, `track`, `screen`, `judgments`, `aggregate`, `report`.

## Tests

```bash
cd engine && python -m pytest tests/ -q    # 3439 pasan, 0 skips
python -m pytest tests_vertex/ -q          # 1031 pasan, 0 skips
                                           # (903 de la capa web + 128 en un
                                           #  navegador real, cuatro tamaños)

# Auditoría del tab de Proyecciones (324 checks con TITO_ROOT). Con TITO_ROOT usa tu clon de
# su repo en vez de GitHub, y así cubre también su web/app/api.
TITO_ROOT=/ruta/a/agente-tito-metralleta python engine/scripts/auditar_tito.py

# Las dos cajas del Dashboard contra las APIs DE VERDAD. Ningún test lo cubre:
# el contenedor de desarrollo bloquea FMP y FRED con 403, así que la forma real
# de la respuesta solo se comprueba con las claves puestas. No imprime ninguna.
FMP_API_KEY=... FRED_API_KEY=... python engine/scripts/preflight_calendario.py

# Los 16 diferenciales: ejecutan SU TypeScript y comparan contra el port.
for d in engine/scripts/diff_*.sh; do TITO_ROOT=/ruta/a/su/repo "$d"; done
```

**Cero skips no es casualidad: está forzado.** `engine/tests/_saltos.py` hace
que un test que se salta a sí mismo tumbe la corrida, salvo que el motivo sea
que falta `node` o `git`. Un test que no corre no protege nada, y dos veces
tapó un fallo real (`AUDITORIA.md §41.27`).

## Variables de entorno

Local en `vertex.env`; en Render, por el dashboard. Ver `DEPLOYMENT.md §4` para
la tabla completa. Las que **no pueden faltar** en un despliegue público:

- `VERTEX_API_TOKEN` — clave de acceso. **Sin ella el servicio solo atiende a
  `localhost`**, así que en Render quedaría inaccesible. Es deliberado: el fallo
  por omisión debe ser cerrado, no abierto.
- `VERTEX_ORIGIN` — la URL pública, para CORS.
- `EDGAR_USER_AGENT` — identidad ante la SEC (tu nombre + email real).
- `VERTEX_DB_KEY` — cifra el `access_token` de Plaid guardado en la base.

## Auditoría en curso

`AUDITORIA.md` tiene los 26 hallazgos de la auditoría inicial con su
diagnóstico y solución, y a partir de §41 las rondas posteriores sobre el tab de
Proyecciones. Cada arreglo va en su propio commit; `git log` es el historial
real.

**Los 26 están cerrados**, los 4 críticos incluidos. Este bloque decía
"Siguiente: A-02" mucho después de que A-02 se resolviera: apuntaba a un fallo
que ya no existía y, peor, daba a entender que los otros seguían abiertos.

## Notas

- La identidad de git está configurada **local a este repo**
  (`Kevin Taboas <kevintaboas02@gmail.com>`). No hace falta tocar `--global`.
- El remoto es `origin` → `github.com/kevintaboas18/vertex_fund_os`. Que siga
  siendo **privado**: el código maneja credenciales de Plaid. La rama `datos`
  del mismo repo es el almacén (huérfana, no dispara despliegues) — ver
  `vertex_almacen.py` y `AUDITORIA.md §41.25`.
- Si alguna vez ves ramas `rescate/…` en el repo, **no son basura y no se
  borran a mano**: son trabajo que `datos` no aceptó y que quedó aparcado ahí.
  El siguiente arranque del servidor lo recoge y borra la rama solo
  (`AUDITORIA.md §41.73`).
- `docs/archive/` guarda los planes de diseño de la construcción del engine —
  histórico, ya implementado. No son instrucciones vigentes.
