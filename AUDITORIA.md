# Auditoría completa — Vertex Fund OS / Warren Buffett Jr

**Fecha:** 2026-07-30 · **Auditor:** Claude Opus 5 · **Alcance:** 539 archivos, ~120k líneas, carpeta por carpeta.
**Base comparada:** https://github.com/infusionvictor/warren-buffett-jr (público, 57 commits, build 2026-07-14).

---

## 0. Veredicto general

El **núcleo metodológico de Victor está intacto y verificado**. Lo que falla es la **capa de entrega que añadiste tú** (`vertex_api.py`, despliegue en Render, puentes de datos) — y ahí hay 4 problemas de seguridad que exponen credenciales de acceso a dinero real.

### Lo que se verificó y PASA ✅

| Área | Resultado |
|---|---|
| `Cerebro/` integridad | **83 archivos, SHA-256 coinciden con `MANIFEST.md`** — la base de conocimiento no fue alterada |
| Pesos por categoría | 20+15+20+20+15+10 = **100** consistente en `Cerebro/*/SCORING.md`, `engine/wbj/aggregate/gates.py` y `vertex_api.py:6273` |
| Dimensiones por categoría | Suman su máximo en las 6 (5+4+4+4+3=20; 3×5=15; 5+4+4+4+3=20; 4+4+3+3+3+3=20; 3+3+3+2+2+2=15; 3+2+2+2+1=10) |
| Gates de perfil | Umbrales **idénticos** entre `gates.py:187-243` y `vertex_api.py:6418-6427` (Momentum/Quality/Value) |
| Suite de tests engine | **1959 pasan, 1 skip** (`tests/test_financial_growth_quality_wiring.py:116`, documentado) |
| Suite `tests_vertex` | **47 pasan** |
| Reglas de visualización | Las 4 están **forzadas en código** con tests (`report/charts.py:117-123` rechaza banda sin supuestos; `test_chart_rules.py` cubre las 4) |
| Umbral insiders >$1M (engine) | Correcto: **agrega por insider antes de umbralizar** (`report/__init__.py::_insiders`, `test_report_mandatory_items.py:39`) |
| Model IDs de Claude | `claude-opus-4-8`, `claude-sonnet-5`, `claude-haiku-4-5` — **los tres válidos y activos** |
| Llamadas a Anthropic | Limpias: sin `temperature`/`top_p`/`budget_tokens` (que darían 400 en Opus 4.8) |
| `.gitignore` | Cubre `vertex.env`, `API/.env`, `vertex.db` correctamente |
| `_WBJ_ENGINE_PATH`, `DB_PATH` | Rutas relativas / vía env var — portables |

### Diferencias vs. el repo de Victor

**Tú añadiste** (no existen en el repo de Victor): `vertex_api.py`, `vertex_fund_os_platform.html`, `main.py`, `Entradas/`, `API/`, `assets/`, `tests_vertex/`, `DEPLOYMENT.md`, `Procfile`, `render.yaml`, `requirements.txt`, `runtime.txt`, `start.sh`, `start.bat`, `Perfil Inversionista/Kevin.md`.

**Te falta** de Victor: `README.md` (raíz) y `.github/workflows/`.

---

## 1. CRÍTICO — Seguridad y acceso a dinero real

> Estos 4 aplican a la app desplegada públicamente en Render (`render.yaml`, `healthCheckPath: /`, sin auth).

### [x] C-01 — ✅ RESUELTO (2026-07-30) — SnapTrade eliminado por completo
**Decisión:** en vez de parchear el endpoint, se eliminó toda la integración SnapTrade (ya hay Plaid y se añadirá otra fuente más adelante). Ver §6 "Trabajo aplicado".

<details><summary>Diagnóstico original</summary>

#### `/api/snaptrade/whoami` filtra `user_secret` sin autenticación
- **Dónde:** `vertex_api.py:12153-12160` (y `:12169-12171` en `/api/snaptrade/register`)
- **Por qué falla:** El endpoint es un `GET` público que devuelve `{"user_id": ..., "user_secret": ...}` en texto plano. Cualquiera que conozca tu URL de Render puede hacer `curl https://tu-app/api/snaptrade/whoami` y obtener la credencial que da acceso de lectura a tus tenencias de broker. No hay ningún chequeo previo.
- **Solución aplicada:** eliminación total (§6). **Pendiente para ti: rotar/revocar el `SNAPTRADE_USER_SECRET`** en el portal de SnapTrade — si la app estuvo pública, asume que se filtró; borrar la variable de Render no invalida la credencial.
</details>

