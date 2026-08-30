"""Páginas del feed de MarketSnack, para comparar SU cliente de flujo.

`marketsnack.ts` es el otro módulo suyo que habla con la red y que por eso se
quedó sin diferencial. Lo que decide tampoco es la red: es la URL que pide
—qué filtros, en qué orden, con qué piso de premium— y sobre todo CUÁNDO PARA.
Son cuatro condiciones de parada encadenadas (lista vacía, ventana cubierta,
tope de páginas, no hay token) y de ellas sale cuánto flujo ven los
sub-agentes 1, 2 y 3. Parar una página antes no rompe nada visible: solo hace
que la Agresividad y la Inusualidad se calculen sobre menos operaciones.
"""
import json

AHORA_MS = 1_756_339_200_000          # 2025-08-28T00:00:00Z, fijo
#: El reloj de los DOS lados sale de aquí. Escribir la fecha a mano en el
#: comparador ya costó una tanda entera de falsos positivos: el corpus estaba
#: en 2025 y la constante tecleada decía 2026, así que el port cortaba la
#: ventana en la primera página y parecía culpa suya.


def t(ms):
    """Un trade con la marca de tiempo en ISO, que es como la manda el feed."""
    from datetime import datetime, timezone
    return {"timestamp": datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "premium": 250000, "symbol": "AAPL"}


DIA = 86_400_000


def casos():
    out = []

    def caso(nombre, paginas, **opts):
        out.append({"nombre": nombre, "paginas": paginas, "opts": opts})

    # ── La URL: filtros, orden y el piso de premium ──────────────────────
    caso("por defecto", [{"list": [t(AHORA_MS)]}])
    caso("con símbolo", [{"list": [t(AHORA_MS)]}], symbol="AAPL")
    caso("periodo distinto", [{"list": [t(AHORA_MS)]}], period="1d")
    for mp in (0, 1, 250000, 250000.9, 0.4, -5, None):
        caso(f"minPremium={mp!r}", [{"list": [t(AHORA_MS)]}], minPremium=mp)
    caso("con token en la 2ª", [
        {"list": [t(AHORA_MS)], "meta": {"next_page_token": "tk1"}},
        {"list": [t(AHORA_MS - DIA)]},
    ])

    # ── Las cuatro paradas ───────────────────────────────────────────────
    caso("lista vacía para", [
        {"list": [], "meta": {"next_page_token": "tk1"}},
        {"list": [t(AHORA_MS)]},
    ])
    caso("sin token para", [{"list": [t(AHORA_MS)]}])
    caso("tope de páginas con token detrás", [
        {"list": [t(AHORA_MS)], "meta": {"next_page_token": "tk1"}},
        {"list": [t(AHORA_MS - DIA)], "meta": {"next_page_token": "tk2"}},
        {"list": [t(AHORA_MS - 2 * DIA)], "meta": {"next_page_token": "tk3"}},
    ], maxPages=2)
    caso("tope de páginas sin token detrás", [
        {"list": [t(AHORA_MS)], "meta": {"next_page_token": "tk1"}},
        {"list": [t(AHORA_MS - DIA)]},
    ], maxPages=2)

    # ── La ventana: `targetDays` y la marca de tiempo más vieja ──────────
    #
    # Aquí es donde su `Date.parse` y el `fromisoformat` del port se pueden
    # separar: el primero traga formatos que el segundo rechaza, y una marca
    # naive (sin zona) es un número para él y un `TypeError` para nosotros.
    largo = [{"list": [t(AHORA_MS - i * DIA)],
              "meta": {"next_page_token": f"tk{i}"}} for i in range(1, 9)]
    caso("ventana cubierta", largo, targetDays=3)
    caso("ventana amplia", largo, targetDays=30)
    # Fechas ANTERIORES al reloj clavado y fuera de la ventana de 3 días: si
    # fueran del futuro la comprobación no se dispara nunca y estos doce casos
    # no medirían nada. (Pasó: con marcas de 2026 y el reloj en 2025 los doce
    # salían «idénticos» sin llegar a comparar formatos.)
    for marca in ("2025-08-20T00:00:00Z", "2025-08-20T00:00:00+00:00",
                  "2025-08-20T00:00:00", "2025-08-20 00:00:00",
                  "2025-08-20", "20 Aug 2025 00:00:00 GMT",
                  "2025-08-20T00:00:00.123456Z", "basura", "", None, 0,
                  "2025-13-45T00:00:00Z"):
        caso(f"marca={marca!r}", [
            {"list": [dict(t(AHORA_MS), timestamp=marca)],
             "meta": {"next_page_token": "tk1"}},
            {"list": [t(AHORA_MS - 40 * DIA)]},
        ], targetDays=3)

    # ── Formas rotas ─────────────────────────────────────────────────────
    caso("list ausente", [{"meta": {"next_page_token": "tk1"}},
                          {"list": [t(AHORA_MS)]}])
    caso("meta ausente", [{"list": [t(AHORA_MS)]}])
    caso("meta nula", [{"list": [t(AHORA_MS)], "meta": None}])
    caso("token vacío", [{"list": [t(AHORA_MS)], "meta": {"next_page_token": ""}}])
    caso("trade sin timestamp", [
        {"list": [{"premium": 1}], "meta": {"next_page_token": "tk1"}},
        {"list": [t(AHORA_MS - 40 * DIA)]},
    ], targetDays=3)
    return out


if __name__ == "__main__":
    print(json.dumps(casos()))
