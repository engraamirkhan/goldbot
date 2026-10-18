# Owner-style multi-timeframe confluence: owner-history mapping and pre-registration (2026-10-18)

Strategy researcher, 2026-10-18. Owner, the same day: "I personally manually have used these three indicators on
15 mins, 1hr and 4hr to make 50-100 pounds profit on a 50 pound account sometimes; this is not to restrict to any
indicator or strategy but to say what I did in the past; don't restrict to any number of useful information,
indicator etc."

The three indicators are the ones replicated in `goldbot/features/smc_replica.py` (structure overlay A, box
indicator B, money-flow oscillator C; match report and the withdrawn TVR rows in `prereg-tv-replica-2026-10-18.md`).
The replica module is on the integration branch `claude/gifted-goldberg-lcb7pl`, not yet on `main`. This file
does four things:

1. checks whether the owner trade-history analyser can read his past trades, and specifies how each trade is mapped
   to the replica state on 15m/1h/4h at entry (section 1);
2. states what a £50 → +£50-100 day means in leverage and what it means under our rails (section 2);
3. designs one multi-timeframe confluence family, `smc_mtf`, and pre-registers two trials with an event-count
   ladder (sections 3-5);
4. lists the zero-trial routes, the ranking, the owner decisions and the ledger (sections 6-9).

**Outcome-blind attestation.** No return, R, hit rate, label or outcome of any configuration here has been computed
or read. Sections 1-5 and 7-9 were written and committed **before** any event count of these rules was computed
(the commit that adds this file holds no counts). Section 10 adds counts only, in a later commit. No holdout bar
(2025-10-01..2026-09-30) is read by the counts.

## 0. Relation to the TVR withdrawal (read first)

TVR-1 (15m internal CHoCH + 15m swing trend + 15m FVG) counted 441 events and TVR-2 (15m swing BOS) 1,196. Both were
withdrawn before dispatch at 0 trials (`prereg-tv-replica-2026-10-18.md` B4b). The program director and research
director then ruled: "No reworded TVR-1 or TVR-2 is written from these counts (that would be a configuration chosen
after seeing them). The SMC topic continues only through the replica feature group (D-TVR)."

This file proposes rules again, so it has to answer that ruling. It does not ask for an exception silently:

- **What is new.** The owner's statement of 2026-10-18 is new information about *how* he used the indicators:
  across 4h, 1h and 15m together. TVR-1 read context from the 15m swing trend only. A multi-timeframe rule is a
  different object, motivated by the owner's description, not by a count.
- **What is not new, and is disclosed.** I knew the TVR counts when writing this, including the funnel (8,385
  internal CHoCHs, 3,616 with the 15m swing trend, 441 with a gap). The decision to put the gap filter in a ladder
  rung, rather than make it compulsory, is informed by that funnel. It is a count, not an outcome, but it is
  information, and the reader should know it.
- **How the risk is contained.** (a) Every free choice is fixed here, before any count of these rules. (b) The
  rung that runs is chosen by a ladder fixed here, by event count only (section 4.3). (c) Every replica parameter is
  the owner's chart setting (`ReplicaParams()` defaults); no indicator parameter is chosen by us. (d) Both trials
  are charged in full to K. (e) If both fail, the SMC rule topic closes for good, multi-timeframe included.
- **Ruling needed.** The research director (with the quant reviewer) decides whether section 0 meets the B4b
  ruling before anything is dispatched. Until then H-34 and H-35 are *pre-registered, not funded*.

## 1. The owner's own trades: can we ingest them, and how they map to the replica

### 1.1 The analyser exists and reads the export

`goldbot/research/owner_history.py` (added 2026-10-10/11; CLI `python -m goldbot.ops.run owner-history`, OWNER_GUIDE
section 4a, method note `owner-method-study.md`) reads an MT5 trade-history report in HTML (UTF-16 or UTF-8), XLSX
or CSV (deals or positions). It keeps XAUUSD positions (`XAUUSD*`, `GOLD`, `XAU/USD`), converts broker server time to
UTC (`--server-tz`, default Europe/Athens), takes 1R from the opening order's initial stop when the Orders section
has it, and drops every identifier. It refuses to read from or write into any git work tree, writes mode-0600 files
next to the export, and prints only an aggregates summary (`.share.md`). Its private per-trade output
`owner_history_<stamp>.trades.csv` has, per trade: side, lots, `open_utc`, `close_utc`, entry, exit, stop, target,
net, holding minutes, `r_multiple`, `risk_source`, session, hour, leverage and `risk_pct`.

