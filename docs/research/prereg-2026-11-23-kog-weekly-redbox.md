# KnightsofGold weekly RED BOX TARGETS (KWR): pre-registration card (2026-11-23)

Research director, 2026-11-23, written before any price after any KoG weekly post was read under this rule. Modelled
on `prereg-2026-11-09-kog-box-ladder.md` and the EVAL-2 reading rule in `author-ladders-r1-ruling-2026-11-15.md`
(P1-P8). This file holds no idea text and no author price level (they stay in `state/tv_ideas.db` on the brain and
in the Mac's `raw/`).

**Status at commit: R1 NOT STARTED.** Two independent refusals, each sufficient (section 6):
1. the extractor audit fails recall on the back catalogue (26.7 % < 85 %); precision passes (100 %);
2. the ADR 0085 power rule refuses the back-catalogue read even at its upper-bound sample (power 0.19 < 0.5,
   fresh-data n_eff 86 < 200).

The rules in sections 3-5 are fixed by this commit. They govern the forward shadow (section 7), which is
descriptive only and never a promotion source.

## 0. Why a new card, and why this is not a re-run

- **Owner input, 2026-11-23:** "every sunday the ideas are also published from knightsofgold". The system has never
  tested KoG's posted weekly levels:
  - The U-K unit (`KBL`) read KoG's 4h idea posts and their free-text box lists. Its extractor failed the audit
    (precision 85.6 %, recall 75.0 %) and U-K was parked to the Q1 refresh (`author-ladders-r1-2026-11.md`).
  - The red-box week (`prereg-amendment-2026-11-15-redbox-week.md`) traded our own `sr_channels` replica, not KoG's
    levels. Verdict: no edge against random ladders.
- **This is a different object.** It reads only the fixed-format "RED BOX TARGETS" block of the Sunday post titled
  "THE KOG REPORT". It does not read the free-text box lists that failed under KBL. The side comes from the block
  (break above = long, break below = short), and so do the levels and the target ladder. The extractor is fresh code
  (`goldbot/research/kog_weekly.py`) and shares nothing with `ladders/extract.py`.
- **The KBL park stands.** Nothing here reopens U-K or KBL-2. If this unit were ever to pass, it would count in K
  separately.

## 1. Mechanism and cost-aware expected edge (research rule 2)

- **Mechanism (why gold could pay it).**
  - Osler (2003, *JF*): stop orders cluster just beyond published levels, and crossing a cluster speeds up the move.
  - KoG publishes to a large retail audience. A break of a published level could set off that audience's stops and
    entries, which is a flow-driven continuation.
  - Prior P(pass) is about 10 % (judgement). Generic box breaks are dead in our data (box family R2, red-box week),
    so any edge has to come from where KoG draws the levels.
- **Geometry, measured from the hand labels (text only, no price).**
  - The back catalogue (BC) has 45 weekly posts with a valid block and 86 setups.
  - Median box width (break-above level minus break-below level): $13 (IQR $10-15).
  - Median distance from the break level to the first target: $6.5.
  - At the stop of section 3, 1 R is about $15-20.
- **Cost per trade (C4, ICM, `cost-of-record-2026-10-31.md`).**
  - Spread as paid on 1m bid/ask, plus $0.144 per round trip, plus $0.145 on each stop exit. That is about $0.30-0.38,
    or **0.02 R**.
  - Gross edge needed at twice cost: **0.04 R**. The card clears rule 2 on cost.
  - It fails on frequency and power (below).
- **Trades and money.**
  - At most 2 setups a week, so about 0.2-0.3 trades a day.
  - At 0.01 lot, 1 R ≈ £12-15. A +0.10 R net edge is worth **about £0.3-0.5 a day**.
  - goal_edge_R (ADR 0085, information only) is 29 R a trade.
  - This is an evidence line about an author the owner follows. **It is not a route to £50 a day.**

## 2. Extractor (frozen before the audit) and its audit

- **Code.** `goldbot/research/kog_weekly.py` at commit `c05af693`, sha256 `d5000eba…0ee32e12`. It was frozen
  before any BC post was read.
  - It was developed only on the 7 exposed weekly posts of 2026-10-04 .. 2026-11-22, with 14/14 setups read exactly.
    This is development, not evidence.
  - Grammar and abstain rules are in the module docstring:
    - weekly post = author KnightsofGold, title exactly "THE KOG REPORT", published on a Sunday (UTC);
    - exactly one header line that starts "RED BOX TARGETS";
    - full-line grammar `BREAK ABOVE|BELOW <price> for <list>`;
    - a monotone ladder, within ±15 %;
    - anything else abstains, with the reason counted.
- **Input and availability.** The input is the post's first snapshot (earliest `tv_idea_versions.seen_utc`; for BC,
  the original description). Author notes are never input. Availability is `available_utc`: publish + 900 s for BC,
  first sight for live posts.
