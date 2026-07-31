"""Diferencial con clasificación: separa lo que es solo TIPO de lo que es VALOR."""
import json, math, sys
sys.path.insert(0,".")
from wbj.tito.compute import to_row, contract_price
casos=json.load(open("/tmp/casos.json")); vic=json.load(open("/tmp/victor_out.json"))

def jsnum(v):
    """Number(v) de JS: lo que hace la aritmética con el valor crudo de Víctor."""
    if v is True: return 1.0
    if v is False: return 0.0
    if v is None: return 0.0
    if isinstance(v,(int,float)): return float(v)
    if isinstance(v,str):
        s=v.strip()
        if s=="": return 0.0
        try: return float(s)
        except ValueError: return float("nan")
    if isinstance(v,list) and not v: return 0.0
    return float("nan")

CATS={}
def cat(nombre, i, s, m, c): CATS.setdefault(nombre,[]).append((i,s,m,c))

for i,(c,v) in enumerate(zip(casos,vic)):
    r=to_row(c); vr=v["row"]
    # ── contract_type
    if r.contract_type != vr["contractType"]:
        raw=(c.get("details") or {}) if isinstance(c.get("details"),dict) else {}
        cat("DECLARADA · case del tipo" if str(raw.get("contract_type") or "").lower()=="put"
            else "REAL · tipo de contrato", i, vr["contractType"], r.contract_type, c)
    # ── expiration
    if r.expiration != vr["expiration"]:
        ve=vr["expiration"]
        if isinstance(ve,str) and ve[:10]==r.expiration: cat("DECLARADA · vencimiento canónico",i,ve,r.expiration,c)
        elif str(ve)==r.expiration: cat("TIPO · vencimiento no-string",i,ve,r.expiration,c)
        else: cat("REAL · vencimiento",i,ve,r.expiration,c)
    # ── numéricos: comparar por VALOR usando la coacción de JS
    for campo, mio, suyo in (("openInterest",r.open_interest,vr["openInterest"]),
                             ("volume",r.volume,vr["volume"]),
                             ("strike",r.strike,vr["strike"])):
        n=jsnum(suyo)
        if not math.isnan(n) and n < 0 and mio == 0:
            cat("DECLARADA · negativo → fallback",i,suyo,mio,c); continue
        if math.isnan(n):
            if mio==0: cat("DECLARADA · basura → fallback",i,suyo,mio,c)
            else: cat(f"REAL · {campo}",i,suyo,mio,c)
        elif not math.isfinite(n):
            if mio==0: cat("DECLARADA · no finito → fallback",i,suyo,mio,c)
            else: cat(f"REAL · {campo}",i,suyo,mio,c)
        elif abs(n-mio)>1e-9:
            if campo in ("openInterest","volume") and int(n)==mio:
                cat("DECLARADA · OI/volumen entero",i,suyo,mio,c)
            elif isinstance(suyo,bool): cat("BOOLEANO · JS lo trata como 0/1",i,suyo,mio,c)
            else: cat(f"REAL · {campo}",i,suyo,mio,c)
    # ── derivados
    for campo, mio, suyo in (("notionalValue",r.notional_value,vr["notionalValue"]),
                             ("openPremium",r.open_premium,vr["openPremium"])):
        s = float("nan") if suyo=="NaN" else (None if suyo is None else jsnum(suyo))
        if suyo=="NaN" or (isinstance(s,float) and math.isnan(s)):
            if mio in (0,0.0,None): cat("DECLARADA · derivado NaN → 0",i,suyo,mio,c)
            else: cat(f"REAL · {campo}",i,suyo,mio,c)
        elif s is None or mio is None:
            if not (s is None and mio is None): cat(f"REAL · {campo} None",i,suyo,mio,c)
        elif abs(s-(mio or 0))>1e-6:
            det = c.get("details") if isinstance(c.get("details"),dict) else {}
            _neg = any(isinstance(x,(int,float)) and not isinstance(x,bool) and x < 0
                       for x in (c.get("open_interest"), det.get("strike_price"),
                                 det.get("shares_per_contract"))) or any(
                   isinstance(x,str) and x.strip().startswith("-")
                   for x in (c.get("open_interest"), det.get("strike_price"),
                             det.get("shares_per_contract")) )
            if _neg:
                cat("DECLARADA · negativo → fallback",i,suyo,mio,c); continue
            crudo_oi = c.get("open_interest")
            def _frac(x):
                if isinstance(x,bool) or x is None: return False
                try: n=float(x if isinstance(x,(int,float)) else str(x).strip())
                except (TypeError,ValueError): return False
                return n==n and n!=int(n)
            if _frac(crudo_oi):
                cat("DECLARADA · OI fraccionario → entero",i,suyo,mio,c)
            elif isinstance(crudo_oi,bool) or isinstance(det.get("strike_price"),bool) \
               or isinstance(det.get("shares_per_contract"),bool):
                cat("BOOLEANO · JS lo trata como 0/1",i,suyo,mio,c)
            else: cat(f"REAL · {campo}",i,suyo,mio,c)

print(f"  {len(casos)} casos · clasificación de TODAS las diferencias\n")
for k in sorted(CATS, key=lambda x:(not x.startswith("REAL"), -len(CATS[x]))):
    marca = "✗" if k.startswith("REAL") else "·"
    print(f"  {marca} {k:<38} {len(CATS[k]):>4}")
    if k.startswith("REAL") or k.startswith("BOOLEANO"):
        for i,s,m,c in CATS[k][:3]:
            print(f"      #{i:<3} víctor={s!r:<16} port={m!r:<16} {json.dumps(c)[:74]}")
reales=sum(len(v) for k,v in CATS.items() if k.startswith("REAL"))
print(f"\n  {'SIN diferencias reales' if not reales else str(reales)+' DIFERENCIAS REALES'}")
