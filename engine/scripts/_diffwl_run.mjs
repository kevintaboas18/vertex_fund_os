// Lado de Víctor de `diff_watchlist.sh`: ejecuta SU watchlist.ts (transpilado a
// `wl.mjs`, solo sin tipos) sobre los casos y vuelca el resultado.
//
// Nada de lo de aquí interpreta ni normaliza: se llama a su función y se
// serializa lo que devuelva. Cualquier maquillaje aquí sería trampa.
import fs from "fs";
import * as W from "./wl.mjs";

const casos = JSON.parse(fs.readFileSync(process.env.WL_CASOS, "utf8"));

// Los brokers viajan por id en los casos, así que se resuelven aquí. Un id
// desconocido llega como `null` a propósito: mide que ambos lados revienten o
// devuelvan lo mismo.
const B = (id) => W.brokerById(id);

const salida = casos.map((c) => {
  const a = c.args;
  try {
    switch (c.fn) {
      case "buildEntry":
        return W.buildEntry(a.source, a.sizing, a.profile, new Date(a.now));
      case "upsert":
        return W.upsert(a.entries, a.entry);
      case "remove":
        return W.remove(a.entries, a.symbol);
      case "markSynced":
        return W.markSynced(a.entries, a.symbol, a.sync);
      case "sortEntries":
        return W.sortEntries(a.entries);
      case "underlyings":
        return W.underlyings(a.entries);
      case "tickerList":
        return W.tickerList(a.entries);
      case "payloadFor": {
        const b = B(a.broker);
        return b ? (W.payloadFor(a.entry, b) ?? null) : "BROKER_DESCONOCIDO";
      }
      case "quoteLink": {
        const b = B(a.broker);
        return b ? (W.quoteLink(a.ticker, b) ?? null) : "BROKER_DESCONOCIDO";
      }
      case "contractQuery":
        return W.contractQuery(a.c) ?? null;
      case "contractRefLabel":
        return W.contractRefLabel(a.c);
      case "outboxKey":
        return W.outboxKey(a.item);
      case "outboxLabel":
        return W.outboxLabel(a.item);
      case "addToOutbox": {
        const b = B(a.broker);
        return b
          ? W.addToOutbox(a.items, a.target, b, new Date(a.now))
          : "BROKER_DESCONOCIDO";
      }
      case "pendingOutbox":
        return W.pendingOutbox(a.items, a.broker);
      case "failedOutbox":
        return W.failedOutbox(a.items, a.broker);
      case "markOutboxSynced":
        return W.markOutboxSynced(a.items, a.keys, a.broker, new Date(a.now));
      case "markOutboxFailed":
        return W.markOutboxFailed(
          a.items,
          a.keys,
          a.broker,
          a.reason,
          new Date(a.now),
        );
      case "removeFromOutbox":
        return W.removeFromOutbox(a.items, a.target, a.broker);
      case "brokerById": {
        const b = B(a.id);
        return b ? { id: b.id, kind: b.kind, granularity: b.granularity } : null;
      }
      case "brokers":
        return W.BROKERS.map((b) => ({
          id: b.id,
          name: b.name,
          kind: b.kind,
          granularity: b.granularity,
          caveat: b.caveat ?? null,
          quote: b.quoteUrl ? b.quoteUrl("BRK.B") : null,
        }));
      case "robinhoodCommand":
        return W.ROBINHOOD_MCP_COMMAND;
      default:
        throw new Error(`caso sin implementar: ${c.fn}`);
    }
  } catch (e) {
    return { ERROR: String(e && e.message ? e.message : e) };
  }
});

// `undefined` no sobrevive a JSON.stringify dentro de un objeto; se normaliza a
// null en AMBOS lados para que la comparación mida la lógica y no el formato.
fs.writeFileSync(
  process.env.WL_OUT,
  JSON.stringify(salida, (_k, v) => (v === undefined ? null : v)),
);
console.log(`  víctor: ${salida.length} casos`);
