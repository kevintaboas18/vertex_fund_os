# Auditoría del port de `store.ts`

Tres pasadas, atacando el port en vez de releer el diff. Nueve hallazgos: siete
arreglados, uno compartido con el original que se deja como está, y uno que
resultó ser un artefacto del contenedor.

- **1ª pasada** (contrato, datos corruptos, degradación) → secciones 1 a 4.
- **2ª pasada** (concurrencia, tickers degenerados, portabilidad) → 5 a 8.
- **3ª pasada** (los arreglos de las dos anteriores) → sección 9.

## 1 · El orden dependía de la zona horaria del servidor — ARREGLADO

`_ts_key` usaba `datetime.timestamp()`, que interpreta un timestamp **sin zona**
en la hora local de la máquina. El mismo archivo se ordenaba distinto según
dónde corriera:

    TZ=UTC              → [2, 1]
    TZ=America/New_York → [1, 2]     ← invertido
    TZ=Asia/Tokyo       → [2, 1]

Y el orden no es cosmético: decide qué trades se caen por el tope de
`MAX_PER_TICKER`. Dos despliegues con TZ distinta acababan con memorias
distintas a partir del mismo tape.

Arreglo: un timestamp sin zona se lee como UTC. Es una **divergencia declarada**
frente a `Date.parse`, que también usa hora local — pero la persistencia no
puede depender de la TZ de la máquina, y es el criterio que ya usa
`flow._epoch`. Invisible cuando el timestamp trae `Z`, que es el caso normal.

## 2 · Una fila corrupta apagaba TODA la memoria en silencio — ARREGLADO

El peor de los cuatro, porque no daba error. `load_trades` devolvía la lista tal
cual, y una fila que no fuera dict hacía reventar el primer `.get()` del
llamador. Ese `except Exception` de `_tito_memory` lo convertía en "no hay
memoria", y de golpe:

- el IV Rank real volvía al proxy de volatilidad realizada,
- el sub-agente 6 salía `None`,
- la auto-calibración no arrancaba nunca.

El endpoint seguía devolviendo 200 con un score más bonito y menos evidencia
detrás. En TypeScript no pasa: una fila mala se lee como `undefined` y el
pipeline sigue. En Python cada `.get()` sobre ella lanza.

Arreglo doble:
1. `load_trades` descarta lo que no sea dict — simétrico con `save_trades`, que
   ya lo hacía. La memoria conserva las filas buenas.
2. `stats.motivo` dice **siempre** por qué se apagó la memoria. Degradar en
   silencio es peor que fallar.

## 3 · `flows_guardados` mentía cuando el tape cambiaba de esquema — ARREGLADO

Contaba la lista ya filtrada por `asset_price > 0`. Si MarketSnack dejaba de
mandar ese campo, el archivo seguía creciendo hasta 5000 mientras el contador
decía **0** — que se lee como "el disco no funciona", justo el diagnóstico
contrario al real.

Ahora son tres números —`flows_guardados`, `flows_utilizables`,
`flows_descartados`— y `/api/tito-health` levanta un check propio
(`memoria.flows.formato`) que nombra el esquema como culpable.

## 4 · Trades sin `id` se funden en uno — GUARDA AÑADIDA

`flow._base_row` hace `int(_num(raw.get("id")))`, así que un tape sin ese campo
devuelve **0 para todos** los trades, y un dedupe por id conserva uno solo:
4999 de 5000 desaparecen sin un error.

Víctor tiene exactamente lo mismo (`id: raw.id`, sin guarda) — no es un fallo
del port. Pero la consecuencia es pérdida silenciosa de datos, así que
`_dedupe_key` cae a una clave compuesta cuando el id es falsy. **Con id real el
comportamiento es idéntico al suyo.**

## 5 · Dos peticiones a la vez perdían el 75% de la memoria — ARREGLADO

El más grave de los ocho. `save_trades` hace leer-fusionar-escribir sin cerrojo:
dos peticiones al mismo ticker leen el mismo archivo y la segunda escribe encima
de lo que acumuló la primera. Medido con 8 hilos × 50 trades:

    antes:  100 / 400        ← se perdía el 75%
    ahora:  400 / 400

Y es un caso normal, no raro: el panel se auto-refresca mientras el usuario
consulta, y Render corre varios workers.

Los **otros tres stores tenían la misma carrera** — dejar uno solo arreglado
habría sido arbitrario. `save_prediction` era el peor de los tres porque se
llama una vez **por horizonte**, así que dos peticiones simultáneas dejaban el
diario con huecos y la calibración tardaba más en encenderse.

Arreglo: `_exclusive(path)`, un `threading.Lock` por archivo (peticiones del
mismo worker) más `flock` sobre un `.lock` aparte (workers hermanos). El cerrojo
va en un `.lock` y no en el JSON porque la escritura atómica reemplaza el inodo
del JSON en cada guardado. Coste medido: **0.3 ms por guardado**.

Es **divergencia declarada**: Víctor tiene la misma carrera —sus `await` entre
`loadTrades` y `writeFile` dejan el mismo hueco— pero perder el 75% de lo que
este archivo existe para acumular no es aceptable.

## 6 · Tickers basura compartían un archivo sin dueño — ARREGLADO

`"!!!"`, `"@@@"`, `""` y `"   "` sanean todos a la cadena vacía, así que
`fileFor` los mandaba al **mismo** `.json`: la memoria de una consulta basura
contaminaba la siguiente. Ahora `_file_for` lanza `ValueError`. Leer nunca
lanza (un ticker sin nombre tampoco tiene historial); guardar sí, porque ahí hay
algo que se perdería en silencio. El endpoint lo absorbe y lo reporta en
`stats.motivo`.

