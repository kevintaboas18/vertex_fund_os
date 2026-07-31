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

### [x] C-02 — ✅ RESUELTO (2026-07-30) — Autenticación en toda la API
- **Era:** ~60 endpoints anónimos en un servicio público. Además de exponer el portafolio, `/api/analyze` permitía a cualquiera vaciar tus cuotas de FMP/Gemini/Anthropic.
- **Ahora:** middleware sobre todas las rutas. `VERTEX_API_TOKEN` es la clave.
  - **Navegador:** `POST /api/login` canjea la clave por una cookie **HttpOnly + SameSite=Strict**. HttpOnly = un XSS no la puede leer; SameSite=Strict = el navegador no la envía desde otro sitio (defensa CSRF). Como las 54 llamadas `fetch()` van al mismo origen, **no hubo que tocar ninguna**.
  - **Scripts (curl, cron):** cabecera `X-Vertex-Token`.
  - Comparación con `secrets.compare_digest` (tiempo constante), para no filtrar la clave por diferencias de latencia.
- **Default seguro:** si `VERTEX_API_TOKEN` **no** está definido, el servidor solo atiende a `localhost`. El desarrollo local sigue sin configuración, y un despliegue al que se le olvidó la variable queda **cerrado**, no abierto.
- **Públicas a propósito:** `/`, `/legacy`, `/wbj`, `/manifest.webmanifest`, `/assets/*` y `/api/auth/status`. El HTML por sí solo no expone datos — todo lo pinta vía `/api/*`, que sí está protegido.
- **UI:** pantalla de acceso que aparece solo si el servidor declara `auth_required`. Se comprueba **antes** de arrancar la app, para no disparar 20 llamadas que solo darían 401.
- **Verificado en navegador real:** anónimo → 401 en `/api/quote`, `/api/analyze`, `/api/portfolio-risk`, `/api/track-record`, `/api/plaid/link-token`, `/api/data-health`. Clave incorrecta → rechazada. Clave correcta → cookie emitida (HttpOnly ✓, SameSite=strict ✓, invisible desde `document.cookie` ✓) y todas las llamadas a 200. Logout → vuelve a 401. `tests_vertex`: 47 pasan.
- **Pendiente tuyo:** definir `VERTEX_API_TOKEN` en Render (`openssl rand -hex 24`). **Sin esa variable el servicio no responderá a nadie** — es deliberado.

### [x] C-03 — ✅ RESUELTO (2026-07-30) — El `access_token` de Plaid salió de las URLs
- **Era:** el token viajaba como parámetro de query en **9** endpoints y se guardaba en el `localStorage` del navegador. Las query strings quedan escritas en los logs de acceso de Render, el historial del navegador y las cabeceras `Referer` hacia terceros — y ese token da lectura de cuentas bancarias/brokerage.
- **Ahora:** el token vive **solo en el servidor**.
  - `POST /api/plaid/exchange-token` lo guarda en la tabla `plaid_items` y devuelve únicamente `{ok, item_id, institution}`. El navegador **nunca lo ve**, así que no puede filtrarlo por ninguna de esas vías.
  - Los 9 endpoints dejaron de aceptar `access_token`; lo leen del servidor vía `_plaid_get_token()`.
  - Endpoints nuevos: `GET /api/plaid/items` (conexiones guardadas, sin token) y `POST /api/plaid/disconnect`, que además llama a `item/remove` de Plaid — invalida el token de verdad, no solo lo borra de tu base.
- **Cifrado en reposo:** si `VERTEX_DB_KEY` está definida, el token se cifra con Fernet (AES-128-CBC + HMAC) antes de tocar el disco. Protege el caso de que alguien obtenga una copia del `.db` (backup, disco de Render) sin tener el entorno del proceso. **No** protege contra un compromiso de la app — para eso está C-02. Sin la variable se guarda en claro y se avisa por log.
- **Frontend:** `portfolioAccessToken` eliminado; ahora solo hay un booleano `plaidConnected` que se resuelve preguntando a `/api/plaid/items` al cargar. Se limpia también el token que versiones previas hubieran dejado en `localStorage`.
- **Verificado:** token cifrado en disco (prefijo Fernet `gAAAAA`, el valor en claro no aparece en el `.db`) y recuperado correctamente; `/api/plaid/items` no lo expone; las 9 firmas sin `access_token`; sin conexión guardada devuelve **409** honesto en vez de romper. En navegador: 0 referencias a `portfolioAccessToken`, 0 `access_token=` en URLs, `localStorage` limpio, y los 7 endpoints del suite (risk, stress, attribution, guardrails, optimizer, options, whatif) responden **200** sobre el snapshot. `tests_vertex`: 47 pasan.
- **Pendiente tuyo:** definir `VERTEX_DB_KEY` en Render. Si ya habías conectado Plaid, vuelve a conectarlo una vez (el token viejo vivía en el navegador, no en la base).

