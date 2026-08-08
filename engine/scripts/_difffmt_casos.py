"""Casos del diferencial de `format.ts` — lo que el usuario LEE en pantalla.

`format.ts` es el único módulo suyo que no vive en `web/lib`, y por eso se
quedó fuera de los tres registros de la auditoría —módulos, rutas y
componentes— durante siete rondas. Nadie lo comparó nunca. Cuando por fin se
comparó, el `fmtAbbr` del panel divergía en **14 de 14** casos: dos decimales
contra uno, el signo menos del lado equivocado y sin escalón "T".

Es el tipo de fallo que no rompe nada y se ve en cada pantalla: los números del
motor eran correctos y se pintaban distintos de los suyos.

Deterministas (semilla fija), como los demás diferenciales.
"""
import json
import random


def _numeros():
    """Los valores que de verdad pasan por estos formateadores."""
    fijos = [
        # Fronteras de la notación compacta: es donde `toFixed(1)` y `Intl`
        # dejan de coincidir, y donde estaban 14 de las 14 divergencias.
        0, 1, -1, 999, 999.4, 999.5, 999.6, 1000, 1001, 1234, 9999, 10_000,
        99_999, 100_000, 999_499, 999_500, 999_999, 1_000_000, 1_004_999,
        1_005_000, 2_400_000, 2_440_000, 2_444_999, 2_445_000,
        999_999_999, 1_000_000_000, 1_500_000_000, 987_654_321,
        999_999_999_999, 1e12, 1.5e12, 1e15,
        # Negativos: su `Intl` pone "-$2.44M" y una resta a mano pone "$-2.44M".
        -0.4, -999, -1000, -2_440_000, -1.5e9,
        # Decimales pequeños, que es lo que llega en un precio o una griega.
        0.001, 0.004, 0.005, 0.01, 0.5, 1.005, 1.015, 2.675, 12.345, 99.995,
        # Lo que no es un número y aun así llega: una API que cambió de esquema.
        None, float("nan"), float("inf"), float("-inf"),
    ]
    rng = random.Random(1907)
    aleatorios = []
    for _ in range(220):
        exp = rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12])
        v = rng.uniform(0, 10) * (10 ** exp)
        aleatorios.append(round(v * rng.choice([1, -1]), 6))
    return fijos + aleatorios


def _instantes():
    """Timestamps para `timeOf` / `dateOf` / `timeET` / `dateET`."""
    fijos = [
        "2026-08-07T15:30:00Z",       # sesión abierta
        "2026-08-07T13:29:59Z",       # un minuto antes de la apertura
        "2026-08-07T20:00:00Z",       # cierre
        "2026-08-07T23:59:59Z",       # tras el cierre: sigue siendo el día 7 en ET
        "2026-08-08T00:00:00Z",       # medianoche UTC: en ET todavía es el 7
        "2026-08-08T03:59:59Z",
        "2026-01-15T14:30:00Z",       # invierno: EST, no EDT
        "2026-03-08T06:59:00Z",       # el fin de semana del cambio de hora
        "2026-03-08T07:01:00Z",
        "2026-11-01T05:30:00Z",
        "2026-12-31T23:00:00Z",       # cambio de año en UTC, no en ET
        "2026-08-07T15:30:00.123Z",
        "2026-08-07T15:30:00+00:00",
        "",                           # lo que llega cuando el campo no llegó
        "no-es-una-fecha",
    ]
    rng = random.Random(2607)
    for _ in range(90):
        mes, dia = rng.randrange(1, 13), rng.randrange(1, 29)
        h, m, s = rng.randrange(0, 24), rng.randrange(0, 60), rng.randrange(0, 60)
        fijos.append(f"2026-{mes:02d}-{dia:02d}T{h:02d}:{m:02d}:{s:02d}Z")
    return fijos


def _unix():
    """Segundos UNIX para `hmET`, que es lo que recibe de los racimos."""
    fijos = [0, 1, 1_770_000_000, 1_785_000_000, 1_800_000_000]
    rng = random.Random(3107)
    return fijos + [rng.randrange(1_760_000_000, 1_800_000_000) for _ in range(90)]


def casos():
    return {"numeros": _numeros(), "instantes": _instantes(), "unix": _unix()}


if __name__ == "__main__":
    # `NaN`/`Infinity` viajan como texto: JSON no los tiene y `JSON.parse` de
    # Node los rechazaría. Los dos lados los reconstruyen igual.
    def limpio(v):
        if isinstance(v, float):
            if v != v:
                return "NaN"
            if v == float("inf"):
                return "Infinity"
            if v == float("-inf"):
                return "-Infinity"
        return v

    c = casos()
    c["numeros"] = [limpio(v) for v in c["numeros"]]
    print(json.dumps(c))
