"""Clasificación del flujo (Time & Sales) y los sub-agentes 1-3 del scorecard.

Port de `web/lib/flow.ts`.

Funciones puras: reciben trades crudos de MarketSnack + `now`, devuelven filas
procesadas y los tres primeros scores del scorecard.

- **Sub-agente 1 — Agresividad** (`aggression_score`): ¿el dinero grande entra
  al ask o golpea el bid?
- **Sub-agente 2 — Convicción** (`conviction_score`): spread, dominancia de un
  lado y fuerza de ejecución.
- **Sub-agente 3 — Inusualidad** (`unusuality_score`): perfil de griegos propio
  de instituciones (theta bajo, delta alto, vencimiento largo).

Los trades en el **MID se descartan** del subconjunto "interesante": no dicen
nada sobre dirección, y contarlos diluiría la señal de agresividad.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Literal, Sequence

from .conditions import condition_of, is_canceled_condition, is_multi_leg_condition
from .occ import MARKET_TZ, days_to_expiration, parse_occ

__all__ = [
    "BIG_PREMIUM",
    "CONVICTION_PREMIUM",
    "CONVICTION_DELTA",
    "AGGRESSIVE_FLOOR",
    "REPEAT_WINDOW_SEC",
    "REPEAT_MIN_COUNT",
    "LEAP_DTE",
    "UNUSUAL_TOTAL",
    "UNUSUAL_TRADE_THRESHOLD",
    "WIDE_SPREAD_ALERT_PREMIUM",
    "Aggression",
    "ExecutionLevel",
    "EXECUTION_LABEL",
    "FlowFlags",
    "TradeScores",
    "FlowRow",
    "ClassifiedFlow",
    "volume_score",
    "timing_score",
    "repetition_score",
    "aggression_of",
    "classify_flow",
    "CLUSTER_WINDOW_SEC",
    "CLUSTER_MIN_COUNT",
    "CLUSTER_MIN_PREMIUM",
    "Cluster",
    "detect_clusters",
    "spread_pct",
    "spread_score",
    "is_wide_spread",
    "dominance_score",
    "execution_level",
    "execution_score",
    "ConvictionScore",
    "conviction_score",
    "order_size_score",
    "delta_score",
    "theta_score",
    "gamma_score",
    "leg_score",
    "expiry_score",
    "UnusualScores",
    "unusual_trade_score",
    "UnusualityScore",
    "unusuality_score",
    "AggressionScore",
    "aggression_score",
]

# ---- Umbrales (ajustables) tomados del Scorecard --------------------------------

BIG_PREMIUM = 1_000_000
CONVICTION_PREMIUM = 100_000
CONVICTION_DELTA = 0.6
#: Piso para que un above-ask / below-bid "cuente" como interesante por sí solo.
AGGRESSIVE_FLOOR = 50_000
REPEAT_WINDOW_SEC = 5 * 60
REPEAT_MIN_COUNT = 3
LEAP_DTE = 90
#: Total (de 30) para marcar el trade como "inusual" y resaltarlo.
UNUSUAL_TOTAL = 24
#: Umbral (de 10) para etiquetar un trade como de grado institucional.
UNUSUAL_TRADE_THRESHOLD = 7
#: Premium desde el cual un spread ancho (>10%) merece alerta explícita.
WIDE_SPREAD_ALERT_PREMIUM = 1_000_000

_ASK_SIDES = frozenset({"ABOVE_ASK", "ASKSIDE", "AT_ASK"})
_BID_SIDES = frozenset({"BELOW_BID", "BIDSIDE", "AT_BID"})

Aggression = Literal["ask", "bid", "mid", "unknown"]
ExecutionLevel = Literal[
    "above_ask", "below_bid", "at_ask", "at_bid", "near", "mid", "unclear"
]

EXECUTION_LABEL: dict[str, str] = {
    "above_ask": "Sobre el ask",
    "below_bid": "Bajo el bid",
    "at_ask": "En el ask",
    "at_bid": "En el bid",
    "near": "Cerca del borde",
    "mid": "En el medio",
    "unclear": "Sin claridad",
}


@dataclass
class FlowFlags:
    """Banderas por trade. Las cross-row (`repeated`, `simultaneous`) se llenan
    en una segunda pasada, cuando ya se ven todos los trades juntos."""

    big: bool = False  # >= $1M
    conv_delta: bool = False  # >= $100K y |delta| > .60
    above_ask: bool = False
    below_bid: bool = False
    mid: bool = False
    leap: bool = False  # DTE largo (LEAP-ish)
    repeated: bool = False  # repetido en ventana de 5 min
    multileg: bool = False  # condición OPRA de multi leg
    simultaneous: bool = False  # mismo timestamp que otros contratos del subyacente
    exceeded_oi: bool = False  # volumen del contrato > open interest


@dataclass
class TradeScores:
    """Sub-scores del scorecard del sub-agente 1 (0-10 cada uno, total 0-30)."""

    volume: int = 0
    timing: int = 0
    repetition: int = 0
    total: int = 0


@dataclass
class FlowRow:
    """Un trade ya clasificado."""

    id: int
    symbol: str
    underlying: str
    type: Literal["call", "put", "unknown"]
    strike: float | None
    expiration: str | None
    dte: int | None
    price: float
    size: int
    side: str
    aggression: Aggression
    asset_price: float
    bid: float
    ask: float
    premium: float
    delta: float
    gamma: float
    theta: float
    vega: float
    #: Decaimiento diario como % del precio del contrato: |theta| / price * 100.
    theta_pct_daily: float | None
    iv: float
    open_interest: int
    volume: int
    score: float
    sentiment: str
    timestamp: str
    condition_code: str | None
    condition_name: str | None
    flags: FlowFlags = field(default_factory=FlowFlags)
    scores: TradeScores = field(default_factory=TradeScores)
    unusual: bool = False
    interesting: bool = False
    #: Estado del contrato respecto a HOY (los trades pueden ser de hace semanas).
    expiry_status: Literal["expirado", "expira_hoy", "vigente", "desconocido"] = "desconocido"


@dataclass
class ClassifiedFlow:
    rows: list[FlowRow]
    interesting: list[FlowRow]


# ---- Sub-agente 1: puntuación por trade ------------------------------------------


def volume_score(size: float, premium: float) -> int:
    """A. Puntuación por Volumen (nº de contratos)."""
    if size >= 150:
        return 10
    if size >= 100:
        return 8
    if size >= 50:
        return 6
    if size >= 20:
        return 4
    if size < 20 and premium > 500_000:
        return 1
    return 0


def _et_minutes(ts: str) -> int | None:
    """Minutos desde medianoche en horario del mercado (ET)."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return None
    et = dt.astimezone(MARKET_TZ)
    return (et.hour % 24) * 60 + et.minute


