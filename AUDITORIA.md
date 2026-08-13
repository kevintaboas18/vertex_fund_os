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

**Orden de arreglo sugerido** (histórico): ~~M-07~~ (git, para poder revertir) → ~~C-01~~ → ~~C-02~~ → ~~C-03~~ → ~~C-04~~ → ~~A-01~~ → ~~A-02~~ → ~~A-03~~ → ~~A-04~~ → ~~A-05~~ → ~~A-06~~ → ~~A-07~~ → el resto de M.

**Los 26 están cerrados.** Esta línea dejó A-02..A-06 sin tachar mucho después de resolverlos, contradiciendo a la tabla de arriba en el mismo documento: quien leyera solo el orden sugerido creería que quedan cinco abiertos.

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

---

# 7. Auditoría del área `analyze` — 2026-07-31

Método: corrida real de `run_report("NVDA")` con las claves de `vertex.env`,
más prueba directa de cada endpoint externo. Todo lo de abajo está **medido**,
no inferido.

## 7.1 Lo que funciona (verificado)

| Área | Estado |
|---|---|
| Pipeline `wbj report` | `status: ok` en 35 s, 11 secciones, degrada sin crash |
| Datos de mercado | OHLCV **2026-07-31** (hoy), 754 sesiones, ajustado, orden descendente |
| Fundamentales | FY2026 completo, 11 años, revenue $215.9 B |
| SEC EDGAR | submissions 200, 13F dataset Q1-Q2 2026, Form 4 junio 2026 |
| 13F institucional | BlackRock / Vanguard / State Street con acciones y valor reales |
| Insiders >$1M | 5 ventas agregadas, la mayor $485.7 M (Stevens, 2026-06-18) |
| Overrides | los **7** implementados y cableados |
| Sub-agentes | los **7** definidos (6 que puntúan + visual) |
| Gráficas | 4 PNG; cumplen las 4 reglas (rango, supuestos, punteado, sello de fecha) |
| Contenido obligatorio CLAUDE.md | los 6 puntos presentes en el markdown |
| Hashes de auditoría | 6 packet_hashes SHA-256, uno por especialista |

## 7.2 Hallazgos

### V-01 (CRÍTICO) — El reporte se contradice a sí mismo sobre el mismo ticker

En una sola corrida:

- `football_field.png` → valor razonable **$33–50**, precio $198.70 (−79 %)
- `scenarios.png` → proyección **$197–320** con "growth +40 % (5y earnings)"
- `valuation_weighted_value` → **$41.17**

Dos motores distintos alimentan las dos gráficas: la valuación usa el DCF por
escenarios (`valuation.py`), y los targets de precio usan crecimiento de
utilidades a 5 años (`targets.py`). Nadie los reconcilia:
`contradictions()` sólo compara los `score_10` de las categorías, nunca las
salidas de precio.

**Por qué falla:** falta una regla de contradicción sobre valor por acción.
**Solución:** extender `CONTRADICTION_RESOLUTION` para comparar
`valuation_weighted_value` contra la banda de `price_targets` y emitir un
choque cuando no se solapan.

### V-02 (CRÍTICO) — El crecimiento base ignora el consenso que ya está en el packet

`valuation.py:1061` → `base_growth = reinvestment_rate × ROIC`, con
`reinvestment_rate = capex / NOPAT`.

Para NVDA: capex $6.04 B / NOPAT ≈ $104 B → 5.8 % × ROIC 87 % ≈ **5.05 %**.

Contra la realidad: NVDA creció **65.5 %** el último año, y el packet ya trae
`fmp_analyst_estimates` con 10 filas proyectando **$1.005 T de revenue a
FY2031** (~36 % CAGR). El propio módulo calcula `consensus_growth`
(línea 1208) pero sólo lo usa para puntuar la plausibilidad del reverse-DCF
(línea 1866) — **nunca para construir los escenarios**.

**Por qué falla:** medir reinversión sólo con capex subestima estructuralmente
a las empresas asset-light que crecen vía I+D (gasto, no capex). El reverse-DCF
lo confirma: el mercado implica 55.3 % de CAGR contra los 5.05 % del motor.
**Solución:** usar `consensus_growth` como base cuando exista (con el capex×ROIC
como respaldo declarado), o incorporar I+D y ΔWC a la tasa de reinversión.
Ya es overridable vía `Entradas/NVDA.json` → `scenarios.base.growth`.

### V-03 (ALTO) — Sin créditos en Anthropic; el judge tumba una categoría entera

La clave `ANTHROPIC_API_KEY` es **válida**, pero la cuenta responde:
`400 — Your credit balance is too low to access the Anthropic API`.

Cadena de efectos medida:

1. judge caído → `judgments_applied: 1` (sólo la respuesta del archivo de analista)
2. Market & Growth pierde 3 de 5 dimensiones (**13 de 20 puntos**):
   `tam_and_industry_tailwind` N/S, `product_and_business_catalysts` N/S,
   `growth_runway_and_share_capture` N/S
3. cobertura de Market cae a **48.75 %** (las otras cinco: 91–100 %)
4. dispara `OVERRIDE_6_COVERAGE_GATE_INELIGIBLE`
5. `raw_total` 45.33 < 50 → etiqueta **"Avoid / Wait"**
6. `thesis_killers: []` y `monitoring_triggers: []`, que DECISION_RULES.md
   exige (mínimo 3 thesis killers)

**Solución:** cargar créditos en la cuenta de Anthropic. Sin eso, ninguna
corrida puede superar el gate de cobertura, y la etiqueta "Avoid" refleja
datos faltantes, no la empresa.

### V-04 (ALTO) — `JUDGE_MODEL` contiene una clave, no un nombre de modelo

`vertex.env:32` → `JUDGE_MODEL=sk-ant-…` (una clave, no un modelo)

`config.py:93` lo toma tal cual (`_key("JUDGE_MODEL") or "claude-opus-5"`), así
que el judge pediría `model="sk-ant-…"`. Independiente de V-03: aunque
haya créditos, esto rompe el judge.
**Solución:** dejarlo vacío (usa `claude-opus-5`) o poner `claude-haiku-4-5`.

### V-05 (MEDIO) — Dos precios distintos en el mismo packet

- `facts_table["price"]` = **195.04** (FinnHub quote, as_of 21:00 UTC)
- `market_data.daily[0].adj_close` = **198.70** (FMP, 2026-07-31)

Margin of safety, earnings yield, FCF yield y reverse-DCF usan 195.04;
los niveles técnicos y el marcador "current" del football field usan 198.70.
Brecha de 1.9 % entre dos números que el reporte presenta como "el precio".
**Solución:** una sola fuente de precio en el packet, con la otra como respaldo
declarado.

### V-06 (MEDIO) — Dos reconciliaciones internas fallan

De `missing_or_conflicted_data`:

- `financial: CORE27_RECONCILIATION_WARNING` — dimensión ponderada 6.72 vs
  core-27 8.80, diferencia **2.08 > 1.5** de tolerancia
- `valuation: FCFF_ECONOMIC_PROFIT_RECONCILIATION_FAILED` — dos métodos que
  deben coincidir, no coinciden
- `valuation: PER_SHARE_VALUE_INCOMPLETE_NO_DILUTION_SCHEDULE`

Se reportan (bien) pero nadie los resuelve. Dos formas de calcular el mismo
score difieren en 2.08 puntos sobre 10.

### V-07 (MEDIO) — Dos endpoints de pago caídos degradan en silencio

| Endpoint | Código | Efecto |
|---|---|---|
| FMP `institutional-ownership` | **402** | cubierto por el dataset 13F de la SEC |
| FinnHub `eps-estimate` | **403** | menos revisiones de estimados |
| FinnHub `revenue-estimate` | **403** | contribuye al hueco de Market |
| QuantData `option/flow` | **403** | flujo de opciones / dark pool / GEX muerto |

FMP quote, statements y analyst-estimates: **200**. FRED: **200**. SEC: **200**.

### V-08 (BAJO) — El mensaje de fallback miente sobre la causa

Los 7 campos de `executive_thesis` dicen
`"Narrative unavailable (no ANTHROPIC_API_KEY)"`. La clave **sí está puesta**;
lo que falta son créditos. El mensaje manda a diagnosticar el lugar equivocado.

### V-09 (BAJO) — Variables muertas y faltantes en `render.yaml`

- Declaradas y **leídas por ningún código**: `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`
- Leídas por el código y **no declaradas** (todas con default, opcionales):
  `JUDGE_MODEL`, `GOOGLE_API_KEY`, `EXTRACTION_MODEL`, `VERTEX_SCHEDULER`,
  `VERTEX_PRIMARY_TICKERS`
- **Obligatoria en Render**: `VERTEX_API_TOKEN` (sin ella el servicio sólo
  atiende localhost, o sea inservible en Render)

## 7.3 Resueltos — 2026-07-31

| # | Estado | Commit |
|---|---|---|
| V-05 | Cerrado | `057692c` |
| V-06 | Cerrado | `a48d956` |
| V-07 | Cerrado | `63b18c9` |
| V-08 | Cerrado | `1f2c53e` |

**V-05.** Dos causas, no una. (a) FMP sirve la sesión EN CURSO como una barra
cuyo `close` es el último print; el builder la tomaba como cierre y construía
`market_timestamp` como un cierre de las 16:00 ET que aún no había pasado —
dos corridas de la misma mañana daban niveles y ATR distintos, justo lo que
los `packet_hashes` dicen descartar. (b) `facts_table["price"]` leía
`profile.price`, que temprano en la sesión devuelve el cierre ANTERIOR.
Ahora `_settled_sessions()` descarta la sesión sin cerrar y el precio es el
cierre ajustado de esa misma barra. Verificado: 195.04 == 195.04, 753
sesiones acción y benchmark. **Bonus:** la serie de la acción no pasaba
`today=now.date()` pese a que su docstring lo exige — el benchmark sí, así que
podían quedar ancladas a días distintos.

**V-06.** El flag de valuación no detectaba supuestos inconsistentes: detectaba
un bug de este repo. `economic_profit_value(ic0, eps, wacc)` exige beneficios
económicos (`NOPAT_t − WACC×IC_{t−1}`) y recibía `NOPAT×(1−rr)×(1+g)^t`, que es
flujo de caja libre; sumar `IC0` encima de un valor presente de flujos duplica
la base de capital (**+31.6 %** con números tipo NVDA). `economic_profit_ev()`
construye la serie desde el mismo pronóstico que el DCF valora y usa
`TV_fcff − IC_N` como término terminal. Reconcilia **exacto** (rel 1e-9) e
invariante a `IC0` entre 0 y 900 k. También: las dos funciones reclamaban el id
`VAL-EVAEV-021` y el registro se queda con la última decorada.
El flag del core-27 ahora nombra la causa en vez de repetir los dos números.

**V-07.** `Provider.blocked_endpoints` recuerda los rechazos 401/402/403 y
`run_report` los declara. Un 404 no cuenta — eso sí significa que la empresa no
tiene el dato. Los tres endpoints de pago aparecen en el reporte.

**V-08.** `_llm_failure_reason()` distingue saldo agotado / modelo inválido /
clave rechazada / 429 / SDK ausente. **Bonus:** una corrida con el judge caído
cacheaba sus respuestas de analista bajo la lista completa de preguntas, así
que toda corrida posterior dentro del TTL saltaba el judge y reproducía los
scores degradados en silencio — una caída envenenando días de reportes.

Pendiente: **V-02** (crecimiento base ignora el consenso), **V-01**
(el reporte se contradice sobre el mismo ticker), **V-03/V-04** (Anthropic).

## 7.4 Resueltos — V-01 y V-02

| # | Estado | Commit |
|---|---|---|
| V-02 | Cerrado | `6d52aa3` |
| V-01 | Cerrado | `2a549e2` |

**V-02.** `base_growth = (capex/NOPAT) × ROIC` invertía la regla que citaba.
`VAL-REINV-043` es *"Terminal reinvestment consistency"* (`rr = g / TerminalROIC`):
una restricción sobre el año terminal, y DECISION_RULES.md regla 2 enuncia la
misma identidad como condición de consistencia **sobre** un pronóstico, no como
forma de producir uno — el DCF ya la aplica en `terminal_year_metrics`.
DATASET.md hace de `forecast_drivers` una entrada **requerida**. Ahora el
crecimiento base es el CAGR de consenso sobre el periodo explícito (NVDA: 36.01%
a FY2031, 22 analistas), con la capacidad fundamental declarada al lado.

Aislando el cambio: ponderado **$41.16 → $124.19**, `fair_value` 1.0 → 5.0,
puntos **0.94 → 1.74**.

Dos bugs encontrados al implementarlo:

1. **`AnalysisMeta` no tiene campo `as_of`**, y tres llamadores hacían
   `getattr(packet.analysis, "as_of", "")` → siempre `""`. Con ese corte toda
   fila de estimados contaba como futura, así que `_next_year_estimate` devolvía
   la **más vieja**: el "próximo año" de NVDA era **FY2022** ($26.7 B contra
   $215.9 B reportados), alimentando el reverse-DCF y el P/E forward.
2. **Sobrescribir solo `scenarios.base.growth`** — el override que el propio
   módulo documenta — reventaba la categoría entera: bear/bull siguen derivados
   del base sin sobrescribir, así que un base de 12% bajo un consenso de 36%
   daba `low=18% > mode=12%`, numpy lanzaba `left > mode` y la valuación volvía
   en `ERROR`.

**V-01.** Regla 5 de CONTRADICTION_RESOLUTION.md: "muestra ambas vistas y nombra
la condición que la resolvería". `_price_view_divergence()` declara juntas la
vista del DCF (intrínseco a 5 años) y la del target (múltiplo de hoy a 12 meses),
con sus distancias, y nombra la condición: si el múltiplo persiste.

