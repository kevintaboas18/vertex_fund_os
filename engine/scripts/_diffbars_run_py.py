import json, os, shutil, sys, tempfile
sys.path.insert(0, "/home/user/vertex_fund_os/engine")
from datetime import datetime
D = tempfile.mkdtemp(); os.environ["WBJ_TITO_DATA"] = D
from wbj.tito import bars_store as BS
from wbj.tito.levels import LvlBar

def B(n, base=100.0):
    return [LvlBar(time=f"2026-07-{1+i:02d}" if 1+i<=31 else f"2026-08-{1+i-31:02d}",
                   high=base+i+1, low=base+i-1, close=base+i) for i in range(n)]
def dt(s): return datetime.fromisoformat(s.replace("Z","+00:00"))

casos = json.load(open("/tmp/bs/casos.json")); out=[]
for c in casos:
    shutil.rmtree(D, ignore_errors=True); os.makedirs(D, exist_ok=True)
    r = {"caso": c["nombre"]}
    try:
        if c.get("pre"):
            BS.save_bars(c["pre"]["ticker"], B(c["pre"]["n"]), dt(c["pre"]["now"]))
        if "raw" in c:
            p = os.path.join(D, "bars"); os.makedirs(p, exist_ok=True)
            open(os.path.join(p, c["rawFile"]), "w").write(c["raw"])
        if c["op"] == "load":
            v = BS.load_bars(c["ticker"])
            r["res"] = None if v is None else {k: x for k, x in
                (("ticker", v.ticker), ("date", v.date), ("n", len(v.bars or []))) if x is not None}
        elif c["op"] == "save":
            BS.save_bars(c["ticker"], B(c.get("n",3)), dt(c["now"]))
            v = BS.load_bars(c["ticker"])
            r["res"] = None if v is None else {k: x for k, x in
                (("ticker", v.ticker), ("date", v.date), ("n", len(v.bars or []))) if x is not None}
            bd = os.path.join(D,"bars")
            r["archivos"] = sorted(f for f in os.listdir(bd) if not f.endswith(".lock")) if os.path.isdir(bd) else []
        elif c["op"] == "cached":
            pedidos=[]
            def fetch(t, d):
                pedidos.append([t,d])
                if c.get("falla"): raise RuntimeError("red")
                return B(c.get("n",3))
            bars = BS.cached_daily_bars(c["ticker"], c.get("days",365), dt(c["now"]), fetch=fetch)
            r["res"] = {"n": len(bars), "pedidos": pedidos}
    except Exception as e:
        r["res"] = {"ERROR": type(e).__name__}
    out.append(r)
json.dump(out, open("/tmp/bs/py_out.json","w"))
print(f"  port procesó {len(out)} casos")
