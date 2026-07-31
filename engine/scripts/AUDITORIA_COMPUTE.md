# Auditoría del port de `compute.ts`

Atacando el port contra el original ejecutado en Node, no releyendo el diff.
Cinco hallazgos, todos arreglados. Las 35 sentencias ejecutables de
`compute.ts` están mapeadas una a una (tabla al final).

## 1 · El nocional daba por hecho 100 acciones por contrato — ARREGLADO

El que motivó el port. `fetch_option_chain` calculaba
`notional_value = OI × 100 × strike` con el 100 escrito a mano. Víctor lo lee
del contrato (`details.shares_per_contract`), porque los **contratos ajustados**
por split o dividendo especial traen otro número.

El nocional es la entrada principal del sub-agente 4:

    shares_per_contract=100 → $900,000,000 · Estructura 3/15
    shares_per_contract= 10 →  $90,000,000 · Estructura 1/15   (antes: 3/15)

Una cadena ajustada puntuaba como si moviera diez veces más dinero.

## 2 · Apliqué la regla de tipos estricta donde Víctor usa la laxa — ARREGLADO

El hallazgo más fino, y solo sale ejecutando el original. `compute.ts` usa
**dos** reglas distintas y a propósito:

| campo | regla de Víctor | por qué |
|---|---|---|
| `price` | `typeof x === "number"` (estricta) | un precio de tipo raro daría un Open Premium equivocado; mejor caer al siguiente de la cascada |
| `open_interest`, `strike_price`, `volume`, `shares_per_contract` | `?? fallback` (laxa) | luego la aritmética de JS convierte, así que `"500"` sale bien |

Yo apliqué la estricta a los cinco. Medido contra Node:

    OI como texto  → Víctor: nocional 5,000,000 · port: 0
    strike texto   → Víctor: nocional   205,000 · port: 0 (¡y la fila se descarta!)

O sea: si Massive pasa a mandar los números como texto, Víctor sigue calculando
y este port se llenaba de ceros **sin un solo error** — la cadena entera con
OI 0, Estructura y GEX a cero, y un scorecard igual de bonito con nada detrás.
Es exactamente el mismo fallo que apareció en `store.ts` con el esquema del
tape. Con el strike en texto era peor todavía: la guarda `strike <= 0` de
`fetch_option_chain` descartaba **todas** las filas y la cadena salía vacía.

Ahora `_coerce` reproduce `??` + la coacción de JS, y `_num` (estricta) se queda
solo donde Víctor la usa: el precio.

## 3 · La fila se contradecía a sí misma — ARREGLADO

`open_interest=int(oi)` para el campo pero `notional_value(oi, …)` con el float
crudo: una fila decía OI 60 con un nocional calculado sobre 60.5. Ahora el mismo
valor alimenta el campo y las dos fórmulas.

## 4 · Perdí la canonización del vencimiento — ARREGLADO

Al mover la conversión a `to_row` se me cayó el `[:10]` que tenía el código
anterior. La cadena de vencimiento no es solo una etiqueta: es la **clave** con
la que agrupan el sub-agente 4 y el heatmap de GEX. Sin canonizar,
`"2026-09-18"` y `"2026-09-18T00:00:00Z"` son dos vencimientos distintos —
grupos partidos en Estructura y un eje del heatmap con timestamps.

## 5 · `massive.py` hacía la conversión por su cuenta — ARREGLADO

La conversión estaba enterrada dentro del cliente HTTP, que es justo donde no se
puede probar sin red — y es donde se había colado el hallazgo 1. Ahora la
arquitectura es la de Víctor: `massive.py` trae páginas, `compute.py` convierte.

## Divergencias declaradas

1. **`_coerce` con basura no numérica** cae al fallback en vez de dar `NaN`. En
   JS `"abc" * 100` es `NaN` y envenena el nocional en silencio; el fallback
   además enciende la salvaguarda de baja liquidez del sub-agente 4.
2. **El open interest se trunca a entero.** Es un conteo de contratos; Víctor lo
   arrastra decimal solo porque JS no distingue. Con datos reales no cambia nada.
3. **El vencimiento se recorta a `YYYY-MM-DD`** (hallazgo 4).
4. **`fetch_option_chain` descarta strike 0 y vencimiento vacío.** Víctor no
   filtra porque su destino es una tabla, donde una fila rara solo se ve fea;
   aquí el destino son GEX, niveles y Estructura, donde un strike 0 mete un nodo
   imán en cero y un vencimiento vacío crea un grupo fantasma.

## Comprobado contra Node, idéntico

| caso | Víctor | port |
|---|---|---|
| precio `NaN` → cae a `day.close` | `{2, day_close}` | ✓ |
| precio booleano → cae a `day.close` | `{2, day_close}` | ✓ |
| precio en texto → cae a `day.close` | `{2, day_close}` | ✓ |
| precio 0 o negativo → siguiente de la cascada | ✓ | ✓ |
| OI en texto | 5,000,000 | ✓ |
| strike en texto | 205,000 | ✓ |
| `shares_per_contract: 0` | 0 | ✓ |
| `shares_per_contract: null` → 100 | 100,000 | ✓ |

## Conectado

- **Cambio de esquema a texto de punta a punta**: el endpoint devuelve el mismo
  `structure` y el mismo `score` con números y con texto. Antes: cero.
- **Fechas con hora**: un solo vencimiento, etiqueta limpia.
- **Ajustados mezclados con normales**: cada contrato usa su multiplicador.
- **Cadena sin precios** (el plan de Massive no da quotes): `price_source` lo
  declara, `open_premium` es `None` —no 0—, y el nocional no depende del precio.

## Correspondencia con el original

**35/35 sentencias ejecutables** mapeadas: `contractPrice` con su cascada de
tres niveles y los cuatro `PriceSource`, `openPremium`, `notionalValue` con
`sharesPerContract`, `normalizeType`, `toRow` con sus diez campos,
`sortByOpenInterestDesc` (copia, no muta) y `countExpirations` (sin las vacías).

Tests: los 12 casos de `compute.test.ts` + 16 que el original no cubre.
