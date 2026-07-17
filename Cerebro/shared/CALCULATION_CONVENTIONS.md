# Calculation Conventions

## Denominators

- Use average balance-sheet values for return ratios when both beginning and ending values exist.
- If only ending values exist, label the result `END_BALANCE_PROXY` and reduce confidence.
- Never divide by a denominator whose absolute value is immaterial without marking the ratio `NOT_MEANINGFUL`.

## Growth rates

```text
YoY growth = (Current / Prior) - 1
CAGR(n years) = (Ending / Beginning)^(1/n) - 1
```

Do not calculate CAGR across a sign change or from a non-positive beginning value. Use absolute changes and an explicit non-comparable flag instead.

## Currency

Translate all valuation cash flows and discount rates into one currency. Use a risk-free rate and inflation/terminal-growth assumption in that same currency.

## Tax

- Use normalized cash tax rate for valuation and NOPAT.
- Use statutory rate only when normalized cash tax data are unavailable and disclose the substitution.
- Clamp normalized tax rate to a defensible range only through an explicit industry/country adapter; never silently.

## Annualization

Annualize volatility with `sqrt(252)` for daily data and document alternate calendars. Do not annualize a partial quarter as if seasonality were absent.

## Winsorization

Peer distributions may be winsorized at the 5th and 95th percentiles for robust ranking. The raw value must remain in the audit trail.
