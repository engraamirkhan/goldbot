# Pre-registration 2026-11-13: L3, the London session on history (H-91 London-to-New-York activity handoff)

Status: **registered before any outcome is computed** (principal regime and opportunity-day researcher,
2026-11-13, research-director plan line L3). No trial runs in this change. Nothing in the engine, RiskGate, caps,
sizing or `config/` changes. Family `session:2026-11`, unit H-91. **K +1 now** (explore read), up to +3 (section 7).

Data inspected before this freeze: none. No bar, return, range or forecast was loaded for this card. Everything
quoted below comes from documents already on `main`, and the source is named each time.

## 0. Scope, and what this line does not duplicate

- **H-90** (`prereg-2026-11-12-london-signal-arms.md`) owns the claim that our signal arms work better in London.
  It has a forward demo leg and its own history legs (H-learn, H-seen). This card does not read, run or re-count
  any of them.
- L3 asks two questions on history:
  - **(D)** Does the London session give gold a *direction* that pays our costs? This covers London-open drift,
    London-NY overlap and the Asia range break at London.
  - **(O)** Does the London session tell us *how much* the rest of the day will move? This is an opportunity
    question: whether to stand aside or trade the New York session.
- Section 1 shows that (D) is already closed on every window we could still read. **No (D) test is registered
  (K +0).** Section 2 registers (O) as H-91. It is the one session-structure question in this area that has never
  been computed: `goldbot/research/h16.py` says "not built: the London+60 / NY+60 refreshes with the noise ratio",
  and H-21 has stayed "proposed" since 2026-10-10.

## 1. Leg D: London direction on history. Declined (K +0), with the evidence

Every example in the L3 brief has been read on 2010-2017 at least once. Several have also been read on 2020-2026,
and that span includes the holdout (`time-structure/sessions.md` section 7). So 2010-2017 is not a clean learn
window for any directional London clock rule. The only fresh intraday window is TEST-OLD 2008-09, and it is
reserved for rules that have already passed confirm (memory W2).

| L3 example | Already read | Result | Status |
|---|---|---|---|
| London-open / London-AM drift | `sessions.md` s.2 (2010-17); s.7 peek (2020-26, holdout inside) | AM drift -2.46 bp/day, t -2.39, one of ~70 looks; **+0.41 bp/day on 2020-26 (sign flips)**. London-open persistence +0.66 bp, t 1.61 | dead; holdout seen for this statistic |
| London first hour predicts the London close | growth C3 V2 (`gs-c3-screen-2026-11.md`, 2010-17) | net -0.008 R, 3/8 years; gross +0.170 R (t 2.78) with 77 % stops and 77 % of the gain from the top decile | FAIL; the reading rule closes session-momentum timing, window and exit variants |
| London intraday momentum | R-05, trials #18/#19 | London -0.028 R (t -1.48) | retired |
| London opening-range breakout (24 variants) | `sessions.md` s.5; R-02 #1/#7/#11/#13 | net -0.12..+0.03 R, best t 0.88 | retired; "do not re-propose 15m ORB" |
| London-NY overlap / handoff | S200-TIME-10, -11 (`sprint200/time-results.md`); H-83 NY-overlap cells; R-17 (09:00 London..10:30 NY) | t 0.02 / 0.18; 0 of 24 cells; V0 gross -0.003 R | retired |
| NY open continuation and Gao pairs | `sessions.md` s.6 (NY 08:30->13:30 t 2.7, 1 of 6 pairs); S200-TIME-01..04 | all FAIL; TIME-03 net t -2.12 | retired |
| Asia range break at London | `sessions.md` s.3 (with-break +0.82 bp, t 1.16); S200-TIME-08; `session_range_break` (TREND-07/13/14, GS-C2 C2a/b; sprint3000 grade D) | nothing; no better than random starts | retired |
| Failed Asia-break fade | `sessions.md` s.3: -2.78 bp at 120 m, t -2.2, n 384; 2020-26 -0.90 (weaker) | one of six looks, not significant after looks; inside the H-08 / R-14 neighbourhood ("no neighbour tests") | closed |
| Large Asia move faded by London; Frankfurt open; London lunch fade; strong US day into London | S200-TIME-09, -30, -28, -13 | t -2.56 / -0.84 / -0.91 / -1.72 | retired |
| LBMA AM/PM fix | `sessions.md` s.2.1; S200-TIME-05/06; R-08 | no activity spike of its own; conditional fades FAIL | retired |
| Asia-up / West-down drift | H-04 #44 (net -0.093 R), H-24 lapsed, `sessions.md` s.2/s.7 | Asia drift is not distinguishable from the uptrend control on 2020-26 | stop |
| London session filter on catalogue entries | sprint3000 (`filter_sets`, `session=london`), learn and confirm read 2026-11-02 | CONFIRM-SEEN | closed |

