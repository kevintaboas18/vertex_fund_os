"""Company health dashboard powered by the 6 real Cerebro specialists.

This is the whole point of the engine: feed a company's data through the
six specialist agents (business, financial, market, technical, risk,
valuation) and present, per area, whether it is HEALTHY, worth WATCHING,
WEAK, or simply MISSING DATA — plus the things to keep an eye on and the
Bull/Base/Bear reference targets.

It is a research dashboard, never a buy/sell instruction (Cerebro rule).

Health is COVERAGE-AWARE: a low score on an area with little data reads as
"necesita datos", not "débil". A 0/10 at 10% coverage means the agent
could not find enough evidence yet — buy the full FMP data and it fills in.
"""

from __future__ import annotations

from wbj.core.confidence import confidence_label
from wbj.schemas.packet import Packet
import wbj.specialists.business as biz_mod
import wbj.specialists.financial as fin_mod
import wbj.specialists.market as mkt_mod
import wbj.specialists.risk as risk_mod
import wbj.specialists.technical as tech_mod
import wbj.specialists.valuation as val_mod

# (key, Spanish label, module) in the order the dashboard shows them.
_AREAS = [
    ("business", "Negocio y ventaja competitiva", biz_mod),
    ("financial", "Finanzas y balance", fin_mod),
    ("market", "Mercado y crecimiento", mkt_mod),
    ("technical", "Tendencia y momentum", tech_mod),
    ("risk", "Riesgo y resiliencia", risk_mod),
    ("valuation", "Valuación", val_mod),
]

# Coverage floor below which we don't trust the score enough to call an area
# healthy or weak — we say "necesita datos" instead. Matches Cerebro's 0.70
# gate-eligibility threshold.
_COVERAGE_FLOOR = 0.70


def _health(score10: float, coverage: float) -> tuple[str, str]:
    """(health_key, Spanish_label). Coverage-aware so thin data never
    masquerades as a weak company."""
    if coverage < _COVERAGE_FLOOR:
        return "sin_datos", "Necesita más datos"
    if score10 >= 7.0:
        return "saludable", "Saludable"
    if score10 >= 4.0:
        return "vigilar", "Para vigilar"
    return "debil", "Débil"


def _watch_items(output) -> list[str]:
    """Things to keep an eye on: mandatory flags (cleaned up) + the
    lowest-scoring dimension when the area is scorable."""
    items: list[str] = []
    for flag in output.mandatory_flags:
        # flags look like "SOLVENCY_WARNING: ..." — keep the human part.
        text = flag.split(":", 1)[1].strip() if ":" in flag else flag.replace("_", " ").title()
        items.append(text)
    return items[:4]


def build_dashboard(packet: Packet, targets: dict | None = None,
                    overlay=None, judge: bool = False, settings=None) -> dict:
    """Run the 6 specialists over `packet` and assemble the health view.

    `overlay` (optional): pre-computed judgment answers to merge.
    `judge` (optional): when True, calls the Claude judgment agent to answer
    the specialists' open qualitative questions and merges the result —
    this is what fills Business/Market/Risk coverage. Needs an
    ANTHROPIC_API_KEY (via `settings`). `targets`: the Bull/Base/Bear dict.
    """
    from wbj.overlay.merge import collect_requests, merge_overlay

    areas = []
    scored_pts = 0.0
    weighted = 0.0
    outputs = []
    for key, label, mod in _AREAS:
        try:
            out = mod.run(packet)
        except Exception as e:  # never let one area crash the dashboard
            areas.append({
                "key": key, "label": label, "health": "sin_datos",
                "health_label": "No se pudo analizar", "score10": None,
                "coverage": 0.0, "confidence": None, "watch": [],
                "verdict": None, "error": f"{type(e).__name__}",
            })
            continue
        outputs.append(out)

    # Qualitative judgment: let Claude answer the open questions the code
    # can't score (moat, catalysts, concentration...), then merge.
    if judge and settings is not None and getattr(settings, "anthropic_api_key", None):
        from wbj.judge import answer_judgments

        reqs = collect_requests(outputs)
        judgments = answer_judgments(packet, reqs, settings)
        if judgments:
            overlay = (overlay or []) + judgments

    if overlay:
        outputs = merge_overlay(outputs, overlay)

    by_key = {o.agent_id.split("_")[0]: o for o in outputs}
    for key, label, _mod in _AREAS:
        out = by_key.get(key)
        if out is None:
            continue
        score10 = round(out.category.score_10, 1)
        cov = out.coverage
        health, health_label = _health(score10, cov)
        # Only areas we actually trust feed the overall health number.
        if health not in ("sin_datos",):
            max_pts = out.category.max_points
            scored_pts += max_pts
            weighted += max_pts * score10
        areas.append({
            "key": key, "label": label,
            "health": health, "health_label": health_label,
            "score10": score10 if health != "sin_datos" else None,
            "coverage": round(cov, 2),
            "confidence": round(out.category.confidence) if out.category.confidence is not None else None,
            "confidence_label": confidence_label(out.category.confidence) if out.category.confidence is not None else None,
            "watch": _watch_items(out),
            "verdict": out.verdict,
        })

    # Order the assembled areas to match _AREAS (error entries already in place).
    order = {k: i for i, (k, _l, _m) in enumerate(_AREAS)}
    areas.sort(key=lambda a: order.get(a["key"], 99))

    overall = round(weighted / scored_pts, 1) if scored_pts else None
    all_watch = [w for a in areas for w in a["watch"]]
    return {
        "overall_health": overall,
        "areas_with_data": sum(1 for a in areas if a["health"] != "sin_datos"),
        "areas_total": len(_AREAS),
        "areas": areas,
        "watch_summary": all_watch[:6],
        "targets": targets,
        "disclaimer": (
            "Dashboard de research: salud por área según la metodología "
            "Ruta 2030. No es recomendación de compra/venta."
        ),
    }