### [ ] C-02 — Cero autenticación en toda la API
- **Dónde:** `vertex_api.py` — ~60 endpoints `@app.get`/`@app.post`, ninguno con `Depends()`, `HTTPBearer` ni verificación de token (grep confirmado: 0 resultados)
- **Por qué falla:** `render.yaml` publica el servicio en internet. Todos los endpoints —incluidos `/api/portfolio`, `/api/portfolio-risk`, `/api/plaid/link-token`, `/api/analyze`— son anónimos. Además de la exposición de datos, `/api/analyze` quema tus cuotas de FMP/Gemini/Anthropic: cualquiera puede vaciarte el plan.
- **Solución:** Añadir un middleware de API key simple: una env var `VERTEX_API_TOKEN` y un `Depends` que compare el header `X-Vertex-Token` con `secrets.compare_digest`. Excluir solo `/` y `/manifest.webmanifest`. El frontend lo inyecta desde una cookie `HttpOnly` seteada en un login mínimo.

### [ ] C-03 — `access_token` de Plaid viaja como parámetro de query
- **Dónde:** `vertex_api.py:10673`, `:10707`, `:11634`, `:11648`, `:11662`, `:11952`, `:11966`
- **Por qué falla:** `def get_portfolio(access_token: str, ...)` en un `@app.get` significa que el token va en la URL. Las query strings quedan en: logs de acceso de Render, historial del navegador, headers `Referer` hacia terceros, y cualquier proxy intermedio. Un `access_token` de Plaid da lectura de cuentas bancarias/brokerage.
- **Solución:** Convertir esos 7 endpoints a `@app.post` con el token en el body (`body: dict`), o mejor: guardar el `access_token` server-side (tabla en `vertex.db`, cifrado) tras el `exchange-token`, y que el cliente solo mande un `account_id`. Nunca en la URL.

### [ ] C-04 — CORS abierto con credenciales
- **Dónde:** `vertex_api.py:52-58` — `allow_origins=["*"]` + `allow_credentials=True` + `allow_methods=["*"]`
- **Por qué falla:** Cualquier sitio web puede hacer llamadas autenticadas a tu API desde el navegador de la víctima. La combinación `*` + credenciales es rechazada por navegadores modernos pero deja la API abierta a clientes no-navegador, y una vez que implementes C-02 con cookies esto se vuelve un CSRF directo.
- **Solución:** `allow_origins=[os.environ.get("VERTEX_ORIGIN", "http://localhost:8000")]` con la URL real de Render; `allow_methods=["GET","POST"]`; `allow_headers=["content-type","x-vertex-token"]`.

---

## 2. ALTO — Rompe funcionalidad o contradice `CLAUDE.md`

### [ ] A-01 — `EDGAR_USER_AGENT` está hardcodeado con el email de Victor y la env var se ignora
- **Dónde:** `engine/wbj/providers/edgar.py:38-39` — `EDGAR_USER_AGENT = "warren-buffett-jr victor@infusioninvestments.com"`
- **Por qué falla:** Es una constante de módulo. `wbj/config.py::Settings` **no tiene campo `edgar_user_agent`**, así que:
  - `render.yaml:30` define `EDGAR_USER_AGENT` → **ignorado**
  - `API/.env.example:5` lo define → **ignorado**
  - `DEPLOYMENT.md §6` dice que la solución al 403 de EDGAR es "define `EDGAR_USER_AGENT` con tu email real" → **esa instrucción no hace nada**
  - Todas tus peticiones a la SEC se identifican como Victor. Si la SEC limita ese UA por fair-access, te afecta a ti y a él a la vez.
- **Solución:** Añadir `edgar_user_agent: str | None = None` a `Settings`, leerlo en `load_settings()` desde `env_vars` **y** `os.environ` (como ya se hace con `anthropic_api_key` en `config.py:70-72`), y en `edgar.py` construir los headers desde `self.settings.edgar_user_agent` con el valor actual como último fallback. Actualizar `render.yaml` con tu email.

### [ ] A-02 — Dos items obligatorios del reporte fallan en silencio en Render
- **Dónde:** `vertex_api.py:1274` (`_wbj_insiders_clasificados`) y `:1308` (`_wbj_holders_from_edgar`)
- **Por qué falla:** Ambos llaman `load_settings()` **sin inyectar `FMP_API_KEY` desde `os.environ`** — al contrario de `:235`, `:281`, `:6596` y `:7468`, que sí lo hacen. En Render no existe el archivo `API/.env` (las claves son env vars), así que `settings.fmp_api_key` es `None` → `FMPProvider.available` es `False` → devuelve `None` → los `except Exception: return {}` lo tragan sin log.
  Resultado: **el item 5 de `CLAUDE.md` (insider buying/selling >$1M) y el item 4 (inversionistas institucionales / 13D-G) salen vacíos en producción, sin aviso**. Localmente funcionan porque sí existe `API/.env`.
