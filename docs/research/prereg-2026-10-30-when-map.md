# Pre-registration: when-to-trade map on random entries (H-82; research plan bet 3; includes H-A trend side)

Author: microstructure-researcher, 2026-10-30. Written and committed **before any return in this screen was computed**.
Base: `origin/release/agents-rd-cto` (`c19c7d8a`). Data: 2010-2017 only. The 2018+ half and the holdout are not touched.

## 1. Question and economic reason

Every family so far is indistinguishable from the random control (research-plan.md section 1). If an edge exists that
does not depend on entry logic, it lives in **when** (session, day type) and **which side** (with or against the recent
trend). Random entries carry no entry skill, so any cell where they pay net of costs is a timing or side edge that every
family inherits. Mechanisms that could produce one:

- Session liquidity and information arrival: London and the New York data window carry most of gold's price discovery;
  Asia is thin (Cai, Cheung and Wong 2001, *J. Futures Markets* 21(6), COMEX gold futures 1994-1997, intraday volatility
  pattern, a volatility claim, gross; single paper for gold *(unverified page refs)*).
- Volatility persistence: a wide prior day predicts a wide day (Corsi 2009, *J. Financial Econometrics* 7(2), HAR
  model, realised volatility, not a return claim; widely replicated). On wide days a fixed $3 bracket reaches a barrier
  faster and the time exit is hit less.
- Short-horizon time-series momentum: the first half-hour predicts the last (Gao, Han, Li and Zhou 2018, *JFE* 129(2),
  SPY and ETFs 1993-2013, gross; replicated on other futures, not on spot gold net of costs: our R-05
  intraday_momentum failed on cost). This is the mechanism behind H-A (performance-analyst, trade only with the sign of
  the 4h return).

**Prior: low.** Our live random controls read +0.025 R net on 354 trades (noise). Priors against: R-02 session_open
retired (gross +0.042 R, t 1.31), H-04 asia_drift stopped on cost (gross +0.043 R), session-VWAP fades closed (H-76),
the ADR 0058 15m discovery top specs had negative own mean R. Expected: no cell passes; |with - against| < 0.03 R. A
negative result is the point: it retires the "timing-only" line (plan line 3) and the H-A filter cheaply.

## 2. Design (frozen)

**Entries (random_bracket, S0 geometry).** `config/exploration_arms.yaml` S0: 1m bars, stop $3.00, target 1.0 R ($3.00),
hold 30 bars (30 min), entries on the minutes {0, 10, 20, 30, 40, 50}. S0's `window_utc` [07:00, 19:50] is **widened to
every open minute**, because the question is when. S0 draws its side from a hash coin; here **both sides are taken at
every entry time** (the expectation over the coin, an antithetic pair). One of the pair is "with" the trend and one
"against", so every entry time gives one trade to a with cell and one to an against cell. Overlapping trades are allowed
(history, no max_open); dependence is handled by the day bootstrap.

**Prices and path.** Dukascopy 1m bid/ask bars, mid = (bid + ask) / 2. Entry at the mid open of the bar stamped t (the
bar must exist). Barriers on the mid high/low of the bars stamped [t, t + 30 min). A bar touching both barriers counts as
the stop (conservative). No barrier: exit at the mid close of the last bar in the window. An entry is dropped if any
minute of [t, t + 30 min] is closed by `goldbot.data.calendar.DEFAULT_SESSIONS.is_open` (daily break, Friday close,
weekend) or if fewer than 10 of the 30 bars exist (feed gap).

**Cost: the WORSE of the two measured broker costs, rounded up, in USD/oz, divided by the $3 stop.** Measured round trips
(research-plan.md section 1, performance-analyst 2026-10-30, re-derived from aux.db by the director): IC Markets
~0.05 R at $3-4.50 stops (spread 0.025 R + commission 0.024 R), at most ~$0.22/oz at $4.50; Vantage 0.021 R. The worse
is IC Markets. I charge **$0.23/oz** flat (the conservative 1m model, `cost-model-1m-2026-10-25.md`: p90 spread both legs
+ slippage upper CI + commission), at or above IC Markets anywhere in its band: **0.077 R** at the $3 stop. The measured
fills are London/NY-heavy, so thin hours add a **session add-on**: 0.5 x max(0, s_entry - s_ref) + 0.5 x max(0,
s_exit - s_ref), with s the Dukascopy `spread_mean` of the entry (exit) bar and s_ref the median `spread_mean` of London
and NY-overlap bars over the 20 trading days before the entry's trading day (point in time). Sensitivity (reported, no
verdict): central $0.144/oz flat, no add-on.

