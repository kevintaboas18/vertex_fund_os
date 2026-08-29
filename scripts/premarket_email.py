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


def bloque_tesis(avisos: dict | None) -> tuple[str, str, str]:
    """Las tesis que se rompieron por su propio criterio: texto, HTML y asunto.

    Va ARRIBA del correo, antes de los movers. Los movers son contexto del
    mercado; esto es del libro de quien lo lee, y hasta hoy la tubería llevaba
    sólo lo primero. La tesis guardaba desde el principio qué la invalidaría
    —un nivel exacto y medible— y nadie lo miraba nunca.

    Lo que NO se pudo medir se dice. Un vigilante que calla cuando le faltan
    datos se lee igual que uno que dice que todo va bien, y no es lo mismo.
    """
    a = avisos or {}
    rotas, sin_datos = a.get("rotas") or [], a.get("sin_datos") or []
    if not a.get("revisadas"):
        return "", "", ""

    def _linea(r):
        lado = ("perdió su soporte" if r.get("lado") == "soporte"
                else "superó su resistencia")
        n, c = r.get("nivel"), r.get("cierre")
        donde = f" (nivel ${n:,.2f})" if isinstance(n, (int, float)) else ""
        cerro = f", cerró en ${c:,.2f}" if isinstance(c, (int, float)) else ""
        return f"{r.get('ticker')}: {lado}{donde}{cerro}"

    en_pie = len(a.get("en_pie") or [])
    if rotas:
        cabeza = f"⚠️ {len(rotas)} tesis se rompió" if len(rotas) == 1 else \
                 f"⚠️ {len(rotas)} tesis se rompieron"
        subject = f"{cabeza} — " if len(rotas) else ""
        cuerpo = "\n".join(f"- {_linea(r)}" for r in rotas)
        html_filas = "".join(
            f'<li style="margin:4px 0;">{html.escape(_linea(r))}</li>' for r in rotas)
        html_cuerpo = (f'<ul style="margin:8px 0 0;padding-left:20px;font-size:14px;">'
                       f'{html_filas}</ul>')
        color = "#d63031"
    else:
        cabeza = "✅ Ninguna tesis rota"
        subject = ""
        cuerpo = "Ninguna de las tesis abiertas rompió su nivel de invalidación."
        html_cuerpo = ('<p style="font-size:14px;margin:8px 0 0;">Ninguna de las tesis '
                       'abiertas rompió su nivel de invalidación.</p>')
        color = "#00b894"

    pie = f"{en_pie} en pie"
    if sin_datos:
        nombres = ", ".join(str(r.get("ticker")) for r in sin_datos[:5])
        pie += f" · {len(sin_datos)} sin poder medir ({nombres})"

    texto = (f"TUS TESIS — {cabeza}\n{cuerpo}\n({pie})\n\n"
             "Es un aviso para volver a mirarlas, no una orden de compra o venta.\n")
    htmlb = (f'<h2 style="font-size:15px;margin:0 0 10px;color:{color};">'
             f'{html.escape(cabeza)} — tus tesis</h2>{html_cuerpo}'
             f'<p style="font-size:12px;color:#888;margin:8px 0 0;">{html.escape(pie)} · '
             'Es un aviso para volver a mirarlas, no una orden de compra o venta.</p>'
             '<hr style="border:none;border-top:1px solid #eee;margin:18px 0;">')
    return texto, htmlb, subject


def bloque_salud(salud: dict | None) -> tuple[str, str]:
    """Qué fuentes están vivas. Una línea si todo va, y la lista si no.

    Existe por la cookie de MarketSnack: es una cookie de SESIÓN y caduca
    sola. Cuando caduca, cinco de los seis sub-agentes se quedan sin dato y
    sólo sobrevive Estructura — y hasta ahora sólo te enterabas si abrías el
    panel. El agente con el que se opera podía llevar días a uno de seis.

    Se dice también cuando NO se pudo comprobar. «No sé si está sano» y «está
    sano» son cosas distintas, y la segunda es la que no hay que fingir.
    """
    # `None` (no me pasaron nada) y `{"ok": None}` (lo intenté y no pude) NO
    # son lo mismo. El primero es el correo de siempre, sin bloque; el segundo
    # es una advertencia. Fundirlos hacía que un correo antiguo dijera que no
    # se pudo comprobar la salud, que es una alarma inventada.
    if salud is None:
        return "", ""
    s = salud
    if s.get("ok") is None:
        txt = "SALUD: no se pudo comprobar el estado de las fuentes."
        return (txt + "\n",
                '<p style="font-size:12px;color:#888;margin:0 0 14px;">'
                + html.escape(txt) + "</p>")
    rotos = s.get("rotos") or []
    if not rotos:
        txt = f"SALUD: las {s.get('total', 0)} fuentes responden."
        return (txt + "\n",
                '<p style="font-size:12px;color:#00b894;margin:0 0 14px;">✅ '
                + html.escape(txt) + "</p>")

    def _linea(c):
        base = f"{c.get('check')}: {c.get('detalle')}"
        if c.get("impacto"):
            base += f" — {c['impacto']}"
        if c.get("arreglo"):
            base += f" [{c['arreglo']}]"
        return base

    cuerpo = "\n".join(f"- {_linea(c)}" for c in rotos)
    txt = (f"⚠️ SALUD: {len(rotos)} de {s.get('total', 0)} fuentes con problema\n"
           f"{cuerpo}\n")
    filas = "".join(f'<li style="margin:4px 0;">{html.escape(_linea(c))}</li>'
                    for c in rotos)
    htmlb = (f'<h2 style="font-size:15px;margin:0 0 8px;color:#d63031;">⚠️ '
             f'{len(rotos)} de {s.get("total", 0)} fuentes con problema</h2>'
             f'<ul style="margin:0 0 14px;padding-left:20px;font-size:13px;">{filas}</ul>')
    return txt, htmlb


