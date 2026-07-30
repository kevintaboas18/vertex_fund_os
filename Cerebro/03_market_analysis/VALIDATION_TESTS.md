# Market and Growth Analysis Agent - Validation Tests

| Test ID | Test | Expected result |
| --- | --- | --- |
| MKT-T001 | TAM 1000 to 1210 over 2 years | CAGR=10% |
| MKT-T002 | Company relevant revenue=50, TAM=1000 | Penetration=5% |
| MKT-T003 | Share 5.0% to 5.7% | Delta=+0.7 percentage points |
| MKT-T004 | 8 upward revisions out of 10 | Breadth=80% |
| MKT-T005 | Prob=.6, impact=100, evidence=.8, time factor=.5 | Expected impact index=24 |
| MKT-T006 | Forecast revenue exceeds TAM | Fail consistency gate |
| MKT-T007 | Only issuer TAM with no method | Source tier 4, score cap 6 |
| MKT-T008 | Consensus snapshot taken after release | Reject surprise calculation |

Every release must pass these tests and the shared QA checklist. Tests using a proxy must state the proxy explicitly.
