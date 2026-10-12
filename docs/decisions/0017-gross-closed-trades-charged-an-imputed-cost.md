# 0017: Gross closed trades are kept and charged a conservative imputed cost

Status: proposed (implementer, 2026-10-12; quant review findings A-D; needs quant and trading-safety review)

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
    measured rates or the settings prior. A swap credit is never counted, and a row with no side pays the long rate.
  - The charge is in USD per oz, so it is cost / entry in return units and cost / initial risk in R. Neither needs
    the lot size or an FX rate.
- A gross row whose charge cannot be computed (no entry price, or no entry time while swap is unknown) is counted:
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
- The stop rule's single-loss incident check still reads the recorded `pnl` (money). Charging it would need an FX
  rate, and the imputed amount is small compared with the 5% weekly cap.
- Other readers of `ret` (perf_report, monthly_report, digest) are unchanged. They can adopt the same helper.
- Rollback (S12): an older release ignores `ret_basis` and reads a `"price"` row by its `pnl_basis`, which is exactly
  today's behaviour. The field restricts nothing and is never on the order or exit path.
- What would change this decision: a broker that reports swap per deal on the demo terminal (then rows become net at
  the source), or evidence that the prior overcharges enough to block a gate that measured costs would pass. Then
  measure first; the charge is not loosened.
