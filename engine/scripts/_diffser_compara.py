"""Comparador del diferencial de SERIES.

Tres preguntas, en este orden, porque cada una es más fuerte que la anterior:

1. **¿El archivo es el mismo?** Los dos lados guardan los mismos casos y se
   comparan los archivos crudos del disco, clave a clave. Aquí caen las
   diferencias de nombre (`avg_iv` contra `avgIv`), de forma (lista pelada
   contra sobre), de orden y de recorte.
2. **¿SU app puede leer el archivo del port?** Su `loadIvHistory` y compañía
   devuelven `null` ante cualquier cosa que no reconozcan. Un formato
   "parecido" que él rechaza deja el IV Rank en el proxy PARA SIEMPRE sin un
   solo error en el log: es exactamente el fallo que no se ve.
3. **¿El port puede leer el archivo de su app?** El mismo círculo al revés. Es
   lo que hace que el historial sobreviva a moverse de un lado al otro, que es
   para lo que existe todo esto.

Como en `_diffstore_compare.py`, la comprobación va en las DOS direcciones: un
caso que difiere sin declararlo es una divergencia nueva sin querer, y un caso
que la declara y no difiere es una guarda que desapareció en silencio. Las dos
fallan el script.
"""
import json
import os
import sys

casos = json.load(open(os.environ["SER_CASOS"]))
v_esc = json.load(open(os.environ["SER_V_OUT"]))
p_esc = json.load(open(os.environ["SER_PY_OUT"]))
v_lee = json.load(open(os.environ["SER_V_LEE"]))
p_lee = json.load(open(os.environ["SER_PY_LEE"]))

BLOQUES = ("chain", "iv", "predictions")
#: Cuántos días guarda cada serie, para el mensaje del recorte.
TOPE = {"chain": 45, "iv": 365, "predictions": 120}