### [x] C-04 — ✅ RESUELTO (2026-07-30) — CORS restringido
- **Era:** `allow_origins=["*"]` + `allow_credentials=True` + `allow_methods=["*"]`.
- **Por qué se arregló junto con C-02:** no era separable. Introducir una cookie de sesión dejando CORS abierto habría **creado** un agujero CSRF que antes no existía. Arreglar C-02 sin C-04 habría empeorado la seguridad.
- **Ahora:** `allow_origins` = `VERTEX_ORIGIN` (la URL de Render) o, si no está definida, solo `localhost:8000`. Métodos limitados a `GET`/`POST`/`OPTIONS`; cabeceras a `content-type` y `x-vertex-token`. Junto con `SameSite=Strict` en la cookie, son dos capas independientes contra CSRF.
- **Pendiente tuyo:** definir `VERTEX_ORIGIN` en Render con la URL pública del servicio.

---

## 2. ALTO — Rompe funcionalidad o contradice `CLAUDE.md`

### [x] A-01 — ✅ RESUELTO (2026-07-30) — Identidad única y configurable ante la SEC
- **Era peor de lo que decía el hallazgo: había DOS identidades hardcodeadas y distintas**, y ninguna configurable:
  - `engine/wbj/providers/edgar.py:38` → `"warren-buffett-jr victor@infusioninvestments.com"`
  - `vertex_api.py:1330` → `"Vertex Holding Group research contact@vertexholding.com"`

  Mientras tanto `render.yaml` y `API/.env.example` **ya declaraban tu identidad** (`Vertex Fund OS - Kevin Taboas kevintaboas02@gmail.com`) y ambas se ignoraban. La SEC limita **por user-agent**, así que eran dos cuotas separadas y una compartida con el proyecto de Victor: si a él le limitan, a ti también.
- **Ahora:** una sola identidad, resuelta así — `Settings.edgar_user_agent` (de `API/.env`) → variable de entorno `EDGAR_USER_AGENT` → fallback genérico que **no lleva el correo de nadie**.
  - `config.py`: campo `edgar_user_agent`, leído del `.env` **y** de `os.environ` (como ya se hacía con `ANTHROPIC_API_KEY`) — necesario porque en Render no existe `API/.env`.
  - `edgar.py`: nueva `edgar_headers(settings)`; las 10 llamadas del provider usan `self._headers`. Se conserva `_EDGAR_HEADERS` a nivel de módulo porque lo importan sueltos `screener.py` y `scripts/webapp.py`.
  - `vertex_api.py`: `SEC_HEADERS` pasa a usar la misma identidad. `_WBJ_ENGINE_PATH` se movió al arranque del módulo, porque el respaldo lo necesita al importar.
- **De paso (parte de M-04):** `scripts/premarket_email.py` mandaba el correo pre-market a `victor@infusioninvestments.com` por defecto. Ahora al tuyo. *(Siguen pendientes en M-04 los feriados fijados a 2026 y la ausencia de workflow.)*
- **Verificado:** los 3 caminos (entorno / `API/.env` / sin configurar) resuelven correctamente en las 5 superficies (`settings`, `EdgarProvider`, módulo, `_EDGAR_HEADERS`, `vertex_api`). Sin configurar **no aparece ningún correo ajeno**. Petición **real a la SEC** con tu identidad: respondió CIK 320193 para AAPL, sin 403. Engine: **1959 pasan, 1 skip**. `tests_vertex`: **47 pasan**. Cero rastro de los dos correos anteriores en código.
- **Pendiente tuyo:** nada — `render.yaml` ya trae tu identidad como `value` (no `sync: false`), así que se despliega sola. Si prefieres otro correo, cámbialo ahí.

