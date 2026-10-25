# High-volume learning program: 200-400 demo trades a day (research director, 2026-10-25)

Owner directive 2026-10-25: "put in strategies, learn from present and past experiences, do many trades (scalping,
intraday, swing), a few 100s a day so the system can learn quickly and the model improves significantly and quickly."
This is the program design. It dispatches no trial, spends no budget, reads no holdout (2025-10-01..2026-09-30) and
touches no code or config. The engine work goes to the architect (section 5); the frozen rules go into the
pre-registrations of section 4 **before** any exploration fill of an arm is read.

## 0. What volume buys, and what it does not (the honest premise)

- **Volume buys execution truth fast; edge discovery comes from history.** At a per-trade SD of about 1 R, telling
  a +0.05 R arm from zero at the gate's t of about 3.4 needs (3.4 / 0.05)^2, about **4,600 trades for one arm**, and
  more once SEs are clustered by day. At 300 a day spread over about 20 arms, a single arm reaches that in about a
  year. History has millions of 1m events now. So: **history estimates edge (priors); live exploration measures cost,
  slippage, latency, fill parity and the live-vs-backtest gap, and gives the meta-labeller rows it can learn from.**
- **No threshold is loosened to make volume.** Volume is a capacity target, not a quota. An arm that fires 3 times a
  day stays at 3. Loosening rules to fill a quota turns the book into noise and its "learning" into fitting noise.
- **The gross-edge problem stands.** H-50 (box 15m): gross +0.030 R, t 1.61; H-48: gross negative. The -0.297 R net
  is mostly the 2010-2019 spread era (0.17 R), not today's measured $0.09/oz (about 0.03 R at a 15m stop). Cheaper
  costs make a small gross edge tradable. They do not create one.
- **About half the volume is control and cost arms** (random-entry brackets, 30 s cost probes). These are not
  strategies. They are the unbiased label set the ranker needs (no selection bias) and the null each arm is measured
  against.
- **Measured today:** scheduled probes 36 / 85 / 26 / 6 a day on 10-20..10-23, plus 1-3 mirror probes a day
  (aux.db `cost_probes`, read-only). The ~90 a day is capacity, not throughput, and no `cost_probe_skips` row exists
  in that window to explain the fall. **That regression is item 0** (section 5, E0).

## 1. Volume plan

Three probe-only learning hosts, one per style, so a swing drawdown or a scalp bug cannot stop the others' learning
and the open-risk sum of one style cannot eat another's daily allowance. **Open 2 new IC Markets Raw GBP demo
accounts** (free; MT5 logins are ours): `icm-scalp` and `icm-swing`, funded about £11,000 so ADR 0042's 2% =
£220/day applies unchanged. `icm-probe` keeps intraday plus the cost probes. `icm-demo` is untouched: strategies,
13 shadow agents, no exploration (ADR 0036 ruling). Every arm trades at 0.01 lot with its **own native stop and
hold** (H-59 principle), through the RiskGate, unchanged (1% cap = £110, daily £220, weekly 6%, streak, SIZE_DOWN and
HALTED). Exits are never gated.

Signals/day are **design estimates to be measured by H-60 in week 1**. An arm whose measured rate differs launches
at the measured rate, not a loosened rule. £ figures assume 1 oz per 0.01 lot and GBPUSD about 1.33.

