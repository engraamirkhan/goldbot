# Slice H: combinations (confluence, portfolio, regime switching, meta-labelling) on XAUUSD 15m / 1h / 4h / 1d

Staff research lead for the research director's time-structure programme (frame: `README.md` in this folder,
sections 4-5). Owner (2026-10-26): "combination of strategies etc" (earlier: "if required multimodels could be used").
Descriptive measurement only: **no trial registered, no backtest job, no holdout or confirmation-half bar loaded**.
Script: `scripts/research/combinations_diag.py` (`uv run python -I scripts/research/combinations_diag.py --raw
<dir of xauusd_1m_dukascopy_YYYY.parquet> --out <dir>`; about 10 s; reads only the 2010-2017 files; seed fixed).
Slice inputs read from their branches: sessions (A), cycles (B), fib-wavelets-cycles (C), oscillators-trend-volume (D),
smc-orderblocks (E, redo: no SMC slot), volatility-bands (F), README 6.11 (the Hurst split is not a volatility or
trend-length proxy). Slice G (key levels) had not landed; its candidates plug in at one place (section 7).

## 0. Integrity statement and cost basis (read first)

- **Data and halves.** 2010-01-01 .. 2017-12-31 only (96 months). Bars as in slice F: UTC-aligned 15m/1h/4h, a
  trading-day 1d bar (NY date after +7 h), `available_utc` = last minute + 1 min, signal on bar i enters at the open of
  i+1, higher-timeframe context through `goldbot.data.store.asof_join` on `available_utc`. The F-1 base reproduces slice F
  exactly (10,425 events, +0.087 ATR15, day-clustered t 5.2; F-2 832 events), which is the check that my construction is theirs.
- **Unit and exit.** R here = 1 ATR(14) of the decision bar at a **fixed time exit** (4 bars for the 15m streams), no stop
  model, so there is **no stop overshoot** in these numbers. Slice F did not build an MAE table for a 1.0 ATR15 stop
  either; a stop would add the audit's $0.70 mean overshoot per stopped trade on top of everything below.
- **Two cost cases, both labelled.** *Research prior*: $0.75/oz round trip in 2010-17 dollars (cycles.md 2) divided by
  the bar's ATR (about 0.5 R on a 15m trade at the 2010-17 ATR15 of about $1.4). The running cost audit now puts the research cost
  at about **$1.07/oz** (vs $0.14-0.23 measured at quiet times), so every `net prior` number below is **optimistic by
  about 0.2-0.3 R per 15m trade**; it does not change any sign (all are already negative). *Measured-fill*: broker
  $0.144/oz (spread ~0.10 + commission) converted at today's median ATR per timeframe (15m $8.0 = 0.018 R, 1h 0.008, 4h 0.004, 1d 0.002),
  as slice F. That case assumes the 2010-17 ATR-unit edge exists at today's volatility, which only the confirmation half can show.
- **Looks counted (K).** 64 statistics on gold: 41 confluence cells (6 filters, all 1/2/3-subsets with n >= 30; 6+15+20),
  15 pair interactions, 2 pre-specified stacks, 3 books, 1 allocator gate, 1 walk-forward switch, 1 meta-label model. Plus the
  inherited F-1 base, which slice F charged at +15 effective looks and which is itself post hoc. The six filters
  collapse to about 4 independent axes (htf4h and htf1d correlate 0.44, ny_pit and vol_hi 0.49, room and htf4h -0.36,
  room and osc -0.42), so **charge slice H about +6 effective looks** (4 axes + 2 stacks) on top of F-1's +15; +1 if only
  one combined strategy is read.
- **Selection honesty.** The six filters were written from mechanism before the first run (section 2), but I knew F-1
  and the sessions finding (NY pit carries 45% of day variance) when I chose them, so `ny_pit` is not a blind choice.
  The ten (now twelve) portfolio streams mix pre-specified families (E1, V1, O1, TS: the exploration-arm shapes, specified in
  `arms-2026-10-25.md` before any look) and slice candidates that were **surfaced by their own discovery looks** (F1, F2, F3,
  A1, A2, B1, D1, C1). Books are reported for both groups so the selection effect is visible.
