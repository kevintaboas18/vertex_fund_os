# Financial Analysis Agent - Decision Rules


## Core 27-metric diagnostic

Each of the 27 core formulas receives:

```text
BAD = 0 points
GOOD = 1 point
EXCELLENT = 2 points
Core financial percent = points / (2 * valid applicable metrics) * 100
Core financial score_10 = Core financial percent / 10
```

The five weighted dimensions remain the category architecture. The core diagnostic must reconcile directionally; a difference greater than 1.5 points between the weighted category score and the 27-metric score requires an explanation.

## Exact default bands

Use the bands in `FORMULAS.md`. Margin and leverage bands marked sector-sensitive require peer comparison. The reported number controls; management guidance is context only.

## Mandatory overrides

1. If normalized net income is negative, FCF is negative, and the company needs external financing, category verdict is capped at `Bad/Avoid`.
2. If ROIC < WACC, category cannot be `Excellent`.
3. If interest coverage <1.5x, issue `SOLVENCY_WARNING` regardless of total.
4. Negative equity makes debt/equity `NOT_MEANINGFUL`; use net debt/EBITDA, coverage, and adapter metrics.
5. Banks and insurers must use the financial-sector adapter; conventional FCF/ROIC scoring is not allowed.

## Financial verdict

- 8.0-10.0: Excellent financial health.
- 6.0-7.99: Good with limited weaknesses.
- 4.0-5.99: Mixed / watch.
- <4.0: Weak / high financial risk.
