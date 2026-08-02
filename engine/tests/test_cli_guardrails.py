"""CLI guardrails: a refused packet is an answer, not a crash.

ORCHESTRATION.md's Phase 0/1 rejects a packet that lacks a currency, a
timestamp, a diluted share count or 252 sessions. That is the guardrail
working. Letting `PacketRejected` escape printed a full traceback, which
reads as a broken tool -- the webapp already drew this distinction in
`_fail`; the CLI did not.
"""

from __future__ import annotations

import pytest
import typer

from wbj.cli import _run_pipeline
from wbj.packet.builder import PacketRejected


def test_a_refused_packet_exits_cleanly_without_a_traceback(capsys):
    def boom(*_a, **_k):
        raise PacketRejected("packet rejected for ZZZZ: missing currency")

    with pytest.raises(typer.Exit) as exc:
        _run_pipeline(boom, "ZZZZ")
    assert exc.value.exit_code == 1

    out = capsys.readouterr().out
    assert "No analysis:" in out
    assert "missing currency" in out
    assert "Nothing was written." in out
    assert "Traceback" not in out


def test_a_real_bug_is_not_disguised_as_a_data_problem():
    """Only the documented rejection is caught. A genuine fault must still
    surface -- relabelling it would hide it."""
    def boom(*_a, **_k):
        raise ZeroDivisionError("a real bug")

    with pytest.raises(ZeroDivisionError):
        _run_pipeline(boom, "AAPL")


def test_a_successful_call_passes_its_result_through():
    assert _run_pipeline(lambda t, x=None: {"ticker": t, "x": x}, "AAPL", x=1) == {
        "ticker": "AAPL", "x": 1}


def test_the_mvp_pipeline_says_it_is_not_the_cerebro_profile(tmp_path, monkeypatch,
                                                             capsys):
    """`scorecard` says "quick" and `aggregate` says "Cerebro profile";
    `analyze` said neither, so its numbers read as the strict methodology's.
    They carry no gates, overrides or coverage rules.

    The label is only worth anything if the user sees it, so drive the
    command with the fetch/compute stages stubbed and read stdout.
    """
    from wbj import cli, memoria, targets
    from wbj.config import Settings

    packet = {"entity": "Test Co", "ticker": "T", "fiscal_year_end": "2025-12-31"}
    result = {
        **packet,
        "metrics": {"revenue_usd": 1000.0, "revenue_yoy": 0.1, "net_margin": 0.15,
                    "fcf_margin": 0.2, "debt_to_equity": 0.3},
        "scores": {"dimensions": {"moat": 7.0},
                   "category": {"name": "business", "points": 15.0,
                                "max_points": 20.0, "score10": 7.5,
                                "coverage": 0.9}},
        "scorecard": {},
    }
    monkeypatch.setattr(cli, "_providers",
                        lambda: (Settings(reports_dir=tmp_path), None, None))
    monkeypatch.setattr(cli, "_build_packet", lambda _t: packet)
    monkeypatch.setattr(cli, "_compute", lambda _p: result)
    monkeypatch.setattr(targets, "live_price", lambda *_a, **_k: None)
    monkeypatch.setattr(targets, "price_targets", lambda *_a, **_k: {})
    monkeypatch.setattr(memoria, "save_prediction", lambda *_a, **_k: False)

    cli.analyze("T")

    out = capsys.readouterr().out
    assert "quick MVP" in out
    assert "wbj report" in out        # points at the strict path