def timing_score(ts: str) -> int:
    """B. Puntuación por Momento (horario ET).

    El mediodía puntúa más alto que la apertura a propósito: la apertura está
    llena de ruido de reposicionamiento, mientras que un ticket grande a las
    12:00 es una decisión tomada con el mercado ya asentado.
    """
    minutes = _et_minutes(ts)
    if minutes is None:
        return 3
    if 660 <= minutes <= 780:  # 11:00-13:00 Mediodía
        return 10
    if 570 <= minutes <= 630:  # 9:30-10:30 Apertura
        return 7
    if 900 <= minutes <= 960:  # 15:00-16:00 Cierre
        return 6
    return 3  # Otros horarios


def repetition_score(count: int) -> int:
    """C. Puntuación por Repetición (nº de trades sobre el mismo contrato)."""
    if count >= 3:
        return 10
    if count == 2:
        return 7
    if count == 1:
        return 4
    return 1


def aggression_of(side: str) -> Aggression:
    """Traduce el código de lado de MarketSnack a ask / bid / mid."""
    if side in _ASK_SIDES:
        return "ask"
    if side in _BID_SIDES:
        return "bid"
    if side == "MIDMKT":
        return "mid"
    return "unknown"


def _epoch(ts: str) -> float | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.timestamp() if dt.tzinfo is not None else None


def _num(v: Any, default: float = 0.0) -> float:
    return float(v) if isinstance(v, (int, float)) else default


