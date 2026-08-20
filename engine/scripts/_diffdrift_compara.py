"""Compara `wbj/tito/drift.py` contra SU `drift_sentiment/`, número a número.

Se ejecuta SU Python de verdad —no una idea de lo que hace— sobre las mismas
cadenas y se exige que los muros, el imán y la clasificación coincidan.

Existe porque el port se rompió una vez sin que nadie lo notara: se
«arreglaron» los muros para que miraran el lado del precio, que suena
razonable y no es lo que él hace.

    DRIFT_ROOT=/ruta/a/drift-sentiment-agent python engine/scripts/_diffdrift_compara.py
"""

from __future__ import annotations

import math
import os
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

SUYO = os.environ.get("DRIFT_ROOT", "").strip()
if not SUYO or not Path(SUYO).is_dir():
    print("DRIFT_ROOT no apunta a un clon de drift-sentiment-agent.")
    print("  git clone https://github.com/infusionvictor/drift-sentiment-agent")
    print("  DRIFT_ROOT=… python engine/scripts/_diffdrift_compara.py")
    raise SystemExit(2)
sys.path.insert(0, SUYO)

from drift_sentiment import chain_filter as su_filtro          # noqa: E402
from drift_sentiment import drift as su_drift                  # noqa: E402
from drift_sentiment import magneto as su_magneto              # noqa: E402
from drift_sentiment import walls as su_walls                  # noqa: E402
from drift_sentiment.models import Contract, Wall              # noqa: E402

from wbj.tito.drift import (                                   # noqa: E402
    DTE_OBJETIVO, _a_filas, clasifica_deriva, es_mensual, magneto,
    muro_calls, muro_puts, vencimiento_mas_cercano,
)
from wbj.tito.structure import ChainRow                        # noqa: E402

HOY = date(2026, 8, 20)

#: Cadenas de prueba. La segunda es la que destapó la divergencia: mayor OI de
#: calls DENTRO del dinero y de puts fuera, que invierte los muros.
def _casos():
    yield "normal", 100.0, [
        (k, "call", 1500 if k == 110 else 300) for k in range(80, 131, 5)
    ] + [(k, "put", 1200 if k == 90 else 250) for k in range(80, 131, 5)]

    yield "muros invertidos", 180.0, [
        (k, "call", 14000 if k == 120 else (6000 if k == 200 else 500))
        for k in range(100, 261, 10)
    ] + [(k, "put", 11000 if k == 230 else (7000 if k == 160 else 500))
         for k in range(100, 261, 10)]

    yield "un solo strike", 50.0, [(50, "call", 10), (50, "put", 10)]

    yield "sin puts", 100.0, [(k, "call", 400) for k in range(90, 121, 5)]

    yield "cero interes", 100.0, [(k, t, 0) for k in (90, 100, 110)
                                  for t in ("call", "put")]


VENCS = ("2026-09-18", "2026-11-20", "2026-12-18", "2027-07-16")

#: Divergencias DECLARADAS, con su motivo. Mismo contrato que
#: `_diffmotor_base.json`: si aparece una que no está aquí, el diferencial
#: falla. Declarar no es esconder — es que la diferencia esté escrita.
DECLARADAS = {
    # Él no filtra por interés abierto, así que en una cadena entera con OI 0
    # publica «muro de calls: $90 (OI 0)» y un imán con nocional 0. Aquí esos
    # contratos se descartan en `_a_filas` y el plazo sale como «sin datos».
    #
    # Es deliberado y va en la dirección de la regla de la casa: sin evidencia
    # no hay número. Un muro con cero contratos abiertos no es un muro, y
    # pintarlo como si lo fuera es peor que decir que no hay dato.
    "cero interes",
}

#: La OTRA divergencia declarada, y la única que queda: la ruta pasa
#: `iman_entre_muros=True`, que acota el imán al rango de los dos muros. Él lo
#: busca en toda la cadena del vencimiento.
#:
#: El motivo está en su propia §6: «intra-range → el precio gravita hacia el
#: Magneto». Un imán FUERA del rango de los muros rompe esa frase — no se puede
#: gravitar hacia algo que está fuera de la banda que se acaba de declarar como
#: el rango. Medido: con la acción a $180 y los muros en 170/190, el mayor
#: nocional de la cadena estaba en un strike de $300, contratos muy dentro del
#: dinero que se van a ejercer.
#:
#: El diferencial compara SIN la banda a propósito —así mide su algoritmo
#: contra el port, que es su trabajo—; la banda se prueba aparte, en
#: `TestElIMANDentroDeLosMUROS` y en `test_el_iman_se_ACOTA_cuando_de_verdad_se_iria_fuera`.
#:
#: Y lo que NO hay: ninguna ventana de strikes. El ±20% que se probó no está en
#: su Drift —ni en `walls.py`, ni en la especificación §4-§5, ni en el README,
#: ni en su `polygon_client`, que baja la cadena entera— y se retiró.
IMAN_ACOTADO_ES_DE_VERTEX = True

