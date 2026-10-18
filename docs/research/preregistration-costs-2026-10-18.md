# Pre-registration: cost-aware proposals (strategy-researcher, 2026-10-18)

**Status: written, not dispatched.** The variants, samples and reading rules below are fixed at this file's commit,
before any outcome of these configurations has been computed. The evidence and the cost arithmetic are in
`costs-binding-2026-10-18.md` ("CB file").

**Outcome-blind attestation.** I read published registry rows #1-#44 and the configurations of #45-#47, but not
their results. I computed cost and volatility statistics only. No outcome of CB-T1 or CB-T2 has been computed.

**Selection disclosure.** CB-T1 is a neighbour of #36, chosen *after* #36's result was published. That selection is
exactly why it is a new trial charged in full to K, read by an unchanged rule, and never used to re-read #36.

**Ranking.** By expected information per trial. Zero-trial items come first because they cost nothing and every
later reading depends on them.

| Rank | ID | What | Trials | Status |
|---|---|---|---|---|
| 1 | CB-0 | Build `costs-v1` and the broker spread profile; fix the GBP blockers K1-K3 | 0 | proposed (engineering + measurement) |
| 2 | CB-D1 | Forward-cost and regime quantities reported on every new trial (cannot pass or rescue anything) | 0 | pre-registered |
| 3 | CB-D2 | Limit-fill simulation on the broker's recorded ticks for shadow-book candidates (forward data only) | 0 | pre-registered (needs 20 recorded days) |
| 4 | CB-T1 | tsmom 1h, cost-amortised: stop 2.0, target 4.0, hold up to 96 h, min_score 1.0 | 1 | pre-registered (needs 1 slot, see K) |
| 5 | CB-T2 | CB-T1's signal with a limit entry at the signal price | 1, conditional | pre-registered, conditional |
| — | Not proposed | H-04 re-test, tight-spread-hours trials, cost-filtered research trials, 15m cost variants | 0 | reasons below |

## CB-0

- **What.** Steps 0-8 of the CB file, section 1.4, including the fixes K1 (swap on a GBP account), K2 (commission on a
  GBP account) and K3 (publish with slippage marked "prior").
- **Why.** Every net figure so far charges priors. The research basis today is 1.07 $/oz, against perhaps
  0.3-0.5 $/oz on Raw. That gap is worth +0.02 to +0.07 R per trade at 1h/15m stops.
- **Cost.** 0 trials, 0 to K.
- **Done when:**
  - `costs-v1/costs_measured.json` exists, with `fields.spread` and `fields.swap` = measured;
  - `fields.commission` = measured after the demo round trip;
  - `spread_profile.json` covers ≥ 20 trading days.
- **Dates.** K1-K3 merged by **2026-10-30**. First publication about 2026-11-06 if the MT5 login lands by 2026-10-22.
  Profile about 2026-11-20.
- Reading rule: none (a measurement). The values are reported as measured, with n, and research uses them from the next dispatch on; no trial already read is re-read with them.

## CB-D1

- **What.** Every trial dispatched after this commit adds these quantities to its evaluator report. They are
  **reported, never deciding**:
  1. Forward-cost net R: gross R of each out-of-fold trade minus C / (k × ATR_tf,today). ATR_tf,today is the
     last-90-day median ATR of the trial's ATR timeframe at dispatch. C is scenario A (1.07 $/oz), scenario C
     (0.52 $/oz), and the `costs-v1` round trip once published. The long swap prior is charged per night held, also
     in today's R.
  2. Gross R by volatility tercile, where the terciles are point-in-time ATR/price cut on each training fold only.
     Report the slope across terciles.
  3. Gross R and n for 2024-01-01..2025-09-30.
  4. Net R (normal pipeline costs) on the subset whose point-in-time cost in R ≤ 0.10.
- **Why.** The research window charges a cost in R 3-5 times today's (CB file, section 2.2). These quantities show
  whether a gross edge in R holds as volatility rises, which is the assumption under any forward-cost projection.
- **Cost.** 0 trials. It reads only the trial's own out-of-fold labels, after the verdict.
- Reading rule: informational only; a CB-D1 quantity never turns a FAIL into a LEAD or a LEAD into a PASS, and is never computed for a trial read before this commit. It is used only to rank LEADs for a holdout look if the owner allows LEADs one (CB file, section 5).

## CB-D2

- **What.** For every shadow-book candidate on 1h and 15m from the date the tick record starts (forward data only),
  simulate a limit entry at the candidate's signal price, valid for one decision bar, on the broker's recorded
  ticks.
  - A long fills when the ask trades ≤ limit − $0.05.
  - A short fills when the bid trades ≥ limit + $0.05.
- **Reported:**
  - fill rate;
  - $ saved per filled entry against the market entry the candidate actually got (or would have got) in shadow;
  - adverse selection: the mid move 15 minutes and 60 minutes after the fill, for filled versus unfilled;
  - the R forgone on unfilled candidates whose market-entry shadow trade won.
- **Why.** Limit orders save the spread but are adversely selected (Harris and Hasbrouck 1996; Linnainmaa 2010).
  The size of that effect in XAUUSD CFDs is unknown, and Dukascopy 1m bars cannot show within-minute fills.
