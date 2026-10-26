# Trading knowledge base (living)

Started 2026-10-18 by the strategy researcher. Owner's brief: the owner's own trading notes are the **base**; build on
them with outside evidence and keep improving the base every research cycle.

This folder answers one question per claim: *what does the trading folklore say, what does the outside evidence say,
and what do our own data say?* It is not a strategy list. A claim enters a model only through the P4 screen and a
pre-registered trial (`docs/research/hypotheses.md`), like everything else.

## Files

| File | Covers |
|---|---|
| [structure-and-liquidity.md](structure-and-liquidity.md) | Swings (HH/HL/LH/LL), breaks of structure and changes of character, equal highs/lows, fair value gaps, order blocks, premium/discount and the 50-61.8% retracement band, liquidity sweeps and false breakouts |
| [support-resistance-and-zones.md](support-resistance-and-zones.md) | Horizontal levels and zones, sr_channels (the "boxes"), break vs rejection at a box, role reversal and retests, dynamic MAs, round numbers, floor pivots, prior day/week extremes, channels, level-to-level targets |
| [money-flow-and-volume.md](money-flow-and-volume.md) | mfi (money flow index) zones, extremes, divergence and crosses; high/low-volume candle classes; the "volume veto"; what CFD tick counts can and cannot tell us |
| [candlestick-and-chart-patterns.md](candlestick-and-chart-patterns.md) | Pin bars, engulfing, inside bars, false-break bars, dojis and the rest; triangles, head and shoulders, double/triple tops and bottoms |
| [multi-timeframe-and-confluence.md](multi-timeframe-and-confluence.md) | Map / trigger / refine timeframes, "intraday must agree with daily", TLS (trend-level-signal) and confluence checklists |
| [risk-and-trade-management.md](risk-and-trade-management.md) | Fixed-fractional risk, minimum reward:risk, partial exits and break-even stops, never widening a stop, entering on the close, not chasing |
| [gold-specific.md](gold-specific.md) | XAUUSD microstructure: sessions, opens, auctions (fixes), spreads and ATR by session and hour, price discovery, round numbers, news, macro drivers, sweeps on gold |

Ranked **new hypotheses** suggested by the evidence (not yet in `hypotheses.md`) are at the end of this file.

## Claim format (every topic file)

Each claim is one table row:

| Column | Content |
|---|---|
| Claim | The idea in our words, stated so it could be tested |
| Course baseline | What the owner's notes teach, **paraphrased** (never quoted) |
| External evidence | Author, year, venue, URL, sample, instrument, and whether the result is **net of costs** or gross |
| Grade | A / B / C / D (below) and the direction: *for*, *against* or *none found* |
| Our data | What goldbot has measured: a trial number, a study file, a feature, or "pending" |

### Evidence grades

| Grade | Meaning |
|---|---|
| **A** | Replicated in peer-reviewed work across independent samples or markets, with out-of-sample or data-snooping control. Ideally net of costs |
| **B** | Peer-reviewed but single-sample, or replicated only gross of costs, or in a related market (FX, equity index) rather than gold |
| **C** | Working paper, thesis, conference paper, industry body, or a test that tuned its parameters in-sample |
| **D** | Practitioner, vendor, course or forum claim with no published method; anecdote |

A grade describes the **evidence**, not the idea. "A *against*" means strong evidence that the claim fails (for
example, simple technical rules after data-snooping control and costs).

### Citation markers

- No marker: the source record (abstract, publisher page, repository entry or full text) was located during the
  2026-10-18 build, and the summary follows it.
- *(unverified)*: cited from memory or from a secondary summary. Treat it as a lead, not as evidence, until someone
  fetches the primary source and removes the marker.

### "Our data" status values

- **tested: #n / R-xx**: a registry trial or a retired row in `hypotheses.md` section B.
- **study: file**: a descriptive study in `docs/research/` (no trial).
- **feature: module**: built as a point-in-time feature, not yet screened.
- **checked: pa-claims**: the descriptive check is in
  [`../pa-claims-2026-10-18.md`](../pa-claims-2026-10-18.md) (landed 2026-10-18; results in "Results fed back" below).