def _base_row(raw: dict[str, Any], now: datetime) -> FlowRow:
    """Convierte un trade crudo en FlowRow (sin las banderas cross-row)."""
    symbol = str(raw.get("symbol") or "")
    occ = parse_occ(symbol)
    dte = days_to_expiration(occ.expiration, now) if occ else None
    side = str(raw.get("side") or "")
    aggr = aggression_of(side)
    premium = _num(raw.get("premium"))
    delta = _num(raw.get("delta"))
    price = _num(raw.get("price"))
    theta_raw = raw.get("theta")
    volume = int(_num(raw.get("volume")))
    open_interest = int(_num(raw.get("open_interest")))
    cond = condition_of(raw.get("trade_condition_id"))

    flags = FlowFlags(
        big=premium >= BIG_PREMIUM,
        conv_delta=premium >= CONVICTION_PREMIUM and abs(delta) > CONVICTION_DELTA,
        above_ask=aggr == "ask",
        below_bid=aggr == "bid",
        mid=aggr == "mid",
        leap=dte is not None and dte >= LEAP_DTE,
        multileg=is_multi_leg_condition(raw.get("trade_condition_id")),
        exceeded_oi=volume > open_interest and open_interest > 0,
    )

    if dte is None:
        status = "desconocido"
    elif dte < 0:
        status = "expirado"
    elif dte == 0:
        status = "expira_hoy"
    else:
        status = "vigente"

    return FlowRow(
        id=int(_num(raw.get("id"))),
        symbol=symbol,
        underlying=occ.underlying if occ else symbol,
        type=occ.type if occ else "unknown",
        strike=occ.strike if occ else None,
        expiration=occ.expiration if occ else None,
        dte=dte,
        price=price,
        size=int(_num(raw.get("size"))),
        side=side,
        aggression=aggr,
        asset_price=_num(raw.get("asset_price")),
        bid=_num(raw.get("bid_price")),
        ask=_num(raw.get("ask_price")),
        premium=premium,
        delta=delta,
        gamma=_num(raw.get("gamma")),
        theta=_num(theta_raw),
        vega=_num(raw.get("vega")),
        theta_pct_daily=(
            abs(_num(theta_raw)) / price * 100 if theta_raw is not None and price > 0 else None
        ),
        iv=_num(raw.get("implied_volatility")),
        open_interest=open_interest,
        volume=volume,
        score=_num(raw.get("score")),
        sentiment=str(raw.get("sentiment") or ""),
        timestamp=str(raw.get("timestamp") or ""),
        condition_code=cond.code if cond else None,
        condition_name=cond.name if cond else None,
        flags=flags,
        expiry_status=status,  # type: ignore[arg-type]
    )


def _mark_repeated(rows: Sequence[FlowRow]) -> None:
    """Marca los trades que forman >=REPEAT_MIN_COUNT sobre el mismo contrato+lado
    dentro de una ventana deslizante de 5 minutos."""
    groups: dict[str, list[FlowRow]] = {}
    for r in rows:
        groups.setdefault(f"{r.symbol}|{r.aggression}", []).append(r)

    for group in groups.values():
        if len(group) < REPEAT_MIN_COUNT:
            continue
        timed = [(r, _epoch(r.timestamp)) for r in group]
        timed = [(r, t) for r, t in timed if t is not None]
        timed.sort(key=lambda x: x[1])
        start = 0
        for end in range(len(timed)):
            while timed[end][1] - timed[start][1] > REPEAT_WINDOW_SEC:
                start += 1
            if end - start + 1 >= REPEAT_MIN_COUNT:
                for i in range(start, end + 1):
                    timed[i][0].flags.repeated = True


def _mark_simultaneous(rows: Sequence[FlowRow]) -> None:
    """Mismo timestamp exacto + mismo subyacente, en >=2 contratos distintos.

    OJO: esto NO define multileg (para eso está la condición OPRA); es solo una
    señal de ejecuciones simultáneas.
    """
    groups: dict[str, list[FlowRow]] = {}
    for r in rows:
        groups.setdefault(f"{r.underlying}|{r.timestamp}", []).append(r)
    for group in groups.values():
        if len({r.symbol for r in group}) >= 2:
            for r in group:
                r.flags.simultaneous = True


def _score_rows(rows: Sequence[FlowRow]) -> None:
    """Aplica el sistema de puntuación del sub-agente (volumen, momento, repetición)."""
    per_contract: dict[str, int] = {}
    for r in rows:
        per_contract[r.symbol] = per_contract.get(r.symbol, 0) + 1
    for r in rows:
        volume = volume_score(r.size, r.premium)
        timing = timing_score(r.timestamp)
        repetition = repetition_score(per_contract.get(r.symbol, 1))
        total = volume + timing + repetition
        r.scores = TradeScores(volume=volume, timing=timing, repetition=repetition, total=total)
        r.unusual = total >= UNUSUAL_TOTAL


