# Pre-registration 2026-11-24: the red-box rule on history (RB1H, RB4H vs their random ladders), EVIDENCE-RB-HISTORY

Principal quantitative strategist, 2026-11-24. Weekly decisions 2026-11-22 step 6; finding `EVIDENCE-RB-HISTORY`
(config/findings.yaml, due 2026-12-04). Hypothesis H-99 in `hypotheses.md`. Written and committed **before any
outcome under this card was computed**. The only data read before this commit were outcome-blind: candidate counts,
session days, stop distances and target distances on the learn window (section 7), all known at the signal close.

Nothing in the engine, RiskGate, `config/accounts.yaml`, `config/exploration_arms.yaml` or any live setting changes.
No order anywhere. RB1H/RB4H stay retired (PR #424); this card only decides whether a history case for a return exists.

## 0. Why this card exists (the gap it closes)

- `prereg-amendment-2026-11-15-redbox-week.md` registered a one-week **learning** run with no pass rule: "No p-value,
  no deflated Sharpe and no arm-vs-control difference from this week is read as an edge". The week read (weekly
  decisions 2026-11-22): pooled replay -0.15 R a candidate vs control -0.17 R; RB15 -0.077 vs RBC15 -0.061. Noise.
- The owner's priority 1 (2026-11-22) retired RB1H/RB4H without a history pass and queued this replay. This card is
  that pass, with a rule written first.

## 1. Prior evidence (this is not a fresh family)

| Read | Rule | Window | Instrument | Net of costs | Result |
|---|---|---|---|---|---|
| R-15 / H-50, trial #89 | `box_break_close` 15m (box break + MFI + trend + golden-zone filters, 1.5/3 ATR) | 2010-01..2025-09 | XAUUSD Dukascopy 1m | yes, $0.23 RT | gross +0.030 R (t 1.61, n 5,684); net -0.297 R; STOP |
| H-80 R2 (`prereg-2026-11-09-kog-box-ladder.md` table) | 15m-4h box breaks, runner/struct/fixed exits | **2018-01..2025-09** | XAUUSD | yes | n 5,355; net -0.20 to -0.29 R |
| ML-M2 / R-20 | meta-model on `box_break_close` 15m candidates | 2010-2025 | XAUUSD | yes | top decile net -0.513 R; retired |
| Red-box week | this card's rule, RB15/RB1H/RB4H | 2026-11-15..22 (forward, demo) | ICM XAUUSD | yes | arm close to control at every TF |

Robust in our data: own-timeframe box breaks have no net edge at 15m. The 1h/4h form of **this** rule (no filter,
structural stop at the broken box's far side, target the next box) has never been read on history. Prior P(learn
CONTINUE) about 5 % (judgement: generic box breaks fail at every TF tested; the mechanism, below, is weak).

**Disclosure that shapes the gates:** the confirm window 2018-01..2025-09 has been read for box breaks at 15m-4h (H-80
R2). A confirm pass here is therefore **CONFIRM-SEEN**: it cannot qualify alone. Qualification also needs the one fresh
intraday window, test_old's clean years 2008-02..2009-12 (ADR 0067; intraday data-quality ruling 2026-11-10), read once
through `windows.allow_test_old_for` after a confirm pass.

## 2. Hypothesis and mechanism

