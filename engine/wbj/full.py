"""Full 6-category deterministic scorecard — completa el engine de Victor.

Combina, SIN LLM y SIN tocar el código de Victor:
- Business / Financial / Risk: tal cual los calcula su `quick_scorecard`
  (fundamentales EDGAR + tablas de anclaje).
- Market & Growth / Technical & Momentum / Valuation: los scorers deterministas
  nuevos (`market.py`, `technical.py`, `valuation.py`), en el mismo estilo.

Devuelve un scorecard de 6 categorías (puntos, cobertura, confianza) con la
misma matemática de agregación de Cerebro. Lo que no tiene evidencia queda
NOT_SCORABLE (cobertura baja) — nunca se inventa.
"""

from __future__ import annotations

from wbj.core.confidence import confidence
from wbj.core.scoring import CATEGORY_WEIGHTS, Category
from wbj.market import market_category
from wbj.quick import quick_scorecard
from wbj.technical import technical_category
from wbj.valuation import valuation_category

# Calidad de fuente por categoría (Cerebro/CONFIDENCE_ENGINE: regulatorio > mercado > estimado)
_SRC_QUALITY = {"business": 90, "financial": 90, "risk": 90,
                "market": 70, "technical": 75, "valuation": 85}
_SRC_LABEL = {"business": "EDGAR (fundamentales)", "financial": "EDGAR (fundamentales)",
              "risk": "EDGAR (fundamentales)", "market": "EDGAR + consenso",
              "technical": "OHLCV de mercado", "valuation": "EDGAR + precio"}


def _cat_confidence(coverage: float, source_quality: int) -> float:
    """Confianza determinista (Cerebro/CONFIDENCE_ENGINE): 0.30*cobertura +
    0.25*calidad_fuente + 0.20*frescura + 0.15*consistencia + 0.10*ajuste_modelo.
    Frescura/consistencia/ajuste asumidos razonables para datos recientes."""
    return round(confidence(coverage * 100.0, float(source_quality), 80.0, 75.0, 80.0), 0)


def _cat_dict(key: str, cat: Category) -> dict:
    cov = round(cat.coverage(), 2)
    return {
        "label": {"business": "Business", "financial": "Financial", "market": "Market & Growth",
                  "technical": "Technical & Momentum", "risk": "Risk & Resilience",
                  "valuation": "Valuation"}[key],
        "points": round(cat.points(), 2),
        "max": float(CATEGORY_WEIGHTS[key]),
        "score10": round(cat.score10(), 1),
        "coverage": cov,
        "confidence": _cat_confidence(cov, _SRC_QUALITY[key]),
        "source": _SRC_LABEL[key],
        "dimensions": [
            {"name": d.name,
             "score10": round(d.score10_value().value, 1) if d.score10_value().is_valid else None,
             "scorable": bool(d.score10_value().is_valid)}
            for d in cat.dimensions],
    }


def _row_to_dict(row: dict) -> dict:
    """Adapta una fila de quick_scorecard (business/financial/risk) al formato unificado."""
    key = row["key"]
    scored = row.get("status") == "scored" and row.get("score10") is not None
    cov = round(row.get("coverage", 0.0), 2)
    return {
        "label": row["label"].replace(" (quick)", ""),
        "points": round(row.get("points") or 0.0, 2),
        "max": float(row["max_points"]),
        "score10": round(row["score10"], 1) if scored else 0.0,
        "coverage": cov,
        "confidence": _cat_confidence(cov, _SRC_QUALITY[key]) if scored else 0.0,
        "source": _SRC_LABEL[key],
        "dimensions": [],
    }


def full_scorecard(packet: dict, ohlcv: dict | None = None, price: float | None = None,
                   market_cap: float | None = None, estimates: dict | None = None,
                   benchmark_closes=None) -> dict:
    """Scorecard determinista de las 6 categorías.

    packet: paquete EDGAR de Victor (_build_packet).
    ohlcv: {'closes','highs','lows','volumes'} ajustado (para Technical).
    price / market_cap / estimates: para Valuation y Market.
    """
    quick = quick_scorecard(packet)                      # business/financial/risk EXACTOS de Victor
    quick_rows = {r["key"]: r for r in quick["categories"]}

    cats: dict[str, dict] = {}
    for key in ("business", "financial", "risk"):
        cats[key] = _row_to_dict(quick_rows[key])

    ohlcv = ohlcv or {}
    tech = technical_category(ohlcv.get("closes", []), ohlcv.get("highs"),
                              ohlcv.get("lows"), ohlcv.get("volumes"), benchmark_closes)
    cats["technical"] = _cat_dict("technical", tech)
    cats["market"] = _cat_dict("market", market_category(packet, estimates))
    cats["valuation"] = _cat_dict("valuation", valuation_category(packet, price, market_cap))

    order = ["business", "financial", "market", "technical", "risk", "valuation"]
    raw_total = round(sum(cats[k]["points"] for k in order), 1)
    total_conf = round(sum(cats[k]["max"] * cats[k]["confidence"] for k in order) / 100.0, 0)
    evidence = int(round(sum(cats[k]["max"] * cats[k]["coverage"] for k in order)))

    return {
        "categories": {k: cats[k] for k in order},
        "raw_total": raw_total,
        "total_confidence": total_conf,
        "evidence_points_covered": evidence,
        "incomplete": [k for k in order if cats[k]["coverage"] < 0.70],
        "source": "wbj-engine (determinista, sin LLM)",
        "entity": packet.get("entity") if packet else None,
        "ticker": packet.get("ticker") if packet else None,
    }