def _compute_interesting(r: FlowRow) -> bool:
    if r.flags.mid:
        return False  # los mid se descartan: no informan dirección
    return bool(
        r.flags.big
        or r.flags.conv_delta
        or r.flags.repeated
        or r.flags.multileg
        or r.flags.simultaneous
        or ((r.flags.above_ask or r.flags.below_bid) and r.premium >= AGGRESSIVE_FLOOR)
    )


def classify_flow(raw: Iterable[dict[str, Any]], now: datetime) -> ClassifiedFlow:
    """Pipeline completo: crudo → filas clasificadas + subconjunto "interesante"."""
    # Las transacciones canceladas se descartan: la orden se anuló, no existió.
    rows = [
        _base_row(t, now)
        for t in raw
        if not is_canceled_condition(t.get("trade_condition_id"))
    ]
    _mark_repeated(rows)
    _mark_simultaneous(rows)
    _score_rows(rows)
    for r in rows:
        r.interesting = _compute_interesting(r)
    interesting = sorted((r for r in rows if r.interesting), key=lambda r: r.premium, reverse=True)
    return ClassifiedFlow(rows=rows, interesting=interesting)


# ---- Detección de racimos (acumulación de trades) -------------------------------

#: Gap máximo entre trades para que sigan siendo el mismo racimo.
CLUSTER_WINDOW_SEC = 5 * 60
#: Mínimo de trades para contar como racimo.
CLUSTER_MIN_COUNT = 3
#: Premium acumulado mínimo del racimo.
CLUSTER_MIN_PREMIUM = 500_000


@dataclass
class Cluster:
    """Un burst de trades agresivos contiguos en la misma dirección."""

    start_sec: int
    end_sec: int
    count: int
    premium_ask: float
    premium_bid: float
    premium: float
    direction: Literal["ask", "bid"]
    unidirectionality: float  # 0..1 (qué tan de un solo lado)
    score: int  # 0-10 (cantidad + dinero + unidireccionalidad)
    trades: list[FlowRow]
    # Composición call/put y apuesta neta (comprar puts = bajista, no alcista):
    call_premium: float
    put_premium: float
    bullish_premium: float  # compra de calls + venta de puts
    bearish_premium: float  # compra de puts + venta de calls
    bet: Literal["alcista", "bajista"]
    bet_label: str  # ej. "Compraron PUTS"


def detect_clusters(
    rows: Sequence[FlowRow],
    window_sec: int = CLUSTER_WINDOW_SEC,
    min_count: int = CLUSTER_MIN_COUNT,
    min_premium: float = CLUSTER_MIN_PREMIUM,
) -> list[Cluster]:
    """Detecta racimos: bursts de >=min_count trades notables (ask/bid) contiguos
    (gap <= window_sec) con premium acumulado >= min_premium.

    La **apuesta neta** no es el lado de ejecución: comprar puts y vender calls
    empujan bajista; comprar calls y vender puts empujan alcista. Confundirlos
    haría leer una cobertura masiva como una apuesta al alza.
    """
    timed = [
        (r, int(t))
        for r, t in ((r, _epoch(r.timestamp)) for r in rows if r.aggression in ("ask", "bid"))
        if t is not None
    ]
    timed.sort(key=lambda x: x[1])

    clusters: list[Cluster] = []

    def flush(group: list[tuple[FlowRow, int]]) -> None:
        if len(group) < min_count:
            return
        ask = bid = call_p = put_p = 0.0
        buckets = {"call_ask": 0.0, "put_ask": 0.0, "call_bid": 0.0, "put_bid": 0.0}
        for r, _ in group:
            p = r.premium
            if r.aggression == "ask":
                ask += p
            else:
                bid += p
            if r.type == "call":
                call_p += p
                buckets["call_ask" if r.aggression == "ask" else "call_bid"] += p
            elif r.type == "put":
                put_p += p
                buckets["put_ask" if r.aggression == "ask" else "put_bid"] += p
            else:
                # tipo desconocido: cae al criterio simple ask=alcista / bid=bajista
                buckets["call_ask" if r.aggression == "ask" else "call_bid"] += p

        premium = ask + bid
        if premium < min_premium:
            return
        unid = (max(ask, bid) / premium) if premium > 0 else 0.0
        norm_count = min(1.0, len(group) / 10)
        norm_prem = min(1.0, premium / 2_000_000)
        score = round(10 * (0.4 * norm_count + 0.3 * norm_prem + 0.3 * unid))
        bullish = buckets["call_ask"] + buckets["put_bid"]
        bearish = buckets["put_ask"] + buckets["call_bid"]
        labels = [
            (buckets["call_ask"], "Compraron CALLS"),
            (buckets["put_ask"], "Compraron PUTS"),
            (buckets["call_bid"], "Vendieron CALLS"),
            (buckets["put_bid"], "Vendieron PUTS"),
        ]
        labels.sort(key=lambda x: x[0], reverse=True)
        clusters.append(
            Cluster(
                start_sec=group[0][1],
                end_sec=group[-1][1],
                count=len(group),
                premium_ask=ask,
                premium_bid=bid,
                premium=premium,
                direction="ask" if ask >= bid else "bid",
                unidirectionality=unid,
                score=score,
                trades=[r for r, _ in group],
                call_premium=call_p,
                put_premium=put_p,
                bullish_premium=bullish,
                bearish_premium=bearish,
                bet="alcista" if bullish >= bearish else "bajista",
                bet_label=labels[0][1],
            )
        )

    group: list[tuple[FlowRow, int]] = []
    for x in timed:
        if not group:
            group = [x]
            continue
        if x[1] - group[-1][1] <= window_sec:
            group.append(x)
        else:
            flush(group)
            group = [x]
    flush(group)
    return clusters


