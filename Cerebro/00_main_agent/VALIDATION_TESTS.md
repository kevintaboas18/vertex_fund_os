# Main-Agent Validation Tests

| Test ID | Test | Expected result |
|---|---|---|
| MAIN-001 | Category maxima sum to 100 | Pass exactly |
| MAIN-002 | Recalculate example 16+10.5+18+16+9+7 | 76.5 |
| MAIN-003 | Risk=4, total=90 | Profile capped at Speculative |
| MAIN-004 | Valuation=3, Technical=7 | Wait/Avoid breakdown override |
| MAIN-005 | ROIC<WACC, total=92 | No Elite/Quality label |
| MAIN-006 | Interest coverage=1.2x | Solvency warning always present |
| MAIN-007 | Financial coverage=0.65 | No profile gate may pass |
| MAIN-008 | Support and fair-value band overlap within tolerance | Confluence flag, not averaged value |
| MAIN-009 | Missing share count | Suppress per-share valuation |
| MAIN-010 | Same metric has material source conflict | Mark conflicted and rerun affected agents |
