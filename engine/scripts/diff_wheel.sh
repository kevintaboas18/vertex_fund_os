#!/usr/bin/env bash
# Diferencial de la WHEEL: wheel.ts, wheelAfford.ts, wheelUniverse.ts, earnings.ts.
#
#     engine/scripts/diff_wheel.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_wheel.sh
#
# Los cuatro estaban PORTADOS y ninguno tenía diferencial: la estrategia entera
# —qué put vender, con cuánto colateral, con qué probabilidad de expirar sin
# valor y en qué orden se listan— se medía solo contra sus propios tests.
#
# Lo que cuelga de aquí no es cosmético: `wheelMetrics` decide el crédito y el
# colateral (o sea si la operación te CABE con $1.000), `scoreCandidate` reparte
# los 100 puntos que ordenan la tabla, `liquidityBlock` decide qué se esconde
# por ilíquido y `earningsFlag` decide si el vencimiento se come un reporte.
#
# También se comparan las CONSTANTES: presets, recortes de prima, umbrales y los
# 40 símbolos del universo. Un preset con otro rango de delta elegiría otros
# strikes y ningún caso lo delataría, porque cada lado usaría el suyo.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
node --experimental-strip-types -e '' 2>/dev/null || {
  echo "  · saltado: este node no quita tipos (hace falta 22+)"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# `wheel.ts` importa blackScholes y expectedMove en tiempo de EJECUCIÓN (delta,
# precio, IV implícita y probAbove), así que van los suyos: si el port
# divergiera ahí, la Wheel entera divergiría por debajo sin que se viera.
for f in wheel wheelAfford wheelUniverse earnings blackScholes expectedMove levels; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar $f.ts"; exit 0
  fi
done
sed -i -E 's|from "\./([A-Za-z]+)"|from "./\1.ts"|g' "$TMP"/*.ts
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

# Que la copia sea la que se cree. Si él mueve un umbral o un preset, el
# diferencial tiene que enterarse por aquí y no dar verde comparando dos cosas
# viejas entre sí.
python3 - "$TMP" <<'PY'
import pathlib, sys
d = pathlib.Path(sys.argv[1])
esperado = {
    "wheel.ts": ["MAX_SPREAD_PCT = 25", "MIN_OI = 100", "deltaMin: 0.10",
                 "deltaMax: 0.40", "Math.max(dte, 1)", "bid: 0"],
    "wheelAfford.ts": ["collateral <= cash", "a.blocked ? 1 : -1"],
    "earnings.ts": ["QUARTER_DAYS", "> 10"],
}
for f, marcas in esperado.items():
    t = (d / f).read_text(encoding="utf-8")
    for m in marcas:
        assert m in t, f"{f}: se perdió «{m}» — su archivo cambió"
u = (d / "wheelUniverse.ts").read_text(encoding="utf-8")
print(f"  contrato: umbrales y presets, los suyos · universo de "
      f"{u.count('ticker:')} símbolos")
PY

cp "$S/_diffwheel_run.mts" "$TMP/run.mts"
python3 "$S/_diffwheel_casos.py" > "$TMP/casos.json"
export WH_CASOS="$TMP/casos.json" WH_OUT="$TMP/victor_out.json"
( cd "$TMP" && node --experimental-strip-types run.mts )
( cd "$ROOT/engine" && python3 "$S/_diffwheel_compara.py" )
