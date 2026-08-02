"""One reading of INDUSTRY_ADAPTERS.md, shared by every specialist.

Four modules were each deciding on their own what a non-default adapter
meant, and all four decided it the same wrong way: `!= "default_nonfinancial"`.
That test treats "this adapter replaces my model" and "this adapter adds
metrics to my model" as the same thing, so a software company was told
its analysis barely fit and an oil major was refused a valuation the
document explicitly asks for. Each copy also carried a 90/40 model-fit
split with no middle.

The distinction the document actually draws:

    Banks     "Prefer excess-return or dividend-discount valuation"
    Insurers  "...and excess-return valuation"
    REITs     "Replace EPS with FFO/AFFO ... NAV, and cap-rate valuation"
    Biotech   "Use risk-adjusted NPV by program"
        -> a different primary model. Conventional formulas must not run.

    Commodities  "Normalize price/deck and margins through a cycle"
        -> same formulas, inputs that must be cycle-normalized first.

    SaaS  "Add ARR growth, NRR, GRR, churn, ..."
        -> same formulas, plus extra metrics. No caveat is warranted.

Keeping this in one place is the point: the next adapter added here is
picked up by every specialist at once, instead of needing four edits that
can silently disagree.
"""

DEFAULT_ADAPTER = "default_nonfinancial"

#: Adapters whose methodology names a *different* primary model. Running
#: the conventional formulas on these produces a number the document
#: forbids, so specialists decline or heavily caveat instead.
MODEL_REPLACING: frozenset[str] = frozenset({"banks", "insurers", "reits", "biotech"})

#: Adapters that keep the conventional model but require normalized
#: inputs. The analysis runs; it carries a caveat.
MODEL_NORMALIZING: frozenset[str] = frozenset({"commodities_cyclicals"})

#: Adapters that only add metrics. Nothing about the core formulas
#: changes, so no caveat and no confidence penalty.
MODEL_ADDITIVE: frozenset[str] = frozenset({DEFAULT_ADAPTER, "saas_subscriptions"})

_MODEL_FIT: dict[str, float] = {
    DEFAULT_ADAPTER: 90.0,
    "saas_subscriptions": 90.0,      # additive; core formulas unchanged
    "commodities_cyclicals": 65.0,   # same formulas, cycle-normalized inputs
    "biotech": 50.0,                 # margin/FCF quality often not meaningful
    "banks": 40.0,                   # ROIC replaced by ROE/ROTCE
    "insurers": 40.0,                # ROE/combined ratio/excess return
    "reits": 40.0,                   # EPS replaced by FFO/AFFO
}


def replaces_model(adapter: str | None) -> bool:
    """True when the adapter's methodology calls for a different primary
    model than the conventional non-financial formulas."""
    return (adapter or "") in MODEL_REPLACING


def normalizes_inputs(adapter: str | None) -> bool:
    """True when the model still applies but its inputs must be
    normalized (through a cycle) before the output can be trusted."""
    return (adapter or "") in MODEL_NORMALIZING


def is_classified(adapter: str | None) -> bool:
    """True when INDUSTRY_ADAPTERS.md places this adapter in one of its
    three treatments (replacing, normalizing, additive).

    A name in none of them is one nobody has checked the conventional
    formulas against. `business.py` already floors its model-fit
    confidence on exactly this test — "claiming a good fit for it would be
    an assertion without evidence" — and valuation uses it to refuse
    rather than price the company with formulas that may not apply.
    """
    return (adapter or "") in (MODEL_REPLACING | MODEL_NORMALIZING | MODEL_ADDITIVE)


def needs_caveat(adapter: str | None) -> bool:
    """True when a specialist running conventional formulas owes the
    reader a warning. Additive adapters do not."""
    return replaces_model(adapter) or normalizes_inputs(adapter)


def model_fit(adapter: str | None) -> float:
    """Model-fit component (0-100) of the confidence formula.

    An unknown adapter returns the floor: a name nobody has classified
    is one nobody has checked the formulas against, and claiming a good
    fit for it would be an assertion without evidence.
    """
    return _MODEL_FIT.get(adapter or "", 40.0)