- **Solución:** Extraer un helper `_settings_con_claves()` que haga `load_settings()` + inyección de `FMP_API_KEY`/`FINNHUB_API_KEY`/`FRED_API_KEY`/`ANTHROPIC_API_KEY` desde `os.environ`, y usarlo en **los 8 call-sites**. Cambiar los `except: return {}` por `except Exception as e: print(...)` para que un fallo sea visible.

### [ ] A-03 — Conflicto de perfil: Kevin vs Victor (capital $1,000 vs $25,000)
- **Dónde:** 4 lugares en desacuerdo:
  - `Perfil Inversionista/Kevin.md` — horizonte **1–3 años**, capital **~$1,000**, dice explícitamente "reemplaza al de Victor Gonzalez"
  - `CLAUDE.md:83` (paso 6) — manda cruzar con `Victor Gonzalez.md`: "horizonte 3–5 años, máx 30–60% por posición, capital $25,000"
  - `engine/wbj/specialists/risk.py:270-279` — `PROFILE` **hardcodea** `capital_usd: 25_000.0`, `horizon_years: (3, 5)`
  - `.claude/agents/risk-analysis.md:19` — hardcodea "capital $25K, máx 30–60% por posición... horizonte 3–5 años"
  - Solo `vertex_api.py:6526-6539` (`_load_investor_profile`) prioriza `Kevin.md` correctamente
- **Por qué falla:** El `profile_fit()` de `risk.py:685-696` reporta rango de posición y horizonte contra los datos de Victor. No altera el score (es output descriptivo), pero **la guía de sizing del reporte es 25× más grande que tu capital real**. Con $1,000 y opciones, "30–60% por posición" son $300–600 por trade — tu propio perfil advierte del riesgo de ruina, y el sistema te dice lo contrario.
- **Solución:** (a) Hacer que `risk.py` **lea** `Perfil Inversionista/*.md` con la misma prioridad que `_load_investor_profile` (Kevin.md primero) en vez de hardcodear, o mínimo cambiar `PROFILE` a los valores de Kevin. (b) Actualizar `CLAUDE.md` paso 6 y la lista de sub-agentes para que apunten a `Kevin.md`. (c) Actualizar `.claude/agents/risk-analysis.md:19`. (d) Decidir si `Victor Gonzalez.md` se archiva o se conserva como referencia histórica.

### [ ] A-04 — Umbral de insiders >$1M mal aplicado en la web (per-transacción, no agregado)
- **Dónde:** `vertex_api.py:6612-6613` — `_val = _sh * _px; if _val <= 1_000_000: continue`
- **Por qué falla:** `CLAUDE.md` item 5 dice "las que **excedan $1M USD en total**". El engine lo hace bien (agrupa por insider y después umbraliza — hay un test explícito, `test_repeated_small_sales_aggregate_past_the_threshold`). La web filtra **cada Form 4 por separado**: un insider que vende 6 veces $300k ($1.8M en total) **desaparece del reporte** porque ninguna transacción individual pasa el millón. Es exactamente el patrón de venta escalonada que más importa detectar.
- **Solución:** Reemplazar el bucle de `_wbj_fmp_important_insiders` por una llamada a `wbj.report._insiders` (ya hace la agregación correcta y ya se importa en `:1274`), o replicar la agrupación: acumular por `reportingName` + dirección, y umbralizar el total.

### [ ] A-05 — 3 de los 7 overrides obligatorios no existen en el camino web
- **Dónde:** `vertex_api.py:6398-6414` vs `engine/wbj/aggregate/overrides.py:89-103`
- **Por qué falla:** El engine implementa los 7 overrides de `SCORING_AND_GATES.md`. `_wbj_gates` implementa solo **4** (2 ROIC<WACC, 4 risk floor, 5 premium breakdown, 6 coverage) más dos flags extra de business. Faltan:
  - **Override 1** (dependencia de capital: pérdida neta + FCF negativo + capital externo → cap en Avoid/Speculative)
  - **Override 3** (cobertura de intereses <1.5x "siempre aparece de forma prominente")
  - **Override 7** (conflicto de datos sin resolver impide publicar valor por acción)

  Los tres aparecen únicamente como **prosa en el prompt del LLM** (`:6497`, `:6506`) — es decir, se le *pide* al modelo que los mencione, pero no hay chequeo determinista. Eso viola la regla innegociable: "sin fórmula, no hay conclusión". Un caso de dependencia de capital puede pasar un gate de perfil en la web y no en el engine.
