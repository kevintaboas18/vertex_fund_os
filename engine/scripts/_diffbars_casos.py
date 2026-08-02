import json
HOY="2026-07-31T21:00:00Z"; AYER="2026-07-30T21:00:00Z"; SESION="2026-07-31T15:00:00Z"
casos=[]
def c(**k): casos.append(k)
# load
# El port de `barsStore.ts` es LITERAL: cero divergencias declaradas. Sus dos
# bugs del `as BarsFile` —el cache sin campo `bars` que lanza y el `bars` de
# texto que cuela— están portados tal cual, así que aquí son casos IDÉNTICOS.
# La guarda vive en `borde.barras_utiles`, que es del borde de Vertex y solo la
# usa `daily_bars_for_panel` (que tampoco es suya).
#
# El comparador sigue admitiendo `divergencia=`: si algún día hace falta una,
# tiene que declararse Y tiene que producir de verdad una diferencia.

c(nombre="load sin cache", op="load", ticker="DEMO")
c(nombre="load tras save", op="load", ticker="DEMO", pre={"ticker":"DEMO","n":3,"now":HOY})
c(nombre="load json roto", op="load", ticker="R", raw="{no", rawFile="R.json")
c(nombre="load sin campo bars", op="load", ticker="S", raw='{"ticker":"S","date":"2026-07-31"}', rawFile="S.json")
c(nombre="load bars no lista", op="load", ticker="T", raw='{"ticker":"T","date":"x","bars":"txt"}', rawFile="T.json")
c(nombre="load array pelado", op="load", ticker="U", raw='[1,2]', rawFile="U.json")
c(nombre="load null", op="load", ticker="V", raw='null', rawFile="V.json")
# save: normalización del ticker
for t in ("demo"," demo ","DEMO","brk.b","brk/b","a"*70,"!!!",""):
    # Su regex manda a `.json` todos los que sanean a nada, en su archivo y en el
    # port: no hay diferencia que declarar. El rechazo lo hace `borde.ticker_valido`
    # en el endpoint, que es donde su pipeline de Next lo tiene.
    c(nombre=f"save ticker={t!r}", op="save", ticker=t, n=2, now=HOY)
# cached
c(nombre="cached primera vez", op="cached", ticker="C1", now=HOY, n=4)
c(nombre="cached con cache de hoy", op="cached", ticker="C2", now=HOY, n=4,
  pre={"ticker":"C2","n":7,"now":HOY})
c(nombre="cached con cache de ayer", op="cached", ticker="C3", now=HOY, n=4,
  pre={"ticker":"C3","n":7,"now":AYER})
c(nombre="cached durante la SESION", op="cached", ticker="C4", now=SESION, n=4,
  pre={"ticker":"C4","n":7,"now":SESION})
c(nombre="cached red falla sin cache", op="cached", ticker="C5", now=HOY, falla=True)
c(nombre="cached red falla con cache viejo", op="cached", ticker="C6", now=HOY, falla=True,
  pre={"ticker":"C6","n":7,"now":AYER})
c(nombre="cached days=30", op="cached", ticker="C7", now=HOY, days=30, n=4)
c(nombre="cached cache vacio", op="cached", ticker="C8", now=HOY, n=0)
c(nombre="cached cache de hoy vacio en disco", op="cached", ticker="C9", now=HOY, n=4,
  raw='{"ticker":"C9","date":"2026-07-31","bars":[]}', rawFile="C9.json")

c(nombre="cache de HOY sin campo bars", op="cached", ticker="X1", now=HOY, n=4,
  raw='{"ticker":"X1","date":"2026-07-31"}', rawFile="X1.json")
c(nombre="cache de HOY con bars no lista", op="cached", ticker="X2", now=HOY, n=4,
  raw='{"ticker":"X2","date":"2026-07-31","bars":"texto"}', rawFile="X2.json")
c(nombre="cache de HOY que es un array", op="cached", ticker="X3", now=HOY, n=4,
  raw='[1,2,3]', rawFile="X3.json")
print(json.dumps(casos))
