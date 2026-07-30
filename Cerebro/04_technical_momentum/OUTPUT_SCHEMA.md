# Technical and Momentum Analysis Agent - Output Schema

```yaml
agent_id: technical_momentum
version: 2.0.0
status: COMPLETE
security: {ticker: null, exchange: null, currency: null}
knowledge_timestamp: null
category:
  max_points: 20
  awarded_points: null
  score_10: null
  confidence: null
coverage: null
dimensions: []
metrics: []
mandatory_flags: []
assumptions: []
source_lineage: []
validation_tests: {passed: 0, failed: 0, warnings: 0}
```


## Additional required fields

```yaml
market_state:
  trend: null
  relative_strength: null
  demand: null
  volatility: null
indicators:
  sma20: null
  sma50: null
  sma100: null
  sma200: null
  atr14: null
  rsi14: null
  adx14: null
  macd: {}
important_levels:
  support: []
  resistance: []
  moving_averages: []
  anchored_vwaps: []
  earnings_gaps: []
  volume_profile: []
breakouts_and_failures: []
```

Each support/resistance item must include:

```yaml
zone_id: null
type: support|resistance
lower: null
center: null
upper: null
timeframe: daily|weekly|combined
status: candidate|confirmed|strong|broken|role_reversed
strength_0_100: null
touches:
  - date: null
    pivot_price: null
    rejection_atr: null
    volume_ratio: null
distance_percent: null
distance_atr: null
confirmation_rule: null
invalidation_rule: null
```
