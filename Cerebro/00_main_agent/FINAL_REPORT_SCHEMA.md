# Final Report Schema

```yaml
report_version: 2.0.0
security:
  ticker: null
  exchange: null
  currency: null
  analysis_timestamp: null
  knowledge_timestamp: null
profile:
  label: null
  raw_score: null
  total_confidence: null
  passed_gates: []
  failed_gates: []
  overrides: []
category_scorecard:
  business: {points: null, max: 20, confidence: null}
  financial: {points: null, max: 15, confidence: null}
  market: {points: null, max: 20, confidence: null}
  technical: {points: null, max: 20, confidence: null}
  risk: {points: null, max: 15, confidence: null}
  valuation: {points: null, max: 10, confidence: null}
executive_thesis:
  business_quality: null
  growth_engine: null
  market_validation: null
  valuation_message: null
  primary_risk: null
important_levels: []
valuation_scenarios: []
reverse_dcf: {}
thesis_killers: []
monitoring_triggers: []
missing_or_conflicted_data: []
audit:
  packet_hashes: {}
  formula_versions: []
  validation_summary: {}
```

## Required executive-summary sentences

1. What the company economically does.
2. Why value creation is or is not durable.
3. What is funding growth.
4. Whether the market currently validates the thesis.
5. What assumptions the current price appears to require.
6. The nearest material support/resistance and intrinsic-value references.
7. The single most important invalidation risk.
