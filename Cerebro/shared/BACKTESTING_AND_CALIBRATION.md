# Backtesting and Calibration

## Required for technical thresholds

Test the level engine on rolling, point-in-time data with corporate-action-adjusted OHLCV.

Measure:

- breakout success after 5, 10, and 20 sessions;
- false-breakout rate;
- support/resistance hold rate;
- average adverse and favorable excursion in ATR units;
- performance by liquidity, volatility, sector, and market regime;
- sensitivity to pivot window, ATR tolerance, recency half-life, and touch separation.

## Required for valuation

Track:

- forecast error by line item;
- terminal-value share of enterprise value;
- base/bear/bull calibration;
- current-price implied assumptions versus subsequent realized results;
- dispersion among DCF, economic-profit, residual-income, and relative models;
- effect of share dilution and restatements.

## Anti-bias rules

- no survivorship bias;
- no look-ahead data;
- filing acceptance date controls availability;
- delisted securities remain in the sample;
- all parameter changes are evaluated out of sample;
- do not optimize thresholds on one ticker and call them universal.
