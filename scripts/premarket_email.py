#!/usr/bin/env python3
"""Pre-Market Movers email — corre en GitHub Actions cada mañana de mercado.

Usage:
    RESEND_API_KEY=... python3 scripts/premarket_email.py
    DRY_RUN=1 FORCE=1 python3 scripts/premarket_email.py   # prueba local sin enviar

Env vars:
    RESEND_API_KEY  clave de https://resend.com (requerida salvo DRY_RUN=1)
    FMP_API_KEY     clave de Financial Modeling Prep (requerida siempre: es de
                    donde salen los movers)
    EMAIL_TO        destinatario (default: kevintaboas02@gmail.com). Admite
                    varios separados por coma.
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
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
# `or` y no el default de get(): GitHub Actions inyecta la variable con cadena
# VACIA cuando `vars.EMAIL_TO` no esta definida, y entonces get() devuelve ""
# en vez del default. Resend recibia "to": [""] y contestaba 422.
EMAIL_TO = os.environ.get("EMAIL_TO") or "kevintaboas02@gmail.com"
EMAIL_FROM = (os.environ.get("EMAIL_FROM")
              or "Vertex Fund OS <onboarding@resend.dev>")
FMP_API_KEY = os.environ.get("FMP_API_KEY") or ""
FMP_BASE = "https://financialmodelingprep.com/stable"
GAINERS = "biggest-gainers"
LOSERS = "biggest-losers"

#: El cron de GitHub no es puntual: se ha visto disparar a las 12:08 y a las
#: 13:52 UTC con el mismo `30 11`. Exigir la hora exacta (`hour != 8`) hacia
#: que el guion se saltara TODOS los envios -- y como saltarse devuelve 0, el
#: workflow salia en verde sin haber mandado nunca un correo. Una ventana
#: absorbe la deriva y el horario de verano; lo que sigue fuera es una corrida
#: a deshora, que no debe mandarse como "pre-market".
VENTANA_ET = range(6, 11)

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


def fetch_json(path: str):
    """Una llamada a FMP. La clave viaja en la query, nunca se imprime."""
    if not FMP_API_KEY:
        raise RuntimeError("Falta FMP_API_KEY: sin clave no hay movers.")
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(f"{FMP_BASE}/{path}{sep}apikey={FMP_API_KEY}",
                                 headers={"User-Agent": "vertex-fund-os"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def market_cap(ticker: str) -> float:
    """FMP no acepta lotes en `quote` (devuelve vacío), así que va de a uno.
    Son 20 peticiones una vez al día. Un fallo suelto no tumba el correo: la
    empresa se queda sin capitalización y no entra en «lo más importante»."""
    try:
        filas = fetch_json(f"quote?symbol={urllib.parse.quote(ticker)}")
        return float(filas[0].get("marketCap") or 0.0) if filas else 0.0
    except Exception:
        return 0.0


def movers(cual: str, limit: int = 10) -> list[dict]:
    """Los que más suben/bajan, de FMP.

    Antes esto raspaba la tabla SSR de stockanalysis.com. Dos razones para
    dejarlo: devuelve 403 a las IPs de GitHub Actions —el correo llevaba
    fallando desde el runner mientras funcionaba desde casa— y no es una de
    las fuentes del proyecto (FMP, FinnHub, FRED, EDGAR).
    """
    filas = fetch_json(cual)
    if not isinstance(filas, list):
        return []
    salida = []
    for f in filas[:limit]:
        try:
            salida.append({
                "ticker": f["symbol"],
                "name": f.get("name") or f["symbol"],
                "pct": float(f["changesPercentage"]),
                "price": f"{float(f['price']):.2f}",
                "mcap": market_cap(f["symbol"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return salida


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
(Pre-market en vivo, {now.strftime('%H:%M')} ET — FMP)

LO MÁS IMPORTANTE (large caps, $10B+):
{txt_rows(big) or '- (ninguna large cap con movimiento fuerte hoy)'}

GANADORES PRE-MARKET (small caps, alta volatilidad):
{txt_rows(small_g)}

PERDEDORES PRE-MARKET:
{txt_rows(small_l)}

Fuente: Financial Modeling Prep (biggest-gainers / biggest-losers).

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
    <div style="font-size:13px;opacity:.85;margin-top:4px;">Pre-market en vivo · {now.strftime('%H:%M')} ET · FMP</div>
  </div>
  <div style="border:1px solid #e5e5f0;border-top:none;padding:20px 24px;border-radius:0 0 12px 12px;">
    <h2 style="font-size:15px;margin:0 0 10px;color:#6c5ce7;">🔥 Lo más importante — large caps ($10B+)</h2>
    {big_html}
    <h2 style="font-size:15px;margin:22px 0 10px;color:#00b894;">🚀 Ganadores pre-market (small caps — alta volatilidad)</h2>
    {table_html(small_g, "#00b894")}
    <h2 style="font-size:15px;margin:22px 0 10px;color:#d63031;">📉 Perdedores pre-market</h2>
    {table_html(small_l, "#d63031")}
    <p style="font-size:13px;color:#444;margin-top:18px;"><b>Fuente:</b>
      Financial Modeling Prep — <code>biggest-gainers</code> / <code>biggest-losers</code>.</p>
    <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
    <p style="font-size:11px;color:#aaa;margin:0;">Clasificación de research — no es asesoría de inversión ni recomendación de compra/venta. · Warren Buffett Jr 🎩📈</p>
  </div>
</div>"""
    return subject, text, htmlbody


