#!/usr/bin/env bash
# Test diferencial de store.ts: ejecuta SU archivo en Node (solo sin tipos, la
# lógica intacta) y el port de Python sobre los mismos casos, y compara.
#
#     engine/scripts/diff_store.sh                       # baja su store.ts de GitHub
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_store.sh
#
# Verifica que la SALIDA coincida, no que el código se parezca. El comparador es
# ESTRICTO: cualquier caso que no dé exactamente lo mismo cuenta como diferencia
# y el script sale con código 1.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
RAW="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib/store.ts"

command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP" "$S/_diffstore_victor.mjs"' EXIT

if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/store.ts" ]]; then
  cp "$TITO_ROOT/web/lib/store.ts" "$TMP/store.ts"
  echo "  fuente: $TITO_ROOT/web/lib/store.ts"
elif curl -fsS "$RAW" -o "$TMP/store.ts"; then
  echo "  fuente: GitHub (main)"
else
  echo "  · saltado: sin TITO_ROOT y sin poder bajar su store.ts"; exit 0
fi

# Transpilado mínimo: se quitan los tipos y NADA más. Ni una línea de lógica.
python3 - "$TMP/store.ts" "$S/_diffstore_victor.mjs" <<'PY'
import pathlib, re, sys
ts = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
js = re.sub(r'^import .*?;\s*$', '', ts, flags=re.M)
js = re.sub(r'export interface \w+ \{.*?\n\}\n', '', js, flags=re.S)
TIPO = (r'(?:Promise<StoredTrades \| null>|Promise<SaveResult>|'
        r'Map<number, FlowRow>|FlowRow\[\]|StoredTrades|string)')
js = re.sub(r'(\w+)\s*:\s*' + TIPO + r'(?=[,)])', r'\1', js)
js = re.sub(r'\)\s*:\s*' + TIPO + r'\s*\{', ') {', js)
js = re.sub(r'new Map<[^>]*>\(\)', 'new Map()', js)
js = re.sub(r'const payload: StoredTrades =', 'const payload =', js)
js = js.replace(' as StoredTrades', '').replace('export async function', 'async function')
js = ('import { promises as fs } from "fs";\nimport path from "path";\n' + js)
js = js.replace('const DATA_DIR = path.join(process.cwd(), "data", "trades");',
                'const DATA_DIR = path.join(process.env.ST_DIR, "trades");')
js += '\nexport { loadTrades, saveTrades, fileFor };\n'
assert 'Date.parse(b.timestamp) - Date.parse(a.timestamp)' in js, "el sort se perdió"
assert 'MAX_PER_TICKER = 5000' in js, "el tope se perdió"
pathlib.Path(sys.argv[2]).write_text(js, encoding="utf-8")
PY

python3 "$S/_diffstore_casos.py" > "$TMP/casos.json"

export ST_DIR="$TMP/vd" ST_CASOS="$TMP/casos.json"
export ST_OUT="$TMP/victor_out.json" ST_PY_OUT="$TMP/py_out.json"
( cd "$S" && node "$S/_diffstore_run.mjs" )
rm -f "$S/_diffstore_victor.mjs"

( cd "$ROOT/engine" && python3 "$S/_diffstore_run_py.py" )
python3 "$S/_diffstore_compare.py"
