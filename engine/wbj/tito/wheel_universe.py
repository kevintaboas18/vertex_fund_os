"""Universo curado del screener de Wheel y asequibilidad del candidato.

Port de `web/lib/wheelUniverse.ts` y `web/lib/wheelAfford.ts`.

El universo **se edita a mano**. Criterios de admisión, en su orden (§4.1 de su
spec):

1. Opcionabilidad real: vencimientos semanales y OI agregado alto.
2. Sería aceptable poseerla — la Wheel te puede dejar con 100 acciones durante
   meses. Nada de quiebras ni biotecnológicas binarias.
3. Cobertura de tramos de precio, para que una cuenta chica opere.
4. Los ETFs de índice van aparte: menor riesgo idiosincrático.

El módulo **no valida** esta lista contra el mercado. Si un ticker deja de
cumplir, se saca a mano y se anota por qué.

La asequibilidad (`afford_of`) corre en el CLIENTE en su app, porque el saldo
vive en localStorage y nunca llega al servidor. Aquí es una función pura más:
quien la llame le pasa el efectivo, y la ruta de Vertex **no** lo hace.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

from .wheel import WheelCandidate

__all__ = ["WheelTier", "WheelSymbol", "WHEEL_UNIVERSE",
           "AffordResult", "afford_of", "sort_by_afford_then_score"]

WheelTier = Literal["etf", "barato", "medio", "caro"]


@dataclass(frozen=True)
class WheelSymbol:
    ticker: str
    tier: WheelTier
    razon: str


WHEEL_UNIVERSE: list[WheelSymbol] = [
    WheelSymbol("SPY", "etf", "S&P 500 — la cadena más líquida del mundo"),
    WheelSymbol("QQQ", "etf", "Nasdaq 100 — muy líquido, más prima que SPY"),
    WheelSymbol("IWM", "etf", "Small caps — colateral moderado"),
    WheelSymbol("DIA", "etf", "Dow 30 — prima baja pero estable"),
    WheelSymbol("XLF", "etf", "Financieras — colateral bajo para un ETF"),
    WheelSymbol("XLE", "etf", "Energía — IV alta con frecuencia"),
    WheelSymbol("NVDA", "caro", "Mega cap con la prima más gorda del índice"),
    WheelSymbol("MSFT", "caro", "Mega cap estable, cadena profunda"),
    WheelSymbol("META", "caro", "Mega cap con IV alta"),
    WheelSymbol("NFLX", "caro", "Cadena líquida, prima alta"),
    WheelSymbol("AVGO", "caro", "Semis de mega cap, opciones activas"),
    WheelSymbol("COST", "caro", "Defensiva de calidad, poseerla no duele"),
    WheelSymbol("LLY", "caro", "Farmacéutica grande, no binaria"),
    WheelSymbol("AAPL", "medio", "La cadena de acción individual más líquida"),
    WheelSymbol("AMZN", "medio", "Mega cap con colateral alcanzable"),
    WheelSymbol("GOOGL", "medio", "Mega cap, cadena profunda"),
    WheelSymbol("TSLA", "medio", "IV alta de forma persistente"),
    WheelSymbol("AMD", "medio", "Semis con IV alta y cadena líquida"),
    WheelSymbol("DIS", "medio", "Marca consolidada, prima decente"),
    WheelSymbol("BAC", "medio", "Banco grande, colateral bajo"),
    WheelSymbol("KO", "medio", "Defensiva con dividendo — cómoda de poseer"),
    WheelSymbol("PFE", "medio", "Farmacéutica grande con dividendo"),
    WheelSymbol("INTC", "medio", "Semis barata, cadena muy activa"),
    WheelSymbol("UBER", "medio", "Cadena líquida, IV media"),
    WheelSymbol("COIN", "medio", "IV muy alta — prima gorda, riesgo real"),
    WheelSymbol("MU", "medio", "Memoria, cíclica con IV alta"),
    WheelSymbol("CVX", "medio", "Energía integrada con dividendo"),
    WheelSymbol("F", "barato", "Colateral bajo y cadena sorprendentemente líquida"),
    WheelSymbol("SOFI", "barato", "Fintech barata con opciones activas"),
    WheelSymbol("PLTR", "barato", "IV alta y cadena muy negociada"),
    WheelSymbol("NIO", "barato", "Colateral bajo, IV alta — riesgo país declarado"),
    WheelSymbol("WULF", "barato", "Minería de bitcoin, colateral muy bajo"),
    WheelSymbol("RIOT", "barato", "Proxy de bitcoin con IV alta"),
    WheelSymbol("MARA", "barato", "Proxy de bitcoin, cadena activa"),
    WheelSymbol("CCL", "barato", "Cruceros, colateral bajo"),
    WheelSymbol("SNAP", "barato", "Colateral bajo, IV alta"),
    WheelSymbol("T", "barato", "Telecom con dividendo — cómoda de poseer"),
    WheelSymbol("VALE", "barato", "Minera con dividendo y colateral bajo"),
    WheelSymbol("HOOD", "barato", "Bróker, IV alta"),
    WheelSymbol("LCID", "barato", "Colateral mínimo — el más especulativo de la lista"),
]


@dataclass(frozen=True)
class AffordResult:
    affordable: bool
    #: Cuánto efectivo falta para cubrir el colateral, en $. 0 si alcanza.
    shortfall: float


def afford_of(candidate: WheelCandidate, cash: float) -> AffordResult:
    collateral = candidate.metrics.collateral if candidate.metrics else math.inf
    if candidate.blocked or not math.isfinite(collateral):
        return AffordResult(affordable=False, shortfall=0.0)
    affordable = collateral <= cash
    return AffordResult(affordable=affordable,
                        shortfall=0.0 if affordable else collateral - cash)


def sort_by_afford_then_score(candidates: Sequence[WheelCandidate],
                              cash: float) -> list[tuple[WheelCandidate, AffordResult]]:
    """Bloqueados al final; entre los operables, primero los que caben.

    Su versión devuelve el candidato con `afford` pegado encima
    (`{...c, afford}`); aquí va como par para no mutar un dataclass congelado.
    """
    pares = [(c, afford_of(c, cash)) for c in candidates]
    return sorted(pares, key=lambda p: (p[0].blocked, not p[1].affordable,
                                        -(p[0].score.total if p[0].score else 0)))