### [x] A-02 — ✅ RESUELTO (2026-07-30)
- Dos funciones llamaban `load_settings()` sin inyectar las claves del entorno. En Render no existe `API/.env` — llegan por el dashboard — así que `settings.fmp_api_key` salía `None`, `FMPProvider.available` era `False` y devolvía `None` en silencio: los items obligatorios **4 (13F/13D-G) y 5 (insiders >$1M)** de `CLAUDE.md` salían VACÍOS en producción. En local funcionaban, que es por lo que no se veía. Nuevo `_engine_settings()` es el único camino y cubre 6 claves en vez de 3. Verificado sin `API/.env`: `available` pasa de `False` a `True`.


### [x] A-03 — ✅ RESUELTO (2026-07-30)
- `risk.py` tenía el perfil de Victor **transcrito a mano** ($25.000, 3–5 años) cuando `Kevin.md` dice explícitamente que lo reemplaza ($1.000, 1–3 años): el reporte sugería un tamaño de posición 25 veces mayor que el capital real, contra la advertencia de riesgo de ruina del propio perfil. Ahora se **lee del archivo** — transcribir un perfil garantiza que se desincronice. Los dos tests que codificaban las cifras de Victor ahora derivan del perfil vigente, así que comprueban el mecanismo y no a una persona.
- **Contrastado con el repo de Victor (2026-07-30).** Su `risk.py` sigue con el perfil transcrito y su docstring dice que es deliberado: *"a literal, dated transcription… not re-parsed from markdown at runtime"*. Su objeción es válida — parsear prosa es frágil — y **mi primera versión la confirmó**: `$1.000 USD` (formato español, punto de miles) se leía como **1 dólar**, porque solo quitaba comas. El dimensionamiento de posiciones habría colapsado en silencio.
- **Corregido:** `_parse_money()` entiende ambas convenciones (`1,000` y `1.000` son el mismo número; `1.500,50` y `1,500.50` también). Y la respuesta a la objeción de Victor no es dejar de leer el archivo, sino **declarar qué se leyó**: `profile_fit` ahora expone `fields_parsed`, `fields_defaulted` y un `profile_caveat`. Hoy avisa que `max_position_pct` **no aparece en `Kevin.md`** y es un default conservador — antes ese 5–20% se presentaba como si el inversionista lo hubiera fijado.

### [x] A-04 — ✅ RESUELTO (2026-07-30)
- El umbral se aplicaba a cada Form 4 **por separado**, así que un insider que vendía 6 veces $300k ($1.8M en total) desaparecía del reporte — justo el patrón de venta escalonada que más importa detectar. `CLAUDE.md` dice "que excedan $1M **en total**". Ahora agrupa por persona y dirección antes de umbralizar, como ya hacía el engine. Verificado con ese caso exacto.


### [x] A-05 — ✅ RESUELTO (2026-07-30)
- Los 7 overrides existen ahora en ambos caminos. Antes 1, 3 y 7 solo aparecían como PROSA en el prompt del LLM — se le *pedía* que los mencionara, sin chequeo determinista, violando "sin fórmula, no hay conclusión". Ahora se leen de los mismos `mandatory_flags` que emiten los especialistas. El override 1 además **capa** el perfil (verificado: un caso de dependencia de capital que pasaba el gate ahora sale Speculative); el 3 y el 7 viajan como banderas propias para que el reporte pueda destacarlos.


### [x] A-06 — ✅ RESUELTO (2026-07-30)
- Un análisis disparaba 80–120 llamadas a FMP y el plan free son **250 al día**. Los bucles de pares recortados a los mínimos que la propia metodología exige (8 pares para `MIN_PEERS`, 5 filas para el percentil RS): máximo ~76, con constantes nombradas en vez de números sueltos.


### [x] A-07 — ✅ RESUELTO (2026-07-30) — `.claude/launch.json` apuntaba al Mac de Victor
- **Era:** `/Users/victorgonzalez/Desktop/warren-buffett-jr/engine/.venv/bin/python` — ruta macOS en una máquina Windows; el preview nunca arrancaba.
- **Ahora:** `python -m uvicorn vertex_api:app --host 127.0.0.1 --port 8000`. Verificado: el server levanta y sirve la app.

