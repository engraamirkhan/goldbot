# 0017: Gross closed trades are kept and charged a conservative imputed cost

Status: accepted (quant review 2026-10-16, finding 6; proposed by the implementer 2026-10-12 on quant review findings A-D)

## Context
A closed record is "net" only when commission and swap are both known and inside `pnl` (`ClosedTrade.pnl_basis`,
GAP-05). The paper engine never knows swap, and a broker may leave commission out, so many rows are gross. The phase
gates (paper and live expectancy, live drawdown, broker agreement), the stop rule's lower bound and the ruin Monte Carlo
all read `ret` or `r` as if every row were net, which is optimistic. Separately, the broker-deals path on an account
with no USD conversion recorded the gross price return while `pnl_basis` said "net".

There are two ways to deal with gross rows:
- Drop them. Rejected: the reasons a row is gross (an adopted orphan, a restart, a broker that leaves out a field) can
  be linked to the trade's outcome, so dropping them is selection bias.
- Mark the whole sample "unknown". Rejected: it hides how large the problem is and gives a gate nothing to act on.

## Decision
- Keep every gross row and charge it a conservative imputed cost, computed in one helper
  (`goldbot/ops/cost_imputation.py`), and only for the costs its return lacks:
  - Commission, both sides: from the account's nightly cost table (measured, else the settings value), else the
    settings commission for the broker (an unknown broker pays the largest configured one).
  - Paid swap for every rollover held through, from `rollover_nights` with the triple day counted, at the table's
    measured rates or the settings prior. A swap credit is never counted, and a row with no side pays the larger paid
    rate of long and short.
  - The charge is in USD per oz, so it is cost / entry in return units (the exit price when the entry is missing)
    and cost / initial risk in R. Neither needs the lot size or an FX rate.
- A record written before `ret_basis` existed, or by a rolled-back release, can carry a price return while it says
  pnl_basis "net" (review R1). Such a row has no `ret_basis`, its money is from the broker, its initial stop is known
  and its R is empty, and it is charged as a price return.
- A gross row whose charge cannot be computed (no price, or no entry time while swap is unknown) is counted:
  - The paper and live gate items it feeds fail closed.
  - The ruin sample is labelled "unknown".
  - The stop rule only reports it, because a breach detector cannot fail in the safe direction without data.
- Every gate item shows the gross count and the total charged. A new optional item, `gates.max_gross_share`, can also
  fail the paper and live gates on the share of gross rows. It is null (context only) until the owner decides; the
  recommendation is 5%.
- `ClosedTrade.ret_basis` is `"pnl"` or `"price"`, and None on older records:
  - `"pnl"` means ret = pnl / notional, so it carries exactly what pnl carries.
  - `"price"` means a price return with no costs.
  - It is not "net"/"gross" because a P&L-based return that is missing only swap is neither. With this split the
    helper charges only what is missing and never charges a cost twice.
- On a USD account with no broker conversion, the notional is already in the account currency, so the engine records
  ret = pnl / notional. A non-USD account with no conversion keeps the price return, marked `"price"`.

## Consequences
- Gate evidence moves pessimistically while paper trades are gross. This is intended: the evidence goes into the
  owner's sign-off.
- Health's stop-rule detail now appends the gross summary whenever gross rows exist; the golden fixture was updated.
- The stop rule's single-loss incident check adds a gross row's imputed USD to its loss as if it were account
  currency. That overstates it on a GBP or EUR deposit, which is conservative for a breach detector.
- A row with no entry time and unknown swap is still unpriced. Charging swap for the agent's maximum hold would need
  the agent's exit policy, and the engine always records an entry time, so this only affects old records.
- Other readers of `ret` (perf_report, monthly_report, digest) are unchanged. They can adopt the same helper.
- Rollback (S12): an older release ignores `ret_basis` and reads a `"price"` row by its `pnl_basis`, which is exactly
  today's behaviour. The field restricts nothing and is never on the order or exit path.
- Known inaccuracies (quant review 2026-10-16, finding 6, LOW; accepted, no code change):
  - The charge uses the account's cost table as of tonight for every past trade (`CostImputer.from_state`,
    `goldbot/ops/cost_imputation.py`), not the commission and swap terms in force when the trade was made. The bias
    can go either way and is small.
  - The stop rule's single-loss incident check (`evaluate_stop_rule`, `goldbot/ops/gates_phase.py`) subtracts the
    imputed USD from `pnl` again on broker-sourced price-return rows, whose `pnl` already includes commission. That
    counts commission twice, a few cents per 0.01 lot, in the conservative direction.
- What would change this decision: a broker that reports swap per deal on the demo terminal (then rows become net at
  the source), or evidence that the prior overcharges enough to block a gate that measured costs would pass. Then
  measure first; the charge is not loosened.