So **yes, it can ingest his trades** today. What it cannot do yet is look at the chart at entry: it has no price
path (`owner-method-study.md` marks those fields †). The mapping in 1.3 is a new local script.

### 1.2 Steps for the owner (one step on his side, no secrets in chat)

The owner does only this, on the computer where his MT5 desktop terminal has the old account(s):

> In MT5 press `Ctrl+T` → **History** tab → right-click → **All History** → right-click → **Report** → **Open XML**
> (or HTML) → save into `~/Personal project/trade-history/`. Repeat for every account he traded with these
> indicators, including blown or reset ones. Then tell the program director "export saved". Nothing is pasted or
> uploaded.

Everything after that runs locally on the Mac and never enters the repo:

1. `python -m goldbot.ops.run owner-history --server-tz <broker zone> --start-balance 50` (the zone for his broker;
   Europe/Athens for IC Markets and most MT5 brokers on EET). Output: the private files and the `.share.md`.
2. `python -m goldbot.ops.run owner-replica-map` (new, section 1.3; implementer lane). It reads the newest
   `.trades.csv` in the same private folder and writes `owner_replica_map_<stamp>.csv` (private) and
   `owner_replica_map_<stamp>.share.md` (aggregates only).
3. The owner, or the program director on his machine, shares the two `.share.md` files with the research director.
   They hold percentages, counts, R and K only: no amounts, tickets, account numbers, names or per-trade rows.

If the old account's history is no longer in the terminal, the broker's client portal statement (HTML/CSV) with the
same columns also loads. If the old account was on a broker whose server time is not EET, the owner says only the
broker name; the zone is looked up by us.

### 1.3 Mapping each trade to the replica state at entry (`owner-replica-map`, specification)

For each owner trade with entry time `e` (UTC, from `open_utc`):

- **Bars.** Our Dukascopy 1m mid bars (release `data-v1`), resampled to 15m, 1h and 4h by
  `goldbot.data.resample.resample_bars` (UTC-floored), replica computed on each series from `SERIES_START`
  (2010-01-03 first bar) with the owner's settings (`ReplicaParams()`). For trades from 2025-01 (1h) / 2026-04 (15m) /
  2020 (4h) the pulled OANDA chart bars in `~/tmp/tv/` are what he actually saw; the script computes both and reports
  the event agreement rate between them (our bars vs his chart) as a data-quality line.
- **Point in time.** For each timeframe the state at `e` is the replica row of the **last bar whose close is at or
  before `e`** (`visible_at <= e`, via `goldbot.data.store.asof_join` with `available_col="visible_at"`). The bar he was looking at while it formed is
  not used: its final value was unknown at `e`. This is stricter than his eye, and is stated in the share file.
- **State recorded per timeframe** (15m, 1h, 4h; prefixed `m15_`, `h1_`, `h4_`): `trend_swing`, `trend_internal`,
  bars since the last internal and swing BOS/CHoCH and its side relative to the trade, a same-side FVG in the last 3
  bars, EQH/EQL in the last 10 bars, MFI value and bucket (≤ 20, 20-80, ≥ 80), bars since the last box break and its
  side, `range_f1_dist_atr` (premium/discount), `ma_stack_sma_ema`, `candle_class`.
- **Exit geometry.** His stop and target in ATR15 and ATR1h units; whether the stop sits beyond the latest 15m
  internal pivot; whether the target sits at the 1h 50-bar extreme, an opposite box break level or an EQH/EQL;
  his realised exit reason (target, stop, manual) from the Deals comments.
- **Base rates.** Each state's frequency among all 15m bars in the same UTC hours and years as his trades, so the
  share file shows **lift** (his frequency / base rate) with a binomial interval, not raw frequencies.
