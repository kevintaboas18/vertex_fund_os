# Business Analysis Agent - Decision Rules


## Wide-moat gate

A business may be labeled `Wide moat` only when all applicable conditions pass:

1. ROIC exceeds WACC by at least 5 percentage points in at least four of the last five fiscal years, or an approved financial-sector adapter shows equivalent excess returns.
2. Five-year operating-margin range is no more than 5 percentage points, or peer-relative resilience is in the top quartile through a cycle.
3. At least two independent moat effects are quantitatively visible: retention/switching costs, cost advantage, network scale, regulated/intangible protection, or efficient scale.
4. No unresolved customer/product concentration threat invalidates durability.

## Business verdict

| Category score | Label | Additional condition |
|---:|---|---|
| 8.0-10.0 | Excellent business | ROIC >=20% or top-decile adapter return, positive spread, FCF conversion >=0.9x, moat gate passes |
| 6.0-7.99 | Good business | ROIC generally 15-20% or positive spread with reasonable stability |
| 4.0-5.99 | Mediocre / mixed | ROIC 8-15%, small/volatile spread, uncertain moat |
| <4.0 | Weak business | ROIC below WACC, shrinking economics, or no measurable advantage |

## Mandatory flags

- `VALUE_DESTRUCTION` when normalized ROIC < WACC.
- `CONCENTRATION_RED_FLAG` when one customer/product exceeds 30% of revenue.
- `DILUTION_RED_FLAG` when diluted shares grow >5% CAGR for three years without commensurate per-share value creation.
- Always list three business-model or execution risks that could destroy the thesis.
