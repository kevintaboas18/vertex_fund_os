"""Task 22: report charts, per the root `CLAUDE.md` visualization rules.

The four rules are enforced here, not left to the caller:

1. **Never a single line.** A projection is always a *band*. A scenario
   with no width raises `ValueError("single-line projection prohibited")` —
   a lone line "miente con confianza".
2. **Label the assumptions.** Every projected band is annotated on-chart
   with the growth/margin assumptions behind it.
3. **The past is not projected.** History is drawn solid; anything
   projected is dotted. Always.
4. **The agent decides, not the chart.** These functions only draw values
   they are handed; they never compute or reinterpret a score.

All functions take their data plus `out_path`, write a 150-dpi PNG using
the headless `Agg` backend, and return the path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")  # headless: never needs a display
import matplotlib.pyplot as plt  # noqa: E402

_DPI = 150
_SOLID, _DOTTED = "-", ":"
_HIST = "#7c5cfc"
_BAND_COLORS = ("#22a06b", "#3b82f6", "#e5484d")


def _save(fig: Any, out_path: Path, *, source: str | None = None,
          as_of: str | None = None) -> Path:
    """Write the figure, stamping the provenance every chart must carry.

    visual-report.md: "Todo gráfico lleva: título, unidades, fuente y
    timestamp de la data." Titles were set and units labelled per chart, but
    nothing recorded WHERE the numbers came from or WHEN they were known --
    so a chart lifted out of its folder became an unattributed claim, which
    is exactly what the Cerebro's source rules exist to prevent.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = " · ".join(x for x in (source, f"data as of {as_of}" if as_of else None) if x)
    if stamp:
        fig.text(0.005, 0.005, stamp, fontsize=6.5, color="#9aa1ad", va="bottom", ha="left")
    fig.tight_layout(rect=(0, 0.03, 1, 1) if stamp else None)
    fig.savefig(out_path, dpi=_DPI)
    plt.close(fig)
    return out_path