| Host (style) | Arm id | TF | Rule (frozen in the prereg) | Est. trades/day | £ at stop (0.01) | Max open | Arm £/day cap |
|---|---|---|---|---|---|---|---|
| icm-scalp | S0 random bracket (control) | 1m | entry every 10 min 07-20 UTC, random side, stop 1 ATR1m, target 1R, 30 bars | 78 | ~1.5 | 3 | 15 |
| icm-scalp | S1a/S1b session-VWAP 2-sigma fade | 1m | target VWAP (a) or 1R (b), stop 1 ATR1m | 2 x 8-15 | ~1.5 | 2 each | 12 each |
| icm-scalp | S2a/S2b opening-range break, London 07:00 and NY 13:30 | 5m | range 15 min (a) / 30 min (b), stop at range mid | 2 x 2-4 | ~3 | 1 each | 10 each |
| icm-scalp | S3a/S3b sweep-and-reclaim (sweep_absorb logic) | 5m | target 1R (a) / 2R (b) | 2 x 5-10 | ~3 | 2 each | 12 each |
| icm-scalp | S4 owner box break+close+MFI | 5m | roster rule at 5m (refused on the 15m clock) | 3-5 | ~3 | 2 | 12 |
| icm-scalp | S5a/S5b EMA pullback in 15m trend | 1m | stop 1.0 (a) / 1.5 (b) ATR1m, target 1.5R | 2 x 10-20 | ~1.5-2.3 | 2 each | 12 each |
| icm-scalp | S6 meta-labelled stream (from month 2) | 1m/5m | top-decile meta p over all S candidates | 10-20 | ~2 | 2 | 15 |
| icm-probe (intraday) | P0 scheduled M1 cost probe (exists) | 1m | 30 s hold, cost only | 60-90 | <1 | 1 | 10 |
| icm-probe | I0 random bracket (control) | 15m | 1 an hour 07-20 UTC, stop 1.5 ATR15, target 2R, 96 bars | 13 | ~9 | 6 | 40 |
| icm-probe | M mirrors of the 13 shadow agents at own stop | 15m-1h | as today (ADR 0037 am. 4/5, H-59) | ~8 | 9-17 | 4 | 45 |
| icm-probe | I1 tsmom 1h band p in [0.30, 0.36) as fills | 1h | the H-57 band, filled (H-57 itself stays shadow) | 2-3 | ~17 | 1 | 20 |
| icm-probe | I2a/I2b box 15m without MFI / stop 1.0 ATR | 15m | ablations named in H-51a and the tight-stop study | 2 x 1-2 | 6-9 | 1 each | 15 each |
| icm-probe | I3 meta-labelled stream (from month 2) | 15m | top-decile meta p over I candidates | 3-6 | ~9 | 2 | 25 |
| icm-swing | W0 random bracket (control) | 4h | 1 a day, stop 1.5 ATR4h, target 2R, 24 bars | 1 | ~38 | 3 | 110 |
| icm-swing | W1 tsmom 4h and 1d, incl. p band [0.30, threshold) | 4h/1d | frozen tsmom configs | 0.3-1 | 38-110 | 2 | 110 |
| icm-swing | W2 box 4h roster rule; W3 macro 1d (macro-researcher's frozen rule) | 4h/1d | as roster / prereg | 0.1-0.5 | 38-110 | 1 each | 110 |
| **Total** | about 24 arms | | | **~290 (range 200-350)**; strategy arms ~110-150 | | | each host ≤ £198 (0.9 x 220) |

**Arithmetic.** Scalp ≈ 78 + 24 + 6 + 14 + 4 + 30 + 15 = **~170**. Intraday ≈ 75 + 13 + 8 + 3 + 3 + 5 = **~105**.
Swing **2-3**. Swing cannot contribute volume at any honest rate; it is on its own host for risk isolation (a 1d stop
at 0.01 lot is £60-110, which would breach `icm-probe`'s £45 `max_loss` and eat its daily allowance through open
risk). Expected drift at **zero gross edge**: scalp about -0.1 R x £1.8 x 170, about -£30/day (cost-bound: at a $2
stop the $0.09 + slippage is about 0.1 R, so 1m arms need about 0.1 R gross just to break even; 5m about 0.05 R).
Intraday about -£8/day; swing about £0. Daily P&L SD on the scalp host is about √170 x £1.8, about £25. All three
are far inside £198.
**Concurrency:** host max_open = sum of arm caps, bounded by open stop risk ≤ 0.5 x the day's remaining allowance
(scalp ≤ 15 open, intraday ≤ 12, swing ≤ 4).
**Kill switch:** at null-edge drift the scalp host loses about 1.4% a week. The 12% drawdown kill switch is a rail,
not a variable. Demo account rotation at -8% needs an ADR (trading-safety + CFO) before it is ever used. The per-arm
stops below keep the drift lower.

**Per-arm stop (H-61, as H-59):** each arm stops for good when its running net R in exit order reaches **-30 R
within its first 300 trades** (scalp) / **-15 R within 100** (intraday) / **-10 R within 40** (swing). Re-entry only
by a new pre-registration.

## 2. Learn from the past: the historical arm-prior program (H-60, 0 trials, K_eff + arms)

- **Data:** data-v1 Dukascopy 1m, 2015-01..2025-09-30. **The holdout is never read.** Run as a GitHub Actions matrix
  (one job per arm x 2-year block), because the Mac VM cannot reach Dukascopy but can read the release.
- **Rule arms** have nothing fitted, so the read is per-year out-of-sample statistics. **ML arms** (S6, I3, the
  meta-labeller) use anchored walk-forward: train ≥ 3 years, test 6 months, purge = max hold, embargo 1 day, refit
  every step.
- **Costs per era:** Dukascopy bid/ask at the bar, plus measured slippage and commission (probe fills by hour of day,
  with the K9 fx field). Slippage is never favourable, and the swap is charged per rollover.
- **Output per arm:** fire rate per day (this feeds section 1), gross and net mean R, SD, day-clustered t, a per-year
  table, hold-time and concurrency distributions, and the share of stops that fit £ caps at 0.01 lot. Also live-label
  parity on the 2026-10 bars.
- **Prior for the bandit:** Normal on net mean R from the **2019-2025-09** OOS years (current cost era), mean x 0.5
  (decay haircut), effective prior n = min(50, n_hist / 100), so roughly 50 live trades can overrule history.
- **Combination:** per arm only, never pooled across arms. Live rows update the arm's own posterior with **daily arm
  sums** (respecting day clustering). Backtest rows are never added to live n.
- **Parity alarm:** after 200 live trades, |live mean - backtest mean| > 3 SE means implementation shortfall or a
  bug, investigated before either number is believed.
- **K:** a 0-trial look with no verdict, no promotion and no retirement (the H-49 stage-1 precedent). It adds the
  arm count (about 24, clustered by return correlation via `k_eff`) to K **once**. K goes about 116 -> about 140, and
  the one-sided Bonferroni t moves 3.33 -> 3.38. That cost to every later reading is small and is stated rather than
  hidden.

## 3. Learn from the present: the online loop (H-61 allocation, H-62 meta-labeller; 0 trials)

**Meta-labeller (one per style; arm id as a feature; scored and evaluated per arm).**
- **Inputs:** the fill-time feature snapshot (as-of joined on `available_utc`, stored with the fill so there is no
  recompute skew). At most **40 features**, a list frozen per quarter and chosen by the ml-researcher on history only,
  never on exploration outcomes.
- **Labels:** net R on the arm's own barriers. The S0/I0/W0 random brackets are the unbiased core.
- **Retrain:** weekly (Sunday) once there are ≥ 1,500 new admissible labels in the style and ≥ 3,000 in total.
  Cross-fitting: 5 folds blocked by session day, purge = max hold, embargo 1 day (5 for swing). Isotonic calibration
  on out-of-fold scores.
- **Strict forward:** a trade is only ever scored by a model whose training ended before its decision time.
- **Champion/challenger:** the challenger replaces the champion only if its log-loss on the **next 2 weeks**, unseen
  in training, beats the champion's by a pre-set margin. Never chosen in-sample. A model never reaches `icm-demo`
  except through promotion.

**Allocation: Thompson sampling over frozen arms.**
- **Posterior:** Normal-Inverse-Gamma on net R per trade, per arm, updated nightly at 21:30 UTC from daily sums.
  Never intraday, so it cannot chase.
- **Allocation:** each arm's share of its host's £ budget and open slots ∝ P(arm is best | data), with a **floor of
  5%** per live arm (evidence keeps accruing) and a **cap of 30%**.
- **Taking a candidate:** within an arm, a candidate is taken with probability 0.8 by meta-p rank and 0.2 uniformly.
  **Every candidate logs its propensity** (taken or not, the probability, meta and bandit model versions), so
  off-policy estimates (IPW / doubly robust) stay unbiased.
- **Frozen arms:** the bandit never edits an arm. New arms enter only by an amendment written before their first fill.

**Exploration is not promotion.** All rows carry `exploration`, are excluded from population evidence
(`population.py`), and are never pooled into H-57/H-58 or any trial.

## 4. Pre-registrations and promotion

| ID | What | Trials | K |
|---|---|---|---|
| H-60 | Historical arm priors: the arm list, rules, cost model and outputs of s.2 | 0 | K_eff + about 24 (clustered), once |
| H-61 | Exploration program: hosts, arms, £ caps, per-arm stops, Thompson rules, propensity logging, -8% rotation pending its ADR | 0 | 0 (tagged; no reading) |
| H-62 | Online meta-labeller protocol (s.3), feature list v1 frozen | 0 | 0 until a model is proposed for promotion |
| H-63+ | One per graduate (below) | 1 each, 2027Q1 open slots | standard |

**Graduation, then promotion.**
- **Graduate:** an arm becomes a candidate when, after **≥ 300 admissible trades over ≥ 20 session days**,
  P(net mean > 0) > 0.95 and live-backtest parity holds.
- **Promotion:**
  1. A new pre-registration of the **exact** config, written before any further fill is read.
  2. A research trial on 2015-2025-09 at full K, including H-60's k_eff, with gate-1 DSR t about 4.1+.
  3. The holdout scored once.
  4. A forward n ≥ 60 at native stop on fills **after** the pre-registration date, admissible by H-52.
  5. The evaluator's verdict.
  6. A roster shadow agent on `icm-demo`, then capital weight only through the tournament: one promotion per family
     per quarter, with sizing under the RiskGate.
- **Budget:** 2027Q1 open = 14; H-57 and H-58 take 2 and H-53 takes 1, leaving **11**. I reserve **4 for
  graduates**; the 6 reserved slots are untouched. The scalping cost bar of H-14/O-4 (0.35 R withdrawal) applies.
- **H-59** stays as written for the mirror probes. Its 60-trade budget does not cover the arms; H-61 carries their
  stops.

## 5. Engine support (for the architect)

- **E0.** Diagnose the scheduled-probe fall (85 -> 6/day) and log every skip with a reason. Fix before anything
  else.
- **E1.** Per-account `decision_tf`: a 1m base clock on probe-only hosts only, so 5m arms decide on 1m closes and
  time exits age in 1m bars. `icm-demo` stays on 15m. Needs a trading-safety review.
- **E2.** Arm registry: frozen config hash, style, host, per-arm magic (`magic_base` + arm index) so the broker
  ledger reconciles per arm. Exploration status is refused on any account that is not `probe_only`.
- **E3.** Tighten-only layers before the RiskGate: per-arm daily £ cap, max_open, the cumulative-R stop, and a host
  open-risk ≤ 0.5 x remaining allowance. The RiskGate itself is unchanged.
- **E4.** N probe-only hosts. Today two shadow hosts fail at load: keep `shadow_host` single and allow many
  `probe_only`. Each host gets its own MT5 terminal and bridge (portable installs on the Wine box, SRE), plus broker
  request-rate limits.
- **E5.** Allocation table: a nightly signed, versioned job writes it. The engine reads it and fails closed to equal
  shares within its caps.
- **E6.** A candidate journal for every candidate, taken or not: propensity, meta p, model and bandit versions, and
  the feature snapshot (≤ 40, list version).
- **E7.** Fill cost record per arm fill: spread, slippage per leg, commission with fx (K9), swap, latency.
- **E8.** A model-serving slot for the weekly meta-model, with strict-forward version pinning.
- **E9.** Dashboard and reports keep exploration apart from the book, by host, arm and style. The finance ledger
  reconciles all demo hosts.
- **E10.** Historical runner: a GitHub Actions matrix over arm x block, reusing `goldbot/research/walkforward.py`
  and `cpcv.py`, with the same rule code as live (no re-implementation).

## Open risks
- 1m arms are probably cost-bound (about 0.1 R). Expect most scalp arms to hit their stops and the bandit to move
  budget to 5m and intraday. That is the program working, not failing.
- Wine box capacity for 3-4 terminals is unknown (SRE).
- If E1 slips, `icm-scalp` starts with S0 only (the existing scheduled-probe path already places M1 orders) and the
  intraday and swing hosts start on the 15m clock; no 1m/5m strategy arm runs before E1.
