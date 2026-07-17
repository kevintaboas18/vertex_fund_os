# Source Hierarchy

## Default order

1. Regulatory filing and filing acceptance metadata.
2. Audited annual report and official issuer filing exhibit.
3. Official exchange or issuer corporate-action record.
4. Official earnings release and investor presentation.
5. Reputable market-data feed with adjustment methodology.
6. Consensus-estimate provider with timestamped revisions.
7. Industry or government market-size source.
8. Secondary research used only when primary evidence is unavailable.

## Conflict resolution

When sources disagree:

1. Verify period, currency, scale, split adjustment, and continuing-operations treatment.
2. Prefer a later official restatement over the original filing.
3. Keep both values in the audit trail.
4. Record the selected value and reason.
5. If the difference remains material, mark the metric `CONFLICTED` and do not score it.

## Materiality defaults

A conflict is material when it changes:

- a category score by at least 0.5/10;
- a valuation midpoint by at least 5%;
- a support/resistance zone by at least 0.5 ATR;
- a mandatory gate or override.
