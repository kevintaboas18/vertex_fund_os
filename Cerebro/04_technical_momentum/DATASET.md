# Technical and Momentum Analysis Agent - Dataset Contract

## Required fields

| Field | Definition | Type | Unit | Minimum history | Preferred source | Requirement |
| --- | --- | --- | --- | --- | --- | --- |
| adjusted_daily_ohlcv | Split/dividend-adjusted open, high, low, close, volume | time series | price/shares | 756 sessions preferred; 252 minimum | validated market-data feed | required |
| adjusted_weekly_ohlcv | Weekly bars aggregated from adjusted daily data | time series | price/shares | 156 weeks preferred | derived from daily | required |
| broad_benchmark_ohlcv | Adjusted benchmark history aligned to stock dates | time series | price | 756 sessions | market-data feed | required |
| sector_benchmark_ohlcv | Adjusted sector/industry benchmark history | time series | price | 756 sessions | market-data feed | required |
| sector_constituent_history | Point-in-time constituent prices for breadth | panel | price | 252 sessions | point-in-time index data | conditional |
| earnings_event_dates | Timestamped earnings release dates and session mapping | event series | datetime | 8 events | issuer/exchange calendar | required |
| corporate_actions | Splits, dividends, spinoffs, symbol changes | event series | mixed | full price history | exchange/issuer | required |
| float_and_liquidity | Free float, median dollar volume, bid-ask proxy | series/snapshot | shares/currency/percent | 63 sessions | market data / filings | required |

## Dataset rules

- All time series must be sorted ascending and free of duplicate timestamps.
- Currency, unit scale, fiscal period, and source locator are mandatory.
- Reported values and estimates must remain separate.
- Minimum history is a requirement for a score, not a suggestion.
- Use `shared/MISSING_DATA_POLICY.md` for unavailable fields.
