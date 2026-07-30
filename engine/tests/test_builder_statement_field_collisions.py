"""A cash-flow movement must not occupy a balance-sheet field's name.

FMP reuses two balance-sheet account names inside the cash-flow statement,
where they mean the period's *change* in that account rather than its closing
balance. `_merge_statement_period` merged the three statements by dict
unpacking, so the cash-flow value won both collisions:

    BALANCE  inventory = 5,718,000,000   <- the balance
    CASHFLOW inventory = 1,400,000,000   <- the movement
    PACKET   inventory = 1,400,000,000   <- the movement won

Both of Victor's formulas that read inventory require the balance:
`FIN-BS-018` (quick ratio = (current assets - inventory) / current
liabilities) and `FIN-DX-031` (cash-conversion cycle, whose DIO leg is
"use average balances"). A quick ratio computed off the movement instead of
the balance overstates liquidity -- a wrong number wearing the right shape.

`DATA_POLICY.md` names this directly: "Do not silently change sign
conventions, currency, tax treatment, or denominator definitions."

Both figures are kept, under distinct names, rather than one winning.
"""

from wbj.packet.builder import _merge_statement_period

_BALANCE = {"date": "2025-09-27", "inventory": 5_718_000_000,
            "accountsReceivables": 39_777_000_000,
            "totalCurrentAssets": 89_378_000_000,
            "totalCurrentLiabilities": 80_610_000_000}
_CASHFLOW = {"date": "2025-09-27", "inventory": 1_400_000_000,
             "accountsReceivables": -7_029_000_000,
             "operatingCashFlow": 120_000_000_000}
_INCOME = {"date": "2025-09-27", "revenue": 416_161_000_000}


def _merged():
    return _merge_statement_period(_INCOME, _BALANCE, _CASHFLOW)


def test_inventory_is_the_balance_not_the_movement():
    assert _merged()["inventory"] == 5_718_000_000


def test_the_movement_is_kept_under_its_own_name():
    """Renaming, not discarding: the cash-flow figure is real data too."""
    assert _merged()["inventoryChange"] == 1_400_000_000


def test_receivables_collision_also_resolves_to_the_balance():
    """Unmapped today, but the same trap: a negative movement in a field
    named like a balance is a defect waiting for its first consumer."""
    m = _merged()
    assert m["accountsReceivables"] == 39_777_000_000
    assert m["accountsReceivablesChange"] == -7_029_000_000


def test_non_colliding_cashflow_fields_still_merge():
    assert _merged()["operatingCashFlow"] == 120_000_000_000


def test_income_and_balance_fields_survive():
    m = _merged()
    assert m["revenue"] == 416_161_000_000
    assert m["total_current_assets"] == 89_378_000_000


def test_quick_ratio_uses_the_balance():
    """FIN-BS-018 = (current assets - inventory) / current liabilities.
    With the movement it read 1.09; with the balance it reads 1.04."""
    m = _merged()
    quick = (m["total_current_assets"] - m["inventory"]) / m["total_current_liabilities"]
    assert abs(quick - 1.0378) < 0.001


def test_absent_cashflow_row_does_not_crash():
    assert _merge_statement_period(_INCOME, _BALANCE, {})["inventory"] == 5_718_000_000
