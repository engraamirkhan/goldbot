# TradingView author ideas: did they work this week? (principal quant strategist, 2026-11-06)

Owner, 2026-11-06: "check the ideas from the three authors on TradingView (KnightsofGold, Goldviewfx,
ArmanShabanTrading), looks like those ideas were working for the week". Below: **K** = KnightsofGold, **G** =
Goldviewfx, **A** = ArmanShabanTrading.

This file holds aggregates and per-idea outcomes only. It contains no idea text and no author price levels. The
text stays on the brain (`state/tv_ideas.db`) and in a local scratch copy that is never committed.

## 0. Answer first

- **This week (ideas published 11-01..11-06, all live): yes, they made money.** A trader who took every live idea at
  the author's stated levels, at 0.01 lot, made **+$176 (about £132)** on 11 filled trades: 8 hit target, 2 stopped,
  1 still open. Per author: K +$82, A +$68, G +$25.
- **That week is not distinguishable from chance.** If the authors had no skill, the stated stop and target
  distances alone would give about 5.6 target hits out of 10 resolved trades (a driftless random walk hits the
  nearer barrier more often). They got 8: z = 1.6, one-sided p ≈ 0.06, before any multiplicity correction. On the
  direction-only read the week averages +0.14 R against gold's own drift of +0.08 R at the same timestamps (t 0.8).
  The test this question needs is >= 60 publish days and >= 100 resolved ideas per author (prereg 3.3). This week
  gives 2-5 per author.
- **The four weeks before it lost.** For ideas published 10-01..10-30 (backfilled), the same levels read gives
  **-$311** on 28 fills (13 target, 15 stop). Over the whole post-holdout sample (10-01..11-06, 40 fills):
  **-$65, mean -0.02 R, 22 target hits against 21.7 expected by chance (z 0.1)**.
- **Verdict for all three authors: INSUFFICIENT, not trending to pass.** Nothing is promoted, no demo arm is opened,
  no trial is spent. Section 5 ranks the next step: an author-specific direction parser for A, plus an automatic
  shadow scorer, so the forward read fills itself at no risk.

## 1. Data, rule and what was not available

- **Ideas.** Brain `tv_ideas.db`, read-only (`mode=ro`), 2026-11-06 about 20:00 UTC: 77 ideas.
  - 6 are not gold (A: BTC, GRAM, UKOIL): excluded.
  - 8 were published 2026-09-18..09-30. That is inside the sealed holdout (2025-10-01..2026-09-30, prereg 5), so
    they were **not scored**.
  - That leaves **63 gold ideas from 2026-10-01 to 11-06**: 46 backfilled (published 10-01..10-30, collected
    10-30) and 17 live (first seen by the collector 0.4-13 min after publish; median latency 7-10 min).
- **Bars.** XAUUSD 1m bid/ask, in priority order:
  - Dukascopy (local raw plus brain store) to 10-23 20:56;
  - then the ICM demo broker store from 10-25;
  - then the ICM live store to 11-06 20:01.
  - The 10-23 21:00..10-25 23:20 gap is the weekend plus Friday's last hour. No idea was open across it.
- **Frozen rule.** There is no frozen scorer or parser.
  - Parser audits 2 and 3 failed (`tv-ideas-parser-audit-2026-10-31.md`, `tv-ideas-direction-audit-2026-10-31.md`).
    Amendment T-1 then parked the line until the Q1 refresh.
  - So this read applies the prereg's scoring rule exactly, but takes the direction from a **hand read** instead of
    the parser. That is the method the audits used as ground truth.
  - Labels and level extractions were written to a file, and its hash recorded (sha256 `3064a8b4…5071`), **before
    any bar after publish was loaded**.
  - Caveat: the idea texts narrate the move before publish, so the labeller was not blind to the prior path, only to
    the outcome.
