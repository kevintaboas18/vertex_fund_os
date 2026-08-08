# Estado del proyecto — Vertex Fund OS

**Actualizado:** 2026-08-08 · **Rama:** `main` · **Estado:** engine completo; los 26
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
cd engine && python -m pytest tests/ -q    # 2868 pasan, 0 skips
python -m pytest tests_vertex/ -q          # 507 pasan, 0 skips

# Auditoría del tab de Proyecciones (303 checks). Con TITO_ROOT usa tu clon de
# su repo en vez de GitHub, y así cubre también su web/app/api.
TITO_ROOT=/ruta/a/agente-tito-metralleta python engine/scripts/auditar_tito.py

# Los 14 diferenciales: ejecutan SU TypeScript y comparan contra el port.
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
- `docs/archive/` guarda los planes de diseño de la construcción del engine —
  histórico, ya implementado. No son instrucciones vigentes.