- **Outputs.** Private per-trade CSV (0600, outside every git tree, same guards as `owner_history`). Share file:
  per state, n, his frequency, base rate, lift and interval; the number of states examined (K_map); and the trade
  count by year. Nothing per trade.

**Inference rule (fixed now).** The share file is used to *describe what he did*, never which of his trades won.
The inferred setup is the conjunction of states whose lift interval excludes 1 at Bonferroni 5%/K_map. His win/loss
per state is **not** computed by the script (outcome-blind towards our future test), because selecting the states
that made him money and then backtesting them on overlapping years is in-sample selection twice over.

**Holdout risk (raised now).** If his trades fall in 2025-10-01..2026-09-30 (likely for a recent £50 account), the
mapping reads indicator state on holdout bars. That reads no outcome of any goldbot rule, but a rule inferred from
holdout-period behaviour and later scored on the holdout would be contaminated. Rule: trades dated inside the
holdout are mapped in a separate block; any rule inferred from them carries the flag `holdout_informed: true`, and
its eventual holdout scoring excludes the days on which he traded. The quant reviewer signs the script before it is
run on his file.

**What it gives.** An owner-derived pre-registration H-36 (section 6): his actual setup, written as a rule, tested
on 2010-2025-09 by the same bar as everything else, charged 1 trial plus K_map in K_eff. Until his export exists,
H-34/H-35 below are my reading of typical discretionary SMC use, not his.

## 2. What £50 → +£50-100 means, honestly

A day of +£50-100 on £50 is **+100% to +200% of equity**. That is a property of size, not of the indicators.

- **At 0.01 lot (1 oz).** £50-100 is about $67-133 per ounce (GBP per USD 0.75). That is 8-17 ATR15 at today's
  median ATR15 of $8.01, or 0.8-1.5 times a whole day's ATR(1d) of $87.63 (`atr-measured-2026-10-18.md`, last 90
  days). A 0.01 lot can make it only by holding a full day's move.
- **More likely, larger lots.** At 0.05 lot the same day needs $13-27 per ounce (1.7-3.3 ATR15); at 0.10 lot,
  $6.7-13 (0.8-1.7 ATR15). But a 1 × ATR15 stop ($8) then risks $40 (£30, **60% of equity**) at 0.05 lot and $80
  (£60, **120% of equity**, beyond the margin stop-out) at 0.10 lot.
- **Ruin.** At 30-60% risk per trade, two or three losses in a row end the account. With a 50% hit rate the chance
  of at least three losses in a row within 20 trades is about 0.79. "Sometimes" is the right word: the days that
  doubled the account are remembered, and the reset accounts are why the export must include every account
  (`owner-method-study.md` section 1).
- **Under our rails** (ADR 0004; `config/settings.yaml`): 0.5% risk per trade by default (£5), a 0.01 lot placed only
  while the stop is ≤ $8.00 (≤ $13.33 with the demo minimum-lot exception at 1%), and an effective daily loss limit
  of £20 at £1,000 (min of 2% and £50). A +£50 day at 0.01 lot with a 1 × ATR15 stop (£6 per R) is **about 8 R in a
  day**. A real edge of +0.10 R net per trade is about £0.60 per trade. The owner's percentages cannot transfer;
  only his trade stream in R can, which is why section 1 measures R and lift, not money. None of this loosens a
  limit, and no section here proposes one.

## 3. The family `smc_mtf` (design, fixed before any count)

### 3.1 Evidence and prior

