"""Casos del diferencial de `store.ts`: se ejecutan contra SU archivo y contra el port."""
import json

def fr(i, ts, **kw):
    d = dict(id=i, symbol="X", underlying="X", type="call", strike=100.0,
             expiration="2026-12-18", dte=50, price=2.0, size=10, side="AT_ASK",
             aggression="ask", asset_price=95.0, bid=1.9, ask=2.1, premium=500000.0,
             delta=0.6, gamma=0.03, theta=-0.05, vega=0.1, theta_pct_daily=2.5,
             iv=0.45, open_interest=1000, volume=500, score=8, sentiment="bullish",
             timestamp=ts, condition_code=None, condition_name=None,
             expiry_status="vigente")
    d.update(kw); return d
T = lambda d, h=15, m=0: f"2026-07-{d:02d}T{h:02d}:{m:02d}:00Z"
casos = []
def c(**k): casos.append(k)

# Las tres guardas del port (divergencias deliberadas). Cada caso que difiera de
# su store.ts tiene que declarar CUÁL, y cada guarda declarada tiene que
# producir de verdad una diferencia: así el diferencial pilla tanto una
# divergencia nueva sin querer como una guarda que desapareció en silencio.
G1 = "G1 · ticker que no da nombre de archivo"
G2 = "G2 · dedupe sin id"
G3 = "G3 · fila corrupta en disco"

# ── load ──
c(nombre="load sin historial", op="load", ticker="NADA")
c(nombre="load json roto", op="load", ticker="R", raw="{no", rawFile="R.json")
c(nombre="load lista pelada", op="load", ticker="L", raw='[{"id":1}]', rawFile="L.json")
c(nombre="load sin campo trades", op="load", ticker="S", raw='{"ticker":"S"}', rawFile="S.json")
c(nombre="load trades no lista", op="load", ticker="N", raw='{"ticker":"N","trades":"txt"}', rawFile="N.json")
c(nombre="load null", op="load", ticker="U", raw='null', rawFile="U.json")
c(nombre="load numero pelado", op="load", ticker="NU", raw='42', rawFile="NU.json")
c(nombre="load con basura entre trades", op="load", ticker="B", divergencia=G3,
  raw='{"ticker":"B","updatedAt":"x","trades":[{"id":1,"timestamp":"'+T(30)+'"},"basura",null,42]}',
  rawFile="B.json")
c(nombre="load sin ticker ni updatedAt", op="load", ticker="V",
  raw='{"trades":[{"id":1}]}', rawFile="V.json")
c(nombre="load ticker numerico", op="load", ticker="W",
  raw='{"ticker":7,"updatedAt":9,"trades":[]}', rawFile="W.json")

# ── save: contrato basico ──
c(nombre="save 2 nuevos", op="save", ticker="A", rows=[fr(1,T(28)), fr(2,T(30))])
c(nombre="save mismo lote dos veces", op="save2", ticker="A2",
  rows=[fr(1,T(28)), fr(2,T(30))], rows2=[fr(1,T(28)), fr(2,T(30))])
c(nombre="save added solo las nuevas", op="save2", ticker="A3",
  rows=[fr(1,T(28))], rows2=[fr(1,T(28)), fr(2,T(30))])
c(nombre="save el mas reciente gana", op="save2", ticker="A4",
  rows=[fr(1,T(28), expiry_status="vigente")],
  rows2=[fr(1,T(28), expiry_status="expirado")])
c(nombre="save orden descendente", op="save", ticker="A5",
  rows=[fr(1,T(28)), fr(2,T(30)), fr(3,T(29))])
c(nombre="save firstSeen el mas viejo", op="save", ticker="A6",
  rows=[fr(1,T(28)), fr(2,T(30))])
c(nombre="save rows vacio", op="save", ticker="A7", rows=[])
c(nombre="save id duplicado en el mismo lote", op="save", ticker="A8",
  rows=[fr(1,T(28)), fr(1,T(28)), fr(2,T(30))])
c(nombre="save timestamps identicos", op="save", ticker="A9",
  rows=[fr(1,T(30)), fr(2,T(30)), fr(3,T(30))])

