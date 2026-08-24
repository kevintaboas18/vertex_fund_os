# Vertex Fund OS

Sistema multi-agente de análisis de inversiones sobre la metodología **Ruta 2030
Wall Street Agent System v2.0.0**. Seis especialistas evalúan una acción desde
ángulos independientes y aportan puntos a un reporte auditable de 100.

> **Sin evidencia, no hay número. Sin número, no hay score. Sin fórmula, no hay
> conclusión.**
>
> Cuando falta el dato, el sistema devuelve `NOT_SCORABLE` — no rellena el hueco
> con narrativa confiada. Un score alto con evidencia vieja o escasa lleva
> confianza baja: score y confianza son cosas separadas.

Basado en [warren-buffett-jr](https://github.com/infusionvictor/warren-buffett-jr)
de Victor Gonzalez. La metodología (`Cerebro/`) está intacta y verificada:
83 archivos, SHA-256 coincidentes con su `MANIFEST.md`.

---

## Las dos capas

| Capa | Qué hace |
|---|---|
| **`engine/wbj/`** | Motor **determinista** en Python. Calcula las 6 categorías, gates, overrides, valuación y niveles. **Sin LLM.** 1959 tests. |
| **`vertex_api.py`** + `vertex_fund_os_platform.html` | API web (FastAPI) e interfaz. Llama al engine y usa el LLM **solo para explicar en palabras** lo que el engine ya decidió. |

El LLM **nunca puntúa**. Si el engine no puede calcular algo, se dice; no se
sustituye por una estimación del modelo.

## Las 6 categorías (100 puntos)

| Especialista | Peso | Qué evalúa |
|---|---:|---|
| Business | 20 | Moat, posición competitiva, management, durabilidad, economía del cliente |
| Market & Growth | 20 | TAM, revisiones, catalizadores, pista de crecimiento, apalancamiento operativo |
| Technical & Momentum | 20 | Tendencia, fuerza relativa, volumen, gaps de earnings, base/breakout, amplitud |
| Financial | 15 | Ingresos, EPS/FCF, márgenes, balance, conversión de caja |
| Risk & Resilience | 15 | Financiamiento, concentración, ejecución, regulatorio, múltiplo, volatilidad |
| Valuation | 10 | Múltiplos ajustados, histórico/pares, yields, escenarios, margen de seguridad |

Sobre los puntos actúan **gates de perfil** (Momentum / Quality / Value) y **7
overrides obligatorios** que pueden capar el resultado por dependencia de
capital, ROIC<WACC, solvencia, riesgo, ruptura de premium, cobertura de datos o
conflicto de fuentes.

## Arrancar

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp API/.env.example vertex.env                       # y pon tus claves
python -m uvicorn vertex_api:app --port 8000
```

Abre `http://localhost:8000`. En Windows también sirve `start.bat`.

### CLI del engine

```bash
cd engine && pip install -e ".[dev]"
wbj analyze NVDA      # análisis completo
wbj track             # actualiza el track record en Memoria/calibracion.md
```

Comandos: `entradas`, `fetch`, `packet`, `compute`, `analyze`, `scorecard`,
`track`, `screen`, `judgments`, `aggregate`, `report`.

### Tests

```bash
cd engine && python -m pytest tests/ -q    # 1959 pasan, 1 skip
python -m pytest tests_vertex/ -q          # 49 pasan
```

## Claves

Local en `vertex.env`; en Render, por el dashboard. Tabla completa en
[`DEPLOYMENT.md`](DEPLOYMENT.md) §4. Las que no pueden faltar en un despliegue
público:

| Variable | Para qué |
|---|---|
| `VERTEX_API_TOKEN` | Clave de acceso. **Sin ella el servicio solo atiende a `localhost`** |
| `VERTEX_ORIGIN` | URL pública, para CORS |
| `EDGAR_USER_AGENT` | Identidad ante la SEC (tu nombre + email real) |
| `VERTEX_DB_KEY` | Cifra el `access_token` de Plaid guardado en la base |
| `GEMINI_API_KEY` | Explicación en palabras |
| `FMP_API_KEY` | Pares, gaps de earnings, revisiones, insiders |

Sin `FMP_API_KEY`, las dimensiones que dependen de ella quedan `NOT_SCORABLE` —
que es lo honesto. EDGAR funciona sin clave, solo con el User-Agent.

## Memoria entre sesiones

El aprendizaje **no es automático**: depende del protocolo de `CLAUDE.md`.

- `Memoria/tesis/<TICKER>.md` — qué se dijo, cuándo, y qué lo invalidaría
- `Memoria/errores.md` — sesgos detectados y correcciones
- `Memoria/calibracion.md` — track record real, generado por `wbj track`
- `Reportes/*/*/prediccion.json` — la semilla; **nunca se edita a mano**

## Límites

El output son **clasificaciones de research**, rangos de valuación de
referencia, niveles de confirmación/invalidación y advertencias de riesgo.

- **No** promete retornos.
- **No** convierte un nivel técnico o de valuación en una orden de compra/venta.
- **Nunca** ejecuta operaciones ni movimientos de dinero: toda ejecución es
  manual y del inversionista.

## Documentos

| Archivo | Qué contiene |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Instrucciones del orquestador — el flujo obligatorio |
| [`AUDITORIA.md`](AUDITORIA.md) | Hallazgos abiertos y resueltos. **Leer antes de tocar código** |
| [`RESUME.md`](RESUME.md) | Estado actual |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Correr en local y desplegar en Render |
| `Cerebro/` | La metodología completa (no modificar sin bump de versión) |
| [`docs/notebooklm-mcp.md`](docs/notebooklm-mcp.md) | MCP de NotebookLM: qué hace, cómo autenticarlo y por qué no puede alimentar un score |
