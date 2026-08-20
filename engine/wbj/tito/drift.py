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
    "nocional_bruto_por_strike",
    "magneto",
    "sigma_proyectada",
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


def muro_calls(filas: list[_Fila], spot: float) -> tuple[float, int] | None:
    """Strike con más interés abierto entre las calls **por encima del spot**.

    El lado importa, y no es un detalle: un muro de calls es una
    **resistencia**. Sin la restricción, el mayor OI de calls suele quedar muy
    por DEBAJO del precio en cualquier acción que haya subido —son calls
    compradas hace meses que ahora están dentro del dinero— y el «muro de
    calls» salía a $120 con la acción a $180. Eso no es una resistencia: es
    historia.

    Medido: con el mayor OI de calls en un strike ITM, la versión sin filtro
    devolvía un muro de calls POR DEBAJO del muro de puts, y el panel pintaba
    un «rango defendido» invertido.

    `None` si no hay ninguna call al precio o por encima. No se baja a la de
    abajo: un muro que no está donde se dice que está es peor que ninguno.
    """
    calls = [f for f in filas
             if f.es_call and f.open_interest > 0 and f.strike >= spot]
    if not calls:
        return None
    top = max(calls, key=lambda f: f.open_interest)
    return top.strike, top.open_interest


def muro_puts(filas: list[_Fila], spot: float) -> tuple[float, int] | None:
    """Strike con más interés abierto entre las puts **por debajo del spot**.

    El espejo del anterior: un muro de puts es un **soporte**. El mayor OI de
    puts se queda por encima del precio cuando la acción ha caído, y llamar
    «soporte» a un strike que está arriba invierte la lectura entera.
    """
    puts = [f for f in filas
            if not f.es_call and f.open_interest > 0 and f.strike <= spot]
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


def nocional_bruto_por_strike(filas: list[_Fila]) -> dict[float, float]:
    """Nocional TOTAL por strike, sin restar un lado del otro.

    Existe porque el neto esconde justo lo que más importa. Un strike con
    10.000 calls y 10.000 puts es la mayor concentración de dinero de la
    cadena —el sitio clásico donde el precio se clava— y al restar sale **casi
    cero**: desaparecía del reparto como si no hubiera nadie ahí.
    """
    acc: dict[float, float] = {}
    for f in filas:
        acc[f.strike] = acc.get(f.strike, 0.0) + abs(f.nocional)
    return acc


def magneto(filas: list[_Fila], spot: float,
            suelo: float | None = None,
            techo: float | None = None) -> tuple[float, float] | None:
    """El strike con MÁS DINERO dentro del rango de los muros.

    Dos cosas que estaban mal y que se arreglan aquí:

    1. **Se sumaba en neto** (calls − puts), así que un strike con mucho de los
       dos se anulaba y desaparecía. Ahora se mide el nocional **bruto**: todo
       el dinero que hay en ese strike, venga del lado que venga.
    2. **No miraba el rango.** Con la acción a $180, el mayor nocional de toda
       la cadena estaba en puts a $230 muy dentro del dinero — contratos que se
       van a ejercer, no un imán al que el precio tienda. Ahora el imán se
       busca **entre el muro de puts y el de calls**, que es el rango que el
       panel presenta como banda de operación. Fuera de esa banda no hay
       atracción: hay historia.

    `suelo` y `techo` son los dos muros. Sin ellos se mira la cadena entera
    —el comportamiento de antes— y por eso son opcionales: el llamador decide.

    Devuelve `(strike, nocional_neto_de_ese_strike)`. El **signo del segundo**
    sigue siendo la polaridad —positivo si mandan las calls ahí, negativo si
    mandan las puts—, que es lo que separa atracción de rechazo. El tamaño que
    lo eligió es el bruto; el signo que lo explica es el neto. Son dos
    preguntas distintas sobre el mismo strike.
    """
    dentro = filas
    if suelo is not None and techo is not None:
        lo, hi = (suelo, techo) if suelo <= techo else (techo, suelo)
        dentro = [f for f in filas if lo <= f.strike <= hi]
    if not dentro:
        dentro = filas
    bruto = nocional_bruto_por_strike(dentro)
    if not bruto:
        return None
    s = max(bruto, key=lambda k: bruto[k])
    return s, nocional_por_strike(dentro).get(s, 0.0)


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


