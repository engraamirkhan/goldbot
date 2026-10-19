# Strategy rollout plan, 2026-10-19 to 2026-11-02 (research director)

Owner 2026-10-19: "prioritize roll out of strategies so these can be tested, improve". This plan orders what enters
shadow and demo testing in the next two weeks, which trials are dispatched, and the weekly loop that retires or
promotes. It was written before any clean shadow trade was read. Clean evidence starts after the history-gap fix
(13:49 UTC today), and no outcome after the fix has been looked at. No trial outcome, no holdout bar and no shadow P&L
was read to write it.

Inputs: `shadow-roster-2026-10-19.md` (5 of 40), `prereg-2026-10-19-live-review.md` (H-50..H-52, **untracked in git
as of writing**), `prereg-kb-2026-10-18.md` (H-48, H-49), `prereg-tv-replica-2026-10-18.md` (B4/B5),
`config/settings.yaml` (2026Q4 override 55; 2027Q1 reserve 8), and the `research-v1` registry (120 rows, downloaded
2026-10-19). It also uses `gh run list` (the data-dukascopy runs at 12:52 and 14:41 succeeded; `data-v1` 2026 file
updated 15:21 UTC).

## 0. What testing can and cannot tell us in two weeks (read this first)

- **Forward shadow and demo judge parity and costs within weeks. They do not judge edge.** At 40 session-day clusters
  (box 15m: about 10 weeks), shadow can only show an edge of at least 0.38 R per trade at t 2. A realistic +0.1 R
  needs about 600 trades (live review, section 3). The edge question is answered by **research trials** on 15.7
  years of bars: H-48 now, H-50 now.
- **None of the 6 ML founders can produce a "taken" trade at an OOF AUC of about 0.50.** All 6 founder families are
  retired in `research.retired_families` (R-01..R-06). Their shadow rows are observation data, not strategies under
  test.
- The box agents on 1h and 4h (907 / 319 one-at-a-time events in 15.7 years) are **below the P4 event floor**, so no
  research trial can pass them on their own. Today they are parity and cost monitors **with no promotion route**.
  The owner should hear this plainly.

## 1. Budget state and rulings (2026Q4)

| Item | Count |
|---|---|
| 2026Q4 override | 55 (owner-approved 2026-10-18) |
| Used, hand-dispatched (registry rows without `rule_ref: loop`) | 53 |
| Loop trials in Q4 (`rule_ref: loop`; outside the quarterly count, **inside K**) | 34 (tsmom 1d mutation grids; last row 2026-10-18 20:56) |
| Funded and open | 2: H-48 `sweep_absorb` (prereg-kb s.1) and the H-49 stage-2 slot |
| `n_trials_effective` | 108 (gate-1 DSR bar t about 4.1) |
| Holdout | untouched |
| 2027Q1 | 20, 8 reserved (prereg queue), open budget pre-registered rows only |

**Ruling R1: H-49's stage-2 slot is reassigned to H-50 now, permanently, and not "until stage 1 passes".**
- Why: H-50 carries the most information per trial on the owner's own strategy, and it is the only route by which any
  box agent can ever be promoted. H-49 stage 2 is a grade-D extension with a prior of about 1%.
- Both choices are made before any data of either is read: stage 1 has not run and H-50 has no outcome. So this is a
  re-ranking, not a selection on results. K counts both either way.
- A borrowed slot that would later have to be "returned" invites a dispute about which trial owns it, so the
  reassignment is final.
- If H-49 stage 1 passes, stage 2 needs a slot of its own: **owner decision, exactly 55 -> 56**, or else the first
  Q1 2027 open slot (from 2027-01-01).

**Ruling R2: H-49 stage 1 runs now. It costs 0 trials, K_eff +1, by its own pre-registration.** The JSON is committed
whether it passes or fails.

**Ruling R3: H-51a/b (2 trials) stay in Q1 2027.** They are conditional on H-50's verdict. No Q4 raise is asked for
them.

**Ruling R4: the research loop adds no further tsmom 1d mutation trials this quarter.**
- R-06 is retired. Its gross t (2.4-2.6) cannot clear the bar at t about 4.1, and every loop trial raises that bar
  for H-48 and H-50.
- In Q4 the loop runs only on pre-registered rows (`rule_ref` set).
- The owner's "continuous research" directive is kept: the trade-off is K honesty against loop throughput, recorded
  here.
- The program-director files the decision note in `docs/decisions/`, and the settings change goes through review.

**Budget ask to the owner: none now.** The only possible ask is 55 -> 56, and only if H-49 stage 1 passes (stage-2
trial).

## 2. Ordered actions (next 14 days)