def dif(a, b, ruta=""):
    """Diferencias hoja a hoja. Devuelve [(ruta, suyo, mío)]."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append((f"{ruta}.{k}", "<ausente>", b[k]))
            elif k not in b:
                out.append((f"{ruta}.{k}", a[k], "<ausente>"))
            else:
                out += dif(a[k], b[k], f"{ruta}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [(f"{ruta}[]", f"{len(a)} filas", f"{len(b)} filas")]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += dif(x, y, f"{ruta}[{i}]")
        return out
    return [] if a == b else [(ruta or ".", a, b)]


declarada = {}
for bloque in BLOQUES:
    for c in casos[bloque]:
        if c.get("divergencia"):
            declarada[(bloque, c["ticker"])] = c["divergencia"]


def compara(nombre, suyo, campo_a, mio, campo_b, con_divergencias):
    """Devuelve (iguales, inesperadas, declaradas, guardas_mudas).

    `con_divergencias` distingue las dos clases de comprobación. Comparar dos
    archivos ESCRITOS por lados distintos admite divergencias declaradas (el
    dedupe del diario es una). Comparar dos LECTURAS del MISMO archivo no
    admite ninguna: si dos lectores ven cosas distintas en el mismo disco, eso
    es un fallo y nunca una decisión de diseño.
    """
    iguales, inesperadas, decl, mudas = 0, [], {}, []
    for bloque in BLOQUES:
        a_l, b_l = suyo[bloque], mio[bloque]
        assert len(a_l) == len(b_l) == len(casos[bloque]), (
            f"{nombre}/{bloque}: {len(a_l)} / {len(b_l)} / {len(casos[bloque])}")
        for a, b, c in zip(a_l, b_l, casos[bloque]):
            assert a["ticker"] == b["ticker"] == c["ticker"], (a, b, c)
            ds = dif(a[campo_a], b[campo_b])
            esperada = declarada.get((bloque, c["ticker"])) if con_divergencias else None
            if not ds:
                if esperada:
                    mudas.append((bloque, c["ticker"], esperada))
                else:
                    iguales += 1
            elif esperada:
                decl.setdefault(esperada, []).append((bloque, c["ticker"], ds))
            else:
                inesperadas.append((bloque, c["ticker"], ds))
    return iguales, inesperadas, decl, mudas


def informe(nombre, suyo, campo_a, mio, campo_b, con_divergencias=False):
    iguales, inesperadas, decl, mudas = compara(
        nombre, suyo, campo_a, mio, campo_b, con_divergencias)
    total = sum(len(casos[b]) for b in BLOQUES)
    print(f"\n  ── {nombre}")
    for bloque, ticker, ds in inesperadas:
        print("  " + "=" * 70)
        print(f"  ✗ DIVERGENCIA NO DECLARADA · {bloque}/{ticker}")
        for ruta, x, y in ds[:8]:
            print(f"      {ruta}")
            print(f"        víctor: {json.dumps(x, sort_keys=True)[:200]}")
            print(f"        port  : {json.dumps(y, sort_keys=True)[:200]}")
        if len(ds) > 8:
            print(f"      … y {len(ds) - 8} diferencia(s) más")
    if decl:
        for g in sorted(decl):
            ts = ", ".join(f"{b}/{t}" for b, t, _ in decl[g])
            print(f"      · declarada: {g}  ({ts})")
    for bloque, ticker, g in mudas:
        print(f"  ✗ GUARDA MUDA · {bloque}/{ticker} declara «{g}» y NO difiere")
    n_decl = sum(len(x) for x in decl.values())
    print(f"      {iguales}/{total} idénticos"
          + (f" · {n_decl} divergencia(s) deliberada(s)" if n_decl else "")
          + (f" · {len(inesperadas)} SIN DECLARAR" if inesperadas else ""))
    return bool(inesperadas or mudas)


def como_visto(salida):
    """Un `archivo` puesto en la forma de un `visto`, para poder comparar una
    lectura contra el archivo del que salió."""
    return {b: [{"ticker": x["ticker"],
                 "visto": None if x["archivo"] is None else {
                     "ticker": x["archivo"].get("ticker"),
                     "updatedAt": x["archivo"].get("updatedAt"),
                     "snapshots": x["archivo"].get("snapshots")}}
                for x in salida[b]] for b in BLOQUES}


mal = False

# 1) Dos escrituras. Aquí —y solo aquí— caben las divergencias declaradas.
mal |= informe("los dos escriben el mismo archivo",
               v_esc, "archivo", p_esc, "archivo", con_divergencias=True)

# 2) y 3) Dos lecturas del MISMO archivo. Cada una compara contra el archivo
# del que salió, no contra la otra: así una diferencia señala al lector y no a
# una mezcla de los dos árboles.
mal |= informe("SU TypeScript abre el archivo del PORT y ve lo mismo",
               v_lee, "visto", como_visto(p_esc), "visto")
mal |= informe("el PORT abre el archivo de SU app y ve lo mismo",
               como_visto(v_esc), "visto", p_lee, "visto")

# Un caso que no escribió NADA en los dos lados pasa las tres pruebas sin haber
# probado nada. Se cuenta aparte para que no se cuele un runner roto que deja
# el disco vacío y sale verde.
escritos = sum(1 for b in BLOQUES for x in p_esc[b] if x["archivo"] is not None)
vacios = sum(1 for b in BLOQUES for x in p_esc[b] if x["archivo"] is None)
filas = sum(len(x["archivo"]["snapshots"]) for b in BLOQUES for x in p_esc[b]
            if x["archivo"] and isinstance(x["archivo"].get("snapshots"), list))
print(f"\n  {escritos} archivo(s) escritos · {filas} fotos · {vacios} sin escribir"
      f" (el caso sin IV y el que no llegó a guardar)")
if escritos == 0 or filas == 0:
    print("  ✗ el diferencial no escribió nada: no probó nada")
    mal = True

# Los topes se prueban de verdad solo si algún caso los cruza.
for bloque in BLOQUES:
    tope = TOPE[bloque]
    if not any(x["archivo"] and len(x["archivo"].get("snapshots", [])) == tope
               for x in p_esc[bloque]):
        print(f"  ✗ ningún caso de '{bloque}' llegó al tope de {tope}:"
              " el recorte no se está probando")
        mal = True

sys.exit(1 if mal else 0)