---

## 3. MEDIO — Inconsistencias, deuda y documentación falsa

### [x] M-01 — ✅ RESUELTO (2026-07-30)
- `CLAUDE.md` describía un árbol con dos typos en el nombre y omitía 7 carpetas reales — incluida `engine/`, el motor determinista. El orquestador se guía por ese árbol: si no sabe que existe, no sabe que hay matemática. Reescrito con la estructura real. El paso 6 y la nota del sub-agente Risk apuntaban al perfil de Victor; ahora a `Kevin.md`, con la advertencia de que el sizing manda con capital pequeño. `.claude/agents/risk-analysis.md` igual.


### [x] M-02 — ✅ RESUELTO (2026-07-30)
- `README.md` creado. Era el único documento que explica el sistema a alguien que llega nuevo — incluido tú en seis meses. Cubre las dos capas, las 6 categorías con sus pesos, cómo arrancar, las claves que no pueden faltar, el protocolo de memoria y los límites del sistema.


### [x] M-03 — ✅ RESUELTO (2026-07-30)
- `.github/workflows/premarket-email.yml` creado. El script decía "corre en GitHub Actions cada mañana de mercado" y no existía ningún workflow: era código muerto. `workflow_dispatch` por defecto en modo prueba, para poder probarlo sin mandar correos.


### [x] M-04 — ✅ RESUELTO (2026-07-30)
- Feriados de 2027 añadidos con la nota de que hay que actualizarlos cada año — sin la lista del año en curso el script corre en días de mercado cerrado. `EMAIL_TO` ya se corrigió con A-01.


### [x] M-05 — ✅ RESUELTO (2026-07-30)
- `main.py` no indexaba la raíz de `Cerebro/`: 7 documentos nunca se cargaban, **incluido `QUICK_START.md`**, que `CLAUDE.md` señala como el punto de partida del packet. Ahora indexa 88 en vez de 81. Validaba además el perfil de Victor; ahora el vigente. **Y no arrancaba en Windows** (N-03): imprime ✔/❌ a una consola cp1252 y moría con `UnicodeEncodeError` antes de indexar nada.


### [x] M-06 — ✅ RESUELTO (2026-07-30) — `RESUME.md` reescrito con el estado real
- **Era:** el documento estaba falso **de arriba abajo**, no en una línea suelta. Afirmaba "paused mid-plan (9.5/25 tasks)", que `engine/wbj/packet/builder.py` "does not exist yet" (**tiene 1139 líneas**), "160 tests" (**hay 2006**), rama `feature/wbj-engine` (**es `main`**), un 403 de FMP en `/api/v3/` (**el provider ya usa `/stable/`**), y mandaba leer `.superpowers/sdd/progress.md`, que **no existe**. Además incluía una instrucción activa: `git config --global user.email victor@infusioninvestments.com` — que habría cambiado la identidad de git de **toda la máquina**, no solo de este repo.
- **Primer intento insuficiente:** taché solo la línea del correo. Anotar una línea de un documento que miente en todas las demás no arregla nada — el resto seguía leyéndose como si fuera cierto.
- **Ahora:** reescrito como estado real y útil: qué es cada capa, cómo se corre (web y CLI, con los 11 comandos reales de `wbj`), cómo se testea, qué variables de entorno no pueden faltar en un despliegue, y dónde va la auditoría. Cada dato verificado contra el código antes de escribirlo.

### [x] M-08 — ✅ RESUELTO (2026-07-30)
- Eliminadas `_stooq_series` y su copia inline (60 líneas). Stooq responde con un desafío anti-bot, así que ese camino devolvía `None` **siempre**: el sistema decía tener 3 fuentes de historia diaria y tenía 2. También las etiquetas de fuente que seguían diciendo "stooq (respaldo)".


### [x] M-09 — ✅ RESUELTO (2026-07-30)
- El campo emitía literalmente `BUY` / `AVOID` — una orden, no una clasificación, justo lo que los "Límites del sistema" de `CLAUDE.md` prohíben. Ahora emite `FAVORABLE` / `CONDICIONAL` / `ESPECULATIVO` / `DESFAVORABLE`. `_reco_norm()` traduce los valores viejos al leer `vertex.db`: el track record compara la dirección de reportes anteriores, así que renombrar sin puente habría invalidado el histórico. Dos tests nuevos: uno prohíbe que ningún perfil devuelva una orden, otro comprueba que las filas antiguas siguen puntuando.


