"""Catálogo de condiciones de transacción de OPRA.

Port de `web/lib/conditions.ts` (vía MarketSnack `/api/trade_conditions`).

Sirve para saber si un trade fue single leg o multi leg **sin adivinar por
timestamp** — dos contratos con la misma marca de tiempo pueden ser un multileg
o dos órdenes independientes que coincidieron, y el código de condición lo
resuelve sin ambigüedad.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "TradeCondition",
    "TRADE_CONDITIONS",
    "MULTI_LEG_CODES",
    "CANCELED_CODES",
    "condition_of",
    "is_canceled_condition",
    "is_multi_leg_condition",
]


@dataclass(frozen=True)
class TradeCondition:
    id: int
    code: str
    name: str


TRADE_CONDITIONS: list[TradeCondition] = [
    TradeCondition(201, "CANC", "Canceled"),
    TradeCondition(202, "OSEQ", "Late and Out Of Sequence"),
    TradeCondition(203, "CNCL", "Last and Canceled"),
    TradeCondition(204, "LATE", "Late"),
    TradeCondition(205, "CNCO", "Opening Trade and Canceled"),
    TradeCondition(206, "OPEN", "Opening Trade, Late, and Out Of Sequence"),
    TradeCondition(207, "CNOL", "Only Trade and Canceled"),
    TradeCondition(208, "OPNL", "Opening Trade and Late"),
    TradeCondition(209, "AUTO", "Automatic Execution"),
    TradeCondition(210, "REOP", "Reopening Trade"),
    TradeCondition(219, "ISOI", "Intermarket Sweep Order"),
    TradeCondition(227, "SLAN", "Single Leg Auction Non ISO"),
    TradeCondition(228, "SLAI", "Single Leg Auction ISO"),
    TradeCondition(229, "SLCN", "Single Leg Cross Non ISO"),
    TradeCondition(230, "SLCI", "Single Leg Cross ISO"),
    TradeCondition(231, "SLFT", "Single Leg Floor Trade"),
    TradeCondition(232, "MLET", "Multi Leg auto-electronic trade"),
    TradeCondition(233, "MLAT", "Multi Leg Auction"),
    TradeCondition(234, "MLCT", "Multi Leg Cross"),
    TradeCondition(235, "MLFT", "Multi Leg floor trade"),
    TradeCondition(236, "MESL", "Multi Leg auto-electronic trade against single leg(s)"),
    TradeCondition(237, "TLAT", "Stock Options Auction"),
    TradeCondition(238, "MASL", "Multi Leg Auction against single leg(s)"),
    TradeCondition(239, "MFSL", "Multi Leg floor trade against single leg(s)"),
    TradeCondition(240, "TLET", "Stock Options auto-electronic trade"),
    TradeCondition(241, "TLCT", "Stock Options Cross"),
    TradeCondition(242, "TLFT", "Stock Options floor trade"),
    TradeCondition(243, "TESL", "Stock Options auto-electronic trade against single leg(s)"),
    TradeCondition(244, "TASL", "Stock Options Auction against single leg(s)"),
    TradeCondition(245, "TFSL", "Stock Options floor trade against single leg(s)"),
    TradeCondition(246, "CBMO", "Multi Leg Floor Trade of Proprietary Products"),
    TradeCondition(247, "MCTP", "Multilateral Compression Trade of Proprietary Products"),
    TradeCondition(248, "EXHT", "Extended Hours Trade"),
]

_BY_ID: dict[int, TradeCondition] = {c.id: c for c in TRADE_CONDITIONS}

#: Códigos que MarketSnack clasifica como MULTI LEG (su filtro "Filter by
#: Multi-Leg"). Todo lo demás cuenta como single leg — incluidos MESL/MFSL/MASL,
#: que se ejecutan "against single leg(s)" y MarketSnack lista en Single-Leg.
MULTI_LEG_CODES = frozenset({"MLET", "MLAT", "MLCT", "MLFT", "CBMO", "MCTP"})

#: Condiciones de transacción CANCELADA: la operación se anuló, así que no debe
#: contar en ningún análisis ni puntaje.
#:   CANC = cancelada · CNCL = última y cancelada · CNCO = apertura y cancelada
#:   CNOL = única del día y cancelada
CANCELED_CODES = frozenset({"CANC", "CNCL", "CNCO", "CNOL"})


def condition_of(id: int | None) -> TradeCondition | None:
    """Busca la condición por id. ``None`` si no está en el catálogo.

    Su `BY_ID` es un `Map`, que acepta cualquier valor como clave; un `dict` de
    Python solo acepta lo hashable, así que un `trade_condition_id` que llegue
    como lista o como objeto lanzaba `TypeError` y se llevaba `classify_flow`
    entero. Lo no hashable no está en el catálogo, que es la misma respuesta que
    da su `Map`: `undefined`.
    """
    if id is None:
        return None
    try:
        return _BY_ID.get(id)
    except TypeError:
        return None


def is_canceled_condition(id: int | None) -> bool:
    """¿La transacción fue cancelada? Esas se descartan del flujo."""
    c = condition_of(id)
    return c is not None and c.code in CANCELED_CODES


def is_multi_leg_condition(id: int | None) -> bool:
    """¿El trade fue multi leg? Se decide por el código real, no por heurística."""
    c = condition_of(id)
    return c is not None and c.code in MULTI_LEG_CODES
