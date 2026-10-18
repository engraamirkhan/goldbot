# Feature-group spec: S-1 (low-ATR / cost regime) and S-2 (hour of day) in O-4 and H-02 (strategy-researcher, 2026-10-18)

Answers the research director's priority 3 in `batch-2026-10-18-readout.md` §5: "S-2 hour features + S-1 ATR-regime
features as feature groups in O-4/H-02 discovery ... Hour-of-day overlaps N1 `tod_periodicity`, so they become one
group, not two". Written before any H-14 count, label or outcome and before O-4's group map is pinned (due
2026-12-11). Nothing here computes a feature-to-label statistic. 0 registry trials.

## 1. Finding: both are already in the pinned default feature set, so the cost is K_eff +0

Checked 2026-10-18 on this branch: `DEFAULT_FEATURE_NAMES` hashes to **`f-079deacc61`**, O-4's base version, and it
contains the features below. Under O-4's pinned grouping (by family, `context_groups: per_timeframe`), each column is
already in one of the 14 counted groups.

| Hypothesis | What it needs | Existing column(s) | Feature / family (O-4 group) | Definition (as coded) | Point in time |
|---|---|---|---|---|---|
| S-1 cost in R | round trip / ATR | **`em_atr_cost`** | `expected_move` / `volatility` | ATR14 / (96-bar median of the bar spread + 0.37 $/oz), `goldbot/features/survey.py:221-235`; 0.37 = 2 x 0.15 slippage + 2 x 0.035 commission (`ROUND_TRIP_EXTRA_USD`) | bar's own OHLC and spread, known at the close; trailing windows only |
| S-1 expected move vs cost | forecast move per cost | `em_yz_4_cost`, `em_yz_16_cost`, `em_yz_48_cost` | same | Yang-Zhang(20) x close x sqrt(h) / cost | same |
| S-1 ATR level in $ | the lot-fit driver | `atr14` | `atr` / `volatility` | Wilder ATR14 on mid, $/oz (`technical.py:91-96`) | same |
| S-1 ATR regime | high/low relative to its own history | `atr_ratio_14_100`, `vol_tercile`, `atr14_pct` | `atr`, `realised_vol` / `volatility` | ATR14/ATR100; rv_20 tercile over the last 400 bars; ATR14/close | same |
| S-1 spread share | spread / ATR | `spread_atr`, `spread_rel_median_48` | `microstructure` / `microstructure` | bar spread / ATR14; spread / its 48-bar median (`technical.py:259-263`) | same |
| S-2 hour of day | hour and session | **`hour_utc`**, `session_id`, `min_since_{asia,london,newyork}_open`, `dow`, `us_dst`, `eu_dst` | `session` v2 / `calendar` | UTC hour of the bar's open timestamp; session from local exchange clocks with DST (`features/session.py:14-34`) | a calendar fact of the bar timestamp; no data read |

**Consequence.** S-1 and S-2 enter O-4 with **no new columns, no new group and K_eff +0**. They also enter H-02 the same
way: H-02's pinned dump puts non-cluster columns in their family groups, so `volatility`, `microstructure` and
`calendar` are already among H-02's screened groups (its K_eff of about 10 already counts them). Nothing in H-02's pinned
dump or `f-51ac5ebabb` changes.

**The director's "one group, not two" is met as follows.** Hour-of-day stays in the `calendar` group, where it already
is. It is **not** merged into `tod_periodicity`. N1 was signed on 2026-10-18 as its own group of six past-slot return
columns. Merging would change a signed amendment, and it would mix a calendar fact with a statistic built from past
returns, so a survival could not be attributed to either. No new hour group is added, so K_eff does not rise.

## 2. New columns considered and rejected (so they are not re-proposed)

| Candidate | Why it is not added |
|---|---|
| `cost_r_1atr` = (spread + 0.37) / ATR14 | About 1 / `em_atr_cost`. For a tree model, a monotone transform of an existing column adds nothing |
| `cost_r_h25` = (spread + 0.37) / (0.6 x ATR14) | A constant multiple of the above |
| `lot_fit_1atr` / stop-fits-0.01-lot flag | Depends on equity, FX rate and RiskGate settings, not on the market. An account fact belongs in the lot-fit overlay (H-27), not in a feature. It would also change the feature hash whenever a setting changes |
| `atr14_rank_60d` (60-day percentile of ATR14 in $) | The only candidate with new information: a longer regime window than `vol_tercile` (about 4 days at 15m) and `atr_ratio_14_100` (about 1 day). Expected value is low, because cost-to-ATR (`em_atr_cost`), not the dollar level, is what matters for net R; the dollar level matters for lot fit, which is an account fact. **Not added in Q1.** It may be proposed again for the Q2 15m discovery with a stated mechanism |
| `calm_hour` dummy (22-00 and 10-12 UTC) | The hours were chosen from an ATR-by-hour table that includes the holdout window (`tight-stop-entry-study` §3). A tree on `hour_utc` can find any hour split without that choice |
| hour sine/cosine | Redundant for tree models; `hour_utc` is an integer and splits freely |

