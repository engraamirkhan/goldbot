# Pre-registration: H-84 macro-driven 4h/1d direction, screen on 2010-2017

Written 2026-10-30 (file dated to the plan's 11-03 due date) by the principal gold macro and flows researcher,
**before any H-84 outcome was computed on any window**. Base `dd39931f` (origin/release/agents-rd-cto merged).
Plan line 1, bet 1 (`research-plan.md` s.2 and s.4); hypothesis row H-84 (`hypotheses.md` A20).

**Seen before this freeze (no outcome):** file listings and sha256 of the inputs; the macro file's schema and series
row counts; the first three 1m rows of 2010; and a plumbing run of the decision builder (no barrier, no return, no
statistic) that printed only: 12,814 four-hour bars, 2,061 daily decisions 2010-01-01..2017-11-30, signal days per
variant (V1 2,008: 1,089 long / 919 short; V2 2,061: 957 / 1,104; V3 562: 308 / 254; V4 500 long), median 4h-bar
spread $0.338/oz and median ATR(1d) $18.0. These are event counts and cost inputs, the same class of fact H-01 froze
its rule on.

## 1. Why gold should pay here (economic reason)

| Claim | Source | Sample, instrument | Net of costs? | Status |
|---|---|---|---|---|
| Gold moves inversely with 10y TIPS real yields (level relation) | Erb & Harvey 2013, *Financial Analysts Journal* 69(4), https://papers.ssrn.com/abstract=2078535 *(unverified page)* | 1975-2012 (TIPS from 1997), gold spot | Gross, contemporaneous | Replicated in direction (Barsky et al. 2021, *Chicago Fed Letter* 464) |
| The real-yield relation broke after 2022 (correlation ~0.84 -> ~0.03) | RBC WM; Janus Henderson (URLs in `state-of-the-art.md` B1) | 2005-2025 | n/a | Practitioner, replicated in direction |
| Gold is a hedge and a short-lived safe haven (~15 trading days) | Baur & Lucey 2010, *Financial Review* 45(2) | 1979-2009, gold spot vs equities/bonds | Gross | Replicated (Baur & McDermott 2010, *JBF*) |
| Trailing 1-12 month returns predict the next month in futures incl. gold | Moskowitz, Ooi & Pedersen 2012, *JFE* 104(2) *(DOI unverified)* | 58 futures, 1965-2009 | Gross | Replicated (Hurst, Ooi & Pedersen 2017, *JPM*, 1880-2016, net of estimated costs); gold alone weak (Huang et al. 2020, per `state-of-the-art.md`) |
| A gold implied-minus-realised variance premium predicts gold returns at 1-24 months | "Nguyen et al. 2017" as cited in `hypotheses.md` H-03 and survey #72 *(unverified: author list, venue and sign not re-checked by me)* | gold, monthly | Gross | **Single paper** |

**Known conflict, disclosed.** R-08 retired "real-yield sign rules" from the literature (broke in 2022). The research
director reopened them inside H-84 on 2026-10-30 as a *predictive* (20-day change -> next 5-20 days) rule, not the
contemporaneous one; the predictive form has never been tested here. The 2010-2017 screen sits wholly in the
pre-break regime, so a pass here is weak evidence; the confirm window (2018-2025-09, 2022 inside it) is the
falsification test, with its own rule in s.6. V3 is H-06, withdrawn 2026-10-16 by its Q1 rule and reinstated by the
plan; H-01 (trial #48) has seen 2010-2025-09 unconditioned (gross +0.166 R, t 2.04, n 294), so V3's gross is partly
known in aggregate; the gate is the new part.

## 2. Frozen code and inputs

- Code: `scripts/research/h84_macro_screen.py`, frozen by **the commit that adds this file** (hash recorded in the
  screen doc). It calls the coded H-01/H-06 machinery unchanged: `TimeSeriesMomentumSpecialist` preset `slow_macro`
  (`barrier_atr`, `_visible_on`, `macro_agreement`, `candidates_in_context`), `drop_stub_days`,
  `data.macro.driver_frames`, `features.macro.har_rv2_annualised`, `store.asof_join`,
  `labels.triple_barrier.rollover_nights`.
- Bars: `raw/xauusd_1m_dukascopy_2010..2017.parquet` only (sha256 prefixes 2010 `26139f0b`, 2011 `a1497c69`, 2012
  `89f4b77d`, 2013 `fe31b844`, 2014 `be2bf066`, 2015 `046ddd84`, 2016 `9c6073cc`, 2017 `e04d7da0`), resampled to 4h
  (`drop_late=True`) and to the feature-day (13:30 New York). The loader asserts no bar at or after 2018-01-01.
  No 2018+ file is opened; the holdout is not touched.
- Macro: `macro-v1` `macro_fred.parquet` (local copy `~/tmp/macro-pit/macro/macro_fred.parquet`, sha256 `47473d2c...`
  = H-02's reference digest), rows cut to `available_utc` < 2018-01-01 and `value_date` < 2018-01-01 (asserted).
  Point-in-time stamps per `macro-pit-audit-2026-10.md` s.2: DFII10 and GVZCLS next US business day 23:00 UTC
  (1.75-2.75 h after H.15; ~1 day after the CBOE close); DTWEXBGS 23:00 UTC on the Tuesday after the H.10 Monday
  release. Every value reaches a bar through `asof_join` on `available_utc` at the **decision bar's close**. Known
  deviations carried from the audit: **A1** (DTWEXBGS is the latest vintage, 20-day change corr 0.994 with first
  release, 1.2% sign flips) and **A2** (DTWEXBGS before 2019 is a back-cast; 2.2% sign disagreement with the legacy
  real-time DTWEXB). Both touch V2 and V3: a V2/V3 survivor must re-pass on true first releases (spliced DTWEXB +
  ALFRED, data-engineer, 0 trials) before its confirm.

## 3. Common mechanics (all four variants)

- **Decision point:** each stub-free feature-day (daily bars spanning >= 12 trading hours), read on the first 4h bar
  whose close is at or after the settlement (H-01's `_visible_on`, once per daily bar). Decision closes from
  2010-01-01 to before 2017-12-01.
- **Entry:** that 4h bar's mid close. R = 1.5 x ATR(1d) (Wilder 14 on stub-free daily mid bars, last visible at the
  decision bar's close), frozen for the trade.
- **Exit (H-01's geometry, unchanged, no exit choice in this screen):** stop 1 R, target 3.0 x ATR(1d) = 2 R, time
  barrier 124 four-hour bars (about 20 trading days); checked on 4h mid bars; stop and target in one bar = stop; a
  stop is filled at the worse of the stop and the bar's open (weekend and news gaps); target filled at the target;
  time exit at the 124th bar's close. A trade still open at the last 2017 bar is closed there (counted as
  `truncated`).
- **Holding:** one position at a time per variant (a decision is skipped while the previous trade is open, H-01's
  `one_at_a_time` semantics). Expected hold 5-20 trading days.
- **Costs (binding, "the worse measured cost"):** per trade max($0.23/oz all-in at mid [`config/arm_costs.yaml`,
  p90 spread both legs + slippage upper CI + commission], half the 4h-bar mean spread at entry + at exit + $0.37/oz
  [H-01's settings prior: 2 x 0.15 slippage + 2 x 3.5 USD/lot commission]). At the median spread that is ~$0.71/oz
  = ~0.026 R at R ~ $27.
- **Swap (binding):** `research_costs` falls back to the settings prior because `costs-v1` has no measured swap:
  long -60 USD/lot/night = **$0.60/oz per night**, short 0, **x3 on Wednesday's rollover** (server clock
  Europe/Athens), charged per rollover from the decision bar's close to the exit bar's close
  (`rollover_nights`). This is today's swap, which is what a deployment would pay; it overcharges 2010-2017 longs.
  Expected ~10 nights a trade: ~$6/oz = **~0.2 R per long trade**. Swap-free net is reported, never read.
- **1.5x costs:** round-trip cost x 1.5, long swap $0.90/oz/night, short swap **$0.15/oz/night** (the prior's 0
  flatters shorts for 2010-2021, when short swap was negative: H-01 swap-bias note).

## 4. Variants (exact rules; K +4)

| Id | Rule (side at the decision bar; 0 = no trade) | Inputs (`research.yml`: `macro: true`, release `macro-v1`) | Sides | Expected trades (one at a time) | P4 screen expectation |
|---|---|---|---|---|---|
| V1_RY | side = -sign(`macro_real_yield_chg20`): DFII10 first releases, 20-observation difference | DFII10 | long + short | ~200-260 | gross 0 to +0.05 R; P(pass) ~0.07 |
| V2_USD | side = -sign(`macro_dollar_chg20`): DTWEXBGS, 20-observation % change | DTWEXBGS (A1, A2) | long + short | ~200-260 | gross 0 to +0.05 R; P(pass) ~0.05 |
| V3_H06 | H-01 `slow` signal (mean of 20/60/120-day vol-scaled daily returns, 60-day vol; long >= 0.5, short <= -0.5) kept only when both `real_yield_chg20` and `dollar_chg20` agree with the side (both < 0 long, both > 0 short); the coded `slow_macro` preset | bars, DFII10, DTWEXBGS | long + short | ~100-150 | gross +0.10..+0.20 R but t < 2 likely at n ~120; P(pass) ~0.08 |
| V4_VRP | long when `macro_vrp` = GVZ^2 - HAR-RV^2 (as coded: GVZ as of the decision bar's close; HAR of completed stub-free UTC days before the bar's day) is at or above the 2/3 point of its own previous 252 daily decision values (share of prior values below it >= 2/3; at least 126 prior values) | GVZCLS, bars | long only | ~80-120 | long swap ~0.2 R a trade makes net > 0 unlikely; P(pass) ~0.03 |

Joint P(at least one passes) ~0.2, in line with the plan's 0.25.

## 5. Statistic and reading rule (frozen)

- **Unit:** net R per trade at the binding cost and swap. Also reported: gross R, net at 1.5x, swap-free net, long
  and short split, per-year net, mean nights, net R summed per decision day.
- **Uncertainty:** circular block bootstrap over calendar weeks 2010-01-04..2017-12-31 (trades assigned to their
  entry week; empty weeks kept), block 4 weeks, B = 10,000, seed 20261103, mean = sum R / trade count per resample.
  One-sided p for net mean <= 0: p = (1 + #{boot - obs >= obs}) / (B + 1). Gross t = gross mean / bootstrap SE.
- **Multiplicity:** Holm across the four variants at one-sided alpha 0.05.
- **PASS** (all five): (a) n >= 60 trades; (b) Holm-adjusted p < 0.05 on net (the Holm-adjusted lower bound of net
  mean R > 0); (c) gross t >= 2; (d) >= 5 of the 8 years 2010-2017 positive (a year counts only with >= 10 trades and
  mean net R > 0, H-01's definition); (e) net mean R > 0 at 1.5x costs. Otherwise **FAIL**.
- **On FAIL:** the variant is retired (added to the retired list in `hypotheses.md` with its numbers); no
  re-parameterisation (threshold, window, lookback, geometry) without new evidence and a new id. If all four fail,
  plan line 1 is retired; macro stays only as gate primitives inside discovery v2 (bet 3), counted there.
- **On PASS:** the variant goes to **one** confirm on 2018-01-01..2025-09-30 (s.6), not run in this lane. A pass
  that exists only through shorts (long-side net <= 0) is reported as such; it still confirms, but under s.6 (c).

## 6. Confirm rule for a survivor (written now, run later only on the director's allocation)

Same code and rule on 2018-01-01..2025-09-30 with macro cut at 2025-09-30, one registry trial each (`rule_ref`
this file): PASS if (a) net mean R > 0 with the week-block bootstrap one-sided p < 0.05 (no further Holm: the
survivor set is fixed here); (b) >= 5 of 8 years positive (2025 to September counts); (c) net summed over
2022-2024 > 0 (the real-rate break: the falsification test) and long-side net > 0; (d) net > 0 at 1.5x costs.
V2/V3 also re-pass the screen on true DTWEXB/DTWEXBGS first releases first (s.2).

## 7. Trial cost and multiple-testing count

- Screen: **0 registry trials**, **K +4** (four looks; ~1,256 -> ~1,260). Each confirm: 1 registry trial from the
  director's quarter budget. No holdout scoring.
- Any later survivor pays the DSR at the K of its day.

## 8. Owner-only flags (nothing assumed)

- **Sizing:** R ~ $27/oz on 2010-2017 and ~1.5 x today's ATR(1d) (~$60-90) live; one 1.5 x ATR(1d) stop at 0.01 lot
  is ~£45-70, above the 1% per-trade cap at £1,000 and near the £50 daily limit (`macro-pit-audit-2026-10.md` s.4.6).
  Any survivor is demo/shadow only unless the owner changes balance or minimum position size.
- **Swap:** the binding long swap is a prior, not measured (`costs-binding-2026-10-18.md` K1-K3); a live 4h/1d book
  depends on it more than on any spread figure.