H-99: a 1h (4h) close through a red box (our `sr_channels` replica, owner's chart settings), entered at the next open
with a stop beyond the broken box and a target at the next box, earns more net R per candidate than the identical rule
on a seeded random ladder with the same box spacing, and earns more than 0 net.

Mechanism, stated so it can be refuted: support/resistance levels drawn from pivot clusters coincide with resting stop
orders; a close through one triggers them and the move continues to the next cluster (Osler 2003, *Journal of
Finance* 58(5), 1791-1819, stop-loss clustering beyond round numbers and published levels, EUR/USD/JPY/GBP 1999-2000,
order data, not a trading return net of costs; Osler 2000, *FRBNY Economic Policy Review* 6(2), S/R levels from
vendors predict intraday trend interruptions, FX 1996-98, not net of costs). No peer-reviewed net-of-cost evidence for
pivot-box breakouts in gold exists that we know of. The random ladder isolates **where** the boxes sit from the
geometry (stop width, target distance, side frequency) a box rule imposes on any price path.

## 3. Rule (exactly the live code, no new parameter)

- Code: `goldbot/research/arm_rules_redbox.py` (rule-code version as at `origin/main` 5608b590), functions
  `redbox_break` (arm) and `redbox_control` (control), through `arm_rules.candidates` and the history replay
  `scripts/run_arm_backtest.py` `run_arm`, driven by `scripts/research/rb_history.py` (this commit).
- Arm rows as frozen in `config/exploration_arms.yaml`: RB1H (1h, hold 48 bars), RB4H (4h, hold 24), controls RBC1H,
  RBC4H; stop band $1-$40, per_trade_bracket `redbox`. Boxes `smc_replica.DEFAULT_PARAMS` (pivot 5, width 1 % of the
  300-bar range, 15 boxes, lookback 300) on the arm's own timeframe.
- Entry: open (mid) of the first 1m bar at or after the decision close. Barriers on the 1m mid path, stop first when
  both fall in one minute; time exit at the hold. Common filters (rollover 20:45-22:15 UTC, Friday from 19:00 UTC,
  tier-1 blackout from `raw/calendar`) and the stop band apply to arm and control alike.
- A candidate with no next box (`no_next_box`) is never sent, by arm or control: excluded from both.
- Control in history: every `redbox_control` candidate is replayed (the amendment: "recorded skipped replay_only ...
  and replayed"); `rb_history._replayed_control` clears only the `replay_only` skip, which in the engine stops the send.
- Not applied (they are demo-account refusals, not the rule): window_0207, N-3, slots, the £ caps, the weekend flat.
  The Friday-19:00 common filter stops new entries; a held position may cross the weekend (swap charged).

## 4. Windows and data

- Learn 2010-01-01..2017-12-31 (by entry time), stage 1, read by this card's first run.
- Confirm 2018-01-01..2025-09-30, stage 2: **sealed until the quant-reviewer signs this card**, and read only for a
  timeframe whose learn verdict is CONTINUE.
- test_old 2008-02-01..2009-12-31 (the clean intraday years), stage 3: only after a confirm pass, once, through
  `allow_test_old_for`. **Deviation from the dispatch brief, on purpose:** the brief asked for learn and test_old now.
  Reading test_old before confirm is refused by `goldbot/research/windows.py` (`TestOldSealed`) and ADR 0067, and,
  because confirm is box-family-seen (section 1), test_old is the only fresh gate this family has left. Spending it on
  a screen would leave no independent test.
- learn_old 2003-2005 is not used: it fails the intraday data-quality ruling (2026-11-10).
- Holdout 2025-10-01 onward: never read (arms/windows guards; exits after 2025-09-30 are cut by `cut_at_holdout`).
- Data: Dukascopy 1m XAUUSD yearly Parquet in the Mac's `raw/` (local only, never published). Each year is run with
  120 calendar days of earlier bars for warm-up (the channel state needs 313 bars, about 75 days at 4h) and 14 days of tail for exits.
  The driver reads these bars with `read_bar_files` directly, so warm-up for 2010 includes Sep-Dec 2009 (test_old) bars
  and exit tails for 2017 include Jan 2018 (confirm) bars. Those bars are price history for state and exits only: no
  candidate or outcome from either window is read or scored (quant review 2026-11-24).

## 5. Cost model (fixed now)

- **Primary (W2 bps model):** net = (move incl. swap - 0.575 bp x entry - 1.75 bp x entry on a stop exit) / risk.
  0.575 bp is $0.23 at $4,000 (the frozen conservative round trip: p90 spread both legs, slippage upper CI,
  commission); 1.75 bp is the measured $0.70 stop overshoot at $4,000. Swap: `SwapSpec.research_shipped()` (today's
  broker financing on each trade's notional, per rollover, triple on the broker's day).
- **Stress (gated, must be > 0):** the frozen dollar constant, $0.23 round trip + $0.70 on each stop exit (`net_r_os`
  of `run_arm`). At 2010-17 prices ($1,050-1,900) this is about 3x the bps charge.
- Outcome-blind cost in R (learn, section 7): median stop 13.7 bp (1h), 20.7 bp (4h); mean round trip 0.043 R (1h),
  0.031 R (4h) before overshoot; about 0.17 R (1h) and 0.11 R (4h) all-in on a stop exit. Gross edge needed for
  "target several times the round trip": the median target is 0.96 R (1h), 1.24 R (4h), so the rule pays about
  4-7 % of a winner in costs. Cost is not what decides this card; location is.

## 6. Populations, statistics and the reading rule (written before any outcome)

- **Primary population:** every replayed candidate of the arm and of the control (the amendment's review item 6:
  "comparisons are per candidate ... never totals"), overlapping positions allowed. Reported beside it, never gating:
  the engine's one-at-a-time population (`max_open: 1`, `run_arm.limit_open`).
- **Statistic:** per timeframe, arm mean net R (primary cost), control mean net R, and **Delta = arm - control**.
- **Standard errors:** paired stationary day-block bootstrap (Politis and Romano 1994): session days (New York 17:00
  roll) resampled jointly for arm and control, mean block 5 trading days (1h) and 10 (4h), about 2.5 holds, 2,000
  draws, seed 20261124 (+1 for 4h). This carries the overlap of 48-bar/24-bar holds that the power prior cannot.
- **Learn verdict per timeframe** (Holm over the 2 timeframes, rank-1 z 1.96 applied to both, stricter than Holm):
  - **CONTINUE** (eligible for the confirm read after quant signature) needs all of:
    L1 t(Delta) >= 1.96; L2 t(arm net) >= 1.96; L3 stress net of the arm > 0; L4 arm net > 0 in >= 5 of 8 years and
    Delta > 0 in >= 5 of 8 years.
  - **STOP** if SE(Delta) <= 0.06 R and (the one-sided 90 % upper bound of Delta < +0.05 R, or that of the arm's net
    < 0). STOP closes EVIDENCE-RB-HISTORY for that timeframe: RB1H/RB4H do not return to demo; any red-box variant
    (filters, exits, other boxes) needs a new mechanism, not a new cut (R-10 precedent).
  - **INCONCLUSIVE** otherwise: no confirm read, no arm; the retirement stands (the burden is on the rule).
- **Confirm (stage 2, only for CONTINUE):** the same statistics on 2018-01..2025-09; pass = BH-FDR q 0.05 over the
  continuing timeframes on one-sided p(Delta) and p(arm net), stress > 0, the deflated Sharpe at the registry's
  K reported (not gated). Labelled CONFIRM-SEEN (section 1).
- **test_old (stage 3, only after a confirm pass):** arm net > 0 and Delta > 0 at one-sided p <= 0.05 on 2008-02..
  2009-12, n_eff >= 200 (ADR 0085 fresh-data floor). Only then may a demo learning or qualification card cite this.
- **Reported, never gating:** gross means, stop/target/time-exit shares, by-year table, median stop in bp, the
  one-at-a-time population's net and Delta, and every number at both costs.

## 7. Power check (ADR 0085, `power_first.evaluate`, settings delta 0.10 R, sd 1.2 R, rho_day 0.25)

Outcome-blind learn counts (`rb_history.py count`, 2010-2017, 120-day warm-up, after the common filters, stop band and no-next-box):

| | RB1H | RBC1H | RB4H | RBC4H |
|---|---|---|---|---|
| candidates | 8,245 | 7,891 | 3,193 | 3,040 |
| session days with a candidate | 2,065 | 2,119 | 1,549 | 1,567 |
| one-at-a-time lower bound (full hold blocks) | 822 | 835 | 419 | 435 |

Gates: learn z 1.96, confirm z 1.96 (7.75 y), test_old z 1.645 fresh (2 y, extrapolated from the learn rate; test_old
not read).

| Population | TF | design effect | n_eff learn | power learn / confirm / test_old | joint | ADR 0085 | MDE80 arm (z 1.96) | MDE80 Delta (z 1.96) | MDE80 Delta at t 3.6 |
|---|---|---|---|---|---|---|---|---|---|
| every candidate (primary) | 1h | 1.75 | 4,716 | 1.00 / 1.00 / 0.89 | 0.89 | **start** | 0.049 R | **0.069 R** | 0.110 R |
| every candidate (primary) | 4h | 1.27 | 2,523 | 0.99 / 0.98 / 0.67 | 0.66 | **start** | 0.067 R | **0.095 R** | 0.150 R |
| one-at-a-time lower bound | 1h | 1.00 | 822 | 0.67 / 0.65 / 0.33 | 0.15 | refuse | 0.117 R | 0.166 R | 0.263 R |
| one-at-a-time lower bound | 4h | 1.00 | 419 | 0.40 / 0.39 / 0.21 | 0.04 | refuse | 0.164 R | 0.232 R | 0.368 R |

MDE80 = (z + 0.842) x 1.2 / sqrt(n_eff); Delta x sqrt(2) (two independent samples, conservative against the pairing).

**Result: powered on the primary population** (both timeframes detect Delta <= 0.10 R at 80 % power under the rule of
record), so stage 1 runs. Caveat, stated before the read: overlapping holds correlate candidates across days beyond
the rho_day prior, so the true n_eff lies between the two rows. The read does not rely on the prior: the day-block
bootstrap measures the SE, and the STOP rule only fires when the realised SE(Delta) <= 0.06 R; otherwise the verdict
is INCONCLUSIVE, never a pass by default. The one-at-a-time population is under-powered and is reported only.

## 8. K and budget

- **K +2** (one registry row per timeframe, k_eff 2; the control is the comparison inside each row, as in H-93). The
  confirm and test_old stages read the same two configs and add no K. Booked on the brain registry of record with the
  learn result (2026Q4: 3,416 of 3,500 used, 84 left; after this card 82). This lane cannot write the brain registry:
  the release step books it (`TrialRegistry.record`, `rule_ref` this file); if `assert_budget` refuses, the learn
  result is void and reported as such.
- Multiple-testing ledger: the family's earlier looks (R-15, H-80 R2, ML-M2, the week) are already in K; this card
  adds 2.

## 9. Owner-only flags

None taken. A return of any red-box arm to demo would be a research-director decision on a passed card (demo is
delegated); real money, sizing and budget stay owner-only.

## 10. Results (stage 1, learn) — appended after the run, below this line, in a separate commit

Run 2026-11-24 on commits `77e39caf` (card) and `712bf54b` (job cache, mechanical), `rb_history.py run --window
learn` then `read`; per-trade output kept local (`~/tmp/rb-history/`, never committed). Entries 2010-01-04..2017-12-28.
Confirm, test_old and the holdout were not read.

**Primary population (every replayed candidate), primary cost (bps), paired day-block bootstrap:**

| TF | n arm / control | gross arm / control | net arm (95 % CI) | net control | Delta (95 % CI) | SE(Delta) | t net / t Delta | stress net arm | years net > 0 / Delta > 0 | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 1h | 8,245 / 7,891 | -0.046 / -0.038 | **-0.159** (-0.187, -0.132) | -0.151 | **-0.008** (-0.046, +0.031) | 0.020 | -11.4 / -0.39 | -0.378 | 0 / 4 of 8 | **STOP** |
| 4h | 3,193 / 3,040 | -0.072 / -0.022 | **-0.162** (-0.214, -0.109) | -0.107 | **-0.054** (-0.120, +0.011) | 0.034 | -6.0 / -1.63 | -0.340 | 0 / 4 of 8 | **STOP** |

STOP fires on both legs of the rule: SE(Delta) <= 0.06 R, UB90(Delta) +0.018 R (1h) and -0.012 R (4h), both < +0.05
R, and UB90(net) < 0 (-0.141, -0.127). No CONTINUE condition holds.

Reported, not gating:
- One-at-a-time population (`max_open: 1`): 1h n 8,089, net -0.163, Delta -0.018 (SE 0.021); 4h n 3,096, net -0.160,
  Delta -0.058 (SE 0.037). Brackets are tight (median stop 13.5 bp 1h, 20.6 bp 4h), so trades rarely overlap and the
  slot limit barely binds: the section 7 lower bound (822 / 419) was far too conservative, and the realised SEs confirm
  the primary power row.
- Exits: stop 49 % / target 51 % (1h), 55 % / 45 % (4h); time exits under 0.5 %. The rule is close to a 1:1 bracket
  around the break; the random ladder with the same spacing does as well or better.
- By year, net arm / control (1h): 2010 -0.206/-0.177, 2011 -0.137/-0.163, 2012 -0.184/-0.167, 2013 -0.153/-0.156,
  2014 -0.160/-0.162, 2015 -0.144/-0.162, 2016 -0.094/-0.076, 2017 -0.208/-0.140. (4h): 2010 -0.223/-0.101, 2011
  -0.015/-0.048, 2012 -0.296/-0.162, 2013 -0.134/-0.136, 2014 -0.121/+0.015, 2015 -0.186/-0.253, 2016 -0.219/-0.095,
  2017 -0.081/-0.084.
- Consistent with the red-box week (pooled replay -0.15 R vs control -0.17 R): history and forward agree.

**Verdict (stage 1): STOP at 1h and 4h.** EVIDENCE-RB-HISTORY is closed "no edge": the red boxes' placement adds
nothing over random boxes with the same spacing (Delta CI excludes +0.05 R at 1h and +0.02 R at 4h), and the rule
loses about 0.16 R a candidate net. RB1H/RB4H do not return to demo. Confirm and test_old stay unread for this family
(not needed: no CONTINUE). Any red-box variant needs a new mechanism, not a new filter, exit or timeframe (R-10).

**15m (finding asked "include 15m or state why not"):** not tested; deferred by post-hoc judgement (written after the
learn read, quant review 2026-11-24). R-15 tested a filtered `box_break_close` with ATR brackets and H-80 R2 read only
2018-25, so neither closes the red-box rule at 15m. RB15's week read was -0.077 vs -0.061 and a 15m stop is about half
the 1h one (costs in R about double), so a 15m read is not worth K now; any 15m return needs its own card.

**K:** +2 booked on the brain registry 2026-11-24 (trials 3451 RB1H and 3452 RB4H, status evaluated, rule_ref this file); 2026Q4 3,418 of 3,500 used, 82 left.
