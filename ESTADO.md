# Estado real del agente — medido, no estimado

**Fecha:** 2026-07-30 · Todo lo de aquí sale de ejecutar el sistema, no de leerlo.

---

## Respuesta corta

**El código está al 100%. El agente NO.**

No hay **ninguna clave de API configurada** — ni `vertex.env`, ni `API/.env`, ni
variables de entorno. Sin ellas, **3 de las 6 categorías no puntúan**: el
análisis corre al **50% de su superficie de evidencia**.

Eso no es un fallo del código: es el sistema comportándose como debe. La regla
innegociable dice *"sin evidencia, no hay número"*, así que en vez de inventar,
marca `NOT_SCORABLE`. Pero significa que **hoy no estás usando media metodología**.

---

## 1. Cobertura medida — análisis real de NVDA, sin claves

```
Puntos de evidencia: 50 / 100  (50%)
```

| Categoría | Peso | Estado hoy | Qué le falta |
|---|---:|---|---|
| Business | 20 | ✅ **15.0 pts** · cobertura 100% | — (EDGAR basta) |
| Financial | 15 | ✅ **14.9 pts** · cobertura 100% | — (EDGAR basta) |
| Risk & Resilience | 15 | ✅ **14.9 pts** · cobertura 100% | — (EDGAR basta) |
| **Market & Growth** | 20 | ❌ **NOT_SCORABLE** · cobertura 0% | FMP: consenso, revisiones, pares |
| **Technical & Momentum** | 20 | ❌ **NOT_SCORABLE** · cobertura 0% | FMP: OHLCV ajustado, benchmark |
| **Valuation** | 10 | ❌ **NOT_SCORABLE** · cobertura 0% | FMP: precio, pares, estimados |

**50 de los 100 puntos dependen de una sola clave: `FMP_API_KEY`.**

El score global que sale (9.0/10) se calcula **solo sobre lo que sí puntuó** —
correcto según la metodología, pero es un 9 sobre media evaluación. Un reporte
honesto tiene que decirlo, y el sistema lo dice en su `disclaimer`.

---

## 2. Fuentes de datos — probadas en vivo

| Fuente | Clave | Estado | Prueba |
|---|---|---|---|
| **SEC EDGAR** | No necesita | ✅ **FUNCIONA** | `cik_for('NVDA')` → 1045810 · 626 tags XBRL |
| **FMP** | `FMP_API_KEY` | ❌ Sin clave | `profile('NVDA')` → `None` |
| **FinnHub** | `FINNHUB_API_KEY` | ❌ Sin clave | `available = False` |
| **FRED** | `FRED_API_KEY` | ❌ Sin clave | `available = False` |
| yfinance | No necesita | ⚠️ Sin verificar | Respaldo de precio; rate-limita seguido |

> EDGAR sale con tu identidad: `Vertex Fund OS - Kevin Taboas
> kevintaboas02@gmail.com`. Verificado con una petición real, sin 403.

## 3. APIs de modelos

| API | Variable | Estado | Qué se pierde sin ella |
|---|---|---|---|
| Gemini | `GEMINI_API_KEY` | ❌ Ausente | **La explicación en palabras.** El scorecard numérico sigue |
| Anthropic | `ANTHROPIC_API_KEY` | ❌ Ausente | El *judge*: moat, catalizadores, thesis-killers, tier de TAM → N/S por diseño |
| OpenAI | `OPENAI_API_KEY` | ❌ Ausente | Respaldo del LLM |
| xAI / Grok | `XAI_API_KEY` | ❌ Ausente | Respaldo del LLM |
| QuantData | `QUANTDATA_API_KEY` | ❌ Ausente | Flujo de opciones, dark pool, GEX |
| Plaid | `PLAID_CLIENT_ID` + `_SECRET` | ❌ Ausente | Portafolio en vivo (el import manual sigue funcionando) |

## 4. Acceso a la app

| Variable | Estado | Consecuencia |
|---|---|---|
| `VERTEX_API_TOKEN` | ❌ Ausente | En local funciona; **en Render el servicio no respondería a nadie** |
| `VERTEX_ORIGIN` | ❌ Ausente | CORS solo aceptaría localhost |
| `VERTEX_DB_KEY` | ❌ Ausente | El token de Plaid se guardaría **sin cifrar** |

---

## 5. Agentes — los 7 completos y cableados

| Sub-agente | Peso | Definición | Carpeta del Cerebro | Módulo del engine |
|---|---:|---|---|---|
| `business-analysis` | 20 | ✅ | ✅ `01_business_analysis/` | ✅ `business.py` |
| `financial-analysis` | 15 | ✅ | ✅ `02_financial_analysis/` | ✅ `financial.py` |
| `market-analysis` | 20 | ✅ | ✅ `03_market_analysis/` | ✅ `market.py` |
| `technical-momentum` | 20 | ✅ | ✅ `04_technical_momentum/` | ✅ `technical.py` |
| `risk-analysis` | 15 | ✅ | ✅ `05_risk_analysis/` | ✅ `risk.py` |
| `valuation-analysis` | 10 | ✅ | ✅ `06_valuation_analysis/` | ✅ `valuation.py` |
| `visual-report` | — | ✅ | reglas de visualización | ✅ `report/charts.py` |

