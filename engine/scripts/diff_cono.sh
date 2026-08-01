#!/usr/bin/env bash
# Diferencial del CONO de la gráfica: la fórmula que dibuja `renderVictorProjChart`
# en el HTML contra el `expectedMove`/`conePoints` REAL de Víctor, en Node.
#
#     engine/scripts/diff_cono.sh
#
# Por qué existe: el cono es lo único del panel que se calcula en el navegador y
# no en el motor, así que ningún diferencial de Python lo alcanzaba. De aquí
# salieron dos diferencias — el suelo `max(iv, 0.01)` que faltaba y la forma del
# cono, que se interpolaba en vez de evaluarse en cada paso.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib/expectedMove.ts"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/expectedMove.ts" ]]; then
  cp "$TITO_ROOT/web/lib/expectedMove.ts" "$TMP/em.ts"; echo "  fuente: $TITO_ROOT"
elif curl -fsS "$RAW" -o "$TMP/em.ts"; then echo "  fuente: GitHub (main)"
else echo "  · saltado: no se pudo bajar su expectedMove.ts"; exit 0; fi

# su expectedMove → JS puro (solo se quitan los tipos)
python3 - "$TMP/em.ts" "$TMP/em.mjs" <<'PY'
import pathlib, re, sys
ts = pathlib.Path(sys.argv[1]).read_text()
i, j = ts.index("export function expectedMove"), ts.index("export interface ConePoint")
js = "const DAYS_PER_YEAR = 365;\n" + ts[i:j]
js = re.sub(r'(\w+)\s*:\s*number', r'\1', js)
js = re.sub(r'\)\s*:\s*ExpectedMove\s*\{', ') {', js)
js = js.replace("export function", "function") + "\nexport { expectedMove };\n"
assert "Math.max(iv, 0.01)" in js, "el suelo de IV se perdió en el transpilado"
pathlib.Path(sys.argv[2]).write_text(js)
PY

# la fórmula del HTML, extraída del archivo real (no copiada a mano)
python3 - "$ROOT/vertex_fund_os_platform.html" "$TMP/nuestro.json" <<'PY'
import json, pathlib, re, sys
html = pathlib.Path(sys.argv[1]).read_text()
bloque = html[html.index("function renderVictorProjChart"):]
bloque = bloque[:bloque.index("\n}")]
assert "Math.max((d.gex && d.gex.iv) || 0.4, 0.01)" in bloque, "falta el suelo de IV"
assert "Math.exp(mult * sd * k)" in bloque, "el cono no se evalúa paso a paso"
pathlib.Path(sys.argv[2]).write_text(json.dumps({"ok": True}))
PY

cp "$ROOT/engine/scripts/_diffcono_run.mjs" "$TMP/run.mjs"
( cd "$TMP" && node run.mjs )
