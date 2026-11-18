# U-G R1 reading rule, re-issued under EVAL-2 (quant-reviewer, signed 2026-11-15)

Re-issue required by `prereg-2026-11-09-kog-box-ladder.md` 4.4 (EVAL-2 adopted by ADR 0076 before R1 started).
**Operative parts as relayed by the coordinator on 2026-11-15; the full signed text is
appended below.** Nothing below was changed by the implementer.

## Operative

PASS needs ALL of P1 net mean R > 0 (C4 cost as written); P2 Δa > 0, p_a ≤ 0.05 (200 reps); P3 Δb > 0, p_b ≤ 0.05
(paired ISO-week bootstrap 2,000); P4 NEW EVAL-2 twins: mean excess over 20 matched twins > 0 at one-sided p ≤ 0.10
(twin = same TF, side, server hour ±1h, drawn from decision bars in the 7 days after the event's exit excluding every
bar where a GEL candidate fires pre-caps; stop/target/hold = event's ATR multiples and bar count at the twin's own
ATR14; same entry lag and costs; C1 clock rule; seeded (idea_id, event_no, twin idx); replay only; p via stationary
day-block bootstrap mean block 5 session days, 2,000 draws, event resampled with its twins); P5 gross t ≥ 2 with SE =
max(post-clustered, session-day-clustered); P6 stress net > 0 (stress adds the shortfall if median spread paid <
$0.129); P7 year units 2021-22 block, 2023, 2024, 2025 Jan-Sep: net > 0 in ≥ 3 of 4 incl. the 2021-22 block; P8 the
other edit set: Δa > 0 and net > 0. FAIL if one-sided 90% UB of Δa < +0.03 R with SE(Δa) ≤ 0.055 R, or 90% UB of net
< 0. Else INCONCLUSIVE (no arm; BC spent). Control (c) report-only. Already-hit targets counted as is; sensitivity: drop
events whose level or target the 1m mid crossed in the 5 UTC weekdays before available_utc. Deflated Sharpe at real K
reported, not gated. K: one row, k_eff 2, only if assert_budget passes for 2026Q4. R3 withdrawn (owner line: "U-G
cannot reach a real-money confirm within about 4 years at a 0.10 R edge; an R1 PASS means a demo learning arm only").

Required before R1 (code + tests): (a) rules.py:191-193 + author_ladders.py:271: control (b) break test uses
ladder_fn(post, i-1), target uses ladder_fn(post, lock_k); test
test_control_b_levels_at_bar_k_are_identical_when_bars_after_k_are_removed at 1h and 4h; (b) parametrize the
truncation test over 1h/4h, ladder_fn path and the P4 twins; (c) author_ladders.py:277 day-clustered SE and use the
max, also delta_a_ub90 at :312; (d) build the P4 twins harness + test
test_twins_match_tf_side_hour_and_atr_multiples_and_are_drawn_after_exit_excluding_family_fire_bars. Results file:
state the date range of the posts behind the extract.py parsing notes and confirm none is a BC post 2020..2025-09;
record the power command ran run_unit (exits simulated, no R printed); disclose the hand reader was the extractor
author.

C2: the brain check is not available to this lane, so per the ruling PRIMARY = unedited-only, SENSITIVITY = all posts
at original text; both computed in the same run, one K row, P8 binds on the other set. Fix this choice in the results
file BEFORE the run.

## Full signed text (quant-reviewer, 2026-11-15), appended unchanged in substance

### 1. U-G (GEL) R1 reading rule, re-issued under EVAL-2 / ADR 0085 and signed

The card's 4.4 exception applies: ADR 0076 covers every family registered after 2026-11-09, and this card froze on
2026-11-15. This rule replaces the R1 and R3 rows of 4.4. Everything else in the card stays as frozen: the extractor at
sha `f9e8a5c1...`, the rules in 3.2, the caps, and controls (a) and (b).

- **Population:** U-G events pooled across 1h and 4h, from `rules.run_unit` with the card's caps. The edit set used is
  fixed in section 3. The 1h and 4h strata are reported but do not decide.
- **Statistic:** mean net R per event at the card's C4 cost.
- **Cost basis:** C4 as written: Dukascopy bid/ask spread as paid, plus $0.144 per round trip, plus $0.145 on each stop
  exit, plus swap for every rollover strictly inside the hold, never credited.
  - This is at least the EVAL-2 deciding cost and at least the 2026-11-15 parity upper bound ($0.140).
  - The Dukascopy spread for 2021-25 is above ICM's $0.086, so the read is conservative by roughly 0.01-0.03 R.
  - The stress line adds the shortfall to every round trip when the median spread paid at fills is below $0.129
    (1.5x ICM's spread).
- **PASS** requires all of P1-P8 (see Operative above). P4 adopts EVAL-2's matched twins, the same "timing, not drift"
  test that EVAL-2 L3 uses. It is needed because Δa and Δb share the 2022-25 gold drift with the author, while P1 does
  not. P5 uses SE = max(SE clustered by post, SE clustered by session day). P7 pre-registers 2021-22 as one block,
  because 2021 has only 29 events.
- **FAIL** retires U-G as a signal source: a hypotheses.md R-row, no arm, no further back-catalogue read. It applies if
  either:
  - the one-sided 90% upper bound of Δa is below +0.03 R with SE(Δa) ≤ 0.055 R (the EVAL-2 futility rule); or
  - the one-sided 90% upper bound of net mean R is below 0.
- **INCONCLUSIVE** is any other outcome. It gives no arm, the forward shadow stays descriptive only, and the back
  catalogue is spent for U-G (no re-read under any changed rule).
- **Power (EVAL-2 L0)** is met and was recorded before the read:
  - 0.76 at 0.10 R on the lane's conservative day-perfectly-correlated basis;
  - about 0.90 on the ADR 0085 basis (sd 1.2, ρ_day 0.25, m_day 1.86, n_eff ≈ 1,216).
  - This power floor replaces the 1,000-event floor.
- **Multiplicity:** m = 1. The P1-P8 intersection-union is not Holm-adjusted, because R1 is a screen. The deflated
  Sharpe at the real K is reported, not gated.
- **What a PASS buys:** only the section 5 learning-class arm and its control (ADR 0061, kill rule unchanged), plus
  shadow. Never promotion.
- **R3 is withdrawn.**
  - Holm m = 2 at 0.05 would be a second alpha pot, which ADR 0085 forbids.
  - At about 370 events a year, a confirm at δ 0.10 R needs about 3.9 years. So once ADR 0085 merges, the promotion
    confirm is booked as "NOT STARTED (power)" (K 0); until then the forward read is EVAL-2 L4 e-processes only.
- **Owner line:** "U-G cannot reach a real-money confirm within about 4 years at a 0.10 R edge. An R1 PASS means a demo
  learning arm only."
- **K:** one registry row, k_eff 2, booked only while `assert_budget` passes for 2026Q4; else R1 moves to 2027Q1.
  - The twins and P8 are ANDs inside the same row, so they add nothing.
  - The exposed-set read is booked separately.
- **Already-hit targets:** primary counts them as they are. Every level is known at `available_utc`, which is
  look-back, not look-ahead. The report-only sensitivity drops a level if the 1m mid crossed it in the 5 UTC weekdays
  before `available_utc`. The hand "forward-only" tag is not used.

### 2. Leakage and code review

Items a-d were required before R1 and are done on this branch:
- **(a)** the control (b) break test reads the ladder at i-1;
- **(b)** truncation tests over 1h/4h, the ladder_fn path and the twins;
- **(c)** max(post, day) clustered SE;
- **(d)** the P4 twins harness.

The rest:
- **(e) Audit hand reader:** the reader was the extractor's author. This is accepted for R1 with disclosure, because
  extraction error biases toward the null. It is not acceptable for any future audit.
- **(f) Parsing notes:** the date range is stated, and no note comes from the back catalogue.
- **(g)** The power command ran `run_unit` with exits simulated and no R printed. This is benign and recorded.
- **Sound:**
  - the holdout is refused at post construction and at minute load;
  - C1 clock admission is in place;
  - P0 and W* are point in time;
  - EMA and ATR are causal;
  - entry is at lock close + 60 s;
  - the stop is taken first;
  - swap is strict-interior and never credited;
  - notes never reach the extractor;
  - the extractor hash was frozen before the sample.

### 3. C2 fallback

Unedited-only drops posts depending on a later note, which is information from after publication. The sign of the bias
is unknown, up to about ±0.03 R.
- If the C2 check runs and passes, primary = all posts at original text, and sensitivity = unedited-only.
- If it cannot run or mismatches, primary = unedited-only, and sensitivity = all posts.
- Both sets are computed in one run, under one K row, and P8 makes the other set a must-hold AND. The primary is fixed
  before the run.

### 4. U-K: a new card now (KBL-2), with conditions

1. **A new card**, quant-signed before any new post is drawn. Its section 0 records the supersession and the KBL-1
   audit failure.
2. **Exactly one change: a slash-list rule.**
   - Rule text: "Three or more price tokens joined only by '/' are separate singletons. A lone two-token 'A/B' stays a
     pair under rule 2."
   - It ships as a new frozen file with its own hash. G's extractor stays untouched.
   - Development uses only the exposed set and the 6 failing posts (format only).
3. **A fresh audit:**
   - a new seed;
   - every id ever read is excluded;
   - an independent staff hand reader who did not write the extractor;
   - 95% precision and 85% recall on 30 random posts, plus recall ≥ 85% on a seeded stratum of 10 slash-list posts;
   - one attempt only. A fail parks U-K until the Q1 refresh.
4. **Before any outcome:** a power check of ≥ 0.5 at 0.10 R on the ADR 0085 basis, and enough 2021-22 coverage for P7.
5. **Reading rule:** section 1, with U-K's rules from 3.1 and control (b) as the boxes themselves.
6. **K:** the audit costs 0; R1 costs +1 (k_eff 1) inside the 2026Q4 budget, otherwise it moves to 2027Q1.
