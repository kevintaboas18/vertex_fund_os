# Ruta 2030 Wall Street Agent System v2.0.0

A deterministic, audit-ready multi-agent equity-research system built around one orchestrator and six specialist agents:

1. Business Analysis - 20 points
2. Financial Analysis - 15 points
3. Market and Growth Analysis - 20 points
4. Technical and Momentum Analysis - 20 points
5. Risk and Resilience Analysis - 15 points
6. Valuation Analysis - 10 points

The system does not allow an agent to replace missing evidence with narrative confidence. Every conclusion must resolve to a reported value, a reproducible calculation, a disclosed model assumption, or `NOT_SCORABLE`.

## What changed in v2

- Added explicit formulas and data requirements for every scored dimension.
- Added formula IDs, units, time windows, source hierarchy, calculation dates, and audit trails.
- Added deterministic support/resistance-zone detection from repeated independent touches.
- Added breakout, failed-breakout, role-reversal, anchored-VWAP, gap, and volume-profile logic.
- Added FCFF, FCFE, APV, economic-profit, residual-income, DDM, SOTP, reverse-DCF, scenario, Monte Carlo, and justified-multiple valuation methods.
- Added industry adapters for banks, insurers, REITs, SaaS, biotech, commodities, cyclicals, and pre-profit companies.
- Added scoring gates, overrides, confidence, missing-data policy, validation tests, and final important-level synthesis.

## Non-negotiable operating rule

> No evidence, no number. No number, no score. No formula, no conclusion.

A qualitative statement may be included only as context and must never be converted into a score unless a rule in this repository explicitly defines the conversion.

## Repository map

```text
wall_street_agent_system_v2/
|-- 00_main_agent/
|-- 01_business_analysis/
|-- 02_financial_analysis/
|-- 03_market_analysis/
|-- 04_technical_momentum/
|-- 05_risk_analysis/
|-- 06_valuation_analysis/
|-- shared/
|-- special_sauces/
|-- examples/
|-- REFERENCES.md
|-- MANIFEST.md
`-- VERSION.md
```

Start with `QUICK_START.md`, then load `shared/DATA_POLICY.md`, `shared/SCORING_ENGINE.md`, and the selected agent's `PROMPT.md`.

## Output boundary

The system produces research classifications, valuation reference ranges, confirmation levels, invalidation levels, and risk warnings. It does not promise returns and does not convert any single technical or valuation level into an automatic buy/sell instruction.

Build date: 2026-07-14

## Included methodology PDF

The archive includes `special_sauces/Ruta2030_Valuation_Special_Sauces.pdf`, a 24-page companion covering normalization, ROIC/WACC, FCFF, FCFE, APV, economic profit, residual income, DDM, SOTP, reverse DCF, fundamental multiples, scenarios, Monte Carlo, dilution, distress, and deterministic important-price-level formulas.