- **Solución:** `_engine_scorecard` ya recibe los `mandatory_flags` de los especialistas. Leer en `_wbj_gates` los IDs `OVERRIDE_1_CAPITAL_DEPENDENCE`, `OVERRIDE_3_SOLVENCY_WARNING`, `OVERRIDE_7_*` desde `F("financial")`/`F("valuation")` y aplicarlos con la misma semántica que `gates.py:283-345`. Mejor aún: llamar directamente a `wbj.aggregate.gates.apply_gates` y borrar `_wbj_gates` (elimina la duplicación de raíz).

### [ ] A-06 — El volumen de llamadas a FMP revienta el plan free en 2 análisis
- **Dónde:** `engine/wbj/packet/builder.py:850-1003` (~18 llamadas) + `vertex_api.py:7555, 7636, 7650, 7716, 7751, 7759, 7763, 7797, 7984, 8005, 8006`
- **Por qué falla:** Un solo `/api/analyze` dispara:
  - Packet base: ~18 llamadas (perfil, 6 estados financieros, OHLCV, peers, estimados, insiders, 13F, earnings, benchmark, sector)
  - Puentes de overlay: ETF sectorial, cashflow, earnings calendar, OHLCV 6 años, peers ×2
  - **Bucles de pares:** hasta 15 × `profile` + 15 × `income_annual` (P/S), 20 × `ohlcv_daily(2y)` (breadth + RS), y otro bucle de `income_annual` + `balance_annual` para peer ROIC

  Total realista: **80–120 peticiones por ticker**. El plan free de FMP son 250/día. El caché (`_MAX_AGE_OHLCV = 1` día, `_MAX_AGE_REFERENCE = 7`) ayuda en repeticiones, pero el primer análisis de un ticker nuevo consume ~40% de la cuota diaria.
- **Solución:** (a) Recortar los bucles de pares: `[:15]`→`[:8]` (es el `MIN_PEERS` que ya se exige en `:7772`) y `[:20]`→`[:10]`. (b) Compartir una sola llamada `income_annual` por par entre el bridge de P/S y el de peer ROIC (ahora se piden dos veces). (c) Añadir un contador de peticiones al `Provider.get_json` y exponerlo en `/api/data-health` para que veas el consumo real. (d) Subir el `max_age_days` de `ohlcv_daily` de pares a 7 (para breadth/RS no necesitas frescura diaria).

### [x] A-07 — ✅ RESUELTO (2026-07-30) — `.claude/launch.json` apuntaba al Mac de Victor
- **Era:** `/Users/victorgonzalez/Desktop/warren-buffett-jr/engine/.venv/bin/python` — ruta macOS en una máquina Windows; el preview nunca arrancaba.
- **Ahora:** `python -m uvicorn vertex_api:app --host 127.0.0.1 --port 8000`. Verificado: el server levanta y sirve la app.

---

## 3. MEDIO — Inconsistencias, deuda y documentación falsa

### [ ] M-01 — `CLAUDE.md` describe una estructura de carpetas que no existe
- **Dónde:** `CLAUDE.md:18-33`
- **Por qué falla:** El árbol dice `Warrent Buffet Jr/` (con dos typos) y **omite 7 carpetas reales**: `engine/`, `Entradas/`, `Reportes/`, `Memoria/`, `scripts/`, `tests_vertex/`, `assets/`, `docs/`. El orquestador se guía por ese árbol; si no sabe que existe `engine/`, no sabe que hay un motor determinista.
- **Solución:** Regenerar el árbol con la estructura real y corregir el nombre del proyecto a `vertex_fund_os/`.

### [ ] M-02 — Falta `README.md` en la raíz
- **Por qué falla:** Victor lo tiene; tu copia no. Es el único documento que explica el sistema a alguien que llega nuevo (incluido tú en 6 meses).
- **Solución:** Traer el de Victor y adaptarlo: añadir la capa Vertex (web app, despliegue) y el perfil de Kevin.

### [ ] M-03 — Falta `.github/workflows/` → `premarket_email.py` nunca corre
- **Dónde:** `scripts/premarket_email.py:2` dice "corre en GitHub Actions cada mañana de mercado"
- **Por qué falla:** No existe `.github/` en tu copia. El script es código muerto: nadie lo invoca.
- **Solución:** Traer el workflow del repo de Victor, o borrar el script si no lo quieres. Si lo mantienes, ver M-04 primero.

