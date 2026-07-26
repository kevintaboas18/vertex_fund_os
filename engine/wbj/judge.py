"""Qualitative judgment agent — the Claude sub-agent that answers the
specialists' `JudgmentRequest`s.

The deterministic Python engine leaves genuinely qualitative metrics
(moat classification, catalyst probability, thesis killers, TAM tier,
customer concentration) as `NOT_SCORABLE` — they aren't numbers. This
module hands those questions to Claude *with the company's real data in
context*, gets structured answers back, and returns `Judgment`s that
`wbj.overlay.merge.merge_overlay` folds into the specialist outputs.

Design:
- ONE API call per ticker: every open question is answered in a single
  request (cheaper + faster than one call per metric).
- Structured output (`messages.parse`) so answers come back validated.
- Honest by construction: the prompt tells Claude to answer
  `INSUFFICIENT` when the packet doesn't support a call, and every answer
  carries an evidence class + source, mirroring the Cerebro's
  "sin evidencia, no hay número" rule.
- No API key / SDK missing → returns `[]` gracefully (the dashboard then
  shows those metrics as still-pending, never a crash).

Model defaults to `claude-opus-4-8`; set `JUDGE_MODEL` in `API/.env`
(e.g. `claude-haiku-4-5`) to trade quality for cost.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from wbj.config import Settings
from wbj.core.nullstates import EvidenceClass
from wbj.schemas.overlay import Judgment
from wbj.specialists.common import JudgmentRequest

_SYSTEM = """Eres un analista de inversiones senior del sistema Ruta 2030. Tu \
trabajo es responder preguntas CUALITATIVAS que el motor cuantitativo no puede \
calcular (clasificación de foso competitivo, catalizadores, riesgos clave, \
concentración de clientes, tier de TAM).

Reglas innegociables:
- Responde SOLO con evidencia. Si los datos provistos no bastan para una \
conclusión, responde con answer="INSUFFICIENT" y evidence_class="Q".
- Nunca inventes cifras. Un juicio cualitativo puede citar contexto, no \
fabricar un número.
- Cada respuesta declara su clase de evidencia: R (reportado en filings), \
C (calculado de datos duros), E (estimación externa), A (supuesto razonado), \
Q (cualitativo/no cuantificable).
- Ajusta cada respuesta al formato pedido en schema_hint (ej. "one of \
Wide|Narrow|None" → devuelve exactamente una de esas palabras)."""


class _Answer(BaseModel):
    request_id: str
    answer: str = Field(description="Respuesta en el formato del schema_hint, o 'INSUFFICIENT'")
    evidence_class: str = Field(description="R, C, E, A, o Q")
    source: str = Field(description="De dónde sale el juicio (ej. '10-K FY2025', 'perfil FMP')")
    rationale: str = Field(description="1-2 frases justificando la respuesta")


class _Answers(BaseModel):
    answers: list[_Answer]


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off a pydantic model OR a plain dict.

    The judge is called with the full Packet on the normal route and with the
    MVP dict on the fast route. `getattr` alone silently returns None for a
    dict, which used to strip the ticker and the whole facts table out of the
    fast-route context — the judge then saw only FMP's marketing blurb.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _fmt_value(val: Any) -> str | None:
    """Render a scalar for the context block; None when there is nothing to show."""
    if val is None:
        return None
    if isinstance(val, bool):
        return "sí" if val else "no"
    if isinstance(val, (int, float)):
        return f"{val:,.4g}" if abs(val) < 1000 else f"{val:,.0f}"
    text = str(val).strip()
    return text or None


def _computed_metrics(outputs: Any) -> list[str]:
    """The numbers the specialists ALREADY computed, so the judge can apply the
    Cerebro's quantitative gates instead of guessing.

    `business_analysis`'s moat request literally says spread persistence, margin
    stability and concentration "are computed mechanically above" — but nothing
    put them above. They live on the specialist outputs, not on the packet, so
    without this the judge was asked to apply a gate it could not see the inputs
    of. `01_business_analysis/AGENT.md` forbids declaring a moat from language
    alone; feeding the measured effects is what makes that rule enforceable.
    """
    lines: list[str] = []
    for out in (outputs or []):
        # `outputs` may be [(key, output)] pairs or bare outputs.
        if isinstance(out, tuple) and len(out) == 2:
            out = out[1]
        agent = _attr(out, "agent_id") or "?"
        rows: list[str] = []
        for metric in (_attr(out, "metrics") or []):
            mid = _attr(metric, "metric_id") or _attr(metric, "id")
            if not mid:
                continue
            value = _attr(metric, "value")
            shown = _fmt_value(_attr(value, "value") if value is not None else None)
            score = _attr(metric, "score10")
            unit = _attr(value, "unit") if value is not None else None
            if shown is None and score is None:
                # NOT_SCORABLE stays visible: the judge must know what is missing,
                # otherwise it cannot honestly answer INSUFFICIENT.
                rows.append(f"    {mid}: N/S")
                continue
            parts = []
            if shown is not None:
                parts.append(f"{shown}{f' {unit}' if unit else ''}")
            if score is not None:
                parts.append(f"score {_fmt_value(score)}/10")
            rows.append(f"    {mid}: {' · '.join(parts)}")
        if rows:
            lines.append(f"  [{agent}]")
            lines.extend(rows)
    return lines


def _company_context(packet: Any, outputs: Any = None) -> str:
    """Compact, factual snapshot of the company for the judge to reason over.

    Accepts the full Packet (pydantic) or the MVP dict, plus — when available —
    the specialist outputs, so the metrics the engine already computed reach the
    judge instead of only five raw balance-sheet figures.
    """
    lines: list[str] = []
    sec = _attr(packet, "security")
    if sec is not None:
        lines.append(f"Ticker: {_attr(sec, 'ticker', '?')} ({_attr(sec, 'exchange', '?')})")
    facts = _attr(packet, "facts_table")
    if isinstance(facts, dict):
        # No arbitrary cap: facts_table is a short curated table (revenue,
        # diluted_shares, cash, total_debt, price). Truncating it would drop
        # evidence silently, which is exactly what the Cerebro forbids.
        for k, v in facts.items():
            shown = _fmt_value(_attr(v, "value") if not isinstance(v, (int, float, str)) else v)
            if shown is not None:
                lines.append(f"  {k}: {shown}")
    # FMP profile (sector/industry/description) if present on the packet dict.
    prof = _attr(packet, "fmp_profile")
    if isinstance(prof, list) and prof and isinstance(prof[0], dict):
        p = prof[0]
        for key in ("companyName", "sector", "industry", "country", "description"):
            if p.get(key):
                lines.append(f"  {key}: {str(p[key])[:400]}")
    metric_lines = _computed_metrics(outputs)
    if metric_lines:
        lines.append("")
        lines.append("Métricas YA calculadas por los especialistas "
                     "(úsalas para los gates cuantitativos; N/S = sin dato, no la estimes):")
        lines.extend(metric_lines)
    return "\n".join(lines) or "(sin contexto estructurado disponible)"


def _to_evidence(code: str) -> EvidenceClass | None:
    try:
        return EvidenceClass(code.strip().upper())
    except (ValueError, AttributeError):
        return None


def _coerce_answer(raw: str, schema_hint: str) -> Any:
    """Map the string answer to the type the hint expects, so
    merge_overlay's schema check accepts scoring-relevant answers."""
    hint = (schema_hint or "").lower()
    raw = raw.strip()
    if raw.upper() == "INSUFFICIENT":
        return raw.upper()
    # dict-shaped hint (e.g. "{probability: 0-1, ...}") → parse JSON
    if hint.startswith("{") or (":" in hint and "one of" not in hint and "array" not in hint):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {"value": v}
        except (json.JSONDecodeError, ValueError):
            return raw  # context-only
    # array hint → wrap into {"items": [...]}
    if "array" in hint:
        try:
            v = json.loads(raw)
            return {"items": v if isinstance(v, list) else [v]}
        except (json.JSONDecodeError, ValueError):
            return {"items": [raw]}
    # scalar numeric hint → float
    if any(t in hint for t in ("float", "number", "0-10", "integer", "1-5", "0-1", "probability")):
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw  # enum / plain string