# ============================================================================
# Sub-agente 2 — CONVICCIÓN
# Mide la calidad/decisión del flujo: spread, dominancia ask-vs-bid y fuerza de
# ejecución. Ver SCOREDCARD/Conviccion.md
# ============================================================================


def spread_pct(bid: float, ask: float) -> float | None:
    """Spread relativo de un trade: ``(ask − bid) / mid``, en %. ``None`` sin quote."""
    if not (bid > 0) or not (ask > 0) or ask < bid:
        return None
    mid = (ask + bid) / 2
    if mid <= 0:
        return None
    return (ask - bid) / mid * 100


def spread_score(pct: float | None) -> int:
    """Puntuación por spread (0-10). >10% no puntúa: se separa aparte."""
    if pct is None:
        return 0
    if pct < 2:
        return 10
    if pct <= 5:
        return 7
    if pct <= 10:
        return 4
    return 0  # spread ancho → se aparta del análisis normal


def is_wide_spread(pct: float | None) -> bool:
    """Un spread > 10% se separa para revisión aparte."""
    return pct is not None and pct > 10


def dominance_score(pct_dominant: float) -> int:
    """Puntuación por dominancia (% del premium en el lado dominante)."""
    if pct_dominant >= 80:
        return 10
    if pct_dominant >= 70:
        return 8
    if pct_dominant >= 60:
        return 6
    if pct_dominant >= 55:
        return 4
    if pct_dominant >= 50:
        return 2
    return 0


def execution_level(price: float, bid: float, ask: float, side: str) -> ExecutionLevel:
    """Dónde cayó el precio del trade respecto al spread.

    "Cerca" = dentro del 20% del ancho del spread desde el ask o el bid.
    """
    if not (bid > 0) or not (ask > 0) or ask < bid:
        return "unclear"
    if price > ask:
        return "above_ask"
    if price < bid:
        return "below_bid"
    width = ask - bid
    if width <= 0:
        return "at_ask" if price >= ask else "at_bid"
    if price >= ask:
        return "at_ask"
    if price <= bid:
        return "at_bid"
    from_ask = (ask - price) / width
    from_bid = (price - bid) / width
    if from_ask <= 0.2 or from_bid <= 0.2:
        return "near"
    # Zona media: si está muy centrado es mid; si no, nos apoyamos en el lado reportado.
    if abs(from_ask - from_bid) < 0.2:
        return "mid"
    return "mid" if side == "MIDMKT" else "near"


def execution_score(level: ExecutionLevel) -> int:
    """Puntuación por fuerza de ejecución (0-10). La dirección se etiqueta aparte."""
    if level in ("above_ask", "below_bid"):
        return 10
    if level in ("at_ask", "at_bid"):
        return 8
    if level == "near":
        return 6
    if level == "mid":
        return 3
    return 0


@dataclass
class ConvictionScore:
    score: int  # 0-10 final de la categoría
    spread: dict[str, Any]
    dominance: dict[str, Any]
    execution: dict[str, Any]
    n: int


