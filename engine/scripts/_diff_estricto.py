"""Comparador del diferencial de `compute.ts`.

No pregunta "¿es idéntico?" sino "¿difiere SOLO donde dijimos que difiere?".
Cada diferencia cae en una de tres cajas y solo la tercera es un fallo:

1. **Muro del lenguaje** — el mismo número en otro tipo de campo. Él conserva el
   crudo (`openInterest: "500"`); aquí va el número, porque el resto del motor
   suma esa columna y en Python un string no se suma.
2. **Divergencia declarada** — una de las guardas del port, con su causa
   identificada a partir del contrato crudo. Solo se disparan con datos
   malformados: con datos bien formados este comparador da 0 en esta caja.
3. **Sin declarar** — cualquier otra cosa. Sale con código 1.

Lo que se compara es la salida OBSERVABLE, o sea la fila ya serializada: en su
lado sale por `Response.json`, así que `JSON.stringify` convierte `NaN` e
`Infinity` en `null`.
"""
import json, math, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from wbj.tito.compute import _js_string, to_row  # noqa: E402

casos = json.load(open(os.environ["DIFF_CASOS"]))
vic = json.load(open(os.environ["DIFF_VICTOR_OUT"]))

CAMPOS = [("optionTicker", "option_ticker"), ("contractType", "contract_type"),
          ("expiration", "expiration"), ("strike", "strike"),
          ("openInterest", "open_interest"), ("volume", "volume"),
          ("price", "price"), ("priceSource", "price_source"),
          ("openPremium", "open_premium"), ("notionalValue", "notional_value")]


def stringify(v):
    """`JSON.stringify` de un escalar: los no finitos se vuelven `null`."""
    if v in ("NaN", "Infinity", "-Infinity"):       # el runner ya los marcó
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def js_number(v):
    """`Number(v)`, solo para saber si dos valores son el MISMO número.

    Se aplica sobre el valor CRUDO, antes de serializar: su campo puede traer
    `"abc"` o `{}` y el del port el `NaN` correspondiente. `Number` de los dos
    es `NaN`, o sea el mismo número, y eso es lo que hay que ver.
    """
    if v in ("NaN", "Infinity", "-Infinity"):          # marcado por el runner
        return math.nan if v == "NaN" else math.inf * (1 if v[0] != "-" else -1)
    if v is None or v == "" or v == []:
        return 0.0                                     # Number(null/""/[]) === 0
    if v is True:
        return 1.0
    if v is False:
        return 0.0
    if isinstance(v, (dict, list)):
        return math.nan
    try:
        n = float(v)
    except (TypeError, ValueError):
        return math.nan
    return n if not isinstance(v, str) or math.isfinite(n) else math.nan


#: Campos donde el crudo de él puede ser un string/bool que JS coacciona.
NUMERICOS = {"strike", "openInterest", "volume", "openPremium", "notionalValue", "price"}


def clasifica(campo, crudo, suyo, mio):
    """¿Qué guarda del port explica esta diferencia? `None` = no declarada."""
    det = crudo.get("details") if isinstance(crudo.get("details"), dict) else {}
    day = crudo.get("day") if isinstance(crudo.get("day"), dict) else {}
    lt = crudo.get("last_trade") if isinstance(crudo.get("last_trade"), dict) else {}
    fuente = {"openInterest": crudo.get("open_interest"),
              "strike": det.get("strike_price"),
              "volume": day.get("volume")}.get(campo)

    def negativo(x):
        return (isinstance(x, (int, float)) and not isinstance(x, bool) and x < 0) or \
               (isinstance(x, str) and x.strip().startswith("-"))

    def ilegible(x):
        return js_number(x) != js_number(x) or not math.isfinite(js_number(x))

    if campo == "contractType":
        return G_CASE if str(det.get("contract_type") or "").lower() == "put" else None
    if campo == "expiration":
        ve = suyo if isinstance(suyo, str) else str(suyo)
        return G_EXP if ve[:10] == mio or str(suyo) == mio else None
    if campo == "optionTicker":
        # La guarda es "el campo se declara str": mismo valor, en texto.
        return G_TICKER if _js_string(suyo) == mio else None
    if campo in ("openInterest", "strike", "volume"):
        if negativo(fuente):
            return G_NEG
        if ilegible(fuente):
            return G_ILEG
        n = js_number(suyo)
        if math.isfinite(n) and int(n) == mio:
            return G_ENTERO
        return None
    if campo in ("notionalValue", "openPremium"):
        entradas = (crudo.get("open_interest"), det.get("strike_price"),
                    det.get("shares_per_contract"), lt.get("price"),
                    day.get("close"), day.get("vwap"))
        if any(negativo(x) for x in entradas):
            return G_NEG
        if det.get("shares_per_contract") is not None and ilegible(det.get("shares_per_contract")):
            return G_SHARES
        if any(ilegible(x) for x in entradas[:3] if x is not None):
            return G_ILEG
        n = js_number(suyo)
        if not math.isfinite(n):
            return G_DESB
        if any(isinstance(x, (int, float)) and not isinstance(x, bool)
               and math.isinf(x) for x in entradas[3:]):
            return G_INF
        crudo_oi = crudo.get("open_interest")
        no = js_number(crudo_oi)
        if math.isfinite(no) and no != int(no):
            return G_ENTERO
        return None
    if campo == "price":
        return G_INF if not math.isfinite(js_number(suyo)) else None
    return None


