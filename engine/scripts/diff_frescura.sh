#!/usr/bin/env bash
# Diferencial de `recencyFactor` (levels.ts): el peso por frescura de un nivel,
# contra SU archivo ejecutado en Node.
#
#     engine/scripts/diff_frescura.sh
#
# Por qué existe: `diff_motor.sh` ya compara `findLevels` entero, pero su
# generador nunca produce el caso que falla — hace falta que la diferencia caiga
# EXACTAMENTE en un umbral (30/90/180 días) y que la consulta sea después de las
# 21:00 UTC. Ahí su cuenta de días fraccionarios y una cuenta de días de
# calendario se separan, y el port daba un factor más ALTO, o sea niveles más
# fuertes de los que él calcula.
#
# Medido cuando se encontró: 4 de 28 combinaciones divergían.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib/levels.ts"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/levels.ts" ]]; then
  cp "$TITO_ROOT/web/lib/levels.ts" "$TMP/levels.ts"; echo "  fuente: $TITO_ROOT"
elif curl -fsS "$RAW" -o "$TMP/levels.ts"; then echo "  fuente: GitHub (main)"
else echo "  · saltado: no se pudo bajar su levels.ts"; exit 0; fi

# Su `recencyFactor` → JS puro. Solo se quitan los tipos; la aritmética no se
# toca, que es justo lo que se está midiendo.
python3 - "$TMP/levels.ts" "$TMP/rf.mjs" <<'PY'
import pathlib, re, sys
ts = pathlib.Path(sys.argv[1]).read_text()
i = ts.index("const DAY = 86_400_000;")
j = ts.index("export interface LevelsInput")
js = ts[i:j].replace("export function", "function")
js = re.sub(r"(\w+)\s*:\s*(string|Date)", r"\1", js)
js = re.sub(r"\)\s*:\s*number\s*\{", ") {", js)
assert "T21:00:00Z" in js, "el ancla de las 21:00Z se perdió en el transpilado"
assert "/ DAY" in js, "la división por DAY se perdió en el transpilado"
pathlib.Path(sys.argv[2]).write_text(js + "\nexport { recencyFactor };\n")
PY

export FR_CASOS="$TMP/casos.json" FR_PY_OUT="$TMP/py.json" FR_OUT="$TMP/victor.json"
python3 "$ROOT/engine/scripts/_difffresc_casos.py"
cp "$ROOT/engine/scripts/_difffresc_run.mjs" "$TMP/run.mjs"
( cd "$TMP" && node run.mjs )

python3 - <<'PY'
import json, os, sys
casos = json.load(open(os.environ["FR_CASOS"]))
suyo  = json.load(open(os.environ["FR_OUT"]))
mio   = json.load(open(os.environ["FR_PY_OUT"]))
assert len(casos) == len(suyo) == len(mio), (len(casos), len(suyo), len(mio))
malos = [(c, a, b) for c, a, b in zip(casos, suyo, mio) if abs(a - b) > 1e-12]
for c, a, b in malos[:10]:
    print(f"  ✗ toque={c['last_touch']!r} now={c['now']}  víctor={a}  port={b}")
print(f"\n  {len(casos) - len(malos)}/{len(casos)} factores de frescura idénticos"
      + (f" · {len(malos)} DIVERGEN" if malos else " · sin diferencias"))
sys.exit(1 if malos else 0)
PY
