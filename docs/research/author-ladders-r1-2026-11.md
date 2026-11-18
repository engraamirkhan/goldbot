# Author level ladders: audit, power and R1 (results file of prereg-2026-11-09-kog-box-ladder.md)

Card frozen at `6e77ff6b` (2026-11-15). Aggregates only: no idea text and no author price level is in this file
(third-party content; the repo is public). Sample files, hand reads and per-post output live in
`~/tmp/author-ladders/` on the Mac only.

## 1. Extractor audit (card 2.1), one attempt per author

- Extractor `goldbot/research/ladders/extract.py`, sha256
  `f9e8a5c1dfec076768e8c9b1c8bf9b9e4723d6016da64b6b6724337422e50885`, frozen at commit `01c4b212` before the sample
  was drawn. Developed only on the stored posts of 2026-10-01..10-27.
- Posts behind the parsing notes in `extract.py` (ruling 2026-11-15): the implementer's development set, published
  2026-10-01..10-27, and the coordinator's descriptive read of posts published 2026-10-25..11-14. Every one of them
  was published after the sealed holdout; **none is a BC post (2020..2025-09)**.
- **The hand reader was the extractor's author** (the implementer), disclosed as the ruling requires.
- Sample: `random.Random(seed).sample(30)` over the unit's R1 pool, ids ascending: spot gold, not education, K 4h,
  G 1h + 4h, published 2020-11..2025-09, unedited (see 4). Seeds K 20261109, G 20261110 (the card's). Excluded: 179
  spent ids (audits 1-3 and the four T-1 seeds; audit 1 and seed 20261102 reproduced from their seeds, checked
  against audit 2's listed ids).
- Hand read: by the implementer, from the sample file only (text, P0, W*, band; no later price, no extractor output),
  written before `audit-score` ran the extractor on the sample. Hand files sha256: K `c7a6d2d6...353841c`,
  G `cf15de16...3698fa`. Limits of this blindness: the reader wrote the extractor, so knew its rules though not its
  output, and a language model's general knowledge of gold's path cannot be switched off.

| Author | Extracted | Matched | Precision (>= 95%) | Hand | Matched | Recall (>= 85%) | Coverage (not abstained) | Forward-only precision / recall (sensitivity) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| KnightsofGold (U-K) | 209 | 179 | **85.6%** | 240 | 180 | **75.0%** | 73% | 71.8% / 71.4% | **FAIL** |
| Goldviewfx (U-G) | 202 | 195 | **96.5%** | 209 | 195 | **93.3%** | 90% | 73.8% / 92.0% | **PASS** |

- **U-K is parked to the Q1 refresh, with no iteration** (2.1 "A fail parks that unit"). Why it failed: 26 of the 30
  unmatched extracted zones and 55 of the 60 missed hand zones are in the 6 sampled posts that carry K's daily
  level lists ("Support: a / b / c / ..."). Rule 2 reads "A/B" as a pair, so a list of separate levels within W*
  of each other becomes zones. This is the card's rule, not a coding slip: a Q1 card needs a rule for slash lists.
- **U-G's extractor is admitted** by this audit (a dated amendment, 2026-11-15). Its forward-only precision is 74%:
  a quarter of G's extracted levels are targets the post marks as already hit. The card's ladder includes them (no
  recap rule is registered); R1 reads the card's ladder as frozen.

## 2. Power check (owed before R1; outcome-blind), U-G only

Read on 2026-11-15 before any outcome under this rule: `scripts/author_ladders.py power` prints events and the stop
and target distances only; no exit, R or P&L was printed or kept. To be exact (ruling 2026-11-15): the command ran
`rules.run_unit`, which simulates every exit internally (max_open 1 needs each exit time to know which later
candidate is busy), so exits were computed in memory; none was printed, stored or read.

| Item | Value |
|---|---|
| Admitted G posts (1h + 4h, unedited, >= 3 levels) | 1,175 (of 1,316 on 1h/4h: 119 fewer than 3 levels, 22 education) |
| Candidates / events after caps and refusals | 3,584 / **1,478** (1h 1,083, 4h 395) |
| Left out | busy 1,059, post cap 681, no target 179, stop outside $3-40 178, clock (C1) 8, near stop 1 |
| Events by year | 2021: 29 (G starts 2021-10), 2022: 391, 2023: 358, 2024: 405, 2025: 295 |
| Median stop / target distance | $8.29 / $8.24 |
| Days with an event | 794 |
| **Power, one-sided 0.05, 0.10 R edge vs control (a), day-clustered** | **0.76** (bar >= 0.5: met) |

Method: under a driftless null an event's R has mean 0 and variance T/S (target over stop distance); events of one
day are treated as perfectly correlated; time exits are ignored (they shrink the variance, so 0.76 is a floor).
The noise of the 200-replicate control mean is ignored (small next to the author mean's).

Note for the read: 2021 holds 29 events, so the card's "net > 0 in >= 3 calendar years including 2021 and 2022" rests
on a thin 2021.

## 3. C2 edited-post sets, fixed before the R1 run (ruling 2026-11-15)

The card's C2 check (live K posts with the edit flag: first snapshot vs latest version) needs the brain's
`state/tv_ideas.db` versions; the Mac copy holds one version per idea and the brain is not reachable from this lane.
Per the ruling, fixed here before R1 runs:
- **PRIMARY = unedited-only** G posts (every post updated after it was available is dropped: 100 G posts).
- **SENSITIVITY = all posts at their original text** (unedited plus note-edited).
- Both are computed in the same run; one K row; **P8 binds on the sensitivity set** (Delta_a > 0 and net > 0).
In the Mac copy every TradingView update time matches an author note within 2 min (0 unmatched for K and G; the card
expected about 2%).

## 4. R1 under the re-issued rule (the first hand-back's block, resolved)

The first hand-back stopped here: card 4.4 required a re-issue under EVAL-2 (ADR 0076). The quant-reviewer re-issued
and signed the U-G rule on 2026-11-15 (`author-ladders-r1-ruling-2026-11-15.md`, P1-P8, FAIL/INCONCLUSIVE bounds,
R3 withdrawn). The required code changes landed before the run: control (b) breaks read the ladder at bar i - 1 and
targets at the lock bar, with prefix-invariance tests at 1h and 4h; the truncation test covers 1h/4h, the ladder_fn
path and the P4 twins; the gross t and SE(Delta_a) use max(post-clustered, session-day-clustered); the P4 twins
harness and its test. R1 results follow in section 5 (run once).

## 5. R1 result, U-G (run once, 2026-11-15, harness at 252ef301): **FAIL**

Primary set = unedited-only (section 3). BC window: fills 2021-10..2025-09, every event admitted by the C1 clock rule.
Costs: C4 as written plus floored point-in-time swap. SE = max(post-clustered, session-day-clustered).

| Condition (ruling 2026-11-15) | Value | Met |
|---|---|---|
| Events | 1,478 (1h 1,083, 4h 395) from 1,175 posts | |
| P1 net mean R > 0 | **-0.099 R** (SE 0.030, t -3.31) | no |
| P2 Delta_a > 0, p_a <= 0.05 (200 random ladders) | Delta_a **-0.019 R** (replicates' mean -0.080), p_a 0.74 | no |
| P3 Delta_b > 0, p_b <= 0.05 (`sr_channels` midpoints, 2,011 events, net -0.097 R) | Delta_b **-0.002 R**, p_b 0.54 | no |
| P4 excess over 20 matched twins > 0, p <= 0.10 | **-0.015 R** (1,477 events with twins, twins' net -0.084 R), p 0.67 | no |
| P5 gross t >= 2 | gross **-0.061 R**, t -2.04 | no |
| P6 stress net > 0 (median spread paid $0.38, no shortfall) | -0.118 R | no |
| P7 year units, net > 0 in 3 of 4 incl. 2021-22 | 2021-22 -0.199, 2023 -0.062, 2024 -0.095, 2025 Jan-Sep -0.009 | no (0 of 4) |
| P8 other edit set (all posts, 1,530 events) | net -0.109 R, Delta_a -0.029 R (p_a 0.85) | no |

- **FAIL triggers** (either suffices; both hold): one-sided 90% UB of Delta_a = **+0.019 R < +0.03 R** with SE(Delta_a)
  0.030 R <= 0.055 R; 90% UB of net = **-0.061 R < 0**. **U-G is retired as a signal source** (R3 was withdrawn by
  the ruling; no arm).
- Strata (reported only): 1h -0.134 R (gross t -2.71), 4h -0.004 R (gross t 0.40).
- Control (c), report-only: random-side twin net -0.057 R; author minus twin -0.042 R.
- Exits: 46% target, 47% stop, the rest time or weekend. Median stop $8.29, median target $8.24.
- Already-hit sensitivity: 1,371 of 1,478 events have their level or target crossed by the 1m mid in the 5 UTC
  weekdays before `available_utc`; the 107 left: net -0.071 R, excess over twins +0.064 R (n far too small to read).
  G's levels sit about one daily range apart, so a 5-weekday window touches nearly all of them; the sensitivity cannot
  separate already-hit targets from the rest.
- Deflated Sharpe at real K 21,333 (daily net R, reported, not gated): SR -0.118 per day, SR0 0.145, DSR 0.000.
- **GBP per day at 0.01 lot: -£0.78** (net -$0.72 per trade, 1,478 trades over 1,023 weekdays, GBPUSD 1.33).
- Reading: the author's levels lose about as much as random ladders with the same spacing (-0.080 R) and as our own
  box midpoints (-0.097 R); the lock rule itself has a negative gross before added costs (-0.061 R). There is no
  evidence that where Goldviewfx draws levels adds anything to an EMA5 cross-and-lock at those levels.

## 6. K accounting (section 8)

| Row | U-K | U-G |
|---|---|---|
| Extractor audit (2.1) | 0 (audit, no trial) | 0 |
| Exposed-set descriptive read (4.1) | booked by the coordinator's descriptive lane (+4 in the program ledger for the 3-week read); not repeated here | same |
| R1 | **0: unit parked by the audit** | one row, k_eff 2, owed: booked only if `assert_budget` passes for 2026Q4 (registry on the brain; not booked by this lane) |
| R3, arms | not reached | not reached |

Nothing was written to the research registry or the trial budget by this lane; the U-G R1 row is owed to the registry of record.
