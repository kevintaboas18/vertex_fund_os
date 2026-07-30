"""One reading of the cash-tax-rate rules, shared by every specialist.

CALCULATION_CONVENTIONS.md gives three rules for the tax rate feeding
NOPAT, and four modules each implemented them separately — identically
wrong:

    Use normalized cash tax rate for valuation and NOPAT.
    Use statutory rate only when normalized cash tax data are unavailable
    and disclose the substitution.
    Clamp normalized tax rate to a defensible range only through an
    explicit industry/country adapter; never silently.

Every copy read `min(max(tax_expense / pretax, 0.0), 1.0)`, which is the
silent clamp the third rule forbids. It is not a rare edge: across ten
companies checked it fires on fifteen fiscal years, wherever a
valuation-allowance release or an R&D credit produced a negative
effective rate (NVIDIA, Exxon, Pfizer, Intel, Netflix) or a one-time
charge pushed it above 100% (Salesforce, 173.8% in FY2016).

An effective rate outside [0, 1] is not a *normalized* cash tax rate — it
is one distorted by the single items NORMALIZATION_AND_RESTATEMENTS.md
exists to strip. So it is reported as unavailable, and the caller takes
the documented path: substitute the statutory rate and say so.
"""

from typing import Any

#: US federal statutory rate, used only as the disclosed substitute.
STATUTORY_TAX_RATE = 0.21


def reported_tax_rate(pretax: Any, tax_expense: Any) -> float | None:
    """The effective cash tax rate from a filing, or None when the
    filing does not yield a usable normalized one.

    Returns None rather than a bent value, so a caller cannot mistake a
    substitution for a reported figure.
    """
    if not isinstance(pretax, (int, float)) or not isinstance(tax_expense, (int, float)):
        return None
    if pretax <= 0:
        return None
    rate = tax_expense / pretax
    return rate if 0.0 <= rate <= 1.0 else None


def has_reported_tax_rate(pretax: Any, tax_expense: Any) -> bool:
    """Whether this period yields a usable normalized cash tax rate.

    Kept beside `reported_tax_rate` so a rate and the disclosure of its
    absence can never disagree about which periods were substituted.
    """
    return reported_tax_rate(pretax, tax_expense) is not None


def tax_rate_or_statutory(pretax: Any, tax_expense: Any,
                          fallback: float = STATUTORY_TAX_RATE) -> float:
    """The reported rate where one exists, else the statutory fallback.

    The caller is responsible for disclosing the substitution —
    `has_reported_tax_rate` tells it when one happened.
    """
    rate = reported_tax_rate(pretax, tax_expense)
    return fallback if rate is None else rate
