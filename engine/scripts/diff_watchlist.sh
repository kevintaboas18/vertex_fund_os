#!/usr/bin/env bash
# Test diferencial de watchlist.ts: ejecuta SU archivo en Node (solo sin tipos,
# la lógica intacta) y el port de Python sobre los mismos casos, y compara.
#
#     engine/scripts/diff_watchlist.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_watchlist.sh
#
# Cubre las 19 funciones puras del módulo más la tabla de BROKERS. Lo que se
# mide es la SALIDA, no el parecido del código: cualquier caso que no dé
# exactamente lo mismo cuenta como diferencia y el script sale con código 1.
#
# Los casos que más importan y por los que existe este archivo:
#
#   · `removeFromOutbox` con filas legado de solo-tickers mezcladas con filas
#     de contrato del MISMO subyacente — el bug que dejó SPXW y SPY imborrables.
#   · `pendingOutbox` deduplicando por `outboxKey`, que vale el símbolo OCC en
#     una fila y el ticker en la otra.
#   · `contractQuery` con `toFixed(4)`: "20" no casa con "20.0000" en el broker.
#   · `upsert` de un contrato ya marcado: NO pisa la foto de entrada original
#     pero sí conserva el estado de sincronización.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib/watchlist.ts"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/watchlist.ts" ]]; then
  cp "$TITO_ROOT/web/lib/watchlist.ts" "$TMP/watchlist.ts"; echo "  fuente: $TITO_ROOT"
elif curl -fsS "$RAW" -o "$TMP/watchlist.ts"; then echo "  fuente: GitHub (main)"
else echo "  · saltado: no se pudo bajar su watchlist.ts"; exit 0; fi

# Transpilado mínimo: se quitan los tipos y NADA más. Ni una línea de lógica.
python3 - "$TMP/watchlist.ts" "$TMP/wl.mjs" <<'PY'
import pathlib, re, sys
ts = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
js = re.sub(r'^import type .*?;\s*$', '', ts, flags=re.M)
js = re.sub(r'export interface \w+ \{.*?\n\}\n', '', js, flags=re.S)
js = re.sub(r'^export type \w+ =[\s\S]*?;\s*$', '', js, flags=re.M)
js = re.sub(r'export const BROKERS: BrokerAdapter\[\] =', 'export const BROKERS =', js)
js = re.sub(r'new Set<[^>]*>\(\)', 'new Set()', js)
js = re.sub(r'const entry: OutboxItem =', 'const entry =', js)

# Las firmas se limpian recorriendo paréntesis, NO con una expresión regular.
#
# Se probó primero con regex y se llevó por delante `strike_price:
# c.strike.toFixed(4)`, porque una propiedad de objeto literal y un parámetro
# tipado se escriben igual: `nombre: algo,`. Borrar tipos a ciegas borra
# lógica, y el diferencial habría comparado dos versiones mutiladas de lo
# mismo y dado verde. De ahí el `assert` del final.
def limpia_firmas(src: str) -> str:
    out, i = [], 0
    while True:
        m = re.search(r'\bfunction\s+\w+\s*\(', src[i:])
        if not m:
            out.append(src[i:])
            break
        ini = i + m.end()                    # justo tras el paréntesis de apertura
        out.append(src[i:ini])
        prof, j = 1, ini
        while prof:                          # cierre del paréntesis de parámetros
            if src[j] == '(':
                prof += 1
            elif src[j] == ')':
                prof -= 1
            j += 1
        params = src[ini:j - 1]
        # Corta por comas de nivel 0 y quédate con el identificador.
        piezas, prof2, ult = [], 0, 0
        for k, ch in enumerate(params):
            if ch in '([{<':
                prof2 += 1
            elif ch in ')]}>':
                prof2 -= 1
            elif ch == ',' and prof2 == 0:
                piezas.append(params[ult:k]); ult = k + 1
        piezas.append(params[ult:])
        nombres = [p.split(':')[0].strip() for p in piezas if p.strip()]
        out.append(', '.join(n for n in nombres if n) + ')')
        # Y el tipo de retorno: desde `)` hasta la llave que ABRE EL CUERPO.
        #
        # No vale «la primera `{`»: `contractQuery` declara su retorno como un
        # objeto (`): {\n chain_symbol: string;\n ...\n} | null {`) y esa llave
        # va primero. La del cuerpo es la única que cumple las dos cosas a la
        # vez: no queda nada más en su línea, y el carácter anterior no es el
        # `:` que abre una anotación.
        k, prof3 = j, 0
        while k < len(src):
            ch = src[k]
            if ch == '{':
                fin_linea = src[k + 1:src.find('\n', k + 1)].strip() == ''
                previo = src[:k].rstrip()[-1:]
                if prof3 == 0 and fin_linea and previo != ':':
                    break
                prof3 += 1
            elif ch == '}':
                prof3 -= 1
            k += 1
        out.append(' ')
        i = k
    return ''.join(out)

js = limpia_firmas(js)
assert 'toFixed(4)' in js, "el toFixed(4) del strike se perdió"
assert 'localeCompare' in js, "el orden por localeCompare se perdió"
assert 'robinhood' in js and 'tastytrade' in js, "la tabla de BROKERS se perdió"
assert 'export function removeFromOutbox' in js, "removeFromOutbox se perdió"
pathlib.Path(sys.argv[2]).write_text(js, encoding="utf-8")
PY

export WL_CASOS="$TMP/casos.json" WL_OUT="$TMP/victor.json" WL_PY_OUT="$TMP/py.json"
python3 "$ROOT/engine/scripts/_diffwl_casos.py"
cp "$ROOT/engine/scripts/_diffwl_run.mjs" "$TMP/run.mjs"
( cd "$TMP" && node run.mjs )
( cd "$ROOT/engine" && python3 "$ROOT/engine/scripts/_diffwl_run_py.py" )

python3 - <<'PY'
import json, os, sys
casos = json.load(open(os.environ["WL_CASOS"]))
suyo  = json.load(open(os.environ["WL_OUT"]))
mio   = json.load(open(os.environ["WL_PY_OUT"]))
assert len(casos) == len(suyo) == len(mio), (len(casos), len(suyo), len(mio))
malos = [(c, a, b) for c, a, b in zip(casos, suyo, mio) if a != b]
for c, a, b in malos[:12]:
    print(f"  ✗ {c['fn']}  víctor={json.dumps(a, ensure_ascii=False)[:150]}")
    print(f"    {' ' * len(c['fn'])}  port  ={json.dumps(b, ensure_ascii=False)[:150]}")
print(f"\n  {len(casos) - len(malos)}/{len(casos)} casos de watchlist idénticos"
      + (f" · {len(malos)} DIVERGEN" if malos else " · sin diferencias"))
sys.exit(1 if malos else 0)
PY
