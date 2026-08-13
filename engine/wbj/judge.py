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

Modelo por defecto `claude-opus-5`; fija `JUDGE_MODEL` en `API/.env`
(p. ej. `claude-haiku-4-5`) para abaratar.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from wbj.config import Settings
from wbj.core.nullstates import EvidenceClass
from wbj.schemas.overlay import Judgment
from wbj.specialists.common import JudgmentRequest

logger = logging.getLogger(__name__)

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


def _company_context(packet: Any) -> str:
    """Compact, factual snapshot of the company for the judge to reason over.

    Accepts the full Packet (pydantic) or the MVP dict; pulls whatever is
    present without assuming a rich schema.
    """
    lines: list[str] = []
    sec = getattr(packet, "security", None)
    if sec is not None:
        lines.append(f"Ticker: {getattr(sec, 'ticker', '?')} ({getattr(sec, 'exchange', '?')})")
    elif isinstance(packet, dict) and packet.get("ticker"):
        # The MVP path carries a plain dict, where `getattr` finds nothing:
        # only `fmp_profile` had a dict fallback, so the judge was told which
        # sector the company is in but never which company it was looking at.
        lines.append(f"Ticker: {packet['ticker']}")
    facts = getattr(packet, "facts_table", None)
    if isinstance(facts, dict):
        # No slice: the facts table is the Phase-1 *common* table (revenue,
        # diluted shares, cash, debt, price), fixed and small by construction.
        # An arbitrary `[:8]` could only ever drop a validated fact silently.
        for k, v in facts.items():
            val = getattr(v, "value", None)
            if val is not None:
                lines.append(f"  {k}: {val:,.0f}" if isinstance(val, (int, float)) else f"  {k}: {val}")
    # FMP profile (sector/industry/description) if present on the packet dict.
    prof = getattr(packet, "fmp_profile", None) or (packet.get("fmp_profile") if isinstance(packet, dict) else None)
    if isinstance(prof, list) and prof:
        p = prof[0]
        for key in ("companyName", "sector", "industry", "country", "description"):
            if p.get(key):
                text = str(p[key])
                lines.append(f"  {key}: {text[:400]}")
    return "\n".join(lines) or "(sin contexto estructurado disponible)"


def _metric_line(row: Any) -> str:
    """One computed metric, value or null-state, with its evidence class."""
    mid = getattr(row, "metric_id", "?")
    val = getattr(row, "value", None)
    if val is None:
        return f"  {mid}: {getattr(getattr(row, 'state', None), 'value', 'MISSING')}"
    unit = getattr(row, "unit", "") or ""
    ec = getattr(getattr(row, "evidence_class", None), "value", None)
    return f"  {mid}: {val:,.4g} {unit}".rstrip() + (f" [{ec}]" if ec else "")


def _specialist_metrics_block(outputs: Any, requests: list[JudgmentRequest]) -> str:
    """The numbers the specialists already computed, for the agents that asked.

    Several questions tell the judge that part of the answer is "computed
    mechanically above" -- `moat_classification` cites DECISION_RULES.md's
    wide-moat gate, whose first, second and fourth conditions are pure
    arithmetic (BUS-SPREAD-014 persistence, BUS-RANGE-010 margin range,
    BUS-CONC-003 concentration). There was no "above": the prompt carried the
    packet's five common facts and nothing else, so the judge was asked to
    apply a quantitative gate without being shown one of its inputs.

    Scope is the asking agent's own rows -- the question came from that
    specialist, so that specialist's computed evidence is what it refers to.
    Null rows are included deliberately: MISSING/NOT_SCORABLE is what
    justifies an INSUFFICIENT answer, and hiding it would leave the judge
    guessing whether a number exists. `mandatory_flags` come along because
    DECISION_RULES.md's flags (VALUE_DESTRUCTION, CONCENTRATION_RED_FLAG,
    DILUTION_RED_FLAG) are gate conditions stated as flags.

    Unbounded by design: each FORMULAS.md registry is fixed-size (BUS 30,
    FIN 33, MKT 25, TECH 40, RSK 35, VAL 44), so the block is bounded by
    construction and needs no arbitrary cap that could drop the one metric
    the question turned on.
    """
    if not outputs:
        return ""
    asking = {r.agent_id for r in requests}
    blocks: list[str] = []
    for out in outputs:
        agent = getattr(out, "agent_id", None)
        if agent not in asking:
            continue
        rows = getattr(out, "metrics", None) or []
        if not rows:
            continue
        head = f"\n[{agent}] metricas ya calculadas por el motor deterministico"
        verdict = getattr(out, "verdict", None)
        if verdict:
            head += f" -- veredicto de categoria: {verdict}"
        blocks.append(head)
        blocks.extend(_metric_line(r) for r in rows)
        flags = getattr(out, "mandatory_flags", None) or []
        if flags:
            blocks.append(f"  FLAGS OBLIGATORIAS: {', '.join(str(f) for f in flags)}")
    if not blocks:
        return ""
    return ("\nEstas cifras ya estan calculadas y validadas: NO las recalcules ni "
            "las contradigas. Usalas como la parte mecanica de cada regla y aporta "
            "solo el juicio cualitativo que falta.\n" + "\n".join(blocks))



