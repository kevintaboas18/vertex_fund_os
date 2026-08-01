"""A degraded run has to say WHY it is degraded, and has to keep retrying.

The qualitative judge answers the questions the deterministic engine
refuses to guess at — moat classification, catalysts, thesis killers, TAM
tier. When it cannot run, whole dimensions stay NOT_SCORABLE. On NVDA
that cost Market 13 of its 20 points and dropped its coverage to 0.49,
which tripped OVERRIDE_6 and forced the verdict down to "Avoid / Wait".

Two things made that unreadable:

  1. Every failure was reported as "no ANTHROPIC_API_KEY", including the
     case where the key is present and working and the ACCOUNT is out of
     credit. The reader was sent to check the one thing that was fine.
  2. The failed run cached its analyst-only answers under the full
     question list, so every later run inside the TTL matched the cache,
     skipped the judge and reproduced the degraded scores in silence.
"""

from __future__ import annotations

import pytest

from wbj.report import _llm_failure_reason


class _Credit(Exception):
    pass


# --- the reason has to name the thing the reader must go fix --------------


@pytest.mark.parametrize("message,expected", [
    ("Error code: 400 - Your credit balance is too low to access the "
     "Anthropic API. Please go to Plans & Billing", "out of credit"),
    ("model: claude-nope-9 not_found", "valid model"),
    ("authentication_error: invalid x-api-key", "rejected"),
    ("Error code: 429 - rate limit exceeded", "rate limit"),
])
def test_each_failure_points_somewhere_different(message, expected):
    _, english = _llm_failure_reason(_Credit(message))
    assert expected in english


def test_a_spent_balance_is_not_reported_as_a_missing_key():
    """The exact regression: the key works, the account does not."""
    spanish, english = _llm_failure_reason(
        _Credit("Your credit balance is too low to access the Anthropic API"))
    assert "ANTHROPIC_API_KEY" not in english
    assert "ANTHROPIC_API_KEY" not in spanish
    assert "credit" in english and "saldo" in spanish
    # And it says so explicitly, because the natural next move is to go
    # replace a key that is already correct.
    assert "key itself works" in english


def test_a_missing_sdk_says_so():
    assert "SDK" in _llm_failure_reason(ImportError("no module named anthropic"))[1]


def test_an_unrecognised_failure_still_names_its_type():
    """Better a class name than a wrong diagnosis."""
    _, english = _llm_failure_reason(ValueError("something else entirely"))
    assert "ValueError" in english


# --- the thesis fallback carries that reason ------------------------------


def test_the_thesis_fallback_names_the_real_cause(monkeypatch):
    """All seven narrative fields used to read "no ANTHROPIC_API_KEY"
    regardless of what actually happened."""
    import anthropic

    from wbj.report import _executive_thesis

    class _Boom:
        def __init__(self, *a, **k):
            pass

        @property
        def messages(self):
            raise _Credit("Your credit balance is too low")

    monkeypatch.setattr(anthropic, "Anthropic", _Boom)
    settings = type("S", (), {"anthropic_api_key": "sk-ant-present",
                              "judge_model": "claude-opus-5"})()
    levels = type("L", (), {"levels": []})()
    profile = type("P", (), {"label": "Avoid / Wait", "raw_score": 45.0,
                             "total_confidence": 86.0, "failed_gates": [],
                             "overrides": []})()

    thesis = _executive_thesis("NVDA", profile, {}, levels, settings, "en")
    text = thesis.business_quality
    assert "out of credit" in text
    assert "ANTHROPIC_API_KEY" not in text


def test_a_genuinely_absent_key_still_says_the_key_is_absent():
    """The old message was not wrong, only over-applied."""
    from wbj.report import _executive_thesis

    settings = type("S", (), {"anthropic_api_key": None,
                              "judge_model": "claude-opus-5"})()
    levels = type("L", (), {"levels": []})()
    profile = type("P", (), {"label": "Avoid / Wait", "raw_score": 45.0,
                             "total_confidence": 86.0, "failed_gates": [],
                             "overrides": []})()

    thesis = _executive_thesis("NVDA", profile, {}, levels, settings, "en")
    assert "ANTHROPIC_API_KEY is not set" in thesis.business_quality


def test_the_spanish_fallback_is_spanish():
    from wbj.report import _executive_thesis

    settings = type("S", (), {"anthropic_api_key": None,
                              "judge_model": "claude-opus-5"})()
    levels = type("L", (), {"levels": []})()
    profile = type("P", (), {"label": "Evitar", "raw_score": 45.0,
                             "total_confidence": 86.0, "failed_gates": [],
                             "overrides": []})()

    thesis = _executive_thesis("NVDA", profile, {}, levels, settings, "es")
    assert thesis.business_quality.startswith("Narrativa no disponible")


# --- the report carries it too --------------------------------------------


# `data_gaps` reaching `missing_or_conflicted_data` is covered in
# `tests/aggregate/test_final_report.py`, where the report factory lives.