- **not checked**.

## Naming and confidentiality rules

- The owner's course notes stay **local only** (`~/tmp/owner-notes/`). Nothing here copies their text, and no course,
  author, vendor or indicator product name appears in this folder.
- Our names: **smc / structure** (structure indicator), **sr_channels / boxes** (support-resistance channel
  indicator), **mfi / money flow** (money flow oscillator), **TLS** (trend-level-signal pre-trade framework).
- The owner's private tools the notes mention but we do not have are called "unknown private levels" and are never
  guessed at.

## How it is maintained (each cycle)

The strategy researcher owns this folder. A **cycle** is one research batch, at least monthly, and always before the
quarterly state-of-the-art refresh (`docs/research/state-of-the-art.md`), which this folder feeds.

1. **Intake.** New owner notes arrive locally. Paraphrase each new claim into the right topic file as a row with
   "Our data: not checked". Never paste.
2. **Evidence refresh.** For each topic, run a fresh literature search (peer-reviewed first, then working papers,
   then practitioner sources, flagged as weaker). Fetch the primary source for any *(unverified)* row that a decision
   now depends on, and remove the marker or downgrade the row.
3. **Data status.** Pull new verdicts from the registry, `hypotheses.md` and dated studies (for example
   `pa-claims-*.md`). Update every affected "Our data" cell. A claim that failed a pre-registered trial is marked
   **retired** with the trial number, so it is not re-tested without new evidence.
4. **Re-grade.** Change a grade only with a cited reason in the change log below.
5. **New hypotheses.** Re-rank the list at the end of this file. A hypothesis moves to `hypotheses.md` (as
   *proposed*) only when the research director accepts it, with its mechanism and its trial and K_eff cost. Proposed
   rows add nothing to K; a diagnostic that reads outcomes adds its K_eff to the configuration it informs.
6. **Change log.** One line per cycle: date, rows added, grades changed, statuses changed.

The knowledge base never spends a trial, never touches the holdout (2025-10-01 .. 2026-09-30) and never changes a
gate. Raising the trial budget, adding instruments, paid data and going live remain owner decisions.

## Cross-cutting findings (read these first)

1. **Data snooping is the main enemy of chart-based rules.** Simple technical rules on the Dow looked strong for a
   century (Brock, Lakonishok, LeBaron 1992), but the best rule picked from a large universe was not significant out of
   sample once the search was accounted for (Sullivan, Timmermann, White 1999), and with false-discovery control any
   in-sample edge was wiped out by low transaction costs and could not be picked in advance (Bajgrowicz & Scaillet
   2012). Sources in the topic files. Grade **A against** standalone simple rules net of costs.
2. **Where edges were real, they decayed.** FX technical rules paid in the 1970s-80s and had lost their edge by the
   early 1990s (Neely, Weller, Ulrich 2009, *JFQA* 44(2), https://www.cambridge.org/core/product/9D336CDCA83233819EB5CDD0F4BC0DAA;
   FX, gross). Short-term trend has not paid since about 2009 on small-tick contracts like gold (Kurth et al. 2026,
   arXiv 2607.01550, in `state-of-the-art.md` A1).
3. **The only well-documented microstructure mechanism behind levels is order clustering.** Take-profit orders
   cluster *at* round numbers and stop-loss orders just *beyond* them; prices tend to reverse at take-profit clusters
   and accelerate through stop clusters, over hours not days (Osler 2003, 2005; FX dealer order book). Every level,
   sweep and "liquidity" idea in the owner's notes is strongest when it is restated as this mechanism.
4. **Smart-money concepts (fair value gaps, order blocks, premium/discount) have no peer-reviewed test.** Searches on
   2026-10-18 found only vendor blogs with undisclosed methods.
