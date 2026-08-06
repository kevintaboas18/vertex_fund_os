#!/usr/bin/env python3
"""Preflight EN VIVO: la única comprobación que ningún test puede dar.

    MASSIVE_API_KEY=... MARKETSNACK_COOKIE=... \\
        python engine/scripts/preflight_vivo.py AAPL

Todo lo demás del motor se verifica sin red: 2.600 tests, 228 checks de
auditoría y 8 diferenciales que ejecutan SUS archivos en Node. Nada de eso toca
`api.massive.com` ni `app.marketsnack.com`, porque el contenedor de desarrollo
los bloquea. Lo que queda abierto no es la lógica — es la **forma real** de la
respuesta: que los campos se llamen como el port cree, que los tipos sean los
que espera y que el volumen quepa en las páginas que pide.

Esto es lo que cierra ese hueco, y está pensado para correrse UNA vez el día del
despliegue, con las credenciales puestas. No es un test: no se ejecuta en CI ni
tiene mocks. Toca la red de verdad y dice qué encontró.

Qué comprueba, en orden de lo que rompe antes:

 1. Las credenciales existen y las acepta el proveedor.
 2. La cadena de Massive trae los campos que `compute.to_row` lee, con el tipo
    que espera — y avisa si alguno viene como texto, que es el cambio de esquema
    que el motor absorbe pero que conviene saber.
 3. `shares_per_contract` viene de verdad. Darlo por hecho en 100 infla el
    nocional de los contratos ajustados hasta 10× y mueve el sub-agente 4.
 4. Las barras diarias llegan y son suficientes (el año que piden `levels` y el
    sub-agente 6).
 5. El tape de MarketSnack trae `id`, `asset_price` y `timestamp` — los tres
    sin los cuales la memoria no acumula nada aunque el archivo crezca.
 6. El scorecard corre de punta a punta con esos datos y las 6 categorías
    puntúan (o dicen por qué no).

No imprime ninguna credencial: solo si están puestas y su longitud.
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OK, FALLOS, AVISOS = [], [], []


def chk(cond: bool, msg: str, aviso: bool = False) -> bool:
    (OK if cond else (AVISOS if aviso else FALLOS)).append(msg)
    print(f"  {'✓' if cond else ('!' if aviso else '✗')} {msg}")
    return bool(cond)


def sec(t: str) -> None:
    print(f"\n\033[1m── {t} ──\033[0m")


def _tipo(v) -> str:
    return type(v).__name__


def main(ticker: str) -> int:
    print(f"\033[1mPreflight en vivo · {ticker} · "
          f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\033[0m")

    # ── 1. Credenciales ────────────────────────────────────────────────────
    sec("1. Credenciales")
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    cookie = os.environ.get("MARKETSNACK_COOKIE", "").strip()
    hay_key = chk(bool(key), f"MASSIVE_API_KEY presente ({len(key)} caracteres)")
    hay_cookie = chk(bool(cookie), f"MARKETSNACK_COOKIE presente ({len(cookie)} caracteres)")
    if not hay_key:
        print("\n  Sin la key de Massive no hay cadena ni barras: se para aquí.")
        return 1

    # ── 2. Cadena ──────────────────────────────────────────────────────────
    sec("2. Massive · cadena de opciones")
    from wbj.tito.massive import MassiveError, fetch_daily_bars, fetch_option_chain

    t0 = time.time()
    try:
        chain = fetch_option_chain(ticker)
    except MassiveError as e:
        chk(False, f"fetch_option_chain: {e}")
        return 1
    chk(True, f"{len(chain.rows)} contratos en {time.time() - t0:.1f}s "
              f"({chain.pages} página(s){', TRUNCADA' if chain.truncated else ''})")
    chk(bool(chain.rows), "la cadena no viene vacía")
    chk(chain.underlying_price is not None,
        f"underlying_price = {chain.underlying_price}",
        aviso=True)
    if not chain.rows:
        return 1

    # Los campos crudos, tal como llegan, ANTES de que el motor los normalice.
    sec("3. Massive · la FORMA de la respuesta (lo que ningún test ve)")
    crudos = getattr(chain, "raw_sample", None)
    if crudos is None:
        # `fetch_option_chain` no guarda el crudo; se pide una página suelta.
        from wbj.tito import massive as M
        crudos = M._get("/v3/snapshot/options/" + ticker.upper(),  # noqa: SLF001
                        {"limit": 5}).get("results", [])[:5]

    tipos: dict[str, Counter] = {}
    for c in crudos:
        det = c.get("details") or {}
        day = c.get("day") or {}
        for nombre, valor in (
            ("details.ticker", det.get("ticker")),
            ("details.contract_type", det.get("contract_type")),
            ("details.expiration_date", det.get("expiration_date")),
            ("details.strike_price", det.get("strike_price")),
            ("details.shares_per_contract", det.get("shares_per_contract")),
            ("open_interest", c.get("open_interest")),
            ("day.volume", day.get("volume")),
            ("day.close", day.get("close")),
        ):
            tipos.setdefault(nombre, Counter())[_tipo(valor)] += 1

    for nombre, cuenta in tipos.items():
        vistos = ", ".join(f"{t}×{n}" for t, n in cuenta.most_common())
        ausente = cuenta.get("NoneType", 0) == sum(cuenta.values())
        texto = "str" in cuenta and nombre != "details.ticker" \
            and nombre != "details.contract_type" and nombre != "details.expiration_date"
        chk(not ausente and not texto, f"{nombre:<32} {vistos}",
            aviso=texto)   # un número en texto NO es un fallo: el motor lo absorbe

    # El vencimiento: si trae hora, `days_to_expiration` y el heatmap dan NaN —
    # es literal, es lo que hace su archivo, y conviene saberlo antes.
    con_hora = [c for c in crudos
                if isinstance((c.get("details") or {}).get("expiration_date"), str)
                and len((c["details"]["expiration_date"])) > 10]
    chk(not con_hora,
        f"el vencimiento llega como YYYY-MM-DD (sin hora): "
        f"{(crudos[0].get('details') or {}).get('expiration_date')!r}",
        aviso=True)

    # `shares_per_contract`: el bug que motivó portar `compute.ts`.
    spc = Counter((c.get("details") or {}).get("shares_per_contract") for c in crudos)
    chk(None not in spc,
        f"shares_per_contract viene en la respuesta: {dict(spc)}",
        aviso=True)

    # ── 4. Barras ──────────────────────────────────────────────────────────
    sec("4. Massive · barras diarias")
    t0 = time.time()
    try:
        bars = fetch_daily_bars(ticker, 365)
    except MassiveError as e:
        chk(False, f"fetch_daily_bars: {e}")
        bars = []
    if bars:
        chk(len(bars) >= 200, f"{len(bars)} barras en {time.time() - t0:.1f}s "
                              f"(se piden 365 días; <200 recorta el backtest)")
        chk(all(isinstance(b.time, str) and len(b.time) == 10 for b in bars[:5]),
            f"la fecha de la barra es YYYY-MM-DD: {bars[-1].time!r}")
        chk(bars[0].time < bars[-1].time, "vienen en orden ascendente")

    # ── 5. Tape ────────────────────────────────────────────────────────────
    sec("5. MarketSnack · tape")
    trades = []
    if hay_cookie:
        from wbj.tito.marketsnack import MarketSnackError, fetch_flow
        t0 = time.time()
        try:
            fl = fetch_flow(ticker, period="1d", min_premium=100_000, max_pages=1)
            trades = fl.trades
            chk(True, f"{len(trades)} trades en {time.time() - t0:.1f}s"
                      + (" · 0 puede ser mercado cerrado" if not trades else ""))
        except MarketSnackError as e:
            caducada = "caduc" in str(e).lower() or "expirada" in str(e)
            chk(False, f"fetch_flow: {e}"
                       + (" ← saca la cookie otra vez de DevTools" if caducada else ""))
    else:
        chk(False, "sin MARKETSNACK_COOKIE: 5 de 6 sub-agentes se quedan sin dato",
            aviso=True)

    # Los tres campos sin los cuales la memoria no acumula nada.
    for campo in ("id", "asset_price", "timestamp"):
        if not trades:
            break
        faltan = sum(1 for t in trades if t.get(campo) is None)
        chk(faltan == 0,
            f"el tape trae `{campo}` en los {len(trades)} trades"
            + (f" · FALTA en {faltan}" if faltan else ""))

    # ── 6. Scorecard de punta a punta ──────────────────────────────────────
    sec("6. El scorecard, con datos REALES")
    if not bars:
        chk(False, "sin barras no hay scorecard que correr")
    else:
        from wbj.tito.scorecard import run_scorecard
        t0 = time.time()
        r = run_scorecard(ticker, trades, chain.rows, bars,
                          now=datetime.now(timezone.utc),
                          spot=chain.underlying_price or bars[-1].close)
        chk(True, f"corrió en {time.time() - t0:.1f}s · score {r.score}/100 · {r.verdict}")
        activos = {k: v for k, v in vars(r.scores).items() if v is not None} \
            if hasattr(r.scores, "__dict__") else dict(r.scores)
        vivos = [k for k, v in activos.items() if v is not None]
        chk(len(vivos) >= 4,
            f"{len(vivos)}/6 categorías con score: {', '.join(sorted(vivos))}")
        for w in r.warnings:
            print(f"      ⚠ {w}")
        chk(bool(r.predictions), f"{len(r.predictions)} horizonte(s) con escenarios")
        for h, p in sorted(r.predictions.items()):
            print(f"      {h}d: bear {p.bear.target:.2f} · base {p.base.target:.2f}"
                  f" · bull {p.bull.target:.2f}")

    # ── Resumen ────────────────────────────────────────────────────────────
    print(f"\n\033[1m{'=' * 66}\033[0m")
    print(f"  \033[32m{len(OK)} OK\033[0m · \033[33m{len(AVISOS)} avisos\033[0m"
          f" · \033[31m{len(FALLOS)} fallos\033[0m")
    if AVISOS:
        print("\n  Avisos (no bloquean, pero conviene saberlos):")
        for a in AVISOS:
            print(f"    ! {a}")
    if FALLOS:
        print("\n  FALLOS:")
        for f in FALLOS:
            print(f"    ✗ {f}")
        print("\n  `/api/tito-health` dice lo mismo desde el servidor ya desplegado.")
    print()
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "AAPL"))
