# Missing Data Policy

## Decision tree

1. Is the metric applicable? If no, use `NOT_APPLICABLE` and invoke the industry adapter.
2. Is the source expected to report it? If yes but absent, use `MISSING`.
3. Can it be calculated from validated components? If yes, use `C` evidence.
4. Is a proxy explicitly registered? If yes, use it with a proxy flag and lower model-fit confidence.
5. Otherwise, use `NOT_SCORABLE`.

## Prohibited imputation

Do not impute:

- customer concentration;
- organic growth;
- market share;
- debt maturity amounts;
- earnings revisions;
- technical volume;
- beta;
- terminal growth;
- WACC;
- support/resistance touches.

## Category coverage

```text
coverage = valid_metric_weight / applicable_metric_weight
```

- `coverage >= 0.85`: complete;
- `0.70 <= coverage < 0.85`: usable with caveat;
- `coverage < 0.70`: incomplete and gate-ineligible.