5. **Our binding constraint is gross edge per trade, not the rule's elegance.** At the cost prior a trade needs about
   **0.71 $/oz ÷ stop $** in gross R to break even: about 0.09 R at a 1.0 × ATR15 stop today ($8), 0.11 R at the
   £5-risk cap ($6.65 stop) (`tight-stop-entry-study-2026-10-18.md`). No 15m or 1h family we have tested shows more
   than about +0.06 R gross.
6. **Confluence collapses event counts.** Each extra checklist condition halves (or worse) the events. The owner-style
   multi-timeframe rules found 0-18 candidates in 15 years against a 1,500 floor (H-34, H-35, withdrawn). A checklist
   that rarely fires cannot be tested, whatever its merit.

## Ranked new hypotheses (not yet in hypotheses.md)

Ranked by expected information per unit of statistical budget, then by how soon a decision depends on them. All cost
**0 registry trials**; the K_eff each adds is stated. IDs are provisional (KB-n); the integrator gives them H-numbers
when the research director accepts them. Every reading rule below is written before any data are looked at, and every
analysis stops at 2025-09-30.

### KB-1. Retest selection: does "wait for the retest" keep the weak breaks and drop the strong ones?

- **Mechanism.** Retest entries buy cheaper and allow a tight stop at the broken level, which the owner's notes
  recommend for every break trade. But breaks driven by informed or speculative flow tend to continue without looking
  back (Llorente, Michaely, Saar, Wang 2002, volume-return relation; see money-flow file), so requiring a retest may
  select exactly the breaks that lack follow-through. That is adverse selection, and it would sink H-25 (limit entry
  at the broken level) before it is dispatched.
- **Design.** Diagnostic on the 15m and 1h box breaks and prior-day-extreme breaks already computed for R-04 and the
  B03 batch (research window 2010-01 .. 2025-09). Split breaks into "retested within N bars" (N = 4 on 15m, 3 on 1h,
  fixed now) vs "never retested". Report the forward return in ATR at 16 and 32 bars from the break close, and the
  share of the 95th-percentile moves that come from the never-retested group.
- **Reading rule.** If the never-retested group carries ≥ 70% of the gross forward move, mark H-25's limit-at-level
  entry as structurally adverse-selected and lower its prior in its row (no new trial, no re-test of R-04). If the two
  groups' forward returns differ by < 0.02 ATR (95% CI includes 0), record "retest neutral".
- **Cost.** 0 trials; K_eff +1, recorded on H-25's row (it informs that configuration).

### KB-2. Sweep depth: stop cascade vs absorbed sweep

- **Mechanism.** Osler 2005 finds stop-loss-triggered moves larger and longer-lasting than take-profit reactions. A
  sweep beyond a prior extreme that runs deep and closes beyond is a cascade (continuation). A shallow sweep that
  closes back within a bar or two means the stops were absorbed (reversal). H-08 tests only the fade side.
- **Design.** Three point-in-time features added to O-4's `liquidity` group before its map is frozen:
  `sweep_depth_atr` (largest penetration beyond the prior session/day extreme, in ATR15), `reclaim_bars` (bars until a
  close back inside; capped), `sweep_activity_z` (tick count of the sweep bars vs the same time of day, KB-3). Signs
  fixed now: shallow (< 0.5 ATR15) with reclaim ≤ 2 bars → fade; deep (> 1.0 ATR15) with a close beyond →
  continuation.
- **Reading rule.** The group survives only if stability selection keeps it in ≥ 60% of training folds **and** both
  signs match the ones above. A survivor gets at most one O-5/O-6 survivor-slot trial under the existing O-4 rules.
- **Cost.** 0 trials; K_eff 0 if added before O-4's map sign-off (inside the existing `liquidity` group), else +1.

### KB-3. Time-of-day-normalised activity for mfi and the "volume veto"

