# Arms expansion 2026-10-26: triple the strategy families on demo (wave plan, capacity, trial accounting)

Research director, 2026-10-26 19:40 UTC. Plan only: no config, code or registry change is made by this file. It builds
on the time-structure pre-registration (`prereg-2026-10-26-time-structure.md`, branch
`worktree-agent-ac505f7827fd93a94`, commit a36c5f85: arms F1A/F1C/G1A/G1C/WFRI) without editing it.

## 0. Owner directives this plan answers (2026-10-26, verbatim)

1. "tripple the numbers" (after being told: 33 arm slots, 11 per account, 18 ideas + 4 random controls).
2. "limits should be dynamic for risk and type of strategy".
3. "a strategy is one strategy that can be applied to different time frames, so break and close strategy to next box
   target for different time frames is still one strategy but train and tested on multiple timeframes".
4. "yes the target should also be different based on the timeframe strategy lot size profit loss etc."
5. "there should be 30 different strategies running in the next 12 hours", then 6 hours (by **2026-10-27 01:35 UTC**),
   and "don't drop any quality or requirements, be wise".

**What is counted.** A *strategy family* is one rule; its timeframes, sessions and brokers are declared **variants
(strata)** of that one strategy. Random controls are not strategies. Today **6 families trade on demo**:
`box_break_close`, `session_vwap_fade`, `opening_range_break`, `sweep_absorb`, `ema_pullback`, `tsmom` (the 18 "ideas"
were these 6 across timeframes, parameters and two brokers). The target is **30 families**.

**The honest answer, up front.**
- **20 families can be live by 01:35 UTC at the full bar** (wave 1): the 6 running plus **14** whose rule code already
  exists as a tested specialist in `goldbot/specialists/`, wired through **one** thin adapter (section 3). Every gate is
  kept: tests first, frozen arm hashes, pre-registration rows (section 4 is that fragment), reviews, CI.
- **The other 10 need new rule code** (the time-structure rules, bands, Donchian, order blocks, key-level retest,
  RSI(2), clock-anchor reversal). They are wave 2, live by **2026-10-28 18:00 UTC**, with the same bar.
- Writing ten new rules tonight to hit 30 by 01:35 would skip either tests-first or the rule review. The owner said not
  to drop quality, so I will not do that.

## 1. The box-break target discrepancy (directive 3/4)

- **Checked:** `goldbot/specialists/box_break_close.py` sets `TARGET_ATR = 3.0` = 2R (fixed, line 49). Every arm built
  on it uses a fixed `target_r` 2.0: S4, I2A, I2B, MB15, MB30, MB1H, W2 and the twins VI2A, VI2B, VMB15, VMB30, VMB1H,
  VW2. `goldbot/features/box_course.py` has `next_box` (line 183), but only the research families use it, and only as a
  ≥ 2R existence floor. Their label is also 2R (the "2R proxy for next-box target" fallback, `box-mfi-families` section
  3).
- **Consequence:** the owner's rule, *target the next box*, has **never** run live or in a registry trial. H-50 (#89,
  STOP) read the 2R proxy. Its verdict covers "box break-close, 15m, fixed 2R", not the owner's exit.
- **Remedy:** the exits-researcher is designing adaptive targets (`exits-adaptive-targets-2026-10-26.md`: next box or
  level, ATR by timeframe, R floors, 0.01-lot limits on partials, P&L-state rules) and will propose the next-box family
  arms. Frozen arms are not edited. The new arms take new ids.
  - **Placeholder ids reserved here:** `BNB5`, `BNB15`, `BNB30`, `BNB1H`, `BNB4H` (box break-close, next-box target),
    one family with 5 timeframe strata.
  - **Slots:** wave 2b (section 6). They are not counted in the 30 families: same family as `box_break_close`, new exit.

## 2. Pre-registration form for every family (directive 3)

### 2.1 One strategy, a pooled read

1. **Declared now:** each family is registered once, with all its timeframe, session and broker strata named before any
   fill. A stratum added later is a new registration under a new id.