def _emails_de_las_cuentas() -> list[str]:
    """El email de CADA cuenta, sacado del respaldo cifrado.

    Los correos viven en la fila de cada usuario, y esa base sólo existe fuera
    de Render en `Privado/privado.enc` de la rama `datos` — un tar cifrado con
    Fernet y `VERTEX_DB_KEY`.

    Se lee de ahí y no de un endpoint del servidor por dos razones: Render en
    plan free duerme, y una ruta que devuelva la lista de correos de todo el
    mundo es exactamente lo que no conviene exponer para ahorrarse un `git
    fetch`.

    Nunca lanza. Sin clave, sin respaldo o sin `cryptography` se devuelve la
    lista vacía y quien llama cae a `EMAIL_TO`: quedarse sin lista no puede
    costar el correo.
    """
    clave = os.environ.get("VERTEX_DB_KEY", "").strip()
    if not clave:
        return []
    try:
        import base64
        import hashlib
        import io
        import sqlite3
        import tarfile
        import tempfile

        from cryptography.fernet import Fernet

        repo = os.environ.get("GITHUB_REPOSITORY") or "kevintaboas18/vertex_fund_os"
        url = f"https://raw.githubusercontent.com/{repo}/datos/Privado/privado.enc"
        req = urllib.request.Request(url, headers={"User-Agent": "vertex-fund-os"})
        with urllib.request.urlopen(req, timeout=30) as r:
            cifrado = r.read()

        # La misma derivacion que usa la app: cualquier cadena vale como clave.
        f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(clave.encode()).digest()))
        claro = f.decrypt(cifrado)

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "vertex.db")
            with tarfile.open(fileobj=io.BytesIO(claro), mode="r") as tar:
                m = next((x for x in tar.getmembers()
                          if x.isfile() and x.name == "vertex.db"), None)
                if m is None:
                    return []
                datos = tar.extractfile(m)
                if datos is None:
                    return []
                with open(db, "wb") as fh:
                    fh.write(datos.read())
            conn = sqlite3.connect(db)
            try:
                filas = conn.execute(
                    "SELECT email FROM usuarios WHERE email IS NOT NULL "
                    "AND email <> '' ORDER BY creado_ts").fetchall()
            finally:
                conn.close()
        # Se normaliza y se deduplica conservando el orden de registro.
        vistos, salida = set(), []
        for (correo,) in filas:
            c = (correo or "").strip().lower()
            if c and c not in vistos:
                vistos.add(c)
                salida.append(c)
        return salida
    except Exception as e:                       # noqa: BLE001
        print(f"[destinatarios] no pude leer las cuentas ({type(e).__name__}: "
              f"{str(e)[:90]}); se usa EMAIL_TO")
        return []