### [ ] M-04 — `premarket_email.py` te manda los correos a Victor
- **Dónde:** `scripts/premarket_email.py:32` — `EMAIL_TO = os.environ.get("EMAIL_TO", "victor@infusioninvestments.com")`
- **Por qué falla:** Sin la env var `EMAIL_TO`, el correo pre-market va a Victor, no a ti. Además `MARKET_HOLIDAYS` (`:39+`) está hardcodeado solo para 2026 — en enero de 2027 el script correrá en días festivos. Y depende de scraping de `stockanalysis.com`, que puede cambiar de HTML sin aviso.
- **Solución:** Cambiar el default a tu email. Añadir los feriados de 2027 o calcularlos. Envolver el parseo en un chequeo de sanidad (si extrae 0 filas, no enviar y loguear).

### [ ] M-05 — `main.py` no ingesta 8 documentos del Cerebro y valida el perfil equivocado
- **Dónde:** `main.py:20-33` (`mapa_arquitectura`) y `:88` (chequeo de perfil)
- **Por qué falla:** Dos bugs:
  1. El mapa lista las subcarpetas de `Cerebro/` pero **no `Cerebro/` en sí**, y usa `glob("*.md")` sin recursión. Resultado: `BUILD_REPORT.md`, `MANIFEST.md`, `QUICK_START.md`, `README.md`, `REFERENCES.md`, `SYSTEM_ARCHITECTURE.md`, `VERSION.md` **nunca se indexan** — incluyendo `QUICK_START.md`, que `CLAUDE.md:37` señala como el punto de partida del packet.
  2. `:88` valida `Perfil Inversionista/Victor Gonzalez.md`. Con el perfil de Kevin activo (A-03), la validación pasa por el archivo incorrecto.
- **Solución:** Añadir `"Cerebro"` al mapa (o cambiar a `rglob("*.md")` sobre `Cerebro/`) y cambiar el chequeo del perfil a "existe al menos un `.md` en `Perfil Inversionista/`, priorizando `Kevin.md`".

### [ ] M-06 — `RESUME.md` está obsoleto y contiene información falsa
- **Dónde:** `RESUME.md` completo
- **Por qué falla:** Dice "paused mid-plan (9.5/25 tasks)", "`engine/wbj/packet/builder.py` does not exist yet" (**existe, 1139 líneas**), "160 tests" (**hay 1959**), y manda leer `.superpowers/sdd/progress.md` que **no existe en el proyecto**. También afirma "FMP key returns 403 on `/api/v3/profile`" — el provider ya migró a `/stable/` (`providers/fmp.py:26`), así que ese diagnóstico ya no aplica. Y `:37` dice `git config --global user.email victor@infusioninvestments.com`.
- **Solución:** Reescribirlo como estado actual, o borrarlo (la información útil ya está en `DEPLOYMENT.md`).

### [ ] M-07 — El proyecto NO es un repositorio git
- **Por qué falla:** No hay `.git/`. Sin control de versiones: no puedes revertir un cambio que rompa el scoring, no hay historial de por qué se ajustó un umbral, y `.gitignore` no protege nada porque no hay git. Toda la disciplina de "cambio de fórmula requiere version bump + backtest note" (`Cerebro/VERSION.md`) es inauditable.
- **Solución:** `git init`, primer commit del estado actual, y **antes de tocar nada** de esta lista. Añadir remote privado. Verificar que `vertex.env`, `API/.env` y `vertex.db` quedan ignorados antes del primer `git add`.

### [ ] M-08 — `_stooq_series` es código muerto que infla el conteo de fuentes
- **Dónde:** `vertex_api.py:295-326`
- **Por qué falla:** El propio docstring lo admite: "HOY INERTE... devuelve [] en la práctica". El sistema dice tener 3 fuentes de historia diaria y tiene 2 (yfinance + FMP).
- **Solución:** Borrar la función y sus call-sites en `_resilient_history`, y ajustar cualquier texto que hable de "tercer respaldo".

### [ ] M-09 — El sistema emite `BUY`/`AVOID`, que `CLAUDE.md` prohíbe
- **Dónde:** `vertex_api.py:6355-6369` (`_WBJ_PROFILE_TO_RECO`) y `:6465-6472`
- **Por qué falla:** `CLAUDE.md` "Límites del sistema" dice: "**No** convierte un nivel técnico o de valuación en una instrucción automática de compra/venta". El mapa traduce cada perfil a `"BUY"`/`"HOLD"`/`"SPECULATIVE"`/`"AVOID"` y lo persiste en la tabla `reports`. Aunque el `classification` en español sí está bien redactado ("Favorable a invertir"), el campo `recommendation` es literalmente una orden.
- **Solución:** Renombrar el campo a `research_class` con valores no-imperativos (`FAVORABLE` / `CONDICIONAL` / `ESPECULATIVO` / `DESFAVORABLE`), y migrar la columna en `vertex.db`. Si necesitas el valor viejo por compatibilidad de histórico, mantenerlo como `legacy_recommendation` y no mostrarlo en UI.

