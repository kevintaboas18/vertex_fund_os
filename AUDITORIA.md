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
