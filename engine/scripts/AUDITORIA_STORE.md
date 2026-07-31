# Auditoría del port de `store.ts`

Cuatro problemas encontrados atacando el port, no releyendo el diff. Tres eran
míos; uno es compartido con el original y se deja como está.

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

## Correspondencia con el original

11/11 con `web/lib/store.ts`: `MAX_PER_TICKER`, saneado del ticker, `trim` +
`toUpperCase`, dedupe con la última ganando, `added` solo para las nuevas, orden
descendente, recorte, `firstSeen` = el más viejo, envoltorio
`{ticker, updatedAt, trades}`, validación de `Array.isArray` y `null` cuando no
hay historial.

Las tres divergencias van declaradas en el código, cada una junto a lo que
protege.
