#!/usr/bin/env bash
# Test diferencial de compute.ts: genera cientos de contratos crudos cubriendo
# todas las formas raras que puede mandar Massive, los pasa por el compute.ts
# REAL de Víctor en Node y por el port en Python, y compara campo a campo.
#
#     engine/scripts/diff_compute.sh                     # baja su compute.ts de GitHub
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_compute.sh
#     DIFF_N=3000 engine/scripts/diff_compute.sh
#
# Es la comprobación más fuerte del port: no verifica que el código se parezca,
# verifica que la SALIDA coincida.
#
# El comparador es ESTRICTO: cuenta TODAS las diferencias. Solo separa el muro
# del lenguaje —el mismo número en otro tipo de campo, que Python no puede
# reproducir sin que el resto del motor reviente— de una diferencia de VALOR,
# que sí sería un fallo del port. Sale con código 1 si hay alguna de valor.
#
# (Hubo un segundo comparador que clasificaba las diferencias por causa e
# indultaba las divergencias declaradas. Se quitó al hacer el port literal: ya
# no hay divergencias que indultar, y el indulto escondía el número que importa.)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
RAW="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib/compute.ts"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/compute.ts" ]]; then
  cp "$TITO_ROOT/web/lib/compute.ts" "$TMP/compute.ts"
  echo "  fuente: $TITO_ROOT/web/lib/compute.ts"
elif curl -fsS "$RAW" -o "$TMP/compute.ts"; then
  echo "  fuente: GitHub (main)"
else
  echo "  · saltado: sin TITO_ROOT y sin poder bajar su compute.ts"; exit 0
fi

# 1. compute.ts → JS puro, quitando SOLO los tipos (la lógica no se toca).
python3 - "$TMP/compute.ts" "$TMP/victor.mjs" <<'PY'
import pathlib, re, sys
ts = pathlib.Path(sys.argv[1]).read_text()
js = re.sub(r'^import type .*?;\s*$', '', ts, flags=re.M)
js = re.sub(r'\)\s*:\s*\{\s*price: number \| null;\s*source: PriceSource;\s*\}\s*\{', ') {', js)
TIPO = r'(?:string \| undefined|number \| null|number|string|Row\[\]|Row|RawContract|ContractType|PriceSource)'
js = re.sub(r'(\w+)\s*:\s*' + TIPO, r'\1', js)          # parámetros y campos
js = re.sub(r'\)\s*:\s*' + TIPO + r'\s*\{', ') {', js)  # tipos de retorno
js = js.replace("export function", "function")
js += "\nexport { contractPrice, openPremium, notionalValue, toRow, sortByOpenInterestDesc, countExpirations };\n"
assert 't === "put" ? "put" : "call"' in js, "normalizeType se perdió en el transpilado"
assert 'openInterest * sharesPerContract * strike' in js, "la fórmula se perdió"
pathlib.Path(sys.argv[2]).write_text(js)
PY

python3 "$S/_diff_casos.py" > "$TMP/casos.json"
sed "s#/tmp/victor_compute.mjs#$TMP/victor.mjs#; s#/tmp/casos.json#$TMP/casos.json#; s#/tmp/victor_out.json#$TMP/victor_out.json#" \
    "$S/_diff_victor.mjs" > "$TMP/run.mjs"
node "$TMP/run.mjs"

cd "$ROOT/engine"
DIFF_CASOS="$TMP/casos.json" DIFF_VICTOR_OUT="$TMP/victor_out.json" \
  python3 "$S/_diff_estricto.py"