- **Mechanism.** The volume-return literature says volume decides whether a move continues or reverses (Campbell,
  Grossman, Wang 1993; Llorente et al. 2002). But gold's activity has a strong hour-of-day shape (Batten et al. 2017),
  so a raw tick-count mfi or "volume > 150% of the last 10 bars" mostly flags the London and New York opens. The
  information, if any, is in activity **relative to the same time of day**.
- **Design.** One opt-in feature group `activity_tod` for O-4: `tick_z_tod` (15m tick count over the median of the
  same 15-minute slot in the prior 20 trading days), `mfi_tod` (money flow with `tick_z_tod` as volume, n = 12),
  `approach_activity` (mean `tick_z_tod` over the 4 bars approaching a level). Prediction fixed now: level touches
  reverse more often after low approach activity; breaks continue more often after high approach activity.
- **Reading rule.** Survives on the O-4 group rule (≥ 60% of folds) with the predicted sign; otherwise the volume
  veto and the candle volume classes are recorded as failed on our data.
- **Cost.** 0 trials; K_eff +1 in O-4 (one group). Overlaps N1 `tod_periodicity`: build on its slot medians.

### KB-4. Take-profit front-running: place targets just before levels

- **Mechanism.** Take-profit orders cluster exactly at round numbers, and prices tend to reverse there (Osler 2003).
  A target at or beyond a $10/$50/$100 level or a prior extreme should fill less often than one a little before it.
  This touches every family's exit and claims no alpha.
- **Design.** Descriptive, 1m bars 2010-01 .. 2025-09: for each approach to within 0.25 × ATR15 of a level, the
  probability of trading at least 0.1 × ATR15 *beyond* it within 16 bars, against placebo levels offset by a fixed
  non-round amount ($3.70 and $6.30) at matched distance and volatility.
- **Reading rule.** If the real levels' failure-to-exceed rate is ≥ 3 percentage points above placebo, with a 95% CI
  excluding 0 in **both** 2010-17 and 2018-2025-09, add "target = level − 0.1 × ATR15" to the exit library as the
  default for level-based targets (applied to passers only). Otherwise record "no front-running benefit".
- **Cost.** 0 trials; K_eff +1, recorded on the first configuration that adopts it.

### KB-5. Equal highs/lows as liquidity magnets

- **Mechanism.** Equal highs or lows are the most visible levels, so stops pile up just beyond them (Osler's
  clustering, extended to salient chart levels; the extension is our inference). If so, price should be drawn through
  them more often than through a single swing extreme at the same distance, and accelerate after crossing.
- **Design.** Base rate on 15m and 1h: P(trade through within 16 bars) for EQH/EQL (replica `eqh`/`eql`, threshold
  0.1 × ATR) vs single confirmed swing extremes matched on distance in ATR, hour and ATR tercile; plus the 4-bar
  forward return after the cross.
- **Reading rule.** Magnet lift ≥ 5 percentage points (CI excluding 0 in both halves) → `eqh_dist_atr`/`eql_dist_atr`
  become candidate target levels for passers and the idea is written up as a one-trial "run to liquidity" proposal
  for a later quarter, **only if** a count first shows ≥ 1,000 events. Otherwise retire "EQH/EQL as magnets".
- **Cost.** 0 trials now; K_eff +1. A later trial costs 1 and needs the research director's ranking.

### KB-6. When is the day's extreme already in? (London first-extreme timing)

- **Mechanism.** Liquidity concentrates at the London open and the New York data slot; practitioners claim the
  day's high or low is often set in London's first hours (grade D). If true and stable, it is context for H-17 (trend
  day caught early) and for runner exits, not an entry.
- **Design.** Point-in-time feature for H-17's map: `hours_since_day_high`, `hours_since_day_low`, and the empirical
  probability (training folds only) that the current day extreme stands to the 16:30 New York close, by hour.
- **Reading rule.** Enters H-17 only as a context feature; it is judged by H-17's own rule. No separate trial.
- **Cost.** 0 trials; K_eff +1 inside H-17 if adopted.