Y la **sexta fila** de esa tabla (*"DCF high, reverse DCF demanding → Lower
valuation confidence"*) estaba implementada entera y era **inalcanzable**:
`run_report` llamaba `contradictions(s10, raw)` sin el contexto que la fila
necesita. La tabla de seis filas tenía cinco en la práctica.

También: `_price_and_atr` devolvía el cierre **crudo** mientras
`facts_table["price"]` lleva el ajustado — la misma grieta que V-05 cerró una
capa más abajo.

Pendiente: **V-03/V-04** (Anthropic: saldo y `JUDGE_MODEL`).

## 7.5 Re-auditoría — 2026-07-31 (tras V-01/02/05/06/07/08)

**Cerrado en esta pasada:**

- **V-04** — `JUDGE_MODEL = 'claude-opus-5'`, ya no es una clave. Verificado.
- **V-09 (nuevo)** — Encontrado al probar los arreglos contra otros tickers.
  Con el crecimiento base ya del consenso, los multiplicadores `bear = base×0.5`
  y `bull = base×1.5` producían supuestos que nadie hizo: **PLTR bull 109.09% =
  ingresos ×40 en cinco años**. FMP ya sirve `revenueLow`/`revenueHigh` — la
  dispersión real de analistas para el mismo horizonte. Commit `6c2457f`.

  | | antes (multiplicador) | ahora (dispersión real) |
  |---|---|---|
  | NVDA | 18.00 / 36.01 / 54.01 | **31.20 / 36.01 / 39.73** |
  | PLTR | 36.36 / 72.73 / 109.09 | **71.21 / 72.73 / 76.52** |
  | KO | 1.75 / 3.50 / 5.25 | **3.20 / 3.50 / 4.07** |

**Verificado sano (no son bugs):**

- `PER_SHARE_VALUE_INCOMPLETE_NO_DILUTION_SCHEDULE` — correcto por diseño.
  VAL-T008 exige declarar "incompleto" sin el schedule de convertibles, que vive
  en las notas del 10-K y ningún endpoint sirve. Es entrada de `Entradas/`.
- `revenue_quality_and_growth` bajo el piso — `FIN-GR-004/005` esperan el judge
  o `Entradas/`. Misma causa raíz que Market (V-03), no un defecto aparte.
- JPM sin escenarios de DCF — correcto: la matriz de selección de modelos veta
  el DCF empresarial para bancos.
- APIs: FMP quote/statements/estimates, FinnHub quote, FRED y SEC EDGAR → **200**.

**Estado medido (NVDA, 2026-07-31):** 47.2/100, "Avoid / Wait",
`OVERRIDE_6_COVERAGE_GATE_INELIGIBLE`, 6 hashes de auditoría, 10 tenedores 13F,
10 insiders, revisit 2026-08-26. **2054 tests del engine + 49 de la web.**

**Único abierto: V-03** — la cuenta de Anthropic sigue sin saldo
(`credit balance is too low`). La clave es válida y `JUDGE_MODEL` ya es correcto.
Sin saldo: 7 preguntas cualitativas sin responder → Market pierde 13 de 20 puntos
→ cobertura 0.49 → OVERRIDE_6 → la etiqueta refleja datos faltantes, no la
empresa. No hay nada que arreglar en el código: requiere cargar créditos.

---

# 8. Re-auditoría del área `analyze` — 2026-08-01

## 8.1 Hallazgo nuevo y arreglado

**A-04 — la distancia a un soporte tenía el signo al revés.** `levels_engine`
calculaba la rama de soporte como `(CurrentClose − upper)`, los operandos al revés
de la fórmula que escribe PRICE_LEVEL_SYNTHESIS.md (`(Level − CurrentPrice) /
CurrentPrice`). Toda zona **debajo** del precio salía **positiva**.

Dos convenciones en la misma tabla, sobre NVDA con precio 200.75:

| nivel | valor | mostraba | correcto |
|---|---|---|---|
| `moving_average` | 193.11 | −3.80 ✓ | −3.80 |
| `support zone` | 186.02–191.74 | **+4.49** ✗ | −4.49 |
| `weekly_zone` | 94.48–107.42 | **+46.49** ✗ | −46.49 |

La plataforma formatea ese campo como `${dp >= 0 ? '+' : ''}${dp}%`, así que un
soporte 47% **abajo** se mostraba como **"+46.5%"**. Los fixtures de
`aggregate/synthesis.py` ya asumían la forma con signo (un soporte 90–92 con precio
100 declarado como −8.0): las dos capas discrepaban y el fixture tenía razón.
Commit `94f0584`. Verificado: los 40 niveles del reporte quedan coherentes, y 0
incoherencias en NVDA/AAPL/KO/JPM/PLTR.

## 8.2 Verificado sano — no son bugs

| Área | Resultado |
|---|---|
| Registro de métricas | **207 de 207** implementadas; 0 sin documentar |
| Pesos de categoría | 20/15/20/20/15/10 — coinciden CLAUDE.md ↔ engine |
| Umbrales de los 3 gates | coinciden literal con SCORING_AND_GATES.md |
| Confianza total | `sum(max × conf)/100` = 86.4683 exacto |
| Perfil de Kevin | `max_position_pct = (0.2, 0.3)` leído de Kevin.md |
| Niveles de precio | soporte debajo, resistencia encima, 40 niveles coherentes |
| Escenarios ↔ niveles | los 3 valores intrínsecos coinciden exacto |
| Frescura | OHLCV 1d, FY 10-K, Q 10-Q, consenso FY2027, 13F Q1-Q2 2026, insiders jun-2026 |
| 6 puntos obligatorios | los 6 presentes (clasificación, revisita, rangos, 13F, insiders >$1M, visuales) |
| `except` silenciosos | los revisados devuelven un null declarado con razón, no tragan |

## 8.3 La consecuencia operativa de V-03

`OVERRIDE_6_COVERAGE_GATE_INELIGIBLE` dispara en **4 de 4** tickers probados:

| ticker | cobertura Market | total | etiqueta |
|---|---|---|---|
| NVDA | 0.49 | 48.0 | Avoid / Wait |
| AAPL | 0.29 | 41.4 | Avoid / Wait |
| KO | 0.29 | 49.9 | Avoid / Wait |
| PLTR | 0.36 | 38.2 | Avoid / Wait |

Market sale **5.2–5.8 de 20 en todos**, sin importar la empresa. Ninguna categoría
llega al 70% de cobertura que exige el override, así que **mientras el judge no
corra, ninguna empresa puede pasar un gate de perfil**. El motor se comporta bien
—se niega a puntuar lo que no puede medir— pero la salida no diferencia entre
compañías.

**Hay una segunda vía además de los créditos:** NVDA saca 0.49 y AAPL/KO sacan
0.29. La diferencia es exactamente `Entradas/NVDA.json`. Llenar ese archivo sube la
cobertura sin depender del judge — y la lista de A-03 dice qué claves faltan (21
para NVDA, cubriendo 25 métricas).

---

# 9. Tercera auditoría del área `analyze` — 2026-08-01

Esta pasada fue a lo que **no** se había escudriñado: los casos de aceptación
del propio Victor.

## 9.1 A-05 — 20 de los 57 casos de Victor no tenían test que los nombrara

Cada `Cerebro/*/VALIDATION_TESTS.md` declara una tabla numerada: entradas a la
izquierda, resultado exigido a la derecha. Son su definición de "funciona".

Veinte no aparecían citados en `engine/tests/`. **El comportamiento era
correcto** —los verifiqué a mano uno por uno y todos pasaban— pero nada ataba
el comportamiento al caso, así que una regresión habría salido como un fallo
anónimo en otro sitio, o no habría salido.

`engine/tests/test_victor_validation_cases.py` los encodea citando el ID. Ahora
**57/57**, y una ruptura dice qué caso de Victor se rompió.

| Caso | Qué exige |
|---|---|
| VAL-T002/T003 | `g >= WACC` se rechaza (denominador cero o negativo) |
| VAL-T004 | reinversión terminal = `g / ROIC` |
| VAL-T005 | puente de deuda: EV 1000 → equity 800 → $10/acción |
| VAL-T006 | probabilidades que suman 1 pasan; las que no, se rechazan |
| VAL-T007 | valor terminal 80% del EV → bandera de alta sensibilidad |
| VAL-T009 | FCFF y beneficio económico reconcilian bajo los mismos supuestos |
| TECH-T002 | true range abarca el cierre previo |
| TECH-T004 | dos máximos a 2 sesiones son **un** toque (exige ≥5) |
| TECH-T009 | distancia en ATR, **con signo** desde el precio |
| TECH-T010 | historia sin ajustar → packet técnico rechazado (`ERROR`, cobertura 0) |
| TECH-T011 | un pivote no se conoce hasta k sesiones después (sin look-ahead) |
| TECH-T012 | sin volumen la dimensión no puntúa; no se inventa un valor medio |
| FIN-T006 | pérdida + FCF negativo + emisión → Override 1 |
| FIN-T007 | ROIC < WACC bloquea Excellent; entradas ausentes **no** lo disparan |
| FIN-T008 | 27 métricas Excellent = 54/54 = 100% |
| MKT-T006 | pronóstico por encima del TAM falla el gate de consistencia |
| MKT-T007 | TAM tier 4 (confianza 45) capa la dimensión |
| MKT-T008 | consenso posterior al reporte no puede medir sorpresa |
| RSK-T008 | Risk 4/15 con raw 90 → capado a Speculative pese a banda Elite |
| RSK-T009 | M-score forense es una cifra, no una acusación |

## 9.2 Errores míos que el chequeo atrapó

Vale registrarlos porque muestran por qué no basta con leer el código:

- Probé `VAL-T003` con los argumentos al revés (`gordon_terminal_value` es
  `(fcff, g, wacc)`, no `(fcff, wacc, g)`) y **casi reporto un bug que no
  existía**. El motor estaba bien.
- Inventé siete nombres de función que no existen (`tam_consistency_ok`,
  `beneish_m_score_flag`, `_PIVOT_K`…). Los reales son
  `forecast_consistency_gate`, `beneish_m_score`, `find_pivots(...).confirmed_index`.
- Asumí que RSK-T008 necesitaba el objeto `Override` para capar. **No**: el gate
  lee el score de riesgo directamente y capa solo con eso — el motor es más
  estricto de lo que supuse.

## 9.3 Estado

**2091 tests del engine + 49 de la capa web.** 57/57 casos de Victor,
207/207 métricas, 0 sin documentar.

Sigue abierto **V-03** (sin saldo en Anthropic) con su consecuencia de §8.3:
`OVERRIDE_6` en 4 de 4 tickers, ninguna empresa puede pasar un gate de perfil.

---

# 10. Auditoría contra el repo real de Victor — 2026-08-01

Esta pasada clonó `infusionvictor/warren-buffett-jr` y comparó archivo por
archivo, además de correr **sus 36 archivos de test contra este motor**.

## 10.1 Lo que es idéntico a Victor

| Área | Resultado |
|---|---|
| **Cerebro (la metodología)** | **83/83 archivos idénticos byte a byte** |
| Superficie pública del motor | **0 funciones/clases suyas eliminadas** |
| Archivos suyos ausentes | **ninguno** — este repo es un superconjunto (64 .py vs 51) |
| `.claude/agents` | 7/7 presentes, 6 idénticos (risk-analysis apunta a Kevin.md a propósito) |

Su repo lleva sin tocarse desde el **18 de julio**, dos semanas antes de este
trabajo.

## 10.2 Sus tests contra este motor: 702 pasan, 29 fallan

Los 29 se reparten así:

| Causa | N | ¿Es un problema? |
|---|---|---|
| Idioma (sus tests esperan narrativa en español; el motor responde en inglés) | 6 | No — el motor se internacionalizó (`i18n.py`) |
| Claves de caché cualificadas por parámetros | 6 | No — viene del commit de port, evita colisiones reales |
| Adapters que esta copia **implementa** en vez de rechazar (bancos → residual income) | 3 | No — es una mejora sobre su rechazo |
| Modelo del judge `opus-4-8` → `opus-5` | 1 | No — upgrade deliberado |
| Comportamientos que esta auditoría corrigió (precio liquidado, dilución 3a) | 4 | No — sus tests codifican lo viejo |
| IDs de petición del judge distintos | 4 | No — internamente consistentes (verificado abajo) |
| **Regresiones reales** | **2** | **Sí — corregidas** |
| Otros (memoria, quick, brief) | 3 | Divergencias del port |

## 10.3 A-06 — un adapter desconocido se valuaba con el modelo convencional

`industry_adapter="bank_adapter"` —la grafía de Victor en VAL-T010, donde este
motor dice `"banks"`— no caía en ninguna rama y producía
`primary=['FCFF_DCF','ECONOMIC_PROFIT']` **para un banco**: exactamente el modelo
que la matriz de DECISION_RULES.md prohíbe para ese tipo de empresa.

`business.py` ya trataba un adapter sin clasificar como no fiable (le pone piso
al model-fit: *"claiming a good fit for it would be an assertion without
evidence"*). Valuación, que es donde más daño hace, no lo hacía.
`_adapters.is_classified()` cierra el hueco: cualquier nombre que ninguno de los
tres conjuntos clasifique se rechaza igual que un adapter que reemplaza el modelo.

## 10.4 A-07 — un techo tratado como banda obligatoria (bug mío)

`profile_fit` comprobaba `lo <= pos <= hi`. Con el **"Máximo por posición
individual: 20% – 30%"** de Kevin.md, una posición del **10% reportaba
`within_position_cap = False`** — como si dimensionar conservador incumpliera un
máximo. Lo introduje yo al cambiar el rango a 20–30% sin revisar cómo se
consumía; estaba latente con el (0.05, 0.20) por defecto, donde casi cualquier
posición real superaba el 5%. Ahora el incumplimiento se mide contra el techo, y
estar por debajo del rango se reporta aparte como información.

## 10.5 CORRECCIÓN: los créditos NO desbloquean Market

En §8.3 escribí que el judge caído le costaba a Market 13 de sus 20 puntos.
**Es incorrecto y lo corrijo.** Simulé las 8 respuestas del judge y las pasé por
`merge_overlay`:

| categoría | antes | después |
|---|---|---|
| Financial | 10.08 | **12.48** (cobertura 0.92 → 1.00) |
| Business | 11.39 | 11.39 |
| **Market** | **5.17** | **5.17** (cobertura **0.49 → 0.49**) |
| Technical / Risk / Valuation | sin cambio | sin cambio |
| **TOTAL** | 47.95 | **50.35** (+2.40) |

Market le hace al judge **una sola petición**
(`three_growth_thesis_killers`), que es narrativa y no alimenta ninguna
dimensión puntuada. Sus **14 métricas sin puntuar salen todas de
`Entradas/<TICKER>.json`**: SAM, SOM, cuota de mercado, HHI, runway, catalizadores,
adopción, ARPU, escenarios.

**Consecuencia práctica:** cargar créditos suma ~2.4 puntos y llena los thesis
killers, pero **no mueve la cobertura de Market**, así que `OVERRIDE_6` sigue
disparando y ninguna empresa pasa un gate. Lo que levanta esa cobertura es
llenar `Entradas/`. La lista de A-03 dice qué claves faltan.

## 10.6 Estado

**2107 tests del engine + 49 de la capa web.** 57/57 casos de aceptación,
207/207 métricas, Cerebro 83/83 idéntico.

---

# 11. Auditoría de las 67 rutas web — 2026-08-01

Se enumeraron las 67 rutas, se escanearon por AST y se ejercitaron **todas**
contra un servidor real (`TestClient`).

## 11.1 W-01 — tres GET que cambiaban estado

| ruta | qué hacía |
|---|---|
| `/api/report-delete` | `DELETE FROM reports` |
| `/api/scheduler/run-now` | lanzaba un hilo de colección |
| `/api/backfill/start` | lanzaba un hilo de backfill |

HTTP define GET como **seguro**: navegadores, prefetchers, escáneres de enlaces
y la caché de ida/vuelta pueden reemitirlo solos. La cookie es `SameSite=Strict`,
que corta el caso entre sitios, pero la reemisión dentro del propio sitio no
depende de eso — y `<img src=".../api/report-delete?report_id=X">` bastaba.
Las tres pasaron a **POST**, con la UI actualizada.

## 11.2 W-02 — QuantData llevaba muerta en silencio

`QUANTDATA_BASE` tenía un `/v1` que la API no usa. **Todas** las rutas devolvían
404 (`"No resource found at 'v1/option/flow'"`), así que el flujo de opciones,
el dark pool y el GEX no funcionaban — y nadie se enteraba, porque
`_quantdata_request` devuelve `{"_error": ...}` y los llamadores degradan callados.

Cada `/api/analyze` gastaba **25 peticiones y ~8 s** contra rutas inexistentes.

Sin el sufijo responden **403** (existen; el plan no las cubre), que es un hecho
distinto. Añadido `_QD_SIN_DERECHO`: un rechazo de permisos no se reintenta en la
misma corrida.

**Medido:** 25 peticiones / 8.1 s → **8 peticiones / 2.2 s**.

## 11.3 W-03 — `/api/analyze` tarda 92–115 s

Medido tres veces (frío y caliente), sobre NVDA y AAPL. Perfilado por host:

| origen | segundos |
|---|---|
| **Gemini (narrativa)** | **50.9** (2 llamadas) |
| QuantData | 8.1 → 2.2 tras W-02 |
| FinnHub + SEC + FMP + Yahoo | ~6.6 |
| motor + agregación | el resto |

**La mitad del tiempo es la narrativa del LLM, que es presentación, no cálculo.**
Los números del motor están listos mucho antes.

Esto importa para el despliegue: Render y la mayoría de proxies cortan entre 30 y
100 s, así que **el endpoint principal probablemente agote el tiempo de espera en
producción**. No lo arreglé porque la solución (devolver los números primero y la
narrativa después, o en streaming) es un cambio de diseño, no una corrección de
error. Queda medido y sobre la mesa.

## 11.4 Verificado sano

- **60 de 67 rutas tras autenticación**; las 7 públicas son las que deben serlo
  (`/`, `/legacy`, `/wbj`, login, auth-status, manifest, favicon).
- Cookie `httponly` + `samesite=strict`.
- `serve_icon` usa **lista blanca** de tamaños — sin traversal de rutas.
- SQL siempre parametrizado — sin inyección.
- **58 de 59 rutas responden sin 5xx.** La única 500 fue
  `/api/plaid/exchange-token` con cuerpo vacío, que es entrada inválida (aunque
  debería contestar 4xx, no 5xx).
- **`str(e)` al cliente en 26 rutas**: comprobé si podía filtrar claves.
  **No.** `/api/news` va a Yahoo sin clave, y Plaid manda credenciales por
  **cabeceras**, no en la URL. Filtra detalle interno (rutas, SQL), no secretos.
  Corregido en `/api/report-delete`; el resto queda como endurecimiento menor.

## 11.5 Estado

**2107 tests del engine + 56 de la capa web** (7 nuevos de seguridad de rutas).

---

# 12. W-03: latencia de `/api/analyze` — 2026-08-01

## 12.1 Dónde se iba el tiempo (medido, no estimado)

Instrumenté el cliente de Gemini para atribuir cada llamada a su llamador:

| origen | segundos | ¿lo consume alguien? |
|---|---|---|
| `_analyze_structured` (reporte estructurado) | **34.5** | sí — es el cuerpo de la respuesta |
| `_wbj_explain` (narrativa en palabras) | **18.4** | **NO — cero usos en la plataforma** |
| QuantData (25 peticiones a rutas 404) | 8.1 | no (W-02) |
| motor + resto de fuentes | ~45 | sí |

## 12.2 El hallazgo: se pagaban 18.4 s por un campo que nadie lee

`grep wbj_explanation` sobre `vertex_fund_os_platform.html` da **0 usos**. La API
lo generaba en cada llamada y ninguna pantalla lo mostraba.

Ahora va detrás de **`?explain=1`**. La capacidad sigue ahí; se paga si se pide.

## 12.3 Resultado

| | antes | ahora |
|---|---|---|
| `/api/analyze` (media de 3) | 92–115 s | **75.5 s** (69.4 / 61.7 / 95.2) |
| peticiones QuantData | 25 (8.1 s) | 8 (2.2 s) |

**Honestamente: sigue siendo lento.** La varianza del LLM es alta y el pico de
95 s roza el corte de Render. Lo que queda —`_analyze_structured` (34.5 s) más el
motor (~40 s)— es coste estructural: bajarlo de verdad exige devolver los números
primero y el LLM después (trabajo en segundo plano + sondeo desde la UI), que es
un cambio de diseño de la interfaz, no una corrección puntual. Queda medido y
documentado; no lo hice unilateralmente porque cambia el contrato de la respuesta.

## 12.4 Sobre las 26 rutas con `str(e)`

Verificado que **no filtran claves** (§11.4). Corregido en `/api/report-delete`,
que es la que borra. El resto queda como endurecimiento menor pendiente: cambiar
26 rutas a la vez, sin poder validar cada una en vivo, arriesga más de lo que
aporta hoy.

---

# 13. Verificación de los tres puntos no-100% — 2026-08-01

## 13.1 Endpoints de pago: son límites de plan REALES

Tras el caso de QuantData (un `/v1` de más disfrazado de "sin permisos"), verifiqué
los otros probando rutas alternativas:

| endpoint | código | veredicto |
|---|---|---|
| FMP `institutional-ownership/*` (4 variantes) | **402** | *"Restricted Endpoint: not available under your plan"* — real |
| FinnHub `eps-estimate`, `revenue-estimate`, `price-target` | **403** | *"You don't have access to this resource"* — real |
| FinnHub `stock/recommendation` | **200** | **sí funciona** — el plan gratis lo incluye |

**No son bugs.** Y dos de los tres **ya están cubiertos por otra fuente**:

- FMP institutional-ownership → cubierto por el **dataset 13F de la SEC** (10 tenedores
  reales en el reporte).
- FinnHub eps/revenue-estimate → cubierto por **FMP analyst-estimates** (200, 10 filas,
  es lo que alimenta el crecimiento base de V-02).

Sólo **QuantData** (flujo de opciones, dark pool, GEX) queda sin sustituto.

## 13.2 Cobertura de Market: mi lista pedía trabajo YA HECHO

`Entradas/NVDA.json` documenta decisiones de investigación cerradas:

- `_share_no_scorable`: *"MKT-SHARE-006 y MKT-SHDELTA-007 quedan NOT_SCORABLE tras
  research (2026-07-19)"*
- `_advertencia_capa_de_canal`: *"Gartner mide gasto del USUARIO FINAL; NVDA vende
  aguas arriba a OEM/ODM/CSP"* — dividir ingresos entre ese TAM compara **capas
  distintas de la cadena de valor**
- `_recurring_ausente`: *"NVDA NO divulga porcentaje de ingresos recurrentes en su 10-K"*
- `_adopt_no_scorable`: la única fuente con ambos números no es citable

El motor tiene razón al negarse, y **Kevin ya lo había resuelto**. Mi lista de A-03
las pedía igualmente: convertía un hallazgo cerrado en una tarea eterna.

`_researched_and_declared()` lee esas notas y separa las dos cosas:

| | antes | ahora |
|---|---|---|
| pendientes de verdad | 21 claves | **18 claves** |
| ya investigadas y declaradas | mezcladas dentro | **4, reportadas aparte** |

También comprobé si alguna métrica N/S se puede alimentar con datos que ya traemos:
**`MKT-DISP-013` no.** Exige `stdev(estimaciones individuales)`, y FMP da low/avg/high
+ recuento, no las individuales. Sacar una desviación típica de un rango exigiría
asumir una distribución — que es justo la imputación que el motor prohíbe.

## 13.3 Latencia: bajada y con el resto explicado

Ver §12. De 92–115 s a **75.5 s de media**. Lo que queda es coste estructural
(`_analyze_structured` 34.5 s + motor ~40 s) y bajarlo exige el rediseño asíncrono.

## 13.4 Porcentajes corregidos

| Área | Antes dije | Real | Por qué |
|---|---|---|---|
| Endpoints de pago | ~60% | **~85%** | 2 de 3 ya cubiertos por otra fuente; sólo QuantData sin sustituto |
| Cobertura de Market | ~35% | **~35% y correcto** | lo que falta es research que Kevin declaró imposible, no un fallo |
| Latencia | ~50% | **~65%** | 75.5 s vs 92–115 s |

---

# 14. ¿Falta algo que Victor sí tenga? — 2026-08-01

Pregunta directa de Kevin: *"si Victor lo tiene con esos es porque funcionan"*.
Cloné su repo y comparé proveedor por proveedor.

## 14.1 FinnHub: el proveedor de Victor es IDÉNTICO al nuestro

Mismos 4 métodos (`estimates`, `revenue_estimates`, `earnings_calendar`, `quote`),
mismas rutas (`stock/eps-estimate`, `stock/revenue-estimate`, `calendar/earnings`,
`quote`). **Victor llama exactamente los dos endpoints que devuelven 403.** Su
código no funciona mejor; su plan sería el mismo o de pago.

Mapa del plan de Kevin: **10 accesibles, 6 bloqueados**.

| accesibles | bloqueados (403) |
|---|---|
| quote, profile2, metric, calendar/earnings, recommendation, earnings, insider-transactions, company-news, financials-reported, peers | price-target, **eps-estimate**, **revenue-estimate**, candle, ownership, social-sentiment |

## 14.2 FMP: nosotros llamamos MÁS que Victor

| | Victor | nosotros |
|---|---|---|
| endpoints FMP | 10 | **13** |
| extra nuestros | — | `key-executives`, `revenue-product-segmentation`, `revenue-geographic-segmentation` |
| `institutional-ownership` | **sí lo llama** (mismo 402) | sí, **+ respaldo 13F de la SEC que él no tiene** |

Mapa del plan de pago ($29): **24 accesibles, 3 bloqueados**.

Bloqueados: `institutional-ownership/extract-analytics/holder`,
`institutional-ownership/symbol-positions-summary`, y `analyst-estimates` con
`period=quarter` (el anual funciona).

## 14.3 Conclusión: no falta nada de Victor

**Los 402/403 no son un fallo de configuración ni algo que Victor tenga resuelto.**
Son límites de plan que él también tendría — llama los mismos endpoints. Y de los
tres, **dos ya están tapados por otra fuente**:

| bloqueado | sustituto | ¿se pierde algo? |
|---|---|---|
| FMP institutional-ownership | dataset 13F de la SEC | **no** — y Victor NO tiene este respaldo |
| FinnHub eps/revenue-estimate | FMP analyst-estimates (200) | **no** — es lo que alimenta V-02 |
| QuantData | ninguno | **sí** — flujo de opciones, dark pool, GEX |

El reporte ahora lo dice: un endpoint tapado se lee *"the data is already sourced
from X, so nothing is lost"*, y uno sin sustituto conserva el aviso de pérdida.
Son dos acciones distintas: no hacer nada, o subir de plan.

## 14.4 Lo que el plan de pago da y NO usamos

`key-metrics`, `ratios`, `enterprise-values`, `grades-consensus`,
`price-target-consensus`, `price-target-summary`, `financial-scores`,
`owner-earnings`, `discounted-cash-flow`, `shares-float`, `market-capitalization`.

**No los cableé a propósito.** Ninguno cierra una métrica que el Cerebro defina y
que hoy esté N/S, y añadirlos significaría meter métricas que Victor no registró —
el motor tiene hoy **0 ids que el Cerebro no documente** y eso debe seguir así.
Los ratios y el DCF de FMP además los calcula el motor por su cuenta, que es
justo la regla de las dos capas.

---

# 15. Los tres puntos que no llegaban al 100% — 2026-08-01

## 15.1 Punto 13 (endpoints bloqueados) → **100% vs Victor**

El único sin sustituto era QuantData. Verificado:

- **No está en el repo de Victor** (0 coincidencias en su código y docs).
- **No aparece en el Cerebro** (0 coincidencias en los 83 documentos).
- **El motor no lo usa** (0 referencias en `engine/wbj/`).

QuantData alimenta un panel extra de la plataforma web (flujo de opciones, dark
pool, GEX) — una adición de Vertex, **no parte del sistema de Victor**. Respecto a
él, todas las fuentes que su metodología necesita funcionan, y encima tenemos el
dataset 13F de la SEC que él no tiene.

## 15.2 Punto 14 (latencia) → de 75.5 s a **33.4 s en repeticiones**

El motor es **determinista** y el packet está anclado a una sesión ya cerrada
(`market_timestamp`, ver V-05): mismo ticker + misma sesión ⇒ mismo resultado bit
a bit. Recalcularlo costaba ~40 s de pandas en cada llamada.

Dos cachés, ambas con la **misma clave**: el reloj congelado del packet.

- `_ENGINE_CACHE` — el scorecard de los 6 especialistas.
- `_LLM_CACHE` — el pase estructurado, que describe esos números ya congelados
  (se guarda una **copia**, porque el llamador reescribe `fair_value` y targets
  sobre el dict).

Se invalidan solas: al abrir una sesión nueva cambia la clave.

| | antes | ahora |
|---|---|---|
| primera llamada | 92–115 s | **67.1 s** |
| repeticiones | 92–115 s | **33.4 s** (34.1 / 32.7) |

Verificado que no hay regresión: `fair_value` 281.05, `upside` 40.0%,
recomendación `DESFAVORABLE` — idénticos con y sin caché.

## 15.3 Punto 15 (cobertura de Market) → **es correcto, no es un defecto**

Verificado contra la política del Cerebro, no contra intuición:

1. **La fórmula del motor es literalmente la suya.**
   `MISSING_DATA_POLICY.md`: `coverage = valid_metric_weight / applicable_metric_weight`.
   El motor: `sum(max_points × valid_weight) / sum(max_points × applicable_weight)`.
2. **Imputar cuota de mercado está PROHIBIDO explícitamente.** La lista de
   "Prohibited imputation" nombra *market share* junto a concentración de clientes,
   crecimiento orgánico y revisiones de estimados.
3. **`NOT_APPLICABLE` (paso 1) es para métricas que no aplican a la EMPRESA** — como
   las de suscripción en una fabricante de chips. La cuota de mercado sí aplica
   conceptualmente a NVDA; lo que falta es una base de TAM comparable. Eso es el
   paso 5: `NOT_SCORABLE`. El motor acierta.

Subir esa cobertura al 100% exigiría **violar la política**: inventar una cuota
dividiendo los ingresos de NVDA entre un TAM que mide otra capa de la cadena.

**Una cobertura de 0.35 en Market no mide lo incompleto que está el agente —
mide lo poco que NVDA publica con fuente citable.** Que el sistema lo diga en vez
de rellenarlo es exactamente *sin evidencia, no hay número*.

---

# 16. Punto 14 al 100%: la latencia era trabajo repetido — 2026-08-01

## 16.1 El perfil dijo otra cosa de la que yo suponía

Con las cachés del motor y del LLM ya activas quedaban 33 s, y yo asumía que
eran red o LLM. **No.** Instrumentado por host: sólo **5.7 s eran red**. Un
`cProfile` sobre la petición cacheada señaló al culpable:

```
55.4 s  analyze_ticker
47.5 s  └─ fetch_edgar_filings   (2 llamadas × 23.8 s)
```

**El 86% del tiempo era una sola función, invocada dos veces.**

`fetch_edgar_filings` recorre el conjunto trimestral 13F de la SEC — un TSV
enorme — y `/api/analyze` la llamaba una vez para el contexto de insiders y otra
para `mandatory_report.edgar`. Las presentaciones no cambian entre ambas.

## 16.2 Tres cachés, todas ancladas a algo que no puede cambiar

| caché | qué guarda | clave | por qué es correcta |
|---|---|---|---|
| `_ENGINE_CACHE` | scorecard de los 6 especialistas | reloj congelado del packet | el motor es determinista y la sesión ya cerró |
| `_LLM_CACHE` | pase estructurado del LLM | el mismo reloj | describe números ya congelados |
| `_EDGAR_CACHE` | presentaciones de la SEC | (ticker, límite) + TTL 30 min | los filings no cambian entre dos llamadas de la misma petición |

Las tres acotan su tamaño y devuelven **copias**, porque el llamador reescribe
`fair_value` y los targets sobre el dict — servir la misma referencia dejaría que
una petición mutara lo que ve la siguiente.

## 16.3 Resultado

| | original | ahora |
|---|---|---|
| primera llamada | 92–115 s | **41.8 s** |
| repeticiones | 92–115 s | **6.5 s** |

Verificado sin regresión en las tres corridas: `fair_value` 281.05,
recomendación `DESFAVORABLE`, idénticos.

**Ambos números caben de sobra en el corte de Render** (que era el motivo de
preocupación). El objetivo de <30 s se cumple en repeticiones con margen de 4×, y
la primera llamada baja de 115 s a 42 s.

---

# 17. Auditoría completa del proyecto — 2026-08-02

Barrida mecánica de todo, con evidencia medida.

## 17.1 Estructura y sintaxis

| comprobación | resultado |
|---|---|
| archivos `.py` que compilan | **204 / 204** |
| módulos de `wbj` que importan | **todos, 0 fallos** |
| docs del Cerebro citados por el código que no existen | **0** (`CLAUDE.md` y `MEMORIA.md` viven fuera de `Cerebro/`) |

## 17.2 Conexiones

| comprobación | resultado |
|---|---|
| endpoints que la UI llama y la API no expone | **0 de 53** |
| ids del DOM que el JS toca y el HTML no define | 3 — **los tres se crean dinámicamente y están guardados** (`\|\| create`, `if (!c)`, `if (exists)`) |
| endpoints de la API sin uso en la UI | 5 (`logout`, `plaid/disconnect`, `finnhub-quote`, `quantdata/*`) — superficie legítima, no código muerto |

## 17.3 Datos y cálculos

| comprobación | resultado |
|---|---|
| NaN / Infinity en el reporte | **0** |
| serializable a JSON estricto (`allow_nan=False`) | **OK** |
| suma de categorías vs `raw_score` | 47.9095 = 47.9095 **exacto** |
| puntos dentro de `[0, max]` y confianzas en `[0,100]` | **todos** |
| fórmulas verificadas contra aritmética a mano | **10 / 10** |

Sobre las fórmulas: mi primera pasada marcó FCFF como discrepante. **Era mi error**:
pasé el capex negativo cuando la fórmula del Cerebro es `EBIT(1−t) + D&A − Capex − ΔNWC`,
con capex como magnitud positiva. Los llamadores del motor hacen `abs()` y el test
`test_fcff_reconciles_with_nopat_minus_reinvestment` fija la convención con
`capex=45.0`. El motor estaba bien.

## 17.4 Sub-agentes y memoria

Los **7** apuntan a su carpeta del Cerebro, declaran sus puntos y sus herramientas.
Memoria: `MEMORIA.md`, `calibracion.md`, `errores.md`, 2 tesis por ticker,
**10 predicciones** guardadas.

## 17.5 End-to-end sobre cinco formas de empresa

| ticker | total | perfil | niveles | gráficas | 13F | insiders |
|---|---|---|---|---|---|---|
| NVDA | 47.9 | Avoid / Wait | 40 | 4 | 10 | 10 |
| AAPL | 41.4 | Avoid / Wait | 36 | 4 | 10 | 9 |
| KO | 50.1 | **Speculative** | 36 | 4 | 10 | 24 |
| JPM | 36.3 | Avoid / Wait | 17 | **3** | 10 | 12 |
| PLTR | 38.2 | Avoid / Wait | 60 | 4 | 10 | 10 |

Las etiquetas **sí diferencian** (KO llega a Speculative). JPM trae 3 gráficas porque
no lleva football field — correcto: la matriz de modelos veta el DCF para bancos.

## 17.6 Veredicto

**Cero fallos abiertos en el código.** Los tres "hallazgos" de esta pasada resultaron
falsos positivos de mis propias comprobaciones (docs fuera de `Cerebro/`, ids creados
en tiempo de ejecución, mi signo del capex). Queda únicamente el saldo de Anthropic,
que no es código.

---

# 18. Las comprobaciones de la auditoría, ahora permanentes — 2026-08-02

## 18.1 Aclaración sobre §17

Dos de las filas de §17 se leyeron como problemas y **son el resultado correcto**:

| fila | lectura |
|---|---|
| `endpoints huérfanos: 0 de 53` | **cero es lo bueno** — la UI nunca llama una ruta inexistente |
| `NaN / Infinity: 0` | **cero es lo bueno** — ningún número corrupto en el reporte |

Y los tres "hallazgos" fueron errores de mis propios scripts de comprobación, no
del código. No había nada que arreglar en ninguno de los tres casos.

## 18.2 Lo que sí faltaba: que esas comprobaciones no se pierdan

Corrían como scripts sueltos en un directorio temporal. **Un invariante que sólo se
comprueba a mano no es un invariante, es una foto.** Victor codifica los suyos como
tests; estos ahora también.

`tests_vertex/test_project_invariants.py` — 11 tests:

| test | defecto que cubre |
|---|---|
| todo `.py` compila | un archivo roto tumba el arranque |
| todo módulo de `wbj` importa | un import roto sólo aparece al llamar esa ruta |
| la UI no llama rutas inexistentes | un `fetch` a 404 deja la pantalla vacía sin decir por qué |
| *(guarda)* la UI llama >30 rutas | si el patrón deja de encajar, el test de arriba pasaría vacío |
| todo doc citado existe | una regla que cita un archivo inexistente queda sin respaldo |
| todo id del DOM es alcanzable | `getElementById` nulo → *"cannot read properties of null"* |
| ningún especialista publica NaN/Inf | `NaN` no es JSON válido: el `JSON.parse` del cliente lanza |
| la salida sobrevive `allow_nan=False` | la misma regla que aplica un cliente HTTP |
| los puntos no salen de `[0, max]` | rompería la suma contra el `raw_total` que leen los gates |
| `fcff` toma el capex positivo | con el signo del estado financiero el capex SUMA |
| el especialista normaliza el signo | la defensa real: da igual cómo venga del proveedor |

Corren **sin red**, sobre el packet golden.

## 18.3 Cada uno arregla la trampa que me hizo caer

Las tres versiones nuevas evitan explícitamente mis falsos positivos:

- los documentos se buscan en **todo el repo**, no sólo en `Cerebro/`;
- un id del DOM vale si el HTML lo declara **o** si el script lo crea **o** si el uso
  está guardado;
- `fcff` se prueba con el capex **positivo**, y hay un caso que demuestra que con el
  signo invertido el resultado se infla en `2 × capex`.

## 18.4 Verificado por mutación

No basta con que pasen. Roto el invariante a propósito, los tres detectan:

- ruta inventada en la UI → `['/api/ruta-que-no-existe']`
- `NaN` e `inf` inyectados → `x.cat.score=nan`, `x.otro[1]=inf`
- `allow_nan=False` rechaza el `NaN`

**2111 tests del engine + 71 de la capa web.**

---

# 19. Puntos 3 y 4, resueltos mirando qué hace Victor — 2026-08-02

Antes de diseñar nada, cloné su repo y leí `engine/scripts/webapp.py`.

## 19.1 Lo que Victor hace

| | Victor | nosotros (antes) |
|---|---|---|
| `/api/analyze` | **síncrono**, bajo un lock global | síncrono, **sin lock** |
| errores al cliente | `self._json({"error": str(e)}, 500)` | `{"error": str(e)}` |
| bind | **`127.0.0.1`** | `0.0.0.0` (Render) |
| caché a nivel de análisis | ninguna | 3 (motor, LLM, EDGAR) |

Su comentario, literal: *"One analysis at a time: providers share one httpx
client/cache."*

## 19.2 Punto 3 (latencia): su respuesta es "no lo difieras"

Victor **no** separa los números del LLM. Corre todo síncrono y liga el servidor a
localhost, donde 90 s no molestan a nadie porque no hay proxy que corte.

Así que el rediseño asíncrono **no es "como Victor lo tiene"** — es lo contrario.
Y nuestra versión ya es más rápida que la suya: él no cachea nada a nivel de
análisis; nosotros vamos a **6.5 s en repeticiones** y 42 s en frío.

Lo que sí le faltaba a nuestra copia era **su lock**. Adoptado. Y ahora importa más
que a él: desde que `/api/analyze` memoiza el scorecard, el pase del LLM y las
presentaciones de EDGAR, dos peticiones simultáneas competirían por esos tres
diccionarios — la segunda podría leer una entrada a medio escribir, o duplicar
40 s de trabajo que la primera ya está haciendo.

**Verificado:** 3 peticiones concurrentes se serializan (6.7 / 13.2 / 20.4 s) y
las tres devuelven lo idéntico — `fair_value` 281.05, `DESFAVORABLE`.

## 19.3 Punto 4 (`str(e)`): su elección es segura en SU contexto

Victor devuelve la excepción cruda, y en su caso es inofensivo: el único que la lee
es él, porque su servidor nunca sale de `127.0.0.1`.

El nuestro arranca con `--host 0.0.0.0` en Render, de cara a internet. Ahí ese
mismo texto puede llevar rutas del servidor, fragmentos de SQL y —si algún día una
excepción de httpx escapara de `raise_for_status()`— la URL completa **con la clave
en la query**. Hoy verifiqué que ninguna ruta está en ese último caso; el cambio es
para que siga siendo cierto sin depender de revisarlo cada vez.

**14 ocurrencias** sustituidas por `_error_publico(exc, contexto)`: el detalle va al
log con `exc_info=True`, y al navegador una frase que no revela nada.

**Es su mismo razonamiento aplicado a un contexto distinto** — él eligió localhost
precisamente para que sus internos quedaran privados—, no una desviación de su
metodología.

## 19.4 Estado

**2111 tests del engine + 75 de la capa web** (4 nuevos: el lock, la ausencia de
fugas, que el helper sí registre lo que oculta, y el bind público como el hecho que
justifica ambas decisiones).

---

# 20. Más paridad con la capa web de Victor — 2026-08-02

El lock ya estaba (§19). Releyendo `engine/scripts/webapp.py` aparecieron dos
patrones suyos más que esta copia no tenía.

## 20.1 Un cliente httpx por proceso

Victor: `edgar = EdgarProvider(settings, Cache(settings.cache_dir))` **a nivel de
módulo**, instanciado una vez y reutilizado toda la vida del servidor.

Esta copia hacía lo contrario. `Provider.__init__` llamaba a `httpx.Client()` cada
vez que nadie le pasaba uno, y `build_providers` se invoca **siete veces** entre
`deep`, `report` y la capa web.

**Medido sobre un `run_report` real: 22 clientes.** Cada uno con su propio pool, así
que ninguna conexión se reutilizaba entre proveedores —handshake TCP+TLS nuevo en
cada llamada— y ninguno se cerraba explícitamente.

Dos cambios:

- `build_providers` memoizado por `(cache_dir, repo_root)`, devolviendo el mismo
  juego con el mismo cliente.
- `Provider.__init__` cae a un **cliente compartido perezoso** en vez de crear uno.
  Arreglarlo en la raíz cubre también los proveedores que se construyen por otras
  rutas, sin perseguir llamadores.

Un cliente explícito sigue mandando — los tests inyectan `MockTransport` y no se ven
afectados.

| | antes | ahora |
|---|---|---|
| clientes en el 1er reporte | **22** | **4** |
| clientes en el 2º reporte | 22 | **2** |

Los 2 restantes son del SDK de Anthropic (el judge y la tesis ejecutiva), que no son
nuestros para agrupar.

## 20.2 Lo que NO copié, y por qué

Victor serializa **dos** rutas: `/api/screen` y `/api/analyze`. Nosotros serializamos
`/api/analyze`. Tenemos ocho rutas pesadas más (`/api/explore`, `/api/backtest`,
`/api/watchlist-radar`…) que siguen sin lock.

**No las serialicé.** Con un solo lock global, encadenar ocho rutas más convierte
cualquier panel lento en un bloqueo de toda la aplicación — y ninguna de esas ocho
toca las tres cachés de `/api/analyze`, que era la razón concreta para serializar.
Queda anotado como decisión consciente, no como olvido.

## 20.3 Estado

**2115 tests del engine + 75 de la capa web.**

---

# 21. La caché de proveedores se escribía a medias (W-04)

Al revisar si esas ocho rutas necesitaban lock, lo que apareció no fue un problema
de rutas: era la caché compartida. `Cache.put` escribía así:

```python
path.write_text(json.dumps(record), encoding="utf-8")
```

`write_text` abre **truncando**. Entre el truncado y el volcado el archivo está
vacío en disco. Y la aplicación corre **cuatro hilos de fondo** que escriben esa
misma caché mientras las peticiones en vivo la leen:

| Hilo | `vertex_api.py` |
|---|---|
| índice de FMP | `_fmp_cargar_indice` |
| backfill | `_run_backfill` |
| planificador | `_scheduler_loop` |
| colección bajo demanda | `_run_daily_collection` |

**Por qué no se veía.** `_read_record` devuelve `None` ante un JSON roto, así que
la carrera no se manifestaba como un error: se manifestaba como una petición más a
la API. Cuota gastada para recuperar algo que ya estaba guardado.

**Medido** (3 escritores + 3 lectores, 1.5 s, misma entrada):

| | lecturas perdidas |
|---|---|
| `write_text` (original) | **71** |
| temporal + `os.replace` | **0** |

## 21.1 El arreglo no era el lock

Serializar ocho rutas habría tapado el síntoma en la web y dejado el defecto vivo
en los cuatro hilos de fondo, que no pasan por ninguna ruta. La escritura atómica
—temporal en el MISMO directorio y luego `os.replace`, atómico en POSIX y en
Windows— lo arregla en la raíz y cubre todos los llamadores a la vez, sin
encadenar la aplicación. **Sigue sin haber lock en esas ocho rutas, y ahora por
una razón medida, no por prudencia.**

Un fallo de disco en `put` no puede tumbar el análisis: el dato ya se obtuvo y la
caché es una optimización, así que el `OSError` se traga tras limpiar el temporal.

## 21.2 Un segundo hallazgo, específico de Windows

Con la escritura ya atómica el test **seguía fallando**: 15 lecturas perdidas, pero
no por contenido roto — `PermissionError`. En Windows `os.replace` deja una ventana
brevísima en la que abrir el destino da *sharing violation* aunque el reemplazo
funcione. En Linux (Render, producción) no ocurre.

El efecto es el mismo agujero de cuota, así que `_read_record` reintenta 3 veces
con 10 ms. Distingue los dos casos: un `JSONDecodeError` es corrupción real y no se
reintenta; un `OSError` es la ventana del rename y sí.

## 21.3 Estado

`engine/tests/test_cache_writes_are_atomic.py` — 5 tests con hilos de verdad contra
disco de verdad, no lectura de código fuente. Verificado que **fallan** contra el
`put` original (71 lecturas rotas), así que discriminan.

**2120 tests del engine + 75 de la capa web.**

---

# 22. El protocolo de memoria se degradaba solo (M-01)

`CLAUDE.md` hace obligatorio escribir `Memoria/tesis/<TICKER>.md` y una línea
en `Memoria/MEMORIA.md` después de cada análisis. El escritor existía y corría
— pero se destruía a sí mismo, sin dar error nunca.

## 22.1 El título se multiplicaba

```python
f.write(f"# Tesis — {ticker.upper()}\n\n{entry}{prev}")
```

`prev` era el archivo ANTERIOR COMPLETO, título incluido, y se le anteponía un
título nuevo. Estado encontrado:

| Archivo | Encabezados `# Tesis` | Bloques | Bloques DISTINTOS |
|---|---|---|---|
| `NVDA.md` | **32** | 32 | **15** |
| `AAPL.md` | 2 | 2 | 2 |

## 22.2 El índice crecía sin límite

`open(idx, "a")` añadía una línea por CORRIDA. `MEMORIA.md` tenía 34 líneas,
25 de ellas `NVDA` con texto idéntico (`raw 37.1/100 · FV $281.05`) — y el
propio archivo pide lo contrario: *"el agente agrega una línea por ticker
analizado"*. Un índice con el mismo ticker 25 veces no sirve para lo único
que existe: mirar de un vistazo qué se dijo de cada empresa.

## 22.3 El arreglo

Un bloque por RESULTADO, no por corrida. Cada bloque lleva
`<!-- firma: perfil|raw|fv|bull|base|bear | desde: fecha -->`; si el análisis
nuevo coincide, se sella *"sin cambios; revisado"* y se conserva la fecha en
que la conclusión apareció por primera vez — que es justo el dato que decía
cuánto tiempo se sostuvo, y que apilar duplicados destruía. Cualquier cambio
abre bloque nuevo y **nunca** borra el viejo.

El índice se reescribe: una línea por ticker, ordenada y enlazada a su tesis.
El historial no se pierde — vive en `tesis/<TICKER>.md`, que es su sitio.

Reparado el daño existente sin perder ningún análisis real: `NVDA.md` 32 → 18
bloques (sólo se colapsaron los idénticos), `AAPL.md` intacto.

Cobertura del protocolo, antes → después: **2/5 → 5/5** tickers con tesis,
2/5 → 5/5 con `prediccion.json`, corriendo el análisis real de JPM, KO y PLTR
(no escribiendo los archivos a mano).

## 22.4 Estado

`tests_vertex/test_memoria_protocolo.py` — 8 tests. Todos REPITEN corridas,
que es lo único que destapa ambos fallos.

**2125 tests del engine + 88 de la capa web.**

---

# 23. ABIERTO: las dos capas no dan el mismo número (M-02)

`CLAUDE.md`: *"Dos capas, una sola matemática. `engine/wbj/` calcula;
`vertex_api.py` presenta."* No se cumple.

Mismo ticker, mismo día (2026-08-03), NVDA:

| Camino | raw | business | financial | market | technical | risk | valuation |
|---|---|---|---|---|---|---|---|
| `run_aggregate` (motor) | **47.91** | 11.39 | 10.08 | 5.05 | 10.48 | 5.95 | 4.97 |
| `/api/analyze` (web) | **37.0** | 6.8 | 10.05 | 1.8 | 9.4 | 4.05 | 5.1 |

**No es el judge.** Medido con `judge=True` y `judge=False`: 47.91 en ambos
casos, porque el judge está caído por créditos y su fallo ya degrada limpio.

**Causa:** `vertex_api.py::_engine_scorecard` construye su PROPIO `_overlay`
(beta, risk_free_rate, interest_expense, equity_issuance, estimates) en
paralelo al que arma `engine/wbj/deep.py::_run_specialists`. Dos overlays
independientes alimentan a los mismos especialistas con entradas distintas.
Es lógica de scoring duplicada que derivó.

**Qué implica:** el número que se ve en la interfaz y el que escribe la
memoria (37.0) NO es el que da el motor (47.91). Las cinco tesis recién
escritas usan el de la ruta.

**Por qué queda abierto:** unificarlo es un cambio de arquitectura —
`_engine_scorecard` tendría que consumir `run_aggregate` en vez de reimplementarlo—
y decide Victor cuál de los dos overlays es el bueno.

---

# 24. Yahoo fuera del motor (Y-01)

Decisión de Victor: las fuentes del sistema son **FMP, FinnHub, FRED y
EDGAR**. yfinance no está en esa lista, y raspa un endpoint que nadie
documenta — un score que depende de eso puede moverse entre dos corridas sin
que nadie sepa por qué.

`_yahoo_revisions` alimentaba tres cosas. Al quitarlo:

| Aportaba | Métrica | Después |
|---|---|---|
| `eps_growth_pct` | VAL-PEG-028 | **Sustituido por FMP**: primer año FORWARD contra el último pasado del mismo panel. NVDA: 9.001/4.694 = **+91.8%** vs el +88.6% de Yahoo |
| `current`/`prior_consensus` | MKT-REVMAG-012 | `_consensus_history` (snapshots propios de FMP). Necesita 30 días; ya está grabando desde 2026-08-01, así que vuelve a puntuar **~2026-08-31** |
| `upward`/`total` | MKT-REVBR-011 | **NOT_SCORABLE.** Ninguna fuente principal sirve conteos de revisión de ESTIMADOS. FMP `grades` son cambios de RECOMENDACIÓN — otra magnitud, no se sustituye |

**Coste medido** (NVDA, 2026-08-03): Market **5.05 → 1.84 / 20**, cobertura
0.487 → 0.388. Se recupera parcialmente solo, con el histórico propio.

El aviso de MKT-REVBR-011 ahora explica la causa y qué declarar en
`Entradas/<TICKER>.json`, en vez de decir sólo "unavailable".

## 24.1 Lo que sigue usando Yahoo

La capa web, en 116 puntos: precio histórico (25), `.info`/quote y **cadenas
de opciones (7)**. FMP cubre los dos primeros (`historical-price-eod/full`,
`quote`, `profile` → 200). **Las opciones no**: `options-chain` y
`options/contracts` dan **404** en este plan, y QuantData —que era la fuente
de opciones— tiene el plan API inactivo. Quitarlas dejaría los paneles de
opciones sin datos.

## 24.2 Estado

`test_the_engine_no_longer_imports_yahoo` recorre todo `engine/wbj/` y falla
si alguien vuelve a importarlo.

**2126 tests del engine + 88 de la capa web.**

---

# 25. Sólo las cuatro fuentes de Victor (Y-02)

Decisión de Victor: el proyecto usa **FMP, FinnHub, FRED y EDGAR**, y nada
más. Se verificó contra su repo: sus proveedores son exactamente
`edgar/finnhub/fmp/fred`, su `pyproject.toml` no declara yfinance, y no hay
una sola línea de opciones ni de Quant Data en su código.

## 25.1 yfinance sustituido, no parcheado

`vertex_market.Ticker` replica el contrato de `yfinance.Ticker` con datos de
FMP, para no reescribir 45 llamadores en un archivo de 13.500 líneas.
Verificado contra la clave de Victor (2026-08-03):

| Necesidad | Endpoint FMP | Antes |
|---|---|---|
| velas diarias | `historical-price-eod/full` | Yahoo |
| **velas 1h / 5m** | `historical-chart/{1hour,5min}` | **sólo Yahoo** |
| cotización / ficha | `quote`, `profile` | Yahoo `.info` |
| múltiplos, márgenes | `ratios-ttm` | Yahoo |
| precio objetivo, recomendación | `price-target-consensus`, `grades-consensus` | Yahoo |
| noticias | `news/stock` | Yahoo |
| insiders | `insider-trading/search` | Yahoo |

El intradía es la sorpresa: el código documentaba que "el respaldo FMP SÓLO
sirve para velas diarias" y la ruta intradía se quedaba sin red. FMP sí las
sirve en este plan.

## 25.2 Lo que se eliminó

Las cadenas de opciones no tienen sustituto (`options-chain` y
`options/contracts` dan **404**), así que se eliminaron en vez de fingir
datos. Con ellas se fue Quant Data, cuyo plan API está inactivo.

| Capa | Se fue |
|---|---|
| Opciones | GEX, max pain, IV, walls, venta de prima, griegas, trade-plan |
| Quant Data | 22 funciones: flujo, convicción ΔOI, dark pool, net-flow, confluencia |
| Derivado | proyecciones, backtest, colector de snapshots, planificador nocturno |

`vertex_api.py` **13.525 → 10.811** líneas (−2.714).
`vertex_fund_os_platform.html` **8.253 → 6.537** (−1.716).

## 25.3 Lo que se conservó a propósito

- `_calibration_prompt_block` — es el track record propio del ticker, no un
  dato de opciones. Estaba enterrado en medio del bloque de Quant Data.
- `Entradas/` y `Memoria/` — Victor no las tiene, pero son el camino de los
  puntos de Market y el protocolo obligatorio de `CLAUDE.md`.
- El `flow_override` quedó en `False` fijo: lo disparaba la convicción por
  ΔOI, y activarlo por defecto afirmaría un flujo institucional que nadie
  midió.

## 25.4 Estado

`/api/analyze` verificado end-to-end tras el recorte: NVDA raw 37.0 (94 s),
JPM raw 22.9 (55 s). `/api/data-health` pasó a **ok=true** — antes era
`false` porque declaraba Quant Data como fuente crítica y estaba caída.
La interfaz carga con 0 errores de consola y sus 6 vistas intactas.

**2126 tests del engine + 86 de la capa web.**

---

# 26. La capa web ignoraba `Entradas/` (M-02, resuelto)

La sección 23 dejó abierto que motor y web daban números distintos para el
mismo ticker el mismo día. La causa medida:

| | Claves del overlay | ¿Usa `build_overlay`? |
|---|---|---|
| Motor (`run_aggregate`, `wbj report`, CLI) | **42** | — |
| Ruta web (`_engine_scorecard`) | **16** | **No: lo reimplementaba** |

No era una regla distinta. Era **hambre de datos**: la ruta arrancaba su
overlay en `{}` y construía 16 claves a mano. Entre las 26 que le faltaban
estaban **todas las de `Entradas/<TICKER>.json`** — el TAM declarado con su
fuente y su tier, la clasificación de moat, la concentración de clientes.

**El analista las escribía en disco y la ruta web no las miraba.** Todo el
trabajo de investigar el TAM de Gartner y documentar por qué `MKT-SHARE-006`
no es puntuable existía sólo para el motor.

Coste medido sobre NVDA con el packet golden: **Risk −3.94, Business −0.92**.

## 26.1 El arreglo

`_overlay` se siembra ahora con `build_overlay(pk, settings)` — el mismo que
usan las otras tres entradas del sistema. Las asignaciones propias de la ruta
van DESPUÉS y siguen ganando, así que sus seis claves exclusivas (`beta`,
`risk_free_rate`, `equity_issuance`, `earnings_dates`, `peer_multiples`,
`sector_breadth`) se suman en vez de competir. Riesgo de regresión: ninguno
sobre lo que ya calculaba.

Efecto en los cinco tickers con reporte, por la ruta real:

| Ticker | Antes | Ahora | |
|---|---|---|---|
| NVDA | 37.0 | **48.6** | +11.6 |
| JPM | 22.9 | **35.6** | +12.7 |
| KO | 39.3 | **47.5** | +8.2 |
| AAPL | 31.5 | **37.8** | +6.3 |
| PLTR | 29.3 | **32.7** | +3.4 |

Ninguno cambia de perfil: los cinco siguen en `Avoid / Wait`. Lo que cambia
es que el número ya no está deprimido por datos que existían y no llegaban.

## 26.2 Lo que queda

La diferencia con el motor pasó de **−7.7 a +3.87**, y ahora es explicable y
va en la dirección correcta: la ruta ve estrictamente MÁS que el motor por
sus seis claves propias. Cerrarla del todo pide subir esas seis a
`build_overlay`, para que las cuatro entradas del sistema vean lo mismo.

## 26.3 Estado

`tests_vertex/test_overlay_parity.py` — 4 tests que comparan las claves que
cada capa entrega de verdad, no el código fuente. El testigo es el TAM: si
deja de llegar con su tier, falla.

**2126 tests del engine + 90 de la capa web.**

---

# 27. Auditoría de Analyze contra el repo de Victor (2026-08-03)

Comparado contra `infusionvictor/warren-buffett-jr` en su commit actual
`72d92d9`. La copia local estaba en `e841254`, muy atrasada; se actualizó
antes de comparar.

## 27.1 Cerebro: idéntico

**84 de 84 archivos byte-idénticos.** La metodología —fórmulas, scoring,
gates, políticas de datos, adaptadores— es exactamente la suya. Cero
divergencia.

## 27.2 Motor: superconjunto estricto

| | |
|---|---|
| Funciones de Victor que me faltan | **0** |
| Funciones mías de más | **137** |
| Archivos que sólo tengo yo | **13** |

Los 13 son precisamente la capacidad que Victor no tiene y que este proyecto
sí necesita: `entradas.py` (canal `Entradas/<TICKER>.json`),
`overlay/from_packet.py` (lo que alimenta a los especialistas),
`report/*` (reporte auditable + las 4 gráficas), `deep.py` (pipeline),
`extract/filing.py` (10-K), y cuatro lecturas compartidas del Cerebro
(`adapters`, `taxes`, `periods`, `confidence_inputs`).

**Volver a ser byte-idéntico borraría el canal `Entradas/`, el generador de
reportes y las gráficas.**

## 27.3 Dónde su código se desvía de su propio Cerebro

`PRICE_LEVEL_SYNTHESIS.md` (idéntico en ambos repos) fija:

```text
Distance_percent = (Level - CurrentPrice) / CurrentPrice
Distance_ATR     = (Level - CurrentPrice) / ATR14
```

Fórmula **con signo**: un nivel por debajo del precio da negativo.

| | Convención |
|---|---|
| Cerebro | con signo |
| Victor `aggregate/synthesis.py:139` | con signo ✓ |
| Victor `engine/tests/aggregate/test_synthesis.py:38` | `-8.0` para soporte ✓ |
| Victor `engines/levels_engine.py:796-800` | **invierte los operandos → siempre positivo** ✗ |
| Este repo | con signo ✓ |

Victor lo sabía. Su propio docstring dice que la discrepancia "no se puede
reconciliar sin modificar ese módulo, lo cual está fuera de alcance". Su
`synthesis.py` copia `zone.distance_percent` **tal cual** (línea 154), así
que las dos convenciones acaban en la misma tabla.

**Ser exacto a él aquí reintroduciría una desviación del Cerebro.** Se
mantiene el arreglo.

Otros defectos suyos vivos hoy que este repo ya corrige: sin
`_settled_sessions` (la sesión en curso se toma como cierre) y sin escritura
atómica de caché (`os.replace`).

## 27.4 Estado

| | Victor | Este repo |
|---|---|---|
| Archivos de test | 36 | **122** (motor) + 9 (web) |
| Tests que pasan | — | **2126 + 90** |

---

# 28. Despliegue: config muerta y un correo en un repo público (D-01)

## 28.1 Config que sobrevivió a su proveedor

Al borrar las 22 funciones de Quant Data quedaron **165 líneas** de
configuración huérfana: `QUANTDATA_API_KEY`, `QUANTDATA_BASE` y ocho
`QD_EP_*`. No rompía nada — sólo hacía creer que el sistema depende de algo
que ya no toca, y `render.yaml` seguía pidiendo la variable en el
despliegue.

**Al cortar ese bloque me llevé también `/api/data-health`**, que vivía
entre esa cabecera y la siguiente. El mismo error que ya cometí con
`portfolioView`: cortar por marcadores de sección en vez de por límites
sintácticos. Lo detectó el test que comprueba que la UI no llame rutas
inexistentes; restaurada desde git con su `_DH_CACHE`.

## 28.2 El correo personal estaba en el repo

`render.yaml` traía `EDGAR_USER_AGENT` con `value:` y el correo de Victor
dentro. Este repo es **público**. La SEC exige un contacto real, así que el
valor no se inventa — pero su sitio es el dashboard de Render, no un archivo
indexable. Pasa a `sync: false`.

También salieron `SCHWAB_APP_KEY` y `SCHWAB_APP_SECRET`, declaradas y nunca
usadas, y entró `JUDGE_MODEL`, que se usa y no estaba declarada (había que
ponerla a mano en el dashboard).

## 28.3 Verificación para móvil, tablet y escritorio

| | |
|---|---|
| viewport | `width=device-width, viewport-fit=cover` ✓ |
| PWA | `manifest.webmanifest` + `apple-mobile-web-app-*` ✓ |
| A 375×812 (teléfono) | **0 desbordes horizontales** ✓ |
| `/api/data-health` | `ok`, 8 fuentes, ninguna muerta |
| `/api/analyze` NVDA | 70.4 s, raw 48.6, upside 35.66% |
| Rutas verificadas | self-test, quote, history, regime, track-record → 200 |

## 28.4 Estado

**2126 tests del engine + 91 de la capa web.**

---

# 29. La web quedó rota en producción por un borrado por líneas (D-02)

Kevin no podía iniciar sesión ni crear cuenta en
`https://vertex-fund-os.onrender.com`. Diagnosticado contra el sitio en vivo:

```
authSubmit      -> undefined
renderDashboard -> undefined
buildTVChart    -> undefined
switchView      -> function     (está ANTES del corte)
loadTrackRecord -> function     (está DESPUÉS)
```

Un solo error de sintaxis impide que se ejecute el bloque `<script>`
**entero** —250.000 caracteres, casi toda la aplicación— y el navegador no
lo grita: la página carga, se ve bien, y las funciones no existen.

## 29.1 La causa

Al quitar los paneles de opciones se borraron líneas por su CONTENIDO sin
mirar si además abrían un bloque. Una era:

```js
document.getElementById('qtTradePlanBody').innerHTML = `
```

que abría una plantilla de 30 líneas. Sin ella el HTML quedó suelto en medio
del código. Lo mismo con la firma de `projLoadChart` (quedó su cuerpo) y con
el `const el = ...` de `runSelfTest`.

**Es el tercer caso del mismo error** en esta sesión: `portfolioView`,
`/api/data-health` y ahora esto. Cortar por marcadores o por contenido en vez
de por límites sintácticos.

## 29.2 Por qué mi verificación no lo vio

Comprobé que la página cargaba y que las 6 vistas existían. `switchView`
está en la posición 134.126 — **antes** del corte, así que respondía. Todo lo
roto vive entre 154.491 y 462.301.

Los tests existentes miran ids del DOM y rutas de la API. **Ninguno
comprobaba que el código llegara a ejecutarse.**

## 29.3 El arreglo

Eliminados como unidad sintáctica: el bloque del Plan de Trade en
`renderDashboard` (lo escribía en un `qtTradePlanBody` que ya no existe), los
globales de Proyecciones con el cuerpo huérfano de `projLoadChart`, y
`runSelfTest`.

Verificado en ejecución: `authSubmit`, `renderDashboard`, `buildTVChart`,
`authToggleMode` definidas; pulsar "Create one" cambia a modo `register`,
aparece el campo de nombre y el título pasa a "Create Account".

## 29.4 El test que faltaba

`tests_vertex/test_javascript_parses.py` — corre `node --check` sobre cada
bloque `<script>` propio (el mismo analizador del navegador) y comprueba que
ningún `onclick` apunte a una función inexistente. Habría atrapado este fallo
antes de desplegarlo.

## 29.5 Un test frágil, de paso

`test_the_health_strip_only_lists_real_sources` fallaba con
`KeyError: 'sources'`: `/api/data-health` NO es pública, y `vertex_api` carga
`vertex.env` al importarse — basta con que el desarrollador tenga su
`VERTEX_API_TOKEN` puesto (para desplegar en Render) para recibir un 401.
Ahora se autentica como cualquier cliente en vez de asumir un entorno sin
token.

**2126 tests del engine + 94 de la capa web.**

---

# 30. Proyecciones restaurada: dos agentes, una frontera (D-03)

Victor pidió que Proyecciones volviera. **Es otro agente**: Analyze puntúa
acciones, Proyecciones opera opciones. Yo lo borré por una interpretación
mía equivocada — cuando dijo *"no toques nada de proyecciones déjalo así"*,
lo leí como "déjalo borrado" cuando quería decir "déjalo intacto".

## 30.1 La frontera

| | Analyze (acciones) | Proyecciones (opciones) |
|---|---|---|
| Fuentes | FMP, FinnHub, FRED, EDGAR | + Yahoo (cadenas) |
| Toca el score | sí | **nunca** |
| Si su fuente cae | análisis degradado y declarado | panel vacío, visible al instante |

Yahoo entra por **un solo import**, con su razón escrita al lado. Las cadenas
de opciones no existen en las cuatro fuentes: FMP da 404 en `options-chain`
con este plan y Quant Data tiene el plan API inactivo.

Dos tests fijan la frontera:
`test_yahoo_and_quantdata_never_reach_the_scoring_engine` (la ruta de scoring
de la web) y `test_the_engine_no_longer_imports_yahoo` (el motor).

## 30.2 Tres nombres que la restauración destapó

Reinsertar 77 funciones por AST dejó fuera lo que vive **entre** funciones:

| Nombre | Efecto |
|---|---|
| `_QD_SIN_DERECHO` | `NameError` en toda llamada a Quant Data |
| `_QD_MAXPAIN_CACHE` | `NameError` en `compute_gex` → GEX caído |
| `_SCHED_STATE` | el planificador sin estado |

Y uno que **no** venía de la restauración: **`import logging` nunca existió
en `vertex_api.py`**. `_error_publico` —el manejador de errores que escribí
hace varias sesiones— lo usaba sin importarlo. Nunca falló porque esos
caminos no habían lanzado una excepción; el primer error real dentro de un
`except` produjo un `NameError` *dentro del manejador de errores*.

## 30.3 Verificación en vivo

| Ruta | Resultado |
|---|---|
| `/api/options-gex` | spot 758.39 · call wall 760 · put wall 720 · max pain 744 · gamma flip 745.89 |
| `/api/projection-targets` | 7 targets |
| `/api/income-strategies`, `/api/trade-plan`, `/api/options-ledger`, `/api/portfolio-options` | 200 |
| `/api/analyze` NVDA | **raw 48.6, FV $281.05 — sin cambios** |

yfinance 1.5.1 sirve 34 expiraciones de SPY (120 calls / 165 puts).
`gex-strike` sigue diciendo "sin exposición GAMMA": eso es Quant Data, cuyo
plan está inactivo — el resto cae a lo derivado de la cadena, como fue
diseñado.

**2126 tests del engine + 94 de la capa web.**

---

# 31. El navegador no se enteraba de los despliegues (D-04)

Victor no podía analizar: `integrityStripHTML is not defined`. **El código
estaba bien** — producción y local eran byte-idénticos (mismo SHA-256), los
cuatro bloques `<script>` compilaban y la función estaba definida en la
línea 2943.

Lo que fallaba era lo que llegaba a su pantalla.

## 31.1 La causa

`/` se servía **sin una sola cabecera de caché**:

```
Cache-Control: (ausente)   ETag: (ausente)
Last-Modified: (ausente)   Expires: (ausente)
```

Sin instrucciones, el navegador aplica caché heurística: decide por su cuenta
cuánto guardarlo. El HTML es el ESQUELETO de la app —todo el JavaScript va
dentro—, así que su navegador seguía ejecutando el bundle roto contra la API
ya arreglada.

Esto no era un caso aislado: **habría pasado en cada despliegue.**

## 31.2 El arreglo

`Cache-Control: no-cache, must-revalidate` + `ETag` derivado del contenido.

`no-cache` no es "no lo guardes": es "guárdalo, pero pregúntame antes de
usarlo". Con `ETag` la revalidación es gratis — si nada cambió, 304 y cero
bytes; si cambió, baja la versión nueva sola.

| Situación | Antes | Ahora |
|---|---|---|
| Misma versión | 572.072 bytes | **304, 0 bytes** |
| Tras desplegar | seguía el viejo hasta vaciar caché | **200, versión nueva** |

Medido en local. `no-store` habría prohibido guardarlo y costado medio mega
en cada carga — caro en el teléfono, que es donde lo usa.

## 31.3 Un error de subcadena, de paso

Al añadir el import de `Response`, la comprobación buscaba la subcadena
`"Response"` en la línea del import — y **ya aparecía dentro de
`HTMLResponse`**. La condición dio positivo, el import no se añadió, y la
ruta devolvía 500. Comparación exacta contra la lista de nombres, no
`in` sobre el texto.

## 31.4 Estado

`tests_vertex/test_html_revalidation.py` — 4 tests: que el esqueleto siempre
revalide, que una versión sin cambios cueste 0 bytes, que un despliegue nuevo
llegue solo, y que el `ETag` salga del CONTENIDO y no del reloj (uno por
marca de tiempo invalidaría la caché en cada reinicio sin motivo).

**2126 tests del engine + 98 de la capa web.**

---

# 32. Los puntos 2-5, resueltos como los tiene Victor

## 32.1 Latencia (#2): fuera el conjunto trimestral 13F

Perfilado con `cProfile` sobre un ticker frío: **96 s totales, 44 s en
lecturas de socket SSL** — 71 peticiones HTTP (31 del motor por httpx, 40 de
la capa web por requests). No había una función lenta: eran round-trips.

El único bloque grande y evitable era `_ownership`, que ante el 402 de FMP
caía a un respaldo de tres escalones sobre EDGAR cuyo primer paso descargaba
el **zip trimestral 13F** de la SEC.

Victor no lo hace. Su `packet/builder.py`, líneas 308-309:

```python
insider_trades = fmp.insider_trades(ticker) or []
institutional_holders = fmp.institutional_holders(ticker) or []
```

y su docstring: *"13F institutional holders (may be plan-restricted →
None)"*. Cuenta con el 402 y devuelve vacío.

**Medido**: `_wbj_holders_from_edgar` **19.1 s → 0.3 s**. `_ownership` pasó
de 167 a 71 líneas.

**El precio, declarado**: `institutional_13f` queda `[]` y
`holders_available` en False. `CLAUDE.md` pide los inversionistas 13F y ese
requisito queda SIN CUBRIR con el plan actual de FMP — en este repo y en el
de Victor por igual. Se resuelve subiendo de plan, no con más código. Los
métodos siguen en `EdgarProvider` por si un día hay presupuesto de latencia.

**Corrección**: antes dije que paralelizar "empeoró" la latencia. Comparé
**tickers distintos** (AMD 177 s contra NVDA 92 s), que no son comparables.
La única medición válida es la directa de arriba.

## 32.2 Insiders (#5): no había bug — el error era mío

Reporté "insiders: 0". Falso: leía `mandatory_report.insiders`, clave que no
existe. La real es `insiders_over_1m`. Corriendo el análisis de verdad:

```
insiders_over_1m : 8 operaciones agrupadas (Mark Stevens $485M en 8 Forms 4)
insiders_flow    : venta $819.5M · 141 ventas · 0 compras
```

FMP devuelve 200 Forms 4 y 63 superan $1M. **Funciona como debe.**

## 32.3 Narrativa (#4): cuota, no código — pero el mensaje mentía

`_wbj_explain` intenta Gemini → OpenAI → Grok. La cadena es correcta. El
diagnóstico real:

| Proveedor | Estado |
|---|---|
| Gemini | **429 RESOURCE_EXHAUSTED** |
| OpenAI | **429 quota** |
| Grok | sin `XAI_API_KEY` |

Pero sólo se propagaba el ÚLTIMO error, así que un problema de facturación
en el proveedor PRINCIPAL se reportaba como *"Grok no configurado
(XAI_API_KEY vacío)"* — señalando una variable que falta a propósito y
escondiendo la causa. Ahora se registran los tres, en orden.

## 32.4 Market (#3): sin resolver, y no es código

Sigue en 0.9/10. El TAM de Gartner mide gasto de usuario final y NVDA vende
componentes: son capas distintas de la cadena de valor. Necesita un TAM de
aceleradores de datacenter (IDC, Mercury Research, Omdia). Es un dato que
hay que comprar o citar, no una línea que escribir.

## 32.5 Estado

**2110 tests del engine + 98 de la capa web.** El archivo que cubría el
respaldo retirado ahora fija lo contrario: que nadie vuelva a descargar el
zip, que los tenedores salgan de FMP, y que un hueco sin sustituto se
declare en vez de anunciarse como tapado.

---

# 33. Grok fuera: sólo Gemini y OpenAI (G-01)

Victor no usa Grok en **ninguna parte** de su repo — ni en `engine/wbj`, ni
en su `CLAUDE.md`, ni en sus dependencias. Aquí estaba en 8 funciones.

## 33.1 Dos rutas dependían SÓLO de él

`/api/sentiment` y `/api/explore-deep` llamaban a `api.x.ai` **sin ningún
respaldo**: una clave sin configurar apagaba la ruta entera. No se podían
borrar sin romperlas, así que se portaron a los dos proveedores del sistema
con el helper nuevo `_texto_llm(system, user)` — Gemini primero, OpenAI
después, y si ninguno responde devuelve por qué falló CADA uno.

## 33.2 Las cadenas de Analyze

`_wbj_explain` y `_analyze_structured` tenían Grok como tercer escalón.
Ahora son **Gemini → OpenAI**. `_grok_json` eliminada.

## 33.3 Nombres que mentían

Las variables y las **claves JSON** se llamaban `grok_ok`, `grok_text`,
`grok_error` — y ya contenían salida de Gemini o de OpenAI. Renombradas a
`llm_*` en la API (31 sitios) y en la interfaz (8), que las lee. Un nombre
que miente sobre su origen es peor que uno feo: manda a depurar al sitio
equivocado.

Fuera también de `render.yaml` y del aviso de claves al arrancar.

## 33.4 Auditoría de Analyze — estado verificado

Contra `infusionvictor/warren-buffett-jr` en `72d92d9`:

| | |
|---|---|
| Cerebro | **84/84 byte-idénticos**, 0 suyos ausentes |
| Motor | **0 funciones suyas me faltan** |
| Proveedores | `base, cache, edgar, finnhub, fmp, fred` — **la misma lista** |

Corrida real de NVDA (77.5 s):

```
raw 48.9 · Avoid / Wait · FV $289.30 · upside 40.0%
scores_source: victor          insiders > $1M: 8
flujo insiders: -$819.5M       niveles de precio: 39
```

Lo que NO corre, y por qué:

| Pieza | Causa | HTTP |
|---|---|---|
| judge | créditos Anthropic | 400 |
| extracción cualitativa 10-K | créditos Anthropic | 400 |
| historial de management | créditos Anthropic | 400 |
| narrativa | cuota Gemini **y** OpenAI | 429 / 429 |
| 13F institucional | plan FMP | 402 |
| Market 0.9/10 | TAM de capa incorrecta | — |

**Ninguna es un defecto de código.** Cuatro son facturación, una es plan y
una es un dato que hay que comprar.

**2110 tests del engine + 98 de la capa web.**

---

# 34. El 13F: por qué FMP no funciona, y por qué a Victor tampoco (F-01)

## 34.1 La respuesta a "¿por qué a él sí y a mí no?"

**A él tampoco.** Su `providers/fmp.py` y el de este repo son **byte-idénticos**
en este método:

```python
def institutional_holders(self, t: str) -> list | dict | None:
    """13F institutional holders (may be plan-restricted → None)."""
    return self.get_json(
        f"{BASE_URL}/institutional-ownership/extract-analytics/holder",
        self._params(symbol=t), ...)
```

Y el docstring de su módulo lo dice: *"Endpoints not included in the caller's
plan return a non-JSON 'Restricted Endpoint' body, which `get_json` turns
into None (graceful degradation)."* **Escribió ese código contando con el
402.**

## 34.2 No es esa ruta: es toda la familia

Probadas una por una contra la clave:

| Endpoint | |
|---|---|
| `extract-analytics/holder` (la de Victor) | **402** |
| `symbol-positions-summary` | **402** |
| `holder-performance-summary` | **402** |
| `institutional-ownership/latest` | **402** |
| `symbol-ownership`, `institutional-ownership/list` | 404 (no existen) |

> *Restricted Endpoint: This endpoint is not available under your current
> subscription*

El módulo entero está por encima del plan de $29. No hay ruta alternativa.

## 34.3 Respaldo restaurado, y ahora sí barato

Vuelve el camino por EDGAR, **dentro de `if not holders:`** — sólo cuando FMP
no trae la lista. Devuelve tenedores reales con acciones y dólares:

```
BlackRock, Inc.                 1.928.629.174 acc   $336.352.928.002
VANGUARD CAPITAL MANAGEMENT LLC 1.538.550.382 acc   $268.519.177.197
STATE STREET CORP                 993.885.601 acc   $173.343.323.230
```

**Corrección de una medición mía.** Dije que el costo era "un zip por
trimestre compartido por todos los tickers". Falso: medí tres llamadas
seguidas al MISMO ticker y costaron 19.3 s, 18.7 s y 18.1 s. El zip de 99 MB
sí se cachea en disco — lo que no se cacheaba era el RESULTADO, así que cada
llamada recorría los 3,8 millones de filas de `INFOTABLE.tsv` otra vez. El
docstring decía "~2 s" y nadie lo había comprobado.

Arreglado: el resultado se cachea por `(cusip, trimestre, top)`. La clave
lleva el trimestre, así que cuando la SEC publica el siguiente el viejo deja
de usarse solo — sin TTL que ajustar.

| | Antes | Ahora |
|---|---|---|
| Primera vez por ticker | 17 s | 17 s |
| **Repetir el mismo ticker** | **18 s** | **0.8 s** |

## 34.4 Y un bug que el respaldo destapó

Con los tenedores ya en memoria, el reporte seguía diciendo **"0 tenedores"**.
Había DOS caminos y `institutional_13f` leía el vacío:

- `insiders["institutional"]` ← el `institutional_holders` estilo yfinance,
  que hoy devuelve `None` (FMP 402).
- `insiders["edgar"]["holders_5pct"]` ← el que **sí** traía los diez.

Los datos estaban en la misma estructura, una clave más abajo. Es el mismo
patrón que ya me engañó leyendo `mandatory_report.insiders` en vez de
`insiders_over_1m`: un dato presente que no se ve porque se busca donde no
está.

Verificado end-to-end: NVDA y AAPL devuelven **8 tenedores** con sus dólares,
más los insiders sobre $1M.

## 34.5 Estado

`tests_vertex/test_13f_llega_al_reporte.py` — 4 tests: que EDGAR llegue
cuando FMP viene vacío, que FMP mande cuando responde, que sin ninguno no
reviente, y el tope de diez.

**2110 tests del engine + 102 de la capa web.**

---

# 35. Yahoo fuera de Analyze, de verdad: FMP → FinnHub → EDGAR (F-02)

## 35.1 El 402 no lo causaba yfinance

Victor lo supuso y merecía una respuesta con evidencia. Petición HTTP
directa a `financialmodelingprep.com`, **con cero módulos de yfinance
cargados**:

```
modulos con 'yfinance': NINGUNO
HTTP 402  <- respuesta DIRECTA de financialmodelingprep.com
Restricted Endpoint: This endpoint is not available under your current subscription
```

El 402 es FMP hablando de su propio plan. Ninguna otra librería lo provoca.

## 35.2 La cadena que pidió, implementada literal

| Escalón | Endpoint | Estado hoy |
|---|---|---|
| 1. FMP | `institutional-ownership/extract-analytics/holder` | **402** |
| 2. FinnHub | `stock/fund-ownership` | **403** (tier de pago) |
| 3. EDGAR | conjunto trimestral 13F | **funciona** |

FinnHub se cablea aunque hoy falle: el día que suba de plan empieza a
funcionar sin tocar una línea. Sus rutas `institutional-ownership` e
`institutional-portfolio` ni existen (404); `fund-ownership` y `ownership`
son de pago. Lo que **sí** sirve gratis es `insider-transactions`, y los
insiders ya salían de FMP (200, 100 filas).

## 35.3 El último uso de Yahoo en el camino del score

Recorriendo el cierre COMPLETO de llamadas desde
`_analyze_ticker_serializado` — 139 funciones — quedaba **uno**:
`_backtest_signals` pedía el historial de precios a Yahoo, a varios saltos de
distancia. Migrado a FMP.

## 35.4 El import pasa a ser perezoso

Aun sin usarlo, `import yfinance as yf` en la cabecera lo metía en memoria en
cada arranque, y hacía imposible distinguir *"está cargado porque el análisis
lo usó"* de *"está cargado porque el archivo lo importó"*.

Ahora se carga en el primer uso real. Medido:

| Momento | ¿yfinance en memoria? |
|---|---|
| Tras importar `vertex_api` | **NO** |
| Tras un análisis completo de NVDA | **NO** |
| Tras `compute_gex` (Proyecciones) | SÍ, y el GEX responde |

La separación entre los dos agentes deja de ser disciplina y pasa a ser
física. De paso abarata el arranque en Render: yfinance arrastra
dependencias que el análisis de acciones no necesita.

## 35.5 Estado

Análisis de NVDA sin Yahoo en memoria: `raw 48.8`, **8 tenedores 13F**
(BlackRock $336B, Vanguard $268B, State Street $173B) y **8 insiders sobre
$1M**.

Dos tests nuevos: uno recorre el cierre de 139 funciones y falla si alguna
vuelve a tocar `yf`; el otro prohíbe el `import` en la cabecera.

**2110 tests del engine + 104 de la capa web.**

---

# 36. El TAM estaba midiendo la capa equivocada (M-03)

Market llevaba en 0.9/10 porque el TAM declarado era **Gartner Data Center
Systems ($489.5B CY2025)**, que mide *gasto del usuario final* en servidores,
switching, almacenamiento y software. NVDA vende **aguas arriba**, a
OEM/ODM/CSP: una GPU suya dentro de un servidor Dell entra en ese denominador
UNA vez, como gasto del comprador final.

Dividir el ingreso de NVDA entre ese número comparaba **dos capas distintas
de la cadena de valor**, y por eso `share`, `share_history` y
`competitor_shares` estaban declarados NOT_SCORABLE — correctamente.

## 36.1 La fuente en la capa correcta

**Omdia, "AI Processors for Cloud and the Data Center Forecast"**, agosto 2025:

| Año | GPUs + aceleradores IA embarcados |
|---|---|
| 2024 | **$123 000 M** |
| 2025 | **$207 000 M** |
| 2030 | $286 000 M |

Es el mercado de CHIPS — la misma capa en la que NVDA cobra. Tier 3, igual que
Gartner: el cambio es de capa, no de calidad de fuente.

`omdia.tech.informa.com` devuelve 403 a WebFetch, igual que `gartner.com`.
Las cifras están verificadas textualmente en **cinco** publicaciones
independientes que citan el mismo comunicado y coinciden exacto.

El propio archivo de Kevin ya registraba que *"Omdia publica el TAM pero las
participaciones por vendedor son de pago"* — la cifra buena estaba
identificada desde julio; faltaba usarla como denominador.

## 36.2 El share vuelve a ser puntuable

Los cuatro números son **reportados**, no estimados:

| | NVDA Data Center | TAM Omdia | Captura |
|---|---|---|---|
| 2024 | $115.2B | $123B | 93.66% |
| 2025 | $193.7B | $207B | **93.57%** |

**Salvedad declarada**: el segmento Data Center de NVDA incluye networking
(NVLink, Spectrum, InfiniBand), que no está en el denominador de chips. En
Q1 FY2027 —el único trimestre con desglose publicado— networking fue $14.8B
de $75.2B, ~20% del segmento. **NVIDIA no publica ese desglose para el año
fiscal completo** (verificado en el comunicado de FY2026: sólo da el total),
así que aplicar el ratio de un trimestre a un año sería imputar. El NIVEL se
declara como cota superior.

La VARIACIÓN sí es sólida: el mismo sesgo está en los dos años y se cancela
al restar. Por eso `MKT-SHDELTA-007` vale más aquí que `MKT-SHARE-006`.

**Lectura**: captura plana con −9 puntos básicos. Coincide con lo que el
propio comunicado de Omdia describe — ASIC a medida (TPU de Google), ASSP
mercantiles (Ascend, Groq, Cerebras) y el avance de AMD con Instinct. No es
pérdida de participación todavía, pero tampoco la expansión que un +68% de
ingresos sugeriría por sí solo.

## 36.3 Efecto medido

| | Antes | Ahora |
|---|---|---|
| Market | 1.84/20 (cob. 0.388) | **4.87/20** (cob. 0.537) |
| `MKT-TAM-001` | — | $207 000 M |
| `MKT-CAGR-004` | — | **+68.3%** |
| `MKT-SHARE-006` | NOT_SCORABLE | 93.57% |
| `MKT-SHDELTA-007` | NOT_SCORABLE | **−0.0009** |
| **raw_total** | 48.8 | **51.9** |
| **perfil** | `Avoid / Wait` | **`Speculative`** |

El perfil cambia porque `raw_total` cruza 50, el gate que venía fallando. No
es que la empresa mejorara: es que el denominador dejó de estar equivocado.

**2110 tests del engine + 104 de la capa web.**

---

# 37. El TAM era del ticker cuando debía ser del mercado (M-04)

Victor lo vio antes que yo: el TAM de Omdia se declaró en
`Entradas/NVDA.json`, así que **sólo NVDA lo tenía**. Medido el 2026-08-05:

| Ticker | Industria | Market | TAM |
|---|---|---|---|
| NVDA | Semiconductors | **4.87**/20 | $207 000 M |
| **AMD** | Semiconductors | 1.82/20 | **ninguno** |
| AVGO | Semiconductors | 2.31/20 | ninguno |

AMD vende Instinct **al mismo mercado** que el comunicado de Omdia describe
—nombra a NVIDIA, AMD, Google, Huawei, Groq y Cerebras en el mismo
denominador— y salía sin TAM. No faltaba el dato: estaba escrito en el
archivo de otra empresa.

## 37.1 `Entradas/_industrias/<slug>.json`

El TAM se hereda por `security.industry` del packet, y **el archivo del
ticker gana** en cualquier clave que repita: la industria son cimientos, no
una imposición. La validación es la misma — un `tam` sin `tam_source` y sin
tier 1-4 se cae igual aquí que en el archivo del ticker.

## 37.2 El riesgo que introduce, y su freno

Una industria de GICS es **más ancha que un mercado**. `Semiconductors` mete
en la misma bolsa a NVIDIA, que vende aceleradores, y a **Micron, que vende
memoria**. En la primera versión MU heredaba el TAM de Omdia y su
participación habría salido diminuta pero *puntuable* — un número
equivocado, que es peor que un hueco porque el hueco se ve.

Freno: la clave `_aplica_a` lista explícitamente a quién cubre. Es opcional
— cuando el mercado sí coincide con la clasificación, exigirla sería
burocracia.

| Ticker | Hereda | Por qué |
|---|---|---|
| NVDA, AMD, AVGO | **sí** | compiten en aceleradores |
| MU | **no** | memoria |
| JPM, KO | **no** | otra industria, sin archivo |

## 37.3 Efecto

Cobertura de Market: AMD y AVGO **0.188 → 0.312**. Los puntos suben poco
porque `share` y `share_history` siguen sin declararse para ellos — el TAM
es el denominador, y el numerador (ingreso del segmento comparable) hay que
investigarlo por empresa.

Lo que cambia de verdad es que **el trabajo de investigar un TAM se hace una
vez por mercado**, no una vez por ticker.

## 37.4 Estado

`engine/tests/test_tam_por_industria.py` — 6 tests: que el TAM llegue a cada
ticker listado, que uno fuera de la lista no herede nada, que sin lista
cubra a toda la industria, que un TAM sin atribución se caiga igual, que un
archivo ausente no rompa, y que el slug case con el nombre del archivo.

**2116 tests del engine + 104 de la capa web.**

---

## Cierre 2026-08-06 — El TAM es MUNDIAL y lo firma quien mide el mercado

Hicieron falta tres intentos, y los dos descartados enseñaron algo que ahora
está escrito como regla en `engine/wbj/overlay/tam_mundial.py`.

| Intento | Por qué se descartó | Medido |
|---|---|---|
| Buscar en Google y aceptar lo que salga | Devolvía el **comunicado de prensa** sobre el dato, no el dato. IDC/Omdia/Gartner venden sus informes | Mordor Intelligence colándose como fuente |
| Census de EE.UU. vía FRED | Oficial y tier 1, pero **sólo EE.UU.** — y `market.py::sam()` estrecha el TAM por geografía, o sea que lo espera mundial | AAPL: 1.900% de participación |
| Sumar los ingresos de todas las cotizadas | Mundial, pero **apila capas**: NVDA factura $216.000M que ya incluyen lo que pagó a TSMC, y TSMC entra otra vez con $119.000M | $921.000M contra ~$790.000M reales |

**Lo que funciona: preguntarle a quien mide el mercado**, en este orden.

1. **Asociación de industria** — tier 2, confianza 85. Mide su propio mercado,
   publica mundial y **gratis**, es el ORIGEN del dato y cubre **una sola capa**.
   WSTS da $795.600M de ventas mundiales de chips: facturación de chips, sin
   fundiciones ni fabricantes de equipo encima.
2. **Casa de análisis** — tier 3, confianza 70, sólo si no hay asociación.

Máximo **dos fuentes**, por decisión de Victor. Agregadores (Mordor, Grand View,
MarketsandMarkets, Statista…) se rechazan por nombre: recopilan cifras de
terceros sin firmar metodología.

### Las reglas que se validan sobre la respuesta

| Regla | Caso real |
|---|---|
| Ámbito mundial obligatorio | Un denominario regional bajo ingresos globales daba a AAPL 1.900% |
| Capa declarada | El error Gartner/NVDA: gasto de usuario final contra ingresos de componentes. Daba 39,6%, perfectamente creíble |
| Años ≠ dólares en la serie | JPM devolvió `tam_history: [2024, 2025]` |
| La serie cierra en el TAM | Si no, las dos mitades hablan de mercados distintos |
| Redirect de grounding + frase textual | Gemini cita con enlaces que caducan y no dicen de quién es la página |

### Medido el 2026-08-06

```
industria                 tier   TAM mundial      fuente
semiconductors              3    $207.000M        Omdia (ANALISTA, no se toca)
  -- sin ese archivo, resuelve solo: WSTS $795.600M, tier 2
software-infrastructure     3    $175.170M        Gartner, Data & Analytics Software
banks-diversified           3    $6,4 billones    McKinsey Global Banking Review
beverages-non-alcoholic     3    $418.000M        NielsenIQ
consumer-electronics        —    SIN TAM          hueco correcto: AAPL compite en
                                                  telefonos, computadoras y wearables,
                                                  que son mercados distintos
```

### Un arreglo que salió de medir

El suplente de OpenAI estaba **configurado pero no instalable**: `openai` no
estaba en `requirements.txt`. Cuando Gemini devolvió 429 por cuota, bebidas y
electrónica se quedaron sin denominador teniendo un suplente escrito. Instalado,
OpenAI resolvió bebidas con Gemini caído.

**Suites:** engine 2148, web 104.

# 38. Auditoría del tab de Proyecciones — ronda 4 (2026-08-06)

**Alcance:** el tab de Proyecciones completo, de la vista al endpoint y al motor.
**Base comparada:** https://github.com/infusionvictor/agente-tito-metralleta (`53d5a20`).

## 38.1 Lo que estaba MAL (y quedó resuelto)

### [x] P4-01 — El tab medía gamma DOS veces
Ya cerrado en la ronda 3 para el cargador y sus paneles (commit `a6d4fc2`), pero
sobrevivieron cuatro restos que esta ronda destapó:

| Resto | Qué se veía | Resuelto |
|---|---|---|
| Tarjeta *"Dark Pool & Flujo Institucional (Quant Data)"* vacía | marco morado + título sobre un cuerpo sin contenido: se leía como "el panel no carga", no como "el panel ya no está" | eliminada |
| Rótulo *"(volumen > OI = posicionamiento fresco)"* | la definición de Quant Data sobre los trades del sub-agente 3, que puntúa inusualidad /30 sobre la cinta | rotulado con lo que de verdad pinta |
| Subtítulo de targets *"7d–120d … GEX + flujo + dark pool + delta"* | horizontes que ya no existen y una fuente que ya no se consulta | 10d/20d/30d con los 6 pesos de su scorecard |
| `trade_plan` del *Plan de operación* | put wall, call wall, gamma flip (de `get_gex_cached` → Quant Data) y un checkpoint de flujo de `_qd_conv`, a dos tarjetas de los mismos cuatro niveles calculados por el motor de Víctor | eliminados del plan; Quant Data sigue intacto en el prompt de Full Research, que es otra pantalla |

**Por qué importa el último:** `trade_plan` se pinta en UN solo sitio y ese sitio es
este tab. Cuando los dos proveedores discrepaban no había forma de saber cuál mirar.

### [x] P4-02 — El motor de calibración no tenía diferencial
Resuelto en la ronda 3 (`diff_calib.sh`, 182 diarios).

## 38.2 Lo NUEVO de su repo, ya portado

Su commit `53d5a20` *"feat(ideas): screener más accesible para cuenta chica"*
toca `web/lib/risk.ts`. Portado literal a `engine/wbj/tito/risk.py`:

| Suyo | Antes | Ahora |
|---|---|---|
| `MIN_DTE` | 7 | **2** — deja pasar semanales; el 0DTE lo sigue tumbando `expiry_status` |
| `IDEA_UNUSUAL_THRESHOLD` | no existía | **5**, propio del screener. **No toca** el 7 institucional de `flow.py` |
| `MONEYNESS_CAP` | no existía | **0.25** — el strike dentro del ±25% del precio |
| `within_moneyness()` | no existía | portada. Ante datos faltantes **no filtra**: la cercanía es preferencia, no salvaguarda |

Lo demás del commit (piso de premium $500k→$100k, slider 10%→50%, conteo de
rechazos `lejano`) vive en su `/api/ideas` y su `ideas/page.tsx`, que **no se
portan** — están declarados en el registro de huérfanas de `auditar_tito.py`.

## 38.3 Cómo se verificó

- `diff_motor2.sh` subió de 846 a **918 casos** con el corpus malformado nuevo
  para `withinMoneyness`: basura en `strike` y en `assetPrice`, los bordes
  exactos de la banda por los dos lados, y la banda misma con `null` explícito
  —que en JS **no** activa el valor por defecto, solo lo hace `undefined`—.
  918/918 idénticos a su archivo corriendo en Node.
- 4 tests de cableado nuevos: ningún rótulo de Quant Data visible en el tab,
  ninguna tarjeta con título y sin cuerpo, el plan sin gamma ni flujo de Quant
  Data (leyendo el **código**, no el comentario que explica la eliminación), y
  el rótulo de inusualidad describiendo lo que pinta.
- 9 tests nuevos en `test_risk.py`: sus 6 de `withinMoneyness` + los 3 que fijan
  que el umbral del screener no se coma al institucional.

## 38.4 Estado

```
2.684 tests del engine · 154 de la capa web · 238 checks de auditoría · 0 fallos
store 47/47 · compute 604/604 · bars 27/27 · primitivas · cono
motor 1142/1142 · motor2 918/918 · motor3 348/349 · geo 274/274
calib 182/182 · frescura 342/342 · reloj 223/223
```

Única divergencia declarada en los 12 diferenciales: `new Date("0")` → año 2000
(parsing legacy de V8, *implementation-defined* según la spec).

**Paneles que quedan en el tab:** gráfica de Víctor · Gamma neto por strike ·
Targets por horizonte · Escenarios de precio (GEX) · Actividad inusual ·
Plan de operación. **Fetches del tab:** `/api/projection-targets` y
`/api/tito-news`. Nada más.

---

# 39. Auditoría del tab de Proyecciones — ronda 5 (2026-08-06)

**Alcance:** el tab entero, atacando lo que las cuatro rondas anteriores no
midieron. **Base comparada:** su repo en `53d5a20`.

## 39.1 El hallazgo grande: seis números sin evidencia

Todas las rondas anteriores preguntaban **"¿lo que se sirve, se pinta?"**.
Ninguna preguntó **"¿se sirve lo que ÉL muestra?"**. Ahí estaba el hueco.

El motor calcula el desglose completo de los 6 sub-agentes desde el primer día
—el spread medio y la dominancia de Convicción, el promedio por parámetro de
Inusualidad, el nocional por strike y el reparto por vencimiento de Estructura,
el IV Rank **con su fuente** y el skew del frente, el MFE/MAE y la tasa de
validación del backtest— y el payload servía **solo el titular 0-10**.

Es exactamente lo que la regla innegociable del proyecto prohíbe:

> *Sin evidencia, no hay número.*

Seis cifras en pantalla y nada detrás. Resuelto: `subagents` en el payload
(60 hojas) y `vcSubagentesHTML` en el panel, port de sus seis tarjetas —
`AggressionScoreCard`, `ConvictionCard`, `UnusualityCard`, `StructureCard`,
`IvContextCard`, `ValidationCard`— con sus veredictos y umbrales literales,
dentro del mismo `<details>` colapsado que él usa.

## 39.2 Los otros cuatro

| # | Hallazgo | Resuelto |
|---|---|---|
| P5-02 | `conviction_trades` servía solo el **contador**. Esas filas son el universo sobre el que puntúan Convicción, Inusualidad y Contexto IV: tres de seis categorías sin nada que las respalde | `conviction_rows` (25 de mayor premium) + su `ConvictionTransactions` |
| P5-03 | Mi propio renderizador nuevo tiraba 4 hojas: `by_expiration.trades`/`.premium` (la muestra que sostiene cada IV media) y `expirations.call_notional`/`.put_notional` (dos vencimientos con el mismo nocional pueden ser tesis opuestas) | pintadas — lo detectó el test de hojas, no yo |
| P5-04 | `strike` y `size` llegan **crudos** de MarketSnack y se interpolaban sin escapar en las dos tablas de filas. El port es literal: no valida tipos | `_vcEsc` en los 4 puntos + test |
| P5-05 | La flecha del `<details>` dependía de la variante `group-open:` de Tailwind, servida por CDN sin versión fijada. Si no resolviera, se verían las **dos** flechas | CSS propio, sin depender de nadie |

## 39.3 Lo que ahora es auditable y antes no

**Registro de sus 39 componentes** (`auditar_tito.py` §9-ter). Cada uno de los
componentes que renderiza su `web/app/page.tsx` está mapeado: **22 con
consumidor** en el tab, **17 declarados con motivo escrito**. Con `TITO_ROOT`
apuntando a su clon, el registro se contrasta contra su carpeta real — un
componente nuevo suyo que nadie declare hace **fallar** el check. Se probó: la
primera versión inventó un `ChartPanel` en `chart/` y se le escaparon
`ChartCrosshair` y `PriceChart`; el check lo dijo.

**Cobertura por HOJA del payload** (`test_cada_hoja_del_detalle_de_subagentes_se_pinta`).
El test viejo miraba claves raíz, y `subagents` es **una** clave con 60 hojas
dentro. El nuevo recorre el árbol servido y exige que cada hoja se lea en el
renderizador o esté declarada con su motivo (6 lo están).

## 39.4 Estado

```
2.684 tests del engine · 159 de la capa web · 271 checks de auditoría · 0 fallos
store 47/47 · compute 604/604 · bars 27/27 · primitivas · cono
motor 1142/1142 · motor2 918/918 · motor3 348/349 · geo 274/274
calib 182/182 · frescura 342/342 · reloj 223/223
```

Nada mío entra en el tab salvo lo que Kevin pidió: su email y su perfil de
inversionista. Las fuentes son Massive (cadena + barras) y MarketSnack (cinta).

## 39.5 Lo que destapó la fusión con `main`

`main` avanzó 22 commits mientras esta rama avanzaba 63. Al fusionar salieron
tres cosas que ninguna de las dos ramas veía sola:

| Hallazgo | Detalle |
|---|---|
| **`/api/projection-targets` duplicado** | La fusión resucitó el endpoint viejo de Quant Data (`compute_horizon_targets` + walls y convicción de QD). Dos `@app.get` con el mismo path **no dan error** en FastAPI: gana el primero y el segundo queda como código que nadie ejecuta y que en la siguiente lectura parece la implementación vigente. Borrado. |
| **La excepción cruda iba al navegador** | `main` añadió `test_route_safety.py` con una regla correcta: este servicio liga `0.0.0.0` en Render, y un `str(e)` de httpx que escapara de `raise_for_status()` llevaría la URL **con la clave**. Dos rutas de Proyecciones caían ahí. |
| **…pero ocultarlo todo tampoco servía** | Con `_error_publico` a secas, "Falta MASSIVE_API_KEY" pasaba a ser "no se pudo completar" y mandaba a leer logs de Render para descubrir una variable de entorno. `_error_de_fuente` deja pasar `MassiveError`/`MarketSnackError` —mensajes que escribimos nosotros, y que el centinela de §8 **demuestra** que no llevan credenciales— y manda todo lo demás al camino ciego. |

`main` había llegado por su cuenta a la misma conclusión de la ronda 4 (fuera
Quant Data del plan de operación), pero dejando el andamio muerto: un
`flow_override = False` sin usos y un `try` con `_pw/_cw/_fl` fijados a `None`
cuyos tres `if` no pueden entrar nunca. Se conservó la eliminación limpia.

---

# 40. Auditoría del tab de Proyecciones — ronda 6 (2026-08-06)

**Alcance:** el tab entero **y todo lo auditado en las cinco rondas previas**,
con dos preguntas nuevas que ninguna se había hecho. **Base:** su `53d5a20`.

## 40.1 El error más caro: el spot salía de la fuente equivocada

Su `page.tsx` elige el precio así, y en este orden:

```
company?.price ?? chainMeta?.underlyingPrice ?? bars[bars.length - 1].close
```

El port **se saltaba el primer eslabón** y usaba el segundo. No es lo mismo:

- `company.price` es el **snapshot del subyacente** (`/v2/snapshot/...`, última
  operación: `day.c ?? min.c ?? prevDay.c`).
- `underlying_price` es el precio con el que Massive calculó **esa cadena**.

Cuando la cadena se sirve de caché o el papel se movió después de calcularla,
no coinciden. Y el spot no es un dato más: ancla **los nodos del GEX**, la
ventana de ±20% (`NEAR_SPOT_PCT`) que decide qué strikes entran, **los
niveles**, **el cono** y **los tres targets**. Cogerlo del eslabón equivocado
mueve el panel entero en silencio.

**Resuelto:** portado su `fetchCompany` → `massive.fetch_company`, y el spot
resuelto en su orden. Con su guarda: un precio ≤ 0 (o no numérico) no gana —
él corta con `if (!spot || spot <= 0) return null`.

## 40.2 Lo que se servía y no se pintaba (14 hojas más)

El test de hojas de la ronda 5 solo cubría `subagents`. Extendido al payload
**entero** (≈120 hojas), aparecieron 14:

| Qué | Por qué importaba |
|---|---|
| `structure.top_strikes.*` (7 columnas) | **la tabla entera** de "Dónde se acumula" de su `StructureCard` — servida desde el primer día, nunca pintada |
| `structure.dominant_side` / `call_pct` / `put_pct` | su barra de reparto calls/puts con "Dominan los CALLS" |
| `flow_clusters.unidirectionality` | **la** métrica del racimo: qué parte del premium va en una sola dirección |
| `flow_clusters.call_premium` / `put_premium` | lo que la sostiene |
| `gex.nodes.trade_premium` / `trade_count` | la parte del nodo que viene del **tape**: distingue un muro de open interest viejo de uno que se construye hoy |
| `predictions.*.calibration.samples` | sobre cuántas predicciones vencidas se midió el ajuste. Un "+2.1%" sin la muestra no se puede juzgar |

Faltaba además `top_strikes.pct_of_total`, que ni se servía.

## 40.3 La salvaguarda de liquidez era más débil que la suya

Su `VeredictoCard` tiene una regla de prioridad que es lo **primero** que hace:
si hay `caveat`, **no se da dirección** — se muestra *"Datos no fiables — no
operar"* y nada más.

Aquí se pintaban los tres targets igual y el caveat quedaba de nota al pie en
9px debajo. Es lo contrario de la regla del proyecto: *la confianza nunca
convierte un desconocido en un score favorable*. **Resuelto**, y con ello se
portó su veredicto completo: dirección en lenguaje llano (📈 *Probablemente
SUBE* / 📉 *BAJA* / ➡️ *LATERAL*), `confLabel` con sus cortes **66 / 33**, el
horizonte en prosa y el tooltip completo del chip de calibración.

## 40.4 Dos cotejos automáticos nuevos (§9-quater)

Lo que antes se revisaba a ojo, ahora falla solo:

- **Sus 32 módulos de `web/lib`**: 24 portados, 8 declarados con motivo. Con
  `TITO_ROOT` se contrasta contra su carpeta real.
- **Sus 32 constantes numéricas exportadas**, valor a valor. No se puede hacer
  a ojo: están repartidas en 13 archivos, y una mal copiada **pasaría verde**
  en los dos lados porque los tests portados usan la constante, no el literal.
  Destapó 3 que existían con **otro nombre** (`CLUSTER_WINDOW_MS`,
  `HISTORY_DAYS`, `IV_HISTORY_DAYS`): mismos valores, pero invisibles para
  cualquier cotejo. Renombradas a las suyas.

También se cotejaron sus **104 funciones exportadas**: todas presentes salvo
las cuatro de Massive que solo usan `/api/bars`, `/api/logo` y `/api/wheel`
—rutas que no se portan— y `fetchCompany`, que es la de §40.1 y ahora está.

## 40.5 Estado

```
2.716 tests del engine · 197 de la capa web · 277 checks · 0 fallos
store 47/47 · compute 604/604 · bars 27/27 · primitivas · cono
motor 1142/1142 · motor2 918/918 · motor3 348/349 · geo 274/274
calib 182/182 · frescura 342/342 · reloj 223/223
```

Cero funciones muertas en el tab. Las 7 constantes de su `chartGeometry` y sus
4 feeds de noticias coinciden literalmente.

## 40.6 La cadena de datos, cotejada línea a línea con su cliente

Revisada la fuente de **cada** número del tab contra su `massive.ts` y su
`marketsnack.ts`. Dos divergencias más, ya corregidas:

| Qué | Él | El port (antes) |
|---|---|---|
| **Rango de barras** | `new Date()` → `toDateStr` = **UTC** | `date.today()` = zona **local** del servidor → al oeste de Greenwich pedía un día menos, al este uno más |
| **`price: 0` del snapshot** | `??` **no** salta el 0 → `if (!spot \|\| spot <= 0) return null`: **no da lectura** | bajaba al precio de la cadena — el fallback callado que la cabecera del módulo llama peor que un error |

Ahora el `??` se copia tal cual, incluido lo que **no** hace: solo salta
`null`/`undefined`. Un 0 se queda en 0 y corta, con el motivo escrito
(`snapshot 0 · cadena …`) en vez de publicar un scorecard sobre un precio que
nadie pidió.

**Lo que ya coincidía y queda verificado:**

- `Authorization: Bearer` + `cache: no-store` en Massive; `Accept` + `Cookie`
  en MarketSnack.
- Cadena: `/v3/snapshot/options/{T}?limit=250`, paginación por `next_url`,
  tope `MASSIVE_MAX_PAGES=40`, y `throw` en cualquier `!res.ok` (404 incluido).
- Barras: `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true&sort=asc&limit=500`.
- Snapshot: `/v2/snapshot/locale/us/markets/stocks/tickers/{T}`, y el precio
  como `day.c ?? min.c ?? prevDay.c`.
- Tape: `/api/flow_feed` con `filter[scope]=all`, `filter[symbol][]`, `period`,
  `filter[premium][gte]` y `next_page_token`, en ese orden.
- Noticias: sus 4 feeds RSS + `/v2/reference/news` de Massive.

## 40.7 Lo viejo de Vertex que seguía en pantalla (y el motivo de Massive)

Kevin reportó dos cosas al usarlo. Las dos eran reales.

### El tab pedía teclear el ticker

En su app **no hace falta**: su `HeaderBar` lleva `QUICK = ["TSLA","NVDA","SPY","AAPL"]`
y se hace clic. Aquí había que escribir el símbolo antes de ver nada. Portados
sus cuatro botones, con el resaltado del que está cargado.

Y con ellos salió el resto del cascarón viejo, que ninguna ronda había mirado
porque todas auditaron el **contenido** y no la **cabecera**:

| Era | Ahora |
|---|---|
| "Proyecciones GEX · Niveles institucionales de opciones…" | **Tito Metralleta · AI Options Agent**, con los 6 sub-agentes en el subtítulo |
| "Escribe un ticker y presiona **Proyectar**" | su copy: *"Analiza un ticker — Elige un ticker arriba (o búscalo) y el agente armará el sentiment score, el flujo inusual, los muros de strikes y el detalle completo de cada sub-agente."* |
| Botón de refresco: *"Refrescar GEX y **dark pool** ahora"* | Quant Data salió del tab hace tres rondas; el rótulo seguía prometiéndolo |
| Banner "Generar tesis AI completa" | fuera: es el agente de Vertex, no el suyo |
| Panel "Plan de operación" (`/api/analyze`) | fuera: mismo motivo |

### "Massive rechazó la API key"

El mensaje juntaba **401** y **403**, que son dos problemas distintos y se
arreglan distinto:

- **401** — la credencial no vale: falta, está mal pegada o fue revocada.
- **403** — la credencial **vale**, pero el plan no cubre ese endpoint. Cambiar
  la key no arregla nada. Massive hereda el modelo de Polygon, donde el
  snapshot de acciones, el de opciones y los aggregates se contratan aparte.

Ahora cada uno dice lo suyo **y la ruta que falló**. Sin la ruta, el mensaje
mandaba a revisar una credencial que puede estar perfecta.

Además, `/api/tito-health` prueba ahora el snapshot **por separado**. Era el
único fallo silencioso que quedaba: `fetch_company` se traga su error y
devuelve `None`, así que si el plan no cubre `/v2/snapshot/...` el panel sigue
funcionando con el precio de la cadena y nadie se entera de que el mejor precio
disponible no se está usando.

El centinela de credenciales se extendió a la rama nueva: la ruta se recorta
antes de la query y la key va en la cabecera, así que no puede filtrarse por
ahí.

## 40.8 El tab ya no exige un ticker para hacer algo

Kevin preguntó si al entrar al agente de opciones Víctor tiene que analizar un
ticker. **Comprobado en su código, no supuesto:** su `page.tsx` arranca con
`useState<string | null>(null)` y enseña *"Analiza un ticker"*. O sea, **sí**
lo pide… pero en **esa** pestaña.

Su app tiene **cuatro**, en su `NavTabs`:

| Pestaña | ¿Pide ticker? |
|---|---|
| **Ticker** (el dashboard) | sí — es lo que aquí era el tab entero |
| **Ideas** | **no** — escanea el flujo de TODO el mercado |
| Wheel | otra estrategia, no se porta |
| Time & Sales | sí |

Lo que Kevin recordaba es **Ideas**: la que aterriza con resultados sin que
escribas nada. Portada como `/api/tito-ideas` + `renderProjIdeas`, y las dos
conviven en el mismo tab: **sin ticker manda Ideas, con ticker manda el
scorecard**, y un clic en cualquier fila abre el análisis completo de ese
ticker.

El pipeline es el suyo, en su orden y con sus parámetros del commit `53d5a20`:
`fetch_market_flow` (sin filtro de símbolo, piso de $100K server-side, 8
páginas, período `1d`) → `classify_flow` → capa 1 de `risk.py`
(`is_tradeable_idea` con el umbral del **screener** ≥5, no el 7 institucional)
→ `within_moneyness` (±25%) → el historial por ticker con `validation_score`.
Y su desglose de rechazos con los cinco motivos, porque sin él *"0 ideas"* y
*"el mercado está tranquilo"* se ven igual en pantalla.

**El sizing no se calcula en el servidor**, igual que en su ruta: el saldo de
Kevin no sale del navegador. La ruta devuelve los griegos; el techo de
contratos lo aplica quien tenga el perfil delante. Es la única razón por la que
`size_flow` sigue siendo una función declarada sin llamador — y ahora el
registro de §9 lo dice con ese motivo, no con el de antes.

Con esto, **tres huérfanas dejan de serlo**: `fetch_market_flow`,
`is_tradeable_idea` y `within_moneyness`, que llevaban desde la ronda 4
portadas y sin nadie que las llamara.

## 40.9 Cuatro síntomas, dos causas — y una es mía

Kevin reportó cuatro cosas. Reproducidas corriendo el motor **sin cinta**, que
es su estado real: `score 50 · activos 1/6 · caveat "Solo 1 de 6 sub-agentes"`.

### Causa A — la cookie de MarketSnack no está sirviendo (no es un bug)

`MARKETSNACK_COOKIE` **no es una API key**: es una cookie de sesión y **caduca**.
Sin ella se apagan **4 de los 6** sub-agentes (Agresividad, Convicción,
Inusualidad y Contexto IV), y con ellos el 6º (Confirmación) porque su historial
se alimenta de esos mismos flows. Queda Estructura, que sale de la cadena.

Eso explica dos de los cuatro síntomas:

- **"solo funciona 1 de 6 sub-agentes"** — exacto, y es lo que toca.
- **"me sigue pidiendo escribir un ticker"** — el screener de Ideas usa la
  cinta, así que fallaba… y el mensaje de error **mandaba a escribir un
  ticker**, que es un consejo falso: con la cinta caída el análisis por ticker
  también sale cojo. Ahora dice el problema y cómo se arregla.

Lo que sí era un defecto: el aviso viajaba dentro de `warnings`, que el panel
pinta **en 9px al final**. El resultado era un scorecard que se ve entero
—score, veredicto, targets— sostenido por UNA categoría de seis. Ahora
`subagentes_apagados` nombra cada categoría, su fuente y el arreglo, y se pinta
en un banner **antes que nada**.

### Causa B — un fallo mío de la ronda 6

Sus **dos** `caveat` no son el mismo aviso, aunque su archivo los meta en el
mismo campo:

```ts
const caveat = lowLiquidity
  ? "Cadena de baja liquidez: la predicción se marca como NO FIABLE y no debe usarse para operar."
  : active < 6
    ? `Solo ${active} de 6 sub-agentes tienen dato; la confianza está recortada.`
    : null;
```

El primero es un **"no operes"**; el segundo dice que el número **vale menos**,
no que no valga. La ronda 6 los colapsó en uno y escondía los targets también
con cobertura parcial — que es **exactamente** el estado en el que deja el panel
una cookie caducada. De ahí los otros dos síntomas: sin targets y sin horizonte.

Ahora se separan con `gex.low_liquidity`, que el motor ya traía:

| Caso | Veredicto | Targets |
|---|---|---|
| baja liquidez | rojo, *"Datos no fiables — no operar"* | **ocultos** |
| cobertura parcial | lectura normal + línea ámbar con su caveat | **visibles** |

Y de paso, lo que Kevin pedía sobre el plazo: el selector lleva el rótulo
**"Horizonte"**, el bloque dice **"Targets a N días (Esta semana / 2 semanas /
1 mes) · desde $X"**, y cada card repite **"a N días"** — un `$180` suelto no
puede quedarse sin fecha.

---

# 41. Las cuatro pestañas de Víctor, dentro de Proyecciones (2026-08-06)

Kevin: *"dentro de proyecciones aún no me das el tab de Ticker, Ideas, Wheel y
Time & Sales"*. Tenía razón: iban dos de cuatro, y sin su navegación.

## 41.1 Lo que faltaba

Su `NavTabs.tsx` declara cuatro vistas. En su app son cuatro páginas
(`/`, `/ideas`, `/wheel`, `/flow`); aquí son cuatro paneles del mismo tab, con
el mismo orden y los mismos iconos.

| Pestaña | Estaba | Ahora |
|---|---|---|
| 📈 **Ticker** | sí, pero mezclada con Ideas | panel propio |
| 💡 **Ideas** | sí (ronda anterior) | panel propio |
| 🎡 **Wheel** | **no** | `/api/tito-wheel` + `renderProjWheel` |
| ⚡ **Time & Sales** | **no** | `/api/tito-tape` + `renderProjTape` |

## 41.2 Wheel — 1.100 líneas suyas portadas

Cuatro módulos nuevos, todos literales:

- **`wheel.py`** — presets, cascada de prima, salvaguarda de liquidez, métricas
  y el score 0-100 (rendimiento 30 · IV Rank 20 · colchón 25 · liquidez 15 ·
  earnings 10).
- **`wheel_universe.py`** — sus **40 símbolos curados** con su tier y su razón,
  más `afford_of` / `sort_by_afford_then_score` de `wheelAfford.ts`.
- **`earnings.py`** — el estimador del próximo reporte por cadencia de
  `filing_date` (~91 días), porque el plan de Massive no trae calendario.
- **`massive.fetch_wheel_chain`** — cadena de puts acotada al DTE del preset,
  anclada al día de mercado **ET** y filtrada a **OTM** (un put ITM no es un
  cash-secured put de Wheel: es otra cosa).

**Dos cosas de este motor se leen al revés que el resto del agente**, y lo
escribe él:

- La banda de **IV Rank está invertida** respecto al sub-agente 5. Allí el pico
  está en 16-30 porque el agente **compra** y quiere vega barata; la Wheel
  **vende** y quiere la volatilidad cara. Hay un test que lo fija en las dos
  direcciones a la vez.
- Un **rendimiento anualizado alto se castiga** (>60% → 10/30). Un screener que
  ordena por prima pone arriba justo las acciones a punto de desplomarse.

**Lo que NO viaja:** su `wheelAfford.ts` corre en el cliente porque el saldo
vive en localStorage. La ruta sirve el **colateral** de cada candidato; quién
puede pagarlo lo decide quien tenga el saldo delante. Hay un test que registra
el JSON entero buscando `affordable`, `shortfall`, `account_size` y `cash`.

## 41.3 Time & Sales

`/api/tito-tape` — la cinta cruda ya clasificada por los sub-agentes 1-3. A
diferencia del scorecard, **no agrega nada**: cada operación con su hora, su
lado de ejecución, su premium, sus griegos, su puntaje de inusualidad y las
cinco marcas (repetido, multileg, sobre el ask, bajo el bid, volumen > OI).

## 41.4 Carga bajo demanda

Entrar a Proyecciones **no dispara cuatro escaneos**. Entre Wheel (40 tickers ×
2 llamadas a Massive) e Ideas (el mercado entero) sería quemar la cuota de
golpe. Cada pestaña carga la primera vez que se abre, y el buscador de ticker
se esconde en Ideas y Wheel — que escanean el mercado entero, y un cuadro de
símbolo ahí promete un filtro que no existe.

## 41.5 Lo que destapó la auditoría al cerrar

El registro de huérfanas falló, y bien:

- `bs_delta`, `implied_vol` y `cached_daily_bars` estaban declaradas como *"solo
  las llama su wheel.ts, que no se porta"*. **Ya no**: ahora las llama el port.
- Aparecieron dos nuevas sin declarar. `sort_by_afford_then_score` es del
  cliente (el saldo). `atm_iv` **está huérfana en su propio repo**: la exporta y
  no la usa ni su `/api/wheel` ni su `wheel/page.tsx` — el escaneo saca su IV de
  respaldo de la volatilidad **realizada**, no de la IV del strike ATM.

Y el guardián de redondeo cazó un `round()` de Python en `wheel.py` que debía
ser `js_round`: una fuerza de soporte de 34.5 se habría leído **34** aquí y
**35** en su pantalla — el mismo soporte, descrito con dos números.

## 41.6 Estado

```
2.721 tests del engine · 231 de la capa web · 277 checks · 0 fallos
28 de sus 32 módulos portados · 34 constantes numéricas idénticas
27 de sus 39 componentes con consumidor
```

## 41.7 Las pestañas no funcionaban, y el refresco pasa a ser continuo

Kevin, probándolo: *"cuando presiono ideas, wheel o time and sales no me sale
nada y sale todo como el fondo. Y se van las opciones de elegir otra opción"*.

### El fallo: anidado

`projNav` y los tres paneles nuevos quedaron **dentro** de `projPaneTicker`.
Al abrir Ideas se ocultaba el padre — y con él la navegación, los otros tres
paneles y todo lo demás. Pantalla en negro y sin forma de volver.

Fue mío, del commit anterior: metí los paneles con un reemplazo de texto sobre
un ancla que ya estaba dentro del panel Ticker, y ningún test miraba la
**profundidad** del DOM. Ahora hay uno que la mide y exige que los cuatro
paneles sean hermanos y que ni la navegación ni el buscador cuelguen de ellos.

### Y de paso, su cabecera de verdad

Su `HeaderBar.tsx` lleva `<NavTabs />` **dentro**, en este orden:

```
marca → NavTabs → tickers rápidos → buscador
```

Por eso en su app la navegación no desaparece nunca: vive en la barra de
arriba, no en un panel. Reconstruida con ese orden, que además responde a lo
otro que pedía Kevin — *"quiero que me salgan las opciones junto a poner un
ticker"*: es exactamente donde él las tiene.

### En vivo, sin botones

Fuera el botón de recargar y la casilla "auto". Con una precisión que hay que
decir en voz alta: **"tiempo real" no es posible aquí, y no por vagancia.**

- **Massive** — REST. Snapshots de cadena, barras y precio. Sin websocket.
- **MarketSnack** — REST. `/api/flow_feed` paginado. Sin websocket.
- **Su propia app no refresca nada**: cero `setInterval`, cero sockets,
  verificado sobre las cuatro páginas. Una búsqueda, una foto.

Lo máximo que dan las dos APIs es **sondeo**, y eso es lo que hay. Por eso el
indicador enseña la **hora del dato** (*"en vivo · hace 12s"*) en vez de un
punto verde perpetuo: un punto verde sin hora prometería streaming.

Tres reglas para que sondear no queme la cuota:

| Regla | Por qué |
|---|---|
| Solo la pestaña **activa** | las otras tres se quedan con su foto hasta que vuelvas |
| Solo con la pestaña del navegador **visible** | sondear en segundo plano gasta cuota para nadie |
| Cadencia de **15 minutos** para las cuatro | decisión de Kevin, y encaja con la fuente |

**Por qué 15 y no menos.** Los planes de datos de Massive sirven la cotización
con hasta 15 minutos de retraso. Sondear más rápido no trae dato nuevo: trae el
**mismo** dato otra vez y gasta cuota. El tooltip del indicador lo explica ahí
mismo, para que 15 minutos no se lea como lentitud del panel.

Conviene tener el número de la pestaña más cara: **Wheel son 40 tickers × 2
llamadas a Massive**, ~80 por barrido, ~320 en una hora si te quedas mirándola.
Las otras tres van de 1 a 3 llamadas. Como solo se refresca la pestaña ACTIVA y
solo con el navegador visible, ese techo únicamente se toca si dejas Wheel
abierta.

Con el mercado cerrado todo baja a **3600s**: el dato no cambia en 16 horas,
pero la vista despierta sola cerca de la apertura sin haber estado pidiendo
datos idénticos toda la noche.

## 41.8 El tab no se armaba al entrar por el menú

Kevin mandó una captura de cómo debe verse —marca, las cuatro pestañas, los
tickers rápidos y el buscador— y dijo: *"no quiero que cuando presione
proyecciones salga solo entrar el ticker"*.

**La causa:** la inicialización que pinta todo eso (`vcPintaNav`,
`vcPintaQuick`, `vcAbreTab`, `vcArrancaVivo`) colgaba de **`cmdKey`, la barra
de comandos (Cmd+K)**. Entrando por el menú —que es como se entra— no la
llamaba nadie: quedaba el DOM crudo, con `projNav` y `projQuick` vacíos, y el
único texto visible era *"Analiza un ticker"*. Todo el trabajo estaba hecho y
sin llamador.

Es el mismo patrón que la auditoría lleva seis rondas persiguiendo —código
portado que nadie ejecuta— pero en el lado del navegador, donde el registro de
huérfanas no llega.

**Resuelto:** la inicialización vive ahora en `switchView`, por donde pasan las
**cuatro** entradas al tab (menú de escritorio, dos del menú móvil vía
`mobileGo`, y el atajo desde el reporte). Y es idempotente: entrar diez veces
no relanza diez escaneos ni deja diez temporizadores.

Cuatro tests nuevos lo fijan, incluido uno que prohíbe que nadie le quite el
`hidden` al tab a mano sin pasar por `switchView` — que es justo el atajo que
volvería a dejar el DOM crudo.

## 41.9 Auditoría del área de Wheel — «0 de 40 · 40 sin cadena»

Kevin mandó la captura: los **40** símbolos caídos. Cuarenta de cuarenta no es
el mercado, es algo sistemático.

### Fallo 1 (segunda vuelta) — faltaba el TERCER eslabón del spot

`fetch_wheel_chain` saca el precio de `underlying_asset.price` **de la cadena
de opciones**. La ronda 6 destapó justamente que ese campo no es fiable en esta
cuenta —por eso el tab de Ticker pide el precio al snapshot del subyacente— y
en Wheel se había quedado como fuente **única**. Si Massive lo omite:

```
chain.spot is None  →  los 40 al contador de fallos
```

El primer intento le puso `fetch_company` de respaldo. **No bastó**, y el
propio desglose del fallo 2 lo destapó a la primera:

```
40  sin precio del subyacente
    SPY: ni la cadena ni el snapshot del subyacente trajeron precio
```

O sea: en esta cuenta **`fetch_company` tampoco responde**. Su `page.tsx`
resuelve el spot con **tres** eslabones, no dos:

```
company?.price ?? chainMeta?.underlyingPrice ?? bars[last].close
```

Faltaba el tercero — y es justo el que nunca falla, porque las barras ya se
descargan aquí para los niveles y el IV Rank: **el último cierre sale gratis**.
Reordenado el worker para bajar las barras antes del spot.

Y `fetch_company` **sale** de este escaneo: son 40 peticiones extra cada 15
minutos a un endpoint que en esta cuenta no responde. Donde sí compensa —el tab
de Ticker, UNA petición— se mantiene su precedencia entera. Para strikes a
30-45 días, una fracción de punto en el spot no mueve la banda de delta.

**Reproducido el escenario exacto de Kevin** —cadena sin `underlying_asset` y
snapshot devolviendo 403— y da **40/40**.

> **Hallazgo colateral, y no menor:** que `fetch_company` no responda significa
> que el tab de **Ticker** lleva tiempo usando su segundo eslabón (el precio de
> la cadena) sin decirlo. Funciona, pero no con el mejor precio disponible. El
> check `massive.snapshot` de `/api/tito-health` existe precisamente para ver
> esto.

### Fallo 2 — un contador para tres desenlaces, con la etiqueta equivocada

*"40 sin cadena"* juntaba tres cosas que se arreglan de forma distinta:

| Lo que pasó | Qué hay que hacer |
|---|---|
| la fuente rechazó (401/403/filtros) | mirar la cuenta o el plan |
| la cadena vino vacía de verdad | nada, ese papel no tiene puts ahí |
| cadena llena, ningún strike en la banda de delta | **cambiar de preset** |

El tercero **ni siquiera es un fallo**: es el preset diciendo que no hay nada de
su gusto en ese papel. Rotularlo "sin cadena" mandaba a revisar la API cuando
había que tocar un botón.

Ahora hay seis motivos con nombre, su conteo y **un ejemplo real de cada uno**
—que es lo que dice si el 403 es del plan o de los filtros—, con el mismo
contrato que el desglose de rechazos del screener de Ideas.

### Fallo 3 — carrera entre hilos

El worker mutaba `fallidos` y `todos` desde los **6 hilos** del pool. Un
`contador += 1` concurrente pierde cuentas en silencio, y ese contador es justo
lo que se enseña en pantalla. Ahora cada hilo **devuelve** su resultado y la
suma se hace fuera, en un solo hilo.

### Lo verificado y sano

- La ventana de DTE se calcula bien: día de mercado **ET** + 30/45 días.
- El motor puro corre entero sin lanzar, con la capa HTTP doblada.
- Los tres presets llevan sus bandas literales, y el score castiga el anualizado
  >60% e invierte la banda de IV Rank respecto al sub-agente 5.
- El saldo del usuario sigue sin viajar.

## 41.10 De dónde sale cada dato de Wheel — y por qué salía vacío

Kevin: *"hay columnas vacías, Víctor no lo tiene así. Verifica de dónde saca
cada información."* El mapa completo, columna por columna:

| Columna | Fuente exacta |
|---|---|
| Ticker | `WHEEL_UNIVERSE` — sus 40 símbolos curados, lista suya, editada a mano |
| Contrato (strike + vencimiento) | Massive `/v3/snapshot/options/{T}?contract_type=put&expiration_date.gte/lte` |
| DTE | calculado: vencimiento − día de mercado **ET** |
| **Prima** | `pick_premium`: **bid** → último → modelo, con recorte 0% / 10% / 15% |
| Cobras | `prima × 100` |
| Colateral | `strike × 100` |
| Anualizado | `(cobras/colateral) × (365/DTE)` |
| P(sin valor) | `prob_above(spot, strike, iv, dte)` |
| Score | 5 partes: anualizado 30 · IV Rank 20 · colchón 25 · liquidez 15 · earnings 10 |
| spot | `chain.underlying_price ?? bars[último].close` |
| IV Rank | volatilidad **realizada** de las barras contra su propio año |
| colchón | `find_levels` sobre las barras diarias |
| earnings | cadencia de `filing_date` de `/vX/reference/financials` (~91 días) |

**La columna que lo tumba todo es la Prima**, y su origen es el **bid**. De ahí
las tres correcciones de esta ronda:

### 1 · «35 sin barras diarias» era un límite de tasa

`cached_daily_bars` hace `.catch(() => [])` —comportamiento suyo, y ahí está
bien— pero eso convierte un 429, un 403 y un ticker sin datos en **la misma
lista vacía**. En un escaneo de 40 símbolos esa es la diferencia entre "espera
un momento" y "revisa tu plan".

40 tickers × 2 llamadas en 6 hilos es una **ráfaga de ~80 peticiones en
segundos**, y los planes de Massive limitan por minuto. La firma era
inconfundible: 5 pasaron y 35 cayeron de golpe.

Resuelto con reintento y espera creciente **solo ante 429** —un 403 no mejora
esperando, y reintentarlo solo retrasa el diagnóstico— y capturando el motivo
real por el parámetro `fetch` que el propio store expone.

### 2 · «bloqueado: sin bid» en todas las filas

La cadena de Massive **no está devolviendo `last_quote`** en esta cuenta. Sin
horquilla pasan dos cosas, las dos malas y ninguna evidente:

- no hay `mid`, así que la **IV implícita no se despeja** y el delta se calcula
  con volatilidad realizada → los strikes se salen de la banda del preset;
- y si alguno entra, `liquidity_block` lo tumba por `sin_bid`.

Dos rutas, el mismo motivo, y ninguna lo nombraba. Ahora hay un motivo propio
—`sin_horquilla`— que lo dice: `last_quote` es un **añadido de plan** en
Massive, no es la key y no mejora reintentando. Las demás pestañas no lo
necesitan: **Ticker e Ideas funcionan igual**.

> La prima **no** se sustituye por el precio teórico. `pick_premium` tiene esa
> cascada, pero `liquidity_block` corre **antes** y bloquea — es su diseño, y
> es correcto: la prima que cobrarías ES el bid, y enseñar un Black-Scholes en
> su lugar sería exactamente el número que no puedes cobrar.

### 3 · La pared de filas vacías

Una fila bloqueada no lleva **ningún** número —esa es su regla—, así que cien
seguidas son cien líneas vacías. Ahora se enseñan 8 como muestra y el resto se
resume arriba, con el conteo por motivo y el porqué de cada uno.

## 41.11 La solución al «sin bid»: verificado qué tiene Víctor de verdad

El diagnóstico de §41.10 decía que sin `last_quote` no hay Wheel. **Estaba
incompleto**, y la respuesta estaba en su propio código.

### Lo que dice su repo, en dos sitios que se contradicen

La cabecera de `massive.ts` afirma que su plan **sí** devuelve `last_quote`.
Pero su `compute.ts` —el archivo que de verdad lee la cadena— dice lo
contrario, y es el que manda:

> *"La fórmula del agente pide **BID**, pero el plan actual de Massive **NO
> devuelve quotes**, así que cae a `last_trade` → `day.close` → `day.vwap`."*

O sea: **su plan tampoco sirve horquilla**, y esa cascada de tres niveles
existe exactamente por eso.

### Dos hallazgos que salen de ahí

**1 · Un hueco real del port.** `fetch_wheel_chain` leía **solo**
`last_trade.price`. Su `contract_price` tiene tres niveles, y el segundo
—`day.close`— es el que salva cualquier contrato que hoy no haya negociado:
fuera de sesión, todos. Ahora la cadena de Wheel usa **su** función.

**2 · Su `pickPremium` tiene ramas inalcanzables.** La cascada `bid → último →
modelo` lleva recortes del **0% / 10% / 15%**… pero `wheelCandidates` bloquea
por `sin_bid` **antes** de llamarla. Nadie escribe dos recortes que nunca se
aplican: existen para el caso que su propio `compute.ts` describe.

### La solución, y por qué no rompe su salvaguarda

`allow_missing_quote` — **divergencia declarada, `False` por defecto**, así que
lo que se coteja contra su repo sigue siendo su código literal. Con ella, un
contrato sin `bid` pero **con precio real de la cascada** deja de bloquearse y
la prima sale por su rama `ultimo`, con su recorte del 10%.

Lo importante: **la salvaguarda no se pierde, se traslada al score.** El
`spread_pct` sigue sin poder medirse, y su `_liquidity_part` ya sabe qué hacer
con ese `None`: lo trata como `inf` → banda *"insuficiente"* → **0 de 15
puntos**. El propio puntaje castiga no conocer la liquidez, que es justo lo que
el bloqueo protegía. Medido: el mismo contrato pasa de **74/100** con horquilla
a **50/100** sin ella.

En pantalla se dice entero: cuántos símbolos se puntuaron sin horquilla, que la
prima sale del último precio y no del bid, que la liquidez cobra 0/15 por eso,
y **«verifica la prima en tu bróker antes de vender»**. La columna de prima
lleva asterisco y color ámbar cuando su fuente no es el bid.

**Reproducido de punta a punta** con una cadena sin `last_quote` y `day.close`
poblado: **40 de 40 símbolos con candidatos**, prima de fuente `ultimo`,
liquidez 0/15.

## 41.12 «¿Cómo tenemos datos 100% reales?» — la sonda que lo contesta

Kevin, tras pagar Massive: *"¿cómo hacemos para tener los datos que
necesitamos y 100% confiables y reales? Verifica."*

**Yo no puedo verificarlo desde aquí, y eso hay que decirlo primero.** El
contenedor bloquea `api.massive.com`. Todo lo que llevo dicho sobre qué
devuelve su plan es **inferencia** a partir de los síntomas de sus capturas —
buena inferencia, pero inferencia.

Así que en vez de seguir adivinando, la respuesta es una **sonda que él corre
contra su propia cuenta**: `/api/tito-fuentes`, con botón en el panel de Wheel.

### Qué hace

Pide **cada endpoint** que el motor usa y comprueba **cada campo** que el motor
lee. Por campo dice tres cosas: si está, de qué tipo, y **qué se rompe si
falta**. La lista no sale de la documentación de Massive —esa describe el plan
más caro— sino de leer quién consume qué en el código.

| Endpoint | Sostiene |
|---|---|
| `/v3/snapshot/options` | Estructura, GEX, niveles, escenarios, Wheel |
| `/v2/aggs/…/range/1/day` | niveles, IV Rank, sub-agente 6, la gráfica |
| `/v2/snapshot/locale/us/…` | el spot en vivo (1er eslabón) |
| `/v3/reference/tickers` | nombre para el matcher de noticias |
| `/vX/reference/financials` | el proxy de earnings de Wheel |
| MarketSnack `/api/flow_feed` | 4 de los 6 sub-agentes + Ideas + Time & Sales |

Y termina con el **veredicto por pestaña**: ok / degradado / roto, con el motivo
y el arreglo.

### Lo que ya se puede afirmar sin la sonda

Wheel es la **única** pestaña que depende de `last_quote`. Ticker, Ideas y Time
& Sales sacan su bid/ask de **MarketSnack**, no de Massive. Por eso funcionan.

Y "degradado" no es "roto": sin horquilla el motor puntúa igual con la cascada
de precio que él mismo escribió, y el score cobra **0 de 15** en liquidez por no
poder medir el spread. Un contrato sin bid **nunca** superará a uno con bid.

### Honestidad sobre "100% real"

Con `last_quote`, la prima **es** el bid: real y verificable. Sin él, la prima
es una **estimación** del último precio con recorte del 10%, y el panel lo dice
con asterisco, color ámbar y *"verifica la prima en tu bróker antes de vender"*.

Lo que **no** se hará nunca es rellenar la columna con un Black-Scholes y
callarlo. Ese sería exactamente el número que no puedes cobrar.

## 41.13 Que quepa en cualquier pantalla

Kevin: *"quiero que se ajuste a un móvil, tablet/iPad, computadora, monitor y
todos los dispositivos."*

### Lo que estaba mal, medido

- **16 tablas con ancho mínimo fijo**, de 420px hasta **720px**. En un teléfono
  de 390px eso es scroll horizontal en todas, y peor: el usuario **no sabe** que
  hay columnas a la derecha. Las de Wheel (9 columnas) y Time & Sales (9) eran
  ilegibles.
- **Contenedor topado en `max-w-6xl`** = 1152px. En un monitor de 27" el tab
  usaba un tercio de la pantalla… y las tablas *seguían* con scroll.
- **Alturas en píxeles**: gráfica de 440px y gamma de 240px. 440px en un
  teléfono apaisado es la pantalla entera.
- La navegación de pestañas se partía en dos filas y empujaba el contenido.

### La solución para las tablas: dejar de dibujarlas como tablas

Bajo 640px cada fila pasa a ser una **tarjeta** con `etiqueta ····· valor`. No
hay scroll porque no hay nada que desbordar.

La etiqueta sale de `data-th`, y ahí está la decisión que importa: **no se
escribe a mano**. Son ~150 celdas repartidas en 8 tablas generadas por
plantilla; escritas a mano se desincronizarían con el primer cambio de columna.
`vcTablaResponsive` las estampa leyendo el `<thead>` de cada tabla, así que **no
pueden** desincronizarse. La primera columna se deja sin etiqueta a propósito:
es el identificador de la fila y manda sola, como título de la tarjeta.

### Los cinco tamaños

| Ancho | Qué cambia |
|---|---|
| **< 640px** móvil | tablas → tarjetas · nav con scroll propio · buscador a ancho completo · gráfica 280px |
| **≥ 640px** tablet | tablas normales · cabecera en una línea · gráfica 360px |
| **≥ 1024px** portátil | 6 columnas de cards · gráfica 440px |
| **≥ 1280px** monitor | contenedor **1400px** |
| **≥ 1536px** 4K | contenedor **1760px** · gráfica 520px |

Seis tests lo fijan, y uno de ellos es el que evita la regresión de verdad:
**ninguna tabla puede llevar ancho mínimo sin el modo tarjeta**. Si mañana se
añade una novena tabla y se olvida la clase, el test la caza.

## 41.14 El perfil del inversionista manda

Kevin: *"basado a mi perfil de inversionista el área de proyecciones me dé ideas
igual que Wheel… en el área de perfil me salga información y perfil… basado a
ese perfil el agente de opciones (proyecciones) y el agente de acciones (analyze
y explore) te recomendarán en qué invertir… **no puedes tocar nada de analyze ni
explore**."*

### La restricción es la que decide el diseño

No se puede tocar Analyze ni Explore. Pero el perfil tiene que llegar a los dos.
La salida estaba ya en el repo: `_load_investor_profile()` —que esas dos áreas
ya usaban antes de este trabajo— lee `Perfil Inversionista/Kevin.md`.

Así que el editor **escribe ese archivo**. El perfil llega a los tres agentes
por el camino que el proyecto ya tenía montado, sin editar una línea de Analyze
ni de Explore.

```
pantalla → POST /api/perfil → perfil.json  (la verdad estructurada)
                            → Kevin.md     (regenerado desde el JSON)
                                 ↓
              lo leen Analyze y Explore, sin tocarlos
```

### Divergencia declarada: el sizing pasa al servidor

Sus dos rutas devuelven griegos y colateral, y nada más, **porque su app no
tiene perfil de inversionista**: el saldo vive en `localStorage` y por eso
`sizeFlow` y `wheelAfford.ts` corren en su cliente.

Aquí el capital está en el servidor. Así que sus dos funciones corren en la
ruta. **Las fórmulas son las suyas, intactas** — `diff_motor2.sh` las cubre,
918/918 casos idénticos. Lo que cambia es dónde se ejecutan.

Las dos estaban declaradas como huérfanas en el registro de la auditoría. Ya no:
el registro dice quién las llama, y si alguien las descablea, el check falla.

| | Antes | Ahora |
|---|---|---|
| **Ideas** | 25 contratos por premium | + `size_flow` con tu capital → columna «Te cabe», las que caben primero |
| **Wheel** | 120 candidatos por score | + `sort_by_afford_then_score` → «Sí» / «faltan $8,500» |

**Lo que no te cabe no se esconde: se marca y se baja.** Que una operación esté
fuera de tu presupuesto es información. Ocultarla te dejaría creyendo que el
mercado no ofrecía nada.

### El fallo caro que casi se cuela: regenerar destruye

`Kevin.md` estaba escrito a mano, con 2.384 caracteres de contexto real. Abrir
la pantalla nueva y pulsar «Guardar» con el formulario vacío lo habría
reemplazado por una plantilla. **Data loss silencioso.**

Y hay un segundo lector que nadie ve: `engine/wbj/specialists/risk.py` **no lee
el JSON — parsea el `.md` con tres regex**: el primer `$` (capital), un rango
`N a M años` (horizonte) y un rango `N% – M%` (tope por posición). Si el
markdown generado dejaba de casar con alguno, ese especialista caía a su valor
por defecto **sin avisar** y el reporte pasaba a hablar del perfil de otra
persona.

Las dos respuestas:

1. **Siembra.** Sin `perfil.json`, el perfil se rellena leyendo el `.md`
   existente: el texto entero va a «En mis palabras», y capital, horizonte y
   tope salen de sus mismos regex. No se pierde una línea. Y si el `.md` ya lo
   generamos nosotros, no se re-siembra — se duplicaría en cada guardado.
2. **El markdown se escribe para los dos lectores.** El capital es el primer
   `$` del documento; el tope por posición se emite siempre como rango; y un
   horizonte en palabras («semanas a meses») lleva detrás su equivalente en años
   **declarado**, porque un valor por defecto que nadie escribió es peor que uno
   aproximado y dicho.

Cinco tests corren `_load_profile()` **de verdad** sobre el markdown generado.
No una copia del regex: una copia se actualizaría sola y dejaría de proteger
nada.

### El menú de cuenta

El nombre de arriba a la derecha era una etiqueta muerta con un «Sign Out» al
lado. Ahora abre el menú: perfil, tema y idioma.

- **Tema**: reutiliza el `applyTheme` que ya existía. Dos interruptores que
  muevan preferencias distintas es peor que uno solo — el usuario cambia el tema
  y al recargar vuelve el otro.
- **Idioma**: el marcado **es** la traducción española. `vxAplicaIdioma` guarda
  el original en `data-i18n-es` la primera vez, así que volver a español no
  depende de un segundo diccionario que se pueda desincronizar. Un test exige
  que toda clave `data-i18n` del marcado tenga entrada en inglés.
- **Honestidad sobre el alcance**: se traducen la navegación, el menú y el
  perfil. **Analyze y Explore no**, porque traducirlos exige editar su marcado y
  esas dos áreas no se tocan. Está dicho en el código en vez de dejar que se
  descubra a medias.

### El smoke que encontró lo que la lectura de texto no podía

Los tests de Python leen el HTML como **texto**: comprueban que una función
existe y que alguien la llama. No ejecutan una línea.

`engine/scripts/_smoke_perfil.mjs` monta un DOM mínimo y **corre el JS vivo**
—`renderProjIdeas`, `renderProjWheel`, `pfPinta`— con payloads realistas, y mira
el HTML que sale. 23 comprobaciones.

Encontró un fallo real: `pfPinta` leía las bandas de tolerancia de una variable
que solo llenaba `pfCargar`, así que la nota del riesgo por operación salía **en
blanco**. Ninguna lectura de texto podía verlo. Corre dentro de la auditoría.

### Batería

**2.721 tests del motor · 303 de la capa web · 248 checks de auditoría · 23 del
smoke · 12 diferenciales · 0 fallos.**

Los diferenciales se volvieron a correr contra el clon actual de su repo
(`53d5a20`): motor 1142/1142, motor2 918/918, compute 604/604, geo 274/274,
reloj 223/223, calib 182/182, frescura 342/342, store 47/47, bars 27/27.

## 41.15 Cuentas de verdad, el cuestionario y el aprendizaje compartido

Kevin: *"en mi documento de Kevin.md hay unas preguntas que yo contesto. Cada
usuario que se cree una cuenta debe contestar esas mismas preguntas… si no
contesta usará las default que son las mías. También cada cuenta que se cree
debe guardarse y puede iniciar sesión usando su email y contraseña. También todo
lo que analicen cada usuario alimenta los agentes en general."*

### Lo que había: un login que no era un login

`vertex_users_db` en `localStorage`, con esta línea marcada en el propio código
como *«demo only — not secure for production»*:

```js
users[email] = { email, name, password };   // contraseña EN TEXTO PLANO
```

Tres cosas rotas a la vez, y ninguna era obvia desde la pantalla:

1. Cualquiera con la consola del navegador abierta leía la contraseña de todos.
2. **Entrar desde el móvil era imposible**: la cuenta no existía fuera de aquel
   Chrome. No es que fallara — es que el registro creaba otra cuenta distinta.
3. «Cerrar sesión» borraba una clave local. No había nada que cerrar.

Ahora: SQLite, **PBKDF2-HMAC-SHA256 con 600.000 iteraciones y sal por usuario**,
y una sesión cuyo **hash** es lo único que toca el disco — llevarse la base de
datos no es llevarse sesiones vivas. La cookie es HttpOnly (un XSS no se la
lleva) y SameSite=Strict (la defensa contra CSRF).

Y un detalle que no es cosmético: *«no existe»* y *«contraseña incorrecta»*
devuelven **el mismo mensaje**. Distinguirlos convierte el login en un
directorio de qué emails tienen cuenta.

### El cuestionario sale de Kevin.md, no de la imaginación

Doce preguntas, una por apartado que Kevin contestó en prosa: objetivos,
horizonte, tolerancia, capital, instrumentos, vetos, universo, tope por
posición, prioridad, experiencia, texto libre y qué espera del sistema.

**Su respuesta es el `defecto` de cada una.** Y el perfil por defecto se
construye DESDE la lista de preguntas, no como una segunda copia: una copia se
desincronizaría con el primer cambio, y el default que se enseña en pantalla y
el que se guarda serían distintos.

Lo que no se contesta se hereda **y se declara**. La pantalla dice «4 de 12
contestadas», cada pregunta lleva su insignia de *valor heredado*, y el propio
`.md` que lee el agente abre diciendo cuántas siguen sin contestar. Un perfil
heredado presentado como propio hace que el reporte hable con una confianza que
no tiene.

### Dos fallos silenciosos que el smoke y el parser real cazaron

**El capital que valía $150.** El `.md` se reordenó y la sección de Tolerancia
quedó delante de la de Capital. `risk._load_profile` toma el **primer** importe
en dólares del documento como capital — y el primero pasó a ser el riesgo por
operación, «$150». El especialista concluía que la cuenta entera eran $150. Lo
cazó correr el parser REAL sobre el markdown REAL, no una copia del regex.

**El horizonte que nadie eligió.** El rango en años estaba DERIVADO de los días,
y «5+ años» acababa impreso como «1 a 3 años» porque la derivación redondeaba 90
días a 0. Ahora cada opción declara su rango a mano: lo declarado no puede
derivar mal.

**Y dos veces el mismo patrón en el JavaScript**: una función de pintado que
leía sus datos de una variable que solo llenaba el cargador. La primera vez dejó
en blanco la nota del riesgo; la segunda, el cuestionario entero. Ningún test de
texto puede ver eso — lo vio `_smoke_perfil.mjs`, que ejecuta el JS contra un DOM.

### Un perfil por usuario, y cómo llega a los tres agentes

| Camino | Qué cambió |
|---|---|
| Proyecciones | `_perfil_leer(request)` → el sizing de Ideas y la Wheel usan TU capital |
| Prompt de Analyze/Explore | `_load_investor_profile()` resuelve TU `.md`; su firma y sus dos llamadores no cambian |
| Especialista de riesgo | `risk.PROFILE` se resolvía **al importar**: era el de Kevin para todo el proceso |

Ese tercero era el peor. La misma posición del 25% es válida para Kevin (tope
30%) e incumplimiento para Ana (tope 10%) — sin arreglarlo, las dos veían el
veredicto de Kevin. Se resolvió con un `ContextVar` en `risk.py`: por contexto
asíncrono, no un global mutable que dos peticiones concurrentes se pisarían.

También se arregló una deriva latente: `_load_investor_profile` calculaba el
directorio de perfiles por su cuenta en vez de usar `_PERFIL_DIR`. Dos funciones
resolviendo la misma ruta acaban en rutas distintas — el editor escribe en una y
el agente lee de la otra, sin que nada falle porque el archivo viejo existe.

### Archivo privado, aprendizaje compartido

`/api/reports/list` devolvía **todos** los reportes a **cualquiera**. Con un solo
usuario era lo mismo que devolver los suyos; con cuentas, es el análisis de una
persona leído por las demás. Alimentar al agente y publicar tu trabajo no son lo
mismo.

- **Tuyo**: tus reportes. Filtrados por `usuario_id`, y `report-delete` solo
  alcanza lo tuyo (los `report_id` llevan ticker y fecha: adivinarlos es fácil).
- **De todos**: las series y el track record. `/api/aprendizaje` nunca devuelve
  quién analizó qué — solo cuánto hay y de cuánta gente.

**Los dos agentes aprenden distinto, y por eso se cuentan aparte:**

| | Cómo aprende | Qué necesita |
|---|---|---|
| **Acciones** (Analyze/Explore) | **Calibración** — un lazo cerrado. Cada reporte guarda convicción y objetivos; el tiempo dice si acertó. | Reportes ya vencidos. Necesita **tiempo**, no solo volumen. |
| **Opciones** (Proyecciones) | **Acumulación hacia adelante** — no hay nada que acertar. La IV histórica, las cadenas y el flujo no los vende nadie: se juntan una foto por día. | `MIN_IV_HISTORY_DAYS` del propio motor. Mirar un ticker YA aporta, aunque no salga ningún reporte. |

El umbral se lee del módulo, no de un número copiado: una copia diría «ya está
listo» mientras el motor sigue usando el proxy de volatilidad realizada.

### Una decisión de seguridad que conviene saber

`VERTEX_REGISTRO` controla quién puede crear cuenta: `abierto` (por defecto),
`invitacion` (con `VERTEX_INVITE_CODE`) o `cerrado`. Está en `abierto` porque lo
pedido era que la gente se registrara — pero en un despliegue público eso
significa que **cualquiera con la URL puede crear una cuenta**. Cambiar la
variable lo cierra sin tocar código.

### Batería

**2.779 tests del motor · 338 de la capa web · 248 checks de auditoría · 27 del
smoke de JS · 12 diferenciales · 0 fallos.**

## 41.16 Perfil por defecto o personalizado, y una pregunta que sobraba

Kevin, sobre la pantalla de perfil: *"lo que dice que espero del sistema, no
quiero que eso exista: siempre será el mismo sistema y agentes… lo de ¿algo más
que el agente deba saber de ti? quiero que sea opcional… no quiero que salga lo
del perfil de inversionista cuando entre en la cuenta, quiero que salga en el
dashboard regular… tiene la opción de Default o personalizado. Si presiona
personalizado salen las preguntas."*

### Una pregunta que no era una pregunta

«¿Qué esperas que el sistema haga por ti?» era el **contrato del sistema
disfrazado de preferencia**. La matemática es determinista y el LLM solo
explica; eso no cambia porque alguien conteste otra cosa, así que preguntarlo
insinuaba una elección que no existe.

Se quitó del cuestionario. El contrato **sigue** en el `.md` que leen los
agentes —quitar la pregunta no puede quitarle al agente el contexto de cómo
trabaja— pero como lo que es: una sección constante, **idéntica para todos**, y
un test lo comprueba comparando el bloque entre dos perfiles distintos.

### Opcional significa que en blanco es una respuesta

El texto libre lleva ahora `opcional: True`, y eso cambia tres cosas:

- **No cuenta en el denominador.** Con las once en el denominador, el perfil se
  quedaba eternamente incompleto por no escribir un texto que nadie tiene que
  escribir — y la advertencia de *«usa el perfil de Kevin»* seguía saliendo
  cuando ya no heredaba nada. Son **10 obligatorias**.
- **La insignia dice «opcional»**, no «valor heredado». No hay nada que heredar:
  el contexto personal de otra persona no es contexto tuyo.
- **Borrar lo escrito la devuelve a «opcional»**, en vez de dejarla marcada como
  contestada con el campo vacío.

### Default o personalizado

Entrar ya no secuestra al cuestionario. Once preguntas como puerta de entrada
son una barrera antes de haber visto nada, así que **registrarse lleva al
dashboard** y al perfil se llega por el menú de cuenta.

Y dentro del perfil, primero se elige:

| Modo | Qué pasa |
|---|---|
| **Por defecto** | Usas el perfil de referencia tal cual. **No salen las preguntas**, y no hay nada «pendiente» — elegir no es dejar un formulario a medias. |
| **Personalizado** | Aparece el cuestionario y los tres agentes recomiendan con tus números. |

Tres decisiones que hacen que esto no sea un interruptor decorativo:

1. **Cambiar de modo no borra lo contestado.** Volver a personalizado recupera
   tus respuestas tal cual. Borrarlas castigaría la curiosidad de quien solo
   quiso ver cómo era el otro modo.
2. **El payload devuelve las dos caras**: `respuestas` es lo que escribiste, el
   nivel de arriba es lo EFECTIVO con el modo aplicado. Sin separarlas, el
   formulario en modo por defecto enseñaría los valores de Kevin como si fueran
   tuyos, y guardarlos los volvería tuyos sin quererlo.
3. **El `.md` declara el modo.** En por defecto abre diciendo *«esta persona NO
   ha personalizado su perfil: estos valores son los de referencia, no los
   suyos»*. El agente tiene que poder distinguir «este es su capital» de «este
   es el de referencia».

El modo llega hasta el final: lo efectivo es lo que dimensiona Ideas y la Wheel,
lo que va al prompt y lo que lee el especialista de riesgo del engine.

### Lo que encontró el smoke esta vez

La clase que marca el modo elegido se aplicaba en una **segunda pasada** con
`querySelectorAll` en vez de ir en la plantilla. Funcionaba en el navegador,
pero el marcado generado no decía cuál estaba elegido — depende de que alguien
recuerde recorrerlo después. Ahora va dentro de la plantilla.

También se limpiaron once comentarios donde habían quedado secuencias `é`
literales: dentro de una cadena de JS se decodifican, en un comentario son texto
muerto que nadie lee bien.

### Batería

**2.787 tests del motor · 361 de la capa web · 248 checks · 29 del smoke de JS ·
12 diferenciales · 0 fallos.**

## 41.17 El perfil deja de ser decorativo en el agente de acciones

Kevin preguntó qué hacía exactamente el texto libre del cuestionario. Rastrearlo
dio dos respuestas incómodas, y las dos se arreglan aquí.

### 1. `profile_fit` tenía los hechos escritos a mano

```python
"universo": "Estados Unidos", "tolerancia": "agresivo / especulativo",
"capital": "~$1,000 USD",
```

Literales en el código. Con **un** usuario era correcto: era el perfil de todo
el mundo. Con cuentas, le contaba a cada persona el perfil de Kevin — incluida
la comprobación de universo, que estaba clavada a EE.UU.: a alguien que hubiera
marcado **Europa** se le decía que una acción alemana estaba *«fuera de tu
universo»*.

Ahora todo sale de `_perfil_leer()`, y con ello:

- **El universo se comprueba contra los mercados que marcaste.** Sin mercados
  marcados no se afirma que nada esté fuera — inventar un universo es peor que
  no tenerlo.
- **El aviso de riesgo de ruina se calibra al capital.** Con $1.000 y opciones
  es urgente; con $250.000 es una nota al pie, y repetirlo con las mismas
  palabras lo convierte en ruido que se deja de leer.
- **El sizing se da en dólares.** «Tope 20%–30%» no dice nada solo; «$200–$300
  por posición» sí.
- **Se declara si el perfil es el de referencia** (`es_por_defecto`). El lector
  tiene derecho a saber que esos hechos no los declaró nadie.

### 2. La explicación estaba desconectada

El texto libre solo entra en un sitio: el **2º pase del LLM**, que recibe los
números ya congelados y los traduce a palabras *«para el inversionista de MI
PERFIL»*.

Ese pase vivía detrás de `?explain=1`. Y la pantalla llamaba a
`/api/analyze?ticker=X` — **sin ese parámetro, nunca**. El propio comentario del
código lo decía: se puso detrás de una bandera porque costaba 18,4 s de los ~105
que tarda el análisis, *«para un campo que la plataforma no lee en ningún
sitio»*.

Resultado: el texto viajaba hasta el prompt y ahí se paraba. **Escribieras lo
que escribieras, no cambiaba una palabra de lo que veías.**

### La solución no fue meterlo en el camino crítico

Poner `explain=1` en la llamada habría sumado 18 s a un endpoint que ya roza el
corte de Render — exactamente el problema que lo dejó desconectado. En su lugar:

```
/api/analyze  ──► ~105 s ──► el análisis aparece
                                    │
                                    └─► /api/wbj-explicacion (report_id)
                                            ~18 s, y el panel se rellena solo
```

El contexto se **reconstruye desde el reporte ya guardado** — los mismos
números, ninguno recalculado. Para eso hubo que extraer el constructor del
contexto, que estaba escrito en línea dentro de `/api/analyze` y por eso solo
podía generarse allí.

Cuatro decisiones del diseño:

1. **Solo tu propio reporte.** Se filtra por `usuario_id` igual que el archivo:
   de nada serviría un archivo privado si esta ruta explicara el de otro. Y «no
   existe» y «no es tuyo» dan el mismo mensaje.
2. **No se paga dos veces.** La explicación se guarda en el reporte; volver a
   abrirlo la sirve al instante.
3. **Los hechos duros van explícitos** además del `.md`. Enterrados en la prosa
   del archivo se pierden, y son justo los que el LLM necesita para calibrar el
   tamaño de lo que describe. Si el perfil es el de referencia, el prompt lleva
   un aviso: *«no le hables como si los hubiera declarado»*.
4. **El panel dice que NO calcula.** Un texto de un LLM junto a unos números
   invita a creer que los produjo. La nota al pie lo corta.

### Batería

**2.787 tests del motor · 377 de la capa web · 248 checks · 37 del smoke de JS ·
12 diferenciales · 0 fallos.**

## 41.18 Verificación: ¿esto tocó los agentes? Y lo que apareció al comprobarlo

Kevin preguntó si el trabajo de cuentas y perfiles había afectado a los agentes,
sub-agentes, cálculos, métricas, fuentes o cobertura.

### La respuesta, verificada

De todo el trabajo, **un solo archivo del motor**:
`engine/wbj/specialists/risk.py`, y el diff entero cae **dentro de
`profile_fit()`**. `engine/wbj/tito/` (los 6 sub-agentes de opciones),
`providers/` y `packet/` (todas las fuentes): cero cambios.

**Prueba estática.** En el especialista de riesgo, `awarded_points` sale en la
línea 1468 y `coverage` en la 1470; `profile_fit` se pega en la 1522, 54 líneas
después, como campo de reporte. Y `PROFILE` no aparece en ninguna otra línea del
archivo: el perfil no tiene por dónde llegar al cálculo.

**Prueba empírica, que es la que vale.** Se inyectó un perfil absurdo en todo el
motor —capital ×1.000, tope de posición 100× más estrecho, horizonte al otro
extremo— y se corrió la suite entera:

```
7 failed, 2780 passed
```

Los siete fallos son exactamente las pruebas de `profile_fit` en sí
(`test_profile_fit_within_cap`, las cinco de semántica del tope,
`test_run_nvda_fixture_profile_fit_populated`). **Cero fallos en scores,
cobertura, métricas, gates u overrides.** Con el perfil cambiado mil veces, el
resto del motor no se movió un decimal.

**Concurrencia.** 30 peticiones simultáneas de 3 usuarios con capitales
distintos: ninguna fuga. Y el especialista de riesgo en 3 hilos a la vez, cada
uno con su capital correcto — el `ContextVar` hace lo que promete.

### Pero al comprobarlo aparecieron dos formas de perder el archivo

Simulando la base que de verdad hay en Render —con reportes de antes de que
existieran cuentas— salieron dos fallos silenciosos, y el segundo destruye datos.

**1. El archivo huérfano.** Los reportes anteriores tienen `usuario_id` NULL, así
que al registrarte tu archivo salía **vacío**. Tu historial entero desaparecía de
la vista sin que nada fallara ni avisara.

Ahora **la primera cuenta del despliegue adopta los huérfanos**: quien se
registra primero es quien los generó, porque era el único usuario que había. La
segunda ya no puede — para entonces tienen dueño, y regalárselos a cualquiera
que se registre sería entregarle el archivo de otro.

**2. El borrado local, que sí perdía datos.** `authLogin` borraba el archivo del
navegador en **cada** login. Los reportes cuyo `payload` nunca llegó al servidor
—los anteriores a que existiera esa columna— **solo viven ahí**, y el
`syncReportsFromServer` de después no puede devolverlos porque en el servidor no
están. La primera vez que alguien entrara tras estrenar las cuentas, ese
historial se habría ido para siempre.

Ahora se recuerda de quién es lo guardado (`vertex_archivo_dueno`) y se limpia
**solo al cambiar de persona**. Un archivo sin dueño es de antes de las cuentas
—de la única persona que había, que es la que está entrando— así que se
conserva. Al **salir** sí se borra sin condiciones: es una salida deliberada y lo
que se pierde ya está en el servidor bajo su cuenta.

### Un riesgo medido y no arreglado

`save_report_payload` corta el JSON a 2 MB con `[:2_000_000]`. Cortar un JSON a
la mitad produce un JSON **inválido**, y ese reporte desaparecería en silencio
del archivo (`/api/reports/list` lo salta con un `except: continue`). Es anterior
a este trabajo, pero el endpoint de explicación añade una segunda escritura al
mismo payload.

Medido: las series de precio de un reporte pesan **~36 KB**. Harían falta ~56
veces más datos para llegar al tope. Queda declarado, no arreglado.

### Batería

**2.787 tests del motor · 383 de la capa web · 248 checks · 37 del smoke de JS ·
12 diferenciales · 0 fallos.**

## 41.19 Ronda 7 — auditoría completa del tab, contra su repo en 53d5a20

Kevin: *"audita el tab completo de Proyecciones y no puedes omitir una área, ni
métrica, agentes, sub agente, cálculos, fuentes ni nada."*

Primero: **su repo no ha cambiado**. Sigue en `53d5a20`, el mismo commit contra
el que se hicieron las rondas anteriores.

### Lo que ya estaba verde

- **12 diferenciales** contra sus archivos reales: motor 1142/1142, motor2
  918/918, compute 604/604, geo 274/274, reloj 223/223, calib 182/182, frescura
  342/342, store 47/47, bars 27/27, cono (desvío 5.68e-14 %), primitivas,
  motor3 348/349 con 1 declarado.
- **278 checks** de auditoría con su repo adjunto, 0 fallos.
- **28 de sus 30 módulos** portados; los 4 restantes declarados con motivo.
- **39 componentes** suyos declarados uno a uno.

### Los cuatro huecos que aparecieron

**1. No había registro de sus RUTAS.** Había uno de módulos y otro de
componentes, pero ninguno de sus 11 endpoints. Si mañana añade uno, nada lo
cazaba — y una ruta suya sin equivalente aquí es funcionalidad que sencillamente
no existe. Nuevo §9-quinquies: 9 de sus 11 rutas tienen equivalente, `logo` y
`watchlist` declaradas con motivo, y se comprueba que la ruta declarada exista
de verdad.

**2. No había registro de DIVERGENCIAS.** Estaban documentadas en comentarios
sueltos por tres archivos y nada las enumeraba: quitar una o añadir otra no lo
veía nadie, que es lo contrario del contrato de este port. Nuevo §9-sexies: 4
divergencias, cada una con qué cambia, por qué y **qué no cambia**.

**3. `maxPages` no era su `maxPages`.** Él usa `Number(process.env...)`, que
acepta notación científica y hexadecimal; `int()` de Python las rechaza. Con
`MASSIVE_MAX_PAGES=1e2` él paginaba 100 páginas y aquí se cortaba en 40 —
**media cadena de opciones de menos, sin que nada avisara**. Ahora usa
`js_number`, el port de `Number()` que ya estaba escrito. Verificado en los 7
casos borde.

**4. La cobertura de hojas solo vigilaba el scorecard.** Ideas y Wheel no
tenían barrido: sus campos estaban cubiertos por tests escritos a mano, que solo
cazan lo que alguien se acordó de comprobar. El barrido nuevo, ya honesto
(acotado a la función de render, no al archivo entero), encontró que **el panel
de la Wheel tiraba lo que él sí pinta**.

### Lo que faltaba de su Wheel, y era media estrategia

Comparando `WheelPresetCard.tsx` y `WheelTable.tsx` línea a línea:

| Suyo | Estaba |
|---|---|
| «Cierra al **50%** de la prima» | ❌ el motor lo servía, el panel lo tiraba |
| «Rola a los **21 días**» | ❌ idem |
| Colchón por candidato | ❌ |
| Δ, IV (impl./est.), OI del contrato | ❌ |
| **«Si expira sin valor / si te asignan / si se desploma 20%»** | ❌ |

Las dos primeras son el **plan de salida**: sin ellas se sabe qué vender y no
cuándo salir. Los tres escenarios son lo que hace legible una venta de put — qué
pasa en cada desenlace, con números. Todo añadido: las reglas del preset en una
tira bajo los botones, el colchón como columna, y los escenarios en una fila
desplegable al hacer clic, con los cinco `why` del score debajo.

### El error que casi se despliega

Al escribir los escenarios metí un comentario HTML con **acentos graves** dentro
de una plantilla de JavaScript. Un acento grave ahí **cierra la cadena**: el
navegador tira un `SyntaxError` y se lleva **el tab entero**, no una tarjeta.

Lo cazó el smoke que ejecuta el JS — los tests de Python leen el archivo como
texto y no habrían visto nada. Nuevo §6-bis, con dos checks: ningún comentario
dentro del `<script>` lleva acentos graves, y el JS del panel **se ejecuta**.

### Checklist final del tab

| Área | Estado |
|---|---|
| Sub-agentes 1-3 (agresividad, convicción, inusualidad) | ✅ diff_motor 1142/1142 |
| Sub-agente 4 (estructura) | ✅ diff_motor |
| Sub-agente 5 (contexto IV) | ✅ diff_motor2 918/918 |
| Sub-agente 6 (confirmación de precio) | ✅ diff_motor · advertencia en cada reporte |
| GEX + mapa de calor | ✅ diff_motor2 · diff_motor3 |
| Niveles por confluencia | ✅ diff_motor · diff_frescura 342/342 |
| Prediction Pro | ✅ diff_motor2 · diff_calib 182/182 |
| Gráfica (cono + geometría) | ✅ diff_cono 5.68e-14 % · diff_geo 274/274 |
| Cadena y barras | ✅ diff_compute 604/604 · diff_bars 27/27 |
| Persistencia (4 stores) | ✅ diff_store 47/47 |
| Pestaña Ticker | ✅ cobertura de hojas, 0 huérfanas |
| Pestaña Ideas | ✅ barrido nuevo, 0 huérfanas |
| Pestaña Wheel | ✅ **5 campos suyos que faltaban, añadidos** |
| Pestaña Time & Sales | ✅ las dos ventanas de su `/api/flow` |
| Fuentes (Massive, MarketSnack) | ✅ · `maxPages` corregido |
| Perfil del inversionista | ✅ 4 divergencias declaradas |
| Responsive | ✅ 5 tamaños |
| En vivo | ✅ 15 min, sin botones |

**2.787 tests del motor · 386 de la capa web · 293 checks · 47 del smoke de JS ·
12 diferenciales · 0 fallos.**

## 41.20 El último cabo: un JSON cortado no es un JSON

Quedaba declarado y sin arreglar desde §41.18. Al preguntar Kevin si todo lo
señalado estaba resuelto, se cerró.

`save_report_payload` aplicaba su tope de 2 MB con `json.dumps(...)[:2_000_000]`.
Cortar un JSON por la mitad produce un JSON **inválido**: la fila quedaba
escrita pero ilegible, `/api/reports/list` la saltaba con su `except: continue`
y el reporte **desaparecía del archivo** sin que nada avisara.

Tres desenlaces, verificados uno a uno:

| Caso | Antes | Ahora |
|---|---|---|
| Normal (~36 KB) | se guarda entero | igual |
| Pasa de 2 MB | JSON cortado = **ilegible** | se guarda **sin las series de precio**, que son lo que pesa y lo que la gráfica puede volver a pedir. El análisis —que no se puede regenerar— se queda, y `_series_omitidas` lo declara |
| No cabe ni sin series | JSON cortado = ilegible | **no se escribe nada**. Un payload ausente se nota y se puede regenerar; uno corrupto se lee como si el reporte no existiera |

Medido antes de tocarlo: las series de un reporte pesan ~36 KB, así que el tope
está ~56× lejos. Es un fallo remoto — pero cortar un JSON nunca es correcto, y
el endpoint de explicación añadió una segunda escritura al mismo payload.

Cuatro tests lo fijan, y el último tuvo que aprender la regla de la casa: leía
el comentario que EXPLICA el corte viejo citándolo, y fallaba por la
documentación del propio arreglo. Se lee el código, no lo que dice de él.

**2.787 tests del motor · 390 de la capa web · 293 checks · 47 del smoke ·
12 diferenciales · 0 fallos.**

## 41.21 Subir el tope de 2 MB, sin que subirlo rompa nada

Kevin: *"¿cómo hacer que sea más grande de 2 MB?"*

### Dónde está el techo de verdad

El 2 MB no lo impone SQLite —aguanta **1 GB** por columna—. Lo impone
`/api/reports/list`, que devuelve hasta **60 payloads COMPLETOS en una sola
respuesta**. El tope por reporte se multiplica por 60:

| Tope por reporte | Respuesta de la lista |
|---|---|
| 2 MB | 120 MB |
| 10 MB | **600 MB** |
| 50 MB | **3.000 MB** |

Y el navegador guarda ese archivo en `localStorage`, que son ~5 MB **en total**
para todos los reportes juntos.

O sea: subir el tope **solo** empeora las cosas. Hacen falta las dos piezas.

### Las dos variables

- **`VERTEX_PAYLOAD_MAX`** — cuánto puede pesar UN reporte. Default 2.000.000.
- **`VERTEX_LISTA_MAX`** — cuánto puede pesar la RESPUESTA de la lista. Default
  40.000.000 (40 MB).

`/api/reports/list` ahora se corta **por peso**, no solo por número: para al
llegar al tope, sirve los más recientes primero y **declara cuántos dejó
fuera**. Un archivo recortado en silencio parece un archivo que perdió reportes.

Con `VERTEX_PAYLOAD_MAX=10000000`, verificado: 20 reportes de 3 MB → sirve 13
(39 MB) y declara `recortados: 7` con su motivo.

Dos salvaguardas más: basura o `0` en cualquiera de las dos variables cae al
default —un tope de 0 no guardaría nada—, y la lista devuelve **siempre al menos
un reporte**, aunque ese solo pase del tope de la respuesta: dejar el archivo
vacío por un reporte grande sería peor que servirlo.

**2.787 tests del motor · 394 de la capa web · 293 checks · 0 fallos.**

## 41.22 Solo dos diferencias con su código: tu perfil y que la Wheel funcione

Kevin: *"todo lo que es diferente soluciónalo. Solo que use el perfil del
usuario y que la Wheel funcione es lo único diferente… esto no lo quiero:
/api/tito-fuentes."*

### Divergencias cerradas

**La clave de Massive vuelve a ser suya.** Él hace `if (!key)`, que en
JavaScript es solo la cadena vacía: una clave con espacios alrededor pasa y
Massive responde 401. Aquí se recortaba antes —lo que era más útil— y se
revirtió. Su versión falla más tarde y peor, y aun así es la que queda.

**`new Date("0")` ahora replica el parseo legacy de V8.** Medido contra Node 22,
la regla es arbitraria hasta el absurdo:

```
"0"        → año 2000
"1".."12"  → MES de ese número, del año 2001
"13".."31" → NaN
"32".."49" → año 20XX
"50".."99" → año 19XX
"100"+     → ese año tal cual
```

Se dijo en su momento y se repite aquí: esto es una peculiaridad del motor de
JavaScript, no lógica de Víctor, y ningún timestamp real puede dispararla
—MarketSnack y Massive mandan ISO—. Vive en el código para que el corpus de
basura case al 100%. **`diff_motor3` pasa de 348/349 a 349/349**, y la línea
base queda congelada en cero.

### Fuera el diagnóstico de fuentes

`/api/tito-fuentes` era mío, no suyo: 208 líneas de backend, su función de
panel, su botón y sus 6 tests. Todo retirado.

Al quitarlo pasó algo que merece quedar escrito. El primer corte se llevó **330
líneas de más** —el bloque entero del perfil, que venía justo después— porque la
guarda contaba `@app.get(` y los helpers del perfil no llevan decorador. Se
restauró desde git y se rehízo **con el AST**, que da el final exacto de la
función en vez de adivinarlo, y con dos aserciones nuevas: que el bloque no
contenga `_PERFIL_DIR` ni `aprendizaje`.

### Lo que la auditoría cazó sola

Al cerrar la divergencia de la clave, el §9-sexies falló:

```
✗ massive.py: 0 marcas «DIVERGENCIA DECLARADA» para 1 declarada(s)
```

El registro seguía declarando una diferencia que ya no existía. Es exactamente
para lo que se escribió una ronda antes.

Y un test del motor afirmaba lo contrario de lo pedido
(`test_el_legacy_de_V8_no_se_replica`). Invertido, y ahora fija la regla entera
caso por caso — porque una regla así de arbitraria sin test es una bomba.

### Quedan dos, y son las que Kevin quiere

| Divergencia | Por qué se queda |
|---|---|
| Sizing y asequibilidad en el servidor | Su app no tiene perfil de inversionista; esta sí. **Las fórmulas son las suyas.** |
| Wheel sin bid | Sin esto el screener sale siempre vacío con el plan actual de Massive. |

**2.787 tests del motor · 388 de la capa web · 292 checks · 12 diferenciales,
todos a cero divergencias.**

## 41.23 La clave de Massive: cerrar una divergencia casi la rompe

Kevin: *"verifica si mi clave de Massive está funcionando. Si no, arréglalo,
porque ahorita sí estaba funcionando."*

### Lo que había pasado

Media hora antes, cerrando divergencias, se revirtió el recorte de la clave para
dejar el código exacto al suyo:

```python
- key = os.environ.get("MASSIVE_API_KEY", "").strip()
+ key = os.environ.get("MASSIVE_API_KEY", "")
```

Su `if (!key)` solo rechaza la cadena vacía. Medido:

| Clave en el entorno | Cabecera enviada | Resultado |
|---|---|---|
| `abc123` | `Bearer abc123` | OK |
| `abc123\n` | `Bearer abc123\n` | **401** |
| `abc123 ` | `Bearer abc123 ` | **401** |

En **su** despliegue eso no pasa: la clave vive en un `.env.local` que se edita
en un editor. Aquí vive en el panel de Render, **donde se pega con el ratón** —
y pegar arrastra un salto de línea con una facilidad enorme.

O sea: una clave que funcionaba podía dejar de funcionar por un carácter
invisible, y el 401 acusaría a la credencial en vez de al espacio que sobra.

**Restaurado y declarado** como cuarta divergencia, con ese motivo escrito. Si
algún día se confirma que la clave no lleva blancos, la línea vuelve a la suya y
el registro baja a tres.

> No se puede probar contra la API real: este contenedor no tiene salida a
> `api.massive.com`, y la regla del proyecto prohíbe leer `API/`. Lo verificado
> es el comportamiento del código con claves de prueba.

### ¿Se dañó algo de los dos agentes?

Comparado el estado de hoy contra `be237a1` —el que Kevin usaba y funcionaba—:

| | Antes | Ahora | Perdido |
|---|---|---|---|
| Rutas de la API | 72 | 71 | solo `/api/tito-fuentes` |
| Funciones de `vertex_api.py` | 436 | 432 | `tito_fuentes` + sus 5 helpers **locales** (0 usos fuera) |
| Motor de opciones (26 módulos) | — | — | **ninguna** |
| Motor de acciones (8 especialistas) | — | — | **ninguna** |

Y ejecutado, no solo leído: los **6 sub-agentes de opciones** (Agresividad,
Convicción, Inusualidad, Estructura, Contexto IV, Confirmación) más GEX,
Niveles, Predicción Pro, Wheel y Sizing responden; los **6 especialistas de
acciones** conservan su `run()`; las 6 categorías siguen con sus pesos
(20/15/20/20/15/10); y las 9 rutas del tab contestan sin un solo 404 ni 500.

**2.787 tests del motor · 388 de la capa web · 292 checks · 0 fallos.**

---

## 41.24 Lo que tenía Víctor y aquí no estaba

Pregunta: *«Verifica si el agente de Víctor hay algo que yo no tenga o hayas
eliminado. Verifica y soluciona todos los errores/problemas que tenga.»*

La auditoría ya sabía la respuesta —el registro la enumeraba en tres sitios— y
lo que decía era esto:

| | Suyos | Aquí | Faltaban |
|---|---|---|---|
| Componentes | 39 | 27 | **12** |
| Módulos de `web/lib` | 32 | 28 | **4** (los del watchlist) |
| Rutas de `web/app/api` | 11 | 9 | **2** (`/api/logo`, `/api/watchlist`) |

Cada uno estaba declarado con su motivo, y cada motivo era razonable **por
separado**. En conjunto no lo era: entre los doce componentes son la mitad de la
evidencia que su panel enseña —la ficha de la empresa, el reparto del dinero del
día, la cadena entera, tu watchlist— y resumir una cosa no es lo mismo que
enseñarla. Se portan los doce.

### Los doce componentes

| Suyo | Aquí | Qué aporta que antes no estaba |
|---|---|---|
| `CompanyHeader` | `vcCompanyHTML` | nombre, sector, market cap, volumen, rango del día, cierre previo — **y el logo de la empresa** |
| `HeaderBar` | la barra del tab + `vcSyncCabecera` | nombre, precio y variación del ticker cargado, arriba y siempre visibles |
| `AnalysisLoader` | `vcLoaderHTML` | sus 4 fases y su barra que **solo avanza** (curva `1 − e^(−n/16)`, topada al 97%) |
| `ActivityCard` | `vcActivityHTML` | premium notable por día, calls contra puts, 7 días |
| `MoneyFlowCard` | `vcMoneyFlowHTML` | el reparto alcista/bajista y sus cuatro azulejos |
| `OptionChainTable` | `vcCadenaHTML` | la cadena **entera**, ordenable por sus ocho columnas |
| `ChartPanel` | `renderProjTop5` | los cinco strikes de más nocional, punteados sobre el precio |
| `FlowPriceChart` | `renderProjFlowMoney` | el dinero por vela: compra arriba, venta abajo, escala log |
| `WatchlistCard` | `renderProjWatchlist` | el watchlist de **contratos**, con tu sizing del momento |
| `RiskProfileCard` | `vcRiesgoHTML` | tus dos presupuestos: capital por trade y quema de theta |
| `RepeatBadge` | `vcRepeatBadge` | el ×N del contrato golpeado varias veces |
| `ChartCrosshair` | `vcCrosshairCablea` | la cruz con la vela (A/M/m/C) y el precio proyectado |

Lo único que **no** se copia es su wordmark: la marca de esta pantalla es
Vertex. El logo de la EMPRESA analizada sí — eso es información, no marca.

### El watchlist: se cambia el de Vertex por el suyo

El de Vertex guardaba **tickers** y les colgaba alertas de precio. El suyo
guarda el **contrato entero** —strike, vencimiento, griegos y tu sizing del día
en que lo marcaste—. La diferencia no es cosmética: un ticker no se puede juzgar
después; una decisión con su foto, sí.

Portado: `watchlist.ts` → `wbj/tito/watchlist.py` (las 19 funciones puras +
BROKERS), `outboxStore.ts` → `outbox_store.py`, `watchlistStore.ts` →
`watchlist_store.py` (solo lectura, para la migración única), `watchlistLocal.ts`
→ el bloque `wlLocal*` del panel, y `/api/watchlist` → `/api/tito-watchlist`
(GET/POST/DELETE).

Lo que **no** cruza el puente hacia el servidor, igual que en su app: los
griegos, tu sizing y tu saldo. Solo viaja lo mínimo para identificar el contrato
en el broker — y con un broker de solo subyacentes, ni eso: solo el ticker.

Eliminado de Vertex: la vista `watchlistView`, sus nueve funciones `wl*`, su
`localStorage`, sus dos botones de navegación y `/api/watchlist-quote`. **No** se
eliminó la campana: `/api/watchlist-radar` y `/api/alerts/scan` siguen, y ahora
vigilan los subyacentes del watchlist de contratos.

### `/api/tito-logo` — por qué un proxy y no un `<img>` directo

La URL del logo que devuelve Massive **exige la `Authorization`**. Sin proxy, la
clave tendría que viajar al navegador para que la imagen cargara. El servidor la
baja y reenvía el binario; la credencial no sale de ahí. Y la cabecera lleva su
`Cache-Control: public, max-age=86400`: un logo no cambia en un día y cada
petición cuesta dos llamadas a Massive.

Un detalle que no es suyo pero tampoco es una divergencia: la `Authorization`
solo acompaña a la URL si apunta al dominio de Massive. Ningún host ajeno
aceptaría un bearer de Massive, así que la petición es idéntica en todos los
casos reales; lo que evita es que una URL torcida en la respuesta mande la clave
a un tercero.

### Lo que apareció al hacerlo

1. **Acentos graves en un comentario HTML dentro de una plantilla de JS.**
   La cierran, y el `SyntaxError` se lleva el **tab entero**. Segunda vez que
   pasa; lo cazó el check §6-bis, que existe justo por la primera.
2. **`start_sec` leído como milisegundos.** Los racimos caían en 1970 y ningún
   día se marcaba como tal en la gráfica del dinero. Lo cazó el smoke nuevo.
3. **El presupuesto de theta sobre el número equivocado.** `budgetsOf` suyo lo
   calcula sobre la **cuenta** (5% de $1.000 = $50), no sobre el riesgo por
   operación ($7,50) — sobre el número equivocado se descartarían contratos
   perfectamente operables. Ahora viaja calculado por el motor.
4. **Un test con un hueco propio.** `test_no_handler_points_at_a_function...`
   no reconocía `const f = s => …` como definición, así que daba por rota una
   función que existe. Corregido el test, no el código.
5. **Diez funciones públicas sin llamador** en `watchlist.py`. No son código
   muerto: en su app corren en el **cliente**, y aquí también (el bloque
   `wlLocal*`). Se portan igualmente porque son lo que el diferencial compara
   contra su archivo — sin ellas, la versión del navegador no tendría con qué
   contrastarse. Declaradas una a una con ese motivo.

### Cómo se verifica que es SU comportamiento

- **`diff_watchlist.sh`** — nuevo, el 13.º. Ejecuta **su** `watchlist.ts` en
  Node y el port en Python sobre 734 casos generados: colas mixtas de filas
  legado y de contrato, strikes con y sin decimales, contratos sin vencimiento,
  tickers en minúscula, colas ya sincronizadas y ya aparcadas.
  **734/734 idénticos.** Probado además con mutaciones: al cambiar `toFixed(4)`
  por `toFixed(2)` y quitar un `.upper()`, delata 64 casos.
- **`engine/tests/tito/test_watchlist.py`** — sus 56 casos de
  `watchlist.test.ts` traducidos, más 7 de persistencia. **63 pasan.**
- **`_smoke_componentes.mjs`** — nuevo. Ejecuta el JS **vivo** de los doce
  componentes contra un DOM de mentira con payloads realistas. **94 checks.**
  Es lo que cazó los puntos 2 y 3 de arriba.
- **`test_watchlist_y_componentes.py`** — 67 tests del cableado: las rutas, el
  buzón en disco, que el payload trae lo que cada componente lee, que la
  watchlist de Vertex se fue de verdad, y que ni los griegos ni el saldo cruzan
  el puente.

### Estado

**2.850 tests del motor · 455 de la capa web · 300 checks de auditoría · 94 del
smoke de componentes · 13 diferenciales a cero divergencias · 0 fallos.**

Cobertura contra su repo (`53d5a20`), ya completa:

| | Suyos | Portados |
|---|---|---|
| Componentes | 39 | **39** |
| Módulos de `web/lib` | 32 | **32** |
| Rutas de `web/app/api` | 11 | **11** |

Las **cuatro divergencias declaradas** siguen siendo las mismas y no ha
aparecido ninguna nueva: el perfil del inversionista en el servidor, el sizing
en el servidor, la Wheel sin bid, y el recorte de la clave de Massive.

---

## 41.25 Los datos dejan de vivir en un disco que se borra

Pregunta: *«En vez de usar la base de datos o el disco de Render, ¿no es mejor
hacerlo dentro de los archivos? ¿Como Víctor lo tiene? ¿Así no estamos limitados
a Render?»*

Dos cosas había que aclarar antes de responder, y las dos cambian la pregunta.

### Víctor no despliega nada

Su repo no tiene `vercel.json`, ni `render.yaml`, ni `Dockerfile`, ni workflows.
Sus scripts son `next dev` y `next start`. Y su `.gitignore` dice:

```
# --- historial local del agente (datos de mercado acumulados) ---
web/data/
data/
```

Sus archivos JSON **nunca se suben**. Viven solo en su computadora. Los archivos
le funcionan porque **corre el agente en su máquina** — no hay contenedor
efímero que los borre. Copiar eso al pie de la letra habría significado matar el
multiusuario.

### Archivos vs base de datos NO era lo que ataba a Render

Un `.json` y un `.db` escriben en el **mismo sistema de archivos** del
contenedor. En plan `free` ese sistema es efímero: cada redeploy y cada
despertar tras dormir lo borra entero. El problema nunca fue el FORMATO; era
DÓNDE vive el archivo.

### Lo que se hizo: el almacén

`vertex_almacen.py`. Un **clon de la rama `datos`** de este mismo repositorio.
Todo lo que el agente guarda cae ahí como archivo normal; un hilo de fondo hace
`commit` y `push` cada 20 s; al arrancar, un contenedor nuevo clona esa rama y
recupera todo.

La rama es **huérfana**: no comparte historia con `main`, así que los cientos de
commits de datos ni ensucian el historial del código ni disparan un despliegue
nuevo cada vez que analizas un ticker.

Resultado: nada se pierde aunque Render borre el disco o borres el servicio
entero; los datos son archivos que se abren en GitHub; y mudarse a Fly, a
Railway o a tu casa es copiar una variable de entorno.

### Cada agente en SU carpeta

Como se pidió — «cada uno guarda reportes diferente y en lugar diferente»:

| | Agente de ACCIONES | Agente de OPCIONES |
|---|---|---|
| Carpeta | `Reportes/<TICKER>/<fecha>/` | `Proyecciones/<TICKER>/<fecha>/` |
| Archivo | `reporte.json` | `scorecard.json` |
| Además | `prediccion.json` · `RESUMEN.md` | `RESUMEN.md` |
| Índice | `Reportes/INDICE.md` | `Proyecciones/INDICE.md` |

`Reportes/` no se movió: es donde `CLAUDE.md` la define y de donde come
`wbj track`. El de opciones estrena `Proyecciones/`. El mismo ticker analizado
el mismo día por los dos agentes son dos archivos distintos que no se pisan.

Cada carpeta lleva un `RESUMEN.md` legible sin abrir el JSON —con los 6 scores
y sus pesos, los escenarios y las advertencias— y un `INDICE.md` que se
**reconstruye entero** en cada guardado, para que no pueda citar reportes que ya
no están.

### Qué sube, y en qué forma

| | Dónde | Cómo |
|---|---|---|
| Reportes de los dos agentes | `Reportes/`, `Proyecciones/` | texto plano |
| Memoria y tesis | `Memoria/` | texto plano |
| Series del motor de Víctor | `Series/tito/` | texto plano, ritmo lento |
| Cuentas, contraseñas, Plaid, perfiles | `Privado/privado.enc` | **cifrado** |
| Claves de API (`API/`) | — | **nunca** |

Lo cifrado usa Fernet con `VERTEX_DB_KEY`. Y la regla dura: **sin esa clave no
se sube**. Un hash de contraseña en un repositorio, aunque sea privado, es un
objetivo de fuerza bruta offline; se prefiere perderlo a filtrarlo, y se dice en
voz alta en `/api/almacen` en vez de hacerlo en silencio.

Las series van a **otro ritmo** (6 h en vez de 20 s) porque cambian en cada
consulta y pesan: el archivo de trades de un ticker llega a 1,7 MB, y
reescribirlo 20 veces al día metería ~34 MB diarios de objetos en el repo.

Del scorecard de opciones **no** se archivan `chain`, `history`, `gex_heatmap`,
`levels_for_chart` ni `chart_geometry`: son 372 KB por reporte —1.500 filas a
254 bytes— o ~1,8 GB al año con 20 tickers. Son materia prima que Massive vuelve
a servir, no evidencia del veredicto. Lo que falta se **declara** en
`_no_archivado`; el archivo no miente por omisión.

### Lo que apareció al construirlo

1. **`WBJ_TITO_DATA=/var/data/tito` en `render.yaml` era una promesa falsa.**
   El bloque `disk:` estaba comentado, así que en plan free `/var/data` no era
   un disco montado: se creaba como carpeta normal y se borraba igual. La
   variable sugería persistencia donde no la había.
2. **El diagnóstico salía al revés.** `restaura()` devolvía un estado sin el
   campo `respalda`, así que el arranque leía `None` y avisaba de «ALMACÉN SIN
   RESPALDO» justo cuando el respaldo funcionaba. Un diagnóstico invertido es
   peor que no tenerlo.
3. **La restauración no actuaba nunca.** La guarda era «¿existe el archivo de la
   base?», pero `init_db()` corre **al importar el módulo**, así que para
   entonces el archivo siempre existe con 110 KB de esquema vacío. Las cuentas
   se recuperaban en el almacén y no llegaban a la base — el usuario no podía
   entrar. Ahora se comprueba si hay **filas**.
4. **`vertex_archivo` congelaba el almacén al importar.** Un proceso que
   reemplazara la instancia seguiría escribiendo en el directorio viejo, que ya
   no se respalda. Se resuelve en cada llamada.
5. **El paquete cifrado se re-subía cada 20 s para siempre.** El tar guarda
   fecha de modificación y uid, así que el mismo dato daba bytes distintos y el
   testigo SHA nunca coincidía. Se normalizan los metadatos.
6. **Un ticker largo se recortaba a 12 caracteres.** Dos símbolos distintos que
   empezaran igual acabarían en la misma carpeta, mezclando dos historiales sin
   que nada avisara. Ahora se rechaza en vez de recortarse.

### Cómo se verifica

- **`test_almacen.py`** — 48 tests. El que importa es `TestUnContenedorNuevo`:
  se borra el disco entero, como hace Render, y se comprueba que vuelven los
  reportes de los dos agentes, las cuentas —**con la contraseña todavía
  sirviendo**— y las series del motor.
- Dos workers empujando a la vez: ninguno pierde lo suyo (rebase + reintento).
- El token no aparece ni en el estado público ni en un push fallido.
- Solo los `.enc` salen de `Privado/`; un archivo en claro ahí se queda en el
  disco efímero.
- Sin `VERTEX_GIT_TOKEN` todo sigue funcionando y **se dice** — con la
  consecuencia escrita, no solo el hecho.

### Lo que hay que poner en Render

| Variable | Para qué |
|---|---|
| `VERTEX_GIT_TOKEN` | Token de GitHub con `Contents: Read and write`. **Sin él no hay respaldo.** |
| `VERTEX_DB_KEY` | Cifra cuentas y perfiles. Sin ella esos datos no se suben. Guárdala aparte: si la pierdes, el respaldo de las cuentas es irrecuperable — eso significa que está bien cifrado. |

### Estado

**2.850 tests del motor · 503 de la capa web · 303 checks de auditoría · 94 del
smoke de componentes · 13 diferenciales a cero divergencias · 0 fallos.**

---

## 41.26 Las tres series no guardaban como Víctor, y eso no se veía

Kevin lo pidió así: *"se supone que se guarde todo lo que Victor guarda y como
él lo hace. Lo único diferente es lo que yo implemente por render."*

Al comprobarlo con SU TypeScript leyendo los archivos del port —escribir con
Python, abrir con `node --experimental-strip-types`— salieron dos verdes y tres
rojos:

| Carpeta | Su app abría el archivo del port |
|---|---|
| `trades/` | ✅ `loadTrades → 1 fila · WULF270115C00020000 · premium 332000 · lado ask · delta 0.62` |
| `bars/` | ✅ |
| `iv/` | ❌ |
| `chain/` | ❌ |
| `predictions/` | ❌ |

### Por qué no se veía

Los tres son la MEMORIA del agente: el IV Rank real, el historial de cadena del
sub-agente 4 y el diario que cierra el lazo de calibración. Con un formato
distinto **no falla nada**. No hay excepción, no hay log, el reporte sale igual
de creíble. Simplemente el rank se queda para siempre en el proxy de volatilidad
realizada y la calibración nunca junta cinco muestras. Es el peor tipo de fallo:
el que se paga en calidad durante meses sin que nada lo diga.

### Qué estaba mal, y no era solo el nombre de las claves

1. **El sobre.** Él escribe `{ticker, updatedAt, snapshots:[…]}`; el port
   escribía una lista pelada. Su `load*` hace
   `Array.isArray(parsed.snapshots) ? parsed : null` — ante una lista devuelve
   `null`, o sea "no hay historial".
2. **Las claves.** `avg_iv` contra `avgIv`, `saved_at` contra `savedAt`,
   `horizon_days` contra `horizonDays`.
3. **Los DATOS de la cadena eran otros.** Esto era lo gordo. El port guardaba
   `{date, strikes:[{strike, call_oi, put_oi, volume}]}`; él persiste el
   **`StructureScore` aplanado** — `score`, `avgNotionalPerStrike`,
   `totalNotional`, `strikeCount`, `notionalPoints`, `dominantCount`,
   `strikePoints`, `volOIPct`, `volOIPoints`, `callPct`, `putPct`,
   `dominantSide`, `lowLiquidity` y los 5 `topStrikes`. Sin `score` ni `points`
   no se puede reconstruir **por qué** el sub-agente 4 puntuó lo que puntuó un
   día concreto, que es exactamente para lo que existe ese historial.
4. **El recorte.** Él corta por CANTIDAD (`.slice(0, N)`); el port cortaba por
   ventana de fecha. La diferencia importa: un ticker sin mirar durante seis
   meses conserva sus 365 fotos con su regla y las pierde TODAS con la otra.

### Lo que se hizo

- `stores.py`: `_sobre`, `_iso` (el `toISOString()` con milisegundos y `Z`),
  `_lee_sobre` (su guarda literal) y `_fusiona` (Map → `sort` descendente →
  `slice`). `save_chain_snapshot` y `save_iv_snapshot` reciben ahora el objeto
  entero (`StructureScore`, `IvContextScore`) como los suyos, no un resumen.
- `ivcontext.py` lee `avgIv`, la clave que hay en el archivo.
- **`migra_series()`**, que corre en cada arranque del almacén: convierte los
  archivos viejos en sitio, es idempotente y **dice lo que descarta**. La cadena
  vieja no se puede convertir —los datos que él guarda no estaban ahí— así que
  esos días se pierden y se cuentan aparte, en vez de dejar un archivo medio
  traducido que parezca completo.

### `diff_series.sh` — el diferencial número 14

Los otros trece comparan NÚMEROS. Este compara **el archivo**, y en las dos
direcciones, porque una sola no basta:

1. los dos lados guardan los mismos casos → ¿sale el mismo archivo?
2. SU TypeScript abre el archivo del port → ¿ve los mismos días?
3. el port abre el archivo de SU app → ¿ve los mismos días?

Se ejecutan SUS `chainStore.ts`, `ivStore.ts`, `predictionStore.ts`,
`structure.ts`, `ivcontext.ts` y `occ.ts` sin tocar, con el quitado de tipos
nativo de Node. Como sus `DATA_DIR` son `process.cwd()` fijados al cargar el
módulo, el lado Node se invoca dos veces con directorios de trabajo distintos:
es la única forma de que el mismo código suyo mire los dos árboles.

**27 casos · 767 fotos · 1 divergencia declarada.** La declarada es el dedupe
del diario por `(fecha, horizonte)` en vez de solo por fecha: su UI muestra un
horizonte a la vez, Vertex sirve los tres en la misma respuesta, y con su clave
dos de cada tres se perderían en silencio.

Que un diferencial no pueda fallar no vale nada, así que se probó al revés con
tres mutaciones: la clave en snake_case (9 casos rojos), el recorte por ventana
(25) y quitar el sobre (25). Las tres salieron con código 1.

### Lo que apareció al hacerlo

- **El comparador se inventaba una diferencia.** Redondear a 6 decimales, que es
  lo que hacen los otros diferenciales, rompe con nocionales de 1e9: `Math.round`
  de JS desempata hacia arriba y el `round` de Python hacia el par, así que el
  mismo double salía `1794425266.850001` en un lado y `...85` en el otro. En un
  diferencial de ARCHIVO no hay nada que redondear — se compara entero.
- **`diff_motor2.sh` tapaba el fallo original.** Su comparador traducía `avgIv`
  → `avg_iv` antes de llamar al port, así que los dos lados coincidían mientras
  el archivo era ilegible para la app del otro. Traducción fuera.
- **La auditoría se cayó sola.** El check "IV Rank pasa de proxy a historia real"
  construía el historial con `avg_iv`. Es exactamente su trabajo: en cuanto la
  clave dejó de ser la del archivo, la comprobación se puso roja.
- **Cuatro tests tenían la premisa invertida.** Estaban escritos para el formato
  viejo: afirmaban que el sobre de SU app se leía como vacío. Ahora el sobre es
  el formato y el archivo extraño es la lista pelada — que recupera
  `migra_series`, no `load_*`.
- **Un caso que no llega al tope no prueba el recorte.** Los casos avanzaban por
  días de calendario y el fin de semana empujaba tres fechas al mismo lunes, así
  que ninguna serie llegaba a su límite. El comparador ahora falla si algún
  bloque no toca su tope: un diferencial que no ejercita el `.slice` no está
  midiendo la regla que dice medir.

### Estado

**2.866 tests del motor · 503 de la capa web · 303 checks de auditoría (261 sin
`TITO_ROOT`, que es lo que se puede correr sin su clon) · 94 del smoke de
componentes · 14 diferenciales a cero divergencias no declaradas · 0 fallos.**

Las cinco carpetas (`trades/`, `bars/`, `iv/`, `chain/`, `predictions/`) guardan
ahora el formato de Víctor y son intercambiables con su app en las dos
direcciones. Lo único distinto sigue siendo lo de Render: dónde vive el archivo
(el almacén en la rama `datos`, porque él no despliega) y el perfil de Kevin.

---

## 41.27 Los 3 tests que se saltaban tapaban dos fallos reales

Kevin: *"¿porque hace 2 skips? ¿de que son? soluciona todo, no quiero que omitas
nada ni tenga ningun error ni nada."*

Los tres skips eran **condicionales**, no marcas de "pendiente": el test se
saltaba a sí mismo cuando el entorno no le daba lo que pedía. Y los dos que se
saltaban en la capa web resultaron estar tapando un fallo del producto.

### Los dos de `tests_vertex` — el autocompletado sí llamaba a la red

El fixture esperaba hasta 60 s a que FMP cargara el índice de tickers y, si no
llegaba, `pytest.skip`. Sin red o sin `FMP_API_KEY` eso es **siempre** —
incluida cualquier integración continua. Un test que nunca corre no protege
nada.

Al quitarle la dependencia de la red (índice sembrado con las 21 empresas reales
de EE.UU. cuyo símbolo o nombre empieza por N, que son las que compiten con NVDA
en el buscador), el test falló a la primera. Con la clave de FMP puesta:

```
N     -> NVDA NFLX NVO NOW …     0 llamadas
NV    -> NVDA NVO NVR NVT        0 llamadas
NVD   -> NVDA                    2 llamadas HTTP   ← 
NVDA  -> NVDA                    2 llamadas HTTP   ← 
```

**Las dos últimas teclas del ticker más buscado seguían bajando a la cola larga
de FMP.** El umbral contaba CANDIDATOS (`< 3` → pregunta), y eso falla justo en
el mejor caso: "NVD" y "NVDA" dejan un solo candidato local —NVDA— porque no hay
más empresas cuyo símbolo empiece así. Dos peticiones por tecla, hasta 2×2,5 s de
timeout, para no añadir nada: lo que buscabas ya salía el primero.

La regla correcta no es *"¿hay pocos?"* sino *"¿hay una respuesta buena?"*. Si
algún símbolo local empieza por lo que tecleaste (rango 0 o 1), FMP solo puede
añadir ruido por debajo. La cola larga se conserva entera para lo que de verdad
la necesita —un ADR o una small cap que no está en el índice—, donde ningún local
empieza por el término.

Y había un segundo motivo por el que esto no se veía: sin `FMP_API_KEY` la
función que consulta ni se ejecuta, así que el test **pasaba sin probar nada**
aunque el índice hubiera cargado. Ahora pone la clave a propósito.

Dos tests nuevos que faltaban: que un término que el índice NO cubre sí baja a
las dos rutas (cortar llamadas no puede cortar la cola larga), y que coincidir
por símbolo gana a coincidir por nombre — NEM ($70B, empieza por "NE") va antes
que NFLX ($500B, que solo coincide porque se llama "**Ne**tflix"). Siete veces
más capitalización y detrás: eso es lo que hace que el orden sea el del rango y
no el del market cap a secas.

### El de `engine` — asertaba sobre una clave que nadie lee

`test_the_dimension_lights_once_four_of_five_are_valid` comprobaba que
`revenue_quality_and_growth` enciende al llegar a 4 de 5 métricas válidas. Para
llenar el hueco pasaba `peer_revenue_growth` por el canal del analista… y esa
clave **no la lee nadie**. FIN-GR-003 lee `packet.estimates["peer_panel"]`. Así
que la dimensión se quedaba en 3/5 y el propio test se saltaba con
`"fixture cannot reach 4/5 without a peer panel"`.

Arreglado como lo llena la data real: el panel de pares va en el PAQUETE, con 8
peers, que es el mínimo que exige `SCORING_ENGINE.md` antes de permitir una
comparación contra pares. Y un test nuevo por el otro lado del umbral: con 7
peers FIN-GR-003 se queda MISSING y la dimensión no puntúa — siete no es "casi
ocho", es un número que se vería igual de creíble en el reporte sin serlo.

### Estado

**2.868 tests del motor · 507 de la capa web · 303 checks de auditoría · 94 del
smoke de componentes · 14 diferenciales · 0 fallos y CERO skips.**

De regalo, la capa web pasó de 207 s a 86 s: los dos fixtures que esperaban a
FMP se llevaban 120 s de reloj por corrida para acabar saltándose el test.

Quedan ~20 `pytest.skip` condicionales en el motor (fixtures de valuación,
`Cerebro not present`) y 4 `skipif` de entorno (node, git). Ninguno se dispara
en este repo — la corrida completa reporta `0 skipped`, no "skipped porque sí".

---

## 41.28 Que no se pueda volver a esconder un test, y tres papeles que mentían

Kevin: *"solucionalo todo y que este perfecto."*

Arreglar los 3 skips de §41.27 uno a uno no cierra nada: el problema no eran
esos tres, es que **saltarse un test no hace ruido**. `pytest` lo pone en una
línea de resumen que nadie lee y sale con código 0. Ya había costado dos veces.

### El guardián

`engine/tests/_saltos.py` + los dos `conftest.py`: un salto es un **fallo**,
salvo que el motivo diga literalmente que falta una herramienta del entorno
(`node`, `git`). Esa distinción es la que importa:

- *"no tengo node instalado"* → limitación de la máquina; se lee y se decide.
- *"el fixture no llega a 4 de 5"* → un test que dejó de medir; se arregla.

Los motivos permitidos van en una tupla `ENTORNO` **por nombre**: añadir uno
nuevo es un acto deliberado que queda escrito, no un descuido que se cuela.

Vive fuera de los dos `conftest.py` porque los dos lo usan y `conftest` es un
nombre que pytest ya ocupa — importarlo desde el otro conftest resuelve al
propio archivo. Se carga por ruta.

Probado en las dos direcciones y en las dos suites: un `skip("el fixture no
llega a 4 de 5")` tumba la corrida con código 1 y nombra el test y el motivo; un
`skip("node no esta instalado")` sale con 0. Y la auditoría comprueba que la
regla sigue instalada en las dos, porque quitarla no rompe nada visible.

### Un skip que era código muerto

`test_overlay_parity.py` se saltaba "sin `Entradas/NVDA.json` en este entorno".
Ese archivo **está en git**: existe en cualquier clon. El skip no protegía de
nada; solo daba permiso a ese test para dejar de comprobar sin decirlo. Ahora es
un `assert`.

### Tres papeles que mentían

Barriendo la documentación contra el código:

1. **`RESUME.md` señalaba un fallo que ya no existía.** Decía *"Siguiente: A-02
   — dos funciones llaman `load_settings()` sin inyectar `FMP_API_KEY`"*, y
   A-02 está resuelto desde el 2026-07-30 (§99 de este mismo archivo). Peor que
   el dato viejo: daba a entender que los demás seguían abiertos.
2. **Este archivo se contradecía a sí mismo.** La tabla del §5 marca los 26
   hallazgos resueltos; la línea de "orden de arreglo sugerido", tres párrafos
   más abajo, dejaba A-02..A-06 sin tachar. Quien leyera solo esa línea contaría
   cinco abiertos.
3. **`RESUME.md` decía "no hay remoto configurado"** y lo hay
   (`github.com/kevintaboas18/vertex_fund_os`), que además es donde vive la rama
   `datos` del almacén. También declaraba *"1959 pasan, 1 skip"* y *"47 pasan"*.

Los tres corregidos con los números medidos hoy, y `RESUME.md` lleva ahora los
comandos de la auditoría y de los 14 diferenciales, que no estaban.

### Estado

**2.868 tests del motor · 507 de la capa web · 307 checks de auditoría · 94 del
smoke de componentes · 14 diferenciales · 0 fallos, 0 avisos y CERO skips** —
y a partir de ahora el cero de skips no es una observación, es una condición de
la corrida.

---

## 41.29 Ronda 8 — el tab entero, área por área, contra `53d5a20`

Kevin: *"no puedes omitir una área, ni métrica, agentes, sub agente, cálculos,
fuentes ni nada."*

Las siete rondas anteriores auditaron contra **tres registros**: módulos de
`web/lib`, rutas de `web/app/api` y componentes `.tsx`. Esta ronda empezó
preguntando qué queda FUERA de esos tres registros — y ahí estaban los agujeros.

### Inventario completo de su repo (53d5a20, sin cambios desde la ronda 7)

| | Él | Vertex | Cómo se verifica |
|---|---|---|---|
| Módulos `web/lib/*.ts` | 32 | 32 portados | 16 diferenciales |
| Rutas `web/app/api` | 11 | 11 | registro + `test_route_safety` |
| Componentes `.tsx` | 39 | 39 | registro + smoke de 94 checks |
| **TS fuera de `web/lib`** | **3** | **1 sin comparar** | ← el agujero |
| Páginas Next (`page`/`layout`) | 5 | n/a (Vertex no es Next) | — |

Los tres de fuera son `app/format.ts`, `app/ideas/types.ts` y
`app/wheel/types.ts`. Los dos `types.ts` son solo interfaces —cero código
ejecutable, comprobado—. `format.ts` no.

### Hallazgo 1 — `format.ts`: 14 de 14 cifras mal, en cada pantalla

`format.ts` es el único módulo suyo que no vive en `web/lib`, así que ningún
registro lo cubría. **Nunca se comparó, en siete rondas.** Al compararlo:

| valor | él | Vertex |
|---|---|---|
| 2.440.000 | `$2.44M` | `$2.4M` |
| 332.000 | `$332.00K` | `$332.0K` |
| 0 | `$0.00` | `$0` |
| −2.440.000 | `-$2.44M` | `$-2.4M` |
| 1e12 | `$1.00T` | `$1000.0B` |

Y no era solo contra él: **el panel pintaba las dos cosas a la vez**. Los
componentes portados en §41.24 ya usaban `VC_MONEY` (su `Intl`) y el panel
viejo seguía con `fmtAbbr`. La misma pantalla, el mismo dólar, dos formatos.

Cerrado: `fmtAbbr` es ahora su `money` y `fmtMoney` su `money0`; se añadieron
los tres que faltaban (`hmET`, `timeOf`, `dateOf`). **`diff_format.sh`** lo fija
con 1.870 comparaciones y 4 divergencias declaradas (el ausente y el `NaN` se
pintan «—», no «$0.00» ni «$NaN»).

De paso salieron tres cosas más al leer los sitios de uso:

- **`$${fmtAbbr(...)}`** en la columna de crédito de la Wheel: `fmtAbbr` ya trae
  el símbolo, así que se leía **`$$2.44M`**.
- **Los racimos del tape se rotulaban en UTC** (`toISOString().slice(11,16)`,
  con la cabecera diciendo "(UTC)"). Él usa `hmET`. Leer "14:30" cuando el reloj
  del mercado marca 10:30, en la única tabla que existe para decir CUÁNDO entró
  el dinero.
- **La fecha·hora de las transacciones era un corte del ISO** (`slice(5,16)`),
  también en UTC, donde él usa `dateOf`+`timeOf` formateados.

### Hallazgo 2 — la Wheel entera, sin diferencial y sin un solo test

`wheel.ts` (421 líneas, 11 exports), `wheelAfford.ts`, `wheelUniverse.ts` y
`earnings.ts` estaban **portados** y no los medía nada:

- ningún diferencial los tocaba;
- los 13 tests que respondían a `-k "wheel or earnings"` eran **todos del agente
  de ACCIONES** (`test_brief`, `test_technical`…), nada que ver con la Wheel.

O sea: la estrategia que decide qué put vender, con cuánto colateral, con qué
probabilidad de expirar sin valor y en qué orden se listan los candidatos no
tenía ni test ni comparación. **`diff_wheel.sh`** (1.072 casos, incluidas las
constantes: presets, recortes, umbrales y los 41 símbolos del universo) y el
port de sus 48 casos de test.

Encontró dos divergencias reales a la primera:

1. **`"fuerza 83.0"` donde él pone `"fuerza 83"`.** El port ya usaba `js_round`
   para redondear, pero interpolaba con una f-string de Python. En JS `${83}` es
   `"83"`. 60 de 200 casos. Es texto que el usuario lee en el tooltip del score.
2. **Una fecha de reporte ilegible ABSOLVÍA en vez de penalizar.** Su
   `getTime()` da `NaN`, toda comparación con `NaN` es falsa y cae en `"dentro"`
   → el candidato pierde 7 de sus 10 puntos. El port devolvía `"no_aplica"` →
   **10 de 10**. Un dato corrupto pasaba de penalizar a absolver, justo en la
   guarda que existe para que no te pille un reporte dentro del vencimiento. Se
   adopta el suyo, que además es el prudente.

### Hallazgo 3 — `fetchBars`: el intradía no existía

De los 6 exports de `massive.ts`, `fetchBars` era el único sin portar, y con él
faltaba el selector de marco temporal de su gráfica de flujo. Dos de sus tres
consumidores piden `tf=1y` (diario), que el payload ya servía por otro camino;
el tercero —`FlowPriceChart`— es el del intradía.

La diferencia no es de resolución: **agregado por día se ve QUÉ día entró el
dinero grande; en velas de 5 minutos se ve si el precio se movió ANTES o DESPUÉS
de que entrara**, que es exactamente lo que mide el sub-agente 6. La divergencia
estaba escrita en un comentario del panel ("su versión intradía agrupa por vela
de 5 min") y llevaba ahí desde entonces.

Cerrado: `fetch_bars` + `TfBar` en `massive.py`, `/api/tito-bars` con su tabla
de marcos literal (`1y`, `15m10d`, `5m5d`, y un `tf` desconocido cae al de por
defecto como en su ruta), y el selector en el panel — que además superpone la
línea de precio sobre las barras de dinero, como su `FlowPriceChart`.

La auditoría cazó sola la ruta nueva cuando todavía no tenía cliente
(`SIN DECLARAR: ['tito-bars']`), que es para lo que existe ese registro.

### Checklist del tab, área por área

| Área | Estado | Cómo se sabe |
|---|---|---|
| Sub-agente 1 · Agresividad | ✅ | `diff_motor` (1.142 casos, con basura) |
| Sub-agente 2 · Convicción | ✅ | `diff_motor` |
| Sub-agente 3 · Inusualidad | ✅ | `diff_motor` |
| Sub-agente 4 · Estructura | ✅ | `diff_motor` + `diff_series` (su foto) |
| Sub-agente 5 · Contexto IV | ✅ | `diff_motor2` (918) + `diff_series` |
| Sub-agente 6 · Confirmación | ✅ | `diff_motor` + `store.ts` acumulado |
| Prediction Pro | ✅ | `diff_motor2` + `diff_calib` (182 diarios) |
| GEX / heatmap | ✅ | `diff_motor2` + `diff_motor3` (349) |
| Niveles por confluencia | ✅ | `diff_motor` + `diff_frescura` (342) |
| Riesgo y sizing | ✅ | `diff_motor2` · el perfil es de Kevin (declarado) |
| **Wheel** (4 módulos) | ✅ **nuevo** | `diff_wheel` (1.072) + 50 tests |
| Noticias | ✅ | `diff_motor3` |
| Cadena (`compute.ts`) | ✅ | `diff_compute` (604 filas) |
| Cono / movimiento esperado | ✅ | `diff_cono` |
| Geometría de la gráfica | ✅ | `diff_geo` (274) |
| Watchlist (4 módulos) | ✅ | `diff_watchlist` (734) |
| **Formateadores de pantalla** | ✅ **nuevo** | `diff_format` (1.870) |
| Reloj / fechas de mercado | ✅ | `diff_reloj` (223) + `diff_primitivas` |
| Persistencia `trades/` | ✅ | `diff_store` (47) |
| Persistencia `bars/` | ✅ | `diff_bars` (27) |
| Persistencia `iv`/`chain`/`predictions` | ✅ | `diff_series` — ida y vuelta con su TS |
| Fuente Massive (6 exports) | ✅ | `test_massive_shape` + `preflight_vivo` · **`fetch_bars` portado en esta ronda** |
| Fuente MarketSnack (2) | ✅ | idem |
| Las 11 rutas | ✅ | registro + `test_route_safety` |
| Los 39 componentes | ✅ | registro + smoke de 94 checks sobre el JS vivo |
| Memoria entre sesiones | ✅ | §41.26 · el archivo es intercambiable con su app |
| Almacén durable | ✅ | §41.25 · 48 tests, contenedor nuevo recupera todo |

**Lo único que NO es suyo, y es deliberado:** el perfil de inversionista de
Kevin (`Perfil Inversionista/Kevin.md`, que lee `risk.py`), su email, y dónde
vive el archivo — la rama `datos` del almacén, porque Víctor no despliega y su
`data/` está en `.gitignore`.

### Estado

**2.918 tests del motor · 512 de la capa web · 308 checks de auditoría · 94 del
smoke de componentes · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

---

## 41.30 Las constantes de sus RUTAS, que el cotejo no miraba

Kevin: *"¿lo solucionaste todo? ¿ya está al 100%?"*

En vez de contestar, se volvió a buscar. Y apareció otra cosa, del mismo tipo
que `format.ts`: **un sitio entero que ningún registro cubría por construcción**.

El cotejo de constantes de la auditoría (§9-quater) compara `export const`
numéricas de `web/lib/*.ts` — 34 valores. Sus **rutas** (`web/app/api/*/route.ts`)
tienen las suyas propias y quedaban fuera:

| ruta | constante | él | Vertex |
|---|---|---|---|
| `/api/flow` | `MIN_PREMIUM` | 100.000 | 100.000, **sin nombre** |
| `/api/flow` | `LEAN_MAX_PAGES` | 6 | 6, **sin nombre** |
| `/api/flow` | `TABLE_CAP` | 100 | **120** |
| `/api/flow` | `CONVICTION_TABLE_CAP` | 150 | **25** |
| `/api/ideas` | `MAX_IDEAS` | 60 | 60, con otro nombre |

Tres eran números sueltos escritos dentro de la llamada: el mismo valor que el
suyo, pero sin nombre, así que nada podía cotejarlos y el día que él cambie uno
no se enteraría nadie.

### La que sí cambiaba lo que se ve

`CONVICTION_TABLE_CAP` valía **25** contra sus **150**, y no era "una tabla más
corta". De esas filas comen sus TRES tarjetas —`ConvictionTransactions`,
`ActivityCard` y `MoneyFlowCard`— y la última es la gráfica que dice *"el dinero
de CADA DÍA"*.

Con las 25 de mayor premium eso no es el dinero del día: es el de los 25 trades
más grandes. Un día entero cuyo flujo fuera de tamaño medio **desaparecía del
gráfico**, y las tres categorías que se apoyan en esas filas —Convicción,
Inusualidad y Contexto IV— se quedaban con una sexta parte de la evidencia en
pantalla.

`TABLE_CAP` iba al revés: 120 filas donde él sirve 100.

### Cerrado

Las cuatro viven ahora en `scorecard.py` con SU nombre, y `vertex_api.py` las
importa por nombre en vez de escribir el número. Y —lo que cierra la clase y no
el caso— **el cotejo de la auditoría ahora escanea también sus rutas**: recorre
`web/app/api/*/route.ts`, busca cada `const` numérica por nombre en el motor y
en la capa web, y falla si falta o si difiere. Encontró sola la quinta
(`MAX_IDEAS`, que aquí se llamaba `_IDEAS_MAX`).

### Estado

**2.918 tests del motor · 515 de la capa web · 310 checks de auditoría · 94 del
smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

---

## 41.31 Ronda 9 — `page.tsx`, la superficie que nadie había leído

Kevin: *"verifica cada área del agente de opciones una a una sin perder nada."*

Las rondas anteriores compararon sus **módulos** (`web/lib`), sus **rutas** y sus
**componentes**. Faltaba el archivo que los une: `page.tsx`, 488 líneas donde él
hace CÁLCULO, no solo maquetado. Nunca se había leído entero.

### Sus tres uniones, que no son la misma

`page.tsx` arma tres conjuntos de trades con dedupe por `id`, y son distintos:

| destino | conjunto |
|---|---|
| GEX | `convRows ∪ unusualRows` |
| heatmap | `convRows ∪ unusualRows` |
| **niveles** | `convRows ∪ notable` ← otro |

El port los tenía bien. Lo que no estaba bien era el comentario del heatmap en
`vertex_api.py`: decía *"esa unión ya la hizo el motor, aquí se reusa"* y el
código pasa `conviction_flow` a secas. Es equivalente **solo porque**
`unusuality_score` se calcula sobre `conviction_rows`, así que la unión no añade
filas. Eso es un **invariante**, no una coincidencia, y no lo comprobaba nadie:
el día que la inusualidad salga de otro universo, el heatmap dejaría de ver esas
filas en silencio. Ahora lo fija `TestLasTresUnionesDeSuPagina`, con un test por
cada una de las tres.

### `topFlows` — el bloque que faltaba entero

Su `PredictionCard` pinta **"Top 3 flows notables"** debajo de los escenarios:
las tres mayores de `convRows ∪ notable` por premium, con la marca alcista/bajista
`(call ∧ ask) ∨ (put ∧ bid)` — comprar calls y vender puts son la misma apuesta.

No existía. Es el único sitio del panel donde los tres targets de Prediction Pro
van acompañados de las **operaciones concretas** que los sostienen: sin él los
números salen sin que se pueda ver de qué dinero se dedujeron, que es lo
contrario de la regla de la casa. Portado: `top_flows` en el payload (con el
`_unir` del motor, no una copia) y `vcTopFlowsHTML` en el panel.

### `RepeatBadge` — portado y muerto

`vcRepeatBadge` y `vcRepeatCounts` estaban **definidas y sin un solo llamador**.
Las tres tablas donde él usa la insignia —`TradesFeed`,
`ConvictionTransactions`, `UnusualityCard`— pintaban un `↻` suelto **sin el ×N**:
decían que hubo repetición pero no cuántas veces, que es la mitad de la señal.
Tres golpes al mismo strike no es lo mismo que doce.

Conectadas las tres, con `buildRepeatCounts` construido **dentro de cada tabla**
como hace él: el ×N cuenta las apariciones AQUÍ, no en el mercado entero.

### Por qué se coló: el check miraba de menos

El auditor ya comprobaba que un componente declarado existiera… pero solo la
**primera** función citada en su nota, y solo que estuviera **definida**. Con eso
`RepeatBadge` pasaba como portado estando muerto.

Ahora revisa TODAS las funciones que cada nota cita y las dos formas de mentir:
citarla sin que exista, y que exista sin que nadie la llame. Son 32. Probado al
revés borrando los dos llamadores: sale en rojo con el nombre.

### Verificado además, área por área

- **Los cinco caminos de aprendizaje**, ejecutados de punta a punta:
  `trades/` (5 filas → sub-agente 6), `iv/` (60 fotos = el umbral exacto donde
  el IV Rank real desplaza al proxy), `chain/`, `predictions/` (6 → calibración)
  y `bars/`. El archivo lleva su sobre `{ticker, updatedAt, snapshots}`.
- **La calibración mueve los targets de verdad**: con sesgo +6% y 9 muestras, la
  base pasa de 105,00 a 108,00 en los tres horizontes. No es un campo que se
  guarda y nadie lee.
- **Las reglas de visualización en el tab**: los tres escenarios dan un RANGO
  (nunca un valor único) y cada uno declara su `driver` y su probabilidad.
- **Las 34 constantes de `web/lib` + las de sus rutas**, valor a valor.

### Estado

**2.921 tests del motor · 519 de la capa web · 310 checks de auditoría · 94 del
smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

---

## 41.32 Ronda 10 — la cadena que existía menos un eslabón

Kevin: *"verifica cada área una a una sin perder nada."*

Esta ronda entró por `types.ts`, sus interfaces compartidas, comparando campo a
campo contra lo que el port produce. Y ahí estaba el fallo, de un tipo nuevo:
**la cadena entera montada menos el eslabón que produce el dato**.

### `CompanyInfo`: se servían 12 de sus 18 campos

`fetch_company` devolvía 12. Faltaban `exchange`, `homepage_url`, `employees`,
`list_date`, `description` y `has_logo`.

Lo que hace que esto sea grave y no cosmético es lo que había **alrededor**:

- `_tito_company` ya **declaraba** `exchange` y `employees` en su dict base,
  valiendo `None`.
- `vcCompanyHTML` ya los **leía**: el subtítulo de la cabecera es
  `[exchange, sector].filter(Boolean).join(' · ')` y hay una casilla de
  empleados.

O sea que el panel pedía la bolsa, el mapeador la declaraba, y nadie la
buscaba. El subtítulo salía con el sector solo —donde él pone "Nasdaq ·
Semiconductors"— y la casilla de empleados vacía. **Durante todas las rondas
anteriores, sin un solo error.**

También va su `EXCHANGE_NAMES` (XNAS→Nasdaq, XNYS→NYSE, …), con su regla: el
código desconocido se muestra tal cual, no se esconde.

Y `has_logo`, que estaba forzado a `True` "para no doblar la latencia": no la
dobla. La marca viene en el MISMO `/v3/reference/tickers/` que ya trae el nombre
y el sector. Ahora se usa el valor real — un 404 menos por cada ticker sin logo,
y se deja de prometer una imagen que no existe.

### El check que lo generaliza

Un campo que el frontend lee y el backend no manda **no rompe nada**: se pinta
"—" y nadie se entera. Así que se añadió el cruce entero:

> se recogen TODOS los campos de primer nivel que leen las 23 funciones de
> render del panel, y se cruzan contra lo que sirve la ruta.

Encontró dos más al estrenarse (`aggression` y `truncated`), los dos legítimos
—vienen de la cinta y de ideas—, ahora declarados por nombre y con su
procedencia. Probado al revés renombrando `top_flows`: sale en rojo con el
nombre del campo.

### Y un test propio que era débil

El primer test de paridad comprobaba que la **clave** existiera. Pasaba igual
con el cableado borrado, porque `exchange` seguía existiendo valiendo `None`. Un
campo que existe y siempre vale nulo se pinta igual que uno que no existe. Se
reescribió para comprobar que el **valor llega**, y se verificó borrando la
línea: ahora falla.

### Verificado y correcto en esta ronda

- **`watchlistLocal.ts`**: sus 6 exports están (`hasMigrated`/`markMigrated`
  incluidos, con nombre en español — la importación única del watchlist viejo
  funciona y es idempotente).
- **`types.ts`**: `Row`, `ChainMeta`, `DailyBar`, `TfBar` y los eventos SSE,
  campo a campo.
- Las tres uniones de `page.tsx`, la calibración que mueve los targets y los
  cinco caminos de aprendizaje siguen verdes (§41.31).

### Estado

**2.925 tests del motor · 524 de la capa web · 310 checks de auditoría · 94 del
smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

---

## 41.33 Ronda 11 — lo que sus componentes deciden por dentro

Las rondas 8-10 compararon módulos, rutas, `format.ts`, `page.tsx` y `types.ts`.
Del lado de los componentes se había comprobado que la función **existiera** y
que alguien la **llamara** (§41.31). Nunca lo que decide **por dentro**.

Sus 39 componentes llevan **172 umbrales propios** que no están en el motor. Son
los que convierten un número en texto y en color.

### Once reglas suyas que no estaban

| componente | regla | qué decide |
|---|---|---|
| `LevelsCard` | `strengthLabel` 70/50/30 | "Muy fuerte" · "Fuerte" · "Moderado" · "Débil" |
| `IvContextCard` | `ivColor` 90/61/40 | el color de la IV |
| `ValidationCard` | ≥55 verde · <45 rojo | el color del hit rate (y **un decimal**, no cero) |
| `MemoriaCard` | sesgo ±1% | "suele apuntar bajo" · "alto" · "bien calibrado" |
| `MemoriaCard` | error ±3% / ±7% | el color de cada predicción del track record |
| `GexHeatmapCard` | `intensity > 0.12` | si la celda enseña su número o solo color |
| `NewsCard` | 60m / 24h | "hace 12m" · "hace 3h" · "hace 2d" |
| `TradesFeed` | score ≥80 / ≥60 | el color del score de cada trade |
| `NivelesSimples` | `strength >= 20` | qué niveles entran en la lista |
| `SimpleChart` | `strength >= 25` | qué niveles se dibujan (ya estaba) |
| `IdeasTable` | `|n| < 10` → céntimos | un theta de $0,25/día salía "$0" |

Ninguna rompe si falta: **la pantalla simplemente dice menos**. `strengthLabel`
es texto que se lee —el número 62 no dice si es mucho o poco—; la frase de sesgo
es la que te hace mirar los targets con reserva; y el 0,12 del heatmap es lo que
evita que una rejilla de 10×N se llene de cifras y tape las tres celdas que
importan.

### El track record no se veía

Al portar el color del error de `MemoriaCard` apareció el hueco de verdad: **su
tarjeta no enseña un resumen, enseña una TABLA** — fecha, qué predijo, qué pasó
de verdad, cuánto se equivocó y qué escenario acertó.

`review_predictions` **ya se llamaba** en el servidor y sus `evals` se tiraban:
solo viajaban los agregados. El panel decía *"6 predicciones vencidas, sesgo
+2%"* y **no había forma de ver ninguna**. Para lo único que existe esa sección
—saber si el agente acierta— el resumen es justo lo que no basta.

Ahora viajan las 12 más recientes y se pintan con el color de su error.

### Dos fallos en el propio auditor

1. **El regex de nombres se equivocaba.** Buscaba `vc*`, `renderProj*` y `wl*`,
   pero media docena de sus componentes caen en `renderVictorTargets` y
   `renderVictorChart`. Con el prefijo corto, el check daba por no portado lo
   que sí estaba (`VeredictoCard`) — un chequeo que se equivoca de nombre
   denuncia lo bueno y calla lo malo. Ampliado a `render*`: de 32 funciones
   comprobadas se pasó a **38**.
2. **El check de umbrales no miraba los helpers.** Los umbrales viven en su
   propio helper, fuera de la función que los usa, así que denunciaba como
   ausentes cinco que sí estaban.

### La única declarada

`ProWallsCard` filtra los niveles del gráfico con `strength >= 35`; él tiene DOS
gráficas de niveles (`SimpleChart` con ≥25 y ésta con ≥35) y el panel de Vertex
tiene UNA. Se usa el ≥25, el más permisivo: con ≥35 la gráfica única perdería
los niveles medios, que él sí enseña en la otra vista. Declarado por nombre en
`_UMBRAL_DECLARADO`.

### Estado

**2.925 tests del motor · 529 de la capa web · 311 checks de auditoría · 94 del
smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

---

## 41.34 Ronda 12 — las palabras, no solo los números

La ronda 11 comparó los **172 umbrales** de sus componentes. Quedaban sus
**1.185 cadenas de texto**, que es lo que de verdad se lee.

Filtrando el ruido (nombres de clase CSS, `use client`, identificadores como
`contractType` o `next/link`) quedaban **~20 etiquetas reales suyas** ausentes.

### El panel hablaba en enum

`NewsCard` pintaba `noticias bullish (3)` donde él pone **"Noticias positivas"**.
Sus dos mapas —`BIAS_LABEL` y `SENT_LABEL`— no estaban. Y `neutral` tiene DOS
textos suyos según dónde salga: *"Sin dirección clara"* en la tarjeta (el sesgo
no se decanta) y *"Sin noticias marcadas"* en la línea de contexto (no hubo
titulares). No es lo mismo no encontrar señal que no tener nada que mirar, y él
lo distingue.

De paso, cada titular lleva ahora su palabra POSITIVA/NEGATIVA/NEUTRAL —él no se
fía solo del punto de color— y su `reasoning`, que explica POR QUÉ el titular es
positivo o negativo y ya viajaba en el payload sin pintarse.

### Cuatro de sus siete señales

Su `NotableTable` marca cada trade con hasta siete chips, cada uno con su texto
y su explicación. El panel pintaba **cuatro símbolos sueltos** —`↻ ⛓ ↑ ↓`— sin
decir qué significaban, y le faltaban **las dos calientes**:

- **`$1M+`** — trade de más de un millón.
- **`Delta fuerte`** — más de $100K con delta > 0,60.

Justo las dos que marcan una apuesta direccional de tamaño, que es lo que este
tab existe para encontrar. Faltaban también `LEAP` y `simultáneo`. Los cuatro
campos existían en `FlowFlags` del motor y no viajaban en el payload.

### "Fuerza de ejecución" y la banda del spread

Su `ConvictionCard` enseña `execution.avgRaw` con un decimal bajo el rótulo
**"Fuerza de ejecución"** y el pie *"qué tan agresivas fueron las órdenes"*. El
panel ponía "Ejecución · N trades · calidad del fill": el número que él enseña
—el promedio sin redondear— **no viajaba**, aunque el motor lo calcula.

Y el pie del spread era un recuento (*"3 con spread ancho"*) donde él pone una
banda (*"muy líquido" / "aceptable" / "ancho"*). El recuento no dice si el
spread MEDIO es bueno, que es lo que la métrica mide. Ahora van los dos.

### El test contrario hizo su trabajo

Al sustituir el pie del spread, `TestElPanelNoTiraNadaDelPayload` se puso rojo:
`conviction.spread.wide_count` dejaba de pintarse. Es el check simétrico del de
la ronda 10 —aquél busca lo que el panel lee y nadie manda; éste, lo que el
motor manda y nadie pinta— y cazó la regresión en la misma sesión que la
introdujo.

### Lo que NO era un hueco

`"Bear case" / "Base case" / "Bull case"` salen como **"Bajista / Base /
Alcista"**: es su etiqueta traducida, no una que falte. Y de las 84 "cadenas
ausentes" del primer barrido, 45 eran nombres de clase CSS e identificadores de
propiedad — un filtro mal calibrado denuncia ruido y esconde lo que importa.

### Estado

**2.925 tests del motor · 529 de la capa web · 311 checks de auditoría · 94 del
smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

---

## 41.35 Ronda 13 — la columna que decía cuán probable era llegar

Entrada nueva: las **ordenaciones** de sus componentes y `conditions.ts`, que
nunca se habían comparado directamente.

### El hueco: `probTouch` no existía en ninguna parte

Su `NivelesSimples` calcula, para CADA nivel,
`probTouch(spot, l.price, iv, horizonDays)` con la IV del GEX — la probabilidad
de que el precio **llegue** a ese nivel dentro del horizonte. En el panel esa
columna no existía.

No es un adorno. La tabla enseñaba precio, fuerza y distancia, y con eso un
soporte de **fuerza 80 al 15%** y otro de **fuerza 50 al 2%** se leen igual de
"fuertes". Medido sobre el fixture, con la IV del GEX (0,483) a 20 días:

| nivel | fuerza | distancia | P(toque) |
|---|---|---|---|
| $99,73 | 34 | −0,3% | **100%** |
| $97,15 | 25 | −2,9% | 84% |
| $90,00 | 34 | −10,0% | **38%** |

Los dos de fuerza 34 son el mismo número y **una probabilidad de llegar que se
diferencia en 62 puntos**. Eso es lo que la columna desambigua.

El motor ya traía `prob_touch` (de `expected_move.py`, portado y probado desde
la primera ronda). Solo faltaba llamarlo y servirlo. Ahora cada nivel viaja con
su `touch`, con su misma IV y su mismo respaldo de 0,4 cuando el GEX no la da.

### Lo que estaba bien

- **`conditions.ts`: 33 de 33.** Cada `id`, cada `code`, y los dos conjuntos
  —`MULTI_LEG_CODES` y `CANCELED_CODES`— idénticos. Es el módulo que decide si
  un trade se descarta por cancelado o se marca como pata de una estrategia
  combinada, así que un código de más o de menos movería los seis scores.
- **La tabla de cadena SÍ es ordenable** por columna (`vcOrdenaCadena`), como su
  `OptionChainTable`.
- **`ActivityCard`** ordena por el timestamp real del primer trade de cada día y
  no por la etiqueta — su bug evitado, que ya estaba portado con su comentario.

### Declarada

Él tiene DOS vistas de niveles: `LevelsCard` (pro, soportes y resistencias
separados) y `NivelesSimples` (estudiante, una lista fundida por cercanía con
la P(toque)). El panel tiene UNA tabla, con la estructura de `LevelsCard` y la
columna de `NivelesSimples`. Se queda la fusión: son los mismos datos y una
sola pantalla no gana nada partiéndolos en dos vistas que dicen lo mismo.

### Estado

**2.925 tests del motor · 533 de la capa web · 311 checks de auditoría · 94 del
smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

## 41.36 Ronda 14 — la cinta, que era la pestaña sin barrido

**Fecha:** 2026-08-08 · **Contra:** `infusionvictor/agente-tito-metralleta@53d5a20`

Las rondas 11, 12 y 13 barrieron el scorecard, Ideas y la Wheel. La cinta
—**Time & Sales**, la tercera pestaña— nunca se barrió, y ahí estaba lo gordo:
`_fila` sirve 27 campos por operación y la tabla enseñaba nueve columnas.

### Lo que estaba mal

1. **`bid` y `ask` viajaban en cada fila y nadie los pintaba.** Desde el primer
   día. Las flechas ↑/↓ dicen que el print salió **fuera** de la horquilla; sin
   la horquilla, un centavo sobre el ask y un dólar sobre el ask son la misma
   flecha. Su `/flow` tiene columna «Bid/Ask» justo para eso. Ahora va bajo el
   precio, y en el `title` de la celda.

2. **La columna «Score» no tenía encabezado.** Diez `<th>` para once `<td>`.
   En escritorio no se nota; en el móvil `vcTablaResponsive` copia la etiqueta
   de la columna N a la celda N, así que **todas las de la derecha salían con
   el nombre de la de al lado**: el Score se llamaba «Dinero», el Dinero
   «Griegos», y la última no llevaba nada. Llevaba así desde que se añadió el
   Score.

3. **El desglose de los Puntos no existía.** Su `NotableTable` pone «Volumen
   X/10 · Horario Y/10 · Repetición Z/10» en el `title`. Solo viajaba el total,
   y un 21/30 sin desglose no dice si vino del tamaño de la orden, de la hora a
   la que entró o de cuántas veces se repitió el contrato — tres señales que se
   leen distinto. `_fila` ahora manda `unusual_parts`.

4. **`sideLabel` tiene cuatro ramas y aquí había tres.** Su cuarta —
   `aggression === "unknown"`— cae a `r.side`, el lado crudo de la cinta. Aquí
   el `else` se comía el desconocido y lo rotulaba **«Mid»**, que es afirmar una
   lectura de la horquilla que el motor declaró que no pudo hacer.

5. **El veredicto de agresividad no salía en la cinta.** Su
   `AggressionScoreCard` va con ella: la barra sola no separa un 55/45 de un
   90/10 — eso lo hace la frase («Sin flujo agresivo» / «Compra agresiva (al
   ask)» / «Presión al bid» / «Mixto»). Estaba portado, pero solo en el
   scorecard del ticker. Faltaban también el `n` de notables y el aviso de
   cinta truncada.

6. **La nota al pie de la cinta describía símbolos que ya no existían.** Decía
   «↻ repetido · ⛓ multileg · ★ volumen > OI» cuando las señales se pintan con
   texto desde que se portó `VC_SENALES`. Ahora es la suya: qué son los Puntos,
   qué significa una fila resaltada y qué es cada lado.

7. **El aviso de cotización retrasada de la Wheel vivía dentro de un `if`.**
   Solo se enseñaba con `quotes_missing`, o sea justo en el caso en que el
   usuario ya sabía que la prima era estimada. Cuando Massive **sí** sirve
   horquilla la cotización **sigue siendo retrasada**, y ahí no se decía nada.
   Su `wheel-disclaimer` no está dentro de ningún condicional. Ahora tampoco.

8. **No se podía repetir un escaneo de la Wheel.** Su `↻ Volver a escanear` no
   estaba: la única forma era irse a otro preset y volver, que además tira el
   resultado bueno por el camino. Un escaneo degradado se repite.

9. **El `RESUMEN.md` del agente de opciones callaba tres cosas que el
   `scorecard.json` ya traía**: los niveles con su P(toque), los tres flujos
   más grandes y el track record. Un resumen es lo que se lee dentro de tres
   meses; con el score y los escenarios solos no se decide nada. Y al
   escribirlo apareció el fallo de siempre en pequeño: la primera versión leía
   `horizon`, `predicted` y `actual` — nombres **parecidos** a los que sirve la
   ruta (`horizon_days`, `base`, `actual_close`) pero distintos. Un nombre
   equivocado no rompe nada: pinta «—» y el archivo se ve bien para siempre.
   Igual con `touch`, que es una fracción 0-1: sin el ×100 el resumen escribía
   «1%» donde el motor dice 81%.

### Lo que estaba bien

- **El trío de la gráfica** (`buildScales`/`packLabels`/`smartDomain`) está
  portado y medido por `diff_geo` (274 casos), que los **extrae del HTML** en
  vez de copiarlos. El `PADDING` de su `PriceChart.tsx` coincide al píxel.
- **Cero funciones públicas sin llamador** en `engine/wbj/tito/*.py`, cruzando
  un recorrido del AST contra todos los tests, `vertex_api.py`,
  `vertex_archivo.py` y `engine/scripts/*.py`.
- **La degradación no revienta**: con la cadena caída, `/api/projection-targets`
  responde 200 con `ok:false` y un mensaje, no un 500.
- **El archivo declara lo que no guarda**: `_sin_derivado` recorta la materia
  prima y añade `_no_archivado`, para que el archivo no mienta por omisión.
- **`VC_SENALES`** son sus siete señales con su texto y su `tip`, incluido el
  código OPRA dentro del tooltip de multileg.

### Lo que se añadió para que no vuelva a pasar

- **Barrido de hojas de la cinta** (`test_la_cinta_no_sirve_nada_que_el_panel_tire`):
  el tercer barrido, el que faltaba. Cada campo que sirve `/api/tito-tape` tiene
  que tener consumidor en `renderProjTape` o estar declarado con su motivo. Es
  el que cazó `bid`/`ask`. Y `test_el_registro_no_miente` ahora también mira la
  cinta, para que una declaración no sobreviva al campo que declara.
- **Tablas cuadradas**, en los dos smokes: para **cada** tabla `vc-t` que el
  panel produzca de verdad, `<th>` del encabezado == `<td>` por fila (contando
  `colspan`). Se barre todo lo pintado, no una lista escrita a mano, así que una
  tabla nueva entra sola. Mutado: quitar el `<th>` de «Score» lo pone en rojo.
- **Los nombres del resumen contra los de la ruta**: cada clave que
  `_md_opciones` lee tiene que existir en `vertex_api.py`. Mutado con
  `horizon_days`→`horizon` y `actual_close`→`actual`: falla. Y la fila del track
  record se comprueba **entera**, no a trozos, que es lo que hace que un «—» de
  más no pase por normal.

### Declarada

**Una respuesta, no un stream.** Sus cuatro rutas largas (`analyze`, `flow`,
`ideas`, `wheel`) son SSE y emiten entre 40 y 100 eventos `{type:"step",label}`.
Aquí devuelven un JSON al final: esto corre detrás del proxy de Render en plan
free, que no garantiza `text/event-stream` sin buffering, y un stream a medio
bufferizar es **peor** que ninguno — la pantalla se congela en el paso 3 y
parece colgada. Lo que no cambia: su propio `AnalysisLoader` ya **colapsa** los
~100 pasos en cuatro fases y su comentario lo dice —«no leemos el texto de cada
paso, solo cuántos han llegado»—; esa es la pantalla portada (`vcLoaderHTML`),
con su curva asintótica y su tope del 97%. Se pierde la etiqueta fina («página 5
de 6»), y solo mientras carga. Queda en el registro de divergencias del auditor,
que ahora son cinco.

### Estado

**2.925 tests del motor · 543 de la capa web · 311 checks de auditoría · 105 +
58 del smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

## 41.37 Ronda 14-bis — Ideas: la mitad de `size_flow` no salía del servidor

**Fecha:** 2026-08-08 · **Contra:** `infusionvictor/agente-tito-metralleta@53d5a20`

Cerrada la cinta, quedaba `ideas/page.tsx` (427 líneas) y su `IdeasTable.tsx`.
Su tabla Pro tiene **trece** columnas; aquí había diez, y las que faltaban eran
justo la historia del theta — que es para lo que existe `size_flow`.

### Lo que estaba mal

1. **El titular contaba mal.** Su `operables` son las que **sí puedes operar**,
   más «N descartadas». Aquí decía «N operables» con N = **la tabla entera**,
   incluidas las que el propio panel marca «no cabe» dos columnas más a la
   derecha. Con $1.000 de capital eso no es cosmético: casi todas las filas de
   una corrida caen del lado de «no cabe», así que el titular decía lo
   contrario de lo que decía la tabla. Ahora sale de `perfil.caben`, que el
   motor ya calculaba.

2. **Seis campos de `Sizing` se calculaban y se tiraban en la ruta**:
   `burn_days`, `theta_burn_per_contract`, `total_burn`, `burn_pct_of_account`
   y `fully_decays`. Son la mitad de `size_flow` y la mitad de su `IdeaCard`:
   «el theta se come $X al día por contrato: $Y en N días (Z% de la cuenta)»,
   «te frenó el theta, no el capital» y el aviso de que **el contrato se
   consume entero dentro del horizonte**. Sin eso, «te caben 3» es un techo sin
   el precio de mantenerlos — que es exactamente lo que distingue una opción de
   una acción.

3. **`blocked` viajaba como `dict` y se pintaba como `[object Object]`.** El
   motor devuelve `{reason, detail}`; el panel hacía `_vcEsc(s.blocked)`. Ahora
   viaja `detail` (la frase) y `blocked_reason` (el código, para la API).

4. **La columna de vencimiento enseñaba solo el DTE.** Su `expiryLabel` lleva
   el comentario «fecha real + días restantes — **nunca solo “57d”**». Un «57d»
   no dice si cae en un mensual, si cruza los earnings o si es el viernes que
   viene.

5. **El historial no llevaba ni su banda de color ni la mediana de sesiones.**
   Su `HistoryCell` tiñe en 60 y 45 —no los 55/45 del hit rate de la Wheel— y
   enseña «~N ses», que es cuánto tarda el patrón en resolverse.

6. **`BindingChip` vivía dentro de un `title`**, que en un móvil no existe.
   Ahora es un chip: «bloqueado» / «no alcanza» / «θ» / «prima».

7. **Faltaban el precio del contrato, la hora del flow y el disclaimer de
   `/ideas`** — el que dice que el tamaño sale del **precio al que se ejecutó
   el flow**, no de la cotización viva, porque el feed no la entrega.

8. **El horizonte del sizing no se declaraba.** `size_flow` quema theta *hasta*
   una fecha, así que el techo de contratos cambia con ella. Su app la elige
   con un botón (`HORIZON_LABELS`); aquí sale del perfil —la parte que sí es de
   Kevin— pero no se veía, y un «te caben 3» sin horizonte es un número sin
   unidad. Ahora va en la franja de perfil y en el encabezado de la columna.

### Lo que apareció al ponerlo: catorce declaraciones podridas

El registro `_NO_SE_PINTAN` dice, hoja por hoja, qué sirve el motor y el panel
no pinta, con su motivo. Tenía un agujero de diseño: el barrido **salta** una
hoja declarada antes de mirar nada, así que una declaración sobrevive a su
propio motivo. Catorce afirmaban lo contrario de lo que hacía el código:

- Cuatro de Ideas (`expiration`, `price`, `timestamp`, `history.median_sessions`)
  quedaron obsoletas en esta misma ronda.
- Tres más de Ideas (`id`, `symbol`, `sizing.cost_pct_of_account`) llevaban
  rotas desde que la estrella del watchlist y el `title` del techo las usaron.
- Siete de la Wheel (`iv_source`, `premium.raw`, `metrics.breakeven` y las
  cuatro de `score.annualized`) desde que se añadió la fila desplegable.

`test_el_registro_tampoco_miente_al_reves` cierra el agujero: para Ideas, Wheel
y la cinta, ninguna entrada del registro puede referirse a algo que el panel sí
pinta. Con una exención declarada: `trades.volume` colisiona por NOMBRE con
`unusual_parts.volume` (el volumen del contrato contra el sub-score de tamaño
de la orden). Los dos nombres son los suyos —`FlowRow` y `TradeScores`—; se
exime la hoja en vez de renombrar su modelo para comodidad del test.

### Estado

**2.925 tests del motor · 547 de la capa web · 311 checks de auditoría · 105 +
62 del smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

## 41.38 Ronda 14-ter — la escritura de la que depende el aprendizaje era la única muda

**Fecha:** 2026-08-08

Auditando lo que se guarda —que es la parte que hace que el agente mejore con
el tiempo— aparecieron dos cosas.

### Lo que estaba bien

Las **quince** funciones de sus cinco stores (`barsStore`, `chainStore`,
`ivStore`, `predictionStore`, `outboxStore`, `watchlistStore`, `store`) están
portadas y llamadas desde las rutas. Las dos que no tienen llamador —`save_bars`
(la llama `cached_daily_bars` por dentro) y `load_chain_history` (**tampoco lo
tiene en su `chainStore.ts`**)— están declaradas con su motivo en el registro
de huérfanas del auditor. El lazo entero cierra:

| Se escribe | Se lee | Para qué |
|---|---|---|
| `save_chain_snapshot` | `load_chain_history` | reconstruir por qué el sub-agente 4 puntuó lo que puntuó |
| `save_trades` | `load_trades` | el backtest del sub-agente 6 |
| `save_iv_snapshot` | `load_iv_history` | el IV Rank REAL, a los 60 días |
| `save_prediction` | `review_predictions` → `calibration_from_review` | el sesgo que corrige los targets |

### Lo que estaba mal

**`_tito_remember` se tragaba su error en un `except Exception: pass`.**

Las otras tres escrituras pasan por `_guarda`, que apunta el fallo en
`memory.escrituras_fallidas`, y el panel lo pinta. La de predicciones —la que
cierra el lazo de calibración, o sea **lo único que hace que el agente mejore
con el tiempo**— era la única muda. Un disco lleno o un permiso mal puesto
dejaban el track record congelado durante meses mientras el panel seguía
diciendo «0 predicciones vencidas», que es exactamente lo que se ve el primer
día: la degradación se disfrazaba de estreno.

Ahora devuelve el motivo, entra en la misma lista y se registra en el log.
`TestLaMemoriaFallaEnVozAlta` lo comprueba en las dos direcciones —falla y se
dice, va bien y no inventa alerta— y que el panel siga pintando la lista.
Mutado (descartar el valor de retorno): falla.

### Y catorce declaraciones podridas más, en los registros del scorecard

El mismo control inverso de 41.37, aplicado a `_HOJAS_NO_SE_PINTAN` y
`_SUB_NO_SE_PINTAN`. Tres mentían: `aggression.ratio` (se pinta desde que el
veredicto de agresividad llegó a la cinta), `structure.notional.total` y
`structure.avg_notional` (los dos se pintan en la misma línea de la tarjeta de
Estructura). Con una exención declarada por colisión de nombre:
`iv_context.iv.contracts` contra `by_expiration[].contracts`.

### Estado

**2.925 tests del motor · 551 de la capa web · 311 checks de auditoría · 105 +
62 del smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

## 41.39 Ronda 15 — sus PALABRAS, sus PÁGINAS, y la evidencia del sub-agente 6

**Fecha:** 2026-08-08 · **Contra:** `infusionvictor/agente-tito-metralleta@53d5a20`

Las catorce rondas anteriores compararon módulos, componentes, rutas, umbrales,
formateadores y hojas de payload. Quedaban tres clases de superficie que nada
enumeraba.

### 1 · Sus páginas no estaban en ningún registro

El registro de componentes excluye `layout` y `page` a propósito, y las tres
páginas de subcarpeta (`ideas/`, `wheel/`, `flow/`) ni siquiera entran en el
glob de `app/*.tsx`. Sus **cuatro pantallas** eran la única superficie suya que
nada enumeraba: se leyeron a mano en las rondas 9 y 14, y esa lectura no dejaba
rastro comprobable. `PAGINAS_SUYAS` las declara con qué las cubre aquí y qué de
ellas no se porta; si añade una quinta, el check falla.

### 2 · Sus palabras: 61 frases, una por una

Las rondas 11 y 12 miraron los **umbrales** de sus componentes y las etiquetas
de sus **bandas**. Nadie enumeró nunca la PROSA — las frases que explican qué
significa el número. «−$2.400M de GEX» no dice nada; «γ− (tendencia: conviene
seguir el movimiento)» sí, y esa mitad de la tarjeta se perdía sin que fallara
nada: un panel con todos los números y ninguna explicación pasa todos los tests
de cableado.

`FRASES_DECLARADAS` extrae de sus `.tsx` las frases en español de 18-80
caracteres y exige que cada una esté en el panel o declarada con su motivo. De
las 61: **42 portadas, 19 declaradas**. Lo que faltaba y se añadió:

- **El régimen del heatmap.** Mi tarjeta enseñaba el GEX neto y no lo que
  implica. Ahora lleva sus dos frases y su leyenda verde/rojo.
- **Los títulos de niveles.** «Resistencias» a secas obliga a saber qué es una
  resistencia; «techos por encima del precio» no. Y su nota del final —la que
  explica que un nivel vale más cuando coinciden precio y opciones, con la
  tolerancia de agrupación— faltaba entera.
- **El par de niveles clave** (`key_support` / `key_resistance`) y
  `tolerance_pct`: tres campos de su `LevelsResult` que la ruta tiraba. Su
  `lvl-key` son los dos números que se miran antes que la tabla.
- **El vacío de `LevelsCard`.** Devolver `''` hacía desaparecer la sección, y
  «no hay tarjeta» se lee como «el agente no la calcula». La calculó y no
  encontró nada.
- **Las tres frases de bloqueo de la Wheel.** «sin bid» es la etiqueta del
  motor; «nadie está poniendo precio de compra: no podrías vender» es lo que
  significa. Un bloqueo sin su porqué parece un fallo del agente.
- **Las dos `mem-stat` del track record**, con `base_touch_rate` — otro campo
  calculado y tirado. Acertar la DIRECCIÓN y llegar al PRECIO son dos cosas
  distintas: «subió como dijo pero se quedó a mitad de camino» es medio
  acierto, y con un solo número no se distingue.
- **El vacío de `MemoriaCard`**, el desglose de los 6 parámetros de
  Inusualidad, «Desglose por señal» del scorecard y la frase del skew.
- **El disclaimer del dashboard.** Él lo pone DOS veces. La Wheel y las Ideas
  ya tenían el suyo; la pantalla que enseña los TARGETS —la que más se parece a
  una promesa— era justo la que no decía nada.
- **Las preguntas de los sub-agentes.** Él tiene TRES redacciones para la
  casilla de Estructura en tres archivos distintos; aquí están las dos que
  corresponden a las dos superficies portadas, y la tercera queda declarada.

### 3 · `validation.outcomes` — la evidencia del sub-agente 6

Su `ValidationCard` no enseña solo la tasa: enseña la tabla **«Qué pasó después
de cada flow»** — cada operación pasada con cuánto recorrió a favor, cuánto en
contra, cuánto tardó y si acabó validada o absorbida. Se calculaba **entera** en
`validation.py` y **moría en el servidor**.

Es exactamente lo que la regla del proyecto prohíbe perder: *sin evidencia, no
hay número*. Un 62% de tasa sin sus 25 filas no deja ver si vino de tres
aciertos grandes o de veinte pequeños, que se leen muy distinto. El barrido de
hojas no podía cazarlo porque el campo **no estaba en el payload**: solo mira lo
que la ruta sirve.

### 4 · Lo que decide si se VE en Render

- **El JS del panel compila** (`node --check` sobre el bloque real). Un acento
  grave dentro de un comentario HTML cierra la plantilla y se lleva el tab
  entero — ya pasó dos veces.
- **Las 45 rutas que el panel pide existen** en la app. Ninguna huérfana.
- **Sin claves, las cuatro rutas del tab degradan a 200 + `ok:false` con su
  motivo**, no a 5xx. El `/` sirve el panel completo (716 KB) con las cinco
  funciones de render dentro.
- **Menos una:** `/api/tito-bars` responde 502 cuando Massive falla —igual que
  su ruta— y el consumidor se lo tragaba con **tres `return` mudos**. En un
  gráfico que se llama «dinero contra precio», que falte el precio sin avisar
  se lee como que no hubo movimiento. Ahora lo dice, y el aviso se borra solo
  cuando el reintento va bien.

### 5 · Dos tests que mentían por construcción

`test_el_panel_pinta_la_columna` y `test_el_panel_pinta_la_tabla_con_el_color_del_error`
cortaban la función a 2.200 y 3.000 caracteres fijos. Añadir texto arriba
empujaba lo comprobado fuera de la ventana y el test denunciaba como ausente
algo que seguía tres líneas más abajo. Ahora leen la función entera.

### Estado

**2.925 tests del motor · 562 de la capa web · 317 checks de auditoría · 105 +
62 del smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

## 41.40 El despliegue caído: una variable BIEN configurada rompía el arranque

**Fecha:** 2026-08-08 · **Síntoma en Render:** «Exited with status 1 while
running your code»

### La causa

`vertex_api.py` importa el motor de Víctor a NIVEL DE MÓDULO:

```python
from wbj.tito.scorecard import (_unir, CONVICTION_TABLE_CAP, LEAN_MAX_PAGES,
                                MIN_PREMIUM, TABLE_CAP)
```

Pero `engine/` llegaba a `sys.path` **solo como efecto secundario** de
`_sec_user_agent()`, que lo insertaba dentro de su rama de respaldo:

```python
def _sec_user_agent():
    ua = (os.environ.get("EDGAR_USER_AGENT") or "").strip()
    if ua:
        return ua                       # ← retorna SIN insertar el path
    try:
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)   # ← el único que lo ponía
```

Con `EDGAR_USER_AGENT` **definida** —que es el caso en Render, y el que la
política de fair-access de la SEC exige— la función devuelve el valor y retorna
antes de tocar el path. El import de arriba muere entonces con
`ModuleNotFoundError: No module named 'wbj'`, uvicorn sale con código 1, y
Render lo reporta con esa frase que no dice nada.

### Por qué no se veía

**La ausencia de configuración salvaba el arranque.** Nadie define
`EDGAR_USER_AGENT` para desarrollar, así que en local la función caía siempre al
respaldo, insertaba el path de paso, y todo lo demás importaba. Es al revés de
lo que se asume al probar: no era una variable faltante lo que rompía, era una
variable correctamente puesta.

Los tests tampoco lo veían: corren dentro de un pytest cuyo `conftest` ya tiene
`engine/` en el path, así que el import de nivel de módulo nunca se ejercitaba
en frío.

Y el `preflight_render.py` de la ronda anterior **tampoco**: probaba el arranque
*sin* claves —para comprobar que las rutas degradan— y esa es exactamente la
condición bajo la que el fallo no ocurre. Un preflight que solo prueba el caso
sin configurar mide medio despliegue.

### El arreglo

`sys.path.insert(0, _WBJ_ENGINE_PATH)` va **donde se define la ruta**, a nivel
de módulo. El path de un motor que vive dentro del repositorio no puede depender
de a quién se le ocurra tocarlo primero. Los `sys.path.insert` repartidos por el
archivo se quedan —todos preguntan `if not in sys.path`— para que ningún import
suelto vuelva a depender del orden.

### Lo que impide que vuelva

- **`TestElArranqueNoDependeDeQueFalteConfiguracion`**: importa `vertex_api` en
  un proceso NUEVO, con el entorno de Render y sin él. Mutado (quitando la
  línea del arreglo): 2 de 4 en rojo.
- **El preflight ahora enciende las variables de Render** (`ENTORNO_RENDER`) y
  añade un paso 4 que importa el módulo en frío, con y sin ellas. Y se
  autentica con `X-Vertex-Token`: con `VERTEX_API_TOKEN` puesta, un 401 es la
  seguridad C-02 funcionando, no un fallo — antes el preflight lo contaba como
  error y medía la puerta en vez de las rutas.

### Estado

**2.925 tests del motor · 566 de la capa web · 317 checks de auditoría · 105 +
62 del smoke · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

## 41.41 Ronda 16 — la cobertura: qué lee cada sub-agente y qué le falta

**Fecha:** 2026-08-08 · **Contra:** `infusionvictor/agente-tito-metralleta@53d5a20`

Las quince rondas anteriores compararon el CÓDIGO contra el suyo. Esta mira otra
cosa: **con qué dato salen los números**.

### Lo que estaba bien (medido, no supuesto)

El lazo de memoria cierra en los cuatro caminos. Verificado ejecutando el ciclo
real —guardar, leer, filtrar, puntuar— y no con diccionarios inventados:

| Camino | Medido |
|---|---|
| `save_trades` → `load_trades` → `trades_utiles` → sub-agente 6 | 5 guardados → 5 leídos → 5 pasan el filtro → **flows=5** |
| El mismo, con flujo de hace 40 días | 5 outcomes **resueltos**, score 10, hit rate 100%, umbral 3,38% |
| IV Rank sin historia propia | `realized-proxy` sobre **220 días** de barras |
| IV Rank con 70 días acumulados | cambia solo a `iv-history` |
| Barras del panel | 365 días, cacheadas por día de mercado |

Y la Wheel, sin proponérselo, **calienta el cache de barras de sus 41 símbolos**
en cada escaneo (`cached_daily_bars` guarda), así que cualquier análisis
posterior de esos tickers arranca con el proxy de IV completo.

**Lo que NO se hizo, a propósito:** guardar las cadenas del escaneo de la Wheel
como fotos de estructura. Su cadena está filtrada a puts dentro de la banda de
DTE del preset — un `structure_score` sobre ese subconjunto no es comparable con
el de una cadena entera, y mezclarlos en la misma serie la corrompería. Más
datos no es mejor si no son comparables.

### Lo que estaba mal

**1. `/api/tito-health` no lo llamaba nadie.** El diagnóstico existía: tocaba
Massive, MarketSnack, el disco, la IV y los flows uno por uno, y decía el
impacto y el arreglo de cada fallo. Estaba declarado como «se consulta a mano al
desplegar», o sea nunca. Es la única respuesta a la pregunta que el scorecard no
contesta: **un 62/100 sostenido por dos categorías de seis se ve idéntico a uno
sostenido por las seis.**

**2. Le faltaban tres coberturas** — justo las que deciden si el agente mejora
con el tiempo o se estrena cada semana:

- **`memoria.predicciones`**: el lazo de calibración. Sin él los targets nunca
  se corrigen; el agente puede llevar seis meses apuntando un 8% de más y
  seguir apuntando lo mismo.
- **`memoria.cadenas`**: la única evidencia que no se puede recomprar. Massive
  vende la cadena de HOY, no la del martes pasado.
- **`memoria.respaldo`**: el que decide si los otros tres sobreviven. En Render
  free el disco se borra en cada redeploy; sin `VERTEX_GIT_TOKEN` los contadores
  vuelven a cero solos y el agente se estrena otra vez **sin que nadie lo note**
  — los números que enseña son correctos, simplemente empiezan de nuevo.

**3. Dos constantes de la ventana corta iban a mano** (`100_000` y `6`) teniendo
el nombre a un import de distancia. Coincidían con los suyos, así que no cambiaba
ningún número; lo que fallaba era el ACOPLE: `/api/tito-tape` baja exactamente
esa misma ventana y sí los usa por nombre. Si Víctor sube `LEAN_MAX_PAGES` a 10,
la cinta bajaría 10 páginas y el scorecard seguiría en 6 — dos pantallas
puntuando sobre universos distintos sin que nada lo dijera. Y el cotejo de
constantes de la auditoría solo ve los nombres: un número suelto le es invisible.

### La pestaña de Cobertura

Quinta pestaña del tab, después de las cuatro suyas. Por cada check: qué es, en
qué estado está, **a qué sub-agente afecta** y qué se rompe si falta, con el
arreglo. La traducción importa: no «falta MARKETSNACK_COOKIE», sino «Agresividad,
Convicción, Inusualidad y Contexto IV se quedan sin dato».

Es de Vertex, no suya, y se declara así: su despliegue es local y él ve el log.
Aquí el agente corre en un servidor que nadie mira.

Al cablearla, el registro de huérfanas del auditor cazó que `load_chain_history`
—declarada «sin llamador, también en su chainStore.ts»— ahora sí tiene uno. La
declaración se retira: una que afirma lo contrario del código es peor que
ninguna.

### Estado

**2.925 tests del motor · 574 de la capa web · 317 checks de auditoría · 105 +
62 del smoke · 16 diferenciales · preflight de Render en verde · 0 fallos,
0 avisos, 0 skips.**

## 41.42 El respaldo no funcionaba en Render — y solo en Render

**Fecha:** 2026-08-08 · **Síntoma:** «Tengo el token y aun así no se guardan ni
las cuentas ni los reportes ni nada».

### La causa

El almacén deducía a qué repositorio subir con `git remote get-url origin`
sobre el directorio del propio código. Eso exige **tres** cosas que en una
máquina de desarrollo se cumplen siempre y en Render pueden fallar todas:

1. que el directorio desplegado traiga `.git` — Render exporta el árbol, no
   siempre el repositorio;
2. que `git` esté en el PATH **del proceso** (está en el build; en el runtime
   no está garantizado);
3. que la URL empiece por `https://` — una `git@github.com:owner/repo.git` se
   descartaba entera.

Si cualquiera fallaba, `_url()` devolvía cadena vacía, `respalda` daba `False`
y el operador veía un aviso genérico de «no se pudo deducir el repositorio»
**al lado de un token perfectamente válido**.

El reparto era el peor posible: **se respaldaba en local y no en producción**,
que es justo donde el disco sí se borra.

### El arreglo

Tres fuentes, en orden: `VERTEX_ALMACEN_REMOTO` → **`RENDER_GIT_REPO_SLUG`** →
el `origin` del código. La de en medio la pone el propio Render en el entorno de
todo servicio desplegado desde git, vale `owner/repo`, y **no necesita `git`, ni
`.git`, ni poder lanzar subprocesos**: es el servicio diciendo de qué repo salió.
El slug se valida contra `[A-Za-z0-9._-]+/[A-Za-z0-9._-]+` antes de construir
una URL con él.

Y la tercera fuente ya no descarta la forma SSH: es el mismo repositorio escrito
de otra manera, y se traduce.

### Dos cosas más que salieron al tirar del hilo

**El orden en `_arranca_almacen`.** `WBJ_TITO_DATA` —que es lo que manda las
series del motor DENTRO del almacén— se fijaba **después** de `restaura()`. Si
la restauración fallaba (red, token, rama), el proceso seguía vivo y sirviendo,
pero todo lo que analizara a partir de ahí caía en `./data/tito`, fuera de lo
que se respalda, y se perdía entero. Y el segundo fallo viajaba escondido detrás
del primero, porque el aviso que se pinta es el de la restauración. Ahora se fija
**antes**: si la restauración falla, el primer respaldo que sí funcione se lo
lleva igual.

**El motivo, cuando el token SÍ está.** «No se pudo deducir el repositorio»
junto a un token puesto se lee como una contradicción. Ahora dice las tres
fuentes que probó y con qué resultado, y empieza por «TIENES el token, pero…».

### Verificado de punta a punta

Con un remoto real y el ciclo completo —arrancar, escribir, sincronizar— llegan
a la rama `datos`:

```
Privado/privado.enc                      ← cuentas y perfiles, cifrados
Proyecciones/WULF/2026-08-08/scorecard.json + RESUMEN.md + INDICE.md
Reportes/NVDA/2026-08-08/reporte.json    + RESUMEN.md + INDICE.md
Series/tito/trades/WULF.json             ← sub-agente 6
Series/tito/predictions/WULF.json        ← calibración
```

Y `WBJ_TITO_DATA` apunta a `almacen/Series/tito`, que es lo que hace que esas
dos últimas sobrevivan al redeploy.

### Lo que impide que vuelva

`TestElRemotoSeDeduceEnRender` (7 casos: el slug de Render, la validación del
slug, la precedencia, las cinco formas de `origin`, y el motivo con y sin token)
y `TestLasSeriesCaenDentroDelAlmacenPaseLoQuePase`, que comprueba en el código
que `WBJ_TITO_DATA` se fija antes de restaurar. Mutados: 2 en rojo.

### Estado

**2.925 tests del motor · 585 de la capa web · 317 checks de auditoría ·
16 diferenciales · preflight de Render en verde · 0 fallos, 0 avisos, 0 skips.**

## 41.43 Ronda 17 — su DISEÑO, que nadie había mirado en dieciséis rondas

**Fecha:** 2026-08-08 · **Contra:** `web/app/globals.css` de `53d5a20` (1.041 líneas)

Dieciséis rondas comparando su lógica, sus textos, sus umbrales, sus constantes
y sus payloads. **Su hoja de estilos no la había abierto nadie.** El tab se
dibujó con utilidades sueltas de Tailwind, y ahí dentro hay decisiones suyas que
no son adorno.

### La que más se nota: `tabular-nums`

Su CSS lo pone en `table`, o sea en TODAS. Las veinte tablas del tab no lo
tenían. Sin él cada dígito lleva su propio ancho —un `1` ocupa menos que un
`8`— así que `$1.111` y `$8.888` no acaban en la misma columna. En una pantalla
que es casi toda números, eso no es tipografía fina: es si se pueden comparar
dos precios de un vistazo o hay que leerlos.

### Lo demás que se portó

| Suyo | Qué hace |
|---|---|
| `tbody tr:nth-child(odd)` | la cebra, para no saltar de fila al recorrer una tabla ancha |
| `tbody tr:hover` | la fila bajo el cursor se distingue |
| `thead th { position: sticky }` | en 25 filas con scroll, el encabezado no se va justo cuando hace falta |
| `tr.unusual td` | la fila inusual se tiñe **entera**, no solo su celda de puntaje |
| `.pill` 999px/700/MAYÚSCULAS/tracking .5 | lo que las separa del texto de al lado sin necesitar más color |
| `--space-*` 8/14/24/32 | con su regla escrita: *«el gap ENTRE cards (lg) tiene que superar al padding DENTRO (md), si no la proximidad se invierte y las cards se leen pegadas»* |
| `@media (max-width:640px)` → md 16, lg 22 | *«en móvil el aire de sección se recorta: ahí el scroll pesa más que el agrupamiento»* |

Y **sus tonos exactos**, que no son los de Tailwind: su verde es `#12b76a` y el
`emerald-400` es `#34d399`. Parecidos en la cabeza, distintos en pantalla.
`--accent #2f6bff` · `--green #12b76a` · `--red #f04438` · `--put #f97066` ·
`--amber #f79009`.

### Divergencia declarada: la luminosidad

Su paleta es **clara** (`--bg:#f3f4f6`, `--panel:#fff`, `--text:#101828`) porque
su app es una página suelta. Este tab vive dentro de Vertex, que es oscuro:
meterle un bloque blanco no sería «igual que él», sería un parche. Se portan sus
**tonos, sus proporciones y sus reglas**; se invierte la luminosidad. Está dicho
en el propio CSS, no dejado a la interpretación.

### Dos cosas que salieron al escribirlo

**Un selector con el id equivocado es CSS muerto.** El primer bloque colgaba de
`#view-proyecciones` y el contenedor se llama `projectionsView`. No falla, no se
ve, y parece que el diseño está puesto. Hay un test que lo comprueba ahora.

**El check del auditor medía la cadena, no la regla.** Buscaba
`font-variant-numeric: tabular-nums` en todo el HTML, y aparece suelto en otras
partes del panel: con la regla borrada de las tablas, el check seguía en verde.
Ahora extrae los BLOQUES cuyo selector menciona `vc-t` y busca dentro. Mutado
—quitando la regla— pasa a rojo.

### Estado

**2.925 tests del motor · 592 de la capa web · 323 checks de auditoría · 105 +
62 del smoke · 16 diferenciales · preflight de Render en verde · 0 fallos,
0 avisos, 0 skips.**

## 41.44 Ronda 18 — el tema claro literal, y un panel que se moría con el CDN

**Fecha:** 2026-08-08

Kevin pidió dos temas: en **oscuro**, el suyo de siempre con los tonos de
Víctor; en **claro**, el diseño de Víctor entero. Al montarlo hubo que abrir la
página en un navegador de verdad, y ahí apareció algo bastante peor que un color.

### El fallo que solo se ve renderizando

```js
Chart.register(targetLineLabelsPlugin);   // a pelo, a nivel de módulo
```

Con el CDN de Chart.js caído eso lanza y **aborta el bloque de script entero**.
Con él se van `_vcEsc`, las 38 funciones de render del tab, `VC_SENALES` y todo
lo declarado más abajo. El resultado no es «el panel sin gráficas»: es el panel
**muerto**, con las tablas en blanco y sin un mensaje que lo explique. Medido:
**3 errores de página y todo el tab sin declarar**.

Ni un test lo veía, y por buenas razones:

- el smoke de Node **define** `window.Chart` y `window.lucide`, así que un
  `register` sin guarda le pasa por delante sin despeinarse;
- `node --check` valida la SINTAXIS, y un `ReferenceError` en ejecución es
  sintaxis perfecta.

Se guardaron los tres puntos de contacto con CDN: `Chart.register`,
`tailwind.config` y las **66** llamadas sueltas a `lucide.createIcons()`, que
pasan por un `vcIconos()` con `try`. Después: **0 errores con los tres CDN
caídos** y las nueve piezas del tab declaradas.

`tests_vertex/test_navegador.py` abre el panel en Chromium y lo comprueba.
Mutado —devolviendo el `Chart.register` sin guarda— caen 2 de 3.

### El tema claro

`html.light #projectionsView` con sus hexadecimales, no con aproximaciones. El
`#eef1f6` genérico de Vertex y su `#f3f4f6` son dos grises distintos, y sus
fondos teñidos no existen en la capa clara general. Verificado con
`getComputedStyle`, o sea el color que el navegador pinta de verdad:

| | Suyo | Medido en pantalla |
|---|---|---|
| fondo | `--bg #f3f4f6` | `rgb(243,244,246)` ✓ |
| encabezado de tabla | `--panel-2 #f8f9fb` | `rgb(248,249,251)` ✓ |
| fila inusual | `tr.unusual #fff3c9` | `rgb(255,243,201)` ✓ |
| pill de call, fondo | `--green-bg #e7f8f0` | `rgb(231,248,240)` ✓ |
| pill de call, texto | `--green-dark #0e9f5f` | `rgb(14,159,95)` ✓ |

Y lo que **no** depende del tema: `tabular-nums` y las mayúsculas de sus pills
valen en los dos, porque son estructura y no color.

### Un defecto que solo se vio mirando la captura

La regla de la gráfica oscura cogía **todos** los `canvas` del tab y pintaba un
rectángulo negro donde no había nada dibujado. Acotada a `#projChart:not(:empty)`.
Eso no lo encuentra ningún test: hay que mirar la foto.

### Estado

**2.925 tests del motor · 599 de la capa web (7 de ellos en un navegador real) ·
323 checks de auditoría · 16 diferenciales · 0 fallos, 0 avisos, 0 skips.**

## 41.45 Ronda 19 — el contraste medido, y la cuenta que desaparecía sin avisar

**Fecha:** 2026-08-08 · **Origen:** capturas del móvil de Kevin

### 1 · En claro había texto que no existía

`text-gray-100` es el VALOR de cada tarjeta de estadística —el precio, el
spread, el % de volumen sobre open interest, los empleados— y **ninguna capa
clara lo cubría**. Se quedaba en `#f3f4f6` sobre un fondo `#f3f4f6`: **1,10:1**,
o sea texto blanco sobre blanco. En las capturas se ven los rótulos («SPREAD
PROMEDIO») y debajo, nada.

Y tres niveles de gris caían todos en su `--faint` (**2,58:1**), que es lo que
hacía ilegible la letra pequeña.

**Sus colores están hechos para RELLENOS** —una pastilla, una barra, una
celda—, donde el contraste lo da el área. Como texto sobre claro no llegan, y
se mide: `--green` 2,62:1 · `--amber` 2,35:1 · `--put` 2,79:1.

Lo que se pinta ahora son **sus mismos matices bajados de luminosidad hasta
4,6:1**, no colores nuevos. Dos precisiones que cambiaron el resultado:

- **Contra su propio fondo, no contra blanco.** El tab es `#f3f4f6`; medir
  contra blanco daba 4,2:1 en pantalla, que pasa por bueno y luego no se ve.
- **Los rellenos también.** Su relleno verde `#e7f8f0` es tan claro que su
  propio `--green-dark` encima da 3,11:1. Los FONDOS de las pastillas siguen
  siendo los suyos exactos; lo que cambia es el color del texto encima.

Medido en Chromium sobre el panel servido, las 17 clases de texto del tab:
**ninguna por debajo de 4,5:1**, y el oscuro sin tocar.

### 2 · La cuenta desaparecía sin decir nada

«Creo una cuenta, cierro sesión, y al volver no me deja entrar.»

Las cuentas viajan en `Privado/privado.enc`, cifradas con `VERTEX_DB_KEY`. **Sin
esa clave no se suben**, a propósito: un hash de contraseña en un repositorio
—aunque sea privado— es un objetivo de fuerza bruta offline, y se prefiere
perderlo a filtrarlo. Esa decisión es correcta.

Lo que estaba mal es que se tomaba **en silencio**, justo en el instante en que
el usuario cree lo contrario. Medido: sin la clave, `POST /api/auth/registro`
devuelve `ok: true` sin una palabra, y al remoto solo sube `.gitignore`. Cuando
Render se duerme —el plan free borra el disco al despertar— la cuenta se va con
él y lo que recibes es «email o contraseña incorrectos».

Ahora `_aviso_persistencia()` dice **qué falta y qué va a pasar**, y viaja en
dos sitios: en la respuesta del registro (alerta inmediata) y en
`/api/auth/status`, que la pantalla de login consulta **antes** de que nadie
escriba un email.

**Lo que sí funciona, verificado borrando el disco entero:** con `VERTEX_DB_KEY`
puesta, la cuenta sobrevive a un contenedor nuevo y se entra igual. El email no
se puede repetir ni cambiando mayúsculas ni con espacios alrededor, y se entra
escribiéndolo de cualquiera de esas formas — `normaliza_email` corre en los dos
lados.

## 41.46 · Ronda 20 — la cuenta volvía del borrado; su PERFIL no

Lo destapó una tontería: la suite dejaba archivos sin versionar en el árbol.
Eran `Perfil Inversionista/usuarios/Kevin-<id>.md`, uno por corrida, y ya había
**15 commiteados por error** de rondas anteriores. Tirando del hilo apareció un
fallo de datos de verdad.

**El agujero.** El `.md` de cada usuario vive en `Perfil Inversionista/usuarios/`,
un nivel más abajo que `Kevin.md`. `_privado_paquete()` recorría el directorio
con `os.listdir`, que **no baja**. Medido: en el tar cifrado viajaban
`vertex.db` y `perfiles/Kevin.md` — nunca el perfil de la persona.

**Por qué importaba, y por qué no se veía.** Al reiniciar Render volvía la
cuenta con su capital dentro de la base… pero el archivo que
`_load_investor_profile()` lee no estaba, y esa función, al no encontrarlo,
**cae a `Kevin.md` sin decir nada**. Y `engine/wbj/specialists/risk.py` saca el
capital, el horizonte y el tope por posición de ese texto con tres regex. O sea:
el reporte salía dimensionado para los $1.000 de Kevin aunque la persona tuviera
otra cosa, y nada en el reporte lo delataba. Es exactamente el fallo silencioso
contra el que se escribió la regla del proyecto.

**Arreglado en tres capas**, porque una sola no basta:

1. **Se respalda.** El paquete cifrado lleva ahora `perfiles/usuarios/<archivo>`.
   Al restaurar se acepta esa ruta con el mismo cuidado que la otra: se rechaza
   `..`, subcarpetas y ocultos, para que un tar manipulado no escriba fuera.
2. **Se regenera.** El `.md` es **caché**; la fuente de verdad es la fila del
   usuario. Si el archivo falta, `_load_investor_profile()` lo reconstruye desde
   la base **con el mismo escritor que lo creó** (`guardar_perfil`) en vez de
   seguir de largo. Un segundo generador de markdown se habría desincronizado.
3. **No se versiona.** `Perfil Inversionista/usuarios/` va al `.gitignore` y los
   15 archivos se sacaron del repositorio. Son el capital y la tolerancia de
   personas: en `main` estaban en claro para cualquiera con acceso al repo. Su
   sitio es `Privado/privado.enc`. Los de referencia —`Kevin.md` y el de
   Víctor— siguen versionados, que para eso son la referencia.

Y la causa de la basura: `_aisla()` parcheaba `DB_PATH` pero no `_PERFIL_DIR`,
así que registrar una cuenta en un test escribía en el repositorio de verdad.
Ya apunta a `tmp_path`, con una copia del `Kevin.md` real para no cambiar el
comportamiento del respaldo.

**Comprobado que los tests nuevos fallan sin el arreglo** (3 de 5 en rojo contra
el código viejo): un test que pasa en los dos lados no prueba nada.

## 41.47 · Ronda 21 — el cuestionario que elegía por ti, y el idioma entero

**1. «Si presiono Personalizado me salen ya opciones elegidas.»** El formulario
leía `pfValor`, que cae al valor EFECTIVO —el de Kevin—, así que las once
preguntas aparecían con la respuesta de otra persona ya marcada, su capital ya
escrito y el rango 20–30 ya puesto. La etiqueta «valor heredado» al lado no
alcanzaba: lo que se ve manda sobre lo que se lee.

Peor era el gemelo, que solo apareció al probarlo en un navegador: el manejador
de las preguntas de opción múltiple también partía de lo heredado, así que el
PRIMER clic **quitaba** una opción de Kevin en vez de añadir la tuya. Pulsabas
«crecimiento» y quedaban marcadas «timing» e «ingresos».

Ahora el formulario lee `pfValorEnBlanco`: vacío hasta que contestas. Y de paso,
vaciar una casilla vuelve a ser «sin contestar» en lugar de guardarse como un
capital de cero.

**2. El idioma, completo.** Elegir inglés traducía 21 frases —la navegación y
poco más— y dejaba el resto en español. Traducir a mano cada `innerHTML` habría
sido tocar unos 900 sitios y olvidar unos cuantos, y lo que se olvida no lo
encuentra quien lo escribió: lo encuentra el usuario.

Así que la traducción no vive en los sitios que pintan. Vive en un diccionario,
y un barrido del DOM cambia lo que reconoce; un `MutationObserver` lo re-aplica
en cada render, venga de donde venga. Tres consecuencias:

- **También traduce lo que manda el servidor.** El cuestionario del perfil llega
  en español desde `/api/perfil` y sale en inglés en pantalla, sin que el
  servidor sepa que hay un segundo idioma.
- **Los datos no corren peligro.** Un ticker o un nombre nunca casan con una
  frase española entera, así que el barrido no los toca.
- **Lo que falte se mide, no se supone.** El test abre la pantalla en un
  navegador real, la recorre entera y **falla si queda español**.

Las claves salen del DOM real, no del archivo: el navegador ya resolvió
entidades y plantillas, y una clave escrita a ojo no casa con nada — sin error,
sin aviso, solo media pantalla en el idioma equivocado.

Lo que ningún diccionario alcanza es la prosa que escribe el modelo, que no
existe hasta que se pide. Para eso el idioma viaja en una cabecera
(`X-Vertex-Idioma`) desde un envoltorio de `fetch` puesto arriba del todo, y
llega al prompt por `_instruccion_idioma()`. Los tres prompts que decían
«Respondes SIEMPRE en espanol» ya no clavan el idioma.

**Lo que encontraron los tests nuevos**, que es la parte que importa:
una clave con espacio doble (venía de un `&#160;`) que no habría casado nunca;
una frase con «57 días» escrita como entrada fija cuando el número es variable;
y la guarda de «ya está hecho», que aplicada también a la vuelta dejaba media
pantalla en inglés al volver a español.

Medido al final: 436 nodos en el armazón y 599 con los paneles cargados de
datos, **0 en español**; el interruptor va y vuelve sin dejar restos; y la
cabecera llega en las 8 peticiones de arranque.

## 41.48 · Ronda 22 — el rango que no decía nada, y el ticker que se quedaba pegado

**1. La pregunta del rango por posición.** Salían dos casillas vacías con una
raya en medio y nada explicaba qué iba dentro. Los dos extremos no son
decorativos y el motor los usa distinto: el **máximo** es el tope —pasarse
cuenta como infracción (`within_position_cap`)— y el **mínimo** es el suelo de
despliegue —quedarse por debajo se reporta como información, no como falta
(`below_intended_sizing`)—. El servidor ya validaba `0 < mín ≤ máx ≤ 100`.

Ahora cada casilla lleva su rótulo (mín / máx), un ejemplo dentro, y debajo el
rango se traduce a dinero sobre TU capital: «Cada posición irá entre el 30% y el
70% de tu capital: $300 – $700 de $1,000». El porcentaje se entiende; el dinero
se siente. El capital que se usa es el que declaraste tú — nunca el de Kevin,
porque enseñar la cifra de otro justo en la casilla donde decides cuánto
arriesgar es peor que no enseñar ninguna.

**2. El dato viejo que se quedaba en pantalla.** Dos sitios:

- **Al volver al tab.** Analizabas WULF, te ibas al Dashboard y al regresar
  seguía todo puesto: cabecera, escenarios, niveles y cinta de un momento que ya
  pasó. Un precio de hace media hora no se distingue en pantalla de uno de hace
  un segundo. Ahora salir de Proyecciones borra el análisis y se vuelve a la
  pantalla vacía.
- **Al pedir otro ticker.** La cabecera de arriba a la derecha (`projHbRight`)
  vive FUERA de `projContent`, así que esconder el contenido nunca la tocaba:
  mientras cargaba NVDA se leían el nombre y el precio de WULF, como si el
  cargador estuviera trabajando sobre esa.

Los dos comparten `vcLimpiaTicker()`, que corre ANTES de abrir pestaña, de pedir
la cinta nueva y de pintar el cargador — el orden importa: más tarde borraba la
cinta que se acababa de pedir. Ideas y Wheel no se tocan: escanean el mercado
entero, no dependen del símbolo, y rehacer ese escaneo cuesta llamadas de
verdad.

## 41.49 · Ronda 23 — el suelo de cobertura, y el idioma que ya no depende de acordarse

**1. `test_a_thin_peer_panel_is_refused_rather_than_averaged`.** Fallaba en
`main` desde otra línea de trabajo. No era el suelo del 70 %, que sigue donde
estaba: era el DENOMINADOR. Cuando FIN-GR-004 pasó a reportar `NOT_APPLICABLE`
en vez de `NOT_SCORABLE` para un emisor que no publica el puente de crecimiento
orgánico —«no tener el problema no puede costar cobertura», y es la decisión
correcta— esa métrica salió del denominador. La dimensión pasó a medirse sobre
las que aplican, no sobre las cinco.

Con eso, la aritmética cambió y la vieja aserción («3 de 5 no puede puntuar»)
dejó de describir nada. Lo honesto no era borrarla: era medir las dos formas.

- **El expediente habitual** — sin puente y sin serie de participación. Aplican
  tres; una muestra fina mata la de comparables; 2 de 3 es 67 % y la dimensión
  se apaga. Es el caso que protege a casi todos los tickers, y sigue protegido.
- **Con la serie que suministra un analista.** Aplican cuatro, tres son válidas,
  75 % supera el suelo y la dimensión puntúa — sobre crecimiento, durabilidad y
  una participación real, con la comparación de pares rechazada y dicha en voz
  alta. Eso es la cobertura haciendo su trabajo, no esquivándolo.

**2. El idioma deja de depender de la memoria.** Quedaban dos agujeros:

- **La prosa del modelo.** Yo había cableado el idioma en tres prompts. Había
  **ocho**. Ahora ninguna petición habla con un proveedor por su cuenta: todas
  pasan por `_gemini_genera` / `_claude_crea`, que le pegan la instrucción al
  final —lo último que lee el modelo gana sobre el «en español» que trae escrito
  la descripción de un campo del esquema—. Un test cuenta las llamadas crudas y
  falla si aparece una novena.
- **El texto nuevo.** El barrido del navegador solo ve lo que se pintó: una
  frase que solo sale cuando una fuente falla, o en la pestaña que ese test no
  abrió, se le escapaba entera. El guardián nuevo lee **el archivo**, saca cada
  cadena que puede acabar en pantalla y falla si alguna no tiene traducción.

Ese segundo guardián cazó **83** cadenas, entre ellas las descripciones de las
cuatro pestañas de Proyecciones, «Puntaje», «Vencimiento», «Agresividad»,
«Tolerancia», el aviso de baja liquidez y «tu contraseña nunca pasa por aquí».
Ninguna se veía en el barrido de pantalla porque ninguna se pinta sin datos.

Afinarlo costó dos vueltas, y las dos merecen quedar escritas porque son el
mismo error por los dos lados:

- El detector solo miraba acentos y palabras gramaticales, así que **las
  etiquetas cortas se le escapaban enteras** — «Cobertura», «Estructura · GEX ·
  niveles», «Puntaje» no tienen ni una. Se le añadió la morfología del español
  (`-ción`, `-idad`, `-miento`, `-ura`, `-ancia`…), que es lo único que delata a
  un sustantivo suelto.
- Y ampliarlo de más lo volvió ruidoso: `-arios` marcaba «scenarios», `-ales`
  marcaba «sales», y dos «no» dentro de una frase **inglesa** la daban por
  española. Podado. Un guardián que grita con lo que está bien deja de servir
  para lo que está mal, porque se acaba desactivando.

Al cierre: **998 entradas y 27 patrones**; 591 nodos con los paneles cargados de
datos y **0 en español**.

**Lo que NO cambia de idioma, y por qué.** Los `RESUMEN.md` que se guardan en
`Reportes/` y `Proyecciones/` siguen en español pase lo que pase. No se sirven
al panel —no son pantalla, son archivo— y los lee el agente como memoria. El
archivo es además COMPARTIDO: si cada quien lo escribiera en su idioma, la
carpeta de un ticker acabaría mezclando los dos y ninguna búsqueda posterior
encontraría la mitad. Un solo idioma en el archivo, el de la pantalla en la
pantalla.

## 41.50 · Ronda 24 — el respaldo atascado, el vaivén que congelaba, y un rótulo que mentía

**1. El respaldo llevaba parado desde el 13 de agosto.** El panel decía
«Respaldo con errores · 18 archivos sin subir», y decía la verdad:

```
git commit: error: Committing is not possible because you have unmerged files.
fatal: Exiting because of an unresolved conflict.
```

La causa: cuando el `push` se rechaza porque otro proceso escribió antes, el
código traía lo del remoto y hacía `rebase` **con el error tolerado**. Un rebase
que choca no deja las cosas como estaban: deja archivos sin fusionar. Desde ese
momento **todo** commit muere, y como nadie entra a ese directorio a mano, se
queda así para siempre. Un fallo de un ciclo se convertía en el fallo de todos
los siguientes.

Ahora: `_sanea()` corre al principio de cada ciclo y deshace lo que haya quedado
a medias (rebase, merge o cherry-pick), el rebase lleva `-X theirs` —dentro de un
rebase «theirs» son los commits que se reaplican, o sea los nuestros— y si aun
así falla, se aborta y se junta por `merge -X ours`. En los choques manda el
disco: es el dato que el agente acaba de escribir.

El test reproduce el atasco con dos ramas que tocan la misma línea —no con
`rebase HEAD~n`, que según la versión de git se resuelve solo y entonces el test
no vigila nada— y comprueba que el archivo nuevo **llega al remoto**.

**2. Cambiar de idioma y volver dejaba 131 cadenas en español.** Con las tablas
cargadas, ir y volver una vez bastaba. No era lentitud —20 cambios seguidos van
a 6 ms de media y no se degradan— sino que la traducción se apagaba.

La guarda de «esto ya lo escribimos nosotros» no llevaba el idioma dentro. Al
restaurar se guardaba el español como «lo último que pusimos», y al volver a
inglés la comparación daba igual y el nodo se saltaba la traducción. «Lo que
pusimos» y «en qué idioma lo pusimos» son dos cosas; compararlas como una sola
congela el nodo en el idioma en que se tocó por última vez. La marca lleva ahora
el idioma pegado.

**3. «Capital máximo por trade» enseñaba el riesgo.** Con un perfil de $10.000 y
un tope por posición del 10–70 %, la tarjeta decía **$3.000** — que es el 30 % de
riesgo por operación, el mismo número que ya salía en la tarjeta de al lado.
Aquel dice cuánto puedes PERDER; este, cuánto puedes DESPLEGAR. Ahora hay dos
tarjetas y cada una dice lo suyo: «Pérdida máxima por trade $3.000» y «Capital
por posición (10–70 %) $1.000 – $7.000».

**Y una lección de la propia auditoría, cobrada en directo:** el arreglo anterior
metía un comentario HTML con acentos graves DENTRO de una plantilla de
JavaScript. Uno solo la cierra, y el bloque entero dejó de definirse —
`vcRiesgoHTML` y `renderProjIdeas` pasaron a `undefined` y el tab quedó muerto.
Es exactamente lo que vigila el check «ningún comentario HTML dentro del JS lleva
acentos graves», escrito la primera vez que pasó.

## 41.52 · Ronda 27 — «¿siempre que presiono deploy se borra?»

Sí. Render en plan gratuito borra el disco en **cada** despliegue, y un archivo
de base de datos no lo arregla porque el archivo vive en ese mismo disco: un
`.db` se borra igual que un `.json`. Para eso está el almacén — el deploy borra,
el contenedor nuevo clona la rama `datos` y todo vuelve. Doce casos lo prueban
borrando el disco a propósito.

Pero quedaba un agujero, y es el que explica que a Kevin se le perdieran cosas
incluso con el respaldo funcionando: **el hilo de fondo sincroniza cada 20 s, y
nada forzaba un respaldo al hacer lo que importa**. Una cuenta creada quince
segundos antes de pulsar «Deploy» no llegaba a subir nunca, y al volver «no
existía». Lo mismo con el perfil y con los reportes.

Ahora lo que la persona acaba de hacer se sube **antes de que la respuesta
llegue a su pantalla**: `_respalda_ya()` en el registro y en el guardado del
perfil, y `sincroniza()` al archivar un reporte. Un análisis tarda un minuto
largo en producirse; esperar un segundo más a que quede guardado no se nota, y
perderlo sí.

El test lo mide sin esperar nada: registra una cuenta y exige que el commit ya
esté hecho; archiva un reporte y lo busca **en el remoto**, no solo en el commit
local.

Y un test viejo que dejó de valer al hacerlo: `test_sincronizar_a_peticion`
exigía `ultimo_error is None`, y desde que archivar sincroniza en el acto llega
siempre el aviso de que sin `VERTEX_DB_KEY` no se respaldan cuentas ni perfiles.
No es un fallo de la sincronización — es la condición de siempre en un entorno
sin clave — así que el caso lo acepta nombrándolo, en vez de exigir un silencio
que ya no existe.

## 41.51 · Ronda 25 — el camino de vuelta: inglés → español

El panel está escrito en **dos** idiomas: Proyecciones y el perfil en español,
Analyze / Explore / Portfolio en inglés. Con un solo diccionario, elegir español
dejaba esas áreas en inglés — la mitad de la aplicación.

Lo que cambió no es solo un segundo diccionario: es que el barrido **dejó de ser
asimétrico**. Antes tenía dos ramas —«traducir» si el idioma era inglés,
«deshacer» si no— y solo una miraba el diccionario, así que el texto nacido en
inglés no tenía forma de volverse español. Ahora se parte SIEMPRE del original,
esté en el idioma que esté, y se escribe su versión en el idioma de ahora; si no
hay traducción se escribe el original, que es a la vez traducir y deshacer según
de dónde se venga.

`VX_EN2ES` y `VX_PAT_ES` son el camino de vuelta. Lo que ya está en el idioma de
destino no casa con nada y se queda igual — que es exactamente lo que se quiere.

Un detalle que costó un fallo: el escáner de código lee los diccionarios como si
fueran texto de pantalla. El de vuelta lleva **español en los valores**, así que
gritaba con las traducciones mismas. Se excluye, igual que ya se excluía el otro.

## 41.52 · Ronda 26 — la última red del respaldo, y un aviso que ahora dice la consecuencia

Kevin preguntó dos cosas que parecían distintas y eran **una sola**: por qué las
cuatro series de memoria marcaban 0 (IV, flows, predicciones, cadenas) y por qué
a veces su cuenta «no existía» y sus reportes desaparecían.

La cadena entera: el respaldo llevaba atascado desde el 13 de agosto (41.50) →
la rama `datos` se quedó congelada en esa foto → Render no tiene disco
persistente, así que cada reinicio **clona esa foto** → la cuenta creada después
no está en ella y los reportes tampoco → y las series, que se acumulan una foto
por día de mercado, volvían a cero antes de poder acumular nada. Los cuatro ⚠ no
eran un fallo del agente: eran el síntoma de que nada persistía.

Lo de 41.50 arregla la causa. Aquí van las dos cosas que hacen que no vuelva:

**1. Una última red.** `_sanea()` cubre lo previsible. Si tras sanearlo el commit
SIGUE muriendo, el clon está roto de una forma que no se previó, y antes eso era
quedarse atascado para siempre. Ahora `_reconstruye()` aparta lo que hay en
disco, clona de cero y lo devuelve encima: **el clon se puede rehacer, los datos
no**. El test rompe el `.git` a propósito y comprueba que los archivos sin subir
sobreviven Y llegan al remoto.

**2. El aviso deja de ser críptico.** «Respaldo con errores» era verdad y no
servía: se vio en pantalla durante semanas y nadie supo que significaba «tus
cuentas y tus reportes desaparecen en el próximo reinicio». Cuando hay archivos
esperando, la insignia se pone **roja** y dice la consecuencia —«NO se está
guardando · N en riesgo»—, porque un estado en el que se pierden datos no puede
verse igual que uno del que se sale solo.

Lo que **no** se recupera: las series empiezan de cero desde ahora. El IV Rank
real necesita 60 sesiones de mercado y no hay forma de comprarlas hechas. Eso no
es trabajo pendiente, es tiempo que hay que dejar correr — lo que se arregló es
que dejen de volver a cero en cada reinicio.

### Estado

**2.925 tests del motor · 658 de la capa web (28 en un navegador real) ·
323 checks de auditoría · 16 diferenciales · preflight de Render en verde ·
0 fallos, 0 avisos, 0 skips.**
