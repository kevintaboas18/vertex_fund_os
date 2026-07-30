#!/usr/bin/env python3
"""Sonda: ¿qué campos devuelve de verdad el order-flow de Quant Data?

    python scripts/probe_quantdata_fields.py NVDA

Responde la única pregunta que decide si Quant Data puede sustituir a
MarketSnack como fuente del tape: ¿trae bid/ask por trade, y trae el objeto
`greeks` completo (gamma, theta, vega) además de delta, más la IV?

`vertex_api.py` solo extrae `delta` de `greeks`, pero eso no prueba que los
demás no estén — puede que nadie los haya necesitado todavía.

NO imprime valores de mercado ni la API key: solo NOMBRES de campo y si están
presentes o vienen en null. Es seguro pegar su salida en un chat.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Mismo default que vertex_api.py (QUANTDATA_BASE): incluye el /v1.
BASE = os.environ.get("QUANTDATA_BASE", "https://api.quantdata.us/v1").rstrip("/")
ENDPOINT = os.environ.get("QD_EP_FLOW", "/options/tool/order-flow/consolidated")

# Lo que el motor de Víctor necesita por trade, y de dónde sale en MarketSnack.
NEEDED = {
    "lado (Agresividad)":        ["tradeSideCode", "side"],
    "premium (Convicción)":      ["premium"],
    "bid por trade (spread)":    ["bidPrice", "bid_price", "bid"],
    "ask por trade (spread)":    ["askPrice", "ask_price", "ask"],
    "precio de ejecución":       ["price", "optionPrice", "fillPrice"],
    "delta (Inusualidad)":       ["delta"],
    "gamma (Inusualidad)":       ["gamma"],
    "theta (Inusualidad)":       ["theta"],
    "vega":                      ["vega"],
    "IV (Contexto IV)":          ["impliedVolatility", "implied_volatility", "iv"],
    "condición OPRA (multileg)": ["tradeConditionId", "trade_condition_id", "conditionId"],
    "vencimiento":               ["expirationDate", "exp"],
    "strike":                    ["strikePrice", "strike"],
    "open interest":             ["openInterest", "open_interest", "oi"],
}


def flatten(d: dict, prefix: str = "") -> dict:
    """Aplana un nivel de anidamiento (p.ej. greeks.gamma) para poder buscar."""
    out = {}
    for k, v in d.items():
        out[k] = v
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out[k2] = v2
                out[f"{k}.{k2}"] = v2
    return out


def main() -> int:
    key = os.environ.get("QUANTDATA_API_KEY", "")
    if not key:
        print("Falta QUANTDATA_API_KEY en el entorno.")
        print("  export QUANTDATA_API_KEY=...   (o córrelo donde ya esté cargada)")
        return 1

    ticker = (sys.argv[1] if len(sys.argv) > 1 else "NVDA").upper()
    payload = {"filter": {"ticker": ticker}, "size": 5}
    req = urllib.request.Request(
        BASE + ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} — {e.read().decode()[:200]}")
        return 1
    except Exception as e:
        print(f"No se pudo conectar: {e}")
        return 1

    rows = data.get("data") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        print(f"Sin trades para {ticker} (¿mercado cerrado?). Prueba con SPY o AAPL.")
        return 1

    flat = flatten(rows[0])
    print(f"\n{ticker} · {len(rows)} trades · {len(flat)} campos (incl. greeks aplanado)\n")

    print("¿Está lo que el motor necesita?\n")
    faltan = []
    for label, candidates in NEEDED.items():
        hit = next((c for c in candidates if c in flat), None)
        if hit is None:
            print(f"  \033[31m✗\033[0m {label:<28} — no está")
            faltan.append(label)
        elif flat[hit] is None:
            print(f"  \033[33m!\033[0m {label:<28} — campo `{hit}` existe pero llega null")
        else:
            print(f"  \033[32m✓\033[0m {label:<28} — `{hit}`")

    print("\nTodos los campos que devuelve (solo nombres):\n")
    names = sorted(k for k in rows[0])
    for i in range(0, len(names), 4):
        print("   " + "  ".join(f"{n:<26}" for n in names[i:i + 4]))
    g = rows[0].get("greeks")
    if isinstance(g, dict):
        print(f"\n   greeks -> {sorted(g)}")

    print()
    if not faltan:
        print("Quant Data cubre TODO lo del tape: se puede jubilar la cookie de MarketSnack.")
    else:
        print(f"Faltan {len(faltan)}: {', '.join(faltan)}")
        print("Se pueden cubrir con proxies declarados (spread e IV desde la cadena;")
        print("gamma/theta por Black-Scholes) — pero hay que declararlos en el reporte.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