- **Two reads.**
  1. **Direction read** (the prereg's decision outcome under T-1, type D, 1.3(b)):
     - side from the hand label: long or short, abstain otherwise;
     - entry at the first 1m bar at or after `first_seen_utc` (live), or `published_utc + 60 s` (backfilled);
     - R = ATR14 on daily bars, as of the prior daily close;
     - horizon: 1 trading day (I), 5 (D), 20 (W);
     - catastrophic stop at 2 R.
  2. **Owner-view levels read** (descriptive, the prereg's type L rules 1.2-1.3(a)):
     - entry, stop and first target as stated. Market or limit per 1.2: market if the price is within 0.25 ATR of the
       entry or between stop and entry. Fill window 1/3/10 trading days; maximum hold 3/10/20 trading days;
     - first barrier touched on 1m bid/ask decides, with the stop assumed first when both fall in one bar;
     - K's two-sided "red box" (break above X for a target, break below Y for a target) is scored as an OCO pair of
       stop entries, with the opposite trigger as the stop (prereg type C). Only K's fresh reports carry new red
       boxes. The red boxes in its end-of-day updates are already ticked as results, so they are not calls;
     - stop rule when none is stated: the next named level on the adverse side, else the prereg's catastrophic
       2 x ATR14.
- **Costs (prereg 1.4).**
  - The bar spread is paid at entry and exit (entry at ask, exit at bid for a long).
  - Plus $0.23/oz per round trip: commission $0.08 from `cost_of_record.yaml` `cor-2026-10-31.2`, and the slippage
    prior $0.15 from settings.
  - Plus swap at the settings prior: long -$0.60/oz/night, triple on Wednesday; short 0.
  - Stress case: 1.5 x (spread + extra). It changes no row by more than 0.01 R, because costs are 0.01-0.02 R at
    these stop sizes (prereg 0).
- **£** at GBPUSD 1.33, the last local FX print. 0.01 lot = 1 oz, so $ per trade = $ per oz.

## 2. Direction read (prereg decision outcome, T-1): per author

Mean net R per resolved idea. "Random" = 0.5 x (long + short) at the same timestamps; "drift" = always long. The
permutation p shuffles sides within the author (10,000 draws, seed 20261106). Two ideas published 11-06 have not
reached their 1-day horizon and are left out.

| Author | Set | n | Mean net R | t | Win | Random | Drift | Long share | Perm p |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| K | backfilled 10-01..10-30 | 1 | -0.30 | – | 0/1 | -0.01 | -0.30 | 1.00 | – |
| K | live / week 11-01..11-06 | 2 | +0.53 | – | 2/2 | -0.01 | -0.03 | 0.50 | 0.50 |
| K | all | 3 | +0.26 | 0.9 | 2/3 | -0.01 | -0.12 | 0.67 | 0.34 |
| G | backfilled | 12 | **-0.32** | -1.4 | 5/12 | -0.01 | -0.32 | 1.00 | 1.00 |
| G | live (incl. 10-30) | 6 | +0.13 | 0.5 | 4/6 | -0.01 | +0.13 | 1.00 | 1.00 |
| G | week | 5 | +0.00 | 0.0 | 3/5 | -0.01 | +0.00 | 1.00 | 1.00 |
| G | all | 18 | -0.17 | -0.9 | 9/18 | -0.01 | -0.17 | 1.00 | 1.00 |
| A | backfilled (all edited) | 8 | +0.02 | 0.1 | 4/8 | -0.04 | -0.41 | 0.38 | 0.57 |
| A | live / week | 2 | +0.10 | 0.3 | 1/2 | -0.01 | +0.37 | 0.50 | 0.51 |
| A | all | 10 | +0.03 | 0.1 | 5/10 | -0.03 | -0.25 | 0.40 | 0.49 |
| **All** | week | 9 | +0.14 | 0.8 | 6/9 | -0.01 | +0.08 | 0.78 | 0.25 |
| **All** | all | 31 | -0.06 | -0.5 | 16/31 | -0.02 | -0.19 | 0.77 | 0.33 |

How to read it:
- **G is long-only.** In all 18 directional ideas the side was long. Its result *is* the drift baseline, to the
  hundredth of an R. October fell from about $4,156 to $3,876 and G lost -0.32 R per idea. This week gold rose from
  $3,890 to a $4,147 high and G broke even. No timing skill is measurable: the permutation p is 1.0 by construction.
- **A beat drift by +0.43 R per idea in October** (+0.02 against -0.41), by being short in a falling month. But all
  8 backfilled A ideas carry TradingView's edit flag. Edited backfills are excluded from every decision read (prereg
  1.5), so that number is not admissible evidence. The live A ideas (2) are below drift.
- **Most K and G posts carry no directional call.** Of 23 K ideas, 3 have a side; the rest are two-sided red boxes,
  result recaps or education. Of 28 G ideas, 19 have a side. This matches audit 3's ceiling: 27% of these authors'
  ideas state one unopposed direction.
- **Capture loss** (live entry at first sight against entry at publish): -0.01 R for A and G, +0.03 R for K.
  Polling is not the constraint.

## 3. Owner-view levels read: per author

Each idea traded exactly as written at first sight: limit, stop or market entry at the stated level, the first
target, the stated stop. No later author updates are applied.

| Author | Set | Ideas with levels | Filled | Target | Stop | No fill / invalidated | Mean net R | t | Net $ (0.01 lot) | Avg stop $ | Hits vs chance (z) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| K | backfilled | 6 | 6 | 3 | 3 | 0 | -0.30 | -0.9 | -28 | 25 | 3 vs 4.1 (-1.0) |
| K | **week** | 4 | 3 | 3 | 0 | 1 | +1.24 | 1.7 | **+82** | 25 | 3 vs 1.6 (+1.8) |
| K | all | 10 | 9 | 6 | 3 | 1 | +0.21 | 0.5 | +54 | 25 | 6 vs 5.7 (+0.2) |
| G | backfilled | 16 | 13 | 4 | 9 | 3 | **-0.62** | -3.3 | **-314** | 85 | 4 vs 6.7 (-1.6) |
| G | live (incl. 10-30) | 7 | 6 | 3 | 2 | 1 (+1 open) | +0.19 | 0.5 | +96 | 48 | 3 vs 2.6 (+0.4) |
| G | **week** | 6 | 5 | 2 | 2 | 1 (+1 open) | -0.03 | -0.1 | **+25** | 46 | 2 vs 2.2 (-0.2) |
| G | all | 23 | 19 | 7 | 11 | 4 | -0.36 | -1.8 | -218 | 73 | 7 vs 9.3 (-1.1) |
| A | backfilled (edited) | 9 | 9 | 6 | 3 | 0 | +0.21 | 0.6 | +31 | 26 | 6 vs 4.9 (+0.8) |
| A | **week** | 3 | 3 | 3 | 0 | 0 | +0.70 | 2.4 | **+68** | 30 | 3 vs 1.8 (+1.4) |
| A | all | 12 | 12 | 9 | 3 | 0 | +0.33 | 1.3 | +100 | 27 | 9 vs 6.8 (+1.4) |
| **All** | **week** | 13 | 11 | 8 | 2 | 2 (+1 open) | **+0.52** | 1.7 | **+176 (£132)** | 36 | 8 vs 5.6 (+1.6) |
| **All** | backfilled | 31 | 28 | 13 | 15 | 3 | -0.28 | -1.7 | -311 | 53 | 13 vs 15.7 (-1.1) |
| **All** | all post-holdout | 45 | 40 | 22 | 17 | 5 (+1 open) | -0.02 | -0.2 | -65 | 49 | 22 vs 21.7 (+0.1) |

"Hits vs chance": the expected number of targets hit with no skill, which is the sum over trades of
stop distance / (stop distance + target distance) for a driftless walk. The z uses the binomial variance. Targets
sit on average 0.7-1.3 x the stop distance away, so a high hit rate is partly geometry: a 70% hit rate on targets at
0.7 R is break-even.

### 3.1 Every live idea this week, as the owner would have traded it (0.01 lot)

| Published (UTC) | Author | Bucket | Side | Entry | Stop distance $ | Outcome | Net R | Net $ | Net £ |
|---|---|---|---|---|---:|---|---:|---:|---:|
| 10-30 15:27 (Fri, prior week) | G | 1h | long | market | 55 | target 11-04 | +1.28 | +70.65 | +53.12 |
| 11-01 11:33 | G | 4h | long | limit → market at Sunday open | 62 | target 11-02 | +0.94 | +57.90 | +43.53 |
| 11-01 11:34 | G | 1h | long | limit | – | no fill | 0 | 0 | 0 |
| 11-01 18:18 | K | 4h red box | long (up break) | buy stop | 36 | target 11-02 01:06 | +0.52 | +18.77 | +14.11 |
| 11-02 14:00 | A | 1h | short | sell limit | 35 | target 11-03 | +1.28 | +44.77 | +33.66 |
| 11-02 17:26 | K | update | – | no forward levels | – | – | – | – | – |
| 11-02 17:42 | G | 1h | long | market | 34 | stop 11-03 | -1.02 | -34.83 | -26.19 |
| 11-03 17:28 | G | 4h | – | two-sided, no plan | – | – | – | – | – |
| 11-03 19:05 | K | update | – | no forward levels | – | – | – | – | – |
| 11-04 17:04 | G | 1h | long | market | 28 | target 11-05 | +0.86 | +24.01 | +18.05 |
| 11-04 17:49 | A | 1h | long | buy stop | 30 | target 11-05 | +0.49 | +14.77 | +11.11 |
| 11-04 18:33 | K | update | long | buy limit | 10 | invalidated (target traded first) | 0 | 0 | 0 |
| 11-05 17:05 | G | 1h | long | market | 30 | stop 11-05 | -1.01 | -30.22 | -22.72 |
| 11-05 18:07 | K | update | short | market | 20 | target 11-06 | +2.70 | +53.85 | +40.49 |
| 11-06 12:49 | K | NFP red box | short (down break) | sell stop | 20 | target 11-06 | +0.49 | +9.77 | +7.35 |
| 11-06 12:59 | A | 1h | short | market | 26 | target 11-06 | +0.34 | +8.74 | +6.57 |
| 11-06 15:12 | G | 1h | long | market | 76 | open, marked 20:01 | +0.11 | +8.45 | +6.35 |
| **Week 11-01..11-06** | | | | | | 8 target, 2 stop, 1 open | **+0.52 avg** | **+175.98** | **+132.31** |

Notes the owner should weigh:
- **K's best trade this week (+2.7 R) was an end-of-day update.** Its call was a single line naming a resistance level
  and a lower point of interest, which we read as a short. On a stricter reading it is no call at all. One judgement
  label moves K's week from +$82 to +$28.
- **G's week is gold's week.** Long-only, entered after a $250 low-to-high rally had already started. It broke even
  (+$25 including an open trade) in a strongly rising week, after -$314 in a falling October.
- **A's week (3 of 3 targets, +$68) is the cleanest.** A states its bias in one fixed sentence template (a
  bias word followed by bullish or bearish), and its targets sit at 0.7 x the stop distance. Three of three at a no-skill
  expectation of 1.8 is z 1.4. Five more weeks like it would start to matter; one does not.

