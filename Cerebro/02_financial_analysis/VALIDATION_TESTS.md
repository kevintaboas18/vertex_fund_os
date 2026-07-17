# Financial Analysis Agent - Validation Tests

| Test ID | Test | Expected result |
| --- | --- | --- |
| FIN-T001 | Revenue 110 vs 100 | YoY growth=10% |
| FIN-T002 | Current assets=150, liabilities=100 | Current ratio=1.5 |
| FIN-T003 | EBIT=30, interest=20 | Coverage=1.5x; no below-1.5 warning |
| FIN-T004 | EBIT=29, interest=20 | Coverage=1.45x; solvency warning |
| FIN-T005 | OCF=120, capex=40, revenue=800 | FCF=80; FCF margin=10% |
| FIN-T006 | NI=-10, FCF=-20, equity issuance positive | Bad/Avoid override |
| FIN-T007 | ROIC=9%, WACC=11% | No Excellent verdict |
| FIN-T008 | 27 valid metrics all Excellent | 54/54=100% |
| FIN-T009 | Negative equity | Debt/equity NOT_MEANINGFUL |
| FIN-T010 | Bank security type | Use bank adapter; conventional FCF/ROIC N/A |

Every release must pass these tests and the shared QA checklist. Tests using a proxy must state the proxy explicitly.