fallos: list[str] = []
declaradas: list[str] = []


def igual(que, a, b):
    if isinstance(a, float) and isinstance(b, float):
        ok = math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    else:
        ok = a == b
    if not ok:
        linea = f"{que}: nuestro {a!r} vs suyo {b!r}"
        caso = que.split("]")[0].lstrip("[")
        (declaradas if caso in DECLARADAS else fallos).append(linea)
    return ok


for nombre, spot, filas in _casos():
    nuestras, suyas = [], []
    for exp in VENCS:
        for k, t, oi in filas:
            nuestras.append(ChainRow(t, exp, float(k), oi, 0, float(k) * oi * 100))
            suyas.append(Contract(strike=float(k), expiration=date.fromisoformat(exp),
                                  contract_type=t, open_interest=oi))
    mias = _a_filas(nuestras, HOY)

    # 1. Los muros
    su_cw, su_pw = su_walls.call_wall(suyas), su_walls.put_wall(suyas)
    mi_cw, mi_pw = muro_calls(mias), muro_puts(mias)
    igual(f"[{nombre}] muro de calls", mi_cw,
          (su_cw.strike, su_cw.open_interest) if su_cw else None)
    igual(f"[{nombre}] muro de puts", mi_pw,
          (su_pw.strike, su_pw.open_interest) if su_pw else None)

    # 2. El imán
    su_mag, mi_mag = su_magneto.magneto(suyas), magneto(mias)
    if su_mag is None or mi_mag is None:
        igual(f"[{nombre}] imán (ausencia)", mi_mag is None, su_mag is None)
    else:
        igual(f"[{nombre}] imán · strike", mi_mag[0], su_mag[0])
        igual(f"[{nombre}] imán · nocional", float(mi_mag[1]), float(su_mag[1]))

    # 3. La clasificación
    if mi_cw and mi_pw and mi_mag and su_cw and su_pw and su_mag:
        _, su_rup = su_drift.classify_drift(
            spot, Wall(strike=su_cw.strike, open_interest=su_cw.open_interest),
            Wall(strike=su_pw.strike, open_interest=su_pw.open_interest),
            su_mag[0], su_mag[1])
        _, mi_rup = clasifica_deriva(spot, mi_cw[0], mi_pw[0], mi_mag[0], mi_mag[1])
        igual(f"[{nombre}] ruptura", mi_rup, su_rup)

    # 4. Los mensuales y el vencimiento elegido
    su_mens = su_filtro.monthly_expirations(suyas)
    igual(f"[{nombre}] mensuales", sorted({f.vencimiento for f in mias if es_mensual(f.vencimiento)}),
          su_mens)
    for _, objetivo in DTE_OBJETIVO:
        igual(f"[{nombre}] vencimiento para {objetivo} DTE",
              vencimiento_mas_cercano(su_mens, objetivo, HOY),
              su_filtro.nearest_expiration(su_mens, objetivo, HOY))

# 5. Los plazos declarados
igual("plazos", [(s, d) for s, d in DTE_OBJETIVO],
      [("Largo" if s == "Long" else "Corto", d) for s, d in su_filtro.DTE_TARGETS])

if declaradas:
    print(f"\n{len(declaradas)} divergencia(s) DECLARADAS (esperadas, con motivo "
          f"en `DECLARADAS`):")
    for d in declaradas:
        print(f"  · {d}")
if fallos:
    print(f"\n\033[31m{len(fallos)} divergencia(s) NO declarada(s):\033[0m")
    for f in fallos:
        print(f"  ✗ {f}")
    raise SystemExit(1)
print(f"\n\033[32mdiff_drift: 0 divergencias sin declarar con "
      f"drift-sentiment-agent\033[0m")
