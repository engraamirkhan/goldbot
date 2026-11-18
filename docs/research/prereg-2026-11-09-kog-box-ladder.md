# Author level ladders: KnightsofGold box break and Goldviewfx EMA5 lock (pre-registration DRAFT, 2026-11-09)

Principal quantitative strategist, 2026-11-09. **DRAFT, not committed, not frozen.** It freezes at the commit that
adds it, after the quant review (section 10). Revised 2026-11-09 with the quant review's changes C1-C11 (verdict
CHANGES), applied verbatim where the reviewer gave text. Below: **K** = KnightsofGold, **G** = Goldviewfx. There are two units,
each counted separately:
- **U-K** (`KBL`): K's 4h box break with a next-box target;
- **U-G** (`GEL`): G's "EMA5 cross and lock" at a level, with a next-level target.

Owner corrections, 2026-11-09:
1. K's setup is mechanical. A 4h candle closes beyond one of the author's boxes, the trade goes that way, and the
   target is the next box. The rule is the author's; only the levels come from the post.
2. G's setup is mechanical too: the EMA5 crosses and locks at one of the idea's levels, the trade goes in the cross
   direction, and the target is the next level. Direction comes from price, not from G's bias. So the 11-06 verdict
   that G is "100% long, drift-only" concerns G's direction calls and does not apply to this rule.

Record of the owner's words: `~/.claude/projects/-Users-aamirkhan-myproject-goldbot/memory/tv-author-ideas.md`
(the owner entries dated 2026-11-09).

This corrects point 4 of the research director's ruling of 2026-11-09, which called this "our own rule at their
lines".