### [x] M-10 — ✅ RESUELTO (2026-07-30)
- `judge_model` → `claude-opus-5`. Mismo precio que 4.8 ($5/$25 por MTok) y mejor en lo que hace el judge: clasificar moat, catalizadores y thesis-killers. En Opus 5 el razonamiento está **activo por defecto** y comparte el presupuesto de `max_tokens`, así que se sube de 4096/2048 a 8192 — con el valor anterior la respuesta habría salido truncada.


### [x] M-11 — ✅ RESUELTO (2026-07-30)
- `Memoria/calibracion.md` creado como semilla. Dice explícitamente que sin predicciones el sesgo **no es medible**, así que el paso 2 del protocolo no aplica — ausencia de datos no es evidencia de buena calibración, tampoco de mala.


### [x] M-12 — ✅ RESUELTO (2026-07-30)
- Documentado en `render.yaml` y `DEPLOYMENT.md`: el plan `free` **no tiene disco persistente**, así que cada redeploy borra reportes, snapshots, la conexión de Plaid y las predicciones del track record. El bloque `disk` y la variable `VERTEX_DB` van juntos o no sirven.


### [x] M-13 — ✅ RESUELTO (2026-07-30)
- Rangos con techo de MAJOR. El pin inicial de `pandas<3` era incorrecto: el entorno corre 3.0.3 y los 2008 tests pasan, así que habría forzado un downgrade sin motivo. Corregido a `<4`.


### [x] M-14 — ✅ RESUELTO (2026-07-30)
- El override 2 dice que ROIC<WACC impide la clasificación "Elite", pero la banda se calculaba **solo desde el raw**: una empresa que destruye valor podía salir etiquetada "Elite raw score" en su propio reporte. Arreglado en las dos implementaciones (`gates.py` y `_wbj_gates`), con `overrides` opcional para no romper ningún llamador existente.


### [x] M-15 — ✅ RESUELTO (2026-07-30) — `docs/superpowers/` archivado
- **Era:** 5 documentos (1236 líneas) de planes y specs de una construcción **ya terminada**, con rutas absolutas de la máquina de Victor, colgando de `docs/` como si fueran instrucciones vigentes. El orquestador podía leerlos y actuar sobre un plan cerrado.
- **Ahora:** movidos a `docs/archive/` con `git mv` (conserva el historial) y un `README.md` que dice explícitamente que son históricos, por qué se conservan — explican el *porqué* del diseño — y adónde ir para el estado real. Se retiró la anotación inline que había puesto antes: con el directorio y el README diciendo "archivo", sobraba.
- **Verificado:** ninguna referencia rota a `docs/superpowers/` en el proyecto.

---

## 4. Nota operativa — el entorno no podía correr el engine

Tu Python global **no tenía** `pytest`, `scipy`, `typer`, `matplotlib`, `anthropic` ni `python-dotenv`. Los instalé para poder auditar (por eso pude correr los 2006 tests). Sin ellos, `_engine_scorecard` cae por el `except` de import en `vertex_api.py:7459-7461` y **todo el scorecard degrada al fallback del LLM** — es decir, tendrías números inventados por Gemini en vez de la matemática de Victor, con el aviso "estimación LLM (fallback)" que menciona `DEPLOYMENT.md §6`.

**Acción recomendada antes de todo lo demás:** crear un venv del proyecto y `pip install -r requirements.txt && pip install -e engine[dev]`, para que local y Render corran el mismo camino determinista.

---

## 5b. Hallazgos NUEVOS de la re-auditoría (2026-07-30)

Encontrados al re-auditar el estado actual — dos los introduje yo en C-02.

### [x] N-01 — ✅ RESUELTO — `/api/login` sin límite de intentos
- 12 intentos fallidos seguidos, ningún bloqueo. Un token de 24 bytes no se adivina por fuerza bruta, pero sin freno el endpoint sirve de **oráculo** y de vector de carga: cada intento es gratis para quien lo lanza y trabajo para el servidor. Ventana deslizante por IP: 8 intentos / 5 min → `429`, con purga de entradas caducadas para acotar la memoria.