def conviction_score(rows: Sequence[FlowRow]) -> ConvictionScore:
    """Score de Convicción (0-10).

    Promedio de los 3 sub-scores (spread, dominancia, fuerza de ejecución),
    ponderando por premium donde aplica: un ticket de $2M debe mover la aguja
    más que uno de $100K.
    """
    counts: dict[str, int] = {
        "above_ask": 0, "below_bid": 0, "at_ask": 0, "at_bid": 0,
        "near": 0, "mid": 0, "unclear": 0,
    }

    spread_weighted = spread_weight = 0.0
    wide_count = 0
    wide_alert: list[FlowRow] = []
    ask_prem = bid_prem = 0.0
    exec_weighted = exec_weight = 0.0

    for r in rows:
        pct = spread_pct(r.bid, r.ask)
        if pct is not None:
            if is_wide_spread(pct):
                wide_count += 1
                if r.premium >= WIDE_SPREAD_ALERT_PREMIUM:
                    wide_alert.append(r)
            else:
                spread_weighted += pct * r.premium
                spread_weight += r.premium

        if r.aggression == "ask":
            ask_prem += r.premium
        elif r.aggression == "bid":
            bid_prem += r.premium

        level = execution_level(r.price, r.bid, r.ask, r.side)
        counts[level] += 1
        exec_weighted += execution_score(level) * r.premium
        exec_weight += r.premium

    avg_spread_pct = (spread_weighted / spread_weight) if spread_weight > 0 else None
    spread_points = spread_score(avg_spread_pct)

    total_dir = ask_prem + bid_prem
    ask_pct = (ask_prem / total_dir * 100) if total_dir > 0 else 0.0
    bid_pct = (bid_prem / total_dir * 100) if total_dir > 0 else 0.0
    dominant_pct = max(ask_pct, bid_pct)
    dom_points = dominance_score(dominant_pct) if total_dir > 0 else 0

    exec_avg = (exec_weighted / exec_weight) if exec_weight > 0 else 0.0

    return ConvictionScore(
        score=round((spread_points + dom_points + exec_avg) / 3),
        spread={
            "avg_pct": avg_spread_pct,
            "points": spread_points,
            "wide_count": wide_count,
            "wide_alert": sorted(wide_alert, key=lambda r: r.premium, reverse=True),
        },
        dominance={
            "ask_pct": ask_pct,
            "bid_pct": bid_pct,
            "dominant_pct": dominant_pct,
            "side": "ask" if ask_pct >= bid_pct else "bid",
            "points": dom_points,
        },
        execution={"points": round(exec_avg), "avg_raw": exec_avg, "counts": counts},
        n=len(rows),
    )


# ============================================================================
# Sub-agente 3 — INUSUALIDAD
# Identifica transacciones con parámetros de griegos propios de instituciones.
# Ver SCOREDCARD/Inusualidad.md
# ============================================================================


def order_size_score(premium: float) -> int:
    """Tamaño de la orden (premium en $)."""
    if premium > 5_000_000:
        return 10
    if premium >= 1_000_000:
        return 8
    if premium >= 500_000:
        return 7
    if premium >= 200_000:
        return 5
    if premium >= 100_000:
        return 3
    return 0


def delta_score(delta: float) -> int:
    """Delta en valor absoluto: un put de −0.85 es tan direccional como un call de +0.85."""
    d = abs(delta)
    if d >= 0.8:
        return 10
    if d >= 0.7:
        return 8
    if d >= 0.6:
        return 7
    if d >= 0.5:
        return 5
    return 0


def theta_score(pct_daily: float | None) -> int:
    """Theta como % de decaimiento diario sobre el precio del contrato.

    Decaimiento bajo = posición para sostener, no lotería.
    """
    if pct_daily is None:
        return 0
    if pct_daily < 1:
        return 10
    if pct_daily <= 3:
        return 8
    if pct_daily <= 5:
        return 5
    return 0


def gamma_score(gamma: float) -> int:
    """Gamma: la zona 0.01-0.08 es la "institucional"; muy alta o muy baja puntúa menos."""
    g = abs(gamma)
    if g < 0.01:
        return 2
    if g <= 0.08:
        return 10
    if g <= 0.15:
        return 8
    return 4


def leg_score(multileg: bool) -> int:
    """Una sola pata es más limpia de leer que un multileg."""
    return 5 if multileg else 10