2. **Primary read:** pooled net R per trade. Each stratum's mean is weighted equally (not by fill count, so the fastest
   timeframe cannot dominate). The SE combines the strata's day-clustered variances (CR1).
3. **Consistency check** (pre-specified, part of "continue"):
   - the mean net R is > 0 in at least ceil(2m/3) of the m strata that reach ≥ 30 fills;
   - no such stratum has a mean ≤ −0.10 R with t ≤ −2.
4. **No best-timeframe pick.** A family continues or stops as a whole. A per-stratum result is reported, never
   promoted on its own.
5. **Exploration rows are never promotion evidence.** A read can say at most *continue*. Promotion still needs gate 1
   (a model-filtered walk-forward, DSR at the day's K) and the holdout once (`prereg-2026-10-25-high-volume.md`,
   H-61).
6. **Read timing:** the H-61 rule, one read at the 300th admissible family fill, P(mean > 0) > 1 − 0.05/m.
   **Per-family stop:** −30 R within 300 fills.
7. **Control:** the family's paired random control on the same account and timeframe group. The read reports
   family minus control net R, paired by day.

### 2.2 How the quant reviewer should count K

- **In the registry, a family is one hypothesis.** Its strata are declared variants.
- **For DSR and the gate-1 bar,** charge each family **m_eff**, the Li-Ji effective number of tests: from the
  eigenvalues λ of the correlation matrix of the strata's daily net-R series,
  m_eff = Σ [I(λ ≥ 1) + (λ − floor(λ))].
  - Measure it on the discovery half 2010-2017 history run of the arms (`run_arm_backtest`), never on forward fills.
  - Bounds: 1 ≤ m_eff ≤ m.
  - Until it is measured, charge **m** (conservative).
- **Broker twins** are strata, not new hypotheses. Their correlation with the primary is near 1 (same rule, same
  market), so they add almost 0 to m_eff.
- **Controls charge 0 hypotheses.** They are nulls. They still take a registration row.
- **Q4 slots.** The precedent (the 11 Vantage twins, and the time-structure prereg) charges one 2026Q4 slot per arm
  id. Until the quant reviewer rules on family counting, this plan uses the precedent, the higher number (section 5),
  and also shows the family count.

### 2.3 Training (directive 3, "train and tested on multiple timeframes")

- **One model per family on pooled multi-timeframe data,** not one per timeframe:
  - features in timeframe-free units: ATR of the decision timeframe, R, hours rather than bars, session clock;
  - `timeframe` and `broker` as categorical columns;
  - the 40-feature cap per model.
- **Leakage across timeframes:** a 15m and a 1h label of one family can overlap in clock time. Purging and embargo use
  clock time (`available_utc`), not bar index, across all strata.
- **Separate models only if they beat the pooled one:** separate per-timeframe models replace it only when, in purged
  CV, they improve the pooled Brier by ≥ 1% with a 95% CI above 0. The rule is fixed now.
- **Owner:** the ml-researcher writes this into the H-62 v2 spec. H-62 today is per (broker, style), so this is an
  amendment.

## 3. Wave 1: 14 new families from existing, tested code (live target 2026-10-27 01:35 UTC)

**Adapter.** One new rule `specialist` (module `goldbot/research/arm_rules_specialist.py`). It calls
`SPECIALISTS[name](timeframe=tf, **config).candidates_in_context(...)` and takes the stop from the specialist's own
`label_spec` and `barrier_atr`, exactly as `tsmom_rule` already does (`arm_rules.py` line 330). The rule logic is
the specialist's, at the commit the arm hash is frozen on.

**Deviations, declared now (0.01 lot, fixed brackets):**
- `breakout` and `trend` have a partial + trail exit policy in research. At 0.01 lot a position cannot be split, so the
  arms run the specialists' **barrier form** (`label_spec` stop and target, time exit).
- The box-course families run their 2R proxy.

The same function runs in the history matrix and live, so history and live use the same rule.

**Static wave-1 limits (inside every cap).** Dynamic sleeves come with ADR 0047 (section 7).