| # | Action | Owner | Effort | Due | Unlocks |
|---|---|---|---|---|---|
| 1 | Commit `prereg-2026-10-19-live-review.md` (H-50/51/52) and this plan **before any H-50 dispatch** (git history is the pre-registration proof). Add the owed `hypotheses.md` rows: H-50 funded by R1; H-51a/b conditional, Q1; H-52, 0 trials. Correct the stale H-TVR1/H-TVR2 rows: they read "target dispatch 2026-10-22", but both were withdrawn at 0 trials (B4/B5) | strategy-researcher writes; release-manager merges | 1 h | 10-19 | H-50 can be dispatched legitimately; the registry and the docs agree |
| 2 | **Dispatch H-48** `sweep_absorb`: `research.yml -f specialist=sweep_absorb -f variants='["prereg"]' -f from_year=2010 -f to_year=2025 -f lot_fit=true -f rule_ref=prereg-kb-2026-10-18.md#1`, on `main`. Funded since 10-18, code and prereg committed (84dfa2d2), not yet run (0 registry rows). Evaluator verdict against prereg-kb s.1, within 24 h of the run | research-director dispatches; evaluator reads | 15 min + 3 min run | 10-20 | the first research verdict on a roster agent: a promotion route for sweep_absorb 15m, or its retirement from the roster |
| 3 | **Run H-49 stage 1** locally on the `data-v1` Parquet (`scripts/fetch_data_release.py`, then `scripts/kb5_magnet_base_rate.py --bars raw --out-json docs/research/kb5-stage1.json`, which reads bars to 2025-09-30 only). Commit the JSON either way. Pass: roster amendment admits `eq_liquidity_run` 15m `{"rule":"prereg"}` (live barriers path exists), and the 55->56 ask goes to the owner. Fail: retire "EQH/EQL as magnets" (`hypotheses.md`, retired list) | strategy-researcher (data-engineer if the fetch fails) | 1-2 h | 10-20 | the 6th roster agent or a clean retirement; 0 trials either way |
| 4 | **Dispatch H-50** `box_break_close` 15m `{}` under R1. Precondition (implementer, about 1 h): confirm `research.yml` dispatches a `screening` family as a rule-only screen on `FULL_HISTORY` (as `box_course`). Inputs as in H-50: `variants='[{}]' from_year=2010 to_year=2025 lot_fit=true rule_ref=prereg-2026-10-19-live-review.md#h-50`. Evaluator verdict before any follow-up (H-51) | implementer (precondition); research-director dispatches; evaluator | 2 h | 10-21 | the definitive answer on the owner's box rule. CONTINUE/LEAD opens promotion for the 15m agent and H-51 in Q1. STOP (stated prior 97%) moves the 4 box agents to parity monitors and stops probe mirroring |
| 5 | **Make forward evidence admissible (H-52)**: (a) tag every shadow trade opened before 13:49 UTC 10-19 `quarantined: history_gap`. No code for this exists today (grep: no `quarantin` in engine/research). (b) Daily parity job: recompute each roster agent's candidates on `bars_1m_broker`, compare with the shadow book, ≥ 95% (bar, side) both ways, quarantine the day on a miss (start from `scripts/replica_parity.py`). (c) An engine-restart counter in the daily report (12 starts in 8 h on 10-19 starved the 1h/4h agents) | implementer + data-engineer; trading-safety-reviewer (shadow book) | 1-2 days | 10-23 | any forward number meaning anything. Without it, two weeks of shadow are unreadable |
| 6 | **Probe volume on icm-probe**: bring the account up (`docs/ops/icm-probe-account-plan.md`). Fix the ADR 0037 mismatch: the probe budget uses the engine's £20 `daily_loss_limit`, not the £50 the ADR states (1 clamped probe fills the day). Fix the `box_break_close` `probe_loss_above_cap` refusals and the `taken: true` on an untaken mirror. Target ≥ 3 filled probes a day, for 30 fills by about 11-02 | sre (account); implementer; trading-safety-reviewer + CFO rule on the probe budget (a demo account; real-account rails unchanged) | 1 day | 10-22 | H-52's cost reading at 30 fills: a measured $/oz with a bootstrap CI, which sets the cost prior of every new pre-registration |
| 7 | **R4**: restrict the research loop to pre-registered rows for the rest of Q4. Decision note in `docs/decisions/` | program-director (note); implementer (setting); quant-reviewer | 2 h | 10-22 | stops K growing on an exhausted family, so the DSR bar holds for H-48/H-50 |
| 8 | **ML founders: park** (section 4) | performance-analyst (reporting); strategy-researcher (Q1 rows) | 1 h | 10-26 | no spend on models with no signal; better models only via H-02/O-4 in Q1 |

Not rolled out in this window, with the reason:
- **smc_structure TVR-1/TVR-2.** Withdrawn at 0 trials: 441 and 1,196 events, below the floors. A roster seat would
  add K with no promotion route, and the SMC columns already go into O-4/H-02 (D-TVR). No rewording from the counts.
