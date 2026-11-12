# Pre-registration 2026-11-12: H-90, gold signal arms in the London session only

Status: **registered before any confirming data is seen** (strategy researcher, 2026-11-12). No trial runs in this
change. Nothing in the engine, RiskGate, caps, sizing or `config/` changes. Family `session:2026-11`, unit H-90,
**K +3** (section 8).

## 0. Disclosure: where the idea came from

- Source: `docs/research/profitability-2026-11-12.md` section 1 "Other cuts" (research director, live demo record
  2026-10-26..2026-11-12, 14 trading days). The cut was made after the data was seen, and it was one of at least
  nine cuts (3 sessions, 2 sides, 4 timeframes). The doc says so itself: "a hypothesis, not evidence".
- Discovery numbers:
  - London (07-12 UTC), signal arms: n 674, +0.132 R per trade, day-clustered t 2.9, +£288.
  - Their random controls: -0.078 R.
  - Asia (00-07 UTC): signal arms -£228, controls -£153.
- None of the discovery fills may count toward the confirmation. They are already spent.
- The discovery pool is mostly gone. It held arms that have since retired (S5A/S5B, Z4D, Z7V and others) and the
  multi-instrument LB arms. LM-1 forbids pooling the LB arms with XAUUSD (`learning-mi-2026-11-02.md`). The forward
  test below is on the gold signal arms still running. This is a claim about the **session**, not about one rule.
- **Prior: low.**
  - H-82 replayed the live entry code of these families on 2010-2017. It bounded gross at ±0.011 R per arm, all
    hours pooled.
  - H-83 found that random entries pay in **0 of 24** session cells, London included.
  - A London net of +0.13 R would need the other sessions to be about -0.1 R gross on the same rules.
  - In the discovery window gold fell, and shorts carried the signal arms (+£206 shorts against -£62 longs).
  - The likeliest reading of the discovery is noise plus drift. This card is built to find out cheaply.

## 1. Hypothesis and economic reason

**H-90:** for the frozen population of gold signal arms (section 2), net R per trade on entries in the London morning
beats the arm's matched random control in the same session and on the same side. It also beats the same arms' excess
outside London. Both hold net of broker-measured costs.

Mechanism (weak, stated so it can fail):

1. **Liquidity and cost.**
   - London is the deepest spot-gold venue: the LBMA OTC market, and the LBMA Gold Price auction at 10:30 London.
   - Our measured ICM costs are lowest in London (`config/cost_of_record.yaml`, `cor-2026-10-31.2`):
     - London: round trip $0.164 per oz (ci90 0.161-0.191), entry slip -$0.031.
     - Asia: round trip $0.204 per oz (ci90 0.172-0.272), entry slip +$0.015.
   - At $3-4.50 fixed-dollar scalp stops, the cost saved is about 0.01 R. That is far below the discovery gap, so
     cost alone cannot explain it.
2. **Price discovery after the Asian range.**
   - The London open resolves the overnight range. Breakout and continuation rules (Z3 round break, Z5 extreme break,
     Z9 1h reclaim, Z10 three-horizon, GS1 session range) should fire on real information there more often than in
     thin Asian hours.
   - We have no peer-reviewed evidence of a London-session *directional* edge in gold.
     - Caminschi & Heaney 2014 (*J. Futures Markets* 34(11); about 2008-2013; GC and GLD; gross): fix leakage before
       the 2015 reform. It is dead.
     - Cai, Cheung & Wong 2001 (*JFM*; COMEX 1994-97): volatility is driven by US releases.
     - The "Asia up / West down" pattern comes only from a thesis, a working paper and 2026 press (gross; the press
       falls inside our holdout).
   - See `state-of-the-art.md` B2-B3. The rationale here is signal *quality* by session, not drift.

## 2. Exact arms (frozen at this commit; no arm may join without an amendment and a K row)

All are XAUUSD on ICM. Each arm is read against its own dedicated control: same timeframe, stop, target and hold
(`config/exploration_arms.yaml`).

| arm | rule | tf | account | role | matched control |
|---|---|---|---|---|---|
| Z1P | tick_surge | 1m | icm-probe | learning | LCZ1P |
| Z2P | run_fade | 1m | icm-probe | learning | LCZ2P |
| Z5P | extreme_break | 5m | icm-probe | learning | LCZ5P |
| Z3D | round_break | 5m | icm-demo | learning | LCZ3D |
| Z9D | htf_reclaim | 5m | icm-demo | learning | LCZ9D |
| Z10D | three_horizon | 5m | icm-demo | learning | LCZ10D |
| GS1 | session_range_break | 15m | icm-demo | owner_experiment (to 11-27) | GS1C |

Excluded from the population:

- Roles control and cost (F1A, G1A): cost reads, and proposed to retire.
- S1A, S1B, Z3V, Z5V: strategy rows without a qualification. They send no entry.
- Every LB-/I0-/S0-<instrument> row (LM-1).
- Every retired row.

An arm that retires under its own rules (300 fills, loss cap, `until`, or the A2 read due 11-13) leaves the
population on its retirement day. Its fills up to then count. If fewer than 3 arms remain active, the read is taken
at once with what exists.

