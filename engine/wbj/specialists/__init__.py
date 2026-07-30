"""Cerebro specialist agents (Tasks 14-19): financial, business, market,
technical, risk, valuation.

`wbj.specialists.common` holds the shared `SpecialistOutput` envelope every
specialist's `run()` returns; each specialist module (`financial.py`, ...)
implements one category's formulas, scoring, and Cerebro-specific
extension fields on top of it.
"""

from __future__ import annotations