**Sessions (4), from `goldbot/data/calendar.py` DEFAULT_SESSION_WINDOWS, each boundary DST-aware in its own zone.**
- London: [London 08:00, New York 08:30) (the configured "london" window).
- NY overlap: [New York 08:30, London 16:30). London 16:30 = the LSE close; it splits the configured "newyork" window.
- NY late: [London 16:30, New York 17:00).
- Asia: every other open minute: the configured "asia" window [Tokyo 08:00 = 23:00 UTC, London 08:00) plus the thin hours
  after the New York close, which `SessionTable.session_label` also labels "asia".
In summer time (BST, EDT): London 07:00-12:30, NY overlap 12:30-15:30, NY late 15:30-21:00 UTC, Asia the rest. The
session is that of the entry minute t.

**Range tercile (3; point in time).** Trading day D runs New York 17:00 to 17:00. range(D) = mid high - mid low of D's
bars. x(D) = range(D-1) / mean(range(D-20..D-1)). Cut points: the 1/3 and 2/3 quantiles of x over the 250 trading days
ending D-1 (minimum 60 days; earlier days are dropped). Low / mid / high. Only completed days enter.

**Trend side (2; point in time).** r4h = mid close of the bar stamped t - 1 min minus the mid close of the last bar
stamped at or before t - 241 min, both closed by t; the anchor must lie within 30 min of t - 241 min. If r4h = 0 or
either bar is missing, the entry time is dropped. "With" = the side equal to sign(r4h).

**Cells:** 4 x 3 x 2 = 24. **Statistic:** mean net R per cell over 2010-2017.

## 3. Tests and reading rule (written before any data is seen)

**Cell rule (primary, 24 tests).** Block bootstrap by trading day (resample days with replacement, B = 20,000, seed
20261030; cell mean = resampled sum of R / resampled count). One-sided p_i = (1 + #{boot mean <= 0}) / (B + 1). Holm
step-down at family alpha 0.05 over the 24 cells: sort p ascending; cell k passes while p_(k) <= 0.05 / (24 - k + 1).
Reported as the Holm-adjusted lower bound (the 0.05 / (24 - k + 1) bootstrap quantile of the cell mean). **A cell passes
only with its Holm-adjusted lower bound > 0.**

**Retirement rule (research-plan.md line 3, as written).** If no cell has net mean > 0 with day-clustered t >= 3 and
>= 6 of 8 years positive, the timing-only line is retired (2026-11-13). A cell that passes Holm but fails 6-of-8 is
"passes, not stable" and earns at most a pre-registration on 2018-2025-09, never an arm.

**Trend-filter rule (H-A, 1 test).** Per entry time d = R_with - R_against (the flat cost cancels; the session add-on is
identical on both legs and cancels too, so d is the same gross and net). Pooled over all 12 session x tercile strata,
day-block bootstrap as above. **H-A passes if mean d >= 0.05 R and the 90 % lower bound (5th bootstrap percentile) > 0.**
Otherwise H-A fails and the 4h trend filter on S5/Z4/Z7 is not run. By-stratum d is descriptive only. Note: the pooled
"with" mean minus the pooled "against" mean equals mean d.

**Multiple testing.** **25 tests** added to cumulative K. The deflated read reports the best cell's day-clustered t
against the expected maximum of 25 null t's (~2.0) and against cumulative K (~1,250: ~360 arm ids + 864 discovery specs
+ 25; expected null max ~ sqrt(2 ln K) ~ 3.8).

**P4 screen expectation.** Gross ~0 in every cell (|gross| < 0.03 R); net ~ -0.08 to -0.12 R (cost plus thin-hour
add-on). A cell needs gross >= ~+0.10 R to clear cost; the scalp bar (gross several times the cost) is ~+0.23 R.
Nothing is expected to pass.

## 4. Inputs, trial cost, gates, owner items

- Inputs: `raw/xauusd_1m_dukascopy_2010..2017.parquet` (local only, never in the repo), `goldbot.data.calendar`. No
  `research.yml` run: a one-off script outside the repo; only aggregates go into
  `docs/research/when-map-screen-2026-11.md`.
- Trial cost: **1 screen, 25 tests added to K.** No registry trial id is consumed unless the research director books it.
  No trial dispatched, no budget change, no holdout or 2018+ read.
- Gates: nothing from this screen reaches an arm. A passing cell earns one pre-registered read on 2018-2025-09; a passing
  H-A earns trend-filtered variants of S5/Z4/Z7 as new arm ids (counted in K).
- Owner-only items: none (no sizing, budget, instrument or live change).

## 5. Known failed in this area (not re-tested)

R-02 session_open (retired 2026-10-10), H-04 asia_drift (stopped on cost, trial #44), session-VWAP fades (H-76), H-48
sweep_absorb (STOP 2026-10-19), R-05 intraday momentum (cost), ADR 0058 discovery v1 (0 survivors).