### KB-7. Compression before expansion for the opportunity score

- **Mechanism.** Practitioners say tight ranges (inside bars, narrowing triangles) precede expansion. Volatility
  clustering predicts the opposite (quiet follows quiet). The sign is genuinely uncertain, so the test is two-sided.
- **Design.** One feature group `compression` (inside-bar run length, NR4/NR7 flags, 4h/1d range over its 20-bar
  median) in H-16's range forecast, judged as an increment over the HAR baseline.
- **Reading rule.** Adopted only if out-of-sample range-forecast error falls by ≥ 2% against HAR in both halves of
  the research window.
- **Cost.** 0 trials; K_eff +1 inside H-16.

**Already covered, so not new:** daily-trend agreement (H-31), round numbers and prior extremes (H-05), sweep fades
(H-08, inside H-14), limit entry at a broken level (H-25), auction and 08:30 ET windows (D-5, H-14), structure and
fair-value-gap rules (H-TVR1/2; R-09), mfi and box rules (A7 "not trialled, by decision").

**Known to fail on the evidence; do not propose:** standalone candlestick rules, Fibonacci ratios as special levels,
floor pivots as a standalone signal, short-horizon oscillator fades (R-01), 15m/1h breakouts (R-04, B03), tight
structural stops at £1,000 (tight-stop study).

## Results fed back (2026-10-18)

| Result | Source | Effect on the base |
|---|---|---|
| EMA20 (1h) touched on 92.8% of trading days (87.5%-95.4% by year; 4,046 days) | `../pa-claims-2026-10-18.md` §1 | Near-certain by construction: "price returns to the EMA20" carries no information; R6 stays grade D, no trial |
| Reversal attempts that fail: 30.7% (1h) and 33.0% (4h) against the trend vs shuffled-bar null 30.3% / 30.2% (null 5-95% 28.4-32.3 / 26.7-33.7) | same §2 | Course "most reversals fail" (S3) is a null-level frequency: **none found**; CHoCH continuation is not a standalone edge |
| EMA10 to EMA50 gap > 1-3 ATR touches EMA50 at the matched every-bar rate (e.g. 48.4% vs 47.8% at N=16, gap > 1 ATR) | same §3 | Mean reversion after a wide MA gap = null; retired as a rule (R-01, R-09 cover it) |
| Gap fill = null (director summary of the claims check) | `../pa-claims-2026-10-18.md` | Gap-fill folklore retired; no FVG rule (R-09) |
| H-40 FAIL: no flag group survived fold-internal selection; pooled OOF AUC 0.503; K now 90 | `../hypotheses.md` A11 | All box/price-action flags retired as filters on the 2010..2025-09 candidates: S-GATE, R-GATE, D-MFI, volume veto/break-with-volume (M-rows), engulfing/pin flags (C-rows), premium/discount (S9), fib-50. KB-3's volume-veto half is read as "no signal on boxes"; the activity_tod group stays only as an O-4 feature |
| KB-1, KB-2 (shallow arm), KB-5 pre-registered with counts | `../prereg-kb-2026-10-18.md`, `../hypotheses.md` A12 | H-48, H-49, D-KB1: counts 3,349 / 2,079 / 2,327; none dispatched (budget override owner-only) |

## Change log

| Date | Change |
|---|---|
| 2026-10-18 | Created: seven topic files from the owner's nine local note sets, about 30 external sources, data status from the registry and dated studies; KB-1..KB-7 proposed (0 trials) |
| 2026-10-18 (cycle 2) | Results fed back from pa-claims and H-40 FAIL; KB-1/KB-2/KB-5 moved to `hypotheses.md` A12 as D-KB1/H-48/H-49 with counts; KB-3/4/6/7 stay features/exits. No grade changed |
| 2026-10-26 | Time-structure programme: `gold-specific.md` section 7 added (9 claims from slices A-H, discovery half, randomised controls); Fibonacci and session-open direction graded A against on our data; no external grade changed |
