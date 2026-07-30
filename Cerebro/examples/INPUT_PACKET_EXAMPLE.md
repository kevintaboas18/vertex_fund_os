# Example Input Packet - EXMPL

```yaml
security:
  ticker: EXMPL
  exchange: NASDAQ
  security_type: operating_company
  reporting_currency: USD
  valuation_currency: USD
analysis:
  knowledge_timestamp: 2026-02-14T21:00:00Z
  market_timestamp: 2026-02-13T21:00:00Z
  industry_adapter: default_nonfinancial
fundamentals:
  annual_history_years: 7
  quarterly_history_count: 12
market_data:
  daily_sessions: 756
  adjusted: true
  benchmark: SP500_TOTAL_RETURN
  sector_benchmark: EXAMPLE_SECTOR_TOTAL_RETURN
estimates:
  provider_snapshot_before_earnings: true
  active_analysts: 14
source_validation:
  unresolved_material_conflicts: []
```

This is a structural example only. It does not describe a real security.
