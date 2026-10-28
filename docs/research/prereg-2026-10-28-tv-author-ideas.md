# TradingView author ideas: pre-registration (2026-10-28)

Principal quantitative strategist, 2026-10-28. Owner directive of the same day: the system should follow three
TradingView authors whose daily and weekly gold ideas the owner uses: **KnightsofGold**, **Goldviewfx** and
**ArmanShabanTrading** (below: **K**, **G**, **A**). A data-engineer lane is building the collector in parallel. For
each idea it records the published time, direction, symbol, timeframe, entry/stop/target parsed from the text where
present, and `backfilled` and `edited` flags.

**Outcome-blind attestation.** This file was written before any idea record existed in the repo or on any host
we control. No idea text, count, direction, level or outcome was seen. Nothing in sections 1-6 may change after the
first idea is scored. Changes go in a dated amendment written before the data it touches is read, as in
`prereg-2026-10-27-scalp-volume.md` sections 7-10.

Related: ADR 0058 (discovery funnel, G3 `prior_informed`, G5 `holdout_contaminated`, forward read 4.4-4.5; proposed,
on branch `worktree-agent-a4e465eacc0cd457a`), `goldbot/execution/costs.py` (`research_costs`),
`goldbot/research/gates.py` (`HOLDOUT_MIN_T = 1.65`), `config/settings.yaml` (`trial_budget_overrides`), and
`state-of-the-art.md` C3.

## 0. Prior evidence and why this is worth a trial

