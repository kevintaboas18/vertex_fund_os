"""`Judgment` — the answer shape for a Task 20 judgment-overlay round trip.

Sources of truth: task-20-brief.md's interface note:
`Judgment{request_id, answer: float | str | dict, evidence_class, source,
rationale}`, paired with `wbj.specialists.common.JudgmentRequest`
(`{request_id, agent_id, metric_id, question, schema_hint}`) — a
`JudgmentRequest` is the specialist's *question*, a `Judgment` is a Claude
sub-agent's *answer* to that question, and `wbj.overlay.merge.merge_overlay`
is what reconciles the two back into a rescored `SpecialistOutput`.

`evidence_class` and `source` are `Optional` here (not required, unlike
`wbj.core.nullstates.Value`'s own fields) *by design*: a malformed or
incomplete answer must still be constructible as a `Judgment` so
`merge_overlay` can reject it with a clear reason at merge time (per the
task brief: "Judgment missing evidence_class/source -> rejected"), rather
than blowing up at parse time on whatever a sub-agent handed back.

`answer` intentionally stays exactly the three JSON-ish shapes the brief
names (`float | str | dict`) rather than adding a bare `list` alternative:
a `schema_hint` like market.py's "array of {event, impact,
probability_assumption}" is answered as `{"items": [...]}` under the
`dict` branch instead of a bare list, so every `Judgment.answer` is valid
top-level JSON when serialized standalone and `merge.py`'s schema-hint
validation only has three shapes to switch on, not four.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from wbj.core.nullstates import EvidenceClass

Answer = float | str | dict[str, Any]


class Judgment(BaseModel):
    """A Claude sub-agent's answer to one `wbj.specialists.common.JudgmentRequest`.

    `request_id` links back to the `JudgmentRequest` being answered (across
    the 6 specialists' `judgment_requests` lists, gathered by
    `wbj.overlay.merge.collect_requests`). `rationale` defaults to `""`
    rather than being required: it is included in the audit trail whenever
    given, but its absence alone should not sink an otherwise well-formed
    judgment the way a missing `evidence_class`/`source` does (per
    DATA_POLICY.md, every number needs provenance; a one-line "why" is
    good practice but not the same load-bearing requirement).
    """

    model_config = ConfigDict(frozen=True)

    request_id: str
    answer: Answer
    evidence_class: EvidenceClass | None = None
    source: str | None = None
    rationale: str = ""
