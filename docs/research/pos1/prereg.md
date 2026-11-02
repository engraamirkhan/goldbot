# POS-1 pre-registration: 16 position and portfolio specs (frozen before any run)

Research director (rethink lead), 2026-11-02.

**Rules.** Governed by W2, C1 and W3 (`docs/research/amendment-w3.md`, same commit). No POS-1 bar, result or engine
output exists at the time of this commit. The candidates come from `config/research/candidates/strategy.yaml`, PR
#282; that file was written before any outcome. The rules are restated below, so this file is self-contained.

**K: +16.** Each id is one trial. Components computed only inside a blend add nothing (W3.5).

**Who runs it.** The engine lane (`goldbot/research/position/`) and the data lane build the inputs. This lane runs
POS-1 **once**, after both have merged and after the eligibility table in section 3 has been re-stamped with the data
manifest. Workers: at most 2. Nothing at or after 2025-10-01 is read; the loader asserts it.

## 1. Common mechanics (W3.2, W3.3, W3.6)

**Clock and inputs.**
- 1d mid bars close at 21:00 UTC, and the fill is at the next 1m open.
- Month-end means the last trading day's 21:00 UTC close, and Friday means Friday's close.
- Lookbacks are in trading days: r21, r63, r126, r252 are the log returns over 21, 63, 126 and 252 days.
- Warm-up may read bars before the window. Each trade belongs to the window of its entry.

**Sizing and stops.**
- Exposure is vol-scaled to 10%/yr per instrument (EWMA 60-day span, lagged one day, cap 2x) unless the rule says
  "unit".
- Every spec carries the 3 x ATR(20, 1d) disaster stop. The no-trade band is the spec's own, else 10%.

**Costs.**
- Point costs per C1 (bps).
- Financing per W3.3 (DTB3 + m_long / - m_short, by date).

**Venue scenarios.**
- Scenario A (ICM CFD) **gates**.
- Scenario B (UK ETC) is reported for the long-only specs 008, 015, 056, 059 and 060.
- Scenario C (CME futures) is reported for all specs.

**Universes.**
- **U_metals:** XAUUSD, XAGUSD, XPTUSD.
- **U_16:** XAUUSD, XAGUSD, XPTUSD, WTI, US500, US100, GER40, JP225, UK100, EURUSD, USDJPY, GBPUSD, AUDUSD, USDCAD,
  USDCHF, NZDUSD, all from Dukascopy 1m (data lane).
- A market enters on its first day with data plus 260 days of warm-up.
- A book is flat on days with fewer than 2 live markets (U_metals) or fewer than 8 (U_16).
- If the data lane cannot supply a market, it is dropped and listed in the manifest **before** the run. Data
  availability is not an outcome.

**Macro inputs** (FRED, `asof_join` on `available_utc`).
- DFII10.
- USD broad: DTWEXB before 2006-01-02 and DTWEXBGS from then on. Only 20-day % changes are used, so the splice is
  scale-free.
- DTB3.
- US500 for spec 033: Dukascopy USA500 daily close if it covers 2003 onward, else the Yahoo ^GSPC daily close with
  `available_utc` = 21:15 UTC on the same day.

## 2. The 16 specs (exact rules)

| id | name | instr. | rule (decision, then exposure) | side |
|---|---|---|---|---|
| 001 | TSMOM-12M | XAU | month-end: s = sign(r252); hold to the next month-end | L/S |
| 002 | TSMOM 1/3/12 | XAU | daily: s = mean(sign r21, sign r63, sign r252); band 1/3 | L/S |
| 004 | Turtle 55/20 | XAU, unit | enter on a close beyond the 55-day high/low; exit on a close beyond the 20-day opposite extreme; stop 2 x ATR(20) (tighter than the disaster stop) | L/S |
| 006 | SMA 50/200 | XAU | s = sign(SMA50 - SMA200), always in | L/S |
| 008 | Faber 200d | XAU | s = 1 if close > SMA200, else 0 | long/flat |
| 015 | TSMOM-12M vs bills | XAU | month-end: s = 1 if r252 > the 12-month bill return (DTB3 compounded), else 0 | long/flat |
| 016 | six-signal blend | XAU | daily: s = mean(sign r21, sign r63, sign r252, sign(SMA50 - SMA200), -sign(20-day chg DFII10), -sign(20-day % chg USD broad)); band 1/6 | L/S |
| 025 | metals TSMOM book | U_metals | per metal, the 002 signal, equal risk at 10%/yr each | L/S |
| 026 | diversified TSMOM book | U_16 | per market, the 002 signal, equal risk; portfolio scaled to 10%/yr on a 60-day covariance; band 1/3 | L/S |
| 027 | diversified Turtle book | U_16, unit | per market, the 004 rule; 0.5% of equity risked per unit at 2 x ATR(20) | L/S |
| 033 | safe haven after an equity crash | XAU, unit | US500 daily return < its trailing 250-day 5th percentile: long at the next open, hold 10 trading days; stop 2.5 x ATR(20) | long |
| 055 | gold trend ensemble | XAU | daily: mean of the exposures of 001, 002, 004, 006 and 008 (each as above, unit-signed), then vol-scaled; band 10% | L/S |
| 056 | gold ensemble, long/flat | XAU | max(0, 055 exposure), capped at 1x | long/flat |
| 057 | U_16 book, inverse-vol | U_16 | the 026 signals, weights proportional to 1/vol(60d), portfolio scaled to 10%/yr on a 60-day covariance | L/S |
| 059 | dual momentum vs cash | XAU | identical in rule to 015; dropped, see note | long/flat |
| 060 | metals rotation | U_metals | month-end: hold the metal with the highest r126 if that beats the 6-month bill return, else flat; unit-sized to 10%/yr | long/flat |

