"""Tests for the company health dashboard (6 real specialists → health view)."""

import json
from pathlib import Path

import pytest

from wbj.dashboard import _health, build_dashboard
from wbj.schemas.packet import Packet

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def test_health_is_coverage_aware():
    # A low score with thin data must read "necesita datos", never "débil".
    assert _health(0.0, 0.10)[0] == "sin_datos"
    assert _health(9.0, 0.50)[0] == "sin_datos"   # good score but < 70% coverage
    # With enough coverage, the score decides.
    assert _health(8.0, 0.85)[0] == "saludable"
    assert _health(5.0, 0.85)[0] == "vigilar"
    assert _health(2.0, 0.85)[0] == "debil"


def test_dashboard_has_all_six_areas_in_order(packet):
    d = build_dashboard(packet)
    keys = [a["key"] for a in d["areas"]]
    assert keys == ["business", "financial", "market", "technical", "risk", "valuation"]


def test_dashboard_marks_thin_areas_as_needs_data_not_weak(packet):
    d = build_dashboard(packet)
    by = {a["key"]: a for a in d["areas"]}
    # On the thin fixture, financial has real data; several others don't.
    assert by["financial"]["health"] in ("saludable", "vigilar", "debil")
    assert by["financial"]["score10"] is not None
    # An area below the coverage floor shows no score and the needs-data label.
    thin = [a for a in d["areas"] if a["coverage"] < 0.70]
    assert thin, "expected at least one under-covered area on the fixture"
    for a in thin:
        assert a["health"] == "sin_datos"
        assert a["score10"] is None


def test_overall_health_only_uses_covered_areas(packet):
    d = build_dashboard(packet)
    assert 0 <= d["overall_health"] <= 10
    assert 1 <= d["areas_with_data"] <= 6
    assert d["areas_total"] == 6


def test_targets_passed_through(packet):
    targets = {"status": "ok", "price": 100.0, "scenarios": []}
    d = build_dashboard(packet, targets=targets)
    assert d["targets"] == targets