### [x] N-02 — ✅ RESUELTO — El flag `Secure` de la cookie dependía de recordar una variable
- Salía de `VERTEX_ORIGIN.startswith("https://")`. Olvidar esa variable emitía la cookie de sesión **sin `Secure` sobre https** — viajaría en claro ante un downgrade a http. Ahora sale del esquema **real** de la petición (`x-forwarded-proto`, que es lo que manda el proxy de Render).

### [x] N-03 — ✅ RESUELTO — `main.py` no arrancaba en Windows
- Imprime `✔`/`❌`/`⚠️` y la consola de Windows usa cp1252, así que `python main.py` moría con `UnicodeEncodeError` **antes de indexar nada**. Descubierto al ejecutarlo, no al leerlo. Se fuerza UTF-8 en la salida con degradación a sustitución si la consola no lo admite.

---

## 5. Resumen del checklist

| # | ID | Severidad | Título | Archivo principal |
|---|---|---|---|---|
| 1 | ~~C-01~~ | ✅ Resuelto | SnapTrade eliminado por completo | §6 |
| 2 | ~~C-02~~ | ✅ Resuelto | Auth por cookie HttpOnly + cabecera | §6 |
| 3 | ~~C-03~~ | ✅ Resuelto | Token custodiado en servidor + cifrado | §6 |
| 4 | ~~C-04~~ | ✅ Resuelto | CORS restringido a VERTEX_ORIGIN | §6 |
| 5 | ~~A-01~~ | ✅ Resuelto | Identidad SEC única y configurable | §6 |
| 6 | ~~A-02~~ | ✅ Resuelto | Claves del entorno vía `_engine_settings()` | §6 |
| 7 | ~~A-03~~ | ✅ Resuelto | El perfil se lee de `Kevin.md` | §6 |
| 8 | ~~A-04~~ | ✅ Resuelto | Insiders agregados por persona | §6 |
| 9 | ~~A-05~~ | ✅ Resuelto | Overrides 1, 3 y 7 añadidos al camino web | §6 |
| 10 | ~~A-06~~ | ✅ Resuelto | ~76 llamadas máximo por análisis | §6 |
| 11 | ~~A-07~~ | ✅ Resuelto | `launch.json` reapuntado a uvicorn local | §6 |
| 12 | ~~M-01~~ | ✅ Resuelto | `CLAUDE.md` con la estructura real | §6 |
| 13 | ~~M-02~~ | ✅ Resuelto | `README.md` creado | §6 |
| 14 | ~~M-03~~ | ✅ Resuelto | Workflow de GitHub Actions creado | §6 |
| 15 | ~~M-04~~ | ✅ Resuelto | Feriados 2027 + email corregido | §6 |
| 16 | ~~M-05~~ | ✅ Resuelto | `main.py` indexa 88 docs y arranca en Windows | §6 |
| 17 | ~~M-06~~ | ✅ Resuelto | `RESUME.md` reescrito con el estado real | §6 |
| 18 | ~~M-07~~ | ✅ Resuelto | Repo git inicializado, 2 commits | §6 |
| 19 | ~~M-08~~ | ✅ Resuelto | Código muerto de Stooq eliminado | §6 |
| 20 | ~~M-09~~ | ✅ Resuelto | Clases de research, sin órdenes | §6 |
| 21 | ~~M-10~~ | ✅ Resuelto | `claude-opus-5` + max_tokens 8192 | §6 |
| 22 | ~~M-11~~ | ✅ Resuelto | `calibracion.md` semilla creada | §6 |
| 23 | ~~M-12~~ | ✅ Resuelto | Pérdida de memoria documentada | §6 |
| 24 | ~~M-13~~ | ✅ Resuelto | Rangos con techo de major | §6 |
| 25 | ~~M-14~~ | ✅ Resuelto | "Elite" bloqueado por override 2 | §6 |
| 26 | ~~M-15~~ | ✅ Resuelto | Movido a `docs/archive/` con README | §6 |

**Orden de arreglo sugerido:** ~~M-07~~ (git, para poder revertir) → ~~C-01~~ → ~~C-02~~ → ~~C-03~~ → ~~C-04~~ → ~~A-01~~ → A-02 → A-03 → A-04 → A-05 → A-06 → ~~A-07~~ → el resto de M.

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
