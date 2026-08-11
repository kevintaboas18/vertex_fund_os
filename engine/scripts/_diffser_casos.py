"""Casos del diferencial de SERIES: chainStore.ts, ivStore.ts, predictionStore.ts.

Lo que se compara aquí no es un número: es **el archivo**. Los tres stores son
la memoria del agente —el IV Rank real, el historial de cadena del sub-agente 4
y el diario que cierra el lazo de calibración—, y esa memoria solo sirve si el
archivo que escribe el port es el MISMO que escribe su app. Si difiere, nada
falla hoy: simplemente el rank se queda en el proxy para siempre y la
calibración nunca junta cinco muestras, sin un solo error en el log.

Deterministas (semilla fija), como los demás diferenciales: se comitea el
generador, no los datos.

Cada caso es una SECUENCIA de guardados sobre el mismo ticker, porque casi todo
lo interesante del formato solo aparece al segundo guardado: el dedupe por día,
el orden descendente y el recorte por cantidad. Un caso de un solo guardado no
distinguiría un store correcto de uno que pisa el archivo entero cada vez.
"""
import json
import random
from datetime import date, datetime, timedelta, timezone


def _dia(d: date) -> str:
    """Un día hábil: sábado y domingo los mueve `marketDateStr` y el caso
    dejaría de ser el que se quiso escribir."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def _habiles(inicio: date, n: int) -> list[date]:
    """`n` días hábiles CONSECUTIVOS, sin repetir.

    Avanzar por días de calendario y empujar el fin de semana hacia el lunes
    hace que tres fechas distintas caigan en el mismo día de mercado, y como
    los stores deduplican por día, el historial sale un 30% más corto de lo que
    pide el caso. Con eso ninguno llegaba a su tope y el recorte —el `.slice`
    que decide qué se olvida— no se probaba en ningún lado.
    """
    out, d = [], inicio
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _ahora(d: date, hora=16, minuto=30, seg=0, ms=0) -> str:
    """16:30 UTC = 12:30 ET: dentro de sesión, así que `marketDateStr` devuelve
    ese mismo día y no el anterior."""
    return (f"{_dia(d)}T{hora:02d}:{minuto:02d}:{seg:02d}.{ms:03d}Z")


def _cadena(rng, n):
    """Filas de cadena. Los nombres son los de SU API (`contractType`…), que es
    lo que come tanto su `structureScore` como el port."""
    out = []
    for i in range(n):
        strike = round(rng.uniform(5, 300), 2)
        out.append({
            "contractType": rng.choice(["call", "put"]),
            "expiration": rng.choice(["2026-09-18", "2026-10-16", "2027-01-15"]),
            "strike": strike,
            "openInterest": rng.randrange(0, 20000),
            "volume": rng.randrange(0, 30000),
            "notionalValue": round(rng.uniform(1e4, 8e7), 2),
        })
    return out


def _flujos(rng, n, dia):
    """Trades para `ivContextScore`. La IV representativa sale ponderada por
    premium, así que los premios tienen que variar o todas las fotos saldrían
    con el mismo `avgIv` y el caso no probaría nada."""
    out = []
    for i in range(n):
        out.append({
            "id": i + 1,
            "symbol": "X",
            "underlying": "X",
            "type": rng.choice(["call", "put"]),
            "strike": round(rng.uniform(10, 200), 2),
            "expiration": rng.choice(["2026-08-21", "2026-09-18", "2026-12-18"]),
            "dte": rng.choice([3, 20, 60, 140]),
            "price": round(rng.uniform(0.1, 12), 2),
            "size": rng.randrange(1, 500),
            "side": "AT_ASK",
            "aggression": "ask",
            "assetPrice": round(rng.uniform(10, 200), 2),
            "bid": 1.0,
            "ask": 1.2,
            "premium": round(rng.uniform(1e3, 9e6), 2),
            "delta": round(rng.uniform(-1, 1), 4),
            "gamma": 0.02,
            "theta": -0.05,
            "vega": 0.1,
            "iv": round(rng.uniform(0.05, 3.5), 6),
            "openInterest": rng.randrange(0, 9000),
            "volume": rng.randrange(0, 9000),
            "timestamp": f"{dia}T15:30:00Z",
        })
    return out


def casos():
    salida = {"chain": [], "iv": [], "predictions": []}

    # ── cadena ───────────────────────────────────────────────────────────────
    # `HISTORY_DAYS = 45`. El caso de 50 días es el que prueba el recorte, y el
    # recorte es por CANTIDAD (`.slice(0, 45)`), no por ventana temporal: con un
    # recorte por fecha, un ticker sin mirar durante meses perdería el historial
    # entero de golpe. Sin un caso que pase de 45 los dos criterios se ven igual.
    rng = random.Random(101)
    plan = [1, 2, 3, 5, 12, 45, 60]
    for k, dias in enumerate(plan):
        guardados = [{"rows": _cadena(rng, rng.choice([0, 1, 6, 40, 120])),
                      "now": _ahora(d)}
                     for d in _habiles(date(2026, 3, 2), dias)]
        salida["chain"].append({"ticker": f"CH{k:03d}", "saves": guardados})

    # Dos guardados el MISMO día: la foto de hoy se actualiza, no se duplica.
    rng = random.Random(102)
    salida["chain"].append({"ticker": "CHDUP", "saves": [
        {"rows": _cadena(rng, 20), "now": _ahora(date(2026, 3, 2), 14, 0)},
        {"rows": _cadena(rng, 35), "now": _ahora(date(2026, 3, 2), 20, 45, 12, 345)},
    ]})

    # Fuera de orden: se guarda el jueves y DESPUÉS el martes. El archivo tiene
    # que quedar igual de ordenado (más reciente primero) que si hubieran
    # llegado en orden — es el `sort` suyo, no el orden de llegada.
    rng = random.Random(103)
    salida["chain"].append({"ticker": "CHREV", "saves": [
        {"rows": _cadena(rng, 15), "now": _ahora(date(2026, 3, 5))},
        {"rows": _cadena(rng, 15), "now": _ahora(date(2026, 3, 3))},
        {"rows": _cadena(rng, 15), "now": _ahora(date(2026, 3, 4))},
    ]})

    # Cadena vacía: `structureScore([])` da un score válido y la foto SE GUARDA.
    salida["chain"].append({"ticker": "CHVAC", "saves": [
        {"rows": [], "now": _ahora(date(2026, 3, 2))}]})

    # ── IV ───────────────────────────────────────────────────────────────────
    # `IV_HISTORY_DAYS = 365`, y `MIN_IV_HISTORY_DAYS = 60` es donde el rank real
    # sustituye al proxy. Los casos de 60 y 70 días cruzan ese umbral: es el
    # punto exacto donde una clave mal escrita dejaría de notarse nunca.
    # El caso de 365 es el único que llega al tope: son 365 días HÁBILES, casi
    # un año y medio de calendario. Cuesta unos segundos y es lo que prueba que
    # el recorte del historial de IV es por cantidad y no por ventana.
    rng = random.Random(201)
    for k, dias in enumerate([1, 2, 7, 60, 70, 365]):
        guardados = [{"rows": _flujos(rng, rng.choice([1, 5, 30]), d.isoformat()),
                      "closes": [], "now": _ahora(d)}
                     for d in _habiles(date(2026, 1, 5), dias)]
        salida["iv"].append({"ticker": f"IV{k:03d}", "saves": guardados})

    # Sin filas con IV → su `ivContextScore` sale `_EMPTY` con `iv.current` nulo
    # y `saveIvSnapshot` NO escribe. Que no escriba es parte del formato: un día
    # sin datos no puede existir en el historial como si valiera cero.
    salida["iv"].append({"ticker": "IVNUL", "saves": [
        {"rows": [], "closes": [], "now": _ahora(date(2026, 1, 5))}]})

    # Un día sin IV DESPUÉS de uno bueno: el archivo se queda como estaba, no se
    # vacía ni se le añade una fila hueca.
    rng = random.Random(202)
    salida["iv"].append({"ticker": "IVMIX", "saves": [
        {"rows": _flujos(rng, 8, "2026-01-05"), "closes": [], "now": _ahora(date(2026, 1, 5))},
        {"rows": [], "closes": [], "now": _ahora(date(2026, 1, 6))},
        {"rows": _flujos(rng, 8, "2026-01-07"), "closes": [], "now": _ahora(date(2026, 1, 7))},
    ]})

    # Mismo día dos veces, con `minIv`/`maxIv`/`frontSkew` distintos.
    rng = random.Random(203)
    salida["iv"].append({"ticker": "IVDUP", "saves": [
        {"rows": _flujos(rng, 4, "2026-01-05"), "closes": [], "now": _ahora(date(2026, 1, 5), 14)},
        {"rows": _flujos(rng, 40, "2026-01-05"), "closes": [], "now": _ahora(date(2026, 1, 5), 20, 59, 59, 999)},
    ]})

    # Un solo vencimiento → `frontSkew` es `null`, y el nulo tiene que llegar al
    # archivo como `null` y no desaparecer de la clave.
    rng = random.Random(204)
    unico = _flujos(rng, 6, "2026-01-05")
    for f in unico:
        f["expiration"] = "2026-09-18"
    salida["iv"].append({"ticker": "IVSKW", "saves": [
        {"rows": unico, "closes": [], "now": _ahora(date(2026, 1, 5))}]})

    # ── predicciones ─────────────────────────────────────────────────────────
    # `JOURNAL_DAYS = 120`. El caso de 125 prueba el recorte.
    rng = random.Random(301)
    for k, dias in enumerate([1, 3, 10, 130]):
        guardados = []
        for d in _habiles(date(2026, 2, 2), dias):
            spot = round(rng.uniform(5, 400), 4)
            guardados.append({"snap": {
                "spot": spot,
                "horizonDays": 20,
                "bear": round(spot * 0.9, 4),
                "base": round(spot * 1.02, 4),
                "bull": round(spot * 1.15, 4),
                "direction": rng.choice(["up", "down", "flat"]),
                "confidence": rng.randrange(0, 101),
            }, "now": _ahora(d)})
        salida["predictions"].append({"ticker": f"PR{k:03d}", "saves": guardados})

    # Mismo día dos veces con el MISMO horizonte: pisa, en los dos lados.
    salida["predictions"].append({"ticker": "PRDUP", "saves": [
        {"snap": {"spot": 20.0, "horizonDays": 20, "bear": 18.0, "base": 20.4,
                  "bull": 23.0, "direction": "up", "confidence": 55},
         "now": _ahora(date(2026, 2, 2), 14)},
        {"snap": {"spot": 21.5, "horizonDays": 20, "bear": 19.0, "base": 21.9,
                  "bull": 24.5, "direction": "up", "confidence": 61},
         "now": _ahora(date(2026, 2, 2), 20, 30)},
    ]})

    # DIVERGENCIA DECLARADA: tres horizontes el mismo día. Él deduplica solo por
    # fecha y se queda con el último; Vertex sirve los tres en la misma respuesta
    # y guarda los tres, porque con su clave dos de cada tres se perderían en
    # silencio y la calibración se quedaría con un tercio de las muestras.
    salida["predictions"].append({
        "ticker": "PRHOR",
        "divergencia": "dedupe por (fecha, horizonte) en vez de solo fecha",
        "saves": [
            {"snap": {"spot": 20.0, "horizonDays": h, "bear": 18.0, "base": 20.4,
                      "bull": 23.0, "direction": "up", "confidence": 50 + h},
             "now": _ahora(date(2026, 2, 2), 14, h)}
            for h in (5, 20, 40)
        ]})

    # Fuera de orden, como en la cadena.
    salida["predictions"].append({"ticker": "PRREV", "saves": [
        {"snap": {"spot": 30.0, "horizonDays": 20, "bear": 27.0, "base": 30.6,
                  "bull": 34.5, "direction": "up", "confidence": 40},
         "now": _ahora(date(2026, 2, 5))},
        {"snap": {"spot": 28.0, "horizonDays": 20, "bear": 25.2, "base": 28.6,
                  "bull": 32.2, "direction": "down", "confidence": 44},
         "now": _ahora(date(2026, 2, 3))},
    ]})

    return salida


if __name__ == "__main__":
    print(json.dumps(casos()))
