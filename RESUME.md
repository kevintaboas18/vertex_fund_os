# Estado del proyecto — Vertex Fund OS

**Actualizado:** 2026-07-30 · **Rama:** `main` · **Estado:** engine completo; auditoría en curso.

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
cd engine && python -m pytest tests/ -q    # 1959 pasan, 1 skip (documentado)
python -m pytest tests_vertex/ -q          # 47 pasan
```

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

`AUDITORIA.md` tiene los 26 hallazgos con su diagnóstico y solución. Cada
arreglo va en su propio commit; `git log` es el historial real.

**Cerrados:** C-01 a C-04 (los 4 críticos), A-01, A-07, M-07.
**Siguiente:** A-02 — dos funciones llaman `load_settings()` sin inyectar
`FMP_API_KEY` desde el entorno, así que en Render devuelven vacío en silencio y
los items obligatorios 4 y 5 del reporte salen en blanco.

## Notas

- La identidad de git está configurada **local a este repo**
  (`Kevin Taboas <kevintaboas02@gmail.com>`). No hace falta tocar `--global`.
- No hay remoto configurado. Si lo añades, que sea **privado**: el código maneja
  credenciales de Plaid.
- `docs/archive/` guarda los planes de diseño de la construcción del engine —
  histórico, ya implementado. No son instrucciones vigentes.
