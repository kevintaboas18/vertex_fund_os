#!/usr/bin/env bash
# Test diferencial de compute.ts: genera cientos de contratos crudos cubriendo
# todas las formas raras que puede mandar Massive, los pasa por el compute.ts
# REAL de Víctor en Node y por el port en Python, y compara campo a campo.
#
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_compute.sh
#
# Es la comprobación más fuerte del port: no verifica que el código se parezca,
# verifica que la SALIDA coincida. Toda diferencia sale clasificada por causa, y
# las que no encajan en una divergencia declarada se marcan como REAL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TITO="${TITO_ROOT:-}"
if [[ -z "$TITO" || ! -f "$TITO/web/lib/compute.ts" ]]; then
  echo "  · saltado: define TITO_ROOT con el repo de Víctor clonado"
  exit 0
fi
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. compute.ts → JS puro, quitando SOLO los tipos (la lógica no se toca).
python3 - "$TITO/web/lib/compute.ts" "$TMP/victor.mjs" <<'PY'
import pathlib, re, sys
ts = pathlib.Path(sys.argv[1]).read_text()
js = re.sub(r'^import type .*?;\s*$', '', ts, flags=re.M)
js = re.sub(r'\)\s*:\s*\{\s*price: number \| null;\s*source: PriceSource;\s*\}\s*\{', ') {', js)
TIPO = r'(?:string \| undefined|number \| null|number|string|Row\[\]|Row|RawContract|ContractType|PriceSource)'
js = re.sub(r'(\w+)\s*:\s*' + TIPO, r'\1', js)          # parámetros y campos
js = re.sub(r'\)\s*:\s*' + TIPO + r'\s*\{', ') {', js)  # tipos de retorno
js = js.replace("export function", "function")
js += "\nexport { contractPrice, openPremium, notionalValue, toRow, sortByOpenInterestDesc, countExpirations };\n"
pathlib.Path(sys.argv[2]).write_text(js)
PY

python3 "$ROOT/engine/scripts/_diff_casos.py" > "$TMP/casos.json"
sed "s#/tmp/victor_compute.mjs#$TMP/victor.mjs#; s#/tmp/casos.json#$TMP/casos.json#; s#/tmp/victor_out.json#$TMP/victor_out.json#" \
    "$ROOT/engine/scripts/_diff_victor.mjs" > "$TMP/run.mjs"
node "$TMP/run.mjs"

sed "s#/tmp/casos.json#$TMP/casos.json#; s#/tmp/victor_out.json#$TMP/victor_out.json#" \
    "$ROOT/engine/scripts/_diff_compare.py" > "$TMP/cmp.py"
cd "$ROOT/engine" && python3 "$TMP/cmp.py"
