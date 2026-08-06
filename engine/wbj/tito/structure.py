"""Sub-agente 4 — ACUMULACIÓN Y RAPIDEZ (categoría "Estructura" del scorecard).

Port de `web/lib/structure.ts`. Ver `SCOREDCARD/Acumulacion-Rapidez.md`.

Identifica el **posicionamiento** y las fechas de expiración relevantes
analizando la actividad por strike y por vencimiento en la **cadena completa**.

Es el único sub-agente que NO lee el tape de trades: mira dónde está *parado* el
dinero (open interest), no dónde se está moviendo. Por eso responde una pregunta
que ninguno de los otros cinco puede responder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from .jsmath import js_add, js_gt, js_number, js_round

__all__ = [
    "LOW_LIQUIDITY_NOTIONAL",
    "STRIKE_DOMINANCE_PCT",
    "TOP_STRIKES_CONSIDERED",
    "ChainRow",
    "StrikeStat",
    "ExpirationStat",
    "StructureScore",
    "notional_score",
    "dominant_strikes_score",
    "volume_over_oi_score",
    "structure_score",
]

#: Debajo de este nocional promedio la cadena se marca como "Baja Liquidez".
LOW_LIQUIDITY_NOTIONAL = 25_000_000

#: Un lado (calls o puts) "domina" un strike si tiene al menos este % de su nocional.
STRIKE_DOMINANCE_PCT = 60

#: Cuántos strikes (los de mayor nocional) se evalúan para la dominancia.
TOP_STRIKES_CONSIDERED = 5

Side = Literal["calls", "puts"]


#: De dónde salió el precio del contrato. `PriceSource` de Víctor: el plan de
#: Massive no devuelve quotes, así que no hay `bid`.
PriceSource = Literal["last_trade", "day_close", "day_vwap", "none"]


@dataclass(frozen=True)
class ChainRow:
    """La `Row` de Víctor: una fila de la cadena ya normalizada.

    ``notional_value`` se recibe calculado para que la fuente de la cadena sea
    intercambiable. Ojo con el multiplicador: son ``open_interest *
    shares_per_contract * strike``, y ``shares_per_contract`` **no siempre es
    100** — los contratos ajustados por split o dividendo especial traen otro.
    `compute.to_row` lo lee del contrato; darlo por hecho inflaba el nocional
    hasta 10× y movía el score de Estructura.

    Los cuatro últimos campos son los que Víctor usa en su tabla de la cadena;
    el sub-agente 4 solo mira los seis primeros. Llevan defaults para que las
    fuentes que no los tengan sigan construyendo filas válidas.
    """

    contract_type: Literal["call", "put"]
    expiration: str
    strike: float
    open_interest: int
    volume: int
    notional_value: float
    option_ticker: str = ""
    price: float | None = None
    price_source: PriceSource = "none"
    open_premium: float | None = None


@dataclass
class StrikeStat:
    strike: float
    notional: float = 0.0
    pct_of_total: float = 0.0
    call_notional: float = 0.0
    put_notional: float = 0.0
    side: Side = "calls"
    #: % del nocional del strike que tiene el lado mayoritario (0-100).
    dominance_pct: float = 0.0
    #: ¿Ese lado supera el umbral de dominancia?
    dominant: bool = False
    open_interest: int = 0
    volume: int = 0
    expirations: int = 0


@dataclass
class ExpirationStat:
    expiration: str
    notional: float = 0.0
    pct_of_total: float = 0.0
    call_notional: float = 0.0
    put_notional: float = 0.0
    contracts: int = 0


@dataclass
class StructureScore:
    score: int  # 0-10 de la categoría
    notional: dict
    strikes: dict
    vol_oi: dict
    expirations: list[ExpirationStat] = field(default_factory=list)


def notional_score(avg_notional: float) -> int:
    """1. Puntuación por Valor Nocional promedio (Strike × Open Interest × 100)."""
    if avg_notional >= 500_000_000:
        return 10
    if avg_notional >= 100_000_000:
        return 8
    if avg_notional >= 50_000_000:
        return 6
    if avg_notional >= 25_000_000:
        return 4
    return 2


def dominant_strikes_score(count: int) -> int:
    """2. Puntuación por en cuántos strikes domina claramente un lado.

    NO mide concentración de nocional: mide **dominancia direccional** por
    strike. Un strike con mucho dinero repartido 50/50 entre calls y puts no
    dice nada de dirección; uno con 90% en calls sí.
    """
    if count >= 5:
        return 10
    if count >= 3:
        return 8
    if count >= 1:
        return 5
    return 0  # sin visibilidad clara


def volume_over_oi_score(pct: float) -> int:
    """3. Puntuación por % de contratos donde el volumen supera el Open Interest.

    Volumen > OI implica posiciones **nuevas**: no se pueden cerrar más
    contratos de los que existen.
    """
    if pct >= 100:
        return 10
    if pct >= 50:
        return 8
    if pct >= 30:
        return 5
    return 2


def _empty() -> StructureScore:
    return StructureScore(
        score=0,
        notional={
            "avg_per_strike": 0.0, "total": 0.0, "strike_count": 0,
            "points": 0, "low_liquidity": True,
        },
        strikes={
            "dominant_count": 0, "considered_count": 0, "points": 0, "top": [],
            "call_pct": 0.0, "put_pct": 0.0, "dominant_side": "calls",
        },
        vol_oi={"pct": 0.0, "exceeded": 0, "considered": 0, "points": 0},
        expirations=[],
    )


def _clave(v):
    """Clave de agrupación con la semántica de un `Map` de JS.

    Su `byStrike` es un `Map`, que acepta **cualquier** valor como clave —
    incluidos `undefined` y un objeto. Un `dict` de Python solo acepta lo
    hashable, así que una lista o un dict como strike lanzaba `TypeError` y se
    llevaba `structure_score` entero.

    Se envuelve lo no hashable en su representación de texto: no colisiona con
    ningún strike real y conserva "cada valor raro es su propio grupo", que es
    lo que hace su `Map`.
    """
    try:
        hash(v)
    except TypeError:
        return f"__nohash__{v!r}"
    return v


def structure_score(rows: Sequence[ChainRow]) -> StructureScore:
    """Analiza la cadena: nocional por strike, dominio calls/puts, y cuántas
    veces el volumen supera al Open Interest.

    Una cadena vacía sale con ``low_liquidity=True``, no con un score neutro:
    no saber es motivo de cautela, nunca de confianza.
    """
    if not rows:
        return _empty()

    by_strike: dict[float, StrikeStat] = {}
    by_exp: dict[str, ExpirationStat] = {}
    total = call_total = put_total = 0.0

    # Todos los acumuladores usan `js_add`, que es el `+` de JS. Con la cadena
    # bien formada esto es la suma de siempre; con un campo que llegue en texto
    # o ausente reproduce su resultado en vez de lanzar un `TypeError` — y aquí
    # un `TypeError` no descarta una fila, tumba `structure_score` entero y con
    # él el sub-agente 4. Lo encontró el corpus malformado de `diff_motor.sh`
    # (41 casos, el bloque que más reventaba).
    #
    # El `r.strike` de la clave del dict NO se coacciona: su `byStrike` es un
    # `Map` que acepta cualquier valor como clave, incluido `undefined`.
    for r in rows:
        n = r.notional_value
        total = js_add(total, n)
        if r.contract_type == "call":
            call_total = js_add(call_total, n)
        else:
            put_total = js_add(put_total, n)

        clave = _clave(r.strike)
        s = by_strike.setdefault(clave, StrikeStat(strike=r.strike))
        s.notional = js_add(s.notional, n)
        if r.contract_type == "call":
            s.call_notional = js_add(s.call_notional, n)
        else:
            s.put_notional = js_add(s.put_notional, n)
        s.open_interest = js_add(s.open_interest, r.open_interest)
        s.volume = js_add(s.volume, r.volume)
        s.expirations += 1

        if r.expiration:
            e = by_exp.setdefault(_clave(r.expiration),
                                  ExpirationStat(expiration=r.expiration))
            e.notional = js_add(e.notional, n)
            if r.contract_type == "call":
                e.call_notional = js_add(e.call_notional, n)
            else:
                e.put_notional = js_add(e.put_notional, n)
            e.contracts += 1

    strike_stats = list(by_strike.values())
    strike_count = len(strike_stats)
    # `total` puede ser un string si algún nocional llegó en texto (el `+` de JS
    # concatena). La división de después lo vuelve a número, que es lo que hace
    # su motor; aquí se hace explícito.
    avg_per_strike = (js_number(total) / strike_count) if strike_count > 0 else 0.0

    for s in strike_stats:
        nt, cn, pn = js_number(s.notional), js_number(s.call_notional), js_number(s.put_notional)
        s.pct_of_total = (nt / js_number(total) * 100) if js_gt(total) else 0.0
        s.side = "calls" if cn >= pn else "puts"
        s.dominance_pct = (max(cn, pn) / nt * 100) if nt > 0 else 0.0
        s.dominant = nt > 0 and s.dominance_pct >= STRIKE_DOMINANCE_PCT

    by_notional = sorted(strike_stats, key=lambda s: js_number(s.notional), reverse=True)
    considered = by_notional[:TOP_STRIKES_CONSIDERED]
    dominant_count = sum(1 for s in considered if s.dominant)
    top = by_notional[:10]

    # Solo cuentan los contratos con actividad: incluir los muertos (OI 0 y
    # volumen 0) diluiría el porcentaje hacia 0 en cualquier cadena larga.
    # `js_gt` / `js_number`: en JS estas comparaciones coaccionan y un valor
    # ilegible las hace falsas; en Python `None > 0` lanza.
    activos = [r for r in rows if js_gt(r.open_interest) or js_gt(r.volume)]
    exceeded = sum(1 for r in activos
                   if js_number(r.volume) > js_number(r.open_interest))
    vol_pct = (exceeded / len(activos) * 100) if activos else 0.0

    n_points = notional_score(avg_per_strike)
    s_points = dominant_strikes_score(dominant_count)
    v_points = volume_over_oi_score(vol_pct)

    expirations = sorted(by_exp.values(), key=lambda e: js_number(e.notional),
                         reverse=True)[:10]
    for e in expirations:
        e.pct_of_total = ((js_number(e.notional) / js_number(total) * 100)
                          if js_gt(total) else 0.0)

    return StructureScore(
        score=js_round((n_points + s_points + v_points) / 3),
        notional={
            "avg_per_strike": avg_per_strike,
            "total": total,
            "strike_count": strike_count,
            "points": n_points,
            "low_liquidity": avg_per_strike < LOW_LIQUIDITY_NOTIONAL,
        },
        strikes={
            "dominant_count": dominant_count,
            "considered_count": len(considered),
            "points": s_points,
            "top": top,
            "call_pct": ((js_number(call_total) / js_number(total) * 100)
                         if js_gt(total) else 0.0),
            "put_pct": ((js_number(put_total) / js_number(total) * 100)
                        if js_gt(total) else 0.0),
            "dominant_side": ("calls" if js_number(call_total) >= js_number(put_total)
                              else "puts"),
        },
        vol_oi={
            "pct": vol_pct,
            "exceeded": exceeded,
            "considered": len(activos),
            "points": v_points,
        },
        expirations=expirations,
    )