def destinatarios() -> list[str]:
    """A quién se le manda. Cada cuenta a SU email; si no hay cuentas legibles,
    lo que diga `EMAIL_TO`."""
    return _emails_de_las_cuentas() or [
        d.strip() for d in EMAIL_TO.split(",") if d.strip()]


def send_resend(subject: str, text: str, htmlbody: str, para: list[str],
                motivos: list[str] | None = None) -> int:
    """Un envío POR PERSONA, no uno con todos en el `to`.

    Meter a todo el mundo en el mismo `to` le enseña a cada usuario los correos
    de los demás. Son cuentas de desconocidos entre sí: eso es una fuga, no una
    comodidad.

    Devuelve cuántos salieron. Un fallo con un destinatario no cancela los
    otros — que uno tenga el buzón lleno no puede dejar a los demás sin correo.
    Si se pasa `motivos`, se le añade el porqué de cada rechazo para que quien
    llama pueda decirlo sin obligar a nadie a leer los logs del servidor.
    """
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        # Un KeyError pelado en el log del runner no dice que hay que ir a
        # Settings > Secrets. Esto sí.
        raise RuntimeError(
            "Falta RESEND_API_KEY. Definela en Settings > Secrets and "
            "variables > Actions del repositorio.")
    enviados = 0
    for uno in para:
        payload = json.dumps({
            "from": EMAIL_FROM, "to": [uno], "subject": subject,
            "text": text, "html": htmlbody,
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails", data=payload, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print(f"Resend {r.status} -> {uno}")
            enviados += 1
        except Exception as e:                   # noqa: BLE001
            # El remitente `onboarding@resend.dev` SOLO puede escribirle al
            # dueño de la cuenta de Resend. Con varios usuarios hay que
            # verificar un dominio propio; hasta entonces los demas rebotan
            # aqui, uno a uno.
            #
            # El motivo lo da Resend en el CUERPO del 4xx, no en el mensaje de
            # la excepcion --que solo dice "HTTP Error 403: Forbidden"--. Sin
            # leer el cuerpo, quien dispara esto ve "no se acepto" y tiene que
            # irse a los logs del servidor a adivinar por que.
            porque = f"{type(e).__name__}"
            cuerpo = getattr(e, "read", None)
            if callable(cuerpo):
                try:
                    d = json.loads(cuerpo().decode("utf-8", errors="replace"))
                    porque = str(d.get("message") or d.get("error") or d)[:200]
                except Exception:                # noqa: BLE001
                    pass
            elif isinstance(e, (TimeoutError, OSError)):
                porque = f"{type(e).__name__}: {str(e)[:120]}"
            print(f"Resend FALLO -> {uno}: {porque}", file=sys.stderr)
            if motivos is not None:
                motivos.append(f"{uno}: {porque}")
    return enviados


def main() -> int:
    now = datetime.now(ET)
    force = os.environ.get("FORCE") == "1"

    if not force:
        if now.hour not in VENTANA_ET:
            print(f"Son las {now.strftime('%H:%M')} ET, fuera de la ventana "
                  f"{VENTANA_ET.start}-{VENTANA_ET.stop - 1} — skip.")
            return 0
        if now.weekday() >= 5 or now.strftime("%Y-%m-%d") in MARKET_HOLIDAYS:
            print("Mercado cerrado hoy — skip.")
            return 0

    try:
        gainers, losers = movers(GAINERS), movers(LOSERS)
    except Exception as e:
        print(f"ERROR: FMP no contesto — {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not gainers and not losers:
        print("ERROR: FMP contesto sin movers utilizables.", file=sys.stderr)
        return 1

    subject, text, htmlbody = build_email(now, gainers, losers)
    para = destinatarios()

    if os.environ.get("DRY_RUN") == "1":
        print(f"[DRY RUN] to={', '.join(para)}\nsubject={subject}\n\n{text}")
        return 0
    enviados = send_resend(subject, text, htmlbody, para)
    print(f"Enviado a {enviados}/{len(para)} destinatarios: {subject}")
    # Cero de N es un fallo: el workflow tiene que salir en rojo. Que salieran
    # algunos y otros no ya se dijo, linea a linea, en stderr.
    return 0 if enviados else 1


if __name__ == "__main__":
    sys.exit(main())
