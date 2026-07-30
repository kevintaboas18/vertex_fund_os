# Valuation Analysis Agent - Validation Tests

| Test ID | Test | Expected result |
| --- | --- | --- |
| VAL-T001 | FCFF1=100, WACC=10%, g=3% in perpetuity | TV at t0 under one-stage setup=100/(0.10-0.03)=1428.57 |
| VAL-T002 | WACC=8%, g=8% | Reject model: denominator zero |
| VAL-T003 | WACC=7%, g=8% | Reject model: g>=WACC |
| VAL-T004 | ROIC=20%, g=4% | Terminal reinvestment rate=20% |
| VAL-T005 | EV=1000, cash=100, debt=300, diluted shares=80 | Equity=800; value/share=10 |
| VAL-T006 | Scenario probabilities 20%,60%,20% | Sum=100%; pass |
| VAL-T007 | Terminal PV=800, EV=1000 | Terminal share=80%; high-sensitivity flag |
| VAL-T008 | Missing option/convertible schedule | Per-share value incomplete |
| VAL-T009 | FCFF and EVA differ materially with same assumptions | Fail reconciliation; inspect reinvestment/capital |
| VAL-T010 | Bank selected | Use residual-income/excess-return adapter; no EV/EBITDA primary model |

Every release must pass these tests and the shared QA checklist. Tests using a proxy must state the proxy explicitly.
