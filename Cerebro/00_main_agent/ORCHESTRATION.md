# Orchestration Workflow

## Phase 0 - Freeze the analysis clock

Set `knowledge_timestamp`, `market_timestamp`, reporting currency, security type, and selected industry adapter.

## Phase 1 - Validate common data

Run source, data, normalization, missing-data, and industry-adapter checks. Create a common facts table so specialists use the same revenue, shares, cash, debt, and price.

## Phase 2 - Parallel specialist execution

Run the six agents independently. Each agent sees the common facts table plus its own dataset. It does not see the other specialists' provisional scores.

## Phase 3 - Freeze packets

Hash each specialist packet. Any later correction creates a new packet version and invalidates the prior main-agent calculation.

## Phase 4 - Recompute raw score

```text
Raw total = Business points + Financial points + Market points
          + Technical points + Risk points + Valuation points
```

## Phase 5 - Apply mandatory overrides

Apply solvency, value-creation, capital-dependence, incomplete-data, risk, and breakdown rules before profile labels.

## Phase 6 - Apply profile gates

Classify the mechanism of opportunity: Momentum, Quality, Value, Conditional, Speculative, or Avoid.

## Phase 7 - Synthesize important levels

Merge valuation references and technical zones. Never average a technical resistance with an intrinsic-value estimate. Display both and identify overlaps.

## Phase 8 - Publish

Produce:

- one-page executive summary;
- category scorecard;
- profile gate result;
- important-level table;
- valuation scenario table;
- three thesis killers;
- evidence and assumption appendix;
- formula and source audit.
