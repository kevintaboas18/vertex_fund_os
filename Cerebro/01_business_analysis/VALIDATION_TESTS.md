# Business Analysis Agent - Validation Tests

| Test ID | Test | Expected result |
| --- | --- | --- |
| BUS-T001 | EBIT=100, tax=25%, invested capital=500 | NOPAT=75; ROIC=15% |
| BUS-T002 | ROIC=15%, WACC=10%, IC=500 | Spread=5 pts; economic value=25 |
| BUS-T003 | Five margins 20%,21%,19%,22%,20% | Range=3 pts; stable signal |
| BUS-T004 | Largest customer share=35% | Concentration red flag |
| BUS-T005 | Five-year FCF=500, net income=450 | FCF conversion=1.111x |
| BUS-T006 | Beginning revenue <=0 for CAGR | NOT_MEANINGFUL |
| BUS-T007 | ROIC<WACC with score otherwise high | No excellent/wide-moat label |
| BUS-T008 | Missing NRR for non-subscription industrial | Use adapter; no penalty for N/A |

Every release must pass these tests and the shared QA checklist. Tests using a proxy must state the proxy explicitly.