- **Cost.** 0 trials. Forward data, no research window, no holdout.
- Reading rule: on >= 200 candidates, if the mean saving per candidate (filled saving minus forgone R converted at each candidate's stop, unfilled candidates included) is >= 0.25 $/oz, CB-T2's label tooling is built and CB-T2 may run; if it is < 0.10 $/oz, CB-T2 is withdrawn (0 trials) and limit entry is recorded as "no net saving on IC Markets XAUUSD"; between the two, wait for 400 candidates and apply the same rule once more, then withdraw if still undecided.

## CB-T1

- **Family and configuration.** `tsmom`, 1h:
  `{"min_score": 1.0, "stop_atr": 2.0, "target_atr": 4.0, "max_bars": 96}`. The other settings are the defaults:
  `lb_fast_h` 24, `lb_mid_h` 120, `lb_slow_h` 480, `vol_window_h` 96, `schedule_h` 4.
  - Resolved with `preregistration.resolve_variant`.
  - Agent `tsmom-g0-b04aa04055`, `config_hash` **`7f60182a4559`**, which is unique among the 36 config hashes in the
    registry snapshot. Re-check at dispatch.
- **Economic reason.** Vol-scaled time-series momentum is the trend horizon with replicated support (Moskowitz, Ooi
  and Pedersen 2012; Hurst, Ooi and Pedersen 2017, net of costs). A trade's $ edge grows with its holding period,
  while the round trip is paid once.
  - A 2.0 × ATR stop halves the cost in R against 1.0 × ATR, and cuts stop-outs from noise.
  - A 4 × ATR target with up to 4 days' hold lets trends run.
  - This is the cheapest 1h configuration under market orders at today's ATR: scenario A 0.030 R, scenario C
    0.015 R, plus a long swap of 0.017 R a night.
- **Expected edge versus cost.** Neighbours (#16, #36, #38) had gross +0.05 to +0.09 R. My prior for CB-T1's gross
  is +0.04 to +0.10 R.
  - The research-window cost will be about 0.10-0.13 R plus swap, so I expect rule-only net ≤ 0 on the window.
  - Forward-cost net (CB-D1) might be +0.01 to +0.06 R.
  - **Prior probability:** PASS ≤ 2% (gate 3 needs about +0.13 R net at n ≈ 1,500, CB file section 3); LEAD about
    15-20%; FAIL about 60%; INCONCLUSIVE about 20% (longer holds block entries, so events could fall near 1,000).
- **Event count.** The 1,000-event P4 floor applies. Expected 1,100-1,700: #36, the same signal with `max_bars` 48,
  had 1,691. Below 1,000 the trial reads INCONCLUSIVE by rule.
- **P4 screen expectation.** Gross t ≥ 2 on ≥ 1,000 events is plausible but not likely (my estimate 35%).
- **Sizing.** The stop is 2 × ATR1h, about $35. That is **unsizable at £1,000** (CB file, section 2.1), so any LEAD
  or PASS is shadow-only at this equity. Changing that is an owner decision.
- **Exact `research.yml` inputs** (one run, one trial):

  ```bash
  gh workflow run research.yml --ref main -f specialist=tsmom -f from_year=2010 -f to_year=2025 -f variants='[{"min_score":1.0,"stop_atr":2.0,"target_atr":4.0,"max_bars":96}]' -f lot_fit=true -f rule_ref='preregistration-costs-2026-10-18.md#cb-t1' -f rationale='COSTS-2026-10-18 CB-T1 tsmom 1h cost-amortised: stop 2.0, target 4.0, hold 96 h, min_score 1.0; cheapest 1h market-order config at today ATR (0.030 R scenario A)'
  ```

  `skip_screen`, `macro` and `score_holdout` are false, the defaults. The holdout is excluded by `to_year` and by
  `research_pass.py`. Dispatch only when `gh run list --workflow research.yml --status in_progress` is empty
  (`batch-2026-10-18.md` blocker 0.2).
- **Gates.** `batch-2026-10-18.md` section 2, rules 1-6, word for word, with two exceptions:
  - K in rule 3 is `registry.n_trials_effective` **at the time of the verdict**, never less than 54;
  - rule 6's 0.71 $/oz stress is kept as written.
  CB-D1 is reported alongside.
- Reading rule: by `batch-2026-10-18.md` section 2 (PASS / LEAD / FAIL / INCONCLUSIVE) with K = the registry's n_trials_effective at the verdict (at least 54). **Continue** (founder; neighbours: hold ±50%, stop 1.5 / 2.5, short-only, at most 3, written into the next pre-registration before any of their data is read) only on PASS. **LEAD**: shadow only, no neighbours; a candidate for a holdout look only if the owner allows LEADs one and its CB-D1 forward-cost net at scenario C is > 0. **Stop** on FAIL or a P4 screen failure: tsmom 1h exit and stop variants are closed (R-06 reconfirmed with this trial number; no further 1h tsmom stop, target or hold variants without new evidence). INCONCLUSIVE: no re-run unless a pre-registered power gain is written first.
- **Trial cost.** 1. It is not in the 2026Q4 budget, whose 53 slots are allocated. Take it from the 2027Q1 free
  reserve (research director's call), or the owner raises `trial_budget_overrides."2026Q4"` to 54 (owner-only,
  flagged).

## CB-T2

- **Condition to run (all must hold, else withdrawn with 0 trials):**
  1. CB-T1 reads LEAD or PASS.
  2. CB-D2's reading rule says "build".
  3. The label tooling below is merged with a lookahead test.
- **Family and configuration.** CB-T1's configuration plus new label settings. These do not exist yet and need a
  BACKLOG item (implementer → quant-reviewer):
  `{"entry_mode": "limit_signal", "limit_window_bars": 1, "limit_through_usd": 0.05}`.
  - Semantics: a buy (sell) limit at the decision bar's mid close, valid until the next decision bar closes.
  - It fills when a 1m `ask_low` ≤ limit − 0.05 (`bid_high` ≥ limit + 0.05). The fill price is the limit.
  - Stop and target are measured from the fill. Unfilled means no trade, and the fill rate is reported.
  - The exit costs are unchanged: a stop pays the bid plus the slippage prior; a take-profit at the limit pays no
    slippage.
- **Economic reason.** The entry saves half the spread plus the entry slippage, about 0.5 $/oz on the research
  basis, which is about 0.014 R at 2 × ATR1h today. The cost is adverse selection and missed winners. CB-D2
  measures both first.
- **Expected edge versus cost.** Net change −0.02 to +0.02 R per signal. A low prior that the verdict changes.
- **Event count.** CB-T1's events × fill rate (CB-D2 measures the fill rate). The same 1,000-event floor applies to
  **filled** trades.
- **Inputs.** As CB-T1, with the three keys added to the variant, `rule_ref='preregistration-costs-2026-10-18.md#cb-t2'`
  and rationale `COSTS-2026-10-18 CB-T2 limit entry on CB-T1`.
- Reading rule: by `batch-2026-10-18.md` section 2 at the K of the verdict; additionally, the limit entry is adopted for CB-T1's shadow book only if CB-T2's net R per signal (unfilled signals counted as 0) is >= CB-T1's net R per signal on the same signal dates; otherwise the market entry stays and limit entry is retired for this family with the trial number.
- **Trial cost.** 1, conditional. From the 2027Q1 free reserve or the Q2 reserve, never the Q4 budget.

## Not proposed (recorded so they are not re-proposed without new evidence)

| Idea | Why not | Evidence |
|---|---|---|
| **H-04 `asia_drift` re-test** with a new stop or hold | At K ≥ 53, with gross t 3.09 and SD about 0.88 R on 4,025 entries, it needs net ≥ +0.055 R. Its gross is +0.043 R, so it **cannot pass even at zero cost**. A stop or hold change re-trades the same 4,025 entries (high correlation, little new information). In $, a 7-hour drift of 0.043 × 1.5 × ATR1h ≈ $1.3 a session today, against a 1.07 $/oz round trip, leaves about +0.01 R | CB file, section 3 |
| **Tight-spread-hours trials** (15m or 1h restricted to 13-16 UTC) | Worth 0.03-0.05 R at a 15m ATR stop and under 0.005 R at 1h. The cheapest hours are the least sizable. No 15m family showed a gross edge to protect | CB file, section 2.3. Keep `spread_atr` and hour as features in H-14 / H-02 |
| **Cost-filtered entry as a research trial** ("skip if cost > x R") at today's levels | At cost ≤ 0.06 R, 3.9% of 1h bars in 2010-2025 qualify, so a #36-like rule keeps about 70 events. Inconclusive by construction | CB file, section 2.2. It belongs in the live/shadow rule H-13 and in CB-D1(4) |
| **15m variants with wider ATR targets** to amortise cost | Already covered by B01-B04 (#21-#35). R-01, R-02 and R-05 have no gross edge to amortise | registry #21-#35 |
| **Re-reading any trial already scored** (#16, #36, H-04 #44, the batch) at measured or forward costs | That is a re-read of a seen configuration. Their verdicts stand. CB-D1 applies only after this commit | program rule (brief) |

## K accounting and budget (for the research director)

| Item | Value |
|---|---|
| Registry trial rows at the snapshot | 47 (rows #1-#47; the batch is still running) |
| 2026Q4 budget | 53, all allocated (#1-#20 + batch 32 + H-02). Free: **0** |
| This file, if CB-T1 runs | **+1**, so K ≥ 54. The gate-1 t bar moves from about 3.94 to about 3.95 |
| CB-T2 | +1 more, only if its three conditions hold |
| CB-0, CB-D1, CB-D2 | 0 (measurement and reporting; nothing is selected) |
| Discovery K_eff | unchanged (no discovery here) |
| Funding | 2027Q1 free reserve (3 slots), the research director's call within budget. Or owner: Q4 override 53 → 54 (flagged, not assumed) |
| Holdout 2025-10-01..2026-09-30 | untouched. No row here scores it. A LEAD's holdout look is an owner policy decision (CB file, section 5) |