# ── save: tickers ──
for t in ("demo", " demo ", "DEMO", "brk.b", "brk/b", "!!!", "", "a"*70,
          "../../ETC/X", "a b", "ñ"):
    # Los que sanean a nada (o pasan del tope) los rechaza la guarda 1.
    _safe = "".join(ch for ch in t.strip().upper() if ch.isascii()
                    and (ch.isalnum() or ch in "._-"))
    _mal = not _safe.strip("._-") or len(_safe) > 64
    c(nombre=f"save ticker={t!r}", op="save", ticker=t, rows=[fr(1,T(30))],
      **({"divergencia": G1} if _mal else {}))

# ── save: valores raros ──
c(nombre="save id ausente (0)", op="save", ticker="Z1", divergencia=G2,
  rows=[fr(0,T(28)), fr(0,T(29))])
c(nombre="save timestamp vacio", op="save", ticker="Z2", rows=[fr(1,""), fr(2,T(30))])
c(nombre="save timestamp no parseable", op="save", ticker="Z3", rows=[fr(1,"ayer"), fr(2,T(30))])
c(nombre="save timestamp naive", op="save", ticker="Z4",
  rows=[fr(1,"2026-07-30T15:00:00"), fr(2,"2026-07-30T16:00:00Z")])
c(nombre="save NaN en un campo", op="save", ticker="Z5", rows=[fr(1,T(30), iv=None)])
c(nombre="save id negativo", op="save", ticker="Z6", rows=[fr(-5,T(30))])
c(nombre="save tope 5001", op="saveN", ticker="Z7", n=5001)
c(nombre="save tope exacto 5000", op="saveN", ticker="Z8", n=5000)

# El comparador con NaN: ECMA-262 lo trata como 0, o sea "iguales", y el sort
# estable deja las filas donde estaban. Con varias ilegibles mezcladas es donde
# se ve si el port reprodujo esa regla o las mandó al final.
c(nombre="save 10 timestamps ilegibles mezclados", op="save", ticker="Z9",
  rows=[fr(1,"ayer"), fr(2,T(28)), fr(3,""), fr(4,T(30)), fr(5,None),
        fr(6,T(29)), fr(7,"2026-13-45T00:00:00Z"), fr(8,12345), fr(9,T(27)),
        fr(10,"2026-07-30")])
c(nombre="save fechas solo dia vs fecha-hora", op="save", ticker="Z10",
  rows=[fr(1,"2026-07-30"), fr(2,"2026-07-30T00:00:00Z"), fr(3,"2026-07-29T23:00:00-05:00")])
c(nombre="save timestamp con milisegundos y offset", op="save", ticker="Z11",
  rows=[fr(1,"2026-07-30T15:00:00.250Z"), fr(2,"2026-07-30T10:00:00.100-05:00"),
        fr(3,"2026-07-30T15:00:00.500+00:00")])
c(nombre="save id no escalar", op="save", ticker="Z12",
  rows=[fr("a",T(30)), fr("b",T(29)), fr(True,T(28)), fr(False,T(27))])

# ── save sobre archivo corrupto ──
c(nombre="save sobre json roto", op="save", ticker="C1", rows=[fr(1,T(30))],
  raw="{no", rawFile="C1.json")
c(nombre="save sobre lista pelada", op="save", ticker="C2", rows=[fr(1,T(30))],
  raw='[{"id":9}]', rawFile="C2.json")
c(nombre="save sobre trades con basura", op="save", ticker="C3", rows=[fr(1,T(30))],
  divergencia=G3,
  raw='{"ticker":"C3","trades":[{"id":9,"timestamp":"'+T(20)+'"},"basura",null]}',
  rawFile="C3.json")
c(nombre="save sobre trades con string suelto", op="save", ticker="C4", rows=[fr(1,T(30))],
  divergencia=G3,
  raw='{"ticker":"C4","trades":[{"id":9,"timestamp":"'+T(20)+'"},"basura"]}',
  rawFile="C4.json")
c(nombre="save sobre archivo del port viejo (updated_at)", op="save", ticker="C5",
  rows=[fr(1,T(30))],
  raw='{"ticker":"C5","updated_at":"2026-07-31T00:00:00Z","trades":[{"id":9,"timestamp":"'+T(20)+'"}]}',
  rawFile="C5.json")

print(json.dumps(casos))
