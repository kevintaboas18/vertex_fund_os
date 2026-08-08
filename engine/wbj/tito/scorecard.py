"""Orquestador del scorecard: de datos crudos a veredicto, en una llamada.

Junta los 6 sub-agentes, el mapa de GEX, los niveles y Prediction Pro. Es la
única función que un consumidor (la API de Vertex, un CLI, un test) necesita
llamar.

Sigue el mismo orden de congelación que el `CLAUDE.md` de Vertex exige para el
agente principal: **los 6 especialistas producen su score de forma independiente
y solo después se agregan**. Ninguno ve el score de otro. Las dos excepciones
son deliberadas y van en una sola dirección:

- El GEX recibe los scores de Convicción y Estructura, pero solo para modular su
  **confianza** — nunca para mover un nivel.
- Prediction Pro recibe los 6 scores ya congelados, que es su trabajo.

Todo aquí es puro: recibe datos ya descargados y devuelve estructuras. El I/O
vive en `marketsnack` y en la fuente de cadena que le pase el llamador.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from .expected_move import LevelInput
from .jsmath import js_add, js_clave, js_number, js_round
from .flow import (
    ClassifiedFlow,
    FlowRow,
    aggression_score,
    classify_flow,
    conviction_score,
    unusuality_score,
)
from .gex import TradeLite, gex_analysis
from .ivcontext import iv_context_score
from .levels import ChainLevel, FlowLevel, GexLevel, LvlBar, find_levels
from .prediction import SubScores, predict_pro
from .structure import ChainRow, structure_score
from .validation import FlowLite, ValBar, validation_score

__all__ = ["BANDS", "ScorecardResult", "verdict_of", "run_scorecard"]

#: Bandas del veredicto (rúbrica de `SCOREDCARD/Options-Flow-Scorecard.md`).
BANDS = [
    (75, "Oportunidad Fuerte", "Alta convicción / mejor potencial"),
    (50, "Oportunidad Moderada", "Convicción media / revisar filtros"),
    (0, "Oportunidad Débil", "Alto riesgo / baja convicción"),
]


def _unir(*grupos: Sequence[FlowRow]) -> list[FlowRow]:
    """`[...a, ...b]` con dedupe por `id` — el patrón que su `page.tsx` repite
    tres veces (GEX, niveles y heatmap).

    El orden es el de llegada, como el `Set` de JS, y el primer `id` gana.
    `js_clave` para la identidad: un `id` que llegue como lista es inhashable
    en Python y su `Set` lo acepta sin pestañear.
    """
    vistos: set = set()
    out: list[FlowRow] = []
    for g in grupos:
        for r in g:
            k = js_clave(r.id)
            if k in vistos:
                continue
            vistos.add(k)
            out.append(r)
    return out


def verdict_of(score: float) -> tuple[str, str]:
    """Traduce el 0-100 a la banda del documento."""
    for floor, label, meaning in BANDS:
        if score >= floor:
            return label, meaning
    return BANDS[-1][1], BANDS[-1][2]


@dataclass
class ScorecardResult:
    ticker: str
    spot: float
    #: Puntaje 0-100 ponderado sobre el peso ACTIVO (ver `weighted_score`).
    score: int
    verdict: str
    verdict_meaning: str
    #: Cuántas de las 6 categorías tienen dato.
    active: int
    scores: dict[str, int | None]
    flow: ClassifiedFlow
    aggression: Any
    conviction: Any
    unusuality: Any
    structure: Any
    iv_context: Any
    validation: Any
    gex: Any
    levels: Any
    predictions: dict[int, Any] = field(default_factory=dict)
    #: Advertencias que el reporte DEBE mostrar (liquidez, categorías faltantes…).
    warnings: list[str] = field(default_factory=list)
    #: `convictionRows` — la ventana ancha (30 d / ≥$1M) sobre la que puntúan
    #: Convicción, Inusualidad y Contexto IV, y la que su `/api/flow` PERSISTE
    #: (`saveTrades(ticker, convictionRows)`). Cae a `flow.interesting` cuando
    #: no se pasa la segunda descarga, igual que su ruta.
    conviction_flow: list = field(default_factory=list)
    #: `convictionMeta.window` de su `/api/flow`: `"30d"` si la ventana ancha
    #: llegó, o el `period` corto si su `catch` tuvo que caer a los 5 días.
    conviction_window: str = "5d"


#: Ventana ancha de su `/api/flow`. **No es un detalle de I/O: decide el score.**
#:
#: Su ruta hace DOS descargas y no una:
#:
#:   1. `period` (5d) · premium ≥ $100K · 6 páginas  → **Agresividad**
#:   2. `period: "1m"` · premium ≥ $1M · 15 páginas · `targetDays: 30`
#:      → **Convicción, Inusualidad y Contexto IV**
#:
#: El comentario de su archivo lo dice: *"Convicción revisa una ventana de 30
#: días (nota del documento)"*. Tres de las seis categorías puntúan sobre un
#: universo distinto —diez veces más caro y seis veces más largo— que el de
#: Agresividad. Medir las tres sobre los 5 días baratos las hace otra cosa.
CONVICTION_DAYS = 30
CONVICTION_MIN_PREMIUM = 1_000_000
CONVICTION_MAX_PAGES = 15


def run_scorecard(
    ticker: str,
    raw_trades: Sequence[dict[str, Any]],
    chain: Sequence[ChainRow],
    bars: Sequence[LvlBar],
    now: datetime,
    spot: float | None = None,
    horizons: Sequence[int] = (10, 20, 30),
    iv_history: Sequence[dict] | None = None,
    past_flows: Sequence[dict] | None = None,
    calibration: dict | None = None,
    conviction_trades: Sequence[dict[str, Any]] | None = None,
) -> ScorecardResult:
    """Corre el pipeline completo y devuelve el scorecard con sus 3 escenarios.

    `bars` va del más viejo al más reciente. `spot` se toma del último cierre si
    no se pasa.

    Los tres argumentos de memoria son los que sacan al motor de su versión
    mínima; sin ellos tres piezas nunca arrancan:

    - `iv_history` — ``[{date, avgIv}, …]`` acumulado. A los 60 días el IV Rank
      real desplaza al proxy de volatilidad realizada (sub-agente 5).
    - `past_flows` — flows de sesiones anteriores. **Sin esto el sub-agente 6
      sale `None` aunque haya tape**, porque un flow de hoy todavía no tiene
      recorrido que juzgar.
    - `calibration` — ``{"bias_pct": float|None, "samples": int}``. Con ≥5
      predicciones vencidas corrige el target base por el sesgo histórico.

    `conviction_trades` es la **ventana ancha** de su `/api/flow` (30 días,
    premium ≥ $1M): la que alimenta Convicción, Inusualidad y Contexto IV. Si
    no se pasa se cae a `raw_trades`, que es lo que hace su propia ruta cuando
    la segunda descarga falla —*"si falla la ventana ancha, Convicción se
    calcula con los 5 días"*—, así que el respaldo también es literal.
    """
    closes = [b.close for b in bars]
    if spot is None:
        spot = closes[-1] if closes else 0.0

    # ── Sub-agente 1: sobre el tape de los últimos días ─────────────────────
    flow = classify_flow(raw_trades, now)
    notable = flow.interesting
    agg = aggression_score(notable)

    # ── Sub-agentes 2, 3 y 5: sobre la VENTANA ANCHA ────────────────────────
    # `convictionRows` de su `/api/flow`. Sin esto los tres puntuaban sobre los
    # mismos 5 días baratos que Agresividad, que es medir otra cosa.
    conviction_rows = (
        classify_flow(conviction_trades, now).interesting
        if conviction_trades is not None else notable
    )
    conv = conviction_score(conviction_rows)
    unu = unusuality_score(conviction_rows)

    # ── Sub-agente 4: sobre la cadena completa ──────────────────────────────
    stru = structure_score(chain)
    low_liquidity = bool(stru.notional["low_liquidity"])

    # ── Sub-agente 5: contexto de volatilidad, sobre la ventana ancha ───────
    ivc = iv_context_score(conviction_rows, closes, iv_history)

    # ── Sub-agente 6: backtest del tape ─────────────────────────────────────
    # Se juzgan los flows ACUMULADOS, no los de hoy: un flow de esta mañana
    # todavía no tiene recorrido que medir. Sin `past_flows` esta categoría
    # sale None por diseño — no hay evidencia, así que no hay score.
    val_bars = [ValBar(time=b.time, high=b.high, low=b.low, close=b.close) for b in bars]
    seen_ids: set[int] = set()
    val_flows: list[FlowLite] = []
    for src in (past_flows or [], notable):
        for r in src:
            get = (lambda k: r.get(k)) if isinstance(r, dict) else (lambda k: getattr(r, k))
            fid = get("id")
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            val_flows.append(FlowLite(
                id=fid, timestamp=get("timestamp"), type=get("type"),
                strike=get("strike"), expiration=get("expiration"),
                asset_price=get("asset_price"), premium=get("premium"),
                aggression=get("aggression"),
            ))
    val = validation_score(val_flows, val_bars, now)

    # ── GEX (recibe convicción/estructura SOLO para modular confianza) ──────
    #
    # Los trades reales que anclan la gamma salen de `convRows ∪ unusualRows`
    # con dedupe por `id`, exactamente como su `page.tsx`. No es lo mismo que
    # `notable`: son los 30 días de dinero grande, que es donde hay gamma real
    # que anclar. El port pasaba los 5 días baratos.
    gex_rows = _unir(conviction_rows, [t[0] for t in unu.top])
    gex = gex_analysis(
        chain,
        closes,
        spot,
        now,
        trades=[
            TradeLite(strike=r.strike, type=r.type, premium=r.premium, gamma=r.gamma)
            for r in gex_rows
        ],
        conviction_score=conv.score,
        structure_score=stru.score,
        low_liquidity=low_liquidity,
    )

    # ── Niveles: precio ∩ opciones ──────────────────────────────────────────
    levels = find_levels(
        bars,
        spot=spot,
        now=now,
        chain=[
            ChainLevel(
                strike=c.strike, contract_type=c.contract_type,
                open_interest=c.open_interest, notional_value=c.notional_value,
            )
            for c in chain
        ],
        # `convRows ∪ notable`, dedupe por `id` — su `page.tsx`. Los muros de
        # dinero real se construyen con las DOS ventanas: la ancha da los muros
        # históricos y la corta los de esta semana.
        flows=[
            FlowLevel(strike=r.strike, type=r.type, aggression=r.aggression,
                      premium=r.premium)
            for r in _unir(conviction_rows, notable)
        ],
        gex=[GexLevel(strike=n.strike, net_gex=n.net_gex) for n in gex.nodes],
    )

    # ── Congelado: a partir de aquí solo se agrega ───────────────────────────
    # Una categoría sin evidencia va como None, NO como 0: cero es una medición,
    # ausencia no lo es. `weighted_score` renormaliza y recorta la confianza.
    sub = SubScores(
        aggression=agg.score if notable else None,
        conviction=conv.score if conviction_rows else None,
        unusuality=unu.score if conviction_rows else None,
        structure=stru.score if chain else None,
        iv_context=ivc.score if ivc.iv["contracts"] else None,
        validation=val.score if val.hit_rate["resolved"] else None,
    )

    # `callPct` de su `page.tsx`: se calcula sobre `convRows` —la ventana ancha—
    # y con el `+=` de JS, que concatena si el premium llega como texto. El port
    # lo hacía sobre los 5 días y con `sum()`, que además lanza con un texto.
    call_premium: Any = 0
    put_premium: Any = 0
    for r in conviction_rows:
        if r.type == "call":
            call_premium = js_add(call_premium, r.premium)
        elif r.type == "put":
            put_premium = js_add(put_premium, r.premium)
    total_dir = js_number(js_add(call_premium, put_premium))
    call_pct = (js_round(js_number(call_premium) / total_dir * 100)
                if total_dir > 0 else None)

    predictions = {
        h: predict_pro(
            spot=spot,
            iv=gex.iv,
            horizon_days=h,
            nodes=[
                LevelInput(
                    strike=n.strike, concentration=n.concentration,
                    side=n.side, net_gex=n.net_gex,
                )
                for n in gex.nodes
            ],
            scores=sub,
            regime=gex.regime,
            callvpct=call_pct,
            hit_rate=val.hit_rate["value"],
            low_liquidity=low_liquidity,
            calibration=calibration,
        )
        for h in horizons
    }

    ref = predictions[list(horizons)[0]] if predictions else None
    score = ref.score if ref else 0
    active = ref.active if ref else 0
    label, meaning = verdict_of(score)

    warnings: list[str] = []
    if low_liquidity:
        warnings.append(
            "SALVAGUARDA DE LIQUIDEZ: el nocional promedio por strike está por debajo "
            "del umbral. Los datos se marcan NO FIABLES y el sistema no recomienda "
            "operar; esto aplica también a la lectura del GEX."
        )
    if active < 6:
        faltan = [k for k, v in sub.as_dict().items() if v is None]
        warnings.append(
            f"Solo {active} de 6 sub-agentes tienen dato (faltan: {', '.join(faltan)}). "
            "El puntaje se renormaliza sobre el peso activo, así que NO es comparable "
            "con el de un ticker con las 6 categorías."
        )
    if val.coverage["below_target"] and val.coverage["flows"] > 0:
        warnings.append(
            f"El backtest de Confirmación de Precio cubre {val.coverage['days']} días; "
            "el documento pide 60. La tasa de acierto es provisional."
        )
    if sub.validation is not None:
        warnings.append(
            "Las bandas de puntos del sub-agente 6 (Confirmación de Precio) son una "
            "PROPUESTA: su PDF no trae tabla de puntuación."
        )

    return ScorecardResult(
        ticker=ticker.upper(),
        spot=spot,
        score=score,
        verdict=label,
        verdict_meaning=meaning,
        active=active,
        scores=sub.as_dict(),
        flow=flow,
        aggression=agg,
        conviction=conv,
        unusuality=unu,
        structure=stru,
        iv_context=ivc,
        validation=val,
        gex=gex,
        levels=levels,
        predictions=predictions,
        warnings=warnings,
        conviction_flow=list(conviction_rows),
        conviction_window=(f"{CONVICTION_DAYS}d" if conviction_trades is not None
                           else "5d"),
    )
