"""Task 20: judgment overlay -- fills in `JudgmentRequest`s the 6 Cerebro
specialists leave `NOT_SCORABLE` with Claude sub-agent answers
(`wbj.schemas.overlay.Judgment`), rescoring via
`wbj.specialists.common.rescore`. See `wbj.overlay.merge` for the
implementation and its design-decision notes.
"""
