#!/usr/bin/env bash
# Diferencial de SU `massive.ts`: el cliente de la cadena y de las barras.
#
#     engine/scripts/diff_cadena.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_cadena.sh
#
# Era uno de los cinco módulos suyos sin diferencial —portado y leído, pero
# nunca EJECUTADO al lado del original— porque es el que habla con la red. Lo
# que decide, sin embargo, no es la red: es qué contrato entra, cuál es el
# precio del subyacente, cuántas páginas se piden, cuándo se declara truncada la
# cadena y con qué fecha se etiqueta cada barra. Todo eso es puro en cuanto la
# respuesta ya está en la mano, y de ahí sale el SPOT que ancla los objetivos.
#
# La red se sustituye en los DOS lados con las mismas páginas: `globalThis.fetch`
# en el suyo, `massive._get` en el nuestro. Ni una línea de lógica cambia.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
node --experimental-strip-types -e '' 2>/dev/null || {
  echo "  · saltado: este node no quita tipos (hace falta 22+)"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in massive compute types occ conditions blackScholes; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar $f.ts"; exit 0
  fi
done
sed -i -E 's|from "\./([A-Za-z]+)"|from "./\1.ts"|g' "$TMP"/*.ts
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

cp "$S/_diffcadena_run.mts" "$TMP/run.mts"
python3 "$S/_diffcadena_casos.py" > "$TMP/casos.json"
export CADENA_CASOS="$TMP/casos.json" CADENA_OUT="$TMP/victor_out.json"
export MASSIVE_API_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
( cd "$TMP" && node --experimental-strip-types run.mts )
python3 "$S/_diffcadena_compara.py"