- **C2 edit check (it ran and passed).** Every stored KoG post, live and BC, has exactly one description version.
  The `edited` flag (60 of 96 BC weekly posts) marks author notes only. So primary = all posts at original text, and
  the sensitivity = unedited-only (ruling section 3).
- **Audit universe.** All 96 KoG weekly reports published 2023-09-17 .. 2025-09-28 (the BC before the holdout).
  - The 2025-10-01 .. 2026-09-30 posts are in the sealed holdout and were not read.
  - The first weekly red-box block is dated 2024-09-29. Before that the posts carry a "bias for the week" block,
    which is a different object and out of scope.
- **Hand truth.** One setup = side, break level and full target ladder, as written. A ladder that is non-monotone as
  written (an author typo) is "invalid" and excluded from truth. There are 6 such sides, and the extractor also
  abstains on them.
  - The hand reader read every red-box block, whatever its header wording ("RED BOXES:", "Red boxes", "RED BOX
    TRADERS", "KOGS RED BOX TARGETS", "RED BOXES INDI LEVELS").
  - The labels were written before the extractor ran on any BC post. Hand-file sha256 prefix: `13a8c5a2e87bb164`
    (kept local).
  - **Disclosure:** the hand reader also wrote the extractor. The 2026-11-15 ruling (e) says this is not acceptable
    for a future audit. So this result can refuse admission but cannot grant it: an independent staff re-read is
    required before any admission.
- **Matching.** A setup matches only if the side, the level and every target are equal.

| Set | Posts | Hand setups | Extracted | Exact | Precision (≥ 95 %) | Recall (≥ 85 %) | Verdict |
|---|---|---|---|---|---|---|---|
| BC, all red-box blocks (decides) | 96 | 86 | 23 | 23 | **100 %** | **26.7 %** | **FAIL (recall)** |
| BC, posts whose header reads literally "RED BOX TARGETS" (report only) | 12 | 23 | 23 | 23 | 100 % | 100 % | – |
| Exposed 2026-10/11 (development, not evidence) | 7 | 14 | 14 | 14 | 100 % | 100 % | – |

- **Why recall fails.**
  - 33 of the 45 BC posts with a block use a header other than "RED BOX TARGETS". That is every post with a block
    from 2024-09-29 to 2025-06-15 except one (2025-02-23), and 2 in August 2025.
  - The literal header is KoG's format from 2025-06-22 onward: 12 BC posts (11 of the 13 posts with a block in that
    span, the other 2 in August 2025 using another header, plus the one earlier post of 2025-02-23), and 7 of 7
    exposed posts. Counts re-checked 2026-11-23 against the stored first snapshots; they reconcile with the table.
- **Consequences.**
  - No iteration on the BC: it is spent as an audit set.
  - A header-variant extractor (kogw-2) could only be audited on forward posts not yet seen. It is not proposed,
    because the BC read is refused on power anyway (section 6).
  - kogw-1 stays as the forward shadow's reader. Its forward abstains and misses are counted each week against an
    independent hand read (section 7).

## 3. Rules (fixed now; no tunable parameter)

- **Setups.** The extractor's setups for one weekly post. A setup is used only if both sides of its post were
  extracted, because the stop needs the box.