## 4. Verdict per author against the prereg decision rules

| Author | BC cell read (3.1) | Forward read (3.3) | Verdict | Reason |
|---|---|---|---|---|
| K | `insufficient`: < 100 ideas in every bucket; the 2020-2025 back catalogue is not scored (no frozen parser) | 2 directional live ideas of 100 needed; 1 publish-day cluster this week of 60 | **INSUFFICIENT** | Mostly two-sided red boxes (prereg type C, excluded from decision reads). The week's +$82 rests on 3 trades, one of them a judgement label |
| G | `insufficient` | 6 of 100 | **INSUFFICIENT; drift-only on the evidence so far** | 100% long. The result tracks the drift baseline exactly (-0.32 R October, +0.00 week). No timing skill is testable while the side never varies |
| A | `insufficient`; all backfilled ideas carry the edit flag (excluded) | 2 of 100 (direction), 3 level trades | **INSUFFICIENT; most promising** | The only author with explicit, parseable single-side calls and a non-trivial short share. October (edited, inadmissible) beat drift by 0.43 R; the live week is +0.70 R on levels. n is far too small |

- **No author passes or trends toward a pass.** Pooled, the post-holdout record (40 fills) sits exactly at the
  no-skill expectation (22 target hits against 21.7 expected, -0.02 R per trade). The good week follows four losing
  ones.
