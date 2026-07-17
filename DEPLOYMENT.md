# Guía de Despliegue — Vertex Fund OS (Warren Buffett Jr)

Cómo correr el sistema en tu máquina y publicarlo en Render. El flujo es:

> **EDGAR + FMP + OHLCV (5 años) → engine determinista de Victor calcula las 6 categorías → gates de Victor → el LLM (Gemini) solo explica en palabras.**

---

## 0. Requisitos
- **Python 3.11+** y **git**.
- Claves de API (ver §4). EDGAR es gratis (solo pide un email como User-Agent).

---

## 1. Correr en tu máquina (local)

```bash
# 1. Clona tu repo y entra a la carpeta
git clone https://github.com/kevintaboas18/vertex_fund_os.git
cd vertex_fund_os

# 2. Entorno virtual
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Dependencias
pip install -r requirements.txt

# 4. Crea el archivo de claves (NO se sube a git — está en .gitignore)
#    Copia el ejemplo y rellena tus valores:
cp API/.env.example vertex.env      # luego edita vertex.env con tus keys
#    (o crea vertex.env a mano con el formato de §4)

# 5. Arranca el servidor
uvicorn vertex_api:app --host 0.0.0.0 --port 8000 --reload

# 6. Abre en el navegador:
#    http://localhost:8000
```

Listo: busca un ticker (ej. `AAPL`), pulsa **"Correr análisis WBJ"** y verás el
scorecard determinista + la explicación en palabras.

> La interfaz anterior (7 señales) queda en `http://localhost:8000/legacy`.

### (Opcional) Instalar el engine para el CLI `wbj`
```bash
cd engine
pip install -e ".[dev]"
wbj analyze AAPL        # análisis por línea de comandos
wbj scorecard AAPL      # scorecard 1-10
cd ..
```
El engine lee sus claves de `API/.env` (FMP/FinnHub/FRED + EDGAR_USER_AGENT).

---

## 2. Desplegar en Render (recomendado)

### Opción A — Blueprint automático (con `render.yaml`)
1. Sube tu rama a GitHub (ya incluye `render.yaml`, `Procfile`, `runtime.txt`).
2. En Render: **New → Blueprint** → conecta tu repo → Render lee `render.yaml`.
3. Render te pedirá el valor de cada clave marcada `sync: false` (§4). Pégalas
   ahí (Render las guarda cifradas; **nunca** van al código).
4. **Create** → Render instala, arranca y te da una URL pública.

### Opción B — Servicio Web manual
1. Render: **New → Web Service** → conecta tu repo.
2. Configura:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn vertex_api:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path:** `/`
3. En **Environment → Environment Variables**, agrega todas las claves de §4.
4. **Create Web Service**.

> ⚠️ El **Start Command es obligatorio** con `--host 0.0.0.0 --port $PORT`.
> La app no fija el puerto sola; Render asigna `$PORT` y hay que pasárselo.

---

## 3. Otras plataformas
- **Railway / Fly.io / Heroku:** usan el `Procfile` incluido:
  `web: uvicorn vertex_api:app --host 0.0.0.0 --port ${PORT:-8000}`.
- **VPS propio (systemd):** corre el mismo comando de uvicorn detrás de nginx.
  Pon las claves en `vertex.env` en la raíz del proyecto.

---

## 4. Variables de entorno (claves)

La app las lee de `vertex.env` (local) o de las Environment Variables (Render).
**Nunca subas `vertex.env` a git** (ya está en `.gitignore`).

| Variable | Para qué | ¿Obligatoria? |
|---|---|---|
| `EDGAR_USER_AGENT` | Identidad ante la SEC (tu nombre + email) | Sí (EDGAR la exige) |
| `GEMINI_API_KEY` | Explicación en palabras (LLM principal) | Sí, para la explicación |
| `FMP_API_KEY` | Pares, gaps de earnings, revisiones | Recomendada |
| `OPENAI_API_KEY` | Respaldo del LLM | Opcional |
| `XAI_API_KEY` | Respaldo del LLM (Grok) | Opcional |
| `FINNHUB_API_KEY` | Datos de mercado adicionales | Opcional |
| `FRED_API_KEY` | Datos macro | Opcional |
| `QUANTDATA_API_KEY` | Flujo de opciones / dark pool | Opcional |
| `PLAID_CLIENT_ID` / `PLAID_SECRET` / `PLAID_ENV` | Conexión bancaria | Solo si usas Plaid |
| `SCHWAB_APP_KEY` / `SCHWAB_APP_SECRET` | Conexión con Schwab | Solo si usas Schwab |
| `SNAPTRADE_*` | Conexión con brokers | Solo si usas SnapTrade |

**Formato de `vertex.env`** (una por línea, sin comillas):
```
EDGAR_USER_AGENT=Vertex Fund OS - Kevin Taboas kevintaboas02@gmail.com
GEMINI_API_KEY=tu_key
FMP_API_KEY=tu_key
# ...las demás que uses
```

- Sin `GEMINI_API_KEY`: el scorecard determinista igual funciona, pero no hay
  explicación en palabras (la app lo indica con un aviso).
- Sin `FMP_API_KEY`: TAM/pares/gaps/revisiones quedan NOT_SCORABLE (honesto).
- EDGAR funciona sin key, solo con `EDGAR_USER_AGENT`.

---

## 5. Verificar que quedó bien

Con el servidor corriendo (local o la URL de Render):

```bash
# Frontend (debe devolver 200 y decir "WARREN BUFFETT JR")
curl -s https://TU-URL/ | grep -o "WARREN BUFFETT JR" | head -1

# Precio rápido
curl -s "https://TU-URL/api/quote?ticker=AAPL"

# Salud de las fuentes de datos
curl -s "https://TU-URL/api/data-health"

# Análisis completo (tarda ~20-40s: EDGAR + FMP + Gemini)
curl -s "https://TU-URL/api/analyze?ticker=AAPL"
```

En el navegador, abre la URL, busca `AAPL` y corre el análisis: deberías ver el
scorecard con **"Fuente de los scores: engine determinista (metodología de Victor)"**.

---

## 6. Problemas comunes (troubleshooting)

| Síntoma | Causa | Solución |
|---|---|---|
| EDGAR `403 Forbidden` | Falta/incorrecto el User-Agent | Define `EDGAR_USER_AGENT` con tu email real |
| Scores dicen "estimación LLM (fallback)" | El engine no pudo (sin red a EDGAR, o deps faltantes) | Revisa que `scipy/httpx/typer/pandas` estén instalados y haya red a `data.sec.gov` |
| FMP `401/403` | Key inválida o plan sin ese endpoint | Verifica `FMP_API_KEY`; pares/gaps requieren plan con esos datos |
| No aparece la explicación | Falta `GEMINI_API_KEY` o cuota agotada | Configura la key; el análisis numérico sigue válido |
| "Application failed to respond" en Render | Falta `--host 0.0.0.0 --port $PORT` | Corrige el Start Command |
| Timeout / lento | Primer análisis baja 5 años de OHLCV + EDGAR | Normal (~20-40s la 1ª vez); luego cachea |

---

## 7. Seguridad (importante)
- **Nunca** subas `vertex.env` ni `API/.env` a git (ya están en `.gitignore`).
- Trata `SCHWAB_*`, `PLAID_*` y `SNAPTRADE_*` como **acceso a dinero real**.
- No reutilices contraseñas personales como secretos de API.
- Si una clave se expuso, **rótala** en el portal del proveedor.
- El output del sistema es **clasificación de research**, no una orden de
  compra/venta. Toda ejecución es manual y tuya.
