#!/usr/bin/env bash
# Diferencial de PRIMITIVAS: `_js_number` vs `Number()` y `_date_parse` vs
# `Date.parse()`, ejecutando V8 de verdad.
#
#     engine/scripts/diff_primitivas.sh
#
# Por qué existe aparte de los otros tres: el diferencial a nivel de fila nunca
# genera un `"1_000"` ni un `"0x1A"`, y ahí es justo donde `float()` de Python y
# `Number()` de JS dejan de coincidir. Cuatro diferencias reales salieron de
# aquí y de ningún otro sitio.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
python3 "$S/_diffprim_casos.py" > "$TMP/casos.json"
export PRIM_CASOS="$TMP/casos.json" PRIM_OUT="$TMP/js_out.json"
node "$S/_diffprim_run.mjs"
python3 "$S/_diffprim_compare.py"
