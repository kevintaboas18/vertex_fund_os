# Valuation Analysis Agent - Decision Rules


## Model-selection matrix

| Company type | Primary models | Secondary checks | Models normally avoided |
|---|---|---|---|
| Mature non-financial | FCFF DCF, economic profit | DDM, relative multiples | None if inputs valid |
| Stable leverage / equity cash flow | FCFE | FCFF, justified P/E | APV unless leverage changes |
| Changing leverage / LBO-like | APV, FCFF | FCFE scenario | Static WACC without debt path |
| Bank / insurer | Residual income, excess return, DDM | P/B vs ROE | Enterprise DCF, EV/EBITDA |
| Multi-segment conglomerate | SOTP | Consolidated DCF | One blended multiple without segment logic |
| REIT | NAV, AFFO DCF, cap rates | P/AFFO, dividend model | Standard EPS/FCFF without adapter |
| Pre-revenue biotech | Risk-adjusted NPV / decision tree | Cash runway, real options | P/E, ordinary terminal-value DCF |
| Cyclical / commodity | Mid-cycle DCF, NAV, replacement value | Cycle-normalized multiples | Peak-earnings multiple |

## DCF consistency rules

1. Forecast revenue, margins, reinvestment, and ROIC together.
2. Growth requires reinvestment: `g = Reinvestment rate * ROIC`.
3. Terminal growth must be below WACC/cost of equity and consistent with long-run nominal economic growth in the valuation currency.
4. Terminal ROIC and reinvestment must support terminal growth.
5. Stable margins must match competitive economics; no unexplained terminal jump.
6. Terminal-value share above 75% triggers `HIGH_TERMINAL_SENSITIVITY`.
7. Reconcile FCFF DCF and economic-profit value; material differences mean inconsistent inputs.
8. Use fully diluted shares and all debt-like claims.

## WACC rules

- Risk-free rate, ERP, cash flows, and terminal growth use the same currency.
- Use bottom-up beta from comparable operating businesses where possible.
- Include leases as debt when operating earnings are adjusted for leases.
- Use market-value weights.
- When debt yield is unavailable, use a disclosed synthetic rating from interest coverage.
- Do not add arbitrary premiums without a separately identified risk exposure that is not already in cash flows.

## Scenario framework

At minimum use Bear, Base, and Bull. Each scenario separately defines:

- revenue growth path;
- operating margin path;
- tax rate;
- capital intensity and working capital;
- ROIC and reinvestment;
- discount rate;
- terminal growth;
- share dilution;
- scenario probability.

Probabilities sum to 100%. The main report shows each value and the weighted value; it does not show only the average.

## Reverse DCF

Solve the current price for at least two of:

- required revenue CAGR;
- terminal operating margin;
- high-growth duration;
- terminal ROIC;
- required market share.

Compare implied values to historical performance, market capacity, consensus, and fundamental growth capacity.

## Valuation verdict

- 8.0-10.0: Attractive across scenarios/returns with a defensible margin of safety.
- 6.0-7.99: Fair to moderately attractive.
- 4.0-5.99: Full valuation; outcomes depend on optimistic execution.
- 0-3.99: Expensive or not supported by cash-flow/return assumptions.

Cheapness alone is never a purchase signal. Weak Business, Financial, and Market scores may indicate a value trap.
