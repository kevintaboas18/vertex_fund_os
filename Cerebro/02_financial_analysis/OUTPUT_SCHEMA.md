# Financial Analysis Agent - Output Schema

```yaml
agent_id: financial_analysis
version: 2.0.0
status: COMPLETE
security: {ticker: null, exchange: null, currency: null}
knowledge_timestamp: null
category:
  max_points: 15
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
core_27_metrics:
  valid_count: null
  points: null
  maximum_valid_points: null
  percent: null
  rows: []
profitability_and_cash: {}
balance_and_maturities: {}
return_on_capital: {}
dilution_and_sbc: {}
mandatory_overrides: []
strongest_metric: null
weakest_metric: null
```