def build_email(now: datetime, gainers: list[dict], losers: list[dict],
                avisos: dict | None = None,
                salud: dict | None = None) -> tuple[str, str, str]:
    fecha = f"{DIAS[now.weekday()]} {now.day} {MESES[now.month]} {now.year}"
    tesis_txt, tesis_html, tesis_asunto = bloque_tesis(avisos)
    salud_txt, salud_html = bloque_salud(salud)
    subject = f"{tesis_asunto}📈 Pre-Market Movers — {fecha}"

    big = sorted([r for r in gainers + losers if r["mcap"] >= LARGE_CAP_MIN],
                 key=lambda r: -abs(r["pct"]))[:6]
    small_g = [r for r in gainers if r["mcap"] < LARGE_CAP_MIN][:5]
    small_l = [r for r in losers if r["mcap"] < LARGE_CAP_MIN][:5]

    def txt_rows(rows):
        return "\n".join(f"- {r['ticker']} {r['name']}: {fmt_pct(r['pct'])} a ${r['price']}"
                         for r in rows)

    text = f"""PRE-MARKET MOVERS — {fecha}
(Pre-market en vivo, {now.strftime('%H:%M')} ET — FMP)

{salud_txt}
{tesis_txt}
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
    {salud_html}
    {tesis_html}
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


def destinatarios() -> list[str]:
    """A quien se le manda en una corrida MANUAL: lo que diga `EMAIL_TO`.

    El envio automatico no pasa por aqui. Vive en `/api/premarket/enviar`, que
    lee `usuarios` de la base VIVA y le manda a cada cuenta a su correo. Aqui
    hubo 65 lineas que bajaban `Privado/privado.enc`, lo desciframban con
    Fernet, extraian un tar y abrian SQLite para llegar a la misma lista --un
    camino que ningun disparador recorria y que ningun test ejecutaba, porque
    todos lo sustituian. Se borro."""
    return [d.strip() for d in EMAIL_TO.split(",") if d.strip()]


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
                     "Content-Type": "application/json",
                     # Sin esto urllib manda "Python-urllib/3.11" y Cloudflare
                     # --que es quien atiende delante de la API de Resend-- lo
                     # rechaza con "403, error code: 1010": acceso bloqueado
                     # por la firma del cliente. Ni la clave ni el destinatario
                     # ni el remitente llegaban a evaluarse, asi que el fallo
                     # se veia identico a "Resend no te acepta el correo" y
                     # mando a revisar tres cosas que estaban bien.
                     #
                     # A FMP y al almacen ya se les mandaba; aqui faltaba.
                     "User-Agent": "vertex-fund-os"},
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
            # El CODIGO va siempre por delante, y esta es la segunda vuelta de
            # esto: la primera version solo intentaba leer el cuerpo JSON, y
            # cuando ese `read()` no daba nada el log quedaba en "HTTPError" a
            # secas -- que no distingue un 403 (no puedes escribir a ese
            # destinatario) de un 422 (remitente invalido) ni de un 429
            # (cuota), que se arreglan de tres formas distintas.
            codigo = getattr(e, "code", None)
            detalle = ""
            cuerpo = getattr(e, "read", None)
            if callable(cuerpo):
                try:
                    crudo = cuerpo().decode("utf-8", errors="replace").strip()
                    try:
                        d = json.loads(crudo)
                        detalle = str(d.get("message") or d.get("error") or d)
                    except Exception:            # noqa: BLE001
                        detalle = crudo          # no era JSON: vale igual
                except Exception:                # noqa: BLE001
                    pass
            if not detalle:
                detalle = str(getattr(e, "reason", "") or e) or type(e).__name__
            porque = (f"HTTP {codigo} — {detalle}"[:220] if codigo
                      else f"{type(e).__name__}: {detalle}"[:220])
            print(f"Resend FALLO -> {uno}: {porque}", file=sys.stderr)
            if motivos is not None:
                motivos.append(f"{uno}: {porque}")
    return enviados


def motivo_para_saltar(ahora: datetime) -> str | None:
    """Por que NO toca mandar ahora, o `None` si si toca.

    Una sola definicion de la regla. Estaba escrita dos veces --aqui y en
    `/api/premarket/enviar`-- y son la misma politica: un feriado de 2028 o un
    cambio de ventana habria que ponerlo en los dos sitios, que es como las
    copias se separan.
    """
    if ahora.hour not in VENTANA_ET:
        return (f"son las {ahora.strftime('%H:%M')} ET, fuera de la ventana "
                f"{VENTANA_ET.start}-{VENTANA_ET.stop - 1}")
    if ahora.weekday() >= 5 or ahora.strftime("%Y-%m-%d") in MARKET_HOLIDAYS:
        return "mercado cerrado hoy"
    return None


def main() -> int:
    now = datetime.now(ET)
    force = os.environ.get("FORCE") == "1"

    if not force:
        motivo = motivo_para_saltar(now)
        if motivo:
            print(f"{motivo} — skip.")
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