## 3. Session definition (DST-aware)

- **London** means the decision time, which is the signal bar's close and not the exit, falls in
  **[07:00, 12:00) Europe/London local time**. That is 07:00-12:00 UTC under GMT (late Oct to late Mar) and
  06:00-11:00 UTC under BST. Boundaries are converted per date with `zoneinfo`.
- The whole forward window falls in GMT, so the definition reproduces the discovery's "07-12 UTC" exactly. For history
  it follows London's clock.
- **Non-London** is every other decision time the arm can trade. The 02:00-07:00 UTC block is already refused by
  `window_0207`, so non-London is mostly 12:00-02:00 UTC. The read reports the split New York / late Asia / early
  Asia.
- The engine's `DEFAULT_SESSIONS["london"]` (08:00 Europe/London to 08:30 America/New_York) is a different window. It
  is reported once as a sensitivity row and is **not** the test. The budget segments of
  `prereg-amendment-2026-11-09-session-roll-forward.md` use `DEFAULT_SESSIONS`, so a 07:00-08:00 London fill is
  admitted from the "asia" segment. This is disclosed, not corrected.

## 4. Costs

- **Forward (primary).**
  - R = broker-net `pnl` / the trade's registered stop in GBP. `pnl` = profit + commission + fee + swap
    (`goldbot/execution/deals.py`); slippage is in the fill prices. These are measured costs, nothing modelled.
  - Fills in `evidence_exclusions` are removed.
- **History (leg H).**
  - Charged at `cor-2026-10-31.2`, ICM cell of the entry's `DEFAULT_SESSIONS` label (the label the table was built
    on), ADR 0064 charge = max(frozen 0.23, round-trip upper bound).
  - Converted to bps of the bar's own price at the $4,000 reference of `settings.yaml` (0.204 $ RT -> 0.51 bp):

    | session | round trip | overshoot |
    |---|---|---|
    | London | $0.230 = **0.575 bp** | $0.120 = 0.30 bp |
    | New York | $0.230 = 0.575 bp | $0.196 = 0.49 bp |
    | Asia | $0.272 = 0.68 bp | $0.136 = 0.34 bp |

  - Overshoot is charged on stop exits only.
  - No swap: the holds are shorter than the rollover, and a fill held across 22:00 UTC pays the cost-of-record swap.
  - If the cost table version moves before the read, the read uses the version pinned here and reports the new one
    beside it.

## 5. Statistic and matched random control

- Per signal fill i of arm a, session s, side d:
  `x_i = R_i - mean(R of a's control fills in session s, side d, same read window)`.
- The control cell is the dedicated control above, which has random sides, the same bracket and the same window.
- **Fallback:** if a control cell has fewer than 30 fills, use the both-sides mean of session s. The read reports the
  share of fills that used the fallback. Above 25 %, the read is labelled "drift-unadjusted" and cannot PASS.
- Side matching takes the gold drift out of the comparison. GS1 earned less than its own twin GS1C (`profitability`
  section 2), so this matters.
- **E_L** = mean of x_i over London fills. **E_N** = the same over non-London fills. **DiD** = E_L - E_N.
- All t statistics are clustered by opportunity day (22:00 UTC boundary) and one-sided. The decision t uses a
  two-sample, day-clustered SE (var_s/n_s + var_c/n_c). The day-block bootstrap (10,000 resamples, seed 20261112)
  resamples signal and control days together and recomputes the control means in every resample.
- Leg H uses the same x_i. Its control per signal entry is a same-side entry at a uniformly drawn bar within ±60 min,
  inside the same session, with the same bracket. It is seeded by hash(arm id, decision time), as in the H-82 method.

## 6. Legs, fill budget, kill date

| leg | data | role | n | cost in fills |
|---|---|---|---|---|
| **H-learn** | replay of Z1, Z2, Z3, Z5, Z9, Z10 entry code (H-82 harness) on 2010-01..2017-12. GS1 joins only if its rule has a history implementation at the frozen hash. | **kill-only** (these years were seen in aggregate by H-82, never by session to our records) | tens of thousands of entries a family | 0 |
| **H-seen** | the same on 2018-01..2025-09 | corroboration only (CONFIRM-SEEN since 2026-11-02). Reported with and without 2025. | as above | 0 |
| **F** (decides) | demo fills sent from 2026-11-13 00:00 UTC (or the day after the quant sign-off and the registry row both exist, if later; never earlier) to the read | confirmatory | read at **400 London signal fills** or on **2026-12-17**, whichever comes first | 0 extra. The arms are already running inside their learning caps and loss caps. |

- Any change to `exploration.daily` segments or caps for these 7 arms or their controls before the read is an
  amendment.
- **Not used:**
  - TEST-OLD (2006-02..2009; intraday 2008-09 only). It is reserved for arms that pass confirm.
  - The holdout 2025-10-01..2026-09-30. It is sealed: no history leg reads a bar on or after 2025-10-01.
