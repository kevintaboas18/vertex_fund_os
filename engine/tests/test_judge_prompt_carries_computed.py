"""The judge must see the numbers its questions refer to.

`moat_classification` asks for a Wide/Narrow/None call "per DECISION_RULES.md's
wide-moat gate", noting that spread persistence, margin stability and
concentration "are computed mechanically above". There was no above: the
prompt carried the packet's five common facts (revenue, diluted shares, cash,
debt, price) and nothing else, so a gate whose conditions 1, 2 and 4 are pure
arithmetic was being judged without any of those three inputs in view.

These tests assert on the prompt the client receives.
"""

from types import SimpleNamespace

from wbj.config import Settings
from wbj.core.nullstates import EvidenceClass, NullState
from wbj.judge import answer_judgments
from wbj.specialists.common import JudgmentRequest

from tests.test_judge import _FakeClient


def _row(metric_id, value=None, state=None, unit="", ec=EvidenceClass.C):
    return SimpleNamespace(metric_id=metric_id, value=value, state=state,
                           unit=unit, evidence_class=ec if value is not None else None)


def _out(agent_id, rows, *, verdict=None, flags=()):
    return SimpleNamespace(agent_id=agent_id, metrics=rows, verdict=verdict,
                           mandatory_flags=list(flags))


def _req(agent="business_analysis", metric="moat_classification"):
    return JudgmentRequest(request_id=f"{agent}:{metric}", agent_id=agent,
                           metric_id=metric, question="q?", schema_hint="one of Wide|Narrow|None")


def _prompt(client):
    return client.last_call["messages"][0]["content"]


def _ask(outputs, requests=None, client=None):
    client = client or _FakeClient([])
    answer_judgments(SimpleNamespace(security=SimpleNamespace(ticker="AAPL")),
                     requests or [_req()], Settings(anthropic_api_key="k"),
                     client=client, outputs=outputs)
    return _prompt(client)


def test_gate_inputs_reach_the_prompt():
    """The three arithmetic conditions of the wide-moat gate."""
    prompt = _ask([_out("business_analysis", [
        _row("BUS-SPREAD-014", 0.312, unit="pct"),
        _row("BUS-RANGE-010", 0.024, unit="pct"),
        _row("BUS-CONC-003", 0.11, unit="pct"),
    ])])
    for metric_id in ("BUS-SPREAD-014", "BUS-RANGE-010", "BUS-CONC-003"):
        assert metric_id in prompt, f"{metric_id} missing from the judge's context"


def test_mandatory_flags_reach_the_prompt():
    """DECISION_RULES.md states gate condition 4 as a flag, not a number."""
    prompt = _ask([_out("business_analysis", [_row("BUS-ROIC-013", 0.74)],
                        flags=["CONCENTRATION_RED_FLAG"])])
    assert "CONCENTRATION_RED_FLAG" in prompt


def test_null_rows_are_shown_so_insufficient_is_justifiable():
    """A hidden MISSING would leave the judge guessing that a number exists."""
    prompt = _ask([_out("business_analysis",
                        [_row("BUS-CONC-003", state=NullState.MISSING)])])
    assert "BUS-CONC-003" in prompt
    assert "MISSING" in prompt


def test_evidence_class_travels_with_each_number():
    prompt = _ask([_out("business_analysis", [_row("BUS-ROIC-013", 0.74, ec=EvidenceClass.C)])])
    assert "[C]" in prompt


def test_only_the_asking_agents_metrics_are_sent():
    """Scope is the specialist that asked; another agent's rows are noise."""
    prompt = _ask([
        _out("business_analysis", [_row("BUS-ROIC-013", 0.74)]),
        _out("technical_momentum", [_row("TECH-RSC-013", 1.2)]),
    ])
    assert "BUS-ROIC-013" in prompt
    assert "TECH-RSC-013" not in prompt


def test_judge_is_told_not_to_recompute():
    prompt = _ask([_out("business_analysis", [_row("BUS-ROIC-013", 0.74)])])
    assert "NO las recalcules" in prompt


def test_no_outputs_still_produces_a_usable_prompt():
    """Degrades to the old behaviour, never to a crash."""
    prompt = _ask(None)
    assert "Datos de la empresa:" in prompt


def test_dict_packet_still_names_the_company():
    """The MVP path is a plain dict, where `getattr` finds no `security`."""
    client = _FakeClient([])
    answer_judgments({"ticker": "MSFT"}, [_req()], Settings(anthropic_api_key="k"),
                     client=client)
    assert "MSFT" in _prompt(client)


def test_facts_table_is_not_truncated():
    """An arbitrary slice could only ever drop a validated fact silently."""
    facts = {f"fact_{i}": SimpleNamespace(value=float(i)) for i in range(12)}
    client = _FakeClient([])
    answer_judgments(SimpleNamespace(security=SimpleNamespace(ticker="AAPL"),
                                     facts_table=facts),
                     [_req()], Settings(anthropic_api_key="k"), client=client)
    prompt = _prompt(client)
    assert "fact_11" in prompt