# ============================================================================
# Filing excerpts: the narrative the judge cannot answer without
# ============================================================================
#
# The judge is asked for customer concentration, backlog/RPO, recurring
# revenue and the moat -- every one of which is disclosed only in the 10-K
# narrative and exists in no structured endpoint at any price. It was given
# the facts table and the FMP profile blurb, so it was being asked to judge a
# filing it had never seen.
#
# Regex extraction was tried first and rejected: the patterns matched NVDA
# ("sales to one direct customer represented 22% of total revenue") and
# silently mismatched Apple, whose only customer-percentage sentence is about
# *trade receivables*, not revenue -- a wrong number wearing the right shape,
# which is worse than a gap. So this hands the passages to the judge and lets
# it decide, with the text in hand to quote and an accession to cite.

#: Where each judged topic is disclosed. The needles are what filings
#: actually say, not what the metric is called.
_FILING_TOPICS: dict[str, tuple[str, ...]] = {
    "customer_concentration": (
        "of total revenue", "of net revenue", "of our revenue",
        "customer concentration", "major customer", "direct customer",
        # Medido sobre cuatro emisores: con las seis agujas de arriba, WMT y
        # BAC encajaban CERO y MSFT y AAPL una sola. El juez recibia pocos
        # pasajes o ninguno, contestaba INSUFFICIENT, y la metrica quedaba
        # MISSING en 10 de 12 tickers.
        #
        # Las seis primeras estan escritas para el lenguaje de una empresa
        # B2B. Lo que falta son las variantes que usa el resto:
        "of consolidated revenue", "of consolidated net", "of total sales",
        "of net sales", "of total net revenue", "of revenues",
        # La frase NEGATIVA, que es la mas comun y la mas valiosa: dice que no
        # hay concentracion, y eso es un hallazgo -- 7-10 puntos en las bandas
        # de DECISION_RULES.md -- no un dato ausente.
        "no single customer", "no customer accounted", "no one customer",
        "no customer represented", "10% or more of",
        # Bancos y aseguradoras lo llaman de otra forma.
        "concentration of credit risk", "significant concentration",
        # Y el otro lado de la cadena: quien depende de pocos PROVEEDORES
        # suele describir la simetria en la misma nota.
        "largest customer", "top ten customers", "top 10 customers",
    ),
    "backlog_rpo": (
        "remaining performance obligation", "backlog", "unfilled orders",
    ),
    "recurring_revenue": (
        "recurring revenue", "subscription revenue", "deferred revenue",
    ),
    "competition_moat": (
        "we compete", "our competitors", "barriers to entry",
    ),
}

#: Characters of context on each side of a hit -- wide enough to carry the
#: sentence and its subject, narrow enough to keep the bundle small.
_EXCERPT_WINDOW = 320

#: Caps. A 10-K runs to 1.4M characters (JPM); sending it whole would be
#: costly and would bury the relevant passages.
_MAX_EXCERPTS_PER_TOPIC = 3
_MAX_TOTAL_CHARS = 9000


