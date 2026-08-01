#!/usr/bin/env bash
# Diferencial del MOTOR: sus validation.ts, levels.ts y structure.ts contra el port.
#
#     engine/scripts/diff_motor.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_motor.sh
#
# Cubre los SEIS sub-agentes que producen el score de 0-100:
#
#     flow.ts        agresividad + convicción + inusualidad   55 pts
#     structure.ts   estructura de la cadena                  20 pts
#     validation.ts  confirmación de precio                   15 pts
#     levels.ts      niveles (gráfica y síntesis de precios)
#
# Usa el quitado de tipos NATIVO de Node en vez de un transpilador propio: solo
# desaparecen los tipos, la lógica no se toca. De aquí salieron dos hallazgos que
# la suite de tests portada no podía ver — el orden del `Set` en `levels.ts` y el
# `Math.round` frente al `round` de Python.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
node --experimental-strip-types -e '' 2>/dev/null || {
  echo "  · saltado: este node no quita tipos (hace falta 22+)"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in validation levels structure types flow conditions occ; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar $f.ts"; exit 0
  fi
done
# Node exige la extensión en los imports relativos; su bundler no. Se añade sin
# tocar nada más — es lo único que se modifica de sus archivos.
sed -i 's|from "\./conditions"|from "./conditions.ts"|; s|from "\./occ"|from "./occ.ts"|' "$TMP/flow.ts"
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

cp "$S/_diffmotor_run.mts" "$TMP/run.mts"
python3 "$S/_diffmotor_casos.py" > "$TMP/casos.json"
export MOTOR_CASOS="$TMP/casos.json" MOTOR_OUT="$TMP/victor_out.json"
( cd "$TMP" && node --experimental-strip-types run.mts )
python3 "$S/_diffmotor_compare.py"
