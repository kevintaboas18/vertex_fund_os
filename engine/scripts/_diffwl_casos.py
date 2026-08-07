#!/usr/bin/env python3
"""Casos para `diff_watchlist.sh`.

Cada caso es `{fn, args}`. Los dos lados —su `.ts` en Node y el port en
Python— los ejecutan en el MISMO orden y el comparador exige igualdad exacta.

El corpus no es solo el camino feliz. Lleva a propósito:

  · filas legado de solo-tickers mezcladas con filas de contrato del MISMO
    subyacente, que es donde `removeFromOutbox` dejaba filas imborrables;
  · strikes enteros y con decimales, para el `toFixed(4)`;
  · contratos sin strike o sin vencimiento, que `contractQuery` debe rechazar;
  · colas con ítems ya sincronizados y ya aparcados, que no se reencolan;
  · tickers en minúscula, para las mayúsculas de `addToOutbox`.
"""

from __future__ import annotations

import itertools
import json
import os

AHORA = "2026-08-07T15:30:00.000Z"
OTRO = "2026-08-06T10:00:00.000Z"


def item(ticker, broker="robinhood", symbol=None, tipo=None, strike=None,
         exp=None, synced=None, failed=None, razon=None, added=OTRO):
    d = {"ticker": ticker, "broker": broker, "addedAt": added, "syncedAt": synced}
    if symbol:
        d.update(symbol=symbol, type=tipo, strike=strike, expiration=exp)
    if failed:
        d.update(failedAt=failed, failReason=razon or "irresoluble")
    return d


WULF20 = item("WULF", symbol="WULF270115C00020000", tipo="call",
              strike=20.0, exp="2027-01-15")
WULF25 = item("WULF", symbol="WULF270115C00025000", tipo="call",
              strike=25.0, exp="2027-01-15")
WULF_VIEJO = item("WULF")                        # cola legado de solo-tickers
SPY_VIEJO = item("SPY")
SPXW = item("SPXW", symbol="SPXW260918P05000000", tipo="put",
            strike=5000.0, exp="2026-09-18")
NVDA_OTRO = item("NVDA", broker="schwab")
YA_HECHO = item("AAPL", symbol="AAPL260918C00250000", tipo="call",
                strike=250.0, exp="2026-09-18", synced=AHORA)
APARCADO = item("TSLA", symbol="TSLA260918C00400000", tipo="call",
                strike=400.0, exp="2026-09-18", failed=OTRO, razon="sin strike")

COLAS = [
    [],
    [WULF20],
    [WULF20, WULF25],
    [WULF_VIEJO, WULF20],
    [WULF20, WULF_VIEJO, WULF25, SPY_VIEJO],
    [YA_HECHO, APARCADO, WULF20],
    [SPXW, SPY_VIEJO, NVDA_OTRO],
    [WULF20, WULF20],                            # duplicado exacto en disco
]

CONTRATOS = [
    {"symbol": "WULF270115C00020000", "ticker": "wulf", "type": "call",
     "strike": 20.0, "expiration": "2027-01-15"},
    {"symbol": "WULF270115C00025500", "ticker": "WULF", "type": "call",
     "strike": 25.5, "expiration": "2027-01-15"},
    {"symbol": "SPXW260918P05000000", "ticker": "spxw", "type": "put",
     "strike": 5000.0, "expiration": "2026-09-18"},
    {"symbol": "X", "ticker": "X", "type": "put", "strike": None,
     "expiration": "2026-09-18"},
    {"symbol": "X", "ticker": "X", "type": "call", "strike": 12.0,
     "expiration": None},
    {"symbol": "Z", "ticker": "z", "type": "call", "strike": 0.0,
     "expiration": "2026-12-18"},               # strike 0: falsy en JS
]

BROKERS = ["robinhood", "schwab", "fidelity", "tastytrade", "webull", "ibkr",
           "none", "inventado"]


def entrada(symbol, ticker, tipo, strike, exp, added, spot=100.0, precio=2.5):
    return {"symbol": symbol, "ticker": ticker, "type": tipo, "strike": strike,
            "expiration": exp, "addedAt": added, "entrySpot": spot,
            "entryPrice": precio, "entryDte": 30, "entryPremium": 1500.0,
            "entryThetaPctDaily": -1.2, "maxContracts": 2, "binding": "riesgo",
            "accountSizeAtEntry": 1000.0, "tolerancePctAtEntry": 15.0,
            "brokerSync": None}


E1 = entrada("A1", "AAPL", "call", 250.0, "2026-09-18", "2026-08-01T12:00:00.000Z")
E2 = entrada("B1", "WULF", "put", 20.0, "2027-01-15", "2026-08-05T12:00:00.000Z")
E3 = entrada("A1", "AAPL", "call", 250.0, "2026-09-18", "2026-08-07T12:00:00.000Z",
             spot=999.0, precio=9.9)            # el mismo símbolo, foto distinta