- **Expected rate:** about 11 London signal fills a day. The population made about 400 fills in the discovery's 14
  days, and London is about 40 % of them. By 12-17 that is about 260.
- **Interim futility look at 150 London fills (F only):** stop for futility if the one-sided 90 % upper bound of E_L
  is below +0.05 R. Futility-only, so no type I inflation.
- **Kill date:** 2026-12-17. The population's `until` ends that day, so F cannot run past it without an amendment.
- **Power (honest), assuming:**
  - per-fill SD 1.12 R (from the discovery CI);
  - day-cluster design effect 1.06 on the SE;
  - one control fill per signal fill.

  | n (London fills) | E_L detectable at 80 % power (t 2) | power if the full discovery gap holds (+0.21 R) | power if half holds (+0.10 R) |
  |---|---|---|---|
  | 260 | 0.30 R | 51 % | 15 % |
  | 400 | 0.24 R | 69 % | 21 % |

  **The expected outcome is LEAD or FAIL, not PASS.** Leg H carries most of the information.

## 7. Reading rule (decided now)

Evaluated in this order. The evaluator applies it, and the research director signs it.

1. **H kill (run first, after the quant sign-off).** Pooled over the population, on H-learn: if **E_L <= 0 or DiD <=
   0** (point estimates), or **fewer than 5 of 8 years** have DiD > 0, H-90 is **retired**. F is then read only as
   the learning arms' own reads; no session claim is made from it. H-seen is reported. If H-seen also has E_L <= 0,
   the retirement note says so. H-seen alone never retires the idea and never supports it.
2. **F PASS** requires every item below:
   - E_L day-clustered **t >= 2.0** (one-sided p of about 0.023).
   - The mean London **net R > 0**.
   - **DiD > 0 with t >= 1.645**.
   - E_L > 0 when any single arm is left out (leave-one-arm-out).
   - E_L > 0 on both the long and the short fills.
   - Not "drift-unadjusted".
   - Not killed at step 1.
3. **F LEAD:** E_L > 0 with t in [1.0, 2.0), and DiD > 0. Either continue with an amendment that names new or
   extended arms (K +1 per window), or stop at the research director's call. LEAD is never an allocation change.
4. **F FAIL:** anything else (E_L <= 0, DiD <= 0 or futility). H-90 is **retired**. "London-only for signal arms" is
   not re-tested on these or neighbouring rules without a new mechanism or data source.

**Why t 2.0 and not the deflated bar.**

- F reads data no one has seen, so the selection among the discovery cuts does not raise its type I error.
- One pre-registered test at one-sided 0.025 is honest for a **demo volume** decision.
- The K ledger still counts it (section 8).
- The Bonferroni bar at the demo K is about one-sided t 3.45 (two-sided 3.63, K 177). The live-money gate is
  DSR >= 0.95 at the full registry K. Both are unchanged and apply to any **real-money** use. An F PASS is not
  evidence for live trading.

## 8. K, budget and registry rows

- **K +3:** H-learn +1, H-seen +1 (a read is a read, whatever its weight), F +1. The demo-arm K goes from 174 to
  **177**. The registry K rises by the same 3.
- The research director writes the three registry rows (family `session:2026-11`, units H-90-HL, H-90-HS, H-90-F) at
  quant sign-off, before leg H runs and before the first F fill counts.
- Q4 budget: `trial_budget_overrides["2026Q4"]` is 3500. The local snapshot shows 176 of 3,500 used in 2026Q4. The
  research director runs `check_budget(3)` on the brain's registry before writing the K rows.
- **If there is no headroom, raising the budget is an owner decision.** Flag it; do not assume it.

## 9. What a PASS changes (a proposal only; routed PD + PO -> implementer -> trading-safety + quant)

- **Volume only, within today's caps.**
  - The gold signal arms of the passing population get London-first admission in `exploration.daily`. They spend the
    London segment before others.
  - Optionally, a kill-only entry refusal `outside_london_h90` refuses those arms outside [07:00, 12:00)
    Europe/London. It is shadowed and recorded blocked, like N-3.
- **Unchanged:**
  - `max_per_day` (70 icm-demo, 85 icm-probe), `max_per_hour` and `scalp_day_cap` 30.
  - The loss caps, the RiskGate, `max_loss`, sizing and the daily £50 limit.
- **No real money.** The live gate and any real-money use stay owner-only.
- The refusal's own read is 20 session days: did refused non-London candidates net ≤ 0 in shadow? Its kill rule is
  that of N-3 section 4.

## 10. Not in scope (K +0)

- Asia as its own refusal: `window_0207` already refuses 02:00-07:00 UTC.
- Side or timeframe cuts.
- The LB multi-instrument arms, by session.
- Any of these needs its own card.

## 11. Sign-off

- quant-reviewer: changes required on 5338cfd6 (two-sample SE, joint bootstrap, budget check, segment freeze), all
  addressed in the amending commit; sign-off pending on it. The frozen text is that commit.
- evaluator: applies section 7 at the read.
- research director: writes the K rows (section 8). Reads leg H before the 150-fill interim.
