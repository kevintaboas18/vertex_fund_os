"""Shared fixtures for wbj.packet tests.

Fake provider objects and the synthetic-OHLCV generator live in
`engine/tests/fixtures/packet/make_packet_fixture.py` (the same script
that builds the committed golden `NVDA_packet.json`) so tests and the
fixture-generation script share one implementation. See that module's
docstring for why fakes rather than MockTransport are used at this layer.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

_FIXTURES_PACKET_DIR = Path(__file__).parent.parent / "fixtures" / "packet"
if str(_FIXTURES_PACKET_DIR) not in sys.path:
    sys.path.insert(0, str(_FIXTURES_PACKET_DIR))

from make_packet_fixture import (  # noqa: E402
    FIXED_NOW,
    FakeEdgarProvider,
    FakeFinnhubProvider,
    FakeFMPProvider,
    FakeFredProvider,
    generate_ohlcv_sessions,
    make_default_providers,
)
from wbj.packet.builder import Providers  # noqa: E402

__all__ = [
    "FIXED_NOW",
    "FakeEdgarProvider",
    "FakeFinnhubProvider",
    "FakeFMPProvider",
    "FakeFredProvider",
    "generate_ohlcv_sessions",
]


@pytest.fixture
def fixed_now():
    return FIXED_NOW


@pytest.fixture
def full_ohlcv(fixed_now):
    """>=252-session OHLCV history ending the day before `fixed_now`."""
    return generate_ohlcv_sessions(end=(fixed_now - timedelta(days=1)).date())


@pytest.fixture
def fake_providers(fixed_now):
    """The full, coherent NVDA scenario (same one the golden fixture uses)."""
    return make_default_providers(fixed_now)