| Claim | Source | Sample, instrument | Net of costs? | Grade |
|---|---|---|---|---|
| About 28% of StockTwits "finfluencers" are skilled (+2.6%/month abnormal), 16% unskilled and 56% antiskilled (-2.3%/month). Antiskilled ones have **more** followers, and fading them pays out of sample | Kakhbod, Kazempour, Livdan, Schürhoff, "Finfluencers", SFI WP 23-30 (2023), https://econpapers.repec.org/RePEc:chf:rpseri:rp2330 | StockTwits, US equities (sample years *unverified*) | No (abnormal returns, gross) | Single study, working paper |
| Crowd opinions on Seeking Alpha predict returns | Chen, De, Hu, Hwang (2014), *Review of Financial Studies* 27(5) *(unverified page detail)* | Seeking Alpha articles and comments 2005-2012, US equities | No | Single study |
| Less than 1% of day traders are predictably profitable net of fees | Barber, Lee, Liu, Odean (2014), *Journal of Financial Markets* 18 *(unverified issue)* | Taiwan day traders 1992-2006 | Yes | Robust in spirit (retail skill is rare; consistent with ESMA's 74-89% losing retail CFD accounts) |
| No published, cost-net evidence on TradingView idea authors, or on gold-specific signal sellers | Searched 2026-10-28; none found | — | — | Gap |

Reading of the prior: skill among public authors exists but is rare. Popularity is a weak or **negative** signal
of skill (Kakhbod et al.). Gold rose from about $1,500 (2020) to about $4,000 (2026), so a bullish author looks
skilled by default. Prior P(at least one author shows cost-net skill by the forward rule) ≈ 10-15%. That is
judgement, not measurement.

The trial is still worth its cost for three reasons:
- The owner already acts on these authors, so the answer changes behaviour whichever way it falls.
- Costs are not the binding constraint here. Daily and weekly ideas carry stops of about $10-40, so the round trip of
  $0.14-0.23/oz (`cost-model-1m-2026-10-25.md`) is about 0.01-0.02 R. Swap is the material cost (section 1.4).
- The data is free, and the forward read is the cleanest test we can run.

## 1. Scoring each author's calls, net of costs, point in time

### 1.1 The idea record, and what is admitted

These fields come from the collector, as frozen in its own spec: `idea_id`, `author`, `published_utc`,
`first_seen_utc`, `symbol`, `timeframe`, `direction`, `entry`, `stop`, `targets[]`, `backfilled`, `edited`, and the
text hash at first sight.

- **Symbol.** Spot gold symbols (XAUUSD from any broker, GOLD, OANDA:XAUUSD and similar) are scored on our
  Dukascopy XAUUSD 1m bars.
  - Futures symbols (GC1!, GC, MGC) are scored **direction-only** (1.3). Their levels carry a futures basis that we
    cannot remove point in time. The levels are discarded, never shifted.
  - Every other symbol is excluded and counted.
- **Direction.** It must be long or short. "Neutral" or "either way" ideas are excluded and counted.
- **Conditional ideas** (for example "buy above X, sell below Y") are type **C**. They are scored descriptively as
  an OCO pair of stop entries and are excluded from every decision read.
- **Type L** (levels): parsed entry, stop and at least one target, on spot. **Type D** (direction-only): everything
  else that is admitted.
- **Sanity.** An L idea is demoted to D, and the demotion is counted, if any of these holds:
  - the stop is on the wrong side of the entry, or the target is;
  - |entry − price at publish| > 3 × ATR14(1d);
  - the stop distance is below 2 × the round trip.
- **Timeframe bucket.** **I** (intraday: chart TF up to 4h), **D** (1d), **W** (1w and above). A missing timeframe is
  bucketed as D.
- **Parser freeze.** The parser is frozen by hash before the first score. A staff audit of 60 random ideas, 20 per
  author, reads text only, with no price after publish. The parser must reach ≥ 95% agreement on direction and ≥ 90%
  on levels, or it is fixed and audited again, still outcome-blind. The audit sample is drawn from pre-holdout ideas.

### 1.2 Entry: the first tradable price

- **Backfilled ideas.** Entry is the open of the first 1m bar that starts at or after `published_utc + 60 s`. The
  60 s stands for reading and reaction latency; the collector's poll lag is not known for history.
- **Live ideas** (`backfilled = false`). Entry is the first 1m bar at or after `first_seen_utc`, our actual
  latency. As a descriptive check, the same idea is also scored at its `published_utc` entry. The gap between the two
  is the capture loss, reported per author.
- **Market versus limit for L ideas.**
  - If the publish-time price lies between the stop and the entry, or within 0.25 × ATR14(1d) of the entry, the idea
    enters at market.
  - Otherwise a **limit at the stated entry** is placed. It is valid for the fill window: I 1 trading day, D 3,
    W 10.
- **Never filled.** If the limit is not touched in its window, the outcome is `no_fill` and scores 0 R.
  - If the stop or the target trades before the entry fills, the idea is `invalidated` and also scores 0 R.
  - The decision statistic is mean net R **per filled idea**. The fill rate and mean R per issued idea (zeros
    included) are reported next to it. A high fill rate on limits placed in the trend's path is part of the
    reading, not noise.
- **D ideas** enter at market.

### 1.3 Outcomes

**(a) Author's levels (type L).**
- Stop = the stated stop. Target = **the first stated target (T1)**. Later targets are descriptive only.
- R = |fill − stop|.
- The first barrier touched on 1m bid/ask (mid ± the bar's half spread) decides the outcome. If both are touched
  inside one 1m bar, the **stop is assumed first**.
- Maximum hold is I 3 trading days, D 10, W 20. At the cap the position is marked at the 1m close.
- No author "updates" posted after publish are applied (no moved stops, no partials). The idea is scored as written
  at first sight.

**(b) Fixed horizon in R units (type D, and the uniform direction read of every idea).**
- R = ATR14 on daily bars as of the prior daily close (point in time).
- Outcome = side × (exit mid − entry fill) / R, minus costs in R.
- Horizon H: I 1 trading day, D 5, W 20.
- A catastrophic stop at 2R, touched on 1m bars, stands in for any real trader's risk control.

**The decision outcome per idea** is (a) for L ideas and (b) for D ideas. The **direction read** applies (b) to
every idea, L included. It is secondary and descriptive, and it is never a pass route.

### 1.4 Costs (`goldbot/execution/costs.py`)

- The 1m bar spread is charged at entry and exit. On top of it comes `research_costs(settings, table)`: the
  measured costs-v1 table's extra cost per oz (slippage + commission) when it exists, else the settings priors. The
  source line it returns is recorded with every read.
- Swap is charged for each rollover held via `SwapSpec` (measured if the table has it, else the settings prior:
  long −$60/lot/night ≈ −$0.60/oz, triple on the triple weekday).
- For a 5-day D long on a $20 stop that is about −0.15 R. **Swap, not spread, is the cost that can decide a long-biased
  author**, which is why it is charged in full.
- **Cost stress:** 1.5 × (spread + extra), plus swap unchanged.

### 1.5 Strata: backfilled versus live, edited versus unedited

| Stratum | Definition | Role |
|---|---|---|
| **BC** back catalogue | `backfilled = true`, published 2020-01-01 .. 2025-09-30, plus 2026-10-01 .. the collector's start | Screen. Can **stop** an author cell; can never pass one (1.6) |
| **HO** holdout | published 2025-10-01 .. 2026-09-30 | Sealed. One read only, for a cell that reached LEAD (section 5) |
| **LV** live | `backfilled = false`, first seen by the collector after its start | **Decisive** (forward read, section 3.3) |
| Edited | `edited = true` (text hash changed after first sight, or TradingView's edit marker on a backfilled idea) | Excluded from every decision read; scored separately |

- For live ideas, only the first-seen snapshot is ever scored. A later edit cannot change a live score.
- **Hindsight-edit check:** if edited ideas beat unedited ones by more than 1 SE in BC, the read reports it as
  evidence of after-the-fact editing.

### 1.6 Survivorship in the back catalogue

Three biases inflate BC, and each one favours the author:

1. **Deleted or hidden ideas are invisible.** TradingView house rules limit author deletion of published ideas, and
   moderators can hide them *(unverified: platform behaviour not confirmed for 2020-2026)*. The losers removed are
   unobservable.
2. **Author selection.** The owner chose these three, out of thousands, after watching them, most likely during
   2025-2026. Holm across 3 authors does not correct for the unseen universe he chose from.
3. **Edits** (1.5).

How the read handles them:
- **Asymmetry.** BC can stop a cell or make it a LEAD; it can never pass one. An author who fails even the inflated
  record is stopped. One who passes it has earned only a forward read.
- **Deletion break-even d\*.** Take the share of missing ideas that, each scored −1R and added at the cell's
  observed rate, would bring the cell's lower bound to 0. LEAD needs d\* ≥ 10%.
- **Deletion-rate estimate (descriptive).** Two free sources:
  - Wayback Machine snapshots of each author's profile idea count, compared with the visible count;
  - the change in the profile count over the live period (a drop means removals).
  If the estimated deletion rate is at or above d\*, the LEAD is withdrawn.
- **Selection** is handled only by the forward read, which starts after the selection, at a stricter t (3.3).

## 2. Baselines (computed for every cell, at the same timestamps)

For each idea i, compute the trade as given, R_i(side_i), and its **mirror**: the opposite side with the same entry
logic and the same stop and target distances, R_i(−side_i). Both are net of their own costs, swap included, so the
swap asymmetry between long and short is charged honestly.

1. **Random direction:** the mean of 0.5 × (R_i(L) + R_i(S)). It is about −cost, plus any asymmetry in the
   geometry.
2. **Drift (always long):** the mean of R_i(L) at the same times and with the same geometry. Over 2020-2026 this is
   expected to be clearly positive gross. It is the hurdle a "bullish gold" author must clear.
3. **Primary null: side permutation inside the author's own record.** Shuffle the author's sides across that
   author's ideas within the cell and within each calendar quarter, 10,000 times. This keeps the author's long share
   and its drift exposure per quarter. The p-value is the share of shuffles whose mean net R is ≥ the observed one.
   It tests **timing and direction skill beyond a long bias and the gold bull market**.
4. Reported: the long share, the mean net R of longs and of shorts separately, and the author minus drift, with a
   block-bootstrap CI.

## 3. Decision rules (fixed now)

**Cells:** 3 authors × 3 buckets (I, D, W) = **9 cells**.
- A cell needs at least 100 filled, unedited, decision-eligible ideas in BC to be read. Otherwise it is
  `insufficient`: not read, but still counted in m and K.
- **Statistic:** mean net R per filled idea. Standard errors are block-bootstrapped (2,000 resamples) with blocks of
  one publish-day (UTC) for I, and one ISO week for D and W, because overlapping holds make neighbouring ideas
  dependent.
- **Multiple testing:** Holm-Bonferroni across the m = 9 cells at family-wise α = 0.05, one-sided.

### 3.1 Back-catalogue read (BC)

- **LEAD** needs all of the following:
  1. the Holm-adjusted one-sided lower bound on mean net R is above 0;
  2. the side-permutation p-value passes at the Holm-adjusted level;
  3. mean net R is still above 0 under the 1.5× cost stress;
  4. mean net R is above 0 in at least 4 of the 6 calendar years 2020-2025, and no single year carries more than 50%
     of the total R;
  5. d\* ≥ 10% (1.6).
- **INTERESTING** (no LEAD; shadow and report only; no arm, no feature promotion) covers:
  - gross skill that is net negative;
  - beating random direction but not the drift baseline;
  - skill in the direction read (b) only;
  - an unadjusted lower bound above 0 that fails Holm;
  - edited ideas beating unedited ones.
- **ANTI-SKILL:** a Holm-significant **negative** mean net R. It is recorded as interesting. Fading the author is
  **not** authorised here: fading pays a second round trip and needs its own pre-registration with new evidence.
- **STOP:** the cell is not a LEAD and the plain one-sided upper bound on mean net R is at or below 0.05 R. The
  cell is retired with its trial number, its live ideas keep being collected (free), and **no** forward verdict is
  read. Re-reading it later needs new evidence and a new trial.
- Anything else is **WEAK**. The cell gets a forward read only with no demo arm and no use in (4).

### 3.2 Holdout read (HO), LEAD cells only, once

- Same statistic, one-sided t ≥ `HOLDOUT_MIN_T` (1.65).
- The owner chose these authors during or just before this window, so HO is selection-contaminated, like ADR 0058
  G5. **HO can stop a LEAD, but cannot confirm one.** A fail ends the cell exactly as STOP does.

### 3.3 Forward read (LV): the only route to SKILL

- **Universe:** every live, unedited, decision-eligible idea from the collector's start, scored by the frozen
  scorer. The decision is on the shadow score of **all** ideas, never on arm fills, so pot or cap refusals cannot
  choose the sample.
- **Unit:** per author, pooled across buckets ("strategy = family"), with buckets as strata. m = 3, under Holm.
- **When:** one read at the later of ≥ 60 publish-day clusters and ≥ 100 resolved ideas. The deadline is
  **2027-10-28**; a read then with fewer clusters is INCONCLUSIVE, never a pass.
- **SKILL** needs all of the following:
  1. the Holm-adjusted (m = 3) one-sided **t ≥ 2.5** on mean net R (the ADR 0058 4.5 bar for selection-contaminated
     sources);
  2. the side-permutation null passes;
  3. mean net R is above the drift baseline;
  4. mean net R is at least 50% of the BC estimate if the author was a LEAD (decay check). For an author who was
     WEAK, condition 4 is replaced by t ≥ 3.0.
- **FAIL:** the read is done and SKILL does not hold. The author is retired as a signal source; the collector keeps
  running only if another use needs it.
- **Global K.** The 9-cell Holm is the family-wise control for *this* question (owner-specified, not chosen by our
  search). **Promotion to live is unchanged:** `promotion.py` gates, `population` DSR_PROMOTE at the population K, and
  `registry.passed_gates`. A SKILL verdict alone never makes anything live-eligible.

## 4. Uses, only if skill is found

**(i) Consensus or agreement feature for meta-labelling.** These are point-in-time columns joined via `asof_join` on
`available_utc`:
- `tva_dir_<author>`: +1 / −1 / 0 while an idea is live (from its entry to its own exit or expiry under 1.3);
- `tva_consensus`: their sum;
- `tva_agree`: the sign agreement of `tva_consensus` with a family candidate's side.

`available_utc` is `first_seen_utc` for live ideas and `published_utc + 60 s` for backfilled ones.
- The columns are added as **one group** in the next feature-discovery run (K_eff +1), and only if at least one
  author is SKILL, or LEAD with HO passed. Training rows fall in 2020-01 .. 2025-09; holdout rows are masked like
  every other feature.
- The group needs ≥ 1,000 family candidates with a non-zero `tva_consensus` in its training folds, or it is not run.
- If the 2019 and earlier columns are NaN, the meta-labeller sees missing data, never zero.

**(ii) "Author idea" demo arm** (ids `TVA-K`, `TVA-G`, `TVA-A`, each with buckets as strata):
- **Rules:** direction, entry logic, stop, T1, fill window and maximum hold exactly as in 1.2-1.3, from the
  first-seen snapshot, acting at `first_seen_utc`. D ideas use (b) with the 2R stop.
- **Sizing:** the arm's per-trade cap inside the existing caps (1% rule, `max_stop_usd` / `min_stop_usd`, pots,
  RiskGate). An idea whose stop does not fit is refused and logged, never resized past a cap. max_open is 1 per
  author.
- **Accounts:** demo only (`load_arms` refuses live-class accounts).
- **Start:** a cell may run its arm once it is LEAD with HO passed. Fills are a broker-cost cross-check on the
  forward read, never the decision sample.
- **Live:** only through the unchanged promotion path. Real-money entries stay **owner-confirmed (90 s)**, exits are
  never gated, and sizing and the live unlock are the owner's.

**(iii) LLM extraction of stated reasoning into spec-language hypotheses (ADR 0058 G3).**
- An extractor turns each idea's stated reasoning (for example a "sweep of Asia low into a bullish FVG") into a spec.
- It is **outcome-blind**: it never sees any idea's result, and it samples ideas by frequency, never by success.
- Specs carry `prior_informed: true` and `author_derived: <author>`. They share G3's cap of 20 specs a week (they do
  not add to it), count in the discovery ledger's K, are selected only on the explore window 2010-2017 (the
  *mechanism* has history there even though the ideas do not), and take the stricter forward read.