- **box_break, box_retest, box_rip, fakey, trend_level_signal, box_course (H-37..H-45).**
  - A live state path needs context bars passed to candidates, more than 300 4h bars, seeded ATR/EMA, the coverage
    flag and the next-open entry. That is multi-day engine work with a safety review.
  - H-40 failed (OOF AUC 0.503), and only H-37 reaches 1,500 events, at its last rung.
  - This waits until one of them passes a research trial.
- **asia_drift (H-04).** Trial #44 read STOP: gross +0.043 R at t 3.09, net -0.093 R. A shadow seat would re-test it
  with no new evidence.
- **box_break_close 5m.** Needs a 5m base clock, which is an engine/exit-path change (roster amendment 2026-10-19b).
- **A standalone MFI rule agent.** It has no pre-registration; H-51a tests the MFI's contribution inside the box rule
  first.

## 3. The weekly "improve" loop (Mondays: 10-26, 11-02, then weekly)

The performance-analyst produces the readout. The evaluator gives the verdicts. The research director rules, in the
order below. No item ranks, tunes or selects by shadow P&L.

**Measured weekly, per roster agent, on clean days only:**
1. **Parity rate** (H-52): matched (bar, side) both ways, plus barrier and exit-bar equality on closed trades.
2. **Signal rate against the research rate.** Poisson test over the clean days, two-sided. An alarm at p < 0.01
   means a data or code fault until shown otherwise.
3. **Clean session-day clusters**, and the one-at-a-time subset against the 40-cluster minimum.
4. **Costs from filled probes**: $/oz and R, spread/slippage/commission split, latency, fills per day, and refusals
   by reason.
5. **Data quality**: `history_gap` events, engine restarts per day, data-v1 freshness.
6. **Shadow net mean R with a 95% CI. Reported, never acted on before 40 clusters.**

**Retire from the roster** (written now, before any clean trade):
- The research trial reads STOP (H-48, H-50, H-49 stage 2): retired the same day, and probes stop mirroring the
  agent.
- Parity is below 95% on 3 consecutive clean days, or on 5 in any 10: suspended. The fault goes to the implementer
  within 24 h. Reinstated after the fix, with its evidence restarted from the fix.
- Futility, at 40 clean clusters: the upper bound of the 95% CI of net mean R per trade is below 0. Then the agent is
  retired. This rule only removes agents, so it costs no trial. The strategy-researcher adds it to the roster file
  as an amendment before the first agent reaches 40 clusters.

**Promote** (unchanged from the roster rules):
- Required: a passed research trial of the exact configuration, net expectancy > 0, DSR ≥ 0.95 at full K (clustered
  K_eff once implemented), the holdout look, ≥ 40 clean clusters, and the evaluator's verdict.
- At most one promotion per family per quarter.
- Owner confirmation for real money stays as ADR 0036 sets it.
- Before any promotion is decided, the follow-up lane implements the clustered K_eff and session-day cluster count in
  `Population.tournament`.

**Seed the next generation.** A CONTINUE or LEAD verdict seeds pre-registered neighbours only:
- H-50 leads to H-51a/b.
- H-48 would lead to a session-level or 30m neighbour, written after its verdict and before any data.
- A STOP retires the idea, with the reason recorded in `hypotheses.md` section B.

## 4. ML founders: parked, with the route to better models

- They are observation-only. Shadow keeps recording their untaken candidates at zero cost, for recalibration data.
  There is no retraining on shadow outcomes, no threshold change, no mutation or cloning (their families are
  retired), and no probe mirroring.
- A better model comes only from the pre-registered Q1 queue:
  - H-02 (4h meta-model with macro and the D-TVR group);
  - O-4 / H-14 (15m feature discovery, scalping), whose quant sign-off is due 10-25.
  A founder model is replaced only by a trial that passes gate 1.
- A founder is removed from the shadow population at 182 days retired (`RETIRED_SHADOW_DAYS`), or earlier if its
  frames fail parity.

## 5. Readiness rows (owner and date; checked against `gh run list`/`gh release list` on 10-19)

| Row | Owner | Date | Status 10-19 |
|---|---|---|---|
| Live-review prereg committed | strategy-researcher / release-manager | 10-19 | untracked: **open** |
| H-48 dispatched + verdict | research-director / evaluator | 10-20 / 10-21 | funded, 0 runs |
| H-49 stage-1 JSON committed | strategy-researcher | 10-20 | not run |
| H-50 screening-dispatch precondition | implementer | 10-21 | open |
| H-50 dispatched + verdict | research-director / evaluator | 10-21 / 10-22 | open (slot: R1) |
| Shadow quarantine + daily parity | implementer / data-engineer | 10-23 | not started |
| icm-probe live, probe budget ruling | sre / trading-safety / CFO | 10-22 | account being brought up |
| Loop restriction (R4) note | program-director | 10-22 | open |
| Futility amendment to the roster | strategy-researcher | 11-02 | open |
| H-06 slot escalation (2026-12-04, 3 weeks ahead) | research-director | 11-13 | outside window, tracked |

These rows go to the program-director within 24 h of any re-date.
