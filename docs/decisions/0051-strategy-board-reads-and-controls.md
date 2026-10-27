# 0051: Strategy board: control pairing, frozen read figures and sleeve totals

Status: proposed (principal software engineer, 2026-10-27); quant-reviewer check of the payload follows (ADR 0050)

## Context
The strategy board (backlog 132, docs/design/trades-ux-2026-10-27.md §2.3) shows each arm against "its control" and,
after the read, its figures. Three things were not fixed anywhere: which control an arm is read against (the frozen
spec names a control's arm for only some controls, e.g. F1C, XC15, F2C), what "the read's figures" are once trades keep
arriving after the read, and how per-sleeve lab totals can be shown without becoming a difference attack on the List's
per-account totals (ADR 0050: no aggregate covering fewer than 3 unread arms).

## Decision
1. Pairing (goldbot/api/strategy_labels.py `_pairs`), first match wins: a running control whose frozen `params.control`
   names the arm; else a control of the arm's own family on its timeframe and a shared account (same rule, or
   `<base>_control` with the family `<base>` or `<base>_...`); else a generic control (one of no strategy's family:
   `random_bracket`, `scalp_control`) on a shared account and sleeve, preferring the arm's pre-registration file, then
   its timeframe, then the spec's order. Tested on the shipped config.
2. Frozen read figures: the arm's first N counted trades closed by `read_utc` (N = its read size, H-61's 300 unless
   `config/arm_reads.yaml` `read_at` lists the prereg's n); everything counted after is "since read". Net R per trade
   with a normal 90% CI, hit rate, net money in one currency.
3. The difference against the control is shown only when the control is read too ("controls stay unread until
   listed"): arm mean minus control mean over the control's counted trades closed by the arm's read, Welch 90% CI.
4. Annealing state comes from the read row (`state`: C1, P, R; omitted = R, H-61 "not graduated at 300 -> retired").
5. Sleeve totals (per account and sleeve, 7 days) use trades_ledger.withheld_reason per sleeve and are withheld for
   every sleeve of an account when one sleeve is short, so no shown pair differs by a short sleeve.

## Consequences
- An arm's control can change if the spec or accounts.yaml changes registrations; the board reads it at start. The
  evaluator's recorded verdict, not the board's pairing, is the record of the read; a mismatch is a quant finding.
- A control must be listed in arm_reads.yaml before any arm shows "vs control"; the evaluator lists it at the first
  read that uses it.
- BXT/BXF (virtual exits on BXR's fills) count as separate arms for the 3-arm rule; the quant check decides whether
  they should count as one.
- Would change my mind: the quant-reviewer requiring a recorded comparison (from the evaluator) instead of a computed
  one, or a pairing table in config instead of the rule.
