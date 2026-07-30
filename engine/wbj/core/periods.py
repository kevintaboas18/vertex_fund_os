"""One reading of a statement row's fiscal year, shared by every caller.

Three modules each decided this on their own and disagreed, the same way
four once disagreed about what a non-default industry adapter meant (see
`wbj.core.adapters`):

    business.py     `fiscalYear`   -- a field no builder emits, so every
                                      metric's `period` read "FY?" and the
                                      recession-year drawdown had no years
                                      to match.
    from_packet.py  `fiscalYear`   -- against *raw FMP* peer rows, which
                                      carry `calendarYear`. Every peer
                                      drawdown came back None, so
                                      DECISION_RULES.md's wide-moat
                                      condition-2 alternative
                                      ("peer-relative resilience is in the
                                      top quartile through a cycle") could
                                      never engage.
    financial.py    `calendarYear` -- correct for packet rows, and the
                                      reason the mismatch went unseen.

The shapes in play are the packet's canonical rows (FMP's `calendarYear`
passed through unmapped, plus `date`), raw FMP rows (`calendarYear`,
`date`), and hand-built rows in tests (`fiscalYear`). Reading all three
here means a caller cannot pick the wrong one, and the company and its
peers are ranked on the same quantity — which is what
`_recession_margin_drawdown`'s docstring already promised.
"""

from __future__ import annotations

from typing import Any

#: Checked in order: the explicit fiscal year where a row carries one,
#: then FMP's calendar year, which is what the builder passes through.
_YEAR_KEYS = ("fiscalYear", "calendarYear")


def fiscal_year(row: Any) -> str | None:
    """The fiscal year of one statement row as a digit string, or None.

    Falls back to the year of the period end date (`date`: "2025-01-26"),
    which every FMP statement row carries, so a row that names its year
    only there is still placed.
    """
    if not hasattr(row, "get"):
        return None
    for key in _YEAR_KEYS:
        value = row.get(key)
        if str(value or "").isdigit():
            return str(value)
    date = str(row.get("date") or "")
    return date[:4] if date[:4].isdigit() else None