**20+15+20+20+15+10 = 100.** Cada agente tiene definición, metodología y código.
El orquestador (`CLAUDE.md`) los lanza con el tool `Agent`.

## 6. Cableado — verificado

| Conexión | Estado |
|---|---|
| Web → engine (`_engine_scorecard`) | ✅ |
| Engine → gates/overrides (`_wbj_gates`) | ✅ los 7 overrides en ambos caminos |
| Plaid / import → snapshot → suite de portafolio | ✅ 7 endpoints a 200 sin Plaid |
| Perfil → `risk.py` → reporte | ✅ lee `Kevin.md`: $1.000, 1–3 años, 20–30% |
| Memoria → calibración | ⚠️ cableado, **sin datos** (0 predicciones) |
| 67 rutas de API | ✅ todas responden |

---

## 7. Lo que falta para llegar al 100%

### 🔴 Bloqueante — sin esto el agente está a medias

- [ ] **`FMP_API_KEY`** — desbloquea **50 de los 100 puntos** (Market, Technical, Valuation).
      Es, con diferencia, lo más importante de esta lista.
- [ ] **`GEMINI_API_KEY`** — sin ella no hay explicación en palabras, solo números.

### 🟠 Antes de exponer la app en Render

- [ ] **`VERTEX_API_TOKEN`** — `openssl rand -hex 24`. Sin ella el servicio
      **no responde a nadie** (deliberado: el fallo por omisión es cerrado).
- [ ] **`VERTEX_ORIGIN`** — la URL pública, para CORS.
- [ ] **`VERTEX_DB_KEY`** — cifra el token de Plaid en la base.
- [ ] **Rotar el `SNAPTRADE_USER_SECRET`** en su portal — estuvo expuesto por un
      endpoint público antes de que se eliminara la integración.

### 🟡 Mejoran la cobertura

- [ ] **`ANTHROPIC_API_KEY`** — activa el *judge*: moat, catalizadores,
      thesis-killers y tier de TAM dejan de ser N/S.
- [ ] **`FINNHUB_API_KEY`** / **`FRED_API_KEY`** — datos de mercado y macro.
- [ ] **`QUANTDATA_API_KEY`** — flujo de opciones, dark pool, GEX.
- [ ] **`PLAID_CLIENT_ID`** + **`PLAID_SECRET`** — portafolio en vivo.

### 🔵 Operación

- [ ] **Subir de plan en Render** — el `free` no tiene disco: cada redeploy
      borra reportes, snapshots y **las predicciones del track record**.
- [ ] **Remoto git privado** — hoy no hay remoto; el código toca credenciales.
- [ ] **Correr `wbj track` mensualmente** — hoy `calibracion.md` está vacío
      porque no hay ninguna predicción con horizonte cumplido.

---

## 8. Cómo pasar de 50% a 100%

```bash
# 1. Crear el archivo de claves (está gitignoreado)
cp API/.env.example vertex.env

# 2. Editarlo con, como mínimo:
#    FMP_API_KEY=...        <- desbloquea los 50 puntos que faltan
#    GEMINI_API_KEY=...     <- explicación en palabras
#    EDGAR_USER_AGENT=Vertex Fund OS - Kevin Taboas kevintaboas02@gmail.com

# 3. Comprobar la cobertura real
python -c "
import sys; sys.path.insert(0,'engine')
from wbj.cli import _build_packet
from wbj.quick import quick_scorecard
s=quick_scorecard(_build_packet('NVDA'))
print(f\"cobertura: {s['evidence_points_covered']}/{s['evidence_points_total']}\")
for c in s['categories']:
    print(' ', c['label'], c['points'], 'cov', c['coverage'])
"
```

Si tras poner `FMP_API_KEY` la cobertura no sube de 50, el problema es el
**plan** de FMP, no la clave: los endpoints de pares, estimados y OHLCV
histórico requieren plan de pago y devuelven "Restricted Endpoint".

---

## 9. Lo que sí está al 100%

| | |
|---|---|
| Metodología (`Cerebro/`) | 83/83 archivos, SHA-256 **idénticos a los de Victor** |
| Pesos y gates | 20+15+20+20+15+10 = 100, idénticos en los 3 sitios |
| Los 7 overrides obligatorios | En engine **y** en la web |
| Tests del engine | **1959 pasan**, 1 skip documentado |
| Tests de la web | **49 pasan** |
| Hallazgos de auditoría | **29 cerrados, 0 abiertos** |
| Reglas de visualización | Forzadas en código, con tests |
| Seguridad | Auth, CORS, custodia del token de Plaid, identidad SEC |
| `main.py` | Corre — 88 documentos indexados |

**El sistema está construido y es correcto. Le falta combustible, no motor.**