### [ ] M-10 — `judge_model` una generación atrás
- **Dónde:** `engine/wbj/config.py:27` y `:73` — default `claude-opus-4-8`; `vertex_api.py:6769` repite el literal dos veces
- **Por qué falla:** No es un bug (`claude-opus-4-8` es válido y activo), pero `claude-opus-5` es el modelo actual **al mismo precio** ($5/$25 por MTok) y es notablemente mejor en juicio cualitativo — que es exactamente lo que hace el judge (moat, catalizadores, thesis-killers). Nota: en Opus 5 el thinking está **activo por defecto** y comparte el presupuesto de `max_tokens`, así que hay que subir los `max_tokens=4096`/`2048` actuales.
- **Solución:** Cambiar el default a `claude-opus-5` en `config.py:27` y `:73`, subir `max_tokens` a ~8192 en `judge.py:378` y `report/__init__.py:107`, actualizar el literal de `vertex_api.py:6769`, y ajustar `engine/tests/test_judge.py:85` que asserta el ID viejo. Actualizar el comentario de `API/.env.example:13`.

### [ ] M-11 — `Memoria/calibracion.md` no existe pero `CLAUDE.md` manda leerlo
- **Dónde:** `CLAUDE.md:97` (protocolo de memoria, paso 2) vs `ls Memoria/` → solo `MEMORIA.md`, `errores.md`, `tesis/.gitkeep`
- **Por qué falla:** El paso 2 del protocolo obligatorio es "Lee `Memoria/calibracion.md`". El archivo se genera con `wbj track` (`cli.py:399`), que necesita `Reportes/*/*/prediccion.json` — y `Reportes/` está vacío. En una instalación nueva el paso siempre falla. `report/render.py:44-62` sí lo maneja con gracia, pero el orquestador (Claude) no sabe que la ausencia es esperada.
- **Solución:** Crear un `Memoria/calibracion.md` semilla que diga explícitamente "sin predicciones aún; sesgo no medible; no ajustar confianza por este motivo", y añadir esa aclaración al paso 2 de `CLAUDE.md`.

### [ ] M-12 — `render.yaml` incompleto: pierdes la memoria en cada deploy
- **Dónde:** `render.yaml:11` (`plan: free`), `:18-23` (bloque `disk` comentado)
- **Por qué falla:** Con plan free no hay disco persistente: `vertex.db` vive en el filesystem efímero. Cada redeploy o cada despertar tras dormir **borra todos los reportes, snapshots de consenso, historial de señales y predicciones** — es decir, destruye el track record del que depende `wbj track` y toda la calibración. Además faltan `JUDGE_MODEL`, `EXTRACTION_MODEL` y `VERTEX_DB` en `envVars`.
- **Solución:** Si el track record te importa (y el protocolo de memoria dice que sí): subir a `starter`, descomentar el bloque `disk`, y añadir `VERTEX_DB=/var/data/vertex.db`. Añadir `JUDGE_MODEL` y `EXTRACTION_MODEL` con `sync: false`. Mientras sigas en free, documentar en `DEPLOYMENT.md` que la memoria es volátil.

### [ ] M-13 — Dependencias sin fijar y versión de Python inconsistente
- **Dónde:** `requirements.txt` (10 de 14 paquetes sin versión), `runtime.txt` (`python-3.11.9`), `engine/pyproject.toml` (`>=3.11`)
- **Por qué falla:** `fastapi`, `numpy`, `pandas`, `scipy`, `yfinance` sin pin: un `pip install` mañana puede traer una major con breaking changes y romper el scoring en silencio. Además tu entorno local corre **Python 3.14.6** (los `.pyc` son `cpython-314`) mientras Render corre 3.11.9 — dos runtimes distintos para el mismo código numérico. Y pandas 3.0 (instalado localmente) tiene cambios de comportamiento vs pandas 2.x.
- **Solución:** `pip freeze > requirements.lock.txt` y usarlo en el build de Render. Fijar mínimos y máximos en `requirements.txt` (`pandas>=2.2,<3`). Decidir un runtime único (3.11 o 3.12) y usarlo local y en Render.