**Note on 059.** RT-STRATEGY-059 (Antonacci dual momentum) is, as written in the candidate file, the same rule as
015. A duplicate trial would only inflate K, so **059 is dropped and replaced by RT-STRATEGY-045** (vol-managed
long gold).
- **Rule of 045:** long XAU with exposure = min(2, 0.10^2 / annualised 21-day realised variance), rebalanced at
  month-end, long only.
- This exposure is a stated time pattern, not a sizing choice (W3.6). The default vol scaling is not applied on top.
- The batch therefore stays at 16 ids, and K stays at +16.

## 3. Power gate (W3.1)

**Inputs.**
- Y_learn = 10, which assumes Dukascopy 2003-05 lands; 2003 is warm-up for the 252-day specs.
- Member SR priors:
  - peer-reviewed trend rule 0.4;
  - practitioner rule 0.3 (0.75x);
  - classes without a published Sharpe (macro direction, safe-haven event, vol-managed long): 0.2, i.e. half the
    trend default, stated here because no published figure exists.
- Correlations follow W3.1 defaults. For 016 the average pairwise rho is 0.42: trend-trend 0.6, the rest 0.3.

**Re-stamp.** If the data manifest gives a different Y_learn, this table is recomputed with the same formulas and
committed before the run.

| id | SR_prior (basis) | P_explore | P_chain | status |
|---|---|---|---|---|
| 001 | 0.40 (MOP 2012 / HOP 2017) | 0.23 | 0.022 | member |
| 002 | 0.467 (3 trend rules, rho 0.6) | 0.30 | 0.040 | member |
| 004 | 0.30 (practitioner) | 0.15 | 0.007 | member |
| 006 | 0.40 (Lemperiere 2014) | 0.23 | 0.022 | member |
| 008 | 0.40 (Faber 2007) | 0.23 | 0.022 | member |
| 015 | 0.40 | 0.23 | 0.022 | member |
| 016 | 0.464 (6 signals, mean member 0.333, rho 0.42) | 0.30 | 0.040 | member |
| 025 | 0.572 (3 metals x 0.467, rho 0.5) | 0.42 | 0.089 | member |
| **026** | **1.036** (16 markets x 0.467, rho 0.15) | **0.90** | **0.627** | **confirm-eligible** |
| **027** | **0.666** (16 x 0.30, rho 0.15) | **0.54** | **0.163** | **confirm-eligible** |
| 033 | 0.20 (no published Sharpe) | 0.09 | 0.002 | member, informational |
| 055 | 0.477 (5 rules, mean 0.393, rho 0.6) | 0.31 | 0.044 | member |
| 056 | 0.40 | 0.23 | 0.022 | member |
| **057** | **1.036** (as 026) | **0.90** | **0.627** | **confirm-eligible** |
| 045 | 0.20 (Harvey 2018: no Sharpe gain for commodities) | 0.09 | 0.002 | member, informational |
| 060 | 0.30 (practitioner) | 0.15 | 0.007 | member |

**What the gate says, in plain words.**
- No gold-only position rule can be validated on 2003-2025 data at literature-sized priors. Gold-only members run in
  explore and are reported, and they count in K.
- They reach confirm only inside an eligible book: gold's 002 signal is a component of 026 and 057.
- The C1 ensemble stage may form composites, but a composite also needs P_explore >= 0.50.
- The fix for gold-only lines is longer daily history (1975 onward), which is a data task. It is not a lower bar.

## 4. Pass rules

**Explore** (LEARN 2003-05 and 2010-17), per C1 with the W3.2 statistic:
- daily net mean > 0;
- block-bootstrap t >= 2.0;
- >= 60% of available years positive;
- drift-adjusted gross > 0;
- random-side (or exposure-matched) null excess >= 0;
- W3.4 counts: >= 30 round trips and >= 500 days in market;
- legs reported separately.

**Confirm** (2018-01-01..2025-09-30, once):
- m = the eligible explore passers plus any eligible C1 ensembles, fixed before the run;
- BH-FDR at q = 0.10;
- net > 0;
- >= 5 of 8 years positive;
- W3.4 counts;
- ex-2025 and drift-adjusted results reported.

**TEST-OLD** (2006-02..2009, once, confirm passers only): net > 0, t >= 1.28, W3.4 counts. A market without
TEST-OLD data is "old-test pending" and is not deployed.

**Qualified** means explore, confirm and TEST-OLD all passed. A qualifier goes to demo per W3.7.

**Kill, frozen now.**
- Every POS-1 id that fails is retired. A changed rule is a new id.
- If 026, 027 and 057 all fail confirm, the position line retires. The research director then recommends the
  decision memo's option A (pause new hypotheses) to the owner.

## 5. Outputs
`docs/research/pos1/results.md` plus a JSON of summary statistics per id. The JSON holds no trade rows and no
prices. For every id it reports, across all 3 venue scenarios:
- the explore table;
- per-year net;
- legs;
- null excess;
- counts.

Losers are reported beside winners. The data manifest (sha256), the re-stamped section 3 table, the engine commit and
this file's commit are cited in the results.
