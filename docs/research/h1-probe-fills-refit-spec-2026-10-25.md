# H1 refit spec: scheduled-probe fills as a separate `probe_fills` cost input (2026-10-25)

**Status: spec only, 0 trials, 0 to K, no holdout.** Binds to `prereg-cost-measurement-2026-10.md` (the "prereg"):
section 4 (K7, K9, K10, K11), section 5 (reading rules), section 8-9 (M1 as deployed; TP exits excluded). Nothing here
changes a reading rule. Fills are cost evidence only, never edge evidence (prereg s8).

## 1. Problem
`jobs.nightly_costs` (goldbot/ops/jobs.py ~199) reads store `fills`; the runner excludes probe fills from `fills`
(goldbot/engine/runner.py:1326-27, `not cost_probe.is_probe(...)`), so `n_fills` is 0 and `slippage_table` stays at
the prior for ever. 134 scheduled (M1, `purpose=cost_measurement`) fills sit in aux.db `cost_probes`
(`cost_probe.probes(state_dir, account_id)`). Keep them out of `fills` (trade record stays clean, prereg K7); feed the
cost table through a separate input.

## 2. Design
1. New pure function `probe_fills_frame(records: list[ProbeRecord]) -> pd.DataFrame` in goldbot/execution/costs.py
   (or a small module beside it). Columns match `slippage_table`: `ts_utc` (= `sent_utc`), `side`, `requested`
   (= `requested_price`), `filled` (= `fill_price`), `order_type="market"`, plus `spread_usd`, `latency_ms`,
   `commission`, `arm` (`M1` if purpose is cost_measurement). Slippage = side*(filled - requested), $/oz, positive =
   worse (existing convention).
2. Row filter (all must hold): `purpose == "cost_measurement"`; `retcode` is a done code; `requested_price` and
   `fill_price` not null; entry leg only (exit legs wait for K11); `sent_utc <= slot` and inside
   `fills_window_days`. Mirrored (M0) probes are not in H1 (bar-close timing, separate label).
3. `nightly_costs` reads `cost_probe.probes(state_dir, acc.account_id)`, builds the frame, passes it as new
   `probe_fills=` argument to `build_cost_table`. `CostTable` gains `probe_slippage: dict[str, SlippageStat]` (key
   `<session>:market`), `n_probe_fills`; the row dict reports `probe_fills`. `fills` argument is unchanged (real
   trades). The published table (`PublishedCostTable`) gains the same field plus `fields["probe_slippage"]`; no ids.
4. **Refit rule (per session, prereg s5 + task rule).** For session s in {asia, london, newyork} (same
   `SessionTable`, by `sent_utc`): with n_s >= 50 probe fills, cell is `from_prior=False`, raw mean M_s and a day-block
   bootstrap 95% CI (2,000 resamples, seed 20261020) are recorded. Fewer: prior, `from_prior=True`. Never pool
   sessions to reach 50. Real-trade `fills` and probe fills are never merged; where a session has both measured, the
   charged slippage is `max` of the two cell values (conservative). The prereg's 30-fill decision read of M1
   cells stays the decision read per bucket; this 50-per-session refit only sets what research is charged.
5. **Charged value (choice stated): floor at the prior, not at 0.** `charged_s = max(slippage_prior_usd (0.15), M_s)`
   per prereg s5 / K3 ("research slippage per leg = max(0.15, mean)"). Favourable (negative) slippage is therefore
   never credited and does not lower the charge; this is stricter than capping at 0. The raw mean and CI are
   published beside it as `measured_mean` for reporting. Consequence today: M = -0.07 gives a charge of 0.15 per leg,
   same as the prior; the refit can only raise cost. The rule is always charge = max(0.15, M_s), with no CI condition.
   Tracked follow-up: main's `slippage_table` must floor real-fill slippage at the prior before any real fills reach `min_fills`.
   NOTE: existing `slippage_table` publishes the raw mean when n >= min_fills, with no floor, so do not reuse it
   unchanged for probes; add the floor, and flag the same gap for real fills to the quant-reviewer.
6. Spread is not taken from fills (prereg decision 1); `spread_usd` of fills is reported only as a cross-check
   (mean 0.08, p90 0.12 vs the tick profile). Commission: only rows with the K9 fx field set count; agreeing deals
   rule of prereg s5 applies. Latency (median 449 ms) is reported, not charged.

## 3. Quant checklist (sign-off items)
- **No leakage.** Only rows with `sent_utc <= slot`; cost for a research run dated D uses table built before D
  (existing costs-v1 as-of rule); never refit inside a backtest window using later fills. Fill data never touches
  labels, features or signal selection (alternating side, no signal).
- **Session split.** Session by `sent_utc` with the same `SessionTable` as `slippage_table`; the 20:45-22:15 NY
  blackout leaves no rollover fills (B6 from ticks only); no pooling; n and CI printed per session.
- **30 s hold vs strategy holds.** Probes measure entry-leg market slippage only; the exit leg waits for K11 and
  excludes TP exits (prereg s9). Entry slippage does not depend on hold length, but strategy holds (hours) add
  swap/gap/stop-slippage risk that probes cannot see: not extrapolated, flagged in the table notes. Entry at 15m
  bar close is an upper bound for off-close entries (prereg s8, accepted).
- **Favourable slippage:** floored at the 0.15 prior (section 2.5); raw mean reported; t-stat (-2.5) is not
  evidence of a real edge for entries, and demo fills bound live slippage from below (last look, queue): every
  cell carries `venue: demo`.
- **Selection/other.** Alternating side removes directional bias; check side-split means in the report. Outliers:
  |slip| > $5/oz reported as tail events and kept. 134 fills across ~3 sessions: confirm each has >= 50 before any
  refit (not assumed). Zero trials added to K.

## 4. Implementer test list (failing test first; `--testmon`)
1. `probe_fills_frame`: keeps only cost_measurement rows with a done retcode and both prices; drops null price, mirrored
   (cost_probe) rows and future-dated rows; sign convention (BUY fill above request = positive).
2. `nightly_costs` with 134 synthetic probe rows and empty `fills`: `probe_fills` count > 0 in the row; `fills` stays 0.
3. Session cell with 49 fills stays `from_prior=True`; 50 flips to measured; no cross-session pooling.
4. Mean -0.07, n=60: charged 0.15, `measured_mean` -0.07 published; mean 0.30 with CI lower > 0.15: charged 0.30.
5. A probe fill never appears in the store `fills` table (runner.py:1326 guard test still passes).
6. Published table: `fields["probe_slippage"]` label matches values (validator), no account id/login in JSON.
7. Bootstrap CI deterministic with seed 20261020; day-block resampling.
8. No look-ahead: a probe sent after `slot` does not change the table built at `slot`.
9. Rows with no K9 fx are excluded from commission, still counted for slippage.
10. aux.db absent or empty: table builds with priors and no error.

Owners: implementer (code), data-engineer (aux.db read), quant-reviewer (floor choice, CI), trading-safety-reviewer
not needed (no order path touched). Flags: the real-fills slippage floor gap above; K9/K11 still gate commission and
exit-leg readings.
