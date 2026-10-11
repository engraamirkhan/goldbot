- **Performance report: quant review fixes (2026-10-11; follow-up to 207c9ef, re-review pending).**
  `goldbot/ops/perf_report.py` reads the record-gap lane's fields when present: `pnl_basis`, `money_source`,
  `family`, `timeframe`, `model_version` and `decision_price`.
  - **Net and gross columns.** Only `pnl_basis` "net" rows go into the net columns, plus legacy rows that record
    both commission and swap. Gross rows go into the gross column and count as "net not recorded (gross only, N)".
    Other rows count as "basis not recorded". Readings, money per day, the north star and drawdown use net rows
    only.
  - **Shadow table.** It reports `sum_r` (gross), and every reading is marked "(gross)". Matching ignores `taken`,
    which becomes its own column. It uses `model_version` when the proposal records one, and the nearest p only for
    legacy proposals. Every unmatched proposal says why.
  - **Calibration row.** It sets the approved signals' shadow R (gross) against their live net R. The closed trade
    is linked by client order id = proposal id.
  - **Limits.** They are grouped by the RiskGate's windows: the risk day from 00:00 UTC and the risk week from
    Sunday 00:00 UTC. The section is labelled "closed-trade, lower bound" and points to a future daily risk summary.
  - **North star.** It selects trades by their entry's opportunity day, whenever they closed. Open trades entered
    on an opportunity day show as pending (from `state/orders_<account>.json`). Day types are labelled "(ex post)".
    The bars come from one source, `--bars-source` (default dukascopy).
  - **Samples.** A row is sufficient only from 30 trades with a net R. The header gives the group count and a
    multiple-comparisons caveat. Entry slippage comes from `decision_price`.
  - Tests: `tests/test_perf_report.py` (37), including the 22:00 boundary, several shadow versions with an untaken
    candidate, a trade straddling the range end, and the day's own range left out of its ATR.
