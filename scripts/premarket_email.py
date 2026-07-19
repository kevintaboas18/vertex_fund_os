#!/usr/bin/env python3
"""Pre-Market Movers email — corre en GitHub Actions cada mañana de mercado.

Usage:
    RESEND_API_KEY=... python3 scripts/premarket_email.py
    DRY_RUN=1 FORCE=1 python3 scripts/premarket_email.py   # prueba local sin enviar

Env vars:
    RESEND_API_KEY  clave de https://resend.com (requerida salvo DRY_RUN=1)
    EMAIL_TO        destinatario (default: victor@infusioninvestments.com)
    EMAIL_FROM      remitente   (default: onboarding@resend.dev — solo puede
                    enviar al email dueño de la cuenta Resend; verifica tu
                    dominio en Resend para usar otro remitente)
    FORCE=1         salta el chequeo de hora/feriado (para pruebas y
                    workflow_dispatch)
    DRY_RUN=1       imprime el email en stdout en vez de enviarlo

Stdlib only — sin dependencias.
"""

import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
EMAIL_TO = os.environ.get("EMAIL_TO", "victor@infusioninvestments.com")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Warren Buffett Jr <onboarding@resend.dev>")

GAINERS_URL = "https://stockanalysis.com/markets/premarket/"
LOSERS_URL = "https://stockanalysis.com/markets/premarket/losers/"

# Feriados NYSE/Nasdaq (mercado cerrado). Actualizar cada año.
MARKET_HOLIDAYS = {
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

MESES = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun",
         "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

LARGE_CAP_MIN = 10e9  # $10B+ = "lo más importante"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_market_cap(s: str) -> float:
    m = re.match(r"([\d.]+)\s*([TBM]?)", s.replace(",", ""))
    if not m:
        return 0.0
    mult = {"T": 1e12, "B": 1e9, "M": 1e6, "": 1.0}[m.group(2)]
    return float(m.group(1)) * mult


def parse_movers(page: str, limit: int = 10) -> list[dict]:
    """Parsea la tabla SSR de stockanalysis.com (celdas: #, ticker, nombre,
    % cambio, precio, ..., market cap al final)."""
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)  # ruido de Svelte
    body = re.search(r"<tbody>(.*?)</tbody>", page, flags=re.S)
    if not body:
        return []
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), flags=re.S)[:limit]:
        tds = [html.unescape(re.sub(r"<[^>]+>", "", td)).strip()
               for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)]
        if len(tds) < 5:
            continue
        try:
            rows.append({
                "ticker": tds[1],
                "name": tds[2],
                "pct": float(tds[3].replace("%", "").replace(",", "")),
                "price": tds[4],
                "mcap": parse_market_cap(tds[-1]),
            })
        except ValueError:
            continue
    return rows


def fmt_pct(p: float) -> str:
    return f"{'+' if p > 0 else '−'}{abs(p):.1f}%"


def table_html(rows: list[dict], color: str) -> str:
    tr = ""
    for r in rows:
        tr += (
            f'<tr style="border-top:1px solid #eee;">'
            f'<td style="padding:8px;font-weight:700;">{html.escape(r["ticker"])}</td>'
            f'<td style="padding:8px;">{html.escape(r["name"])}</td>'
            f'<td style="padding:8px;color:{color};font-weight:700;">{fmt_pct(r["pct"])}</td>'
            f'<td style="padding:8px;">${r["price"]}</td></tr>'
        )
    return f'<table style="width:100%;border-collapse:collapse;font-size:14px;">{tr}</table>'