- A spec that reproduces a retired idea is refused by the novelty check. Examples: R-13 EQH/EQL magnets, the
  TVR-1 and TVR-2 SMC structure rules, and others in hypotheses.md.
- **Runs only if** at least one author is SKILL, or LEAD with HO passed. Without skill, their reasoning is folklore
  we have largely tested already.

## 5. Trial accounting (K) and windows

| Row | What | Budget slots | K |
|---|---|---|---|
| TVA-BC | One registry row: the BC screen of 9 cells, plus the HO read of LEAD cells | 1 | +9 (k_eff 9) |
| TVA-K, TVA-G, TVA-A | One row per author: the forward read, and the demo arm under the same id if one runs | 3 | +3 |
| **Total** | | **4** | **+12** (K about 397 → about 409) |

- Each row is written before its first score (BC) or before its first live idea is scored (forward).
- Conditional uses add only through their own ledgers: (i) K_eff +1 in the discovery run that uses it; (iii)
  G3's ledger.
- **Budget (flagged; the program director decides, under the owner directives of 2026-10-18 and 2026-10-28 and the
  Vantage precedent).** 2026Q4's override is 145, and all of it is allocated to the waves and the scalp-volume ids.
  - **Recommended:** TVA-BC from a 2026Q4 raise of 145 → 146. The three forward rows go to **2027Q1**, where their
    reads land in any case.
  - The alternative is a raise of 145 → 149 now. Raising the budget is an owner-class decision. The recorded
    precedent lets PD + PO raise it under the owner's standing "backtest as much as needed" directive, so it is
    flagged here and not assumed.
