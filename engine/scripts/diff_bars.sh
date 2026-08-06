#!/usr/bin/env bash
# Test diferencial de barsStore.ts: ejecuta SU archivo en Node (solo sin tipos,
# la lógica intacta) y el port en Python sobre los mismos casos, y compara.
#
#     engine/scripts/diff_bars.sh                       # baja su barsStore.ts de GitHub
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_bars.sh
#
# Verifica que la SALIDA coincida, no que el código se parezca.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib/barsStore.ts"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/barsStore.ts" ]]; then
  cp "$TITO_ROOT/web/lib/barsStore.ts" "$TMP/barsStore.ts"
  echo "  fuente: $TITO_ROOT/web/lib/barsStore.ts"
elif curl -fsS "$RAW" -o "$TMP/barsStore.ts"; then
  echo "  fuente: GitHub (main)"
else
  echo "  · saltado: sin TITO_ROOT y sin poder bajar su barsStore.ts"; exit 0
fi

python3 - "$TMP/barsStore.ts" "$TMP/victor.mjs" <<'PY'
import pathlib, re, sys
ts = pathlib.Path(sys.argv[1]).read_text()
js = re.sub(r'^import .*?;\s*$', '', ts, flags=re.M)
js = re.sub(r'interface BarsFile \{.*?\n\}\n', '', js, flags=re.S)
TIPO = r'(?:Promise<BarsFile \| null>|Promise<DailyBar\[\]>|Promise<void>|DailyBar\[\]|BarsFile|string|number)'
js = re.sub(r'(\w+)\s*:\s*' + TIPO, r'\1', js)
js = re.sub(r'\)\s*:\s*' + TIPO + r'\s*\{', ') {', js)
js = js.replace(' as BarsFile', '').replace(' as DailyBar[]', '')
js = js.replace('export async function', 'async function')
js = ('import { promises as fs } from "fs";\nimport path from "path";\n'
      'import { marketDateStr } from "./occ.mjs";\n'
      'let fetchDailyBars = async () => [];\n'
      'export function _setFetch(f){ fetchDailyBars = f; }\n' + js)
js = js.replace('const DATA_DIR = path.join(process.cwd(), "data", "bars");',
                'const DATA_DIR = path.join(process.env.BS_DIR, "bars");')
js += '\nexport { loadBars, saveBars, cachedDailyBars };\n'
pathlib.Path(sys.argv[2]).write_text(js)
PY

cp "$ROOT/engine/scripts/_diffbars_occ.mjs" "$TMP/occ.mjs"
cp "$ROOT/engine/scripts/_diffbars_run.mjs" "$TMP/run.mjs"
python3 "$ROOT/engine/scripts/_diffbars_casos.py" > "$TMP/casos.json"

BS_DIR="$TMP/vd" BS_CASOS="$TMP/casos.json" BS_OUT="$TMP/victor_out.json" node "$TMP/run.mjs"
sed "s#/tmp/bs/casos.json#$TMP/casos.json#; s#/tmp/bs/py_out.json#$TMP/py_out.json#" \
    "$ROOT/engine/scripts/_diffbars_run_py.py" > "$TMP/run_py.py"
python3 "$TMP/run_py.py"

export BS_CASOS="$TMP/casos.json" BS_OUT="$TMP/victor_out.json" BS_PY_OUT="$TMP/py_out.json"
python3 "$ROOT/engine/scripts/_diffbars_compare.py"