G_CASE   = "case del tipo de contrato (\"PUT\" → put)"
G_NEG    = "negativo → fallback (un nocional que resta)"
G_ILEG   = "no numérico → fallback (NaN envenena la suma)"
G_SHARES = "shares ilegible → 0 (no inventar el multiplicador)"
G_DESB   = "producto desbordado → recortado"
G_INF    = "precio infinito → siguiente de la cascada"
G_EXP    = "vencimiento canónico YYYY-MM-DD"
G_TICKER = "ticker a str (el campo se declara str)"
G_ENTERO = "open interest / volumen a entero"

valor, tipo, ejemplos = {}, {}, {}
declaradas = {}

distintos = 0
for i, (c, v) in enumerate(zip(casos, vic)):
    if v["row"].get("ERROR"):
        continue
    r = to_row(c)
    dif = False
    for js, py in CAMPOS:
        suyo, mio = stringify(v["row"][js]), stringify(getattr(r, py))
        if isinstance(suyo, (int, float)) and isinstance(mio, (int, float)) \
                and not isinstance(suyo, bool) and not isinstance(mio, bool):
            igual = abs(float(suyo) - float(mio)) <= 1e-9 * max(1.0, abs(float(suyo)))
        else:
            igual = suyo == mio and type(suyo) is type(mio)
        if igual:
            continue
        # El muro del lenguaje solo existe en los campos NUMÉRICOS: es que él
        # conserve el crudo (`"500"`, `true`) donde aquí va el número. En un
        # campo de texto —tipo de contrato, vencimiento— una diferencia nunca es
        # de tipo, así que esos van directos a la clasificación por guarda.
        if js in NUMERICOS:
            a, b = js_number(v["row"][js]), js_number(getattr(r, py))
            if (a == b) or (a != a and b != b):
                tipo[js] = tipo.get(js, 0) + 1
                ejemplos.setdefault((js, "tipo"), (i, suyo, mio, c))
                continue
        g = clasifica(js, c, v["row"][js], getattr(r, py))
        if g:
            declaradas[g] = declaradas.get(g, 0) + 1
            ejemplos.setdefault((js, g), (i, suyo, mio, c))
            continue
        valor[js] = valor.get(js, 0) + 1
        ejemplos.setdefault((js, "valor"), (i, suyo, mio, c))
        dif = True
    if dif:
        distintos += 1

print(f"  {len(casos)} casos · comparación ESTRICTA (salida ya serializada)\n")
if tipo:
    print("  · MURO DEL LENGUAJE — mismo número, distinto tipo de campo")
    print("    (él deja el crudo: `\"500\"`, `true`; aquí va el número, porque")
    print("     el resto del motor suma ese campo y en Python un string no se suma)")
    for k, n in sorted(tipo.items(), key=lambda kv: -kv[1]):
        i, s, m, c = ejemplos[(k, "tipo")]
        print(f"      {k:<16} {n:>5}   ej #{i}: víctor={s!r} port={m!r}")
    print()
if declaradas:
    print("  · DIVERGENCIAS DECLARADAS — las guardas del port, funcionando")
    print("    (solo se disparan con datos malformados; con datos buenos, 0)")
    for k, n in sorted(declaradas.items(), key=lambda kv: -kv[1]):
        print(f"      {k:<48} {n:>5}")
    print()
for k, n in sorted(valor.items(), key=lambda kv: -kv[1]):
    i, s, m, c = ejemplos[(k, "valor")]
    print(f"  ✗ SIN DECLARAR · {k:<16} {n:>5}   ej #{i}: víctor={s!r} port={m!r}")
    print(f"     {json.dumps(c)[:100]}")
print(f"\n  {len(casos)-distintos}/{len(casos)} filas sin ninguna diferencia SIN DECLARAR"
      + (f" · {distintos} con una diferencia sin explicar" if distintos
         else " · toda divergencia está declarada"))
sys.exit(1 if distintos else 0)
