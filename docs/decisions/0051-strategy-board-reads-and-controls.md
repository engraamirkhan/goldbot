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
2. Read units and sizes are the evaluator's (config/arm_reads.yaml, quant 2026-10-27 condition 2): `read_at` per arm,
   `read_units` for families read POOLED (W1, W2, H-80, H-81; H-80 counts only BXR's fills, `counted`), `counts` for an
   arm whose n is not its trades (S0R: stop exits). A pooled family shows one progress, ETA and read; its arms only
   their own counts. A unit is read when `reads` lists the arm id or the unit id; a broken or inconsistent file reads
   no unit. The ADR 0050 3-arm rule counts read units, so a pooled family's arms count once.
3. Frozen read figures: the unit's first N counted trades closed by `read_utc`; everything counted after is "since
   read". Net R per trade with a normal 90% CI, hit rate, net money in one currency.
4. The difference against the control is the evaluator's RECORDED comparison only (`vs_control_r`, `vs_control_ci90`
   on the read row; quant 2026-10-27 condition 1: the prereg defines it, e.g. H-81's day-paired family minus control,
   and never reads the control itself). The board computes none.
5. Annealing state comes from the read row (`state`: C1, P, R; omitted = R, H-61 "not graduated at 300 -> retired").
6. Sleeve totals (per account and sleeve, 7 days) use trades_ledger.withheld_reason per sleeve and are withheld for
   every sleeve of an account when one sleeve is short, so no shown pair differs by a short sleeve.

## Consequences
- An arm's control can change if the spec or accounts.yaml changes registrations; the board reads it at start. The
  evaluator's recorded verdict and comparison, not the board's pairing, are the record of the read.
- The evaluator keeps `read_at` and `read_units` in step with config/exploration_arms.yaml (a test fails on an
  unknown arm); a new pooled family needs its `read_units` row before its first fill.
- Counting units, not arms, withholds more lab totals while pooled families dominate a lab-day (the e2e seed now uses
  three units on the Day/swing lab).
