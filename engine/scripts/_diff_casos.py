"""Genera contratos crudos cubriendo todo el espacio de formas que puede mandar Massive."""
import json, random
random.seed(int(__import__("os").environ.get("DIFF_SEED", "20260731")))
VALORES = [None, 0, 1, -1, 0.5, 60, 60.5, 9000, 205, 250.0, 1e9,
           "0", "500", "205.5", "", "  500  ", "abc", "1e3", True, False, [], {}]
TIPOS   = [None, "call", "put", "PUT", "CALL", "Put", "", "P", 5]
SUBOBJ  = [None, {}, "texto", 5, []]

def campo(): return random.choice(VALORES)
def caso(i):
    c = {}
    if random.random() < .92:
        d = {}
        for k in ("contract_type","expiration_date","strike_price","shares_per_contract","ticker"):
            if random.random() < .8:
                d[k] = random.choice(TIPOS) if k=="contract_type" else (
                       random.choice(["2026-09-18","2026-09-18T00:00:00Z","",None,20260918])
                       if k=="expiration_date" else campo())
        c["details"] = d if random.random() < .85 else random.choice(SUBOBJ)
    if random.random() < .9:
        day = {k: campo() for k in ("volume","close","vwap") if random.random() < .75}
        c["day"] = day if random.random() < .88 else random.choice(SUBOBJ)
    if random.random() < .7:
        c["last_trade"] = {"price": campo()} if random.random() < .85 else random.choice(SUBOBJ)
    if random.random() < .9: c["open_interest"] = campo()
    return c
casos = [caso(i) for i in range(int(__import__("os").environ.get("DIFF_N", "600")))]
# más los del test del propio Víctor
casos += [{}, {"details":{}}, {"day":{}},
          {"details":{"contract_type":"call","expiration_date":"2026-07-22","strike_price":205,
                      "shares_per_contract":100,"ticker":"O:AAPL260722C00205000"},
           "day":{"volume":81,"close":119.28},"last_trade":{"price":119.28},"open_interest":60}]
print(json.dumps(casos))