### [ ] M-14 — Override 2 no bloquea la banda "Elite" en ninguna implementación
- **Dónde:** `Cerebro/00_main_agent/SCORING_AND_GATES.md` (override 2) vs `gates.py:162-175` (`descriptive_band`) y `vertex_api.py:6372-6378` (`_wbj_band`)
- **Por qué falla:** El doc dice "ROIC below WACC prevents `Elite`, `Quality Opportunity`, or `Excellent business`". Ambas implementaciones bloquean correctamente `Quality Opportunity`, pero `descriptive_band`/`_wbj_band` devuelven `"Elite raw score"` con raw ≥ 90 **sin consultar el override**. Una empresa que destruye valor puede aparecer etiquetada "Elite" en el reporte.
- **Solución:** Pasar los overrides activos a la función de banda y degradar `"Elite raw score"` → `"Strong raw score (Elite bloqueado por Override 2)"` cuando esté presente `OVERRIDE_2_ROIC_BELOW_WACC`. Añadir un test en `tests/aggregate/test_gates.py`.

### [ ] M-15 — `docs/superpowers/` con rutas de Victor y planes ya ejecutados
- **Dónde:** `docs/superpowers/plans/2026-07-16-wbj-engine.md:13` — `/Users/victorgonzalez/Desktop/warren-buffett-jr/`
- **Por qué falla:** 4 documentos de diseño (734 + 3 archivos de specs) que describen trabajo ya terminado, con rutas de otra máquina. Si el orquestador los lee como instrucciones vigentes, actúa sobre un plan cerrado.
- **Solución:** Mover a `docs/archive/` con un `README` que diga "histórico, ya implementado", o borrar.

---

## 4. Nota operativa — el entorno no podía correr el engine

Tu Python global **no tenía** `pytest`, `scipy`, `typer`, `matplotlib`, `anthropic` ni `python-dotenv`. Los instalé para poder auditar (por eso pude correr los 2006 tests). Sin ellos, `_engine_scorecard` cae por el `except` de import en `vertex_api.py:7459-7461` y **todo el scorecard degrada al fallback del LLM** — es decir, tendrías números inventados por Gemini en vez de la matemática de Victor, con el aviso "estimación LLM (fallback)" que menciona `DEPLOYMENT.md §6`.

**Acción recomendada antes de todo lo demás:** crear un venv del proyecto y `pip install -r requirements.txt && pip install -e engine[dev]`, para que local y Render corran el mismo camino determinista.

---

## 5. Resumen del checklist

| # | ID | Severidad | Título | Archivo principal |
|---|---|---|---|---|
| 1 | ~~C-01~~ | ✅ Resuelto | SnapTrade eliminado por completo | §6 |
| 2 | C-02 | 🔴 Crítico | Cero autenticación en ~60 endpoints | `vertex_api.py` |
| 3 | C-03 | 🔴 Crítico | `access_token` de Plaid en query string | `vertex_api.py:10673+` |
| 4 | C-04 | 🔴 Crítico | CORS `*` con credenciales | `vertex_api.py:52` |
| 5 | A-01 | 🟠 Alto | `EDGAR_USER_AGENT` hardcodeado, env var ignorada | `edgar.py:38` |
| 6 | A-02 | 🟠 Alto | Items obligatorios 4 y 5 vacíos en Render | `vertex_api.py:1274,1308` |
| 7 | A-03 | 🟠 Alto | Perfil Kevin vs Victor ($1k vs $25k) | `risk.py:270` |
| 8 | A-04 | 🟠 Alto | Insiders >$1M sin agregar por persona | `vertex_api.py:6612` |
| 9 | A-05 | 🟠 Alto | Overrides 1, 3 y 7 ausentes en la web | `vertex_api.py:6398` |
| 10 | A-06 | 🟠 Alto | 80–120 llamadas FMP por análisis (plan: 250/día) | `builder.py` + `vertex_api.py` |
| 11 | ~~A-07~~ | ✅ Resuelto | `launch.json` reapuntado a uvicorn local | §6 |
| 12 | M-01 | 🟡 Medio | `CLAUDE.md` omite 7 carpetas reales | `CLAUDE.md:18` |
| 13 | M-02 | 🟡 Medio | Falta `README.md` raíz | — |
| 14 | M-03 | 🟡 Medio | Falta `.github/workflows/` | — |
| 15 | M-04 | 🟡 Medio | Email pre-market va a Victor | `premarket_email.py:32` |
| 16 | M-05 | 🟡 Medio | `main.py` no indexa 8 docs del Cerebro | `main.py:20` |
| 17 | M-06 | 🟡 Medio | `RESUME.md` con datos falsos | `RESUME.md` |
| 18 | M-07 | 🟡 Medio | No es repositorio git | — |
| 19 | M-08 | 🟡 Medio | `_stooq_series` código muerto | `vertex_api.py:295` |
| 20 | M-09 | 🟡 Medio | Emite `BUY`/`AVOID` (prohibido) | `vertex_api.py:6355` |
| 21 | M-10 | 🟡 Medio | `judge_model` una generación atrás | `config.py:27` |
| 22 | M-11 | 🟡 Medio | `calibracion.md` inexistente | `Memoria/` |
| 23 | M-12 | 🟡 Medio | Render free borra la memoria en cada deploy | `render.yaml:11` |
| 24 | M-13 | 🟡 Medio | Deps sin pin; Python 3.14 local vs 3.11 Render | `requirements.txt` |
| 25 | M-14 | 🟡 Medio | Override 2 no bloquea banda "Elite" | `gates.py:162` |
| 26 | M-15 | 🟡 Medio | `docs/superpowers/` obsoleto | `docs/` |