E_SYNC = dict(E2, brokerSync={"broker": "robinhood", "status": "sincronizado",
                              "sent": "contract", "at": OTRO})

LISTAS = [[], [E1], [E1, E2], [E_SYNC, E1], [E2, E1, E_SYNC]]

casos: list[dict] = []


def add(fn, **args):
    casos.append({"fn": fn, "args": args})


# ── buildEntry ───────────────────────────────────────────────────────────────
for strike, exp, dte, theta in itertools.product(
        [20.0, 25.5, None], ["2027-01-15", None], [30, 0, None], [-1.2, None]):
    add("buildEntry",
        source={"symbol": "S1", "ticker": "wulf", "type": "call",
                "strike": strike, "expiration": exp, "dte": dte, "price": 2.5,
                "assetPrice": 18.4, "premium": 1500.0, "thetaPctDaily": theta},
        sizing={"maxContracts": 3, "binding": "riesgo"},
        profile={"accountSize": 1000.0, "tolerancePct": 15.0},
        now=AHORA)

# ── upsert / remove / markSynced / sortEntries / underlyings / tickerList ─────
for lista in LISTAS:
    for nueva in (E1, E2, E3):
        add("upsert", entries=lista, entry=nueva)
    for sym in ("A1", "B1", "NO_EXISTE"):
        add("remove", entries=lista, symbol=sym)
        add("markSynced", entries=lista, symbol=sym,
            sync={"broker": "robinhood", "status": "sincronizado",
                  "sent": "contract", "at": AHORA})
    add("sortEntries", entries=lista)
    add("underlyings", entries=lista)
    add("tickerList", entries=lista)

# ── payloadFor / quoteLink ───────────────────────────────────────────────────
for b in BROKERS:
    for e in (E1, E2):
        add("payloadFor", entry=e, broker=b)
    for t in ("WULF", "BRK.B", "aapl"):
        add("quoteLink", ticker=t, broker=b)

# ── contractQuery / contractRefLabel ─────────────────────────────────────────
for tk, tipo, strike, exp in itertools.product(
        ["wulf", "SPXW", "BRK.B"], ["call", "put"],
        [20.0, 25.5, 0.0, 5000.0, 1234.5678, None],
        ["2027-01-15", None]):
    add("contractQuery", c={"ticker": tk, "type": tipo, "strike": strike,
                            "expiration": exp})
    add("contractRefLabel", c={"ticker": tk, "type": tipo, "strike": strike,
                               "expiration": exp})

# ── outboxKey / outboxLabel ──────────────────────────────────────────────────
for it in (WULF20, WULF_VIEJO, SPXW, YA_HECHO, APARCADO, NVDA_OTRO):
    add("outboxKey", item=it)
    add("outboxLabel", item=it)

# ── addToOutbox ──────────────────────────────────────────────────────────────
for cola in COLAS:
    for c in CONTRATOS:
        for b in ("robinhood", "schwab", "none"):
            add("addToOutbox", items=cola, target=c, broker=b, now=AHORA)

# ── pendingOutbox / failedOutbox ─────────────────────────────────────────────
for cola in COLAS:
    for b in ("robinhood", "schwab", "none"):
        add("pendingOutbox", items=cola, broker=b)
        add("failedOutbox", items=cola, broker=b)

# ── markOutboxSynced / markOutboxFailed ──────────────────────────────────────
CLAVES = [[], ["WULF"], ["WULF270115C00020000"],
          ["WULF270115C00020000", "WULF"], ["NO_EXISTE"],
          ["AAPL260918C00250000"], ["TSLA260918C00400000"]]
for cola in COLAS:
    for claves in CLAVES:
        add("markOutboxSynced", items=cola, keys=claves, broker="robinhood",
            now=AHORA)
        add("markOutboxFailed", items=cola, keys=claves, broker="robinhood",
            reason="no resuelve", now=AHORA)

# ── removeFromOutbox — el caso que dio el bug ────────────────────────────────
OBJETIVOS = [
    {"symbol": "WULF270115C00020000", "ticker": "WULF"},
    {"symbol": "WULF270115C00020000", "ticker": None},
    {"symbol": None, "ticker": "WULF"},
    {"symbol": None, "ticker": "wulf"},
    {"symbol": None, "ticker": None},
    {"symbol": "NO_EXISTE", "ticker": "SPY"},
    {"symbol": None, "ticker": "SPY"},
    {"symbol": "SPXW260918P05000000", "ticker": "SPXW"},
]
for cola in COLAS:
    for obj in OBJETIVOS:
        for b in ("robinhood", "schwab"):
            add("removeFromOutbox", items=cola, target=obj, broker=b)

# ── brokerById + la tabla entera ─────────────────────────────────────────────
for b in BROKERS:
    add("brokerById", id=b)
add("brokers", )
add("robinhoodCommand", )

json.dump(casos, open(os.environ["WL_CASOS"], "w"), ensure_ascii=False)
print(f"  {len(casos)} casos")