def build_email(now: datetime, gainers: list[dict], losers: list[dict]) -> tuple[str, str, str]:
    fecha = f"{DIAS[now.weekday()]} {now.day} {MESES[now.month]} {now.year}"
    subject = f"📈 Pre-Market Movers — {fecha}"

    big = sorted([r for r in gainers + losers if r["mcap"] >= LARGE_CAP_MIN],
                 key=lambda r: -abs(r["pct"]))[:6]
    small_g = [r for r in gainers if r["mcap"] < LARGE_CAP_MIN][:5]
    small_l = [r for r in losers if r["mcap"] < LARGE_CAP_MIN][:5]

    def txt_rows(rows):
        return "\n".join(f"- {r['ticker']} {r['name']}: {fmt_pct(r['pct'])} a ${r['price']}"
                         for r in rows)

    text = f"""PRE-MARKET MOVERS — {fecha}
(Pre-market en vivo, {now.strftime('%H:%M')} ET — stockanalysis.com)

LO MÁS IMPORTANTE (large caps, $10B+):
{txt_rows(big) or '- (ninguna large cap con movimiento fuerte hoy)'}

GANADORES PRE-MARKET (small caps, alta volatilidad):
{txt_rows(small_g)}

PERDEDORES PRE-MARKET:
{txt_rows(small_l)}

Contexto y noticias: https://stockanalysis.com/markets/premarket/ · https://www.benzinga.com/premarket

---
Clasificación de research — no es asesoría de inversión ni recomendación de compra/venta.
Warren Buffett Jr 🎩📈
"""

    big_html = (table_html(big, "#e17055") if big else
                '<p style="font-size:13px;color:#888;">Ninguna large cap con movimiento fuerte hoy.</p>')
    htmlbody = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:640px;margin:0 auto;color:#1a1a2e;">
  <div style="background:#6c5ce7;color:#fff;padding:20px 24px;border-radius:12px 12px 0 0;">
    <div style="font-size:12px;letter-spacing:2px;opacity:.85;">WARREN BUFFETT JR · MOTOR DE ANÁLISIS</div>
    <h1 style="margin:6px 0 0;font-size:22px;">📈 Pre-Market Movers — {fecha}</h1>
    <div style="font-size:13px;opacity:.85;margin-top:4px;">Pre-market en vivo · {now.strftime('%H:%M')} ET · stockanalysis.com</div>
  </div>
  <div style="border:1px solid #e5e5f0;border-top:none;padding:20px 24px;border-radius:0 0 12px 12px;">
    <h2 style="font-size:15px;margin:0 0 10px;color:#6c5ce7;">🔥 Lo más importante — large caps ($10B+)</h2>
    {big_html}
    <h2 style="font-size:15px;margin:22px 0 10px;color:#00b894;">🚀 Ganadores pre-market (small caps — alta volatilidad)</h2>
    {table_html(small_g, "#00b894")}
    <h2 style="font-size:15px;margin:22px 0 10px;color:#d63031;">📉 Perdedores pre-market</h2>
    {table_html(small_l, "#d63031")}
    <p style="font-size:13px;color:#444;margin-top:18px;"><b>Contexto y noticias:</b>
      <a href="https://stockanalysis.com/markets/premarket/">stockanalysis.com</a> ·
      <a href="https://www.benzinga.com/premarket">Benzinga pre-market</a></p>
    <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
    <p style="font-size:11px;color:#aaa;margin:0;">Clasificación de research — no es asesoría de inversión ni recomendación de compra/venta. · Warren Buffett Jr 🎩📈</p>
  </div>
</div>"""
    return subject, text, htmlbody


def send_resend(subject: str, text: str, htmlbody: str) -> None:
    key = os.environ["RESEND_API_KEY"]
    payload = json.dumps({
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": subject,
        "text": text,
        "html": htmlbody,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"Resend: {r.status} {r.read().decode()}")


def main() -> int:
    now = datetime.now(ET)
    force = os.environ.get("FORCE") == "1"

    if not force:
        # El workflow corre 12:00 y 13:00 UTC; solo una equivale a las 8 ET.
        if now.hour != 8:
            print(f"Son las {now.strftime('%H:%M')} ET, no las 8 — skip (cron UTC/DST).")
            return 0
        if now.weekday() >= 5 or now.strftime("%Y-%m-%d") in MARKET_HOLIDAYS:
            print("Mercado cerrado hoy — skip.")
            return 0

    gainers = parse_movers(fetch(GAINERS_URL))
    losers = parse_movers(fetch(LOSERS_URL))
    if not gainers and not losers:
        print("ERROR: no pude parsear movers (¿cambió el HTML de stockanalysis.com?)",
              file=sys.stderr)
        return 1

    subject, text, htmlbody = build_email(now, gainers, losers)

    if os.environ.get("DRY_RUN") == "1":
        print(f"[DRY RUN] to={EMAIL_TO}\nsubject={subject}\n\n{text}")
        return 0
    send_resend(subject, text, htmlbody)
    print(f"Enviado a {EMAIL_TO}: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