**Orden de arreglo sugerido:** M-07 (git, para poder revertir) → ~~C-01~~ → C-02 → C-03 → C-04 → A-01 → A-02 → A-03 → A-04 → A-05 → A-06 → ~~A-07~~ → el resto de M.

---

## 6. Trabajo aplicado

### 2026-07-30 · Eliminación de SnapTrade + cableado a Plaid (cierra C-01 y A-07)

**Decisión del usuario:** SnapTrade no es necesario (ya hay Plaid; se añadirá otra fuente más adelante). Se eliminó por completo en vez de parchear el filtrado de credenciales.

**Eliminado (0 referencias restantes en todo el proyecto):**
- `vertex_api.py`: 270 líneas — 4 constantes `SNAPTRADE_*`, `_get_snaptrade`, `_snaptrade_reason`, `_st_body`, `_snaptrade_extract_positions`, `_snaptrade_extract_options` y los 6 endpoints (`/status`, `/whoami`, `/register`, `/login-link`, `/accounts`, `/holdings`).
- `vertex_fund_os_platform.html`: 135 líneas de JS (`connectSnapTrade`, `loadSnapTradeAccounts`, `loadSnapTradeHoldings`, `disconnectSnapTrade`, `stMsg`, `stUid`, `stSecret`) + el panel HTML de 29 líneas + botón de la barra.
- `render.yaml`: 4 env vars `SNAPTRADE_*`. · `DEPLOYMENT.md`: 2 referencias.
- Dependencia externa `snaptrade-python-sdk`: ya no se importa en ningún sitio.

**Cableado para que no quede nada suelto** — el snapshot del servidor (`portfolio_holdings` / `option_holdings`) era la fuente única de todo el suite de portafolio y solo lo escribía SnapTrade. Ahora lo escriben dos caminos:

1. **Plaid (automático).** `/api/portfolio` y `_resolve_positions` persisten el libro en cuanto Plaid responde, así las rutas siguientes funcionan sin volver a pedir el `access_token`. Se añadió `_plaid_extract_options()` — contraparte de `_extract_equity_positions` (que descarta opciones a propósito) — que lee `security.option_contract` de Plaid y emite la forma que consume el motor de griegas.
2. **Manual / fuente futura.** Tres endpoints nuevos:
   - `POST /api/portfolio/import` — punto de extensión: recibe `{positions[], options[], source}` normalizados y reemplaza el snapshot.
   - `GET /api/portfolio/snapshot` — qué hay guardado (lo consulta la UI).
   - `POST /api/portfolio/clear` — borra el snapshot.

   En la UI, el botón *SnapTrade* pasó a ser **Importar**, con un panel que parsea texto plano (`NVDA, 5000, 4000` para acciones; `NVDA CALL 180 2026-09-18 2 12.50` para opciones), valida línea por línea y reporta errores concretos.

**Verificado end-to-end (server real en `localhost:8000`):**
- 0 referencias a SnapTrade en el proyecto; 0 funciones o IDs del DOM huérfanos.
- `import` por la UI → snapshot persistido (3 posiciones + 1 opción, $10,000).
- `/api/portfolio-risk` corre **sin `access_token`** sobre el snapshot y devuelve vol/beta/correlación.
- `/api/portfolio-options` ve la opción → el motor de griegas conserva su fuente de datos.
- `_plaid_extract_options` probado contra un payload con la forma real de Plaid.
- Validación rechaza filas inválidas (ticker vacío, valor ≤0, strike 0, fecha mal formada) en vez de guardar basura.
- `tests_vertex`: **47 pasan**. `vertex_api.py` importa limpio: **62 rutas**, ninguna `snaptrade`.

**Pendiente tuyo:** rotar/revocar el `SNAPTRADE_USER_SECRET` en el portal de SnapTrade y borrar las 4 variables `SNAPTRADE_*` del dashboard de Render.

**Respaldos:** `vertex_api.py.bak` y `platform.html.bak` en el scratchpad de la sesión.
