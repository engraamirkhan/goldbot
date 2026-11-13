# Amendment 2026-11-13: faster survivor reads for Z10D, Z9D and Z5P (twins, one history read, sequential looks)

Status: **written 2026-11-13, before any data it decides on is read.** No twin has filled; the 2010-2017 replay of
Z5, Z9 and Z10 has not been run (section 2 says why that window is clean); no sequential look has been taken.
Amends `learning-2026-11-02.md` (the 300-fill read of Z10D, Z9D, Z5P) and closes H-90
(`prereg-2026-11-12-london-signal-arms.md`, amended `prereg-amendment-2026-11-13-h90-cost-budget.md`). Backlog 188.
Owner request 2026-11-13: bring the survivor reads forward. Nothing in the RiskGate, the per-trade caps, the daily
loss limits, the daily entry budgets (`exploration.daily`), the slots or the sizing changes. No owner-only decision
is taken. Research director, 2026-11-13.

## 0. Where the time goes (re-derived from the brain's `aux.db` `exploration_obs`, read-only, 2026-11-13)

| arm | admitted to date | candidates a trading day, 11-09..11-12 | admitted a day | refused: capacity (day cap, session, hour, budget) | refused: market (spread, 02-07 window, tick/stale data) |
|---|---|---|---|---|---|
| Z10D | 58 | 8.5 | 3.0 | 4.8 | 0.8 |
| Z9D | 40 | 8.0 | 3.0 | 2.0 | 3.0 |
| Z5P | 44 | 6.2 | 3.5 | 2.2 | 0.5 |

- The rules fire at their 2010-17 census rate (Z10 7.3, Z9 7.4, Z5 6.8 a day). The slow pace is **admission**,
  not signal scarcity. On icm-demo, 11-10..11-12, the 30-a-day scalp cap was spent by Z3D 22, LCZ10D 18, ZCD 15,
  LCZ9D 12, LCZ3D 11, Z10D 7 and Z9D 5 fills.
- Z3D, LCZ3D, ZCD (icm-demo) and Z1P, Z2P, LCZ1P, LCZ2P (icm-probe) were stopped on 2026-11-13
  (`prereg-amendment-2026-11-13-h90-cost-budget.md`, `profitability-2026-11-12.md` (a)). That frees about half of
  each host's scalp cap for the three survivors and their controls without raising any cap. This is the main lever.
- At 3.4 fills a trading day, 300 is 14-19 weeks away. With the capacity refusals gone it is about 6-9 weeks.

## 1. More fills: same-rule twins on the other gold accounts

### 1.1 What is registered

Each twin copies its original's rule body exactly (`twin_of`: rule, timeframe, params, max_open, checked at load),
under its own id and hash; each has a dedicated random control that copies the original control's body except the
`control` name. Learning class, minimum lot, £6.50 per-trade cap, £130 loss cap each, `since` 2026-11-16, `until`
2026-12-17 (the originals' end, so all rows of a rule share one read date).

| twin | original | account | config_hash | k_id | control | control config_hash | control K row |
|---|---|---|---|---|---|---|---|
| Z10P | Z10D | icm-probe | `52dba4bd59224d12` | `101623dde0a7` | LCZ10P | `148c5702a38ab470` | `db683ba51c3a` |
| Z9P | Z9D | icm-probe | `29aface33c3b42a5` | `42678e7bb425` | LCZ9P | `a84a26dd06a1edb8` | `691b8e627460` |
| Z5D | Z5P | icm-demo | `3a4d665ccf827bbe` | `e74ef1b2749d` | LCZ5D | `a8e94b8916450e99` | `36bb72dc54db` |

- **Gate before the first twin entry:** the section 2 history read is not KILL for that rule, and its two K rows are
  written on the brain (`scripts/record_learning_k_rows.py --budget`, then the write). Until both hold, the loader
  refuses the rows (fail closed), so merging this config sends nothing.
- **Vantage is not registered.** H-81 section 7.2 sets the bar: cost/R <= 0.08 at the 95 % upper bound once Vantage
  has 200 own fills. Cost of record `cor-2026-10-31.2` has 194 Vantage fills; the London and New York round-trip
  upper bounds ($0.45, $0.378) give 0.100 R and 0.084 R on a $4.50 stop. The bar fails, and a stop that the cost eats
  is the same leak class as the SUGAR stops. Vantage twins of these rules are registered by a later dated amendment
  only when a cost-of-record version shows >= 200 Vantage fills and every session's upper bound <= $0.36.

### 1.2 How the accounts pool (decided now)

- **The unit is the distinct signal event:** (rule, decision close, side). Both ICM accounts read the same broker
  feed, so a twin fires on the same bars as its original. A same-broker event filled on two accounts is one
  observation: its net R is the mean of the two fills. Counting it twice would inflate n without adding information.
  The twin's real gain is the events its original was refused on (capacity, a terminal's tick or stale-data error).
