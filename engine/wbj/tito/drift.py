"""Drift Sentiment — el mapa de POSICIONES de la cadena, por vencimiento mensual.

Port del `drift-sentiment-agent` de Víctor, adaptado a la cadena que Vertex ya
baja. Mide algo DISTINTO de `gex.py`, y la diferencia importa:

    gex.py     → gamma: `γ × OI × 100 × spot² × 0,01`. Es un MODELO (la gamma
                 sale de Black-Scholes con una IV estimada) y contesta «¿dónde
                 tiene que cubrirse el dealer?». Se apaga lejos del dinero.
    drift.py   → interés abierto y nocional: `OI` y `OI × 100 × strike`. Son
                 HECHOS publicados por el mercado, sin modelo que pueda fallar,
                 y contestan «¿dónde hay más gente y más dinero?».

Sobre la misma cadena los dos apuntan a strikes distintos, y ninguno está mal.
Medido con un ejemplo de spot 300: el strike 400 tenía el mayor OI de la cadena
(11.000 calls) y su GEX era 0,4M —menos que un strike con la tercera parte de
contratos—, porque la gamma se desploma al alejarse del dinero.

**El sesgo del nocional, dicho con su tamaño exacto.** El Magneto **es** el
strike con más nocional neto, y sale donde esté ese nocional: por encima del
precio si mandan las calls, por debajo si mandan los puts, y con el signo
puesto para que se sepa cuál de las dos. No tira hacia arriba.

Lo que sí hay es un sesgo estrecho, y solo ese: `nocional = OI × 100 × strike`
multiplica por el strike, así que **a igual número de contratos** gana el
strike más alto. Es un desempate entre concentraciones parecidas, no una
dirección — una pared de puts por debajo del precio se lleva el Magneto sin
discusión, porque el desempate solo actúa cuando los contratos empatan.

Todo aquí son funciones puras: ni red, ni disco, ni reloj propio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

__all__ = [
    "DTE_OBJETIVO",
    "BucketDrift",
    "DriftAnalysis",
    "es_mensual",
    "vencimientos_mensuales",
    "vencimiento_mas_cercano",
    "tolerancia_dte",
    "muro_calls",
    "muro_puts",
    "nocional_por_strike",
    "magneto",
    "sigma_proyectada",
    "_cerca_del_spot",
    "clasifica_deriva",
    "drift_analysis",
]

#: Los cuatro plazos de su especificación: dos de sentimiento largo y dos de
#: corto. El de ~30 días es el único que solapa con lo que el agente ya cubre
#: (sus horizontes son 10/20/30); los otros tres son terreno que hoy no ve.
DTE_OBJETIVO: tuple[tuple[str, int], ...] = (
    ("Largo", 320),
    ("Largo", 120),
    ("Corto", 90),
    ("Corto", 30),
)

#: Un contrato son 100 acciones.
_MULTIPLICADOR = 100


@dataclass(frozen=True)
class _Fila:
    """Lo mínimo de un contrato para este análisis."""

    tipo: str          # "call" | "put"
    vencimiento: date
    strike: float
    open_interest: int

    @property
    def es_call(self) -> bool:
        return self.tipo == "call"

    @property
    def acciones(self) -> int:
        return self.open_interest * _MULTIPLICADOR

    @property
    def nocional(self) -> float:
        """Positivo en calls, negativo en puts — su convención de signo."""
        v = self.acciones * self.strike
        return v if self.es_call else -v


@dataclass
class BucketDrift:
    """Un plazo resuelto: sus muros, su imán y su cono."""

    etiqueta: str
    sentimiento: str
    dte_objetivo: int
    vencimiento: str
    dte_real: int
    muro_calls: float | None
    muro_calls_oi: int
    muro_puts: float | None
    muro_puts_oi: int
    magneto: float | None
    magneto_nocional: float
    sigma: float | None
    total_oi: int
    nocional_neto: float
    deriva: str
    breakout: bool
    #: Dos plazos pueden caer en el MISMO vencimiento si la cadena es pobre.
    #: Sin decirlo, dos filas idénticas parecen un error de cálculo.
    duplicado: bool = False


@dataclass
class DriftAnalysis:
    spot: float
    buckets: list[BucketDrift] = field(default_factory=list)
    #: Plazos que NO se pudieron resolver, con su motivo. Sin evidencia no hay
    #: número: antes que etiquetar «320 días» un vencimiento de 120, no se pinta.
    sin_datos: list[dict] = field(default_factory=list)
    mensuales: int = 0
    motivo: str = ""


def es_mensual(v: date) -> bool:
    """El 3.er viernes del mes, que es donde vence el contrato estándar.

    Los semanales caen en otros viernes y quedan fuera a propósito: el dinero
    institucional se posiciona en los mensuales, y mezclarlos mete ruido.

    OJO con una limitación de su regla, que se conserva: en una semana con
    festivo el vencimiento se adelanta al jueves (Viernes Santo) y este filtro
    lo descarta. Es raro y se prefiere perder ese mes a inventar una excepción.
    """
    return v.weekday() == 4 and 15 <= v.day <= 21


def vencimientos_mensuales(filas: list[_Fila]) -> list[date]:
    return sorted({f.vencimiento for f in filas if es_mensual(f.vencimiento)})


def tolerancia_dte(objetivo: int) -> int:
    """Cuánto puede alejarse un vencimiento de su objetivo antes de rechazarlo.

    Existe por un fallo silencioso concreto: la cadena se corta en 40 páginas y
    los vencimientos LEJANOS son los primeros en caer. Sin tope, el plazo de 320
    días resolvería al de 120 que sí llegó y lo pintaría con la etiqueta de 320.
    No falla — **miente**.

    Un ciclo mensual y medio (45 días) o el 25% del objetivo, lo que sea mayor.
    """
    return max(45, int(objetivo * 0.25))


def vencimiento_mas_cercano(vencs: list[date], objetivo: int,
                            hoy: date) -> date | None:
    """El mensual futuro cuyo DTE está más cerca del objetivo, o `None`."""
    futuros = [v for v in vencs if (v - hoy).days >= 0]
    if not futuros:
        return None
    return min(futuros, key=lambda v: abs((v - hoy).days - objetivo))


def muro_calls(filas: list[_Fila]) -> tuple[float, int] | None:
    """Strike con más interés abierto entre las calls. Un recuento, no un modelo."""
    calls = [f for f in filas if f.es_call and f.open_interest > 0]
    if not calls:
        return None
    top = max(calls, key=lambda f: f.open_interest)
    return top.strike, top.open_interest


def muro_puts(filas: list[_Fila]) -> tuple[float, int] | None:
    puts = [f for f in filas if not f.es_call and f.open_interest > 0]
    if not puts:
        return None
    top = max(puts, key=lambda f: f.open_interest)
    return top.strike, top.open_interest


def nocional_por_strike(filas: list[_Fila]) -> dict[float, float]:
    """Nocional neto acumulado por strike (calls +, puts −)."""
    acc: dict[float, float] = {}
    for f in filas:
        acc[f.strike] = acc.get(f.strike, 0.0) + f.nocional
    return acc


def magneto(filas: list[_Fila]) -> tuple[float, float] | None:
    """El strike con MAYOR NOCIONAL NETO EN VALOR ABSOLUTO.

    Su signo es la polaridad: positivo = dominan las calls (atracción en su
    modelo), negativo = dominan las puts (rechazo).

    Sale donde esté el nocional: por encima o por debajo del precio, según qué
    lado pese más. El único sesgo es de desempate — al multiplicar por el
    strike, entre dos concentraciones de contratos PARECIDAS gana la más alta.
    """
    acc = nocional_por_strike(filas)
    if not acc:
        return None
    s = max(acc, key=lambda k: abs(acc[k]))
    return s, acc[s]


def sigma_proyectada(spot: float, iv: float | None, dte: int) -> float | None:
    """`σ = spot × IV × √(DTE/365)` — una desviación típica de precio.

    **La IV no es la de la cadena.** El plan de Massive no devuelve
    `implied_volatility` por contrato, así que aquí entra la IV estimada de la
    volatilidad realizada (`gex.estimate_iv`), que es la misma que ya usa el
    resto del motor. Es un sustituto declarado, no la IV implícita real.
    """
    if iv is None or iv <= 0 or dte <= 0 or spot <= 0:
        return None
    return spot * iv * math.sqrt(dte / 365.0)


def clasifica_deriva(spot: float, mc: float, mp: float,
                     mag_strike: float, mag_nocional: float) -> tuple[str, bool]:
    """Devuelve `(frase, es_breakout)` — su §6, literal.

    Dentro del rango entre los dos muros manda la polaridad del Magneto; fuera
    del rango es ruptura hacia el muro que queda en la dirección del viaje.
    """
    lo, hi = sorted((mp, mc))
    if not (lo <= spot <= hi):
        if spot > hi:
            return (f"RUPTURA al alza: el precio está fuera del rango de muros "
                    f"[{lo:,.2f} – {hi:,.2f}]. El siguiente muro está en "
                    f"{mc:,.2f}.", True)
        return (f"RUPTURA a la baja: el precio está fuera del rango de muros "
                f"[{lo:,.2f} – {hi:,.2f}]. El siguiente muro está en "
                f"{mp:,.2f}.", True)
    if mag_nocional > 0:
        return (f"DENTRO DEL RANGO · atracción: el imán de nocional está en "
                f"{mag_strike:,.2f} y domina el lado de las calls. El precio "
                f"tiende a gravitar hacia él.", False)
    return (f"DENTRO DEL RANGO · rechazo: el imán de nocional está en "
            f"{mag_strike:,.2f} y domina el lado de las puts. El precio tiende "
            f"a irse a los extremos del rango.", False)


def _a_filas(chain, hoy: date) -> list[_Fila]:
    """`ChainRow` de Vertex → la fila mínima de aquí. Descarta lo inservible."""
    fuera: list[_Fila] = []
    for r in chain or []:
        tipo = getattr(r, "contract_type", "")
        if tipo not in ("call", "put"):
            continue
        try:
            strike = float(getattr(r, "strike", 0) or 0)
            oi = int(getattr(r, "open_interest", 0) or 0)
        except (TypeError, ValueError):
            continue
        if strike <= 0 or oi <= 0:
            continue
        crudo = str(getattr(r, "expiration", "") or "")[:10]
        try:
            v = date.fromisoformat(crudo)
        except ValueError:
            continue
        fuera.append(_Fila(tipo=tipo, vencimiento=v, strike=strike,
                           open_interest=oi))
    return fuera


def _cerca_del_spot(filas: list[_Fila], spot: float,
                    pct: float | None) -> list[_Fila]:
    """Recorta a los strikes dentro de ±`pct` del spot. `None` = sin recorte.

    **Esto NO es suyo, y por eso entra por parámetro.** Su
    `polygon_client.fetch_chain` baja la cadena entera y sus `walls.py` /
    `magneto.py` la miran completa: ni el código, ni la especificación §4-§5,
    ni el README mencionan ninguna ventana de strikes.

    Es política de Vertex, y el motivo es de presentación: el panel pinta el
    número del agente y el suyo **en la misma tarjeta**, separados por una
    barra. El del agente sale de `gex.NEAR_SPOT_PCT` —±20% del spot, constante
    SUYA, de su `gex.ts`—. Medir uno sobre ±20% y el otro sobre la cadena
    entera es comparar dos universos distintos y presentarlos como si fueran
    lo mismo: con la acción a $180, el mayor OI de calls puede estar en un
    strike de $120 comprado hace un año, y ese número al lado del muro de
    gamma no significa nada.

    Con `pct=None` el análisis es exactamente el suyo, y así lo compara
    `diff_drift.sh`. Quien quiera su comportamiento literal no pasa nada.
    """
    if pct is None or not (pct > 0) or not (spot > 0):
        return filas
    lo, hi = spot * (1 - pct), spot * (1 + pct)
    return [f for f in filas if lo <= f.strike <= hi]


def drift_analysis(chain, spot: float, hoy: date,
                   iv: float | None = None,
                   near_pct: float | None = None) -> DriftAnalysis:
    """El análisis completo: cuatro plazos, sus muros, su imán y su cono.

    `chain` son las `ChainRow` que Vertex ya tiene en memoria — **no se baja
    nada**. `iv` es la estimada del motor; sin ella los conos salen a `None` y
    el resto del análisis sigue en pie.

    `near_pct` recorta los strikes a ±ese % del spot antes de buscar muros e
    imán. **No es suyo**: él mira la cadena entera. Ver `_cerca_del_spot`.
    Con `None` —el valor por defecto— el análisis es literalmente el suyo.
    """
    filas = _a_filas(chain, hoy)
    if not filas:
        return DriftAnalysis(spot=spot,
                             motivo="La cadena no trajo contratos utilizables.")
    if not (spot > 0):
        return DriftAnalysis(spot=spot, motivo="Sin precio del subyacente.")

    mensuales = vencimientos_mensuales(filas)
    salida = DriftAnalysis(spot=spot, mensuales=len(mensuales))
    if not mensuales:
        salida.motivo = ("La cadena no trae vencimientos mensuales (3.er "
                         "viernes). Solo semanales.")
        return salida

    vistos: set[date] = set()
    for sentimiento, objetivo in DTE_OBJETIVO:
        etiqueta = f"{sentimiento} ~{objetivo} DTE"
        v = vencimiento_mas_cercano(mensuales, objetivo, hoy)
        if v is None:
            salida.sin_datos.append({"etiqueta": etiqueta, "dte_objetivo": objetivo,
                                     "motivo": "sin vencimientos futuros"})
            continue
        dte = (v - hoy).days
        tol = tolerancia_dte(objetivo)
        if abs(dte - objetivo) > tol:
            # El caso que la cadena cortada provoca. Se DICE, no se disfraza.
            salida.sin_datos.append({
                "etiqueta": etiqueta, "dte_objetivo": objetivo,
                "motivo": (f"el mensual más cercano está a {dte} días y el "
                           f"objetivo son {objetivo}: la cadena no llega tan "
                           f"lejos")})
            continue

        propias = _cerca_del_spot(
            [f for f in filas if f.vencimiento == v], spot, near_pct)
        mc = muro_calls(propias)
        mp = muro_puts(propias)
        mag = magneto(propias)
        if mc is None or mp is None or mag is None:
            salida.sin_datos.append({
                "etiqueta": etiqueta, "dte_objetivo": objetivo,
                "motivo": ("ese vencimiento no tiene calls y puts con interés "
                           "abierto cerca del precio")})
            continue

        deriva, ruptura = clasifica_deriva(spot, mc[0], mp[0], mag[0], mag[1])
        salida.buckets.append(BucketDrift(
            etiqueta=etiqueta, sentimiento=sentimiento, dte_objetivo=objetivo,
            vencimiento=v.isoformat(), dte_real=dte,
            muro_calls=mc[0], muro_calls_oi=mc[1],
            muro_puts=mp[0], muro_puts_oi=mp[1],
            magneto=mag[0], magneto_nocional=mag[1],
            sigma=sigma_proyectada(spot, iv, dte),
            total_oi=sum(f.open_interest for f in propias),
            nocional_neto=sum(f.nocional for f in propias),
            deriva=deriva, breakout=ruptura,
            duplicado=v in vistos))
        vistos.add(v)

    if not salida.buckets and not salida.motivo:
        salida.motivo = "Ningún plazo se pudo resolver con esta cadena."
    return salida