- **Owner-only flags (not decided here).** Any real-money use; any budget raise for the TVA rows (prereg 5).
- **Trial accounting.**
  - 0 registry trials, 0 budget slots, holdout untouched (the 8 Sep ideas were not scored).
  - This descriptive look adds **K +6** to the program ledger (3 authors x 2 reads). It is counted in
    `hypotheses.md` C so that a later TVA forward read carries it.

## 5. What next (ranked by information per unit of risk)

1. **Shadow scorer, automatic (0 trials, 0 risk; recommended now).** This is the scorer the prereg already owes
   (section 6, `research/tv_ideas.py`). Score every live idea nightly by the two reads above. Store the outcomes in
   `aux.db`, and report per author in the daily review's research section (never in the strategy rankings). This
   costs nothing and fills the forward read (>= 60 clusters and >= 100 ideas) by about 2027-02 for an author who
   posts daily. It needs a direction source, so:
2. **An author-specific direction parser for A (one outcome-blind audit, per the T-1 bar).**
   - New evidence justifies re-opening the parked line for A only: A's live posts use a fixed bias template.
   - A written amendment, dated before the audit, would freeze a template parser for A (its fixed bias sentence: a
     bias word, a verb of state or change, then bullish or bearish). It is then audited on a fresh pre-2025-10 sample of
     A ideas: new seed, n = 30, precision >= 95% and coverage >= 50%.
   - K and G stay parked: their text has no single-side template (K: two-sided red boxes; G: long-only route maps).
   - K's red boxes can be scored as type C (OCO) without any parser, because its break-above / break-below target
     lines are a fixed format. That is descriptive only, by prereg 1.1.
