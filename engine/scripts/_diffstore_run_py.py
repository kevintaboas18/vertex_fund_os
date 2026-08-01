"""Lado del port en el diferencial de `store.ts`: mismos casos, `wbj.tito.stores`.

Espeja `_diffstore_run.mjs` campo por campo. Las dos únicas concesiones son de
lenguaje y están marcadas:

- `undefined` no existe en Python, así que el lado JS ya normaliza a `null`.
- `===` distingue `true` de `1` y `dict` no: donde el harness compara un id se
  descarta el bool a mano, para no inventar una diferencia que el port no tiene.
"""
import json, os, shutil, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
D = os.environ.get("ST_DIR") or tempfile.mkdtemp()
os.environ["WBJ_TITO_DATA"] = D
from wbj.tito import stores as ST  # noqa: E402

RE_ISO_MS = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def T(d, h=15, m=0):
    return f"2026-07-{d:02d}T{h:02d}:{m:02d}:00Z"


def lote(n):
    return [{"id": i + 1, "timestamp": T(1 + (i % 30), 10, i % 60), "symbol": "X",
             "type": "call", "strike": 100, "expiration": "2026-12-18",
             "assetPrice": 95, "premium": 5e5, "aggression": "ask",
             "expiry_status": "vigente"} for i in range(n)]


def forma(v):
    """Forma del `updatedAt`, no su valor (es la hora de pared)."""
    if isinstance(v, str):
        return "ISO_MS_Z" if RE_ISO_MS.match(v) else "otro:" + v
    return v


def es_id_1(t):
    """`t && t.id === 1` — el `===` no acepta `True`."""
    if not isinstance(t, dict):
        return False
    i = t.get("id")
    return i == 1 and not isinstance(i, bool)


casos = json.load(open(os.environ["ST_CASOS"]))
out = []
for c in casos:
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(os.path.join(D, "trades"), exist_ok=True)
    r = {"caso": c["nombre"]}
    try:
        if "raw" in c:
            with open(os.path.join(D, "trades", c["rawFile"]), "w", encoding="utf-8") as fh:
                fh.write(c["raw"])
        if c["op"] == "load":
            v = ST.load_trades(c["ticker"])
            r["res"] = None if v is None else {
                "ticker": v.ticker,
                "updatedAt": v.updated_at,
                "n": len(v.trades) if isinstance(v.trades, list) else None,
                "ids": [(t.get("id") if isinstance(t, dict) else None) for t in v.trades]
                       if isinstance(v.trades, list) else None,
            }
        else:
            rows = lote(c["n"]) if c["op"] == "saveN" else c["rows"]
            s1 = ST.save_trades(c["ticker"], rows)
            s2 = ST.save_trades(c["ticker"], c["rows2"]) if c["op"] == "save2" else None
            v = ST.load_trades(c["ticker"])
            res = lambda s: s and {"total": s.total, "added": s.added, "firstSeen": s.first_seen}
            r["res"] = {
                "s1": res(s1), "s2": res(s2),
                "ticker": v and v.ticker,
                "updatedAt": v and forma(v.updated_at),
                "ids": v and [(t.get("id") if isinstance(t, dict) else None)
                              for t in v.trades[:8]],
                "n": v and len(v.trades),
                "estado": v and next((t.get("expiry_status") for t in v.trades
                                      if es_id_1(t)), None),
            }
            td = os.path.join(D, "trades")
            r["archivos"] = sorted(f for f in os.listdir(td) if not f.endswith(".lock"))
            r["claves"] = (sorted(json.load(open(os.path.join(td, r["archivos"][0])))
                                  .keys()) if r["archivos"] else None)
    except Exception as e:                       # noqa: BLE001 — se compara la clase
        r["res"] = {"ERROR": type(e).__name__}
    out.append(r)

with open(os.environ["ST_PY_OUT"], "w", encoding="utf-8") as fh:
    json.dump(out, fh)
print(f"  port procesó {len(out)} casos")
