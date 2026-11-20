# Pre-registration: gold daily family GD-1 (session split + macro filter), 2026-11-20

Author: macro-researcher (staff lane). Committed BEFORE any gold return in the windows below is read. One pooled family,
three fixed variants, one registry trial (K +1). Holdout (>= 2025-10-01) sealed: no bar or macro row at or after
2025-10-01 is loaded for any statistic (asserted in code).

## Why (and what is already known against it)
- Brief (research director, 2026-11-20, `~/tmp/plan-1120/edge.md`): the trend pool's whole 2018-25 gross was gold's
  long drift; look for a daily-horizon edge that beats drift at equal time in market.
- (a) Session split. Gold rising in eastern hours and falling in western hours: Donati & Jung, CBS master's thesis
  "Gold Price Dynamics Around the Clock" (https://research.cbs.dk/en/studentProjects/gold-price-dynamics-around-the-clock/,
  sample and cost treatment *(unverified)*, single source, not peer reviewed, gross). Press figures for 2026 (StoneX
  2026-11-16; WGC H1 2026 via FXStreet) fall in our holdout and are motivation only, not evidence.
  Our own evidence: H-04 `asia_drift` (trial #44, 2026-10-18) read **2010-01..2025-12** with a 1.5 ATR(1h) stop:
  gross +0.043 R, t 3.09, n 4,025; net -0.093 R at priors. So 2010-2025 is SPENT for (a); the only unread window is
  **2003-01-01..2009-12-31**. Disclosed: the 2010-25 gross sign is known and positive (this is a persistence test on
  older data, not a fresh idea). New element vs H-04: no stop, bps costs at the measured ICM cost, and the drift-control
  comparison H-04 never made.
- (b) Macro filter. R-08 (literature) and R-16/H-84 (2010-17 screen, K +4) retired the 20-day DFII10 and broad-dollar
  SIGN as a 4h/1d direction rule: gross t -0.59 (real yield, wrong sign) and +0.47 (dollar). Sprint-200 S31 used it as a
  gate (explore 2010-17, fail). **This variant re-tests a retired family in a new form** (long/flat time-in-market
  filter, no barriers, judged against drift). Prior is low; run because the brief directs it and K +1 is cheap. Unread
  windows for it: 2006-02-01..2009-12-31 and 2018-01-01..2025-09-30 (sprint-200 macro confirm not run; H-84 had no
  confirm). Known publicly (not from our data): the real-yield link broke in 2022-24, inside the 2018-25 window.

## Data (pinned)
- Bars: `raw/xauusd_1m_dukascopy_{YYYY}.parquet`, mid = (bid_close + ask_close) / 2; a bar is labelled by its open and
  visible at open + 1 min. Price at instant T = mid close of the last bar visible at or before T whose open is >= T - 5
  min; else missing and that day is dropped (count reported).
- Macro: `macro_fred.parquet` (series `real_yield_10y` = DFII10, `broad_dollar` = DTWEXBGS), joined only through
  `goldbot.data.store.asof_join` on `available_utc` at the decision instant (latest available vintage).
- Costs: round trip $0.23/oz (`config/arm_costs.yaml` frozen figure; >= every ICM cost-of-record cell,
  `cor-2026-10-31.2`, Asia $0.204) and swap long $0.5112/oz/night (ICM measured broker terms on the brain, 2026-11-20,
  -51.12 USD/lot), Wednesday triple, converted to bps at the reference price $4,163 (median mid of the 20 days to
  2026-10-18): round trip 0.553 bp, swap 1.228 bp/night (4.5 %/yr). Constant bps over history (overcharges the low-rate
  years: conservative). Stress: 2 x both.

## Variants (fixed)
- **A1 Asia long**: each weekday, long 00:00 -> 07:00 UTC (H-04's window), flat otherwise. No rollover inside (ICM
  rollover 21:00/22:00 UTC), so no swap. Cost 1 round trip per day. Window 2003-01-01..2009-12-31.
  Drift control: f x BH_net with f = 7/23, BH_net the 00:00 -> next weekday 00:00 UTC return less swap (1 night,
  Wednesday 3).
- Daily decision for B: T_d = 20:00 UTC each weekday (before the rollover in both DST regimes); position held T_d ->
  next weekday T. x_RY = DFII10 last - DFII10 21 observations earlier; x_USD = ln(DTWEXBGS last / 21 obs earlier);
  both from rows with `available_utc` <= T_d.
- **B1 both falling**: long iff x_RY < 0 and x_USD < 0, else flat.
- **B2 composite**: long iff z_RY + z_USD < 0, z = x / sd(x over the previous 252 decision days, min 126; past only).
- B costs: half a round trip per entry and per exit, swap per rollover held (Wednesday 3). Windows pooled:
  2006-02-01..2009-12-31 and 2018-01-01..2025-09-30 (last position closes before 2025-10-01). Macro rows before the
  window start are used only as signal warm-up. Drift control: f x BH_net (BH 20:00 -> 20:00, swap every night), f =
  the variant's share of days long (outcome-blind).

## Statistics and reading rule (written before the data)
Per variant, on daily net returns r_t (flat days = 0) and excess e_t = r_t - f x BH_net_t:
- SR = mean / sd x sqrt(252). CIs: stationary block bootstrap, mean block 20 days, 10,000 resamples, seed 20261120,
  percentile, at level 1 - 0.05/3 (Bonferroni over 3 variants; the 95 % CIs are reported too).
- **PASS (a variant)** iff all hold: (1) SR_net >= 0.5; (2) SR_net CI lower bound > 0; (3) mean e_t CI lower bound > 0;
  (4) mean net > 0 at 2 x costs. **Family PASS** iff >= 1 variant passes; else **FAIL**: GD-1 retired, (a) closes for
  gold time-in-market at daily cost; (b) reconfirms R-16 (no new form re-tested without a new information source).
- Reported, not gating: per-year net, A1 without 2006-07 (quant DQ ruling for <= 4h cells), alpha/beta of r_t on BH,
  1 x and 2 x cost.
- Power (ADR 0085 standardised: delta 0.10 R at sd 1.2 = per-day Sharpe 0.083, ann. 1.32; z 2.39 at alpha 0.05/3):
  A1 n ~1,750 days -> power ~0.86; B pooled n ~2,900 -> ~0.98. Realistic: at true net SR 0.5, A1 power ~0.14 and B
  ~0.25 at the Bonferroni level. Disclosed: a FAIL at SR 0.5 truth is likely; a PASS is informative.
- If PASS: demo deploy plan only (arm spec, kill rule, sizing stays owner/CFO). Live is owner-only.

## Trial cost
K +1 (one registry row, family `gold_daily_gd1`, rule_ref this file @ its commit). No other look at these windows for
this family after the run.
