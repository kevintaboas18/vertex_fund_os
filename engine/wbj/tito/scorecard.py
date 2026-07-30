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


def run_scorecard(
    ticker: str,
    raw_trades: Sequence[dict[str, Any]],
    chain: Sequence[ChainRow],
    bars: Sequence[LvlBar],
    now: datetime,
    spot: float | None = None,
    horizons: Sequence[int] = (10, 20, 30),
    iv_history: Sequence[dict] | None = None,
    calibration: dict | None = None,
) -> ScorecardResult:
    """Corre el pipeline completo y devuelve el scorecard con sus 3 escenarios.

    `bars` va del más viejo al más reciente. `spot` se toma del último cierre si
    no se pasa. `calibration` es la memoria del agente:
    ``{"bias_pct": float|None, "samples": int}``.
    """
    closes = [b.close for b in bars]
    if spot is None:
        spot = closes[-1] if closes else 0.0

    # ── Sub-agentes 1-3: sobre el tape ──────────────────────────────────────
    flow = classify_flow(raw_trades, now)
    notable = flow.interesting
    agg = aggression_score(notable)
    conv = conviction_score(notable)
    unu = unusuality_score(notable)

    # ── Sub-agente 4: sobre la cadena completa ──────────────────────────────
    stru = structure_score(chain)
    low_liquidity = bool(stru.notional["low_liquidity"])

    # ── Sub-agente 5: contexto de volatilidad ───────────────────────────────
    ivc = iv_context_score(notable, closes, iv_history)

    # ── Sub-agente 6: backtest del propio tape ──────────────────────────────
    val_bars = [ValBar(time=b.time, high=b.high, low=b.low, close=b.close) for b in bars]
    val_flows = [
        FlowLite(
            id=r.id, timestamp=r.timestamp, type=r.type, strike=r.strike,
            expiration=r.expiration, asset_price=r.asset_price,
            premium=r.premium, aggression=r.aggression,
        )
        for r in notable
    ]
    val = validation_score(val_flows, val_bars, now)

    # ── GEX (recibe convicción/estructura SOLO para modular confianza) ──────
    gex = gex_analysis(
        chain,
        closes,
        spot,
        now,
        trades=[
            TradeLite(strike=r.strike, type=r.type, premium=r.premium, gamma=r.gamma)
            for r in notable
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
        flows=[
            FlowLevel(strike=r.strike, type=r.type, aggression=r.aggression, premium=r.premium)
            for r in notable
        ],
        gex=[GexLevel(strike=n.strike, net_gex=n.net_gex) for n in gex.nodes],
    )

    # ── Congelado: a partir de aquí solo se agrega ───────────────────────────
    # Una categoría sin evidencia va como None, NO como 0: cero es una medición,
    # ausencia no lo es. `weighted_score` renormaliza y recorta la confianza.
    sub = SubScores(
        aggression=agg.score if notable else None,
        conviction=conv.score if notable else None,
        unusuality=unu.score if notable else None,
        structure=stru.score if chain else None,
        iv_context=ivc.score if ivc.iv["contracts"] else None,
        validation=val.score if val.hit_rate["resolved"] else None,
    )

    call_premium = sum(r.premium for r in notable if r.type == "call")
    put_premium = sum(r.premium for r in notable if r.type == "put")
    total_dir = call_premium + put_premium
    call_pct = (call_premium / total_dir * 100) if total_dir > 0 else None

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
    )