| Limit | Value |
|---|---|
| Size | 0.01 lot |
| Per-trade cap, 15m arms | £17, the existing intraday arm value |
| Per-trade cap, 1h arms | £30, under ADR 0043's £45 intraday cap; MB1H's £17 skips most 1h stops, see section 5 |
| `max_open` per arm | 1 |
| Controls | fixed share 0.25 of their pot, never bandit-allocated |
| Account caps | unchanged |

| # | Family (hypothesis; prior verdict) | Code path (tested) | Timeframes: live now / declared strata | Account | Pot | Per-trade cap | Effort | Lane |
|---|---|---|---|---|---|---|---|---|
| 7 | `box_break` H-37 (screen only) | `specialists/box_course.py` BoxBreakSpecialist, rung L0 | 15m / 15m | icm-demo | intraday | £17 | S | L2 |
| 8 | `box_rip` H-38 | box_course BoxRipSpecialist L0 | 1h / 1h (4h boxes inside the rung) | vantage-demo | intraday | £30 | S | L2 |
| 9 | `box_retest` H-39 | box_course BoxRetestSpecialist L0 | 1h / 1h | vantage-demo | intraday | £30 | S | L2 |
| 10 | `fakey` H-44 | box_course FakeySpecialist L0 | 1h / 1h | vantage-demo | intraday | £30 | S | L2 |
| 11 | `trend_level_signal` H-45 | box_course TrendLevelSignalSpecialist L0 | 1h / 1h | vantage-demo | intraday | £30 | S | L2 |
| 12 | `smc_tvr1` CHoCH + FVG (H-TVR1; withdrawn on count, never read on outcome) | `specialists/smc_structure.py` rule tvr1 | 15m / 15m | icm-demo | intraday | £17 | S | L3 |
| 13 | `smc_tvr2` BOS continuation (H-TVR2; withdrawn on count) | smc_structure rule tvr2 | 15m / 15m | vantage-demo | intraday | £17 | S | L3 |
| 14 | `eq_liquidity_run` H-49 (stage 1: R-13 base rate) | `specialists/eq_liquidity_run.py` | 15m / 15m | icm-demo | intraday | £17 | S | L3 |
| 15 | `session_open` (R-02) | `specialists/session_open.py` | 15m / 15m, London and NY sessions as strata | icm-demo | intraday | £17 | S | L4 |
| 16 | `intraday_momentum` Gao (R-05) | `specialists/intraday_momentum.py` | 15m / NY and London as strata | icm-demo | intraday | £17 | S | L4 |
| 17 | `asia_drift` H-04 (#44 stopped on cost) | `specialists/asia_drift.py` | 1h / 1h | icm-demo | intraday | £30 | S | L4 |
| 18 | `breakout` compressed range (R-04) | `specialists/breakout.py`, barrier form | 1h / 1h + 15m (declared `timeframes`) | vantage-demo | intraday | £30 / £17 | S | L5 |
| 19 | `trend` EMA50 pullback + ADX (R-03) | `specialists/trend.py`, barrier form | 1h / 1h | vantage-demo | intraday | £30 | S | L5 |
| 20 | `mean_reversion` BB + RSI (R-01) | `specialists/mean_reversion.py` | 15m / 15m + 1h (declared `timeframes`) | icm-demo | intraday | £17 / £30 | S | L5 |
| ctl | random control, 1h | existing `random_bracket` (`minutes` [0], local window as PR #203, stop 1.5 ATR1h, 2R, 48 bars) | 1h | icm-demo (C1H) and vantage-demo (VC1H) | intraday | £30 | S | L6 |

**Excluded:** `tv_event` (shadow-only by design, `requires_tv`).

**Why the retired families are here.** An R-xx verdict reads "this version, tested this way, did not beat random after
the research-prior cost". The broker's measured cost is 5-7 times lower (quant audit 2026-10-26). These rows gather
live, signal-time cost and fill evidence. They do not re-open the verdicts: no promotion and no registry trial without a
new pre-registration.

**Wave-1 slots:**

| Host | Before | New | After |
|---|---|---|---|
| icm-demo | 11 | 7 families + 2 variants (intraday_momentum London, mean_reversion 1h) + 1 control | 21 |
| vantage-demo | 11 | 7 families + 1 variant (breakout 15m) + 1 control | 20 |
| icm-probe | 11 | 0 (F1A/F1C arrive with wave 2) | 11 |

That is 19 new arm ids and 52 slots in total.

## 4. Pre-registration rows for wave 1 (fixed before any fill)

- **Rule:** each family is the specialist's own candidates at the commit the arm hash is frozen on. The only settings
  are the rung, preset or session listed in section 3.
- **Common conventions:** `arms-2026-10-25.md` section 0.
  - Decision on the close of the arm's bar. Market entry right after it.
  - Stop and target are fixed prices, searched on 1m bars, stop first.
  - Tier-1 blackout. No entry 20:45-22:15 UTC or on Friday after 19:00 UTC.
  - Local session clocks where a session is named, through PR #203's local window.
  - A candidate outside its £ cap or stop band is skipped and logged with its propensity.
- **Read:** section 2.1, per family. Per-family control: C1H on icm-demo (I0 for the 15m families) and VC1H on
  vantage-demo (VI0 for 15m).
- **Costs reported on every read:**
  - the worse of $0.23/oz plus $0.70 on each stop exit, and the family's own signal-time cost;
  - the research prior ($1.07/oz).
- **Holdout** 2025-10-01..2026-09-30: never read. The history run covers 2010-01..2025-09 only, and it stays
  exploration evidence.

## 5. Capacity (wave 1 fits inside the caps; no new account tonight)

| Constraint | Today | Wave 1 | Binding? |
|---|---|---|---|
| Account daily loss (RiskGate, `daily_loss_limit_abs`) | £220 / £220 / £200 (2% of about £11k / £11k / £10k) | **unchanged** | backstop |
| Weekly | demo ceiling 0.06 in `config/accounts.yaml` (ADR 0042/0043); settings default 0.05 | **unchanged** | backstop; see the note below the table |
| Exploration budget (realised + open at stops + new) | 0.9 × 220 = £198 (icm-demo: intraday £108, swing £90); vantage £98 / £82 | **unchanged** | binds first: more arms share the same £ |
| Host positions (`max_positions` 18; exploration `max_open_total` 17; margin and notional cap: about 19-21 on £11k, `exploration.global_cap`) | icm-demo and vantage-demo use only **6 of 17** (intraday pot 5, swing 1) | intraday pot `max_open` **5 -> 9** on icm-demo and vantage-demo (total 10 of 17) | no. The £108 / £98 pot binds before 9 open positions at £17-30 each |
| Brain CPU (2 OCPU A1) | engine ~360 MB per account; arms recompute candidates on the engine's window | 15 more rule calls per 15m close per host; box_course and smc replicas are the heaviest | **unmeasured: gate.** Every arm's call must finish inside its staleness (60 s for 15m+); budget ≤ 10 s per host per 15m close. Lane L7 measures before deploy |
| Bridge | PER_PASS orders a pass, FIFO with rotation; a 15m-1h family fires 1-6 times a day | about +40 orders a day per host | no |
| Margin | 0.01 lot ≈ £3,120 notional, about £156 margin at 1:20; 10 open ≈ £1.6k, margin level ≫ 300% | — | no |

**Weekly cap note.** The demo ceiling in config is 6%, and the owner's message says 5%. Lowering it to 5% would be a
tightening, so it is allowed. That is a CFO call. No change is made by this plan. Wave 1 is budgeted so that the
expected weekly drift stays below 5%: zero-edge drift at the measured cost is about £8 a day per intraday host, about
0.4% a week.

**Verdict.**
- **Safety:** ~100 arm slots on 3 hosts is safe by construction. The caps bind and are never loosened.
- **Learning speed: it does not work on 3 hosts.** More arms share the same £198 and 17 positions, so each arm's time
  to its 300-fill read stretches 2-3 times.
- **Swing is starved:** 1 slot per host at £40. W1B and VW1B (1d stop about $131, about £99) can **never** fill
  live today.
- **MB1H and VMB1H mostly skip:** at £17, a 1.5 ATR1h stop of about $26.6 is about £20.

**More hosts (wave 3, no paid spend, no owner sign-up):**

| Host | What | Cost | SRE work |
|---|---|---|---|
| **H4 `vantage-swing`** | a second Vantage demo login, opened from the terminal's own "Open an Account" dialog (Claude via VNC, as the MT5 logins are). GBP £10,000. Swing 4h/1d families, `max_open` 3 inside £180 of open risk, per-trade cap up to £100 (the RiskGate's 1%, never above). This fixes W1B-type 1d stops | £0: a second terminal on the existing paid 2 GB `goldbot-mt5-vantage` box | 24 h RAM baseline of the box. Own Wine prefix + `MemoryMax`/`OOMScoreAdjust` on the new units (icm-probe plan Option A). Port and token keys `mt5-*-vantage-swing` via the keyring prompts. Engine unit `goldbot-engine@vantage-swing`. Proceed only if the projected peak is ≤ 1.7 GB of 2 GB |
| H5 (optional) | a second IC Markets demo | a new VM: paid, or a free tier that needs an owner sign-up | **not proposed**: the 1 GB Oracle micros cannot host a second terminal (icm-probe plan section 1: they already swap) |

**SRE tonight:** only L7, the CPU profile on the brain. No account opening tonight: night operations during a deploy
add risk and give no wave-1 benefit.

## 6. Waves

| Wave | When (UTC) | What | Families live | Gate |
|---|---|---|---|---|
| 1 | build 19:45-23:30; reviews in parallel; deploy by 01:35 on 10-27 | adapter + 14 families + 2 controls; pot `max_open` 5 -> 9; time-structure arms F1A/F1C/G1A/G1C/WFRI **only if** their rule code is merged (it is new code, so wave 2 by default) | **20** | tests first; quant + trading-safety + code + security reviews; CI green; L7 CPU ≤ 10 s; the owner's chat approval of `goldbot-deploy` (classifier rule) |
| 2 | 10-27 to 10-28 18:00 | 10 families with new rule code: F1 (Hurst band), G1 (key-level break), WFRI (Friday), Keltner/BB squeeze release, Donchian 4h/1d, 30-day-extreme first touch (C-1), order-block retest (E-1, exploration row), key-level retest/rejection (G-6, exploration row), RSI(2) trend pullback, clock-anchor reversal (Tokyo open / COMEX settle, forward-only). Controls: 5m on icm-probe, 15m on icm-probe | **30** | as wave 1 |
| 2b | when the exits-researcher's spec is merged | next-box box-break family `BNB5/15/30/1H/4H` (placeholder, section 1) and the other adaptive-target variants | 30 (same family as box_break_close) | exits spec + quant |
| 3 | 10-29 to 10-30 | H4 `vantage-swing`; swing strata of the families (4h/1d), controls XW4/XW1D; Vantage and ICM twins of wave-1 families to read broker cost | 30, about 85-95 slots | SRE RAM gate; trading-safety on the new account |
| held | after reconciliation | D-1 Asia trend persistence, A-1 failed Asia-range fade, D-2 CMF follow: the two builds disagree (`time-structure-2026-10-26.md` section 5), so there is no frozen rule to arm | — | reconciliation note |

**Expected volume.** Wave 1 adds about 15-30 fills a day across the two hosts: 15m families 1-6 a day each, 1h
families 0.5-2 a day. Wave 2 adds about 30-60 a day, mostly F1 and the 5m rules. These are estimates, not
measurements.

## 7. Dynamic limits by strategy type (directive 2; policy numbers for the ADR 0047 architect)

**Backstop, never changed:**
- the RiskGate;
- 1% per trade;
- the account daily loss (2%) and the weekly cap;
- the £198 / £180 exploration budget;
- host positions.

The dynamic layer can only **tighten** below the base values in the table. Factors rise only at the 00:00 UTC roll and
fall at once (the ADR 0031 pattern).

**At 0.01 lot there is no size to scale.** A tighter limit therefore acts through:
- the per-arm daily £ share;
- `max_open`;
- skipping candidates whose stop exceeds the cap.

Shrinking the cap selects low-volatility trades and biases the sample. So drawdown and cost act on share and
`max_open`; only the ATR regime touches the cap. Every skip is journalled with its propensity.

| Type | Base per-trade cap | Base `max_open` per host | Base daily share of the host's exploration £ | ATR regime r = ATR14 / its 60-day median | Arm drawdown (cumulative net R from peak) | Signal-time cost per R (95% upper bound at ≥ 30 fills, else $0.23 + $0.70 per stop exit) |
|---|---|---|---|---|---|---|
| Scalp 1m/5m | £6.50 | 12 | 70% on a scalp host | r 1.3-2.0: `max_open` × 0.5; r > 2.5: `max_open` 2 | −10 R: share × 0.5; −20 R: × 0.25 and `max_open` 1; −30 R: H-61 stop | ≤ 0.10: 1.0; 0.10-0.20: × 0.5; > 0.20: × 0.25; a candidate > 0.25 is skipped (`cost_per_r`) |
| Intraday 15m-1h | £17 (15m), £30 (1h); ≤ £45 | 9 | 55% on a mixed host | as scalp; r > 2.5: `max_open` 3 | −6 R: × 0.5; −12 R: × 0.25 and `max_open` 1; H-61 stop | as scalp (thresholds 0.08 / 0.15 / 0.20) |
| Swing 4h-1d | £40 on shared hosts; up to £100 (1%) on H4 | 1 shared; 3 on H4 | 45% on a mixed host; 100% on H4 | r > 2.0: no new entry while one is open | −4 R: × 0.5; −8 R: × 0.25; H-61 stop | rarely binds (cost per R < 0.02) |
| Random controls | as the type they control | 1 per control | fixed: scalp 25%, intraday 25-30%, swing 1 entry in 3 | **same as their type**, so they stay the null for the same conditions | **none** (results-blind, ADR 0043) | **none** |

- **Restore.** One step back up once the arm recovers half the step from its trough **and** has taken ≥ 20 (scalp),
  10 (intraday) or 5 (swing) fills since the cut. Never above base.
- **Combining factors:** the minimum of the three, not the product. The factors are correlated, and one named binding
  reason ("limited by: drawdown") is auditable.
- **Bias warning for the evaluator:** limits conditioned on past outcomes change which trades are taken. Reads use the
  logged propensities (inverse-propensity weights) and report the unweighted mean beside them.

## 8. Trial accounting (Q4 override)

| | Precedent count (one slot per arm id) | Family count (pending the quant ruling) |
|---|---|---|
| 2026Q4 used today | 69 / 69 | 69 / 69 |
| Time-structure arms (their prereg) | 74 | 72 (F1, G1, WFRI = 3 families; controls 0) |
| Wave 1 (14 families, 3 declared variants, 2 controls = 19 ids) | **74 -> 93** | 72 -> 86 |
| Wave 2 (7 new families beyond F1/G1/WFRI, about 4 extra strata, 2 controls) | 93 -> 106 | 86 -> 93 |
| Wave 3 (H4 swing strata + controls + twins, about 15 ids) | 106 -> about 121 | about 93 (strata only) |
| Registry K (`n_trials_effective`) | 145 -> 150 -> 169 -> 182 -> about 197 | 145 -> 148 -> 162 -> 169 |
| K with the programme's 161 charged looks | about 358 | about 330 |
| Gate-1 bar: model-filtered t = E[max Z_K] + 1.645 | 4.30 at K 145 -> **4.59** at 358 | **4.56** at 330 |

- **Formula:** E[max Z_K] = (1 − γ) Φ⁻¹(1 − 1/K) + γ Φ⁻¹(1 − 1/(K e)), γ = 0.5772.
- **Effect:** the bar rises by about 0.3 in t. Tripling the families costs little in bar terms, because K enters
  through about sqrt(2 ln K).

**Override requested:** 2026Q4 **69 -> 93 for wave 1** (74 of that is the time-structure arms), then 106 at wave 2.
- Basis: the owner directives of 2026-10-26 (section 0) and the precedent of the Vantage twins and the time-structure
  arms.
- Decision: program-director (PD + PO; research director recommends). Reported to the owner.
- Conditions: every row is an exploration row, never promotion evidence. No arm can graduate without gate 1 at the
  K of that day and the holdout once.

**Quant review, in parallel with the build (not before it).** It must sign off before the first fill:
1. family counting and m_eff (section 2.2);
2. the pooled read and the consistency rule (2.1);
3. the barrier-form deviations of `breakout` and `trend`;
4. IPW reads under dynamic limits (7);
5. the cross-timeframe purge (2.3).

## 9. What I would stop

- **Counting timeframes as strategies.**
- **Reading any box verdict as a verdict on the owner's rule** until the next-box exit has run.
- **Fixed per-arm £17 caps on 1h rules.** They make MB1H a skip generator.
- **One-slot swing on shared hosts as a learning plan.** H4 replaces it.

## 10. Delegation plan (wave 1: build lanes, each in its own worktree, tests first, disjoint files)

| Lane | Role | Scope (files) | Acceptance |
|---|---|---|---|
| L1 | implementer | `goldbot/research/arm_rules_specialist.py` (new); register `specialist` in `arm_rules.RULE_FUNCS` and `arms.RULES` (only L1 touches these two files); `tests/test_arm_rules_specialist.py` | parity with each specialist's `candidates_in_context` and `label_spec` on a fixture; prefix invariance (appending bars never changes a past candidate); unknown specialist or blocked → refuses; mypy and ruff clean |
| L2 | implementer | `tests/test_arms_box_course.py` (new) against the L1 interface: box_break, box_rip, box_retest, fakey, trend_level_signal | per family: parity, prefix invariance, warm-up fits the engine's live window (~40 trading days of 1m), structural stop → £ cap skip logged |
| L3 | implementer | `tests/test_arms_smc.py` (new): smc tvr1, tvr2, eq_liquidity_run | as L2 |
| L4 | implementer | `tests/test_arms_session.py` (new): session_open, intraday_momentum (NY, London), asia_drift | as L2, plus DST: candidates on local clocks across 2026-10-25 (UK) and 2026-11-01 (US) |
| L5 | implementer | `tests/test_arms_classic.py` (new): breakout (1h, 15m), trend, mean_reversion (15m, 1h) | as L2, plus the barrier form equals the specialist's `label_spec` |
| L6 | implementer (after L1) | `config/exploration_arms.yaml` (new rows, hashes via `arm_hash`), `config/accounts.yaml` (registrations; intraday pot `max_open` 5 -> 9 on icm-demo and vantage-demo; per-arm caps £17/£30; C1H/VC1H) | load test green; no limit outside section 5 changed; `login: null` kept; trading-safety review |
| L7 | sre (read-only + new script) | `scripts/profile_arm_rules.py` (new); run on the brain over the live window | per-host time per 15m close for all wave-1 arms ≤ 10 s; RAM delta reported; go/no-go to the PD |
| L8 | strategy-researcher | wave-2 rule specs into `arms-2026-10-25.md` by amendment (F1/G1/WFRI pointer to their prereg; squeeze, Donchian, C-1, OB retest, key-level retest, RSI(2), clock anchors) | rule text frozen before any code; quant read |

**Reviews, in parallel:**
- quant-reviewer: section 8 list;
- trading-safety: L1 + L6 (order path config);
- code-reviewer: all lanes;
- security before the push;
- evaluator: the pool and read rules (2.1), read-only.

**Release:** one PR, merged when CI is green, then deploy (owner's chat approval for `goldbot-deploy`). Then the
history matrix runs for the wave-1 arms, which is exploration only.