- **Streams that did not reproduce their slice (flag).** A1 (failed Asia break fade) gives **-0.224 ATR15, t -2.0 on 388 events**,
  i.e. the fade *loses* in my build, where slice A reports a fade profit (-2.78 bp in its sign convention); D1 (Asia trend
  persistence, 1h) gives gross -0.002 ATR1h (t -0.06) against slice D's +0.64 $ (t 2.7); C1 is a **daily-bar proxy** (66 events, -0.057 ATR1d), not the 1m
  first-touch rule of slice C (+0.20 R, block t 3.7). I did not debug these (outside scope); the director should treat A-1 and D-1 as
  unconfirmed by an independent build until the owners reconcile entry and unit conventions. They are included in the portfolio so the effect of
  weak members on a book is visible, not because they are endorsed.

## 1. Already tried (do not redo)

| Item | Where | Reading |
|---|---|---|
| Regime allocator, rule table v1 | `goldbot/allocator/rules.py` | trend weight when ADX(1h) > 25, mean reversion when < 18, breakout when 1h ATR in its bottom quartile, session-open 0.75, zero within 30 min of a tier-1 event; agent weight = family weight x fitness share. Coded and live; **never validated against a control** |
| Meta-labelling on a null primary | R-07 (#20, #53), H-40 | OOF AUC about 0.50; the H-62 spec says a meta-model filters an edge and cannot create one |
| H-62 meta-labeller (exploration ranker, 40 columns/style) | `h62-meta-labeller-spec-2026-10-25.md` | frozen, not yet fitted; its main claimed value is cost state, which mid-price research bars cannot test |
| Filter stacks as a rule family | H-40 (S-GATE session, R-GATE regime) | retired as filters on a null base |
| Multi-timeframe confluence as hypotheses | H-34/H-35 | withdrawn on counts (0-18 candidates): stacking filters kills n; section 2 re-measures this on a base that has events |

What is new here: the conditional lift of each pair/triple against **random same-size subsets of the same base** (so a
filter cannot look good only by thinning), an interaction contrast with a day-block bootstrap CI, the daily return correlation of twelve streams with a
sign-randomised null, and a control for the allocator (shuffled weights).

## 2. Confluence: does stacking add information?

Base trade set (one strategy, fixed before the run): F-1 = union of Keltner(20, 2 ATR), Bollinger(20, 2), Donchian-20
breakouts on 15m; trade **with** the break when Hurst128 is in its low tercile, **against** it when high; skip the middle.
n 10,425 (109 a month), gross **+0.087 ATR15**, t(day) 5.2, 8/8 years. A sign-randomised version of the same trades
has mean -0.001 (sd 0.017).

Filters, each from a mechanism (written before running; flags are about the trade's direction d; `filters()` in the script):

| Flag | Definition | Mechanism | Share of base |
|---|---|---|---|
| `htf4h` | sign of the last closed 4h Keltner position = d | Elder triple screen; slice F's "stretch resumes" | 52% |
| `htf1d` | the same on 1d | owner rule: intraday agrees with the daily chart | 49% |
| `ny_pit` | signal bar in New York 08:00-13:30 local | NY pit window carries 45% of day variance (README 6.1); DST-correct | 28% |
| `vol_hi` | ATR15 trailing 250-bar rank >= median | range forecast proxy (slice B: volatility is the one predictable thing) | 50% |
| `osc` | RSI14 on the trade's side of 50 | oscillator agrees (slice D: only as feature) | 48% |
| `room` | at least 1 ATR15 to the prior-day extreme in the trade direction | key-level room (slice G proxy, until its levels land) | 78% |

### 2.1 Singles (z = event mean against 1,000 random same-size subsets of the base; net per trade in ATR15)

| Filter | n (per month) | gross | t(day) | z vs random subset | yrs+ | net prior | net measured | gross R per month |
|---|---|---|---|---|---|---|---|---|
| none (F-1 base) | 10,425 (109) | +0.087 | 5.2 | | 8/8 | -0.441 | +0.069 | 9.5 |
| `ny_pit` | 2,884 (30) | **+0.180** | 4.6 | **+3.5 (BH q 0.10)** | 8/8 | -0.219 | **+0.162** | 5.4 |
| `htf4h` | 5,413 (56) | +0.113 | 4.8 | +1.6 | 8/8 | -0.417 | +0.095 | 6.3 |
| `htf1d` | 5,158 (54) | +0.112 | 4.8 | +1.4 | 7/8 | -0.417 | +0.094 | 6.0 |
| `vol_hi` | 5,188 (54) | +0.112 | 4.7 | +1.5 | 8/8 | -0.294 | +0.094 | 6.1 |
| `room` | 8,128 (85) | +0.084 | 4.4 | -0.4 | 8/8 | -0.457 | +0.066 | 7.1 |
| `osc` | 4,963 (52) | +0.066 | 2.6 | -1.1 | 7/8 | -0.480 | +0.048 | 3.4 |

### 2.2 Pairs and triples that matter (all 41 cells are in the script output; only BH survivors and the decision rows are shown)

| Combination | n (per month) | gross | z vs subset | net prior | net measured | yrs+ |
|---|---|---|---|---|---|---|
| `htf1d + ny_pit` | 1,381 (14) | +0.219 | +2.9 (BH) | -0.177 | +0.201 | 6/8 |
| `ny_pit + room` | 2,134 (22) | +0.190 | +3.2 (BH) | -0.211 | +0.172 | 7/8 |
| `htf4h + htf1d + ny_pit` | 980 (10) | +0.224 | +2.7 (BH) | -0.176 | +0.206 | 7/8 |
| `ny_pit + vol_hi` | 2,579 (27) | +0.156 | +2.3 | -0.221 | +0.138 | 7/8 |
| `htf4h + osc + room` | 1,219 (13) | +0.022 | -1.3 | -0.589 | +0.004 | 3/8 |

Pre-specified stacks, each read as one strategy: **A** (switch + `htf4h` + `vol_hi`): 2,729, +0.109, t 3.3, 7/8; **B**
(switch + `ny_pit` + `vol_hi`): 2,579, +0.156, t 4.0, 7/8. Neither beats `ny_pit` alone.

### 2.3 Interaction: does a pair carry more than the sum of its parts?

Interaction = mean(A&B) - mean(A) - mean(B) + mean(all), day-block bootstrap 95% CI (300 draws). **No pair has a positive
interaction whose CI excludes zero.** Two are significantly *negative* (redundancy): `ny_pit x vol_hi` -0.049 [-0.090, -0.011] (the
NY pit is the high-volatility window, filter correlation 0.49: the two flags select the same bars) and `htf4h x osc` -0.030
[-0.062, -0.000]. `htf1d x ny_pit` is +0.014 [-0.051, +0.080] and `ny_pit x room` +0.013 [-0.019, +0.045].

### 2.4 Reading

1. **One filter adds information beyond thinning: the NY pit window** (gross 0.087 -> 0.180, z +3.5 against random subsets of the same
   size, 8/8 years). `htf4h`, `htf1d` and `vol_hi` lift mildly (+0.11, z 1.4-1.6) and fail the BH cut individually; `room` and `osc` add nothing or hurt.
2. **Stacking is additive-at-best, not synergistic.** Every higher-order cell that beats the base contains `ny_pit`; none beats `ny_pit`
   by more than noise (best triple +0.224 vs +0.180, n 980, same z). Adding a filter after `ny_pit` costs 30-60% of the trades for no
   measurable per-trade gain: the extra conditions are mostly the same information (volatility and clock).
3. **The trade-count cost is real and it eats the total.** `ny_pit` cuts 109 trades a month to 30 and gross R a month from 9.5 to 5.4. At the measured-fill cost net R a month falls too
   (about 7.5 -> 4.9); filtering raises the per-trade edge, which matters only where cost per trade is large (the research prior, where all cells are still negative: best -0.18 ATR15).
4. **Mechanism check.** README 6.11 says the Hurst split is not a volatility proxy, so `ny_pit` is a second, partly overlapping selector
   (activity), not a repair of the first. Whether the lift survives with `vol_hi` partialled out is the NY x vol_hi interaction above:
   negative, so some of it is the same activity.
5. **Verdict for confluence.** The combination worth carrying is **F-1 inside the NY pit window** (one strategy, 2,884 events in 8 years, 30 a month, +0.18 ATR15
   gross). It is 28% of F-1's events, still above the 1,000 intraday floor in 8 years. It is a post-hoc cut on a post-hoc rule; I carry it as a
   split of the same F-1 read (zero extra trials), not as a new hypothesis. Prior that it survives confirmation at the measured cost: about 8%; at the research cost: about 0%.

## 3. Portfolio: weak, low-correlated strategies side by side

Streams (decision timeframe, fixed exits, 2010-17; all daily R series zero-filled on days with no trade):

| Stream | Family (allocator) | TF | Source |
|---|---|---|---|
| F1 | breakout | 15m | slice F-1 (the base above) |
| F2 | breakout | 15m | F-2: break into a stretched 4h, trade with the stretch |
| F3 | trend | 4h | Donchian-55 breakout, hold 4 bars |
| O1 | session_open | 15m | London 30-minute opening range break (arm S2 shape), hold 16 bars |
| A2 | session_open | 1h | NY pit continuation to the COMEX settle (slice A, A-2) |
| E1 | trend | 15m | EMA20/50 pullback with reclaim (arm S5 shape), hold 4 bars |
| V1 | mean_reversion | 15m | session-VWAP 2 sigma fade (arm S1 shape), hold 4 bars |
| TS | trend | 1d | slow tsmom (60-day sign, every 5th day, hold 5; H-01/W1B shape) |
| A1 | mean_reversion | 15m | failed Asia-range break fade (slice A, A-1) *not reproduced* |
| B1 | calendar | 1d | Friday long (slice B, B-1) |
| D1 | trend | 1h | Asia trend persistence (slice D, D-1) *not reproduced* |
| C1 | trend | 1d | 30-day-low short, daily-bar proxy *not the source rule* |

### 3.1 Per stream (gross and net R per trade; annualised Sharpe of the daily series)

| Stream | n (per month) | gross | t(day) | net prior | net measured | Sharpe gross | Sharpe measured |
|---|---|---|---|---|---|---|---|
| F1 | 10,425 (109) | +0.087 | 5.2 | -0.441 | +0.069 | 1.83 | 1.45 |
| B1 | 408 (4.3) | +0.099 | 2.8 | +0.058 | +0.098 | 0.98 | 0.97 |
| F2 | 832 (8.7) | +0.146 | 2.8 | -0.335 | +0.128 | 0.97 | 0.85 |
| F3 | 512 (5.3) | +0.142 | 2.2 | +0.025 | +0.138 | 0.77 | 0.75 |
| O1 | 1,797 (19) | +0.134 | 1.6 | -0.445 | +0.116 | 0.56 | 0.49 |
| E1 | 14,153 (147) | +0.045 | 3.2 | -0.503 | +0.027 | 1.12 | 0.67 |
| TS | 401 (4.2) | +0.056 | 0.7 | +0.015 | +0.055 | 0.25 | 0.24 |
| A2 | 2,035 (21) | -0.017 | -0.3 | -0.254 | -0.026 | -0.12 | -0.18 |
| D1 | 1,520 (16) | -0.002 | -0.1 | -0.237 | -0.011 | -0.02 | -0.09 |
| C1 (proxy) | 66 (0.7) | -0.057 | -0.3 | -0.099 | -0.058 | -0.11 | -0.11 |
| V1 | 8,186 (85) | -0.019 | -1.0 | -0.555 | -0.037 | -0.36 | -0.70 |
| A1 | 388 (4.0) | -0.224 | -2.0 | -0.796 | -0.242 | -0.70 | -0.76 |

Only F3, B1 and TS net positive at the research prior (daily and 4h streams: cost per R is tiny). Every 15m stream is negative at the
research prior; F1, F2, F3, B1, O1 and E1 are positive at the measured-fill cost.

### 3.2 Correlation

Daily gross R, 12 streams, 66 pairs: **mean pairwise correlation -0.000, mean |rho| 0.042, maximum 0.19 (F2-F3)**; E1-V1 -0.18 (pullback versus VWAP fade),
V1-A2 -0.12, O1-A1 -0.10. On days both are active the numbers are similar except F2-F3 0.42 (same 4h-stretch information by design), F2-TS 0.20, O1-A1 -0.25.
By regime (daily vol tercile, daily ADX bucket) the mean pairwise rho stays within +-0.006 in every cell; the average stream Sharpe
is highest in low-volatility days (0.70 gross vs 0.06 in high-volatility, 502 days) and flat across ADX buckets (0.50-0.60). By entry session (streams with >= 40 trade days): Asia +0.017, London -0.016, NY -0.003, Late +0.011;
the Late window has the weakest average Sharpe (-0.07). Low correlation is partly mechanical: eight streams trade fewer than 22 times a month, so most days are zeros.

### 3.3 Books (equal-risk = 1/sd weights; inverse variance = 1/var; 2010-17 daily Sharpe)

| Book | N | avg single gross / measured | best single gross / measured | book gross eq-risk | book measured eq-risk | measured inv-var | book net prior |
|---|---|---|---|---|---|---|---|
| all 12 | 12 | 0.43 / 0.30 | 1.83 / 1.45 | 1.49 | 1.03 | 0.81 | -11.2 |
| selected (F1, F2, F3, A1, A2, B1, D1, C1) | 8 | 0.45 / 0.36 | 1.83 / 1.45 | 1.22 | 0.98 | 0.75 | -5.6 |
| **pre-specified (E1, V1, O1, TS)** | 4 | 0.39 / 0.18 | 1.12 / 0.67 | **0.82** | **0.37** | 0.29 | -11.7 |

Sign-randomised null (trade directions drawn at random keeping each stream's long share, 200 runs, equal-risk): all 12 mean 0.30, sd 0.32, p95
0.79; pre-specified 4 mean 0.03, sd 0.30, p95 0.52. (The all-12 null is not centred at zero because gold rose and TS/B1 are long-biased.)

Reading:
1. **Zero correlation is real; the sqrt(N) gain is not available, because the members are not equal.** With rho = 0 the formula gives
   average Sharpe x sqrt(N): 0.43 x sqrt(12) = 1.49, which is what the book earns, but the **best single stream (F1, 1.83) beats the book**. The law holds for N similar strategies;
   here one stream carries it and half the rest have negative gross.
2. **The pre-specified book is the unbiased one.** Gross 0.82 (null p95 0.52, about +2.6 null sd), measured-fill 0.37, research-prior negative. That is a real if small
   diversification effect among known families (E1 and O1 carry it; V1 detracts). It is not an edge under the research cost.
3. **Inverse-variance is worse than equal-risk** (0.81 vs 1.03 measured), because it concentrates in the low-variance 1d streams (TS, B1) that earn little;
   equal-risk (1/sd) is the right baseline. Equal-risk for the allocator means sizing each stream so its daily R sd is equal; 1/N risk across streams that trade 4 or 150 times a month requires per-trade size scaling, which the arm frameworks do not do today.
4. **Cost per trade decides which members belong.** Streams that survive the research-prior cost are the slow ones (F3, B1, TS); the fast streams (F1, E1, O1) only survive at the measured fill. A book that mixes them
   inherits the cost era of its worst member.

## 4. What the allocator and specialists already do, and what is missing

`goldbot/allocator/rules.py` (RuleAllocator, `rule-table-v1`): per-family weights from ADX(14, 1h) and the 1h ATR quartile over 60 days, zero near tier-1 events, agent weight = family weight x fitness share. Missing:

| Gap | Evidence here |
|---|---|
| **No control for the gate.** The rule table has never been compared with random weights | Stylised test (streams mapped to families as in 3; day-start regime; same `RuleAllocator.weights`): equal-risk book measured Sharpe **1.03**, allocator-gated **0.47**, shuffled-date weights **0.94 +- 0.21**. The gate is 2.3 sd *worse* than random weights: it gives breakout only 0.19 average weight (bottom-ATR-quartile days) and removes F1 on the days it earns |
| Correlation-aware sizing | stream correlations are about zero, so sqrt(N) sizing is cheap *if* members are comparable; no covariance estimate exists in the allocator |
| Edge-weighted allocation | weights come from ADX/ATR, not from realised net R; the fitness share is within a family only |
| A cost state in the gate | all 15m streams flip sign between the two cost cases; weights ignore spread |
| Walk-forward expert selection | tried in 5.1 |

Caveat: my family map and day-start regime are stylised, not the live specialists; the finding is that the rule table is **unvalidated**, not that it is wrong.

## 5. Regime-gated switching (mixture of experts)

- **Hard switch inside one strategy works in the data we have**: F-1 itself is a regime switch (Hurst low: with the break; high: against it); slice F's three-geometry table, a permuted-bar null and README 6.11 (not a volatility or trend-length proxy) support that it is a real state variable, not an artefact. A second switch (`ny_pit`) adds lift (section 2).
- **Switching between families by trailing performance (walk-forward, 5.1 below):** weights from trailing-2-year measured-fill Sharpe (floor 0), tested 2012-2017: **1.47 vs equal-risk 0.93** on the same days. One look, no null run, and the weights end up loading mostly on F1 (the selected stream), so I read it as "recent winners kept winning in 2012-17", not as a validated switch; a shuffled-performance null is needed before any use.
- **Switching on volatility regime** (a hard gate on daily vol or ADX terciles): the correlation structure does not change by regime (section 3.2), only the level of Sharpe does (low-volatility days are best), which points to **sizing by regime** (risk down on high-volatility days) rather than **selecting streams by regime**.
- Verdict: use regime as a **feature in the stream's own rule** (F-1 does), not as an external family allocator, until the allocator beats shuffled weights.

### 5.1 Walk-forward expert selection detail
Weights = max(trailing 24-month Sharpe, 0), normalised, applied to 1/sd-scaled daily R, years 2012-2017 (2010-11 burn-in). Measured-fill cost; 12 streams.

## 6. Stacking and meta-labelling (H-62 context)

One ridge-logistic meta-model (12 pre-listed features, well under the 40-feature cap, none chosen from this run: Hurst128, ADX15, ATR15 rank, RSI side, Bollinger z side, 4-bar return side, 4h and 1d Keltner side, hour sin/cos, direction, with-break flag), label = F-1 trade gross > 0, trained on all earlier years and tested on the next year (2013-2017), top half by predicted probability traded.

| Test year | n | OOF AUC | mean all | mean top half |
|---|---|---|---|---|
| 2013 | 1,236 | 0.555 | +0.115 | +0.139 |
| 2014 | 1,253 | 0.569 | +0.104 | +0.175 |
| 2015 | 1,290 | 0.562 | +0.111 | +0.000 |
| 2016 | 1,349 | 0.561 | +0.111 | +0.141 |
| 2017 | 1,316 | 0.536 | +0.107 | +0.179 |

Mean OOF AUC **0.557** against a label-permutation null of 0.502 +- 0.010 (30 runs): a real ranking signal, but the **top-half lift is +0.017 ATR15 against a null of +0.002 +- 0.031**, about half a null sd. The largest standardised coefficients are the with-break flag (-0.39: the fade arm wins more often than the
continue arm) and the side-aligned Bollinger z (+0.30): the model mostly **re-learns the Hurst switch's arm base rates** and the stretch, rather than finding a new state. Meta-labelling here **filters an existing edge slightly (AUC) but does not add to it (lift)**, consistent with R-07 and the H-62 spec; it also cannot test H-62's main hypothesis (cost state), because these research bars are mid prices without spread. One look; one model; not a trial.

## 7. Plug-in design for slices C, D, G and others

- **Confluence:** a candidate with a boolean mask over the base trades is added to `filters()` in `combinations_diag.py` (name -> mask); the script
  then reports the single, all pairs/triples against random same-size subsets, interactions and the stack, and adds the cell count to the look total.
  Slice G's key-level flags (PDH/PDL room is already a stand-in), slice C's Hurst/wavelet regime and slice D's oscillator or CMF flag enter here.
- **Portfolio:** a candidate with a trade list (idx, direction, forward return in its decision-bar ATR, hold) is one function returning a frame from `mk(...)` and one entry in `STREAM_FAMILY`; the
  correlation matrix, books, nulls and allocator comparison recompute. Mark each stream "pre-specified" or "surfaced by its own discovery" so the unbiased book stays separate.
- **Rule for combined strategies:** one combined strategy is judged as one strategy at its own pre-registered rule; no member is picked afterwards. Max 40 features per model still binds: F-1 + `ny_pit` + `htf4h` adds 3 flags (`hurst_base_flag`, `brk_event_dir`, `htf4h_kc_pos` from slice F section 10).

## 8. Candidate hypotheses

### H-1  F-1 inside the New York pit window (confluence; a split of the F-1 read)
- Mechanism: a regime-conditional band breakout (compression/expansion, slice F) acts where order flow is thickest (NY data and COMEX pit, 45% of day variance, README 6.1). Evidence grade D (mechanism), C (discovery).
- Already tried?: F-1 is new in its form; the window split is new. Not R-02 (unconditional opening-range break).
- Timeframe and clock: 15m, signals with New York local 08:00-13:30 (DST per date). Entry/exit as F-1 (next open, time exit 4 bars, stop 1.0 ATR15 untested).
- Control: random same-size subsets of F-1 events (done, z +3.5) and F-1's own permuted-bar null.
- Discovery evidence: Section 2.1, n 2,884, +0.180 ATR15, t(day) 4.6, 8/8 years; looks 64 (about +6 effective on top of F-1's +15).
- Cost check: gross 0.18 ATR15 = $1.44/oz at ATR15 $8: 10x the measured $0.144, 1.3x the audit's $1.07/oz research cost (fails the trial test: gross must be several times cost); net prior -0.22 at the earlier $0.75 and worse at $1.07.
- Expected events: 2,884 in 8 years (about 360 a year, 30 a month); full-history count owed (counts only).
- Kill rule: (0-trial, only if the director funds F-1's confirmation read) the NY-window mean below +0.08 ATR15, or below F-1 overall, or under 5 of 8 positive years.
- Route: **arm-side split of F-1's exploration arm** (zero trials), plus the `ny_pit` flag as an H-62 column; no slot. Prior P(continue) about 8% at the measured cost, about 0% at the research cost.

### H-2  Equal-risk book of the pre-specified exploration families (portfolio)
- Mechanism: low-correlated streams (mean rho -0.03 among E1, V1, O1, TS) diversify a small edge; diversification cannot create a gross edge.
- Evidence: Section 3.3, gross Sharpe 0.82 (null p95 0.52), measured-fill 0.37, research-prior negative. Route: **portfolio read on arm data** (zero trials): run the same correlation table on the fills of S1/S2/S5/I1/W1 as the arms accumulate and report the pre-specified book; not a candidate to trade.
- Prior P(continue) about 5%.

### Dropped
- Stacking `osc`, `room`, `vol_hi` onto F-1: no information beyond `ny_pit` (section 2.3).
- A family allocator gate in its current rule-table form (section 4): 2.3 sd worse than shuffled weights in this stylised test.
- Walk-forward stream switching as a rule (5.1): one look, no null, loaded on the selected stream.
- Meta-labelling F-1: AUC 0.557 but lift within null (section 6); keep inside H-62 as ranker only.

## 9. Verdicts per topic

| Topic | Verdict |
|---|---|
| Confluence (pairs/triples) | **one useful filter: NY pit window** (H-1, arm split); everything else redundant or neutral; no synergistic pair |
| Trade-count cost of stacking | real: each added filter removes 30-60% of trades; total R a month falls even where per-trade R rises |
| Portfolio correlation | **about zero** (mean -0.000, |rho| 0.04, max 0.19); regime and session do not change it |
| Equal-risk book | beats the average member, **not the best member**; the pre-specified book is positive only at the measured cost |
| Inverse-variance book | worse than equal-risk; do not use |
| Rule-table allocator | **unvalidated**; stylised test says worse than shuffled weights; needs a control before more weight is placed on it |
| Regime-gated switching | regime as a feature inside a rule (F-1): yes; external family switching: not evidenced |
| Meta-labelling | AUC real, lift not; ranker only (H-62), cost-state test needs spread data |

## 10. Looks and what I would stop doing

- Looks: 64 on gold (41 confluence, 15 interaction, 2 stacks, 3 books, 1 allocator, 1 walk-forward, 1 meta), plus 1,000/300/200/30-run controls (not looks). K charge: **+6 effective** (or +1 if only H-1 is read), on top of F-1's +15. No confirmation-half or holdout bar was loaded; I wrote no 2018+ path.
- Stop: stacking more than one condition onto a base before the first condition's z is shown against random subsets; reading a book's Sharpe without the best-member and null comparisons; adding a regime allocator weight without a shuffled-weight control.
- Open items for the director: (a) reconcile A-1 and D-1 builds with slices A and D (my versions do not reproduce their signs); (b) a first-touch 1m build of C-1 to replace my daily proxy (66 events); (c) rerun with slice G's levels via `filters()`; (d) the same tables on real arm fills once S1/S2/S5/I1/W1 have fills (the only way to see stop overshoot and live correlation); (e) the allocator's shuffled-weight control on arm data.