- **Validity window.** From `available_utc` to the broker's Friday close minus 5 minutes of the first broker trading
  week with bars after `available_utc` (the broker's server clock, NY+7: Friday close 24:00, window end 23:55).
  - A post that becomes available after Friday's close is valid for the next week.
  - A later weekly post does not cut the window short; one post covers one week.
- **Signal (primary 1h; 15m reported as a stratum).** The first bar of that timeframe that closes strictly beyond
  the break level, after `available_utc` and inside the window:
  - long when close > the ABOVE level;
  - short when close < the BELOW level.
  - Bars are aligned to the broker server clock and use mid closes.
  - Each side fires at most once per post. Both sides may fire in one week, one after the other or overlapping. Each
    is its own event.
- **Entry.** At the open of the first 1m bar at or after the signal bar's close + 60 s: ask for a long, bid for a
  short. No entry if that bar is at or after the window end (refused and counted).
- **Stop (chosen before any outcome): the opposite break level**, i.e. the far edge of KoG's red box.
  - A long's stop is the post's BELOW level; a short's is its ABOVE level.
  - R = |fill − stop|. If R is below $3 the event is refused and counted. There is no upper cap.
- **Targets: the ladder, in equal parts.**
  - Targets at or behind the fill price are dropped. If none are left, there is no trade (counted).
  - With n targets left, 1/n of the position exits at each target as the 1m bid (long) or ask (short) reaches it,
    filled at the target price on touch.
  - The stop never moves.
  - Whatever is left exits at the window's end at the last 1m close before it: bid for a long, ask for a short.
    Weekend-flat therefore applies, and swap is charged for each rollover strictly inside each part's hold (triple
    on the triple day, never credited).
  - Net R is the sum of the 1/n parts, each in units of the initial R.
- **Outcome resolution.** On 1m bid/ask, the first barrier decides. If the stop and a target fall in one 1m bar, the
  stop is taken first.
- **Costs.** C4 as written (section 1): the round-trip commission and extra once, on the full size; the $0.145 stop
  add-on on the fraction stopped. Stress is 1.5 × the added costs, plus the shortfall when the median spread paid is
  below $0.129. The row's `r_stress` carries the 1.5 × added costs; the reader applies the shortfall from the median
  `spread_usd` of the events read, divided by each event's `risk_usd`.
