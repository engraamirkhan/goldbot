# ML+probability team, lane PROBTREND: result (calibration gate FAILED, no P&L read, 0 survivors)

Principal Regime and Opportunity-Day Researcher, 2026-11-02. Pre-registration `probtrend-prereg.md` (commit `afa20940`,
before any look; amendment 1 `7a21fd91`, before any data was loaded). Script `scripts/mlprob/probtrend_screen.py`
and the vendored engine `scripts/mlprob/probtrend_engine.py` (verbatim `scripts/sprint200/trend_screen.py` at
`ccaf6b26`), committed in `283bdc76` before any run. Calibration output: `probtrend-calib-explore.json` (aggregates and
model coefficients only; no prices).

**Verdict: FAIL at the pre-registered calibration gate (section 3).** A point-in-time trend-day probability built
from prior-day structure, the volatility regime, the tier-1 US calendar and (at London open) the Asia session has no
skill over climatology on 2011-2017 walk-forward forecasts. As frozen, no P&L was read, no confirm was run, no arm
is proposed. K +12 (the 12 registered variants are counted although none was read).

## Calibration (explore 2011-2017, annual walk-forward, 1,806 labelled days per snapshot)

| snapshot | base rate | Brier | climatology Brier | BSS | AUC | calibration slope | trend rate, gated top 40% vs rest | gate (BSS > 0 and AUC >= 0.55) |
|---|---|---|---|---|---|---|---|---|
| A (trading-day open) | 0.338 | 0.2276 | 0.2243 | -0.015 | 0.514 | 0.22 | 0.357 vs 0.326 | FAIL |
| B (London open) | 0.289 | 0.2072 | 0.2056 | -0.008 | 0.559 | 0.36 | 0.324 vs 0.265 | FAIL (BSS) |

Reliability (10 equal-count bins, mean forecast -> observed trend rate):
- A: 0.22->0.33, 0.28->0.34, 0.30->0.36, 0.32->0.31, 0.33->0.32, 0.35->0.35, 0.37->0.32, 0.39->0.30, 0.44->0.32,
  0.53->0.43. Flat except the top decile.
- B: 0.13->0.24, 0.19->0.22, 0.23->0.23, 0.26->0.24, 0.28->0.33, 0.30->0.31, 0.32->0.33, 0.35->0.33, 0.39->0.29,
  0.49->0.37. A weak monotone tilt, over-dispersed forecasts (slope 0.36).

Per-year BSS (diagnostic after the gate verdict, not a rule input; same forecasts): A 2011 -0.038, 2012 -0.043, 2013
-0.040, 2014 +0.008, 2015 -0.003, 2016 +0.022, 2017 -0.008; B -0.002, -0.032, -0.024, +0.002, +0.009, -0.006, -0.001.
No year has material skill, so the failure is not the small 2011 training set (161 days).

What the model did learn (frozen 2017 fits, standardised coefficients, all small): tier-1 event day +0.16 (A) / +0.09
(B); Asia range / ATR +0.26 and Asia directional efficiency -0.17 at B (a wide but two-sided Asia session precedes
London-NY trend days; a one-way Asia session does not); Monday -0.22 at B. These are the only inputs with any weight,
and together they move the trend-day rate by about 6 points (0.32 vs 0.26 at B), worth far less than the 0.10 R a trade
the gate needed to add.

## Table (pre-registered variants)

| gate | explore n, net R, t, vs ungated, vs control | confirm n, net R, t, DSR, years+ | verdict |
|---|---|---|---|
| PT-01..PT-12 (11 gated trend rules + pooled book) | not read (calibration gate failed) | not run | FAIL (calibration) |

Amendment-1 items (ex-2025, drift control, bps costs, W2 rules, TEST-OLD) apply only to P&L reads and did not arise.

## Disclosures (integrity)

- **Run order:** prereg commit -> amendment 1 commit -> script commit (engine vendored) -> engine parity (no outcome
  printed: this lane's `simulate` reproduces the engine's real trades and leak-free twins exactly on S200-TREND-03, -20,
  -32, 2010-2017) -> one calibration run -> verdict. No rerun, no change to features, label, prior or threshold.
- **Seen before the prereg:** the trend lane's published per-rule aggregates (they define the candidate pool).
- **Holdout:** only 2010-2017 minute files were opened (the engine's loader asserts the window end).
- **K:** +12 (PT-01..PT-12). Program K about 3,233 -> 3,245 for this lane (plus the sibling team lanes).

## What this says, and what I would do next

1. **Retire "trend-day probability from daily structure + Asia session" as a gate input** (reading rule, section 8). It
   joins H-16 (range score beyond HAR), H-17 (trend-day continuation) and OS-SD (vol-tercile gating): in gold, the
   volatility of tomorrow is forecastable, its directional efficiency is not, from these inputs. Do not re-test
   neighbouring thresholds, labels or feature sets without a new information source.
2. **The only information with weight was intraday and scheduled:** the Asia session's shape and tier-1 event days. If
   the team wants another look, it needs a new input class (options-implied: GVZ term structure or risk reversals;
   order-flow: COMEX volume or depth; positioning shocks), not another transform of OHLC history. Each is a new trial
   with its own prereg; GVZ was already retired as a range input (H-16), so the bar is high.
3. **The cost problem is not a selection problem.** The gross trend premium is spread across days we cannot tell apart
   in advance; the lever is cost per trade (the `mlexec` lane, a swap-free venue: owner decision) or a longer horizon,
   not a better day filter.
4. Owner-only items: none raised (no sizing, instrument, budget or live change).
