# Pre-registration: H-105 (U1), the gold forward window for the frozen sr-v3 / zoo-v2 learners

Research director, 2026-11-28. Owner approval of 2026-11-28 for the three fresh routes named in H-104 §10
(`docs/research/prereg-2026-11-28-zoo-v2-dl.md`, PR #521). H-104's Stage 0 of record refused all 38 cells on S0-3
(evaluator verdict on PR #521, 2026-11-28). This card is route **U1**: ADR 0105 fallback (ii), a forward window from
2026-10-01 in the ADR 0085 N12 reserve weeks.

**Label-blind.** No forward label, score or outcome of any learner below exists. No sealed OOF score (`oof_scores.parquet`,
`v_*`/`k_*` columns) was opened. Inputs: the served tiers file (sha256 ff653ea7...), the v0 artifact files (sha256 only,
plus the L-cw column count), the `p30_ok`/`day`/`year`/`tf`/`side` columns of the six `oof.parquet` files (the evaluator's
projection, re-run) and a weekday calendar.

**Status: proposed.** Quant-reviewer and evaluator sign. This card books **0 K**. Real money, sizing and live unlock
stay owner-only. Nothing here touches the engine, the RiskGate or any account.

## 0. Money first

**What this card is (quant review of #523):** a **closure and stamp record**, UNTESTABLE today, not a hypothesis card.
It fixes the window, the reserve-week rule, the v0 stamps, the forward grade and the forward cost rules, and records
that no cell can reach a powered read.

Gold on dense candidates at 15m-4h loses its cost and nothing else (H-104 §0, seen): take-all net −0.95 R (15m) to
−0.13 R (4h) per candidate, mean cost(x) 0.95 R to 0.13 R. 1d+1w: net −0.007 R, cost 0.054 R. A forward window does
not change that economics. It only replaces the missing fresh history for gold 2019-2025 (grade C under r11-v2.1,
ADR 0105). So the question this card answers is narrow: **can a forward window from 2026-10-01 give these frozen learners
a powered judged read, and when?** Answer, from label-free counts (section 5), under ADR 0085 as written: **not before
2044-10 (15m, m 4; L-cw alone 2043-09); never at 30m-1d+1w.**

## 1. Cells (learner × unit), Holm m fixed now

Units and learners as H-104 §1 defines them, plus L-cw (the sr-v3 served learner). Each cell is frozen at its v0
artifact trained on 2010-17 (sha256 prefix of the artifact file in `~/tmp/sr-v3-run-2026-11-27-xtv1/out/<unit>/`,
re-derived for this card; they equal H-104 §1):

| Unit | L-cw (v0) | G-cw (v0) | HB (v0) | AVG (v0) | SEQ-1 | CAS-1 | CAS-2 | Cells = **Holm m of the unit's batch** |
|---|---|---|---|---|---|---|---|---|
| 15m | a1d13f84 | 1b476f62 | 6599cf7e | e7f294d8 | — | — | — | **4** |
| 30m | 4885e6df | d4885e4b | 6599cf7e | 32239702 | — | — | — | **4** |
| 1h | 045fa655 | 98a98b16 | 6599cf7e | 488397f2 | deferred | — | — | **5** |
| 2h | 7534d952 | 9b5f9892 | 6599cf7e | 234c020e | deferred | — | — | **5** |
| 4h | d373fde3 | aef609a1 | 6599cf7e | 9de0a5aa | deferred | deferred | deferred | **7** |
| 1d+1w | eb04a6f5 | deferred (no v0) | 6599cf7e | deferred (no v0) | deferred | deferred | deferred | **7** |
| **Total** | 6 | 6 | 6 | 6 | 4 | 2 | 2 | **32** |

- **L-cw is intercept-only at every unit** (all units are below L3; its v0 has 0 columns, re-derived from `L-cw.json`).
  So L-cw = B8 here: a per-unit level minus cost(x). On learn it keeps nothing at 15m-4h (seen).
- **Deferred cells** have no v0 artifact today. Their window starts on the first Monday of an odd ISO week after their
  artifact sha256 is recorded in an amendment to this card (ADR 0085: forward replay is frozen before data). They are
  fit-only on 2010-17 (0 K, ADR 0085 A2). A deferred cell without an artifact at its unit's read enters with p = 1.
- **Holm m (evaluator finding 2):** each unit is read as **its own batch** on the ADR 0085 ledger, with the fixed m in
  the last column. A cell that is NOT STARTED or ABSTAIN at the read enters its batch with p = 1. m never changes with a
  start-dependent condition. If two units fall due at the same month-end they are still two batches (two ledger slots,
  slower unit first).
- Not in scope: RL-X1 and agg-v1 (H-104 §5, §6: their populations are dense entries with zero gross; a forward window
  does not change that), gold 1m/5m (D-15), every non-gold class.

## 2. Window, population and data

**Window.** Decisions from **2026-10-01 00:00 UTC**, in **N12 reserve weeks only** (odd ISO week numbers; ISO week 41
is the first, 2026-10-05). **End:** the unit's read date (section 5) or the kill date, whichever is first. Rows whose
label horizon ends after the last frozen graded month are dropped, not truncated. Even weeks may train learning versions
(sr-v3 step 4) and are never judged here.

**The holdout seal reaches into the window (finding, label-free).** A forward row is usable only if every month its
columns read is graded, and holdout months (2025-10..2026-09) are sealed and ungraded (`holdout_guard`; ADR 0104 §4,
"ungraded months are never usable"). So the first usable decision is 2026-10-01 + the column set's input lookback
(`dense_labels.input_lookback`):

| Population | 15m-2h | 4h | 1d | 1w |
|---|---|---|---|---|
| P30 (G-cw, HB, AVG, CAS-1; evaluator finding 1) | 2027-02-06 (128.8 d) | 2027-04-08 (189 d) | 2029-11-08 (1,134 d) | 2042-04-10 (5,670 d) |
| L-cw (0 columns: label window only, rev 9 D1) | 2026-11-01 (first month with a complete prior forward cost month, section 3) | same | same | same |
| SEQ-1 (64 raw bars) | 2026-10-01 + 64 bars × 7/5 | same rule | same rule | same rule |
| CAS-2 | intersection of G-cw and SEQ-1 rows | | | |

Comparisons between learners use the intersection of their rows, with the count reported.

**Data source and grade.** Dukascopy XAUUSD 1m bid + ask (the trained source; labels at mid, side-aware barriers, T2
judged head, xt-v1 exits sha256 b68e2f7b...). Each forward month is graded under **r11-v2.1 unchanged** (rule blob
c6f283d7) against the **icm-demo MT5 M1 reference** (ADR 0105 fallback (ii), quant K2/K3), which must pass (a)-(c) in
that month, from the **frozen copy** (day 8 after month-end, `broker_capture.frozen_copy_for_grade`, PR #471; its sha256
in the tiers rows). A month without a complete capture, or failing (a)-(c), is C and out. Forward tiers are appended as
new rows to a new served-tiers version; no 2003-2025-09 row changes (no A2 tier change). Grading is label-free. No
fallback-(ii) grade runs until `learning:broker_m1_capture` has passed at least once on the brain.

## 3. Cost of record (reconciled with the srv3-cost-2b3628372a9b3bfa holdout rule)

- `srv3-cost-2b3628372a9b3bfa` cannot charge forward rows correctly. Its era term joins the gold spread table as of
  `available_utc`, and the table stops at 2025-09 (holdout months are refused). A 2027 row would silently take the
  2025-09 spread: finite, so `gold_row_bp` does not raise. **That is a stale charge, not a refusal (code-path finding).**
- ADR 0098 R-H says "a holdout read needs a new version". The same holds for the forward window. **The forward cost of
  record is a new version, `srv3-cost-<sha>` (forward)**, built by the frozen builder with the same rule, the same 54
  gold cells, the ECR term rolled mechanically (below), and the gold spread
  table extended with forward months only (Dukascopy quoted spread ub95 per session and month, ≥ 2026-10, each month
  frozen at day 8 like the grade). Holdout months are never in it. It is recorded on an ADR 0098 R-H line before the
  first U1 read. It is this window's version of record from its first read (no earlier read of the window exists, so
  no K +1 applies); a second version on the same window is K +1 and the first read stands.
- **ECR over a window of many years (decided): a mechanical monthly roll, fixed now.** For each calendar month from
  2026-10, the ECR ub95 per broker × session × tf cell is taken over the trailing 3 calendar months of demo fills
  (≥ 30 fills, else the session `all` cell, else the previous frozen month carries), frozen at day 8 after month-end
  with its sha256, and joined as of `available_utc` like the spread. Reason: `ecr-2026-11-20.2` measures four weeks of
  2026 fills. Holding it for years would charge 2030s rows with 2026 broker conditions, and the size and sign of that
  drift are unknown. A roll that is fixed before any forward label exists is point in time and leaves no choice to
  shop. The 2x stress gate still applies.
- Code item (implementer, backlog 280): "`gold_row_bp` refuses a row whose as-of spread row has `available_utc` more
  than 40 days before `decision_utc`; forward spread months carry `available_utc` = their day-8 freeze time; history
  months keep their existing `available_utc`. Test: a 2027-02-03 row against a table whose last month is 2026-12
  (frozen 2027-01-08) charges; against a table ending 2025-09 it raises." (quant, verbatim)

## 4. Hypotheses and honest priors (rule 2 card)

Common terms as H-104 §2: cost per candidate from H-104 §0, £/R at 0.01 lot ≈ ATR14 USD / 1.27, S(k) under the
offset form, ρ needed for S = +0.05 R at σ 1.5: 15m 0.55, 30m 0.29, 1h 0.23, 2h 0.20, 4h 0.169, 1d+1w 0.12 (H-104,
largest measured price IC 0.05).

| Id | Hypothesis | Mechanism | Expected gross per kept trade | Expected net £/day at 0.01 lot | Prior P(confirm pass, ≥ 1 cell) |
|---|---|---|---|---|---|
| H-105a | The frozen 2010-17 learners keep their (small) ranking skill on 2027+ gold | Volatility state and cost avoidance persist; the price level and ATR in R change the cost(x) ratio | as H-104a-h: S ≈ 0 at 15m-4h, ≈ +0.01 R at 1d+1w | ≈ £0 at 15m-4h; about £1/day at 1d+1w | < 1 % (power, section 5) |
| H-105b | L-cw (intercept-only) keeps where level − cost(x) > 0 | A cost-and-level filter; at 2026 gold prices cost in bp is lower, so the keep share may rise | ≈ +0.0 R | ≈ £0 | < 1 % |

Rule 2: no 15m-4h cell meets gross ≥ 2 × cost (H-104 §2). This card exists to fix stamps before the data, not because
the edge is expected.

## 5. Power and the earliest read (ADR 0085, label-free)

- **Read rule.** One read per unit, at the first month-end (read on day 8 after it, once the month is frozen and graded)
  where the unit's graded n_eff on its P30 population is ≥ max(200, n_req), with n_req from power_g ≥ 0.5:
  Φ(0.10·√n / sd − z) ≥ 0.5, sd = 1.2 R (`sd_r_measured_by_tf` is null, so the floor binds), z = Holm rank 1 of the
  unit's m at that read's ledger alpha (α_j = 0.05·2^−y/4, ledger year y from 2026-11-15), and simulated joint power
  ≥ 0.25 (`power_first.joint_power`). **No interim read.** n_eff is counted from graded reserve days only, never from
  labels.
- **n_eff per reserve session day** (sr-v3 2.2 rates, ρ_day 0.25): 15m 1.96, 30m 1.30, 1h 1.00, 2h 0.50, 4h 0.20,
  1d+1w 0.15. Reserve session days: about 130 a year.
- **n_req by ledger year** (m of the unit): m 4: 1,258 (y1), 1,440, 1,625, 1,811 (y4). m 5: 1,316, 1,500, 1,685,
  1,871. m 7: 1,405, 1,589, 1,775, 1,962. Each year the alpha halves and n_req grows by about 185.

**Projected graded n_eff (P30, every month graded A, reserve weeks):**

| Unit (m) | by 2027-06-30 | by 2027-12-31 | by 2028-12-31 | n_eff ≥ 200 only (gate 11) | **Power ≥ 0.5 reached** |
|---|---|---|---|---|---|
| 15m (4) | 98 | 225 | 478 | 2027-11 | **2044-10** (read 2044-11-08, ledger year 18, z 5.58) |
| 30m (4) | 65 | 149 | 316 | 2028-04 | **never** (checked to 2045) |
| 1h (5) | 50 | 115 | 244 | 2028-08 | **never** |
| 2h (5) | 25 | 58 | 122 | 2030-03 | **never** |
| 4h (7) | 6 | 19 | 45 | 2035-01 | **never** |
| 1d+1w (7) | 0 | 0 | 0 | 2044-04 | **never** |

- L-cw (label-window population, from 2026-11): 15m reaches power 2043-09; every other unit never.
- **Sensitivities, not adopted:** a single cell (m 1) at 15m on reserve weeks reaches power 2038-10. Using even weeks
  too (not allowed by N12 for judged rows) would give 15m 2029-08 and 30m 2032-07 at m 1.
- **Why:** n grows linearly (about 255 a year at 15m), and n_req grows by about 185 a year because ADR 0085's ledger
  halves α_j every year. At every unit slower than 15m the alpha schedule outruns the data.

## 6. Reading rule for a unit that reaches power (fixed now; predicted never to run)

- Population and cost: section 2 and section 3. "S at the worse-broker cost" stated in every read.
- **Gates:** sr-v3 5.3 gates 1-8 (gate 7: B0, B1, B3, B7 twin by day, side and hour, B8, and buy-and-hold over the
  window after one round trip, ADR 0106 decision 4), 11 (fresh n_eff ≥ 200), 13 reported, 14 at 15m (R-T broker leg).
  H-104 §4 gate 8 (paired D vs G-cw on shared rows) for HB, AVG, SEQ-1, CAS-1 and CAS-2. Gate 12 cannot be met by a
  forward window (it needs 2021 or 2022); its forward form is **two disjoint halves of the window, each with kept mean
  net > 0 and ≥ 10 kept trades a year spanned** (ADR 0085 decision 3, pre-registered blocks). "The halves split at the
  calendar midpoint of the judged window as of the read date (no chosen split point), and each half spans at least 12
  months." (quant, verbatim)
- Stationary day-block bootstrap (BOOT 10,000, mean block 5 days, seed 20261206). Holm within the unit's batch at m of
  section 1. Every read reports S by half, the S split, £/day at 0.01 lot, and the losers next to the winners.
- A pass licenses only what sr-v3 5.3 licenses (learning-class demo gating). Real money: owner-only.

## 7. Predicted Stage 0 (the evaluator runs and records it; S0-3 first)

| Cells | S0-1 (preconditions) | S0-2 (ρ needed at σ 1.5, reported) | **S0-3 (record reason)** | Predicted |
|---|---|---|---|---|
| L-cw, G-cw, HB, AVG at 15m (4) | v0 present; forward cost version and forward grades absent | fail (0.55) | power ≥ 0.5 first at 2044-10 | NOT STARTED |
| same at 30m (4) | as above | fail (0.29) | never | NOT STARTED |
| 1h, 2h (10, incl. SEQ-1 deferred) | as above; SEQ-1 unfitted (#489) | fail (0.23, 0.20) | never | NOT STARTED |
| 4h (7, incl. SEQ-1, CAS-1/2 deferred) | as above; SEQ-1, CAS unfitted | pass (0.169) | never (P30 n_eff 45 by end-2028) | NOT STARTED |
| 1d+1w (7) | G-cw, AVG no v0; SEQ-1, CAS unfitted | pass (0.12) | P30 population empty until 2029-11 (1d) / 2042-04 (1w); never | NOT STARTED |

**Predicted: 0 of 32 cells start, 0 K.** Record reason S0-3 (ADR 0085) at every cell.

## 8. K, budget, kill rule, dates

- **K:** 0 now. Worst case 32 (one per cell read, in the quarter of its unit's read). Predicted 0.
- **Budget (code-backed only, evaluator finding 3):** 2026Q4 80 free on the stale brain snapshot of 2026-11-24 (the
  ops-VM registry is the count of record); 2027Q1 `trial_budget_quarter` 20, reserved 8, no override on main: 12 open.
  U1 books nothing in either quarter.
- **Kill (quant F5 form):** if the Stage 0 of record leaves every cell NOT STARTED (power), U1 closes **UNTESTABLE on the
  gold forward window under ADR 0085** the same day. No read, not a NULL, N10 not invoked. What stays recorded: the
  window start, the reserve-week rule, the v0 stamps of section 1 and the forward cost and grade rules. "The stamps fix
  definitions, not blindness. A later card reading these rows lists every look at reserve-week outcomes (harm
  sequences, performance-analyst, dashboards) made before its own signature, and its read is booked as a fresh look
  under ADR 0085, not as this card's pre-registration." (quant, verbatim)
- Reopening needs a new card with new mechanism evidence and a count in K. A changed alpha schedule is the quant's
  ruling, never this card's.

| Date | Step | Owner |
|---|---|---|
| 2026-11-28 | Card | research director |
| last signature, no later than 2026-12-04 | Stage 0 recorded (projected counts above); closure fires that day if predicted | evaluator; research director |
| 2026-12-05 | Oct and Nov broker M1 frozen copies with sha256 in the D-1 status (ADR 0105 deadline), whatever U1's verdict | data-engineer + sre |

## 9. Integrity

- Written before any forward label exists for these learners. The evaluator confirms at Stage 0 that the dl-v1
  consumption ledger and `window_reads.jsonl` hold no row with a decision at or after 2026-10-01 for any section 1
  learner.
- Holdout 2025-10-01..2026-09-30 sealed: no minute of it is read, as a label or as a feature lookback (section 2).
- Controls: B7 twin by day, side and hour on every started cell. Deflation at the registry K of the read day, reported.
- **Re-derived for this card:** P30-usable OOF days per unit from the six `oof.parquet` files with the column projection
  `['tf','day','year','p30_ok','side']` only: 15m 614, 30m 614, 1h 611, 2h 605, 4h 421, 1d+1w 0 (equal to the evaluator's
  PR #521 counts). The tiers-file method used in section 5 and in U2 gives 603 / 603 / 600 / 597 / 419 / 0 on the same
  years, within 2 %. The v0 sha256 prefixes in section 1 were recomputed from the artifact files.

## 10. What this exposes (rule 1 gap)

A forward window is not a route to a judged gold read at these gates in any useful time. The binding constraints are
(1) the ADR 0085 alpha schedule against about 130 reserve days a year, and (2) the holdout seal on P30 lookbacks. The
money lever stays where H-104 §10 put it: cost per candidate, longer holds, and populations with shown gross.

## Sign-off

- Research director: 2026-11-28.
- Quant-reviewer: CHANGES on #523 (closure framing, §0 dates, 40-day era-spread refusal, stamps not blindness,
  gate-12 split, ECR decision), applied verbatim 2026-11-28; re-verify pending.
- Evaluator: pending (Stage 0 of record).