- **Refusal codes** (counted, each a row): `one_side_only`, `extractor_abstain` (no setup extracted), `no_signal`,
  `r_below_3`, `no_targets_left`, `entry_after_window`, `no_bars` (a hole in the minutes still there 5 weekdays after
  the twins' week closed).
- **Holdout (C1 clock rule).** A BC event is admitted only if fill minute + window end < 2025-10-01 00:00 UTC; every
  bar read, including the twins, is before 2025-10-01 00:00 UTC.

## 4. Controls

- **(a) Random-shift ladders, matched.**
  - Per post, 200 replicates seeded on (idea_id, r). The whole block (both levels and both ladders) is shifted by
    U(−w, +w), where w is the post's box width.
  - Every replicate is run on every side and timeframe, also where the author's side did not fire or was refused
    (stored on that row too); a replicate that does not fire or is refused counts as no event.
  - The rule is identical. Δa = author mean net R − replicate mean; p_a = (1 + #{r: mean_r ≥ author}) / 201.
- **(b) Dropped, with the reason.** Our `sr_channels` boxes were the red-box week's object (no edge against random).
  The claim here is KoG's placement against the same geometry at random places, which is (a).
- **P4: EVAL-2 matched twins**, as the 2026-11-15 ruling defines them:
  - min(20, eligible) per event, drawn without replacement; the count (`twin_n`) is stored. A 1h event has at most
    15 eligible bars (3 server hours on 5 weekdays);
  - the same TF, side and server hour ±1 h;
  - drawn from the decision bars of the 7 days after the event's exit, excluding bars where a KWR signal fires;
  - stop and target distances are the event's ATR14 multiples at the twin's own ATR;
  - the same entry lag, costs and clock rule; no $3 floor on a twin's R (its distances are the event's, which passed
    it).
- **(c) Random-side twin.** The same fill, stop and targets, with the side from a seeded coin. Report only. Heads keeps
  the event; tails takes the other side at the same entry minute, with the stop and every target mirrored about its
  own fill at the event's distances.
- **Seeds and twin geometry.** Control (a), (c) and the P4 draws use
  `numpy.random.SeedSequence([zlib.crc32(str(idea_id).encode()), r])`, with r = the replicate (0-199) for (a),
  1000 + s for the twins and 2000 + s for the coin, where s = 2 × tf (1h 0, 15m 1) + side (long 0, short 1). The
  twins' ATR14 (simple mean of 14 true ranges) is on the event's timeframe, and each ladder target is mapped as its own
  ATR multiple.
- **Amendment 2026-11-23 (implemented controls; before any forward outcome).** The four bullets above were made exact
  to match `goldbot/research/kog_weekly_shadow.py` after quant and code review: twins min(20, eligible), seed streams,
  control (a) on every side, control (c) mirrored about its fill, no twin floor, refusal codes `extractor_abstain` and
  `no_bars`, and the stress shortfall applied by the reader. No forward week had been scored.

## 5. Reading rule (EVAL-2, P1-P8; written before any outcome)

- **Population and statistic.** The 1h events decide; the 15m stratum is reported only (k_eff 2). The statistic is
  mean net R per event at C4.
- **PASS needs all of:**
  - **P1:** net mean R > 0.
  - **P2:** Δa > 0 with p_a ≤ 0.05.
  - **P3:** none, because control (b) was dropped. It is replaced by (c) reported.
  - **P4:** mean excess over the 20 twins > 0 at one-sided p ≤ 0.10 (stationary day-block bootstrap, mean block 5
    session days, 2,000 draws).
  - **P5:** gross t ≥ 2, with SE = max(clustered by post, clustered by session day).
  - **P6:** stress net > 0.
  - **P7:** net > 0 in both halves, 2024-09/2025-03 and 2025-04/2025-09, because the BC spans one year. This is
    weaker than the 3-of-4-year rule, and section 6 makes it moot.
  - **P8:** the other edit set has Δa > 0 and net > 0.
- **FAIL** if the one-sided 90 % UB of Δa < +0.03 R with SE(Δa) ≤ 0.055 R, or the 90 % UB of net mean R < 0.
- **INCONCLUSIVE** otherwise. It gives no arm, and the BC is spent.
- The deflated Sharpe at the real K is reported, not gated.
- **K:** one registry row, k_eff 2, booked only while `assert_budget` passes for the quarter. Applies only to a
  future scored run; KWR-1 is booked `not_started_power`, n_trials 0.

## 6. Power (ADR 0085) and K: the refusal

- **Basis.** `power_first.evaluate` at the repo settings (delta 0.10 R, sd 1.2, rho_day 0.25), run 2026-11-23. The
  inputs are outcome-blind upper bounds: every one of the 86 valid hand setups fills, at most 1 a day, over 1 year.
  The literal-header extractor would give 23, so the real n is lower still.

| Gate | n_eff | z | Power at 0.10 R | Reasons |
|---|---|---|---|---|
| R1 BC screen (one-sided 0.05) | 86 | 1.645 | **0.19** | power < 0.5; fresh n_eff 86 < 200; joint 0.20 < 0.25 |
| Forward confirm after 2 years (ADR 0085 year-1 Holm, m 1) | 172 | 2.50 | **0.08** | power < 0.5; n_eff < 200 |

- **What the sample can and cannot show.**
  - A 0.10 R edge needs about 390 events for 50 % power even at z 1.645. That is about 4 years of KoG weekly posts.
  - The BC could see only a large edge: its power is 0.75 at 0.30 R. ADR 0085 fixes delta at 0.10 R, and that is not
    reopened here.
- **Verdict: NOT STARTED (power).** The BC score is not run. This is a `not_started_power` registry row, which costs
  no K (ADR 0085 1).
- **Registry check (brain, registry of record, 2026-11-23).**
  - 2026Q4 used 3,416 of 3,500, so 84 left and 0 reserved. n_trials_effective is 3,492.
  - This card books **0** trials.
  - The recommended forward shadow (7.1) books +0. The 7.1 alternative (arm rows) would book +2 arm ids and needs
    an amendment to this card first.

## 7. Forward shadow (zero risk; descriptive only)

- **What is recorded.** Every Sunday weekly post from 2026-11-22 onward. Each one's setups are scored by the rules
  of section 3 on the 1h and 15m strata, together with control (a) and the P4 twins.
  - Shadow only: never an order, never a promotion source, never real money.
  - Each week the shadow also logs kogw-1's abstains and an independent staff hand read of the block, so that forward
    precision and recall are measured. They are counted from the 2026-11-29 post: the 2026-11-22 post was in the
    extractor's development set (its outcomes stay in the shadow).
  - Shadow weeks scored before a new card's commit are exposed. They cannot be that card's evidence or confirm set;
    only posts published after that commit count.
- **What it can tell us.** At about 90 events a year, it cannot confirm a 0.10 R edge (section 6). It can only show:
  - a gross effect large enough to justify a new card with new mechanism evidence;
  - or the absence of one, closed at the 2027-05-23 review.
### 7.1 Mechanism: proposal, not deployed

**The existing replay-only arm mechanism** (amendment 2026-11-15) works like this:
- An arm row with `control_mode: replay_only` (`goldbot/research/arms.py` `Arm.control_mode`) has every candidate
  recorded "blocked replay_only, mu 0" by `engine/exploration.py` `_enter`.
- `_send` refuses it as its very first check.
- `exploration_data.label_candidates` labels it with the r2 replay.

That is zero order risk by construction, but it does not fit this object as it stands:
1. `Arm._consistent` allows replay_only only for `role: control`. Registering KoG's rule as a "control" would
   mislabel it in every control read.
2. The engine's per-trade bracket carries one target, and section 3 exits a ladder in equal parts.
3. The rule needs a point-in-time KoG post feed in `RuleContext.spec_ctx`, which today has none.

**Recommended (smallest, exact, no engine path): extend the nightly `tva_scores` job.**
- **Code.** In `goldbot/research/tv_ideas.py` `run_job`, after `score_ideas`, call a new
  `kog_weekly.score_shadow(posts, b1, costs)`. It does three things:
  - selects the weekly posts with `available_utc` ≥ the setting `tv_ideas.kog_weekly_shadow_from: 2026-11-22`;
  - extracts them with the frozen kogw-1;
  - scores section 3 (1h and 15m) with control (a), (c) and the P4 twins on the 1m bid/ask bars once the post's week
    has closed plus 7 days (P4 twins).
- **Storage.** Rows go to a new aux table `kog_weekly_shadow`, with no idea text and no level in any log: post id,
  side, tf, the fill and exit times, net R, the control R and the abstain reason.
- **Config.** One settings key. No arm row, no `exploration_arms.yaml` change, no RiskGate or engine import (a test
  asserts the module imports nothing from `goldbot.engine` or `goldbot.execution`).
- **Coverage.** The week of 2026-11-22 is scored after Friday 2026-11-27's close from forward 1m bars, so no week is
  lost before the deploy.
- **K.** +0 for the forward shadow (descriptive, the box-ladder convention for 1D/1W shadow).
- **Reviews.** quant-reviewer (rules = section 3) and code-reviewer. trading-safety only to confirm there is no order
  path.

**Alternative, if the director wants the forward record in the arm views.**
- Add `role: "shadow"` to `Arm.role`, and in `_consistent` require `control_mode: replay_only` for that role. The
  existing replay_only guards then cover it unchanged.
- Add `arm_rules_kog_weekly.py` (`kog_weekly_break` plus `kog_weekly_control`, shifted ladders). Its first target is
  the bracket; the full ladder is recorded as candidate features and scored by the job above.
- Add an engine feed `spec_ctx["kog_weekly"]`, joined via `asof_join` on `available_utc`.
- Add two `exploration_arms.yaml` rows `{role: shadow, control_mode: replay_only}`. These count +2 arm ids in K.
- This is more code, for the same evidence.

## 8. Owner-only flags

- None is taken here. Real-money use, sizing, budget and the TradingView access route stay owner-only.

## 9. Losers next to winners

- The extractor's recall failure and the power refusal are this card's results. No unit of this line has passed
  anything.