def answer_judgments(
    packet: Any,
    requests: list[JudgmentRequest],
    settings: Settings,
    client: Any = None,
    outputs: Any = None,
) -> list[Judgment]:
    """Ask Claude to answer every open judgment request for one ticker.

    Returns [] (no crash) when there are no requests, no API key, or the
    `anthropic` SDK isn't installed. `client` is injectable for tests.
    `outputs` are the specialist outputs the requests came from; passing them
    puts the already-computed metrics in the judge's context, which the
    quantitative gates (e.g. business_analysis's wide-moat gate) depend on.
    """
    if not requests:
        return []
    if client is None:
        if not settings.anthropic_api_key:
            return []
        try:
            import anthropic
        except ImportError:
            return []
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    ctx = _company_context(packet, outputs)
    q_lines = [
        f"- request_id={r.request_id} | metric={r.metric_id} | schema_hint={r.schema_hint}\n"
        f"    pregunta: {r.question}"
        for r in requests
    ]
    user = (
        f"Datos de la empresa:\n{ctx}\n\n"
        f"Responde CADA una de estas {len(requests)} preguntas cualitativas. "
        f"Devuelve un answer por request_id, con evidence_class, source y rationale.\n\n"
        + "\n".join(q_lines)
    )

    resp = client.messages.parse(
        model=settings.judge_model,
        max_tokens=4096,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=_Answers,
    )
    parsed = resp.parsed_output
    if parsed is None:
        return []

    by_id = {r.request_id: r for r in requests}
    judgments: list[Judgment] = []
    for a in parsed.answers:
        req = by_id.get(a.request_id)
        if req is None:
            continue
        judgments.append(
            Judgment(
                request_id=a.request_id,
                answer=_coerce_answer(a.answer, req.schema_hint),
                evidence_class=_to_evidence(a.evidence_class),
                source=a.source or "claude-judge",
                rationale=a.rationale,
            )
        )
    return judgments