def expiry_score(dte: int | None) -> int:
    """Vencimiento (días para expirar)."""
    if dte is None:
        return 0
    if dte >= 120:
        return 10  # incluye los LEAPs de ~320 días
    if dte >= 90:
        return 8
    if dte >= 60:
        return 7
    if dte >= 30:
        return 5
    return 2


@dataclass(frozen=True)
class UnusualScores:
    size: int
    delta: int
    theta: int
    gamma: int
    leg: int
    expiry: int
    total: float  # 0-10 (promedio de los 6)


def unusual_trade_score(r: FlowRow) -> UnusualScores:
    """Puntúa un trade con la tabla de Inusualidad (6 parámetros → promedio 0-10)."""
    size = order_size_score(r.premium)
    delta = delta_score(r.delta)
    theta = theta_score(r.theta_pct_daily)
    gamma = gamma_score(r.gamma)
    leg = leg_score(r.flags.multileg)
    expiry = expiry_score(r.dte)
    total = (size + delta + theta + gamma + leg + expiry) / 6
    return UnusualScores(
        size=size, delta=delta, theta=theta, gamma=gamma, leg=leg, expiry=expiry,
        total=round(total * 10) / 10,
    )


@dataclass
class UnusualityScore:
    score: int  # 0-10 de la categoría
    avg_by_param: dict[str, float]
    unusual_count: int  # trades con total >= umbral
    n: int
    top: list[tuple[FlowRow, UnusualScores]]  # más inusuales, ordenados


def unusuality_score(rows: Sequence[FlowRow]) -> UnusualityScore:
    """Score de Inusualidad (0-10).

    Promedio **ponderado por premium** del puntaje inusual de cada trade: los
    tickets grandes pesan más, porque son los que mueven el mercado.
    """
    if not rows:
        return UnusualityScore(
            score=0,
            avg_by_param={"size": 0, "delta": 0, "theta": 0, "gamma": 0, "leg": 0, "expiry": 0},
            unusual_count=0,
            n=0,
            top=[],
        )

    scored = [(row, unusual_trade_score(row)) for row in rows]

    weighted = weight = 0.0
    sums = {"size": 0, "delta": 0, "theta": 0, "gamma": 0, "leg": 0, "expiry": 0}
    for row, s in scored:
        # Piso de 1 para que un trade con premium 0 no desaparezca del promedio.
        w = max(row.premium, 1.0)
        weighted += s.total * w
        weight += w
        sums["size"] += s.size
        sums["delta"] += s.delta
        sums["theta"] += s.theta
        sums["gamma"] += s.gamma
        sums["leg"] += s.leg
        sums["expiry"] += s.expiry

    n = len(scored)
    return UnusualityScore(
        score=round(weighted / weight) if weight > 0 else 0,
        avg_by_param={k: round(v / n * 10) / 10 for k, v in sums.items()},
        unusual_count=sum(1 for _, s in scored if s.total >= UNUSUAL_TRADE_THRESHOLD),
        n=n,
        top=sorted(scored, key=lambda x: (x[1].total, x[0].premium), reverse=True)[:150],
    )


# ============================================================================
# Sub-agente 1 — AGRESIVIDAD (la casilla del scorecard)
# ============================================================================


@dataclass(frozen=True)
class AggressionScore:
    score: int  # 0-10 (casilla del scorecard)
    ratio: float  # premium al ask / (ask + bid)
    premium_ask: float
    premium_bid: float
    premium_mid: float
    n: int  # nº de transacciones notables consideradas


def aggression_score(rows: Sequence[FlowRow]) -> AggressionScore:
    """Score de Agresividad (0-10) — "¿compran al ask con fuerza?".

    Pondera por premium: cuánto del dinero notable entró agresivo al ask vs
    golpeando el bid. Los mid **se reportan pero no cuentan** para el ratio.
    Sin flujo ask/bid → score 0 (no un 5 "neutral": la ausencia de señal no es
    media señal).
    """
    ask = bid = mid = 0.0
    for r in rows:
        if r.aggression == "ask":
            ask += r.premium
        elif r.aggression == "bid":
            bid += r.premium
        elif r.aggression == "mid":
            mid += r.premium
    denom = ask + bid
    ratio = (ask / denom) if denom > 0 else 0.0
    return AggressionScore(
        score=round(ratio * 10) if denom > 0 else 0,
        ratio=ratio,
        premium_ask=ask,
        premium_bid=bid,
        premium_mid=mid,
        n=len(rows),
    )
