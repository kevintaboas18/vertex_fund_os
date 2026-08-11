#!/usr/bin/env python3
"""Lado del port de `diff_watchlist.sh`.

Ejecuta `wbj.tito.watchlist` sobre los mismos casos y vuelca el resultado con
las MISMAS claves que su TypeScript (camelCase), porque lo que se compara es el
JSON contra el suyo.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "engine"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wbj.tito import watchlist as W  # noqa: E402


def _fecha(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _entry(d: dict) -> W.WatchlistEntry:
    sync = d.get("brokerSync")
    return W.WatchlistEntry(
        symbol=d["symbol"], ticker=d["ticker"], type=d["type"],
        strike=d["strike"], expiration=d["expiration"], addedAt=d["addedAt"],
        entrySpot=d["entrySpot"], entryPrice=d["entryPrice"],
        entryDte=d["entryDte"], entryPremium=d["entryPremium"],
        entryThetaPctDaily=d["entryThetaPctDaily"],
        maxContracts=d["maxContracts"], binding=d["binding"],
        accountSizeAtEntry=d["accountSizeAtEntry"],
        tolerancePctAtEntry=d["tolerancePctAtEntry"],
        brokerSync=W.BrokerSync(**sync) if sync else None,
    )


def _item(d: dict) -> W.OutboxItem:
    return W.OutboxItem(
        ticker=d["ticker"], broker=d["broker"], addedAt=d["addedAt"],
        syncedAt=d.get("syncedAt"), symbol=d.get("symbol"), type=d.get("type"),
        strike=d.get("strike"), expiration=d.get("expiration"),
        failedAt=d.get("failedAt"), failReason=d.get("failReason"),
    )


def _item_json(i: W.OutboxItem) -> dict:
    """Como lo serializa su objeto: los campos de contrato SOLO si la fila los
    trae, y los de fallo SOLO si falló. En su TypeScript son opcionales y no
    aparecen en el JSON; aquí existen siempre con `None`, así que se omiten a
    mano para que la comparación mida la lógica, no el dataclass."""
    d = {"ticker": i.ticker, "broker": i.broker, "addedAt": i.addedAt,
         "syncedAt": i.syncedAt}
    if i.symbol:
        d.update(symbol=i.symbol, type=i.type, strike=i.strike,
                 expiration=i.expiration)
    if i.failedAt:
        d.update(failedAt=i.failedAt, failReason=i.failReason)
    return d


def _entry_json(e: W.WatchlistEntry) -> dict:
    d = asdict(e)
    if d.get("brokerSync") and d["brokerSync"].get("detail") is None:
        # Su `BrokerSync` no lleva `detail` cuando no hay nada que contar.
        d["brokerSync"].pop("detail")
    return d


def _ref(d: dict) -> W.ContractRef:
    return W.ContractRef(d["ticker"], d["type"], d["strike"], d["expiration"])


def _fuente(d: dict) -> W.EntrySource:
    return W.EntrySource(**d)


def _corre(c: dict):
    fn, a = c["fn"], c["args"]
    if fn == "buildEntry":
        return _entry_json(W.build_entry(_fuente(a["source"]), a["sizing"],
                                         a["profile"], _fecha(a["now"])))
    if fn == "upsert":
        e = [_entry(x) for x in a["entries"]]
        return [_entry_json(x) for x in W.upsert(e, _entry(a["entry"]))]
    if fn == "remove":
        e = [_entry(x) for x in a["entries"]]
        return [_entry_json(x) for x in W.remove(e, a["symbol"])]
    if fn == "markSynced":
        e = [_entry(x) for x in a["entries"]]
        s = W.BrokerSync(**a["sync"])
        return [_entry_json(x) for x in W.mark_synced(e, a["symbol"], s)]
    if fn == "sortEntries":
        e = [_entry(x) for x in a["entries"]]
        return [_entry_json(x) for x in W.sort_entries(e)]
    if fn == "underlyings":
        return W.underlyings([_entry(x) for x in a["entries"]])
    if fn == "tickerList":
        return W.ticker_list([_entry(x) for x in a["entries"]])
    if fn == "payloadFor":
        b = W.broker_by_id(a["broker"])
        return W.payload_for(_entry(a["entry"]), b) if b else "BROKER_DESCONOCIDO"
    if fn == "quoteLink":
        b = W.broker_by_id(a["broker"])
        return W.quote_link(a["ticker"], b) if b else "BROKER_DESCONOCIDO"
    if fn == "contractQuery":
        return W.contract_query(_ref(a["c"]))
    if fn == "contractRefLabel":
        return W.contract_ref_label(_ref(a["c"]))
    if fn == "outboxKey":
        return W.outbox_key(_item(a["item"]))
    if fn == "outboxLabel":
        return W.outbox_label(_item(a["item"]))
    if fn == "addToOutbox":
        b = W.broker_by_id(a["broker"])
        if not b:
            return "BROKER_DESCONOCIDO"
        items = [_item(x) for x in a["items"]]
        t = a["target"]
        objetivo = W.OutboxTarget(symbol=t["symbol"], ticker=t["ticker"],
                                  type=t["type"], strike=t["strike"],
                                  expiration=t["expiration"])
        return [_item_json(x)
                for x in W.add_to_outbox(items, objetivo, b, _fecha(a["now"]))]
    if fn == "pendingOutbox":
        items = [_item(x) for x in a["items"]]
        return [_item_json(x) for x in W.pending_outbox(items, a["broker"])]
    if fn == "failedOutbox":
        items = [_item(x) for x in a["items"]]
        return [_item_json(x) for x in W.failed_outbox(items, a["broker"])]
    if fn == "markOutboxSynced":
        items = [_item(x) for x in a["items"]]
        return [_item_json(x) for x in W.mark_outbox_synced(
            items, a["keys"], a["broker"], _fecha(a["now"]))]
    if fn == "markOutboxFailed":
        items = [_item(x) for x in a["items"]]
        return [_item_json(x) for x in W.mark_outbox_failed(
            items, a["keys"], a["broker"], a["reason"], _fecha(a["now"]))]
    if fn == "removeFromOutbox":
        items = [_item(x) for x in a["items"]]
        return [_item_json(x)
                for x in W.remove_from_outbox(items, a["target"], a["broker"])]
    if fn == "brokerById":
        b = W.broker_by_id(a["id"])
        return {"id": b.id, "kind": b.kind, "granularity": b.granularity} if b else None
    if fn == "brokers":
        return [{"id": b.id, "name": b.name, "kind": b.kind,
                 "granularity": b.granularity, "caveat": b.caveat,
                 "quote": b.quote_url("BRK.B") if b.quote_url else None}
                for b in W.BROKERS]
    if fn == "robinhoodCommand":
        return W.ROBINHOOD_MCP_COMMAND
    raise AssertionError(f"caso sin implementar: {fn}")


casos = json.load(open(os.environ["WL_CASOS"]))
salida = []
for c in casos:
    try:
        r = _corre(c)
    except Exception as e:                       # su lado devuelve {ERROR: ...}
        r = {"ERROR": str(e)}
    salida.append(asdict(r) if is_dataclass(r) else r)

json.dump(salida, open(os.environ["WL_PY_OUT"], "w"), ensure_ascii=False)
print(f"  port:   {len(salida)} casos")
