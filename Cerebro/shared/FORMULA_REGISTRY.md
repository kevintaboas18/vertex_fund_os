# Formula Registry Standard

Every formula must have:

- a stable formula ID;
- formula text and sign convention;
- required inputs and units;
- acceptable frequency and minimum history;
- security-type applicability;
- threshold or ranking direction;
- missing-data behavior;
- formula version;
- at least one validation test.

## Formula-result object

```yaml
formula_id: TECH-ATR-014
formula_version: 2026.1
inputs:
  high: series_ref
  low: series_ref
  prior_close: series_ref
parameters:
  lookback: 14
result:
  value: 3.24
  unit: USD_per_share
status: VALID
warnings: []
```

## Change control

A threshold or formula change is a model change, not a copy edit. It requires:

1. a version increment;
2. a reason;
3. before/after test cases;
4. an impact sample;
5. a backtest or calibration note for technical and probabilistic models.