def filing_excerpts(ticker: str, edgar: Any) -> dict[str, Any]:
    """Passages from the latest 10-K for the topics the judge must answer.

    Returns `{"accession", "filing_date", "excerpts": {topic: [passage]}}`,
    or `{}` when no 10-K is reachable. Never raises: an unreadable filing
    leaves the judge exactly as informed as it was before.
    """
    if edgar is None or not ticker:
        return {}
    try:
        cik = edgar.cik_for(ticker)
        if cik is None:
            return {}
        doc = edgar.latest_10k_text(cik)
    except Exception:  # a filing we cannot read must not fail the analysis
        logger.warning("10-K unavailable for %s; judge runs without filing text",
                       ticker, exc_info=True)
        return {}
    if not doc or not doc.get("text"):
        return {}

    text = re.sub(r"\s+", " ", doc["text"])
    lowered = text.lower()
    excerpts: dict[str, list[str]] = {}
    budget = _MAX_TOTAL_CHARS

    for topic, needles in _FILING_TOPICS.items():
        found: list[str] = []
        spans: list[tuple[int, int]] = []
        for needle in needles:
            start = 0
            while len(found) < _MAX_EXCERPTS_PER_TOPIC and budget > 0:
                i = lowered.find(needle, start)
                if i == -1:
                    break
                start = i + len(needle)
                lo, hi = max(0, i - _EXCERPT_WINDOW), min(len(text), i + _EXCERPT_WINDOW)
                if any(lo < s_hi and s_lo < hi for s_lo, s_hi in spans):
                    continue  # already covered by an earlier hit
                passage = text[lo:hi].strip()
                spans.append((lo, hi))
                found.append(passage)
                budget -= len(passage)
            if len(found) >= _MAX_EXCERPTS_PER_TOPIC or budget <= 0:
                break
        if found:
            excerpts[topic] = found

    if not excerpts:
        return {}
    return {
        "accession": doc.get("accession"),
        "filing_date": doc.get("filing_date"),
        "excerpts": excerpts,
    }


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


def _excerpt_block(bundle: dict[str, Any]) -> str:
    """The filing passages, formatted for the prompt.

    Presented as *candidates to read*, not as answers: the excerpt search is
    keyword-based and returns near misses (Apple's only customer-percentage
    sentence is about trade receivables, and one hit here is a competition
    lawsuit). The judge is told to quote what it uses and to answer
    INSUFFICIENT when no passage actually supports the metric.
    """
    if not bundle:
        return ""
    lines = [
        f"\nPasajes del ultimo 10-K (accession {bundle.get('accession')}, "
        f"presentado {bundle.get('filing_date')}).",
        "Son CANDIDATOS localizados por palabra clave, no respuestas: varios no "
        "responderan la pregunta. Usa solo los que de verdad la respondan, CITA "
        "textualmente la frase que uses, y responde INSUFFICIENT si ninguno sirve.",
    ]
    for topic, passages in (bundle.get("excerpts") or {}).items():
        lines.append(f"\n[{topic}]")
        for passage in passages:
            lines.append(f"  ...{passage}...")
    return "\n".join(lines)


def answer_judgments(
    packet: Any,
    requests: list[JudgmentRequest],
    settings: Settings,
    client: Any = None,
    edgar: Any = None,
    outputs: Any = None,
) -> list[Judgment]:
    """Ask Claude to answer every open judgment request for one ticker.

    Returns [] (no crash) when there are no requests, no API key, or the
    `anthropic` SDK isn't installed. `client` is injectable for tests.
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

    ctx = _company_context(packet)
    # Customer concentration, backlog/RPO and recurring revenue are disclosed
    # only in the 10-K narrative. Without it the judge was being asked to
    # judge a filing it had never seen.
    ticker = getattr(getattr(packet, "security", None), "ticker", None)
    filings = _excerpt_block(filing_excerpts(ticker, edgar)) if edgar is not None else ""
    computed = _specialist_metrics_block(outputs, requests)
    q_lines = [
        f"- request_id={r.request_id} | metric={r.metric_id} | schema_hint={r.schema_hint}\n"
        f"    pregunta: {r.question}"
        for r in requests
    ]
    user = (
        f"Datos de la empresa:\n{ctx}\n{computed}\n{filings}\n\n"
        f"Responde CADA una de estas {len(requests)} preguntas cualitativas. "
        f"Devuelve un answer por request_id, con evidence_class, source y rationale.\n\n"
        + "\n".join(q_lines)
    )

    resp = client.messages.parse(
        model=settings.judge_model,
        # Opus 5 razona por defecto y ese razonamiento COMPARTE el presupuesto
        # de max_tokens con la respuesta: con 4096 la respuesta salía truncada.
        max_tokens=8192,
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
