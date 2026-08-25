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

Esos tres plazos largos salen también como **horizonte en los targets**: la
matemática es la del agente sin tocar —mismo cono, mismas seis puntuaciones—
y lo único distinto son los tres niveles, que ahí son los de Drift.

Apuntan a strikes distintos a propósito: la gamma se apaga lejos del dinero y
el recuento de contratos no. Los muros de Drift son los de **él**, literales:
el strike con más interés abierto de cada lado. Que la resistencia salga por
debajo del soporte no es un fallo — es su condición de ruptura.
`engine/scripts/diff_drift.sh` ejecuta su Python y lo comprueba número a
número.

Lo único que Vertex añade es que el **imán se acota al rango de los dos
muros**. Él lo busca en toda la cadena del vencimiento; aquí se acota porque
su propia §6 dice «el precio gravita hacia el Magneto», y un imán fuera de la
banda de los muros rompe esa frase. Va declarado en `diff_drift.sh` y apagado
por defecto en el motor. Drift **no puntúa**: el score de 6 sub-agentes se
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
cd engine && python -m pytest tests/ -q    # 3466 pasan, 0 skips
python -m pytest tests_vertex/ -q          # 1076 pasan, 0 skips
                                           # (932 de la capa web + 144 en un
                                           #  navegador real, cuatro tamaños)

# Auditoría del tab de Proyecciones (324 checks con TITO_ROOT). Con TITO_ROOT usa tu clon de
# su repo en vez de GitHub, y así cubre también su web/app/api.
TITO_ROOT=/ruta/a/agente-tito-metralleta python engine/scripts/auditar_tito.py

# Las dos cajas del Dashboard contra las APIs DE VERDAD. Ningún test lo cubre:
# el contenedor de desarrollo bloquea FMP y FRED con 403, así que la forma real
# de la respuesta solo se comprueba con las claves puestas. No imprime ninguna.
FMP_API_KEY=... FRED_API_KEY=... python engine/scripts/preflight_calendario.py

# Los 17 diferenciales: ejecutan SU código y comparan contra el port.
for d in engine/scripts/diff_*.sh; do TITO_ROOT=/ruta/a/su/repo "$d"; done

# El 17.º es contra su OTRO repo (drift-sentiment-agent). Sin DRIFT_ROOT se
# clona solo; con él usa tu clon.
DRIFT_ROOT=/ruta/a/drift-sentiment-agent engine/scripts/diff_drift.sh
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
- `MASSIVE_API_KEY` — la cadena de opciones y las barras diarias. Sin ella el
  tab de **Proyecciones** no tiene nada que calcular.
- `MARKETSNACK_COOKIE` — la cinta. **No es una API key: es una cookie de sesión
  y caduca sola.** Cuando caduca, cuatro de los seis sub-agentes del agente de
  opciones (Agresividad, Convicción, Inusualidad, Contexto IV) salen en
  *pendiente* y el panel avisa «N de 6 sub-agentes con dato». Se re-pega a mano
  desde el navegador: DevTools → Network → `/api/flow_feed` → header `Cookie`.
  Las dos salen en la píldora de salud de la barra superior, con su estado
  real sacado de la última consulta (`AUDITORIA.md`, «2 de 6 sub-agentes»).

## El área de Portafolio

Una sola fuente de verdad: **el snapshot** (Plaid si hay token, si no lo que
cargaste en `/api/portfolio/import`). Ocho sub-pestañas — Holdings, Riesgo,
Stress Test, What-If, Atribución, Guardrails, Optimizador, Opciones — más
**Drift**, que es el puente con el agente de opciones.

Lo que conecta con los agentes:

| Puente | Qué lleva |
|---|---|
| `get_agent_views` → Black-Litterman | `upside_pct` y `conviction` del último reporte de cada ticker entran como *vistas* del optimizador |
| `/api/portfolio-edge` | El libro cruzado con tu track record por ticker |
| `/api/portfolio-drift` | Los tres niveles de Drift **solo** a 90/120/320 días, por contrato |

Reglas que no se negocian aquí:

- **La exposición de opciones NO es la prima.** Entra como delta-equivalente
  (`delta × contratos × 100 × spot`) en el motor de riesgo, y el «Valor Total
  del Portafolio» no la cuenta. Ver `_posiciones_con_opciones`.
- **Sin dato de mercado, las griegas salen nulas.** No en cero: un cero es una
  afirmación, y lo que pasa es que no se sabe.
- **Los umbrales salen del perfil**, y cada regla dice si el suyo lo
  contestaste tú (`origen: perfil`) o es heredado.
- **Drift no puntúa.** Es contexto de posicionamiento. En cuanto publique un
  score, el portafolio pasa a ser un tercer agente que nadie auditó.

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