| Claim | Source | Sample, instrument | Net of costs? | Grade |
|---|---|---|---|---|
| Classic technical rules show no outperformance after data-snooping control and costs in later samples | Bajgrowicz & Scaillet 2012, J. Financial Economics 106(3) | DJIA daily 1897-2011 | yes (costs and FDR) | robust (replicated by Park & Irwin 2007 review, J. Economic Surveys 21(4), mixed and snooping-prone) |
| Intraday technical rules do not add value after costs | Marshall, Cahan & Cahan 2008, J. Banking & Finance 32 | US equities intraday, 2002-2003 (*unverified period*) | yes | single study |
| Stop-loss and take-profit orders cluster at round numbers and recent extremes; price runs after stop clusters are hit | Osler 2003, J. Finance 58(5); Osler 2005, J. Int. Money and Finance 24(2) | FX order book of one bank, 1999-2000 | gross (mechanism, not a strategy) | robust as a mechanism in FX; no gold evidence |
| Time-series momentum at 1-12 month horizons, gold included | Moskowitz, Ooi & Pedersen 2012, J. Financial Economics 104(2); Hurst, Ooi & Pedersen 2017, J. Portfolio Management 44(1) | 58 futures 1965-2009; century-long futures 1880-2016 | net of estimated costs (Hurst) | robust, but monthly, not intraday |
| Short-horizon trend in gold: small gross edge, negative net | goldbot R-06 (#16, #17): gross +0.061 R (t 2.63), net −0.079 R | XAUUSD 1h/4h 2010-2025 | both reported | own evidence |
| BOS/CHoCH, FVG, order blocks, premium/discount | no peer-reviewed study found (`indicator-survey.md` §3; R-09) | — | — | folklore grade |
| Multi-timeframe alignment ("triple screen") | Elder 1993, *Trading for a Living* (book, practitioner) | none tested | no | practitioner, untested |

**Prior: low.** The one economic reason is the combination of (i) a weak higher-timeframe trend premium (R-06's gross
+0.06 R at 1h-4h) with (ii) stop-cluster flow when a short-term extreme breaks in the trend direction (Osler). Both
are small; the families nearest to this failed on gross edge (R-02, R-03, R-04). P(PASS) by the batch bar is about
1-2%. The value of the trials is that they answer the owner's question about his own method with a definite result,
and a FAIL closes the topic.

### 3.2 Shared definitions

- **Bars.** Dukascopy 1m mid (release `data-v1`) → 15m, 1h, 4h with `resample_bars` (UTC-floored 4h bars at
  00/04/08/12/16/20 UTC; the owner's TradingView 4h bars are anchored to the New York close, so 4h events can differ
  by bar alignment: disclosed, not corrected). Replica on each timeframe from `SERIES_START` with `ReplicaParams()`.
- **Point in time.** At 15m decision bar t (close time `t + 15m`), a 1h or 4h value is the replica row of the last
  1h/4h bar with `visible_at <= t + 15m`. 15m values use bars ≤ t. Nothing after bar t's close is read.
- **ATR15.** Wilder ATR(14) of the 15m bars, seeded (`smc_replica.atr_seeded`), read at t.
- **Pullback extreme.** `PL[t] = min(low[t-15..t])` (long) / `PH[t] = max(high[t-15..t])` (short): the last 4 hours.
- **Structure stop.** Long: `d = close[t] − PL[t] + 0.1 × ATR15`; short mirrored. `d = max(d, 0.75 × ATR15)`.
  **Skip** the candidate if `d > 1.5 × ATR15` (a stop of more than $12 at today's ATR does not fit 0.01 lot at
  £1,000 even with the demo exception).
- **Target.** `2 × d` (2R). **Room filter:** long only if `HH1h − close[t] ≥ 2d`, short only if
  `close[t] − LL1h ≥ 2d`, where HH1h / LL1h are the high / low of the last 50 closed 1h bars (the opposite liquidity
  pool and the replica's own `range_len`). This is the owner-like "target the other side of the range": the 2R
  target must sit inside the 1h range, not beyond it.
- **Time exit.** 32 bars (8 hours). One position at a time in total; a signal while in a trade is skipped. When stop
  and target fall in one bar, the stop counts first.
- **Barrier implementation.** `barrier_atr` returns `d` per candidate and the label spec is `stop_atr 1.0`,
  `target_atr 2.0`, `max_bars 32`, so the existing triple-barrier labels, shadow book and engine run it unchanged.
- **Costs.** No broker cost is measured yet (`costs-binding-2026-10-18.md`: `costs-v1` unpublished, blockers K1-K3).
  Labels charge the priors: Dukascopy bar spread + 0.37 $/oz + swap prior (−60 USD/lot/night long, 0 short). Stress:
  0.71 $/oz (batch rule 6). If `costs-v1` with a broker spread profile is published before the verdict, its
  per-hour figures are reported beside the result; the label cost model does not change (K5).
- **Lot fit.** `lot_fit=true`: the report shows what places at £1,000 / 0.01 lot under the default gate (stop ≤
  $8.00) and the demo exception (≤ $13.33). Candidates are not filtered by it beyond the 1.5 × ATR15 cap above.
- **Expected cost in R.** With `d` between 0.75 and 1.5 ATR15: 2010-2025 priors 0.14-0.39 R per trade (higher at the
  0.75 floor); today 0.71/8.01 ÷ (d/ATR15) = 0.06-0.12 R. Gross needed for net > 0 over the full sample: about
  +0.2-0.3 R. Gross needed for gate 3 at K ≈ 77: per-trade t ≈ 4.1.

## 4. Pre-registered trials

### 4.1 H-34 `omtf_c`: higher-timeframe trend, 15m pullback ends

Long at the close of 15m bar t when all conditions of the selected rung hold; short mirrored (s = −1, "discount"
becomes "premium", lows become highs).

| Code | Condition (long) |
|---|---|
| C1 | `h4.trend_swing = +1` |
| C2 | `h1.trend_swing = +1` |
| C3 | `m15.choch_bull_internal[t] = 1` (15m internal structure flips back up: the pullback ended) |
| C4 | `PL[t] ≤ h1 50-bar 0.5 level` (the pullback reached the 1h discount half; `hh − 0.5 × (hh − ll)` of the visible 1h bar) |
| C5 | `m15.fvg_bull = 1` on any of bars t-2..t (displacement on the flip) |

Plus the shared stop cap and room filter (3.2) at every rung.

**Mechanism.** Gold's 1h-4h trend has a small gross drift (R-06). Entering after a 15m counter-move into the 1h
discount half, on the bar that breaks the counter-move's internal high, buys the trend at a better price with a
stop just beyond the pullback, where the stop cluster of late shorts sits (Osler).

### 4.2 H-35 `omtf_r`: higher-timeframe trend, 1h liquidity sweep, 15m reclaim

| Code | Condition (long) |
|---|---|
| R1 | a 1h support break (`h1.sr_break_dn = 1`) on one of the last 4 visible 1h bars (stops below a box taken) |
| R2 | `h1.mfi ≤ 20` on one of those same 4 visible 1h bars (the owner's oversold zone) |
| R3 | `m15.choch_bull_internal[t] = 1` (the 15m flips back up after the sweep) |
| R4 | `h4.trend_swing = +1` (the sweep runs against the 4h trend: a pullback sweep, not a trend change) |

Short mirrored (`sr_break_up`, `mfi ≥ 80`, `choch_bear_internal`, 4h trend −1). Shared stop cap and room filter.

**Mechanism.** Stops resting below a visible support are triggered by the break; when the 4h trend is up and the
15m structure turns back up within four hours, the break was a liquidity grab (Osler 2005 price cascades), and price
returns to the range. Money flow below 20 marks the exhaustion the owner reads on C.

### 4.3 Event-count ladder (fixed now; selects by count only)

Gate 1 refuses fewer than 1,500 candidates (`research/gates.py` `MIN_CANDIDATES`), and P4 needs 1,000 events. Each
trial runs **exactly one rung**: the first rung, in the order below, whose candidate count after the stop cap and
room filter on 2010-01-01..2025-09-30 (both sides pooled, before one-at-a-time) is **≥ 1,500**.

| Trial | L0 (most owner-like) | L1 | L2 | L3 | If no rung reaches 1,500 |
|---|---|---|---|---|---|
| H-34 | C1 C2 C3 C4 C5 | C1 C2 C3 C4 (gap dropped) | C1 C2 C3 (discount dropped) | C1 C3 (1h dropped) | withdrawn, 0 trials |
| H-35 | R1 R2 R3 R4 | R1 R3 R4 (MFI dropped) | R1 R3 (4h dropped) | — | withdrawn, 0 trials |

- **Order rationale.** The rungs drop, in turn, the condition with the weakest claimed mechanism, keeping the
  4h trend and the 15m flip (H-34) or the sweep and the flip (H-35) to the end. The gap is dropped first in H-34 in
  part because the TVR funnel showed it to be the sparsest filter (section 0).
- **Overlap rule.** If more than 50% of H-35's selected-rung candidates are also H-34 selected-rung candidates
  (same bar and side), H-35 is withdrawn as redundant (0 trials). Below 50%, both run.
- **Sample start.** 2010 (the replica's pinned series start). If the ~2003 data extension is in `data-v1` and has
  passed data QA before dispatch, the series start moves to its first bar, `SERIES_START` is re-pinned with a test,
  the counts are recomputed and the ladder is re-applied, all before dispatch and recorded in section 10. MFI
  (R2) uses tick counts; a pre-2010 year with tick counts missing on more than 5% of bars is excluded from H-35 only.
- **Report.** Counts of every rung, long/short and per year, are recorded in section 10 before dispatch.

### 4.4 Run inputs (`research.yml`)

Blockers before dispatch: (1) the specialist `smc_mtf` (15m, `screening = True`, presets `omtf_c` and `omtf_r`
with a `rung` key) implemented from sections 3.2 and 4.1-4.3 with a test that its candidate bars equal the
definitions on synthetic bars, a prefix-invariance (lookahead) test across all three timeframes, and quant + code
review; (2) the replica merged to `main`; (3) the research director's ruling on section 0; (4) funding (section 8).

```bash
# H-34 and H-35: 2 trials, 15m, smc_mtf (rungs filled in from section 10 before dispatch)
gh workflow run research.yml --ref main -f specialist=smc_mtf -f from_year=2010 -f to_year=2025 \
  -f variants='[{"preset":"omtf_c","rung":<L>},{"preset":"omtf_r","rung":<L>}]' -f lot_fit=true \
  -f rule_ref='owner-style-mtf-2026-10-18.md#5-reading-rule-fixed-now' \
  -f rationale='Owner-style MTF: 4h/1h structure context, 15m internal CHoCH trigger, structure stop, 2R inside the 1h range'
```

`to_year=2025` with `research.holdout_from = 2025-10-01` cuts the holdout; `score_holdout` is never set.

### 4.5 P4 expectation

Gross mean R > 0 with t ≥ 2 on ≥ 1,000 events (≥ 1,500 by the ladder). My expectation: **gross about 0 to +0.05 R,
t below 2**, i.e. a FAIL at the screen, by analogy with R-02/R-03/R-04 and TVR's construction. The chance of a screen
pass is about 15%; of a full PASS about 1-2%.

## 5. Reading rule (fixed now)

Each trial is read alone, by the evaluator, word for word by `batch-2026-10-18.md` section 2, rules 1-6 (P4 screen;
rule-only net mean R > 0 with day-clustered t ≥ 2; DSR ≥ 0.95; ≥ 300 out-of-fold trades with ≥ 30 in
2024-01..2025-09; year stability including 2024 and 2025; cost stress at 0.71 $/oz). Changes:

- **K for rule 3** is the registry's `n_trials_effective` at the verdict date, **never below 77** (the highest floor
  already pre-registered, H-25's 75, plus these two). Gate-1 t ≈ 4.1.
- **Context lift (can only downgrade).** The evaluator also reports the gross mean R of the bare 15m trigger (C3 /
  R3 alone, same exits) on the same sample. If the selected rung does not beat it with Welch t ≥ 1, the verdict
  carries "HTF context adds nothing", and a PASS becomes a LEAD.
- **Side and rung.** Long and short reported separately; the rung that ran is named in the verdict.

Verdicts:
- **PASS:** all six rules and the context lift hold. Shadow founder. One neighbour is then pre-registered before any
  of its data is read: the owner-like structure exit (exit on an opposite 15m internal CHoCH, target at the 1h
  50-bar extreme, same stop), 1 trial.
- **LEAD:** rules 1, 2 and 5 hold, 3, 4, 6 or the lift fails. Shadow only, no orders; the same neighbour may be
  pre-registered (1 trial) only after 100 forward shadow candidates.
- **FAIL:** screen fails at ≥ 1,000 events, or rule-only net mean R ≤ 0. R-09 is reconfirmed with the trial number.
  **If H-34 and H-35 both FAIL, the SMC rule topic is closed**, multi-timeframe and owner-style included. Only an
  owner-derived rule from his own export (H-36) may still be tested, because it is new evidence about his method.
- **INCONCLUSIVE:** cannot happen (the ladder withdraws below 1,500).

## 6. Zero-trial routes

- **D-OMTF (proposed; K_eff +1 in O-4).** Add the 1h and 4h replica columns (`h1_`/`h4_` prefixed, PIT as in 3.2) as
  one group `ctx_smc` in O-4's 15m group map (H-14), next to D-TVR's 15m group, before O-4 runs. A model that can use
  the context screens every confluence at once inside one trial. Needs the research director's and the quant
  reviewer's sign-off (the O-4 map is not pinned yet).
- **H-36 (proposed, conditional; 1 trial + K_map).** The owner-derived setup from section 1.3, pre-registered only
  after his export is mapped, and only if at least 30 of his XAUUSD trades map to bars with a full replica state.
  Its rule is the conjunction of the significant-lift states; exits from his median stop and target in ATR units.
- **No other rule from this file** (box breaks, MFI crosses, EQH/EQL or candle codes as standalone rules): they
  stay in R-01/R-04/R-09 and in the feature groups.

## 7. Ranking by expected information per trial

| Rank | Item | Trials | Why |
|---|---|---|---|
| 1 | Owner export + `owner-replica-map` (section 1) | 0 | The only route to *his* rules; costs implementer time only. Tells us whether H-34/H-35 resemble what he did |
| 2 | D-OMTF in O-4 | 0 (K_eff +1) | Screens every 1h/4h replica confluence with the 15m trigger inside one trial |
| 3 | H-34 `omtf_c` | 1 | The textbook multi-timeframe use, with a weak but real HTF trend premium behind it |
| 4 | H-35 `omtf_r` | 1 | Has a microstructure mechanism (sweeps) but a reversal component (R-01 prior) |
| 5 | H-36 | 1 + K_map | Highest relevance, but only after rank 1 exists |

If funding is scarce: rank 1 and 2 first, and H-34 and H-35 wait for the owner map (it may show he traded neither).

## 8. Budget and owner decisions (flagged, not taken)

- **Budget.** 2026Q4's override is 53 and fully allocated (H-02 is #53); H-25 already asks for 54-56. These two trials
  need either the owner raising the 2026Q4 override by 2 (to 55, or 58 if H-25 is funded first) or two slots of
  2027Q1's free reserve (3). The research director has read the owner's directive of 2026-10-18 ("backtest as much as
  needed") as covering an override raise (B4a). **Raising the budget is an owner decision under my brief**: I flag it
  and recommend funding only after the owner export is mapped (rank 1), from the 2027Q1 reserve, so that H-34/H-35
  run only if his own trades look like them.
- **Owner action.** The one-step export in section 1.2. Nothing else is asked of him.
- **Not proposed.** No change to risk per trade, the daily limit or the minimum-lot rule. The owner's leverage is
  explained (section 2), not adopted.

## 9. Ledger

| Item | Count |
|---|---|
| New pre-registered trials | 2 (H-34, H-35), **not funded, not dispatched**; 1 conditional (H-36, proposed); 1 conditional neighbour per PASS/LEAD |
| K for DSR at a verdict | registry `n_trials_effective` at the verdict, never below 77 |
| Feature-discovery K_eff | D-OMTF +1 in O-4 if adopted; H-36 adds K_map when it runs |
| Holdout | untouched: counts end 2025-09-30; the owner map flags holdout-dated trades (1.3) |
| Rungs | count-selected, not outcome-selected; only one rung per trial is ever labelled, so no K_eff is added for the ladder |

## 10. Event counts (counts only, no outcome)

*To be filled in a later commit, after this design is committed.*
