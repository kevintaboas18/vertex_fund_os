# Technical and Momentum Analysis Agent - Validation Tests

| Test ID | Test | Expected result |
| --- | --- | --- |
| TECH-T001 | Constant price series | ATR=0; level-distance ratios guarded |
| TECH-T002 | High=12, low=10, prior close=11 | True range=2 |
| TECH-T003 | Two pivot highs in overlapping ATR zones, 20 sessions apart, each rejects >0.5 ATR | Confirmed resistance |
| TECH-T004 | Two highs only 2 sessions apart | Count as one swing touch |
| TECH-T005 | Three independent rejected highs | Strong resistance candidate subject to score |
| TECH-T006 | Close above zone+0.25ATR, volume ratio 1.6, two closes above | Confirmed breakout |
| TECH-T007 | Breakout close returns inside zone next day | Failed breakout |
| TECH-T008 | Former resistance retested and rejects upward >0.5ATR | Role-reversal support |
| TECH-T009 | Current close 100, resistance low 102, ATR 2 | Distance=1 ATR; nearby resistance |
| TECH-T010 | Price history not split adjusted | Reject technical packet |
| TECH-T011 | Pivot k=3 at historical date | Pivot availability delayed 3 sessions |
| TECH-T012 | Volume missing | Volume dimension capped; breakout cannot confirm |

Every release must pass these tests and the shared QA checklist. Tests using a proxy must state the proxy explicitly.
