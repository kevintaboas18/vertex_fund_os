"""Corpus de casos límite para las dos primitivas de JS que el port reimplementa.

`_js_number` (compute.py) y `_date_parse` (stores.py) son las dos piezas donde
Python y JavaScript se parecen lo bastante como para que una diferencia pase
inadvertida. El diferencial a nivel de FILA no las alcanza: su generador nunca
produce un `"1_000"` ni un `"0x1A"`, y esos son exactamente los valores donde
`float()` y `Number()` dejan de coincidir.
"""
import json

casos = [
    # ── numéricos en texto
    "500", "205.5", "  500  ", "1e3", "1E3", "1e-3", ".5", "5.", "+5", "-5",
    "0", "-0", "", "   ", "\t7\n",
    # Python `float()` acepta el separador de millar; `Number()` no.
    "1_0", "1_000", "12_3.4", "1_0.5",
    # `Number()` entiende bases; `float()` no.
    "0x1A", "0X1a", "0o17", "0b101", "0xZZ",
    # `float()` acepta inf/nan en cualquier case; `Number()` solo "Infinity".
    "Infinity", "-Infinity", "+Infinity", "infinity", "INFINITY",
    "inf", "-inf", "nan", "NaN", "NAN",
    # basura corriente
    "1,000", "1 000", "5%", "$5", "abc", "null", "undefined", "true", "false",
    "12abc", "abc12",
    # ── tipos que no son string
    None, True, False, 0, 1, -1, 0.5, 1e9, 1e308, [], [7], [1, 2], {}, {"a": 1},
    # ── fechas (para Date.parse)
    "2026-07-30", "2026-07-30T15:00:00Z", "2026-07-30T15:00:00z",
    "2026-07-30T15:00", "2026-07-30T15:00:00.250Z", "2026-07-30T15:00:00.2Z",
    "2026-07-30T15:00:00-05:00", "2026-07-30T15:00:00+0500",
    "2026-07-30T15:00:00.123456Z",
    "2026-07-30 15:00:00", "2026-07-30T15:00:00",
    "2026", "2026-07", "+002026-07-30T00:00:00Z", "-000001-01-01T00:00:00Z",
    "2026-13-45T00:00:00Z", "2026-02-30", "2026-00-10", "2026-07-00",
    "1970-01-01T00:00:00Z", "1969-12-31T23:59:59Z",
    "30/07/2026", "Jul 30 2026", "ayer", "2026-07-30T25:00:00Z",
]

#: Formatos que la ECMA-262 declara *implementation-defined*: todo lo que no
#: encaja en su Date Time String Format lo resuelve cada motor a su manera. V8
#: los interpreta con heurísticas propias (`Date.parse("500")` es el año 500,
#: `Date.parse("$5")` es mayo de 2001) y el port devuelve `NaN`. No es una
#: diferencia que haya que cerrar: replicarlas sería copiar una peculiaridad de
#: V8, no la lógica de Víctor.
LEGACY_DE_V8 = [
    "500", "205.5", "  500  ", ".5", "5.", "+5", "-5", "0", "-0", "5%", "$5",
    "\t7\n", "1,000", "1 000", "12abc", "1e3", "1E3", "1e-3",
    "Jul 30 2026", "30/07/2026", "2026-02-30", "2026-00-10", "2026-07-00",
    "true", "false", "0x1A", "0X1a", "0o17", "0b101",
    "Infinity", "-Infinity", "+Infinity", "infinity", "INFINITY",
    "inf", "-inf", "nan", "NaN", "NAN", "1_0", "1_000", "12_3.4", "1_0.5",
]

if __name__ == "__main__":
    print(json.dumps({"casos": casos, "legacy": LEGACY_DE_V8}))