def clasifica_deriva(spot: float, mc: float | None, mp: float | None,
                     mag_strike: float | None, mag_nocional: float,
                     fuga: str | None = None) -> tuple[str, bool]:
    """Devuelve `(frase, es_ruptura)`.

    **Aquí estaba la trampa.** La versión anterior hacía
    `lo, hi = sorted((mp, mc))` — ordenaba los dos muros para que el rango
    saliera bien *aunque vinieran al revés*. Eso no arreglaba nada: tapaba que
    el muro de calls podía salir por debajo del de puts, que es imposible si
    uno es resistencia y el otro soporte. El arreglo está en la definición de
    los muros, no aquí.

    Con los muros ya bien puestos, el precio queda SIEMPRE entre los dos —por
    construcción— así que la ruptura no se puede detectar comparando con
    ellos. Se detecta con `fuga`, que es otra cosa y mejor: **el precio dejó
    atrás el grueso del posicionamiento**. Si el strike con más calls abiertas
    de toda la cadena está por debajo del precio, la acción ya se comió su
    propio libro de calls; el espejo, con las puts, hacia abajo.
    """
    if fuga == "alza":
        return ("RUPTURA al alza: el precio dejó atrás el grueso de las calls "
                "abiertas — el libro entero quedó dentro del dinero." +
                (f" La resistencia que queda es {mc:,.2f}." if mc is not None else ""),
                True)
    if fuga == "baja":
        return ("RUPTURA a la baja: el precio dejó atrás el grueso de las puts "
                "abiertas — el libro entero quedó dentro del dinero." +
                (f" El soporte que queda es {mp:,.2f}." if mp is not None else ""),
                True)
    if mag_strike is None:
        return ("Sin concentración de nocional utilizable en este vencimiento.",
                False)
    if mag_nocional > 0:
        return (f"DENTRO DEL RANGO · atracción: el imán de nocional está en "
                f"{mag_strike:,.2f} y domina el lado de las calls. El precio "
                f"tiende a gravitar hacia él.", False)
    return (f"DENTRO DEL RANGO · rechazo: el imán de nocional está en "
            f"{mag_strike:,.2f} y domina el lado de las puts. El precio tiende "
            f"a irse a los extremos del rango.", False)


def _mayor_oi(filas: list[_Fila], call: bool) -> _Fila | None:
    """El strike con más interés abierto de ese lado, SIN mirar el spot.

    Es el que dice si el precio ya dejó atrás el libro — el dato que produce la
    ruptura. Los muros que se pintan son los de `muro_calls`/`muro_puts`, que
    sí miran el lado; este es solo para esa comparación.
    """
    de_ese_lado = [f for f in filas if f.es_call == call and f.open_interest > 0]
    if not de_ese_lado:
        return None
    return max(de_ese_lado, key=lambda f: f.open_interest)


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


def drift_analysis(chain, spot: float, hoy: date,
                   iv: float | None = None) -> DriftAnalysis:
    """El análisis completo: cuatro plazos, sus muros, su imán y su cono.

    `chain` son las `ChainRow` que Vertex ya tiene en memoria — **no se baja
    nada**. `iv` es la estimada del motor; sin ella los conos salen a `None` y
    el resto del análisis sigue en pie.
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

        propias = [f for f in filas if f.vencimiento == v]
        # Los muros MIRAN EL LADO: el de calls es resistencia (al precio o por
        # encima), el de puts es soporte (al precio o por debajo). Sin esa
        # restricción salían invertidos en cuanto la acción se movía, porque
        # el mayor OI se queda donde se compró, no donde está el precio hoy.
        mc = muro_calls(propias, spot)
        mp = muro_puts(propias, spot)
        # …y el imán se busca DENTRO de esa banda, con el nocional bruto.
        mag = magneto(propias, spot,
                      suelo=(mp[0] if mp else None),
                      techo=(mc[0] if mc else None))
        if mc is None and mp is None:
            salida.sin_datos.append({
                "etiqueta": etiqueta, "dte_objetivo": objetivo,
                "motivo": "ese vencimiento no tiene calls y puts con interés abierto"})
            continue

        # ¿El precio dejó atrás el libro? Se mide con el mayor OI SIN filtrar
        # por lado: si el strike de calls más cargado de toda la cadena está
        # por debajo del precio, la acción ya se comió su propio libro.
        # Esto es EXACTAMENTE lo que hacía el `sorted((mp, mc))` de antes, y
        # para esto sí valía: comparar el precio contra los dos strikes más
        # cargados de la cadena, estén donde estén. El fallo era usar ESOS
        # MISMOS dos números como «soporte» y «resistencia» en pantalla — son
        # dos preguntas distintas y se habían fundido en una.
        #
        #   · dónde está el grueso del posicionamiento  → la ruptura
        #   · dónde está la resistencia y el soporte    → los muros por lado
        _lc, _lp = _mayor_oi(propias, True), _mayor_oi(propias, False)
        fuga = None
        if _lc is not None and _lp is not None:
            _lo, _hi = sorted((_lc.strike, _lp.strike))
            if spot > _hi:
                fuga = "alza"
            elif spot < _lo:
                fuga = "baja"

        deriva, ruptura = clasifica_deriva(
            spot, mc[0] if mc else None, mp[0] if mp else None,
            mag[0] if mag else None, mag[1] if mag else 0.0, fuga)
        salida.buckets.append(BucketDrift(
            etiqueta=etiqueta, sentimiento=sentimiento, dte_objetivo=objetivo,
            vencimiento=v.isoformat(), dte_real=dte,
            muro_calls=(mc[0] if mc else None), muro_calls_oi=(mc[1] if mc else 0),
            muro_puts=(mp[0] if mp else None), muro_puts_oi=(mp[1] if mp else 0),
            magneto=(mag[0] if mag else None),
            magneto_nocional=(mag[1] if mag else 0.0),
            sigma=sigma_proyectada(spot, iv, dte),
            total_oi=sum(f.open_interest for f in propias),
            nocional_neto=sum(f.nocional for f in propias),
            deriva=deriva, breakout=ruptura,
            duplicado=v in vistos))
        vistos.add(v)

    if not salida.buckets and not salida.motivo:
        salida.motivo = "Ningún plazo se pudo resolver con esta cadena."
    return salida