If the research director still wants a dedicated S-1 group, the only defensible one is `atr_regime` = {`atr14_rank_60d`}:
an opt-in family (never in `DEFAULT_FEATURE_NAMES`, like `tod_periodicity`), VERSION "1", computed on the decision
frame only (not a context feature), with a trailing window of the previous 5,520 bars (60 trading days x 92 bars) and a
minimum of 1,840. The current bar's ATR14 is ranked against the window, ties take the average rank, and the result is
NaN below the minimum. `lookahead_check` must report 0 columns. **K_eff +1**, under the same drop-not-delay rule as N1
(dropped if not merged by 2026-12-11). Recommendation: do not add it.

## 3. How S-1 and S-2 are read inside O-4 (fixed now)

- **Unit.** O-4's reading unit is the group (`survivor_unit: group`). S-1 and S-2 are therefore read through the
  `volatility`, `microstructure` and `calendar` groups under O-4's own rule. No separate S-1 or S-2 verdict exists.
- **Diagnostics (reported, never read).** The verdict record lists the selection frequency of `em_atr_cost`, the three
  `em_yz_*_cost` columns, `atr14`, `spread_atr`, `hour_utc` and `session_id`, if the run records per-column frequencies.
  No column-level result can make a group survive or earn a slot.
- **S-2's trial trigger, restated for the group unit.** `sizability-2026-10-18.md` said: "spend an individual trial
  only if stability selection keeps an hour or session feature in >= 60% of folds". Under O-4 that becomes: the
  `calendar` group survives by O-4's rule and ranks in the top two, and so earns O-5 or O-6, the model trial. No
  rule-only "calm hours" trial is written unless a 15m parent has passed on its own. Otherwise the record reads
  "hour filter: no selection".
- **S-1's question** ("does low-ATR gating destroy the 15m edge?") stays the 0-trial lot-fit diagnostic on each batch
  cell (`sizability-2026-10-18.md` S-1). Discovery cannot answer it, because it reads groups, not subsets.

## 4. Risk raised: cost-driven survivals (for the quant-reviewer; MEDIUM)

O-4's labels pay the spread (ask in, bid out; P-5). Rows with a high spread relative to ATR therefore lose more often
on **both** sides. `em_atr_cost`, `spread_atr` and `hour_utc` (spread and ATR vary by hour) can raise the pooled
out-of-fold AUC without any directional information. They could then pass O-4's AUC legs (>= 0.53, lower bound > 0.50)
as cost filters. The net-R leg (net mean R > 0 on >= 1,000 trades) still guards the verdict, because a pure cost filter
cannot be net positive without some gross edge on the rows it keeps. But an O-5/O-6 slot could still be spent on a
group whose AUC is mostly cost.

**Proposed amendment** (for quant-reviewer review and director signature before the 2026-12-11 pin; it changes no
config hash):
- For each surviving group, the evaluator reports the same model's out-of-fold AUC against the **mid-price**
  barrier label (no spread), next to the pinned label's AUC.
- This figure is reported and never read. A large gap between the two AUCs is flagged in the verdict as "cost-driven".

The diagnostic needs a mid-label option in discovery, which is code owed. If that code is not merged by 2026-12-11,
the flag is written from the per-column frequencies above.

## 5. K_eff ledger effect

| Discovery | Before | After this spec | Note |
|---|---|---|---|
| O-4 (H-14) | about 17 (14 coded groups + `liquidity` + `activity` + `tod_periodicity`), +1 if D-TVR is adopted (TVR ruling branch) | **unchanged** | +1 only if the director overrides section 2 and adds `atr_regime` |
| H-02 | about 10 (pinned) | **unchanged** | pinned dump untouched |
| Registry trials | none | none | 0 trials |

Owners: research-director signs section 1 (group placement) and section 4 (the amendment) under O-4. The quant-reviewer
reviews section 4. The implementer owns the mid-label diagnostic (section 4) and `atr_regime` only if it is signed.