- **Controls:** each control has its own hash seed, so its fills are independent random draws. The control fills of
  a rule's rows pool by session x side cell.
- **Strata:** account is the stratum for cost (each fill net of its own broker's measured costs; both strata are ICM
  today) and for a heterogeneity check. If the two accounts' mean net R differ by more than 2 day-clustered SE, the
  read is flagged "execution-split" and the evaluator reports each account separately before any verdict.
- **Statistic:** per event i of rule a in session s, side d: x_i = R_i - mean(R of a's pooled control fills in
  (s, d)), with the H-90 section 5 fallback (fewer than 30 control fills in a cell: both-sides mean of the session;
  above 25 % fallback the read cannot promote). Mean of x_i, standard error clustered by opportunity day (22:00 UTC
  boundary). Censored fills are counted and reported apart, never in n (`learning-2026-11-02.md` quant ruling (5)).
- **Money stop across rows:** if the summed net P&L of a rule's rows since their `since` reaches -£130, every row of
  that rule retires that day (research director's daily check). Each row's own £130 engine cap still applies.

### 1.3 Expected pace and read dates

Assumptions, stated so they can be checked: capacity refusals fall to near zero from 11-16; market refusals stay as
measured; twins add about 0.5 distinct events a day. Trading days from 2026-11-16; 12-25 and 01-01 closed.

| rule | admitted now | expected distinct events a trading day | 100 | 200 | 300 |
|---|---|---|---|---|---|
| Z10 (Z10D + Z10P) | 58 | 6.5 | 2026-11-24 | 2026-12-15 | about 2027-01-08 (continuation rows) |
| Z9 (Z9D + Z9P) | 40 | 5.5 | 2026-11-30 | about 2026-12-24 (continuation) | about 2027-01-21 (continuation) |
| Z5 (Z5P + Z5D) | 44 | 5.5 | 2026-12-01 | about 2026-12-23 (continuation) | about 2027-01-21 (continuation) |

- **Pace check 2026-11-20:** if Z10's distinct events for 11-16..11-20 average below 5 a trading day, the evaluator
  reports why on backlog 188 (no cap is raised by this amendment).
- **Continuation (decided now, not on the data):** a learning row cannot run past 45 days. A rule still in the
  *continue* region of section 3 on 2026-12-17 continues with continuation rows: new ids, the same rule body,
  `since` 2026-12-18, `until` at most 45 days later, registered by a config-only change citing this section, and
  their K rows written before their first fill. A rule that stopped (futility, promotion, money stop or the section 2
  kill) gets none.

## 2. One history read, 2010-2017, kill-or-support (cannot promote)

### 2.1 Which windows are clean for Z5, Z9 and Z10

| window | status for these three rules | used here |
|---|---|---|
| 2010-01..2017-12 (learn) | **Clean for returns.** H-81 computed fire rates only ("no return of any rule was computed", section 5). A5 lists Z5, Z9, Z10 as "no replay" (`prereg-amendment-2026-10-30-kill-rules.md`). H-90 leg H has not run. | **yes, once** |
| 2018-01..2025-09 (confirm) | **CONFIRM-SEEN** by trigger class (EVAL-2 general rule; H-90 section 6 marks it so since 2026-11-02). Corroboration only, never decides. | no |
| 2008-01..2009-12 (TEST-OLD, intraday) | Clean, and reserved as the L3 primary confirm for an arm that passes forward. Reading it now would leave no clean confirm window. | no, stays sealed |
| 2025-10-01..2026-09-30 (holdout) | Sealed. | no |

### 2.2 The run (existing runner, no new code)

`scripts/run_arm_backtest.py --arm <id> --block 2010-01-01:2017-12-31 --bars raw --calendar raw/calendar
--screen-broker icm --cost-version cor-2026-10-31.2`, for Z10D, Z9D, Z5P and their controls LCZ10D, LCZ9D, LCZ5P
(the twins share their originals' history: `runnable_ids`). The live rule code on the hashed rows, the spread gate
and send delay, entries at the next 1m open, stop first, ICM cost of record by session (`net_r_cor`), overshoot per
the table's ruling. EVAL-2 is not used, so `scripts/eval2_selftest.py` is not a precondition.

### 2.3 Reading rule (per rule)

E = mean `net_r_cor`(arm) - mean `net_r_cor`(its control), 90 % interval by a day-block bootstrap (opportunity day,
4,000 resamples, seed 20261113). Years: the count of 2010..2017 with E > 0.

- **KILL** (binding): mean `net_r_cor`(arm) < 0 **and** the 90 % upper bound of E < +0.05 R. Reason: an edge over
  random below 0.05 R (about one ICM round trip on a $4.50 stop) cannot pay its cost, so the forward excess of
  +0.2-0.4 R would be noise plus drift. The rule's original, its twin and both controls retire that day
  (`retired_day`, `retired_rule` naming this section). Its twin never enters.
- **SUPPORT:** the 90 % lower bound of E > 0, mean `net_r_cor`(arm) > 0, and E > 0 in at least 5 of 8 years.
  Recorded on the live-candidate list as "history-supported". It changes no bound in section 3 and promotes nothing.
- **NEUTRAL:** anything else. The forward read decides, unchanged.
- Reported beside the verdict (descriptive, no decision): gross R, net R by session and side, n entries.

## 3. Sequential looks at 100, 200 and 300 distinct events

- **Information fraction** t = n / 300 distinct admissible events of the rule (section 1.2). Looks at n = 100, 200,
  300. If 2026-12-17 (or a continuation row's end) comes first, the look is taken there at t = n / 300.
- **Statistic:** Z = mean(x) / SE (section 1.2), one-sided.
- **Promotion bound:** Lan-DeMets O'Brien-Fleming spending at one-sided alpha = 0.025 / 3 = 0.0083 per rule
  (Bonferroni over the three rules), alpha(t) = 2 - 2 Phi(z_(1-alpha/2) / sqrt(t)). At equal looks the bounds are
  **Z >= 4.37 at 100, >= 3.03 at 200, >= 2.41 at 300** (simulated, 2 million paths); off-schedule looks recompute
  the bound from the spending function.
- **Promotion also requires:** mean net R of the arm > 0 at its own broker's measured cost; x > 0 on both long and
  short events; not "execution-split"; fallback share <= 25 %; section 2 not KILL. A promotion moves the rule to the
  **live-candidate review** (`live-candidates.md`), nothing more. It still needs TEST-OLD 2008-09 (L3 primary),
  every cost cell at >= 30 fills, and the live gate (DSR >= 0.95 at the full registry K), which this amendment does
  not change.
- **Futility (non-binding, so it costs no alpha):** stop the rule if **Z < 0 at 100** or **Z < 0.75 at 200**. A
  stopped rule retires that day with its twin and controls.
- **Honest power** (per-event SD 1.1 R, one control fill per event, day-cluster design effect 1.06):

| true excess | promoted | stopped for futility |
|---|---|---|
| 0 R | 0.8 % | 80 % |
| +0.10 R | 9 % | 50 % |
| +0.20 R | 37 % | 21 % |
| +0.30 R | 76 % | 6 % |

  A dead rule most likely stops at 100 or 200, which saves 100-200 slots for other work; only a large real edge
  promotes early. The H-82 prior on neighbouring 5m entries (gross within +/-0.011 R) says the likeliest outcome is a
  stop.

## 4. H-90: closed today

H-90's F leg has not started: its quant sign-off and K rows do not exist (`prereg-amendment-2026-11-13-h90-cost-budget.md`
section 1). After the cost-budget cut it expects about 80 London fills by 2026-12-17, and its power is about 20 %
even if the full +0.21 R discovery gap holds. Its expected verdict is FAIL or LEAD, never PASS. Its population
(Z5P, Z9D, Z10D, GS1) is read anyway by this amendment and by GS1's own rule. H-90 is **closed for design futility,
before any leg read**: its three K rows are never written (K +0). Its leg H is not run. The section 2 replay reports
net R by session as a description only, never as an H-90 read. "London-only for signal arms" is reopened only with a
new mechanism, a new card and a K row.

## 5. K and the registry

- **+3 trials:** the three section 2 history reads, one per rule (a read is a read). Controls' runs are not trials.
- **+6 learning K rows**, written only for a rule not killed in section 2 (two per rule: twin and its control), under
  the registry's one-row-per-learning-row rule. They test no new hypothesis; the pooled read is one test per rule.
- **Sequential looks:** inside each rule's single pre-registered test, paid for by the alpha spending; not added to K.
- **H-90:** its reserved +3 are released unused.
- Every look and verdict is recorded on backlog 188 with n, Z, the bound and the decision, losers beside winners.

## 6. Not changed

Caps, slots, daily entry budgets (icm-demo 70, icm-probe 85, scalp cap 30 each), the RiskGate, the per-trade caps,
the daily and weekly loss limits, the cost table version, the holdout, the live gate, every exit. `login` stays null.
Rollback: drop the six rows from `config/exploration_arms.yaml` and `config/accounts.yaml`, restart (open positions
keep every exit).

## 7. Sign-off

- Research director: this amendment; runs section 2 and records the verdict before any twin K row is written.
- Evaluator: applies sections 1.2 and 3 at each look (independent, read-only).
- Quant-reviewer: reviews the pooling unit, the spending bounds and the section 2 rule.

## 8. Result of section 2 (run 2026-11-13, after commit 81529730 froze sections 0-7)

`run_arm_backtest.py` at origin/main f589c94b, block 2010-01-01:2017-12-31, ICM cost of record `cor-2026-10-31.2`,
tier-1 blackout on (859 windows). E and its 90 % interval by the section 2.3 day-block bootstrap (4,000, seed
20261113), computed by the research director from the per-trade output (local, not published; sha256 prefixes
Z10D a3aeb410f73f30aa, Z9D c3d1c4cf19afeebb, Z5P 287617e840aad00e, LCZ10D 2aa61fd8a6a8d658, LCZ9D 68eaa5af6bb01efe,
LCZ5P aa5d6957585339c8).

| rule | entries (control) | gross R | net R at cost of record | control net R | E | E 90 % | years E > 0 | verdict |
|---|---|---|---|---|---|---|---|---|
| Z10 | 14,988 (34,116) | -0.009 | -0.066 | -0.056 | -0.009 | [-0.016, -0.002] | 2 of 8 | **KILL** |
| Z9 | 15,384 (31,266) | +0.003 | -0.055 | -0.048 | -0.006 | [-0.015, +0.003] | 2 of 8 | **KILL** |
| Z5 | 13,964 (33,892) | +0.006 | -0.051 | -0.055 | +0.004 | [-0.004, +0.011] | 4 of 8 | **KILL** |

- Descriptive: net R is -0.046..-0.067 in every session (Asia, London, New York) and on both sides for all three.
  No session rescues them, which also answers H-90's question for these rules without reading it as H-90.
- Reading: the three rules' gross on 2010-17 sits within +/-0.01 R of zero, the same bound H-82 found for the other
  H-81 families. The forward +0.2-0.4 R excess over 40-58 fills was noise plus the November drift.
- **Applied the same day, as section 2.3 requires:** Z10D, Z9D, Z5P, LCZ10D, LCZ9D and LCZ5P retire on 2026-11-13
  (`retired_day`, `retired_rule` in `config/exploration_arms.yaml`); open positions keep every exit. The twins Z10P,
  Z9P, Z5D and their controls are **not added to the spec**: they never enter, so no K row is written for them
  (a row in the spec would make `record_learning_k_rows.py` write six unused K rows). Their ids stay reserved here.
- **Section 3 is moot:** no rule is left to look at. The 5m learning class on gold now has no active arm.
- **K:** +3 (the three reads). The six learning K rows: +0. H-90: +0.