def price_levels_chart(
    df: Sequence[Mapping[str, Any]],
    levels: Sequence[Mapping[str, Any]],
    smas: Mapping[str, float],
    out_path: Path,
    *,
    currency: str = "USD",
    source: str | None = None,
    as_of: str | None = None,
) -> Path:
    """Price history (solid) with support/resistance **zones as shaded
    bands** (rule 1 — a zone is a range, never a hairline) and SMA lines.

    `df`: rows with `date` and `close`. `levels`: rows with `label`,
    `lower`, `upper`. `smas`: `{"SMA50": 209.9, ...}`.
    """
    fig, ax = plt.subplots(figsize=(10, 5))
    xs = [r["date"] for r in df]
    ys = [r["close"] for r in df]
    ax.plot(range(len(ys)), ys, _SOLID, color=_HIST, lw=1.6, label="Price (history)")

    for i, z in enumerate(levels):
        lo, hi = float(z["lower"]), float(z["upper"])
        ax.axhspan(lo, hi, alpha=0.16, color=_BAND_COLORS[i % len(_BAND_COLORS)])
        ax.annotate(f"{z.get('label', 'zone')}  {lo:,.2f}–{hi:,.2f}",
                    xy=(0, (lo + hi) / 2), fontsize=8, va="center")

    for label, value in (smas or {}).items():
        ax.axhline(float(value), ls="--", lw=1.0, color="#9aa1ad")
        # Anclada al último punto pero desplazada HACIA DENTRO: anclada
        # justo en el borde, la última cifra se cortaba contra el marco
        # ("SMA200 193.1" en vez de "193.11"). Un número a medias en un
        # gráfico de niveles es peor que no ponerlo.
        ax.annotate(f"{label} {float(value):,.2f}", xy=(len(ys) - 1, float(value)),
                    xytext=(-4, 3), textcoords="offset points",
                    fontsize=8, ha="right", va="bottom", color="#5b6270")

    step = max(1, len(xs) // 6)
    ax.set_xticks(range(0, len(xs), step))
    ax.set_xticklabels([xs[i] for i in range(0, len(xs), step)], fontsize=8, rotation=0)
    ax.set_ylabel(f"Price ({currency}/share)")
    ax.set_xlabel("Session")
    ax.set_title("Price with support/resistance zones")
    ax.legend(fontsize=8, loc="upper left")
    return _save(fig, out_path, source=source, as_of=as_of)


def scenario_fan_chart(
    history: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
    out_path: Path,
    *,
    currency: str = "USD",
    source: str | None = None,
    as_of: str | None = None,
) -> Path:
    """History solid, each scenario a **dotted projected band** labeled
    with its assumptions.

    `scenarios`: rows with `name`, `low`, `high` and `assumptions` (free
    text that must state the growth/margin used). Raises `ValueError` when
    a scenario has no band width — rule 1 is not negotiable.
    """
    for s in scenarios:
        if float(s["high"]) == float(s["low"]):
            raise ValueError("single-line projection prohibited")
        # Rule 2 is as non-negotiable as rule 1: "Si no dices los supuestos,
        # el número no significa nada." The label fell back to an empty
        # string, so a band with no stated growth/margin drew silently and
        # read as though it had been derived from something.
        if not str(s.get("assumptions", "")).strip():
            raise ValueError(
                f"scenario {s.get('name', '?')!r} has no stated assumptions: "
                "rule 2 requires the growth/margin behind every projected band")

    fig, ax = plt.subplots(figsize=(10, 5))
    ys = [r["value"] for r in history]
    n = len(ys)
    ax.plot(range(n), ys, _SOLID, color=_HIST, lw=1.8, label="History (actual)")

    horizon = max(4, n // 4)
    x_proj = [n - 1, n - 1 + horizon]
    last = ys[-1] if ys else 0.0
    for i, s in enumerate(scenarios):
        color = _BAND_COLORS[i % len(_BAND_COLORS)]
        lo, hi = float(s["low"]), float(s["high"])
        # Projected band: dotted edges + light fill. Never a single line.
        ax.plot(x_proj, [last, lo], _DOTTED, color=color, lw=1.4)
        ax.plot(x_proj, [last, hi], _DOTTED, color=color, lw=1.4)
        ax.fill_between(x_proj, [last, lo], [last, hi], color=color, alpha=0.14)
        # Encima del borde SUPERIOR de la banda, no en su centro. Anclada al
        # centro, el texto caía justo sobre las dos líneas punteadas que
        # definen el escenario y tapaba el rango que pretende explicar —
        # con dos escenarios, cada etiqueta se comía la banda del otro.
        ax.annotate(f"{s.get('name', 'scenario')}  {lo:,.0f}–{hi:,.0f}\n{s.get('assumptions', '')}",
                    xy=(x_proj[1], hi), xytext=(-2, 6), textcoords="offset points",
                    fontsize=8, va="bottom", ha="right", color=color)

    ax.axvline(n - 1, color="#c9ced8", lw=1.0)
    # Aire arriba para que la etiqueta más alta no choque con el marco.
    _lo_eje, _hi_eje = ax.get_ylim()
    ax.set_ylim(_lo_eje, _hi_eje + (_hi_eje - _lo_eje) * 0.13)
    ax.annotate("projected →", xy=(n - 1, min(ys) if ys else 0), fontsize=8, color="#9aa1ad")
    ax.set_ylabel(f"Price ({currency}/share)")
    ax.set_xlabel("Session (projection beyond the divider)")
    ax.set_title("Scenarios (history solid · projections dotted, with assumptions)")
    ax.legend(fontsize=8, loc="upper left")
    return _save(fig, out_path, source=source, as_of=as_of)


def scorecard_chart(category_points: Sequence[Mapping[str, Any]], out_path: Path, *,
                    source: str | None = None, as_of: str | None = None) -> Path:
    """Horizontal bars of awarded points vs each category's maximum.

    Rows with `unscored=True` are drawn as an empty track labeled N/S —
    never a full-length zero bar, which would read as "scored badly".
    """
    fig, ax = plt.subplots(figsize=(8, 4.2))
    labels = [c.get("label", c.get("key", "")) for c in category_points]
    y = range(len(labels))
    for i, c in enumerate(category_points):
        maximum = float(c["max_points"])
        ax.barh(i, maximum, color="#eef0f4")
        if c.get("unscored"):
            ax.annotate("N/S — no evidence", xy=(0.4, i), va="center", fontsize=8, color="#9aa1ad")
        else:
            ax.barh(i, float(c["points"]), color="#22a06b")
            ax.annotate(f"{float(c['points']):,.1f}/{maximum:,.0f}", xy=(maximum, i),
                        xytext=(5, 0), textcoords="offset points",
                        va="center", ha="left", fontsize=8, color="#5b6270")
    # Sitio para esa etiqueta. Sin esto arranca justo en el máximo del eje
    # (20 pts) y se sale del área de dibujo: "11.4/20" quedaba pegado al
    # borde y las categorías de 20 puntos perdían el texto.
    if category_points:
        techo = max(float(c["max_points"]) for c in category_points)
        ax.set_xlim(0, techo * 1.16)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Category points (of 100 total)")
    ax.set_title("Category scorecard (points vs max)")
    return _save(fig, out_path, source=source, as_of=as_of)


def football_field_chart(
    reference_bands: Sequence[Mapping[str, Any]],
    current_price: float,
    out_path: Path,
    *,
    currency: str = "USD",
    source: str | None = None,
    as_of: str | None = None,
) -> Path:
    """Valuation ranges per model/scenario as horizontal bands, with the
    current price marked — the classic football field. Each band is a
    range (rule 1); a zero-width band is rejected."""
    for b in reference_bands:
        if float(b["high"]) == float(b["low"]):
            raise ValueError("single-line projection prohibited")

    fig, ax = plt.subplots(figsize=(8, 4.2))
    labels = [b.get("label", "") for b in reference_bands]
    for i, b in enumerate(reference_bands):
        lo, hi = float(b["low"]), float(b["high"])
        ax.barh(i, hi - lo, left=lo, height=0.5,
                color=_BAND_COLORS[i % len(_BAND_COLORS)], alpha=0.6)
        ax.annotate(f"{lo:,.0f}–{hi:,.0f}", xy=(hi, i), va="center", ha="left", fontsize=8)
        if b.get("assumptions"):
            ax.annotate(str(b["assumptions"]), xy=(lo, i - 0.32), fontsize=7, color="#5b6270")

    ax.axvline(float(current_price), color="#16181d", lw=1.6)
    # Anchored in AXES coordinates for the vertical: pinning it to data
    # coordinate -0.7 put the label below the axis whenever there were few
    # bands, so the one line a reader most needs identified -- the current
    # price the ranges are being compared against -- was drawn unlabelled.
    ax.annotate(f"current {float(current_price):,.2f}",
                xy=(float(current_price), 0.98), xycoords=("data", "axes fraction"),
                xytext=(-4, 0), textcoords="offset points",
                fontsize=8, ha="right", va="top", color="#16181d")
    # The topmost band's assumption label sits above its bar; without head
    # room it was clipped by the axis edge -- and rule 2's whole point is
    # that the assumption has to be readable.
    ax.set_ylim(-0.8, len(labels) - 0.35)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel(f"Value ({currency}/share)")
    ax.set_title("Valuation football field (ranges, with declared assumptions)")
    return _save(fig, out_path, source=source, as_of=as_of)
