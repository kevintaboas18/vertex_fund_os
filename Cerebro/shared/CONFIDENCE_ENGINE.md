# Confidence Engine

Confidence is a separate 0-100 value and never changes the economic score.

```text
Confidence = 0.30*coverage + 0.25*source_quality + 0.20*freshness
           + 0.15*consistency + 0.10*model_fit
```

Each component is 0-100.

## Components

- `coverage`: valid weighted inputs divided by required weighted inputs.
- `source_quality`: regulatory/official evidence scores higher than secondary estimates.
- `freshness`: decays after the staleness default.
- `consistency`: agreement across periods and reconciled sources.
- `model_fit`: suitability of the selected formula for the security type.

## Labels

| Confidence | Label | Main-agent action |
|---:|---|---|
| 85-100 | High | Normal use |
| 70-84 | Medium | Show principal caveat |
| 50-69 | Low | No high-conviction profile label |
| <50 | Insufficient | Category cannot pass a gate |

The final report displays both `score` and `confidence` because a precise-looking score without evidence quality is misleading.
