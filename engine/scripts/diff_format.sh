#!/usr/bin/env bash
# Diferencial de `format.ts`: lo que el usuario LEE en pantalla.
#
#     engine/scripts/diff_format.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_format.sh
#
# `format.ts` es el único módulo suyo que NO vive en `web/lib`, y por eso se
# quedó fuera de los tres registros de la auditoría —módulos, rutas y
# componentes— durante siete rondas. Nadie lo comparó nunca. Cuando por fin se
# comparó, el `fmtAbbr` del panel divergía en 14 de 14 casos medidos: dos
# decimales contra uno, el signo menos del lado equivocado y sin escalón "T".
#
# Es el fallo que no rompe nada y se ve en cada pantalla: el motor calculaba
# bien y el panel lo pintaba distinto de como lo pinta él.
#
# Los dos lados corren en el MISMO Node. Comparar en dos procesos mediría el
# ICU de cada uno en vez de medir el código.
#
# Y el lado del panel NO se reescribe aquí: se recorta del HTML tal cual. Una
# copia del bloque en el diferencial pasaría a ser otro archivo que mantener y
# que puede quedarse atrás — justo lo que dejó a `format.ts` sin comparar.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/app"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/app/format.ts" ]]; then
  cp "$TITO_ROOT/web/app/format.ts" "$TMP/format.ts"
elif ! curl -fsS "$BASE/format.ts" -o "$TMP/format.ts"; then
  echo "  · saltado: no se pudo bajar format.ts"; exit 0
fi
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

# Su lado: se quitan los tipos y NADA más. `format.ts` no tiene lógica de tipos
# más allá de las anotaciones de firma, así que el recorte es trivial y se
# comprueba después que los diez exports siguen ahí.
python3 - "$TMP/format.ts" "$TMP/suyo.mjs" <<'PY'
import pathlib, re, sys
ts = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
js = re.sub(r'(\w+)\s*:\s*(string|number)(?=[,)])', r'\1', ts)
js = re.sub(r'\)\s*:\s*(string|number)\s*\{', ') {', js)
for n in ("int", "money", "money0", "px", "pct",
          "timeOf", "dateOf", "timeET", "dateET", "hmET"):
    assert re.search(rf'export (const|function) {n}\b', js), f"se perdió {n}"
assert 'America/New_York' in js, "se perdió la zona del mercado"
pathlib.Path(sys.argv[2]).write_text(js, encoding="utf-8")
PY

# Mi lado: el bloque de formateadores RECORTADO DEL HTML, sin tocar una línea.
python3 - "$ROOT/vertex_fund_os_platform.html" "$TMP/mio.mjs" <<'PY'
import pathlib, re, sys
html = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")

def bloque(desde, hasta):
    i = html.index(desde)
    j = html.index(hasta, i)
    return html[i:j]

trozos = [
    bloque("const VC_INT ", "// ── CompanyHeader.tsx"),
    bloque("function fmtAbbr(v)", "\n\n/* ═"),
]
js = "\n".join(trozos)
# `fmtMoney` va detrás de `fmtAbbr` en el mismo trozo; se comprueba.
for n in ("VC_INT", "VC_MONEY", "VC_MONEY0", "VC_PX", "VC_PCT",
          "vcTimeET", "vcDateET", "vcTimeOf", "vcDateOf", "vcHmET",
          "fmtAbbr", "fmtMoney"):
    assert re.search(rf'\b(const|function) {n}\b', js), f"no se recortó {n}"
js += ("\nexport { VC_INT, VC_MONEY, VC_MONEY0, VC_PX, VC_PCT,"
       " vcTimeET, vcDateET, vcTimeOf, vcDateOf, vcHmET, fmtAbbr, fmtMoney };\n")
pathlib.Path(sys.argv[2]).write_text(js, encoding="utf-8")
PY

python3 "$S/_difffmt_casos.py" > "$TMP/casos.json"
export FMT_CASOS="$TMP/casos.json" FMT_OUT="$TMP/out.json"
export FMT_SUYO="file://$TMP/suyo.mjs" FMT_MIO="file://$TMP/mio.mjs"
node "$S/_difffmt_run.mjs"
FMT_OUT="$TMP/out.json" python3 "$S/_difffmt_compara.py"