**Why no re-scoring at the W2 bps costs.**

- W2 costs (0.575 bp round trip at $4,000) are about 3x cheaper in R than the dollar costs some of these reads used.
- Re-scoring retired rules at cheaper costs is the R-10 precedent: retired, and it is not new evidence.
- The arithmetic fails anyway. The largest gross t anywhere in this table is 2.78 (C3 V2). A daily clock rule gives
  about 2,000 events in 8 years. At a per-trade SD near 1 R, reaching t >= 3.6 would need a gross mean of about
  +0.08 R or more with no fat-tail help. No London directional read has come close.
- **Power of any new (D) card at the 3.6 bar is about 0.** Spending K on one would only raise the bar for everything
  else.

**Re-open condition.** A new *mechanism or data source*, not a new window, cut or exit. Examples: intraday LBMA
volume, or SGE/LBMA premium data that is point in time.

## 2. Leg O: H-91, the London-to-New-York activity handoff

### 2.1 Hypothesis and economic reason

**H-91:** the London morning's range relative to its own recent norm at the same clock time (LRR), known at 12:00
London, forecasts the New York session's range. It adds to the information available at the same moment: the
day-level HAR forecast, the NY session's own recent ranges, a tier-1 US release in the window, and the weekday.

**Mechanism.**

- Volatility clusters within the day as well as across days. The most recent information is the most predictive:
  HAR (Corsi 2009, *J. Financial Econometrics* 7(2), 174-196, equity and FX realised volatility, a volatility
  forecast, so cost does not apply; replicated widely).
- The London morning holds about 21 % of gold's day variance, and the NY pit window about 45 % (`sessions.md`
  s.2, 2010-17, our measurement).
- On days when information arrives before New York (macro, geopolitics, a USD move), London trades it first. New
  York then inherits the order flow and the repositioning.

**Evidence and its grade.**

| Source | Sample | Instrument | Cost | Grade |
|---|---|---|---|---|
| Early-interval realised volatility forecasts the last half-hour's (*Finance Research Letters* 58, 2023, https://ideas.repec.org/a/eee/finlet/v58y2023ipas154461232300394x.html) | as published | China gold futures | volatility only | single paper |
| Noise-area intraday detector (Zarattini, Aziz, Barbon 2024, SFI RP 24-97) | 2007-2024 | SPY | net, but for the directional rule | single paper, not gold |
| US releases drive gold's intraday volatility (Cai, Cheung, Wong 2001, *J. Futures Markets* 21(3)) | 1994-97 | COMEX gold | n/a | replicated in spirit by our s.2 activity table |
| Intraday periodicity (Andersen and Bollerslev 1997, *J. Empirical Finance* 4) | | | | *(unverified)* |
| A London-to-NY *range* handoff in spot gold | | | | **no study found** (`opportunity-days.md` 309-310). This test would be the first |

**Prior.**

