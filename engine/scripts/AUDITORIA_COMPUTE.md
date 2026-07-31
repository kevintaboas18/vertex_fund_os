# Auditoría del port de `compute.ts`

Tres pasadas, atacando el port contra el original ejecutado en Node. Diez
hallazgos, todos arreglados. Las 35 sentencias ejecutables de `compute.ts` están
mapeadas una a una (tabla al final).

- **1ª pasada** (fórmulas y reglas de tipo) → secciones 1 a 5.
- **2ª pasada** (lo que añadió la 1ª + resiliencia) → 6 a 8.
- **3ª pasada** (test diferencial de 604 casos) → 9 y 10.

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

## 6 · El tipo de contrato dependía del `case` — ARREGLADO

El más caro de los ocho, porque no avisa: **miente**. Víctor compara
`t === "put"` exacto, así que un `"PUT"` de la fuente se convierte en **call**.
Mi código anterior al port hacía `.lower()`; al mover la conversión a `to_row`
se perdió. Medido con la cadena entera en mayúsculas:

    "put"  →  GEX total  -13,614,827  ·  régimen negative
    "PUT"  →  GEX total  +27,229,653  ·  régimen positive

Todos los puts pasan a contarse como calls: el GEX neto **cambia de signo**, el
put wall desaparece y el régimen se invierte. La señal central del motor entero
decidida por cómo capitaliza un string la fuente de datos.

## 7 · `_coerce` reventaba con un `"NaN"` — ARREGLADO

Bug del arreglo de la 1ª pasada. Mi docstring prometía que la basura no numérica
cae al fallback, pero `float("NaN")`, `float("inf")` y `float("-Infinity")`
**parsean**. Un `open_interest: "NaN"` llegaba entero hasta `int(oi)`, que lanza
`ValueError` — dentro del bucle de `fetch_option_chain`, o sea tumbando la
cadena completa. Ahora se filtra por `math.isfinite`.

## 8 · Una fila malformada se llevaba la página entera — ARREGLADO

El `?.` de Víctor sobrevive a que `details` sea un string o un número (devuelve
`undefined`). Mi `raw.get("details") or {}` solo cubre `None`, así que un
`details: "texto"` lanzaba `AttributeError`. Como `to_row` corre dentro del
bucle de descarga, **un solo contrato malformado entre 5000 dejaba la cadena
vacía** y el panel decía "sin cadena para X".

`_obj()` reproduce ahora la semántica de `?.` en `raw`, `details`, `day` y
`last_trade`. Verificado con una página que mezcla contratos buenos con
`details: "texto"`, `open_interest: "NaN"`, `None` y `"x"`: los buenos pasan,
los malos se descartan, la cadena sobrevive.

## 9 · `shares_per_contract` ilegible fabricaba el multiplicador — ARREGLADO

Lo encontró el diferencial. `_coerce(shares, 100)` no distinguía **ausente** de
**presente pero ilegible**: con `shares_per_contract: "abc"`, `[]` o `""` caía al
100 y calculaba un nocional como si fuera un contrato estándar.

Es el bug del hallazgo 1 reintroducido por la puerta de atrás: **inventar el
multiplicador justo donde no hay evidencia de cuál es**. Víctor en ese caso
acaba con `NaN`; el port daba $900.000 de nocional sobre un contrato del que no
se sabe nada.

Ahora `_shares()` separa los dos casos: ausente o `null` → 100 (el `?? 100` de
Víctor); presente e ilegible → 0, que además enciende la salvaguarda de baja
liquidez en vez de fabricar un número.

## 10 · Los booleanos: las dos reglas los tratan al revés — ARREGLADO

Otro que solo sale del diferencial. En JS `typeof true === "boolean"` los
**rechaza** en la regla estricta del precio, pero `true * 5 === 5` los
**convierte** en la aritmética. No es un descuido de Víctor: son sus dos reglas
haciendo cosas distintas con el mismo valor. Mi `_coerce` los rechazaba en las
dos.

## Divergencias declaradas

1. **`_coerce` con basura no numérica** cae al fallback en vez de dar `NaN`. En
   JS `"abc" * 100` es `NaN` y envenena el nocional en silencio; el fallback
   además enciende la salvaguarda de baja liquidez del sub-agente 4.
2. **El tipo de contrato se compara en minúsculas** (hallazgo 6).
3. **`_obj()` tolera que `details`/`day`/`last_trade`/el contrato no sean
   objetos** (hallazgo 8). Es la semántica de `?.`, que en Python no existe.
4. **El open interest se trunca a entero.** Es un conteo de contratos; Víctor lo
   arrastra decimal solo porque JS no distingue. Con datos reales no cambia nada.
5. **El vencimiento se recorta a `YYYY-MM-DD`** (hallazgo 4).
6. **`fetch_option_chain` descarta strike 0 y vencimiento vacío.** Víctor no
   filtra porque su destino es una tabla, donde una fila rara solo se ve fea;
   aquí el destino son GEX, niveles y Estructura, donde un strike 0 mete un nodo
   imán en cero y un vencimiento vacío crea un grupo fantasma.

## Test diferencial: 604 casos contra el `compute.ts` real

La comprobación más fuerte del port, y reproducible con un comando:

    TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_compute.sh

Traduce `compute.ts` a JS puro quitando **solo** los tipos —la lógica no se
toca—, genera 604 contratos crudos cubriendo todas las formas raras que puede
mandar Massive (texto, booleanos, `NaN`, `null`, arrays, objetos anidados con
tipo cambiado, fechas con y sin hora, MAYÚSCULAS) y compara los **diez campos**
de la fila más `contractPrice`, campo a campo.

No verifica que el código se parezca: verifica que la **salida** coincida. Cada
diferencia sale clasificada por causa, y la que no encaja en una divergencia
declarada se marca como REAL.

    DECLARADA · derivado NaN → 0            133
    DECLARADA · basura → fallback           116
    DECLARADA · case del tipo                97
    DECLARADA · OI/volumen entero            90
    TIPO      · vencimiento no-string        79
    DECLARADA · vencimiento canónico         65
    DECLARADA · OI fraccionario → entero     38

    SIN diferencias reales

Arrancó en **23 diferencias reales** y las dos causas que las explicaban son los
hallazgos 9 y 10.

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
- **Página con basura mezclada**: contratos buenos + `details: "texto"` +
  `open_interest: "NaN"` + `None` + `"x"`, y encima todo en MAYÚSCULAS. El
  endpoint responde `ok`, con `structure > 0` y el régimen de GEX correcto.

## Correspondencia con el original

**35/35 sentencias ejecutables** mapeadas: `contractPrice` con su cascada de
tres niveles y los cuatro `PriceSource`, `openPremium`, `notionalValue` con
`sharesPerContract`, `normalizeType`, `toRow` con sus diez campos,
`sortByOpenInterestDesc` (copia, no muta) y `countExpirations` (sin las vacías).

Tests: los 12 casos de `compute.test.ts` + 36 que el original no cubre,
más el diferencial de 604 casos.