## 7 · Leer dejaba carpetas escritas — ARREGLADO

`_file_for` y `_path` hacían `mkdir` en la ruta de **lectura**: preguntar "¿hay
historial?" creaba `data/tito/trades/`. El que escribe ya las crea.

## 8 · `fcntl` habría roto el arranque en Windows — ARREGLADO

El cerrojo del punto 5 trajo `import fcntl`, que es solo POSIX. En Windows —donde
se corre el preflight local— habría tumbado el módulo entero y con él
`vertex_api`. Ahora el import es opcional; sin `fcntl` queda el cerrojo de hilos,
que cubre el caso de un solo proceso. Verificado simulando el entorno:
400/400 sin `flock` y sin crear `.lock`.

## 9 · El cerrojo del punto 5 podía colgar un worker — ARREGLADO

Bug **introducido por mi propio arreglo**, que es justo lo que buscaba la 3ª
pasada. Entrar dos veces a `_exclusive` sobre la misma ruta desde el mismo hilo
colgaba el proceso **para siempre**: `threading.Lock` no es reentrante, y
`flock` tampoco lo es entre dos descriptores del mismo proceso, así que ni
cambiándolo por un `RLock` se arreglaba.

Hoy ningún camino anida, así que era latente — pero el modo de fallo es el peor
que hay: sin error, sin timeout, el worker simplemente deja de responder. Y la
trampa quedaba armada para el próximo que llamara a un `save_*` desde dentro de
otro.

Arreglo: un `threading.local()` con las rutas que este hilo ya tiene tomadas; si
ya la tiene, se pasa de largo (la exclusividad la da el de fuera). Verificado
que la reentrada **no** aflojó la exclusividad real: 6 hilos, cero solapes.

## No arreglado a propósito

`BRK/B` y `BRKB` caen en el mismo archivo, porque el saneado borra la barra.
Es el comportamiento de Víctor, y las formas reales de esos tickers usan punto
(`BRK.B`) o guion (`BRK-B`), que sobreviven al saneado. Cambiar el nombrado de
archivos por un caso que no ocurre no compensa.

## Hallazgo inválido

"`save_trades` no falla en disco de solo lectura": el contenedor de auditoría
corre como **root**, que salta los bits de permiso, así que la prueba con
`chmod` no prueba nada. No es un resultado.

## Lo que se comprobó y estaba bien

- Escritura atómica: matar el proceso a media escritura deja el archivo anterior
  intacto y sin `.tmp` huérfanos.
- Archivo corrupto (JSON roto, `null`, vacío, lista pelada del formato viejo,
  `trades` que no es lista) → `None`, y el siguiente guardado lo repara.
- `flags` y `scores` sobreviven al viaje por JSON como dicts; lo guardado
  reconstruye un `FlowLite` sin tocar nada.
- Coste real por petición con el archivo lleno: 217 ms de guardado + 30 ms de
  lectura + 95 ms del backtest de 5000 flows × 200 barras.
- 5000 trades con el análisis completo ocupan 3.74 MB por ticker.
- Acumulación real: 5 sesiones seguidas → 3, 6, 9, 12, 15. Re-consultar el mismo
  día no infla el total.
- El "análisis más reciente gana" de Víctor repara solo: una corrida con tape
  sano vuelve a poner en pie los trades que se guardaron degradados.
- `/api/tito-health` no filtra credenciales.
- Un lector concurrente **nunca** ve el archivo a medias, ni durante 15
  reescrituras seguidas de un archivo de 2000 trades.
- El `.lock` no se lee como historial ni deja `.tmp` huérfanos.
- Los 4 stores bajo 8 hilos: ivStore 8/8 días, chainStore 8/8, predictionStore
  24/24 (8 días × 3 horizontes).
- **8 peticiones HTTP simultáneas** al endpoint real: ninguna se cuelga, ninguna
  falla, cero trades duplicados. Es la prueba de que está conectado, no solo de
  que el store funciona aislado.
- Arranque en frío con el disco vacío: el motor responde igual y declara la
  memoria como disponible-pero-vacía, no como rota.
- Contención real: 10 escrituras concurrentes sobre un archivo de 4000 trades →
  1.7 s en total, las 10 aplicadas (4010/4010).
- El `.lock` convive con el `.json` sin pisarlo en ningún ticker real
  (`NVDA`, `BRK.B`, `BRK-B`), y no se lee como historial.
- Ningún ticker legítimo cae en la guarda del punto 6 (`BRK.B`, `BRK-B`, `0DTE`,
  `A.B_C-D` pasan).
- Los invariantes de Víctor siguen intactos tras los siete arreglos: orden
  descendente, `added`, `first_seen`, "el más reciente gana", envoltorio,
  análisis completo y `None` sin historial.
- `_LOCKS` acotado: un cerrojo por archivo, 505 entradas tras 500 tickers.

## Correspondencia con el original

11/11 con `web/lib/store.ts`: `MAX_PER_TICKER`, saneado del ticker, `trim` +
`toUpperCase`, dedupe con la última ganando, `added` solo para las nuevas, orden
descendente, recorte, `firstSeen` = el más viejo, envoltorio
`{ticker, updatedAt, trades}`, validación de `Array.isArray` y `null` cuando no
hay historial.

Las tres divergencias van declaradas en el código, cada una junto a lo que
protege.