- **Explore window.** It does not apply: the ideas start in 2020, and nothing is selected. The scorer has no tunable
  parameter; every constant in 1.2-1.3 is fixed above. ADR 0058's explore rule exists to stop selection on later
  data. With no selection there is nothing for it to protect, so BC (2020-01 .. 2025-09) acts as a confirm-style
  screen. It cannot reach SKILL, which closes the gap.
- **Holdout (2025-10-01 .. 2026-09-30).** It is respected, with one bounded exception.
  - The scorer refuses HO-window ideas through the existing `holdout_window` guard. The one exception is the single
    HO read of a LEAD cell (3.2), which is logged in the registry row.
  - Why the exception is justified: HO is used only as a one-way stop, never as confirmation. The ideas are
    external signals, so the read reveals no goldbot family's holdout performance.
  - Any model that uses (i) keeps HO rows masked.
- **Live data after 2026-10-01** lies outside the holdout and is clean (the authors were chosen before it).

## 6. Timeline, owed work and flags

- **Owed before the first score:**
  - the collector (data-engineer, in flight);
  - the parser freeze and the outcome-blind audit (staff, 1.1);
  - the scorer `research/tv_ideas.py` (implementer, failing tests first). Its tests cover synthetic bars for: a
    same-bar stop-first touch, `no_fill`, `invalidated`, swap nights, the HO refusal, and edited live ideas scored
    on their first snapshot;
  - a quant-reviewer pass on the scorer and on this file.
- **Earliest BC verdict (STOP, LEAD or WEAK): about 2026-11-09**, once the backfill, the parser freeze and the
  reviewed scorer exist.
- **Earliest SKILL verdict: about 2027-02-05.** That assumes an author who posts most trading days from 2026-10-29:
  60 publish-day clusters by about 2027-01-21, plus up to 10 trading days for the last D idea to resolve. Less
  frequent posters take longer. W-only flow is unlikely to reach 60 weekly clusters before the 2027-10-28 deadline,
  and is then INCONCLUSIVE by rule.
- **Flags (not decided here):**
  - the budget raise (section 5);
  - the TradingView access route and its terms-of-service risk (already accepted by the owner for the direct pull,
    2026-10-18, memory "TradingView direct pull"). Idea text stays local, under the same rule as market data;
  - any live use (owner-only).