3. **No demo arm now.**
   - Prereg 4(ii) allows an arm only once a cell is LEAD with the holdout read passed. ADR 0061's learning class
     would allow a bounded demo arm without that.
   - It adds nothing the shadow scorer does not already give. The forward decision is on the shadow scores of all
     ideas, never on fills, so an arm only buys a broker-cost cross-check. That check is worth less than 0.02 R here,
     because stops of $20-80 make the $0.17-0.23 round trip negligible.
   - Revisit if A passes the parser audit and its shadow record reaches 30 or more ideas at a positive lower bound.
     The spec below is pre-written for that day.

### 5.1 Learning-arm spec, pre-written; NOT to be opened until the condition in 3 holds

| Field | Value |
|---|---|
| id | `TVA-A-L` (learning), control `TVA-A-C` (`params.control: "learning:TVA-A-L"`) |
| role | `learning` (ADR 0061 amendment 2026-11-02 / ADR 0072); demo only; `evidence_class: learning`, never a qualification |
| rule | At `first_seen_utc`: side from the frozen A parser; entry, stop and T1 from the idea's levels under prereg 1.2-1.3(a). Limit or stop entry is emulated engine-side: market order when the touch prints, fill window 1 trading day. Maximum hold 3 trading days. No updates applied |
| control | Same timestamps, same entry type, stop distance, target distance and hold, **random side** (seeded per idea id). Matches the bracket, as ADR 0061 requires |
| sizing | 0.01 lot fixed; refused (never resized) if the stated stop is above `max_stop_usd` or below `min_stop_usd`; `learning_max_open` 1 |
| loss_cap_gbp | 20 x per-trade cap. At a $40 typical stop and 0.01 lot that is about £30 per trade, so about **£600**; tighten to the account's pot if lower |
| until | `since` + 45 days (ADR maximum) |
| stop rule | 300 admissible fills, or the loss cap, or `until`, whichever comes first. A read can only motivate a pre-registered qualification |
| K | +2 rows (arm and control) via `scripts/record_learning_k_rows.py`, inside the quarter's budget |

### 5.2 Build brief for an implementer (shadow scorer first, arm plumbing later)

- **Exists:**
  - the collector, with first-seen snapshots and versions (`goldbot/data/tv_ideas.py`, brain service
    `goldbot-tv-ideas`);
  - `levels` JSON per idea, from the failed parser, not trustworthy;
  - 1m bid/ask stores (Dukascopy, broker, live);
  - the cost of record;
  - the learning-arm loader and stops (`engine/exploration_learning.py`, `research.arms.learning_refusal`);
  - fixed-bracket market entries.
- **Missing:**
  1. `goldbot/research/tv_ideas.py`, the scorer: the prereg 1.2-1.3 logic as in this read, with the test list from
     prereg 6. Those tests cover a same-bar stop-first touch, `no_fill`, `invalidated`, swap nights, the holdout
     refusal, and edited live ideas scored on their first snapshot.
  2. A frozen per-author direction and levels extractor. Start with A's bias template and K's red-box format; G
     stays abstain.
  3. A nightly job writing `tva_scores` to `aux.db`, plus a daily-review section.
  4. For an arm only: **pending-entry emulation**. The execution layer sends market orders only; no MT5 limit or stop
     order type is used anywhere in `goldbot/execution`. The engine would hold an idea-level trigger (price, side,
     expiry) and send a market order through RiskGate on the first 1m touch. The bracket (stop, T1) then goes on the
     position as today, and the order expires at the end of the fill window.
  5. An idea → arm adapter that turns an idea row into a candidate with its own bracket, not the arm's fixed one.
     Learning arms today assume one fixed bracket per arm, so the control-matching rule needs a per-trade bracket
     variant. Quant review is required.

## 6. Reproduction

Scratch scripts, labels and scores are kept outside the repo, because they hold third-party text and derived levels.
Label file sha256 `3064a8b446f35d29c1df686cefc721eb3cfb1838903c4d0776a2d77810015071`. Seeds: permutation 20261106.
The numbers above re-derive from the brain DB plus the bar stores listed in section 1.
