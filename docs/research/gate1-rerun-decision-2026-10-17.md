# Gate 1 after the DSR-inputs fix: rerun decision (research director, 2026-10-17)

**Decision: no rerun. No trial is spent, K stays at 20.** Gate 1 (`backtest_to_paper`) stays closed, as it would
whether DSR inputs were stored or not. A new pass has to come from a new pre-registered trial (Q1 2027, from registry
trial #21), and every such row stores `dsr_inputs` itself.

Context: since e501d56 (quant review 2026-10-16), gate 1 recomputes the deflated Sharpe from
`results.model_filtered.dsr_inputs` on today's trial count and fails closed on a row that has none. No registry row
has them. The quant reviewer's remedy is to rerun the best trial as one registry trial (same config, family and
feature version, `--rule-ref` to the design gates, bars cut at `holdout_from`, holdout not re-scored), and let the
rerun decide. This note decides whether that rerun is worth a trial.

## Evidence

Registry: `research-v1/registry.jsonl`, downloaded 2026-10-17, 20 rows. It was read with this tree's
`registry.costs_recorded`, `promotion_refusal`, `n_trials_effective` and `gates_phase.best_backtest_trial`
and `current_dsr`. Data release `data-v1` has not changed since 2026-10-01. The last `research` run was 37899017759
on 2026-10-09, and it failed.

| Check | Result |
|---|---|
| Rows with `results.gates.passed: true` | **0 of 20** |
| Rows with the cost treatment recorded (`costs_recorded`: swap + cost source) | **0 of 20** |
| Rows with stored `dsr_inputs` | 0 of 20 |
| `best_backtest_trial(rows)` | **None** |
| `n_trials_effective` (K) | 20 (no discovery rows) |
| 2026Q4 budget (`quarter_trials`) | **20 / 20 used**. `check_budget` refuses a 21st trial until 2027-01-01 |

Missing DSR inputs is not what holds gate 1 shut. If every row had them, every row would still fail on gate checks
that do not involve the DSR at all.

### The "best" trial: #16 (tsmom 1h, `f-840782b51d`, run 2026-10-09)

#16 comes closest. It is one of only two walk-forward rows that pass 2 of the 4 research gates (candidates, per-fold).
The other is #20, pooled_1h, which fits only 35 model-filtered trades and has a worse net result. #16 also has the
largest gross edge in the registry (hypotheses.md R-06).

| #16 vs gate 1 | Value | Needed |
|---|---|---|
| Labelled candidates | 3,829 | 1,500 (passes) |
| Smallest test fold | 94 | 60 (passes) |
| Model-filtered trades | **33** | 500 (`gates.backtest_min_trades`). The DSR is only computed from 200 trades (`metrics.MIN_TRADES_FOR_DSR`) |
| Positive model-filtered years | **1** (2021) | 3, including 2021 or 2022 |
| Model-filtered mean return | −0.042% per trade, PF 0.84 | > 0 |
| Rule-only gross / net R | +0.061 R (t 2.63) / **−0.079 R (t −3.50)** | net > 0 |
| Swap | not charged (prior: about −60 USD/lot/night on longs) | charged |

Older rows are worse or no longer valid:
- Pre-fix rows: #2 trend (60 trades, stored DSR 0.995) and #10 mean_reversion (16 trades, stored DSR 0.991). They come
  from the leaky, non-cross-fitted evaluation that the 2026Q4 reruns #12 and #14 superseded. Their configs then fit
  3 and 13 trades.
- R-01 to R-07 are retired or failed (hypotheses.md B), #16 included (R-06: failed, superseded by H-01).

## Why the rerun is not worth a trial

1. **It cannot pass.** Same config, same feature version and the same bars cut at `holdout_from` give the same
   model-filtered sample, about 33 trades. `metrics.summarize` stores `dsr_inputs` only from 200 trades, so the
   rerun's row would again have no DSR inputs and would fail closed for the same reason. It would also still fail the
   500-trade rule, the 3-positive-years rule and the net-expectancy check. Charging swap, which this tree now does,
   only lowers the net figure.
2. **The budget does not allow it now.** 2026Q4 is at 20/20, and `research_pass.py` would refuse the run. The earliest
   slot is 2027Q1. There, 20 − 8 reserved (`research.reserved_trials_quarter`) = 12 open trials, under the E-5 cap,
   which allows a run only with a pre-registered rule.
3. **It costs every later trial.** K would go from 20 to 21, raising the DSR bar for the Q1 queue (H-01, H-02 and its
   survivors at N = registry + K_eff ≈ 35) and buying no information.
4. **It re-tests a failed family** without the new evidence that hypotheses.md B requires.
5. **It does not block anything.** Gate 1 is not in the engine's path. The demo engine start on Sun 18 Oct runs on
   demo accounts, and `backtest_to_paper` is only read as the stop rule's reference date (`ops/checks/risk.py`).

## When a rerun would be worth it

Rerun a config only if its latest evaluated row has `gates.passed: true` and ≥ 200 model-filtered trades but no
`dsr_inputs` or no recorded cost treatment, so that the rerun could change gate 1's answer. No such row exists today.
All rows from #21 on are written by post-e501d56 code and store both. If one ever qualifies, pre-register it in that
quarter's preregistration file before dispatch, citing `DESIGN.md` gates through `--rule-ref`.

For reference only, a dispatch would look like this. **Not dispatched, and not to be run against #16:**

```
gh workflow run research.yml --ref main -f specialist=<family> -f variants='[<the row's config overrides>]' \
  -f rule_ref='<preregistration-YYYYQn.md#gate1-rerun-...>' -f rationale='gate-1 DSR-inputs rerun of trial #<n>'
```
