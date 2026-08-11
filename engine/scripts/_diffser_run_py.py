"""Lado Python del diferencial de SERIES. Mismos casos, mismo orden.

Dos modos, en espejo con `_diffser_run.mts`:

    SER_MODO=escribe   guarda con el PORT y vuelca el archivo crudo
    SER_MODO=lee       lee con el PORT lo que escribió SU TypeScript

El segundo es el que cierra el círculo: el primero prueba que los dos escriben
lo mismo, y este que cada uno entiende el archivo del otro. Solo con los dos se
puede decir que el archivo es intercambiable, que es lo que hace que la memoria
del agente sobreviva a cambiar de lado.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wbj.tito import stores as st                                   # noqa: E402
from wbj.tito.flow import FlowFlags, FlowRow, TradeScores           # noqa: E402
from wbj.tito.ivcontext import iv_context_score                     # noqa: E402
from wbj.tito.structure import ChainRow, structure_score            # noqa: E402

C = json.load(open(os.environ["SER_CASOS"]))
MODO = os.environ["SER_MODO"]


def _cuando(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# A DIFERENCIA de los demás diferenciales, aquí no hay redondeo a 6 decimales:
# se compara el ARCHIVO, y un archivo se compara entero. El motivo está escrito
# en `_diffser_run.mts` — el redondeo metía diferencias falsas por el desempate
# (`Math.round` de JS va hacia arriba, `round` de Python hacia el par).


def _fila_cadena(d: dict) -> ChainRow:
    return ChainRow(contract_type=d["contractType"], expiration=d["expiration"],
                    strike=d["strike"], open_interest=d["openInterest"],
                    volume=d["volume"], notional_value=d["notionalValue"])


def _fila_flujo(d: dict) -> FlowRow:
    return FlowRow(
        id=d["id"], symbol=d["symbol"], underlying=d["underlying"], type=d["type"],
        strike=d["strike"], expiration=d["expiration"], dte=d["dte"], price=d["price"],
        size=d["size"], side=d["side"], aggression=d["aggression"],
        asset_price=d["assetPrice"], bid=d["bid"], ask=d["ask"], premium=d["premium"],
        delta=d["delta"], gamma=d["gamma"], theta=d["theta"], vega=d["vega"],
        theta_pct_daily=None, iv=d["iv"], open_interest=d["openInterest"],
        volume=d["volume"], score=0, sentiment="neutral", timestamp=d["timestamp"],
        condition_code=None, condition_name=None,
        flags=FlowFlags(), scores=TradeScores())


def archivo(sub: str, ticker: str):
    """El archivo CRUDO, igual que el lado JS: se compara el disco, no el
    valor de retorno."""
    try:
        with open(os.path.join(os.environ["WBJ_TITO_DATA"], sub, f"{ticker}.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


out = {"chain": [], "iv": [], "predictions": []}

if MODO == "escribe":
    for c in C["chain"]:
        for s in c["saves"]:
            st.save_chain_snapshot(c["ticker"],
                                   structure_score([_fila_cadena(x) for x in s["rows"]]),
                                   _cuando(s["now"]))
        out["chain"].append({"ticker": c["ticker"], "archivo": archivo("chain", c["ticker"])})

    for c in C["iv"]:
        for s in c["saves"]:
            score = iv_context_score([_fila_flujo(x) for x in s["rows"]], s["closes"], [])
            st.save_iv_snapshot(c["ticker"], score, _cuando(s["now"]))
        out["iv"].append({"ticker": c["ticker"], "archivo": archivo("iv", c["ticker"])})

    for c in C["predictions"]:
        for s in c["saves"]:
            ahora = _cuando(s["now"])
            snap = s["snap"]
            # `date` y `savedAt` los deriva él de `now` dentro del store; aquí
            # los pone el llamador (`_tito_remember` hace exactamente esto),
            # así que se replica igual o la comparación mediría al runner.
            st.save_prediction(c["ticker"], st.PredictionSnapshot(
                date=st.market_date_str(ahora), horizon_days=snap["horizonDays"],
                spot=snap["spot"], bear=snap["bear"], base=snap["base"],
                bull=snap["bull"], direction=snap["direction"],
                confidence=snap["confidence"],
                saved_at=ahora.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{ahora.microsecond // 1000:03d}Z"))
        out["predictions"].append({"ticker": c["ticker"],
                                   "archivo": archivo("predictions", c["ticker"])})
else:
    # El port devuelve el ARRAY (es lo que consume el motor); el sobre se lee
    # del disco, que es donde tiene que estar. Para comparar contra su `visto`
    # se reconstruye la misma forma.
    def visto(sub, ticker, filas):
        crudo = archivo(sub, ticker)
        if crudo is None or not isinstance(crudo.get("snapshots"), list):
            return None
        return {"ticker": crudo.get("ticker"), "updatedAt": crudo.get("updatedAt"),
                "snapshots": filas}

    for c in C["chain"]:
        out["chain"].append({"ticker": c["ticker"],
                             "visto": visto("chain", c["ticker"],
                                            st.load_chain_history(c["ticker"]))})
    for c in C["iv"]:
        out["iv"].append({"ticker": c["ticker"],
                          "visto": visto("iv", c["ticker"], st.load_iv_history(c["ticker"]))})
    for c in C["predictions"]:
        out["predictions"].append({"ticker": c["ticker"],
                                   "visto": visto("predictions", c["ticker"],
                                                  st.load_journal(c["ticker"]))})

with open(os.environ["SER_OUT"], "w", encoding="utf-8") as fh:
    json.dump(out, fh)
