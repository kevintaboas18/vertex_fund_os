# Risk and Resilience Analysis Agent - Validation Tests

| Test ID | Test | Expected result |
| --- | --- | --- |
| RSK-T001 | EBIT=15, interest=10 | Coverage=1.5x |
| RSK-T002 | Coverage=1.49x | Mandatory solvency warning |
| RSK-T003 | Cash=120, facility=0, monthly burn=10 | Runway=12 months |
| RSK-T004 | Price index peak 100, trough 40 | Max drawdown=-60% |
| RSK-T005 | Two customers 50% each | HHI=0.50 |
| RSK-T006 | Negative EBITDA | Net debt/EBITDA NOT_MEANINGFUL |
| RSK-T007 | Bank company | Altman/Beneish applicability reviewed; industrial scoring not automatic |
| RSK-T008 | Risk category=4/15, raw total=90 | Main profile capped Speculative |
| RSK-T009 | Forensic M-score above screen threshold | Flag only; no accusation |

Every release must pass these tests and the shared QA checklist. Tests using a proxy must state the proxy explicitly.