- S1 (in-sample significance): moderately high. Range persistence is one of the most replicated facts in finance.
- S2/S5 (out-of-sample increment over a HAR baseline that already knows the session's own history): uncertain.
- H-16 cleared its rho gain (+0.20) but failed its quintile ratio (1.44 < 1.5). Ranking information is easier to
  find than separation large enough to act on.

### 2.2 Exact definitions (DST-aware, point in time)

**Clocks.**

- London is `goldbot.data.calendar.LOCAL_SESSIONS["london"]`, 08:00-16:30 Europe/London. New York is
  `LOCAL_SESSIONS["newyork"]`, 08:30-16:00 America/New_York.
- Local times convert to UTC per date through `zoneinfo`, as the frozen code does. Under GMT and EST, 12:00 London
  is 12:00 UTC and 08:30 New York is 13:30 UTC. Under BST and EDT they are 11:00 UTC and 12:30 UTC. In the gap weeks
  (UK on GMT, US on EDT) they are 12:00 UTC and 12:30 UTC.
- **The decision time T = 12:00 London precedes the target window by 30-90 minutes on every date.** This is
  asserted per day, and a day that fails it is skipped.
- The London window here starts at 08:00 London, not H-90's 07:00. That is the frozen feature's clock. The 07:00
  form is **not** read (that would be a second look).

**Bars.** Dukascopy 1m, mid = (bid+ask)/2, resampled to 15m as in `scripts/research/h16_screen.py`. Mon-Fri local
dates.

**Predictor.**

- `LRR_D` = `goldbot.features.day_type.noise_ratio` (VERSION "1", frozen) on the 15m bar that closes at 12:00
  London (the bar starting 11:45).
- That is the London range from 08:00 to 12:00 London, divided by its mean over the previous 14 London sessions at
  the same minute since the open.
- Regressor x_L = ln(LRR_D). Only bars closed by T are used.

**Target.**

- R_NY,D = high - low of the 15m mid bars inside [08:30, 16:00) New York on local date D.
- A_D = the mean of R_NY over the 22 previous eligible NY sessions, all strictly before D.
- y_D = ln(R_NY,D / A_D).

**Baseline regressors, all known at T:**

- b1 = ln(R_NY,D-1 / A_D);
- b5 = ln(mean R_NY over D-5..D-1 / A_D);
- h = ln(`opp_har_range_hat` for D), from `day_type.har_forecast` (frozen; emitted before D);
- e = 1 if a tier-1 US release is scheduled in [08:30, 16:00) NY on D. It comes from the calendar file by
  `asof_join` on `available_utc` at or before T. This makes the test harder for LRR, which is conservative;
- four weekday dummies.

**Full model:** the baseline plus x_L.

**Eligible day.**

- All of these exist: 14 prior London values at the 11:45 bar, 22 prior NY sessions, every regressor, and at least
  80 % of the 15m bars in both windows.
- US holidays are excluded (`day_type._US_HOLIDAYS`). NY early-close days fall out through the 80 % bar rule.
- **Refusal:** fewer than 1,500 eligible days for S1, or fewer than 1,200 out-of-sample days for S2/S5, refuses the
  screen before any statistic is read.

### 2.3 Statistics and the matched random control

- **S1, increment.** OLS of y on the full model over every eligible day 2010-2017. The coefficient on x_L must be
  **> 0 with Newey-West (5 lags) t >= 3.6**.
- **S2, out-of-sample.** Walk-forward OLS refits, monthly and expanding (`day_type.monthly_refits`, at least 500
  rows, 1-day purge). Score the base and full forecasts on each next month.
  - Required: rho(full, y) - rho(base, y) >= +0.03 (Spearman, `h16.spearman`).
  - Required: its 20-day block bootstrap 95 % lower bound > 0 (`h16.block_bootstrap_gain`, 10,000 resamples,
    seed 20261113).
- **S3, matched random control (placebo).**
  - 1,000 draws, seed 20261113. In each draw, every day's x_L is replaced by x_L from another eligible day in the
    same calendar month, drawn uniformly. This keeps the regime level and the seasonality.
  - S1 is refitted in each draw. The real t must exceed **all 1,000** placebo t (p < 0.001).
  - **Reported, not gated:** an Asia placebo, the same ratio built from the Tokyo 08:00 to London 08:00 range. It
    shows whether London's clock matters, or just "the latest few hours".
- **S4, stability.** S1's coefficient refitted in each calendar year separately is > 0 in **at least 7 of 8 years**.
- **S5, north-star relevance.** This judges the forecast by how it ranks days, against the base forecast.
  - "Best" NY sessions: the top quintile of |P(16:00 NY) - P(08:30 NY)| / A_D, the directional move a trend arm
    could capture.
  - "Poor" NY sessions: the bottom quintile.
  - Capture = the share of best sessions that fall in the forecast's top tercile.
  - Avoid = the share of poor sessions that fall in its bottom tercile.
  - Required: capture(full) - capture(base) >= +3 percentage points.
  - Avoid is reported, along with both models' rates by year.
- **Reported, not gated:** the coefficient and t without e and without h; the partial R^2; the London window
  started at the gap-week dates; quintile mean range ratios, so this can be compared with H-16's 1.5x bar.

### 2.4 Costs, in R and in pounds

- **Cost of record.** `cor-2026-10-31.2`, ICM, ADR 0064 charge max($0.23, ci90 upper) per round trip. At the
  $4,000 reference that is 0.575 bp (London and New York cells). Broker-measured: about **£0.12-0.13 per trade at
  0.01 lot**, spread about $0.09.
- **Scale.** The NY 4h range was $12.20 in 2024-10..2025-09 (`sessions.md` s.1.1, an activity table, not an
  outcome of this test). Cost is about 2 % of one NY 4h range, so it **never decides which session moves**.
- **What the cost does decide is the value of standing aside.**
  - A $4.50 fixed stop at 0.01 lot is 1 oz, so R is about £3.4. The cost is then about 0.037 R per trade.
  - Standing aside on a low-forecast session saves money only for a rule whose gross edge there is below about
    0.04 R.
  - So H-91 makes no money by itself. Its value is as a **sizing and stand-aside input for an arm that already has
    an edge.** Today no gold arm is qualified, and H-90 is the nearest candidate.
  - The read states this. A PASS is not a trading result.

### 2.5 Windows

| Window | Use | Status for this statistic |
|---|---|---|
| learn 2010-01..2017-12 | **explore (this card)** | fresh. The H-16 screen never built the London refresh; `sessions.md` read returns, activity shares and anchors, not this conditional |
| confirm 2018-01..2025-09 | once, only after an explore PASS, same rule; reported with and without 2025 | probably fresh. sprint3000 used ATR-percentile *filters* on directional entries, a different statistic, and `sessions.md` s.1.1 read activity only. **The quant-reviewer checks `state/sprint3000_reads.jsonl` before the confirm read and rules CONFIRM-SEEN or not** |
| test_old 2008-01..2009-12 (intraday DQ, memory W2 2026-11-10) | once, after a confirm PASS; S1 t >= 1.28 on at least 100 days, coefficient > 0 | fresh |
| holdout 2025-10-01.. | **sealed**. The loader asserts that no bar on or after 2018-01-01 is read in explore, and none on or after 2025-10-01 in confirm | |

### 2.6 Reading rule (decided now)

- **PASS** requires S1, S2, S3, S4 and S5 together.
  - It permits the confirm read only. It is not adoption.
  - After a confirm PASS and a test_old PASS, the next step is a proposal: a 12:00 London refresh of the opportunity
    score (`opp_london_ratio`), offered to the allocator as a stand-aside and size input for arms with their own
    qualified edge.
  - The route is PD + PO -> implementer -> quant + trading-safety.
  - Demo sizing changes are delegated (risk-rules memory). **Any real-money sizing use is an owner decision.**
- **FAIL** is anything else. H-21 is then retired as a London-to-NY predictor, and the London refresh is dropped
  from the opportunity-score design (`opportunity-days.md` s.3). No re-test with another window start (07:00,
  09:00), another cut time or another normaliser without a new mechanism.
- **REFUSED** (n below the floor or a NaN statistic): nothing is read. A fix of the cause re-runs under the same
  rule, and it is disclosed.
- Losers are reported with winners. The explore numbers never count as confirm evidence.

## 3. Trial cost and K

- **Registry budget: 0 slots.** This is an explore screen, as the H-16 screen was. The research director decides
  whether the confirm read takes a slot.
- **K.**
  - The demo-arm and registry line is **174 before this line**. H-90's +3 makes it 177 once its rows are written.
  - **H-91 adds +1 now** (explore), giving 175, or 178 with H-90.
  - It adds +1 more at confirm and +1 at test_old, and only if those reads are reached. **Leg D is +0** because it
    is declined.
  - The program-wide look ledger rises by the same 1 to 3.
  - The bar is the director's deflated t >= 3.6, about Bonferroni two-sided at K ~ 175-180.
- **Power.** One observation per day, about 1,900 eligible days. A partial correlation of 0.083 gives t about 3.6.
  Published intraday range persistence is several times that, so if the effect is real, the 3.6 bar is reachable,
  unlike leg D.
- **Compute:** minutes on the Mac. **Code:** section 4.

## 4. Runner status: new code needed, so this line stops here

No existing runner computes H-91. `h16.py` and `scripts/research/h16_screen.py` evaluate the day-level H-16 only,
and they say the London refresh is not built. The sprint/TIME screens trade directional rules. Reusable frozen
parts:

- `day_type.noise_ratio`, `har_forecast`, `monthly_refits` and the holiday list;
- `h16.spearman` and `block_bootstrap_gain`;
- `h16.cut_before_holdout` and the h16 screen's 2010-17 bar loading.

**To be written** (one implementer lane plus a quant review before any run):

1. A driver, `scripts/research/h91_london_handoff.py`:
   - load 2010-17 1m bars and assert the year range;
   - build 15m mid bars;
   - take LRR at the 11:45 London bar;
   - build the NY-session range, A_D, b1 and b5;
   - join the tier-1 event flag by `available_utc`;
   - fit OLS with Newey-West t;
   - run the walk-forward base and full models and S2;
   - run the S3 placebo with 3 worker processes;
   - compute S4 and S5, plus the reported rows;
   - write JSON to `~/tmp/h91/` (ratios only, no prices; not committed).
2. A self-test, `scripts/research/h91_selftest.py`. On synthetic bars it checks the DST cases (summer, winter and
   both gap weeks), that T falls before the window, that no bar after T enters x_L or the baseline, and that a
   planted LRR effect is found while a null is not.
3. Before the run, the commit freezes the code hash, and the result doc records it.

**Close date 2026-12-04.** If the code has not merged and run by then, L3 closes as "not run". The director then
decides whether to carry it into 2027Q1.

## 5. Sign-off owed

- **quant-reviewer:** this card, before code; the confirm-window status in section 2.5, before any confirm read.
- **evaluator:** applies section 2.6.
- **research director:** writes the K row (+1) before the explore run.