**Outcome-blind attestation.** I wrote this card after reading, from `state/tv_ideas.db` on the brain (read-only), the
following for each stored K and G idea: counts, intervals, flags, number counts, zone widths and level gaps. I read no
idea text, no price after any publish time, and no outcome under this rule. The 11-06 report
(`tv-ideas-score-2026-11-06.md`) did expose outcomes for these ideas, but under a different rule (the author's
stated entry, stop and T1, and K's red boxes as OCO). That is why every stored idea is descriptive only here (4.1).
This file holds no idea text and no author price level.

## 0. Ruling: a new hypothesis, not a fourth parser

**This is a new pre-registered hypothesis, and it is admissible.** It is not a further iteration of the parked line.
- **Section 1.1 of the 10-28 prereg never covered this object.** `prereg-2026-10-28-tv-author-ideas.md` 1.1 says
  conditional ideas are type C, "scored descriptively as an OCO pair of stop entries and ... excluded from every
  decision read". The same file's attestation forbids changing sections 1-6 after the first score. So the only
  honest route is a new card, not an amendment.
- **The parked parser is a different thing.** Amendment T-1 (section 7) parks the **direction parser**: an output of
  long, short or abstain for the section-3 author-call cells. Its words are "One attempt. A fail parks the line to the
  Q1 refresh; no further parser iteration this quarter". The extractor here outputs **no side**: only a price ladder.
  The side comes from price (a 4h close for K, an EMA5 lock for G). So it is not an iteration of that parser.
- **The director's own ruling points here.** Ruling of 2026-11-09, point 4: "Reopening needs a new card with mechanism
  evidence and counts in K."
- **Conditions that keep this honest:**
  - Nothing here feeds TVA-K or TVA-G (prereg 3, 4(i), 4(ii)). Those verdicts and the T-1 park are unchanged.
  - The extractor gets **one audit attempt per author**. A fail parks that unit to the Q1 refresh, with no iteration.
  - The ideas already exposed are descriptive only (4.1).
  - K is counted afresh (section 8).
  - The new extractor is fresh code frozen by hash; no field of `direction_t1` or the audit-1/2/3 parsers is read;
    the audit excludes every K/G id from audits 1-3 and all four T-1 seeds.

## 1. Prior evidence

| Claim | Source | Sample, instrument | Net of costs? | Grade |
|---|---|---|---|---|
| Support and resistance levels published by six firms predicted intraday trend *interruptions* (bounces) better than artificial levels drawn at random. The effect was modest and lasted about 5 days | Osler, "Support for resistance: technical analysis and intraday exchange rates", FRBNY *Economic Policy Review* 6(2), 2000 *(unverified issue detail)* | Intraday USD/DEM, USD/JPY, GBP/USD, 1996-1998 *(unverified exact span)* | No | Single study. Method = our control (a) |
| Stop-loss orders cluster just beyond round numbers, and take-profit orders sit at them. Once a cluster is crossed, the move continues faster (a stop cascade) | Osler, *Journal of Finance* 58(5), 2003, "Currency orders and exchange rate dynamics" | Order book of one bank, 3 currency pairs, 1999-2000 *(unverified exact span)* | No | Single study, mechanism-level |
| Moving-average crossover rules had gross predictive power on the DJIA. The best rule is not significant after a data-snooping correction, and not after 1986 | Brock, Lakonishok, LeBaron, *JF* 1992; Sullivan, Timmermann, White, *JF* 1999 | DJIA 1897-1986, plus a later sample | Gross only. Fails out of sample | Robust *negative* for generic MA crosses |
| **Our own box breaks have no edge** | R-15 (15m box_break_close: gross +0.030 R, t 1.61; net -0.297 R, n 5,835); H-80 R2 (15m-4h boxes, 2018-01..2025-09, n 5,355: net -0.20 to -0.29 R); H-37 (1h/4h, expected fail); R-RS (retail SMC rules) | XAUUSD, 2010-2025 | Yes | Robust in our data |
| These authors' levels have no record of skill | `tv-ideas-score-2026-11-06.md`: 40 post-holdout fills, -0.02 R, 22 target hits against 21.7 expected by chance; K's OCO red boxes 6 hits against 5.7 | XAUUSD, 2026-10-01..11-06 | Yes | n far too small |

**Reading.**
- A generic box break or MA cross is dead in our data. So any edge must come from **where the author draws the
  levels**. That is exactly what controls (a) and (b) isolate.
- Osler's mechanism predicts *bounces* at published levels and *continuation after crossing stop clusters*. Only the
  second favours this rule.
- Prior P(a unit passes R1) is about 10% (judgement). The data is free and the owner already trades these levels by
  hand, so a read is worth its K.

## 2. The shared level extractor (frozen by hash before any audit)

**Input:** FWD: the idea's **first snapshot** (`tv_idea_versions`, earliest `seen_utc`), title plus description;
edits after first sight are refused. BC: the original title + description (4.1). Author notes (`tv_idea_updates`)
are later rows and never extractor input (test: the extractor's input bytes exclude every note row).

1. **Price tokens.** These are numbers with 4 integer digits (an optional decimal is allowed) within ±10% of P0
   (±25% for 1D/1W ideas). P0 is the last 1m mid strictly before `available_utc`, taken point in time. Years,
   percentages and times are excluded by the band, and a number followed by `%` is dropped.
2. **Pairs.** "A-B", "A–B", "A—B", "A to B" and "A/B" are pairs. A shorthand B (fewer digits) takes A's leading
   digits. Let W* = 0.5 × ATR14(1d), point in time (T-2 item 4 ATR):
   - |A − B| ≤ W\*: the pair is **one zone** [min, max];
   - |A − B| > W\*: the pair is **two levels**, because it is a move statement, not a zone.
3. **Singletons.** Each is a zero-width zone [x, x]. The extractor never invents a width.
4. **Clean-up.**
   - Zones that overlap or touch are merged, and duplicates dropped.
   - The ladder is the merged zones, sorted.
   - For U-G, a non-zero zone is reduced to its midpoint as a level, because G's rule is about levels.
5. **Abstain.** An idea abstains if it has fewer than 3 zones (K) or 3 levels (G), is an education post, is not on
   spot gold, or falls in the holdout window. Each abstain is counted.

**Shape at draft time** (stored ideas, counts only):
- **K:** 29 ideas: 20 on 4h, 6 on 1h, 3 on 1W, 2 of them education posts; 21 updates. Non-education 4h/1h posts carry
  0-16 price tokens. Explicit zone widths run $4-30 (median about $10). Median gaps are about $9-45.
- **G:** 31 ideas on 1h, 4h, 1D and 1W, with 0 updates and 0 edited. Posts carry 8-23 tokens. 1h gaps are about
  $40-60 and 4h gaps about $60-90. Pairs of $46-97 are split into levels by rule 2.

### 2.1 Audit (one attempt per author, outcome-blind)

- **Truth.** A hand reader writes each sampled post's zone list (K) or level list (G) **before** the extractor's output
  is queried, as audit 2 did. The reader sees text only, with no price after publish.
- **Match.** An extracted zone matches when both edges are within $1 of a hand zone.
- **Bars, per author:**
  - **precision ≥ 95%** = matched extracted zones / extracted zones;
  - **recall ≥ 85%** = matched / hand zones. A missed zone silently moves targets, so recall is also required.
- **Sample:**
  - a seeded draw of 30 non-education posts per author from the pre-2025-10-01 back catalogue (4.2). Seeds are
    recorded now: **K 20261109, G 20261110**. Exclude every K/G id read in audits 1-3 and in all four T-1 seeds.
- **Development.** The implementer may develop the extractor only on the stored posts of 2026-10-01..11-09. It is
  never developed on audit posts.
- **Result.** A pass admits the unit's extractor (a dated amendment). A fail parks that unit to the Q1 refresh, with
  no iteration.

## 3. Rules (fixed now; no tunable parameter)

Common to both units:
- **Bars.** Bars are aligned to the broker server clock (NY+7) and signals use mid closes. Fills follow T-2 item 2:
  broker 1m bid/ask for the live read, Dukascopy for the back catalogue.
- **Entry.** Market, at the first 1m bar at or after the signal bar's close + 60 s, plus costs.
- **Outcome.** The first barrier touched on 1m bid/ask decides. If both are touched in one 1m bar, the stop is taken
  first.
- **Exits.** At the hold limit, the 1m close. Weekend-flat at the broker's Friday close minus 5 minutes, in shadow
  too, so that shadow equals the arm.
- **Caps.** At most **3 entries per post**, each (level, direction) at most once per post, and **max_open 1 per
  unit**, applied in shadow too. A stop above $40 or below $3 refuses the event; refusals are counted, never resized.
- **Costs (quant review C4).** "Fills and barriers on 1m bid/ask, so spread is paid there, once. Per round trip add
  $0.144 (charged RT 0.230 − spread 0.086: commission plus slippage bound), plus $0.145 on every stop exit (charged
  overshoot). Swap for each 17:00 NY rollover strictly inside (fill, exit), triple on the triple day. BC uses the
  point-in-time swap with rate vintage (catalogue convention); FWD uses the broker's swap. Stress: 1.5 × the added
  costs." Source: `cost-of-record-2026-10-31.md` (ICM pooled).
- **ATR (C7).** ATR14(tf) = simple mean of the 14 true ranges of mid tf bars ending with the signal/lock bar
  (inclusive), server-clock bars (T-2 item 4 convention). Zero-width zones and both buffers are accepted (quant
  review C7).

### 3.1 U-K (`KBL`): K's 4h box break

- **Posts.** K, spot gold, **4h interval only** (the owner's described setup), not education, at least 3 zones. K's 1h
  and 1W posts are descriptive only.
- **Validity.** From `available_utc` until the next qualifying K 4h post becomes available, with a cap of 5 UTC
  weekdays (holidays count).
- **Signal.** A 4h close C_t after `available_utc` with C_{t-1} ≤ U_j < C_t is a long; with C_{t-1} ≥ L_j > C_t it is
  a short. (U_j is zone j's upper edge, L_j its lower edge.)
  - If several zones were crossed, the broken zone is the last one crossed in the trade direction.
  - If C_t sits inside another zone [L, U], there is no trade: a close inside a box is not beyond it.
- **Stop.** The opposite edge of the broken zone, minus a buffer b for a long (plus b for a short), with b = 0.25 ×
  ATR14(4h) taken point in time.
- **Target.** The near edge of the first zone strictly beyond the fill price in the trade direction. If there is none,
  there is no trade.
- **Hold.** At most 12 4h bars (48 h).

### 3.2 U-G (`GEL`): G's EMA5 cross and lock (owner's definition, verbatim)

- **Posts.** G, spot gold, not education, at least 3 levels.
  - 1h and 4h posts are arm-eligible.
  - **1D and 1W posts are shadow-only and descriptive**, because engine 1d/1w is dark (backlog 144).
  - Each interval keeps its own ladder, valid until the next G post **on the same interval** becomes available, with
    a cap of 5 UTC weekdays (1D/1W: 10).
- **EMA5.** A standard EMA (α = 2/6) of mid closes on the idea's own interval, seeded on at least 50 prior bars.
- **Trigger, verbatim from the owner (2026-11-09):**

> **Lock at bar i** (a long; mirror it for a short) when all of these hold:
> - body close_i > L;
> - open_i is on the other side of L, or close_{i-1} <= L;
> - the EMA5 line has crossed L at or before the centre of bar i's body. With the EMA plotted at bar centres, that means EMA5_i > L.
>
> **Otherwise the next candle's close locks it:** at bar i+1, lock if close_{i+1} > L and EMA5_{i+1} > L. If either fails, there is no trade from that break.
>
> The entry is at the lock bar's close, i.e. the next bar's open plus costs.

  - The break at bar i must come after `available_utc`. Bar i-1 may come before it.
  - **Implementation (C6):** entry follows §3 (first 1m bar at or after the lock bar's close + 60 s). The lock bar is
    i or i+1. Lock at i requires bar i's close time > `available_utc`.
- **Resolved (owner, 2026-11-09).** The i+1 lock requires **both** close_{i+1} beyond L **and** EMA5_{i+1} beyond L.
  If price has fallen back through the level by the next candle's close, there is no lock. The looser variant
  (EMA5 alone) is dropped: one unit only, never registered or run.
- **Stop.** L − 0.5 × ATR14(tf) for a long (mirror for a short), taken point in time. If the fill is within 0.1 ×
  ATR14(tf) of the stop, or beyond it, there is no trade.
- **Target.** The first level strictly beyond the fill price in the trade direction. If there is none, there is no
  trade.
- **Hold.** 1h: 24 bars. 4h: 12 bars. 1D: 10 bars and 1W: 4 bars (shadow only).

## 4. Data universes and reads

### 4.1 What is admissible

| Set | Definition | Role |
|---|---|---|
| HO | ideas published 2025-10-01..2026-09-30 (K has 3 stored, 09-28..09-30) | Refused by the `holdout_window` guard. Never read |
| Exposed | stored ideas published 2026-10-01 .. the admission amendment (K about 19 4h posts, G about 25) | **Descriptive only.** The 11-06 report exposed their outcomes under another rule. Reported, K +1 per unit |
| BC | all gold posts 2020-01..2025-09 at their original title + description and `available_utc` (= publish + 900 s); see C2 below | R1 screen |
| FWD | `available_utc` ≥ the unit's extractor admission, first snapshot (T-2 item 7) | R3 (the confirm) |

- **Holdout (C1).** BC events are admitted only if fill minute + the unit's maximum hold < 2025-10-01 00:00 UTC
  (decided before the fill, from the clock alone). Others are dropped and counted, never truncated. The BC reader
  refuses any bar ≥ 2025-10-01 (holdout_window guard) and has a truncation test: the events up to t are identical
  when bars after t are removed.
- **Edited posts (C2).** BC = all gold posts 2020-01..2025-09 at their original title + description and
  `available_utc` (= publish + 900 s). Author notes are later rows, never extractor input (test: the extractor's
  input bytes exclude every note row). Posts whose TradingView update time matches no note within 2 min (about 2%)
  are excluded and counted. Sensitivity: unedited-only. Hindsight check (prereg 10-28 §1.5): the edited-vs-unedited
  gap is reported. Before R1, the live K posts with the flag are checked text-only: the extractor's ladder on the
  first snapshot must equal the ladder on the latest version for all of them; any mismatch reverts BC to
  unedited-only.
- **Survivorship (C11).** Deleted or moderated ideas are invisible in BC. The bias favours the author, size unknown,
  and is weaker for a rule that takes its side from price. The live poll measures the deletion rate in FWD, and R3
  reports it.
- **Reader (C11).** R1 uses a new BC reader. `tv_ideas.py` skips backfilled rows. The reader keeps the holdout
  refusal and the truncation test of C1.

### 4.2 Back catalogue (already pulled)

- **What.** BC already pulled 2026-10-28, cookie-less (raw/tv_ideas/tv_ideas.db, backfilled=1, `available_utc` =
  publish + 900 s). Idea text never goes in the repo; it lives on the brain and in the Mac's raw/.
- **Why it matters.** Without it, the units read forward only. At about 1-1.5 events a day, detecting a 0.05 R difference from
  control (a) at t 2 (per-trade SD about 0.7 R, so n about 780) takes **about 1.5 years per unit**.
- **Edits.** 17 of K's 20 stored 4h posts carry TradingView's edit flag; G's carry none. Edited posts are admitted on
  their original text (C2, 4.1).

### 4.3 Controls (for every event set, same bars, windows, caps and costs)

- **(a) Random ladders, matched.** Per post, 200 replicates seeded on (idea_id, r):
  - permute the post's sequence of gaps and its multiset of widths;
  - anchor the ladder at the original lowest level + U(−g, +g), where g is the post's median gap;
  - run the identical rule.
  - Δa = author mean net R − replicate-mean mean net R. p_a = (1 + #{r : mean_r ≥ author}) / 201, where replicate r
    uses ladder r for every post.
- **(b) Our own auto levels.**
  - U-K: the `box_break_close` 4h boxes (`sr_channels`, frozen roster parameters) in force at each 4h close inside the
    same validity windows, run through 3.1.
  - U-G: the same `sr_channels` boxes in force at the bar, each reduced to its midpoint (rule 2.4, as for G's own
    zones), run through 3.2 (the lock rule).
  - `sr_channels` is point in time by `tests/test_smc_structure.py::test_every_replica_column_is_prefix_invariant`
    (replica columns on bars ..t equal those on bars ..t+250). Owed before R1: add `"4h"` to its parametrization
    (implementer, with the BC reader). The control boxes are computed on the Dukascopy series from its pinned
    2010-01-01 start (`SERIES_START`), never from the BC window start, because the replica state depends on the
    series start.
  - Round numbers at matched spacing are reported descriptively, not as a decision control. If PASS and the
    round-number ladder's net mean R ≥ the author's, the verdict states the edge is not attributable to the author.
  - For (a) and (b), report events, median stop and median target distance against the author's.
  - Δb uses a paired block bootstrap (author and control resampled by the same ISO weeks, 2,000 draws).
- **(c) Random-side twin.** Same fill time, same stop and target distances, side by a seeded coin on (idea_id, event
  number). It is the demo control, and in shadow it shows whether the geometry alone pays.

**Claim per unit:** the author's levels beat both (a) and (b) net of costs, and net mean R > 0. Unit p = max(p_a,
p_b) (intersection-union). R1 is a screen at one-sided 0.05 each, without Holm. R3: Holm, m = 2, on the two unit
p's. U-G decides on pooled 1h+4h; the strata are reported only.

### 4.4 Reading rules (written before any data under this rule is read)

Standard errors are clustered by post. Statistic: mean net R per event.

| Read | Window | CONTINUE / PASS | STOP |
|---|---|---|---|
| **R1** screen | the full BC (2020-01..2025-09) | PASS needs ≥ 1,000 events, net mean R > 0, Δa > 0 with p_a ≤ 0.05, Δb > 0 with p_b ≤ 0.05, gross t ≥ 2, net > 0 under the cost stress, and net > 0 in ≥ 3 calendar years including 2021 and 2022, each year reported | Only with ≥ 1,000 events: PASS not met, or the 90% upper bound on Δa < 0.03 R → retired (hypotheses.md R-row), FWD kept descriptive only. Below 1,000 events R1 never retires (Event floors, below). |
| **R3** forward (the confirm, fresh text) | FWD; read at ≥ 300 events, or on 2027-05-09 | Δa > 0 and Δb > 0 on the unit p (Holm, m = 2), and net > 0. Required before any non-learning use | FAIL: retired as a signal source |

- **Event floors (C3).** 300-999 events = `inconclusive (event floor)`: it cannot retire and cannot qualify, and it
  may only CONTINUE to R3. Under 300 = `insufficient`. All are counted in K. R3 is a forward confirm, not a P4
  screen: an R3 FAIL retires the unit as a signal source at ≥ 300 forward events.
- **R2 is deleted (C3).** The confirm is R3 (forward, fresh text). An ADR 0061 qualification cites screen R1 and
  confirm R3, and must pass research/gates.py (≥ 1,500 candidates, 60 per fold, DSR ≥ 0.95 on ≥ 200 trades at
  registry K). If the code requires a history confirm, the unit stays learning-class only.
- **Floors fixed at R1 start.** The event floors are the owner's `research.screen_min_events` (and any owner ruling
  on 300) in force when R1 is run, fixed before R1 starts. No other element of this reading rule changes after the
  freeze. Exception, decided before any R1 data is read: if the team adopts a new evaluation method (EVAL-2, owner
  directive 2026-11-09) before R1 starts, the R1/R3 reading rule is re-issued under EVAL-2 and re-signed by the
  quant, with K recounted.
- **OPEN OWNER QUESTION (C3).** Whether a 300-event floor may retire a unit (retire-only). Nothing below 1,000
  events retires until the owner rules.

## 5. Demo learning arms (ADR 0061 learning class, amendment 2026-11-02)

| Field | KBL-L (control KBL-C) | GEL-L (control GEL-C) |
|---|---|---|
| role | `learning`, `evidence_class: learning`; never a qualification and never real money | same |
| account, size | icm-demo only, 0.01 lot fixed | same; 1h and 4h only |
| bracket | per trade, from 3.1 | per trade, from 3.2 |
| control | (c): same timestamp and stop, target and hold distances; random side; `params.control: "learning:<arm>"` | same |
| caps | max_open 1; 3 per post; refused if the stop is above $40, below $3, or outside the account's min/max stop | same |
| loss_cap_gbp | £300 (20 × about £15, a $20 stop at 0.01 lot) | £300 |
| until | `since` + 45 days (ADR maximum) | same |
| kill | **after 20 closed trades, off if arm net mean R − control mean ≤ 0**; also at the loss cap, at 300 fills or at `until` | same |

- **Start (C9).** Start = the extractor audit (2.1) has passed, AND R1 = PASS at ≥ 1,000 events, AND the ADR 0061
  per-trade-bracket amendment is merged.
- **Per-trade bracket ruling (C10).** Per candidate, the twin copies the arm's fill minute, stop distance, target
  distance, hold and weekend-flat. Its side comes from a seeded coin on (idea_id, event number), independent of the
  arm's side. If either the arm or the twin is refused, both are dropped from the paired read and counted. L1 uses
  each trade's own stop. Each has max_open 1. Tests: twin stop, target and hold equal the arm's on every candidate;
  a refused arm candidate refuses its twin; the cap check uses the per-trade stop.
- **Why not after the audit alone.**
  - A 20-trade kill has no power: the SE is about 0.16 R against the 0.05 R being sought.
  - The arm's information is a broker-cost check worth less than 0.02 R at these stops.
  - Shadow carries the evidence for free.
- **Build needed (not built here).**
  - Learning arms need the **per-trade bracket** variant (C10) as an ADR 0061 amendment; today ADR 0061 matches a
    fixed bracket per arm.
  - Entries are market orders at bar close, so no pending-entry emulation is needed.
  - The adapter reads `tv_ideas.db` point in time and uses only the first snapshot.

## 6. Honest expected value

- **Trade rate (estimate, not measured; no price was loaded).**
  - K posts about 0.6 qualifying 4h ideas per weekday (about 18 in 30). K's gaps are about $10 against a 4h ATR of
    about $30-40, so nearly every 4h bar crosses a zone. The caps (3 per post, max_open 1, holds of 1-3 bars to
    target) give **about 1-1.5 trades per weekday**.
  - G's 1h gaps are about $55 against a 1h ATR of about $15-20, so a lock fires about every 1-2 days per interval:
    **about 0.5-1 per weekday**, with 1h and 4h together.
- **Size of each trade.**
  - U-K: stop about $20-25 (box width + close overshoot + buffer), target about $8-12, so about 0.4 R.
  - U-G: stop about $10-20.
  - At 0.01 lot (1 oz), 1 R is about £11-19 at GBPUSD 1.33. Cost (C4: spread + $0.144, + $0.145 on stop exits) is about 0.015-0.02 R plus about 0.02 R of swap
    on an overnight long.
- **£/day at 0.01 lot.**
  - No edge (the base case): −0.02 to −0.03 R per trade, **about −£0.5/day** for both units together.
  - Optimistic: +0.10 R net per trade gives about **£2.5/day (U-K) + £1/day (U-G) ≈ £3.5/day**.
  - Against the £50/day north-star this is a learning and evidence line, not an income line. Scaling it is
    owner-only and is not proposed.

## 7. Timeline and owed work

| Item | Owner | Due |
|---|---|---|
| Quant review of this card; freeze commit | quant-reviewer | 2026-11-13 |
| BC reader (new; holdout refusal + truncation test, C1/C11) and the edited-post checks (C2) | implementer, quant-reviewer | 2026-11-20 |
| Extractor freeze (hash) and the audit per author (2.1) | implementer (frozen) + staff hand reader | 2026-11-27 |
| Shadow scorer for 3.1/3.2 with controls (a)-(c) | implementer, quant-reviewer | 2026-12-04 |
| R1 | evaluator | after the reader and the audit pass, by 2026-12-11 |
| ADR 0061 per-trade-bracket amendment and arm plumbing (C10) | implementer, trading-safety-reviewer | only if R1 = PASS |

## 8. K and the budget (2026Q4 cap 3,500, ADR 0073; owner)

| Row | U-K | U-G |
|---|---|---|
| Exposed-set descriptive read (4.1) | +1 | +1 |
| R1, one registry row (R2 deleted, C3) | +1 (k_eff 1) | +1 (k_eff 2: pooled decides, 1h and 4h strata reported) → +2 |
| R3 forward row | +1 | +1 |
| Learning arm + control (`scripts/record_learning_k_rows.py`, before the first fill) | +2 | +2 |
| 1D/1W shadow (descriptive) | – | included in the exposed/forward rows, k_eff +0 (never decision-eligible) |
| **Unit total** | **+5** | **+6** |

- **Total: +11.** The looser 3.2 variant was dropped by the owner (one unit only).
- **Budget check.**
  - The Mac registry copy (`state/research_registry.jsonl`, 2026-11-07 09:39) holds 3,353 Q4 rows. EVAL-1 adds 72 and
    12 more are reserved, giving 3,437. Adding this card gives **3,448 ≤ 3,500** (one row per unit, the EVAL-1 caller
    convention).
  - Under the conservative convention (EVAL-1 cut to 3,499), this card **does not fit in 2026Q4**.
  - **Booking rule (C11).** The brain is the registry of record. Until it is reconciled with the Mac copy (brain 3,259
    rows, Mac 3,353), use the larger de-duplicated union for budget and DSR K. Book rows in order (descriptive,
    R1, R3, arm + control) while `assert_budget` passes; anything over moves to 2027Q1.
  - Raising the cap is owner-only and **is not proposed**.
- DSR K for these reads: `n_trials_effective` at the read, at least about 3,436.

## 9. Owner-only flags (not decided here)

- Any real-money use.
- Any budget raise.
- Any change to the TradingView access route (the BC was pulled cookie-less; the cookie route was retired
  2026-11-02).
- **C3:** whether a 300-999-event R1 may retire a unit. Until the owner rules, nothing below 1,000 events retires.

## 10. Quant review (2026-11-09: CHANGES, C1-C11 applied in this draft)

Settled by the review: the new-card ruling and its conditions (now five), edited posts admitted on original text
(C2), zero-width zones and both buffers (C7), R2 deleted with R3 as the confirm (C3), the per-trade bracket ruling
(C10), the brain as the registry of record (C11), `sr_channels` midpoints as G's control (b) (C5).

Still open:
1. Owner: the 300-event retire-only floor (C3). Nothing below 1,000 retires until ruled.
2. Implementer: add `"4h"` to the prefix-invariance test's parametrization before R1 (E2).
3. Freeze waits for the EVAL-2 synthesis; the quant signs with E1, E2 and the floor clause in.
